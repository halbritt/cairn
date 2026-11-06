#!/usr/bin/env python3
"""Prove artifact purge and request identity survive an actual older restore.

Only the external Plane command is synthetic. The candidate CLI performs every
artifact, link, purge and recovery operation in databases this fixture owns.
"""

import argparse
import json
import os
from pathlib import Path
import tempfile

from check_work_projection_restore import PLANE_PROGRAM, digest, uid
from disposable_host import (DisposableHost, OwnedCluster, ProvidedCluster,
                             ProvidedDatabase, checked, require, write_private)


def contains_artifact_witness(value, artifact_id):
    if isinstance(value, dict):
        if value.get("artifact_id") == artifact_id and value.get("canonical_key"):
            return True
        return any(contains_artifact_witness(v, artifact_id) for v in value.values())
    if isinstance(value, list):
        return any(contains_artifact_witness(v, artifact_id) for v in value)
    return False


def scenario(cluster, binary, report):
    repo = "fixture:artifact-restore:" + uid()
    other_collection = repo + ":other"
    workspace, project, item = uid(), uid(), uid()
    marker = "artifact_copied_" + uid()
    with DisposableHost.start(cluster, repo=repo, binary=binary,
                              database="cairn_artifact_before_restore",
                              roles=(), serve=False) as live:
        for name in list(live.env):
            if name.startswith("PLANE_") or name == "CAIRN_PLANE_BIN":
                del live.env[name]
        plane = live.home / "plane-fixture"
        write_private(plane, PLANE_PROGRAM)
        plane.chmod(0o700)
        data = live.home / "plane-data.json"
        write_private(data, json.dumps({project: {
            "project": {"id": project, "workspace": workspace, "identifier": "ART"},
            "items": [{"id": item, "project": project, "name": "Restore control item",
                       "sequence_id": 1, "state": None, "assignees": [],
                       "updated_at": "2026-11-05T00:00:00Z"}]}}))
        config = live.home / "plane.toml"
        write_private(config, 'base_url = "https://plane.invalid"\nworkspace = "synthetic"\n')
        live.env.update(CAIRN_PLANE_BIN=str(plane), PLANE_CONFIG=str(config),
                        WORK_RESTORE_PLANE_DATA=str(data))
        live.operator("bootstrap", {"request_id": uid(), "reason": "Disposable artifact restore proof"})
        for feature in ("work-view/1", "work-artifacts/1"):
            live.command(["work", "features", "enable", "--feature", feature, "--request-id", uid()])

        def work(host, *args, **kwargs):
            return host.command(["work", *args, "--collection", repo], **kwargs)

        def artifact(host, *args, **kwargs):
            return work(host, "artifact", *args, **kwargs)

        def receipt_identity(host, request_id, operation):
            return host.sql(
                "SELECT json_build_object('caller', caller, 'operation', operation, "
                "'request_id', request_id, 'digest', encode(request_digest, 'hex'))::text "
                "FROM cairn.mutation_request WHERE request_id='" + request_id +
                "' AND operation='work-artifact-" + operation + "'")

        work(live, "project", "add", "--slug", "artifact-proof", "--workspace", workspace,
             "--project", project, "--request-id", uid())
        work(live, "refresh", "--project", "artifact-proof")
        retained = {}
        for name in ("selected", "control"):
            register = ["register", "--kind", "report", "--url",
                        f"https://reports.invalid/{uid()}", "--revision", "v1",
                        "--display-name", marker, "--request-id", uid()]
            revision = artifact(live, *register)
            link = ["link", "--project", "artifact-proof", "--item", item,
                    "--revision", revision["revision_id"], "--role", "input",
                    "--rationale", marker, "--request-id", uid()]
            linked = artifact(live, *link)
            unlink = ["unlink", "--project", "artifact-proof", "--item", item,
                      "--link-id", linked["link_id"], "--expected-revision",
                      str(linked["revision"]), "--request-id", uid()]
            removed = artifact(live, *unlink)
            require(removed["state"] == "removed", "setup did not retain removed link")
            output = ["link", "--project", "artifact-proof", "--item", item,
                      "--revision", revision["revision_id"], "--role", "output",
                      "--rationale", marker, "--request-id", uid()]
            artifact(live, *output)
            retained[name] = {"id": revision["artifact_id"], "revision": revision["revision_id"],
                              "history_link": linked["link_id"],
                              "commands": [register, link, unlink, output], "receipts": {}}
            for command in retained[name]["commands"]:
                require(marker in json.dumps(artifact(live, *command)), "setup retry positive control missing")
                request_id = command[command.index("--request-id") + 1]
                identity = receipt_identity(live, request_id, command[0])
                require(identity and len(json.loads(identity)["digest"]) == 64,
                        "setup mutation receipt identity missing")
                retained[name]["receipts"][request_id] = identity
            register_request = register[register.index("--request-id") + 1]
            retained[name]["register_response"] = live.sql(
                "SELECT response::text FROM cairn.mutation_request WHERE request_id='" +
                register_request + "' AND operation='work-artifact-register'")
            require(json.loads(retained[name]["register_response"])["artifact_id"] == revision["artifact_id"],
                    "setup saved response does not identify the selected artifact")
            require(int(live.sql("SELECT count(*) FROM cairn.work_artifact_link_history WHERE link_id='" +
                                 linked["link_id"] + "' AND rationale LIKE '%" + marker + "%'").strip()) > 0,
                    "pre-backup history did not contain the selected copied marker")

        selected, control = retained["selected"], retained["control"]
        # The same external identity can belong to another collection. A
        # canonical-key recovery match must retain that collection boundary.
        foreign_register = list(selected["commands"][0])
        foreign_request = uid()
        foreign_register[foreign_register.index("--request-id") + 1] = foreign_request

        def foreign_artifact(host, *args, **kwargs):
            return host.command(["work", "artifact", *args, "--collection", other_collection], **kwargs)

        foreign_revision = foreign_artifact(live, *foreign_register)
        require(foreign_revision["artifact_id"] != selected["id"],
                "setup did not separate the same locator across collections")
        foreign_identity = receipt_identity(live, foreign_request, "register")
        control_scope = {"repo": repo, "task_id": "restore-control", "run_id": "one"}
        live.operator("create", {"request_id": uid(), "draft": {
            "kind": "note", "body": "Artifact restore admission control.", "scope": control_scope,
            "claim_type": "self", "sensitivity": "shareable"}})
        control_receipt = live.operator("compile", {"request_id": uid(), "scope": control_scope,
            "query": "", "purpose": "context", "available_tokens": 64000})
        checkpoint = live.operator("checkpoint", {"request_id": uid(), "export_id": "artifact-before-purge.dump"})
        backup = live.home / "artifact-before-purge.dump"
        checked([cluster.pg_bin / "pg_dump", "--format=custom", "--file", backup, live.dsn],
                "actual pre-purge artifact pg_dump", timeout=120)
        report["stages"].append("actual_pre_purge_whole_database_backup")

        def assert_purged(host):
            artifact(host, "show", "--artifact", selected["id"], expected="NOT_FOUND", forbidden=marker)
            artifact(host, "show", "--revision", selected["revision"], expected="NOT_FOUND", forbidden=marker)
            for command in selected["commands"]:
                request_id = command[command.index("--request-id") + 1]
                retained_identity = host.sql(
                    "SELECT count(*) FROM cairn.mutation_request WHERE request_id='" + request_id +
                    "' AND operation='work-artifact-" + command[0] +
                    "' AND (ordinary_deleted OR payload_deleted_by IS NOT NULL)")
                require(retained_identity == "1", "artifact purge lost request identity or retained its response payload")
                require(receipt_identity(host, request_id, command[0]) == selected["receipts"][request_id],
                        "artifact purge changed retained caller/operation/request digest identity")
                require(host.sql("SELECT count(*) FROM cairn.mutation_request WHERE request_id='" +
                                 request_id + "' AND response IS NOT NULL") == "0",
                        "excluded artifact receipt still stores copied payload")
                artifact(host, *command, expected="PAYLOAD_UNAVAILABLE", forbidden=marker)
            changed = list(selected["commands"][0])
            changed[changed.index("--url") + 1] = "https://reports.invalid/changed-intent"
            artifact(host, *changed, expected="IDEMPOTENCY_CONFLICT")
            for command in control["commands"]:
                require(marker in json.dumps(artifact(host, *command)), "purge changed control artifact retry")
            require(foreign_artifact(host, *foreign_register) == foreign_revision,
                    "purge changed another collection's same-locator receipt")
            require(receipt_identity(host, foreign_request, "register") == foreign_identity,
                    "purge changed another collection's request identity")
            foreign_current = foreign_artifact(host, "show", "--artifact", foreign_revision["artifact_id"])
            require(foreign_current["artifact_id"] == foreign_revision["artifact_id"] and
                    foreign_current["display_name"] == marker,
                    "purge removed another collection's same-locator artifact")
            foreign_exact = foreign_artifact(host, "show", "--revision", foreign_revision["revision_id"])
            require(foreign_exact["revision_id"] == foreign_revision["revision_id"] and
                    foreign_exact["artifact_id"] == foreign_revision["artifact_id"] and
                    foreign_exact["artifact"]["report_locator"] == foreign_revision["artifact"]["report_locator"],
                    "purge removed another collection's exact revision")
            listed = artifact(host, "list", "--project", "artifact-proof", "--item", item, "--include-removed")
            require(selected["id"] not in json.dumps(listed), "purged artifact remained in link/history view")
            require(control["id"] in json.dumps(listed), "control artifact disappeared from retained view")
            require(host.sql("SELECT count(*) FROM cairn.work_artifact_revision WHERE artifact_id='" +
                             selected["id"] + "'") == "0", "purge retained selected revision payload")
            require(host.sql("SELECT count(*) FROM cairn.work_artifact_link_history WHERE link_id='" +
                             selected["history_link"] + "' AND rationale LIKE '%" + marker + "%'") == "0",
                    "purge retained copied link-history payload")
            require(int(host.sql("SELECT count(*) FROM cairn.work_artifact_link_history WHERE link_id='" +
                                 control["history_link"] + "' AND rationale LIKE '%" + marker + "%'").strip()) > 0,
                    "purge removed unrelated artifact history")

        live.command(["work", "features", "disable", "--feature", "work-artifacts/1", "--request-id", uid()])
        purge = ["purge", "--artifact", selected["id"], "--request-id", uid()]
        purged = artifact(live, *purge)
        require(artifact(live, *purge) == purged, "purge lost-reply retry changed result")
        assert_purged(live)
        report["stages"].append("disabled_purge_preserves_scope_and_request_identity")

        external = live.home / "post-backup-artifact-recovery.json"
        exported = live.operator("recovery-export", args=[external])
        record = json.loads(external.read_text())
        require(contains_artifact_witness(record, selected["id"]),
                "external recovery capture omitted selected artifact identity/purge witness")
        require(live.operator("recovery-inspect", args=[external])["consistent"],
                "current artifact purge is recovery-inconsistent")
        report["stages"].append("separate_post_backup_artifact_witness")

        restored_dsn = cluster.create_database("cairn_artifact_older_restore")
        checked([cluster.pg_bin / "pg_restore", "--no-owner", "--no-privileges",
                 "--dbname", restored_dsn, backup], "actual older artifact pg_restore", timeout=120)
        with DisposableHost.start(ProvidedDatabase(restored_dsn), repo=repo, binary=binary,
                                  home=cluster.root / "artifact-restored-home", roles=(), serve=False) as restored:
            require(marker in json.dumps(artifact(restored, "show", "--artifact", selected["id"])),
                    "older whole-database restore did not actually revive pre-purge artifact")
            require(restored.sql("SELECT count(*) FROM cairn.work_artifact_purge_fence WHERE artifact_id='" +
                                 selected["id"] + "'") == "0", "older restore improperly retained newer fence")
            before = restored.operator("recovery-inspect", args=[external], expected="INTEGRITY_FAILURE")
            require(before["data"]["gaps"], "older restore did not identify missing purge witness")
            session = restored.operator("begin-restore", {"request_id": uid(), "target": backup.name,
                "reason": "Restore selected artifact exclusion"})
            artifact(restored, "show", "--artifact", selected["id"], expected="RESTORE_PAUSED")
            reapply = ["--request-id", uid(), "--expected-sha256", exported["sha256"],
                       "--reason", "Retain post-backup artifact purge", str(external)]
            applied = restored.operator("recovery-reapply", args=reapply)
            require(restored.operator("recovery-reapply", args=reapply) == applied,
                    "artifact recovery retry changed result")
            require(restored.operator("recovery-inspect", args=[external])["consistent"],
                    "first recovery reapply did not establish consistency before fault injection")
            report["stages"].append("actual_older_restore_gap_and_exact_reapply_retry")
            # Model a partial recovery that retained the fence but restored one
            # copied response. A fence row alone must not certify consistency.
            register_id = selected["commands"][0][selected["commands"][0].index("--request-id") + 1]
            response_hex = selected["register_response"].encode("utf-8").hex()
            require(receipt_identity(restored, register_id, "register") == selected["receipts"][register_id],
                    "first recovery reapply changed the selected receipt identity")
            require(restored.sql("SELECT count(*) FROM cairn.mutation_request WHERE request_id='" +
                                register_id + "' AND operation='work-artifact-register' "
                                "AND response IS NULL AND (ordinary_deleted OR payload_deleted_by IS NOT NULL)") == "1",
                    "first recovery reapply did not remove the selected receipt payload")
            changed_rows = restored.sql("WITH changed AS (UPDATE cairn.mutation_request SET ordinary_deleted=false, "
                                        "payload_deleted_by=NULL, response=convert_from(decode('" + response_hex +
                                        "','hex'),'UTF8')::jsonb WHERE request_id='" + register_id +
                                        "' AND operation='work-artifact-register' RETURNING request_id) "
                                        "SELECT count(*) FROM changed")
            require(changed_rows == "1", "partial-recovery injection did not change exactly one receipt")
            injected = restored.sql("SELECT response::text FROM cairn.mutation_request WHERE request_id='" +
                                    register_id + "' AND operation='work-artifact-register' "
                                    "AND ordinary_deleted=false AND payload_deleted_by IS NULL")
            require(json.loads(injected) == json.loads(selected["register_response"]),
                    "partial-recovery injection did not restore the exact original response")
            corrupt = restored.operator("recovery-inspect", args=[external], expected="INTEGRITY_FAILURE")
            require(any(gap["kind"] == "work_artifact_purge_fence" and
                        gap["subject_id"] == selected["id"] for gap in corrupt["data"]["gaps"]),
                    "recovery inspection did not identify the revived artifact receipt")
            repair = list(reapply)
            repair[repair.index("--request-id") + 1] = uid()
            restored.operator("recovery-reapply", args=repair)
            report["stages"].append("partial_recovery_receipt_gap_detected_and_repaired")
            verification = {"session_id": session["session_id"],
                "checkpoint": {"checkpoint_id": checkpoint["checkpoint_id"],
                               "expected_sha256": checkpoint["sha256"], "expected_export_id": checkpoint["export_id"]},
                "recovery": record, "fixtures": [{"receipt_id": control_receipt["receipt_id"], "query": ""}]}
            restored.operator("rebuild-restore", {"request_id": uid(), "session_id": session["session_id"]})
            require(restored.operator("verify-restore", verification)["ready"], "artifact recovery not ready")
            restored.operator("resume-restore", {"request_id": uid(), "verification": verification,
                "policy": "local-restore/1", "reason": "Resume checked artifact recovery"})
            assert_purged(restored)
            report["stages"].append("actual_older_restore_reapply_resume_and_fresh_process_reads")
            restored.command(["work", "features", "enable", "--feature", "work-artifacts/1", "--request-id", uid()])
            foreign_new_intent = list(foreign_register)
            foreign_new_intent[foreign_new_intent.index("--request-id") + 1] = uid()
            foreign_registered = foreign_artifact(restored, *foreign_new_intent)
            require(foreign_registered["artifact_id"] == foreign_revision["artifact_id"] and
                    foreign_registered["revision_id"] == foreign_revision["revision_id"],
                    "restored purge fence blocked or replaced another collection's same locator")
            new_intent = list(selected["commands"][0])
            new_intent[new_intent.index("--request-id") + 1] = uid()
            artifact(restored, *new_intent, expected="INVALID_REQUEST")
            new_intent[new_intent.index("--url") + 1] = "https://reports.invalid/allowed-new-identity/" + uid()
            new_intent[new_intent.index("--request-id") + 1] = uid()
            require(artifact(restored, *new_intent)["artifact_id"] != selected["id"],
                    "purge fence incorrectly disabled all new registrations")
            report["stages"].append("restored_identity_fence_with_new_registration_positive_control")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--report-directory", type=Path, required=True)
    args = parser.parse_args()
    require(args.binary.is_file(), "explicit candidate executable required")
    args.report_directory.mkdir(parents=True, exist_ok=True)
    report = {"schema": "cairn.work-artifact-restore-proof/1", "status": "running", "stages": [],
              "binary_sha256": digest(args.binary), "fixture_sha256": digest(__file__),
              "projection_helper_sha256": digest(Path(__file__).with_name("check_work_projection_restore.py")),
              "provider_called": False, "plane": "synthetic CLI boundary", "cluster_cleaned": False}
    cluster = None
    try:
        if os.environ.get("CAIRN_DISPOSABLE_TEST_ROOT"):
            cluster = ProvidedCluster.from_environment()
            report["cluster_cleanup_owner"] = "integration_runner"
        else:
            root = Path(tempfile.mkdtemp(prefix="cairn-artifact-restore-"))
            cluster = OwnedCluster(root)
            cluster = OwnedCluster.start(root)
            report["cluster_cleanup_owner"] = "fixture"
        scenario(cluster, args.binary.resolve(), report)
        require(digest(args.binary) == report["binary_sha256"], "candidate changed during proof")
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        try:
            if cluster is not None and cluster.owned:
                try:
                    cluster.remove()
                    report["cluster_cleaned"] = True
                except Exception as cleanup_error:
                    report["status"] = "failed"
                    report["cleanup_error"] = str(cleanup_error)
                    report["cluster_root"] = str(cluster.root)
                    raise
        finally:
            report["binary_sha256_after"] = digest(args.binary)
            report["fixture_sha256_after"] = digest(__file__)
            (args.report_directory / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
