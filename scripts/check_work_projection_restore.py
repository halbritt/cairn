#!/usr/bin/env python3
"""Exercise work metadata recovery through an actual older pg_dump/pg_restore.

Only Plane is synthetic. All project, annotation, purge, retry, recovery and
restore transitions use the candidate CLI against databases this fixture owns.
The fixture accepts an explicit built candidate and never opens a configured
production database. Source and binary identity accompany the result.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from disposable_host import (DisposableHost, OwnedCluster, ProvidedCluster,
                             ProvidedDatabase, checked, require, write_private)


def uid():
    return str(uuid.uuid4())


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


PLANE_PROGRAM = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
data = json.loads(Path(os.environ['WORK_RESTORE_PLANE_DATA']).read_text())
project = args[args.index('--project') + 1]
selected = data[project]
if 'project' in args and 'get' in args:
    reply = {'ok': True, 'data': selected['project']}
elif 'issue' in args and 'list' in args:
    items = selected['items']
    reply = {'ok': True, 'data': items, 'meta': {'pagination': {
        'drained': True, 'fetched': len(items), 'total_count': len(items)}}}
else:
    raise SystemExit('unexpected Plane fixture command')
print(json.dumps(reply))
'''


def run_scenario(cluster, binary, report):
    repo = "fixture:work-restore:" + uid()
    persistent = "persistent_" + uuid.uuid4().hex
    once = "one_time_" + uuid.uuid4().hex
    workspace = uid()
    projects = {slug: {"project": uid(), "persistent": uid(), "once": uid()}
                for slug in ("selected", "control")}
    plane_data = {}
    for slug, ids in projects.items():
        plane_data[ids["project"]] = {
            "project": {"id": ids["project"], "workspace": workspace,
                        "identifier": slug.upper()},
            "items": [{"id": ids[kind], "project": ids["project"],
                       "name": marker, "sequence_id": number,
                       "state": None, "assignees": [],
                       "updated_at": "2026-11-05T00:00:00Z"}
                      for number, (kind, marker) in enumerate(
                          (("persistent", persistent), ("once", once)), 1)]}

    with DisposableHost.start(cluster, repo=repo, binary=binary,
                              database="cairn_work_projection_recovery",
                              roles=(), serve=False) as live:
        # Explicit private routing prevents the caller's real Plane config from
        # entering this fixture. The stand-in returns the actual CLI envelope.
        for name in list(live.env):
            if name.startswith("PLANE_") or name == "CAIRN_PLANE_BIN":
                del live.env[name]
        plane = live.home / "plane-fixture"
        write_private(plane, PLANE_PROGRAM)
        plane.chmod(0o700)
        data_path = live.home / "plane-data.json"
        write_private(data_path, json.dumps(plane_data))
        config = live.home / "plane.toml"
        write_private(config, 'base_url = "https://plane.invalid"\nworkspace = "synthetic"\n')
        live.env.update(CAIRN_PLANE_BIN=str(plane), PLANE_CONFIG=str(config),
                        WORK_RESTORE_PLANE_DATA=str(data_path))
        live.operator("bootstrap", {"request_id": uid(), "reason": "Disposable work recovery proof"})
        live.command(["work", "features", "enable", "--feature", "work-view/1", "--request-id", uid()])

        def work(host, *args, **kwargs):
            return host.command(["work", *args, "--collection", repo], **kwargs)

        def show(host, slug, kind):
            return work(host, "show", "--project", slug, "--item", projects[slug][kind])

        progress_commands = {}
        for slug, ids in projects.items():
            project = work(live, "project", "add", "--slug", slug, "--workspace", workspace,
                           "--project", ids["project"], "--request-id", uid())
            ids["binding"] = str(uuid.UUID(project["binding_id"]))
            work(live, "refresh", "--project", slug)
            command = ["progress", "add", "--project", slug, "--item", ids["persistent"],
                       "--text", persistent, "--request-id", uid()]
            annotation = work(live, *command)
            require(annotation["body"] == persistent, "annotation setup did not retain marker")
            edit = ["progress", "edit", "--project", slug, "--item", ids["persistent"],
                    "--annotation-id", annotation["annotation_id"], "--expected-revision",
                    str(annotation["revision"]), "--text", persistent + " updated", "--request-id", uid()]
            work(live, *edit)
            progress_commands[slug] = [command, edit]
            require(persistent in json.dumps(show(live, slug, "persistent")),
                    "pre-backup positive control missing")
        control_scope = {"repo": repo, "task_id": "restore-control", "run_id": "one"}
        live.operator("create", {"request_id": uid(), "draft": {
            "kind": "note", "body": "Retained restore admission control.",
            "scope": control_scope, "claim_type": "self", "sensitivity": "shareable"}})
        control_receipt = live.operator("compile", {"request_id": uid(), "scope": control_scope,
            "query": "", "purpose": "context", "available_tokens": 64000})
        checkpoint = live.operator("checkpoint", {"request_id": uid(), "export_id": "work-before-purge.dump"})
        backup = live.home / "work-before-purge.dump"
        checked([cluster.pg_bin / "pg_dump", "--format=custom", "--file", backup, live.dsn],
                "pre-purge pg_dump", timeout=120)
        report["stages"].append("actual_pre_purge_backup")

        # Disabling admission must leave maintenance and historical reads usable.
        live.command(["work", "features", "disable", "--feature", "work-view/1", "--request-id", uid()])
        for marker, persistent_rule in ((persistent, True), (once, False)):
            command = ["metadata", "purge", "--project", "selected", "--field", "all",
                       "--pattern", marker, "--request-id", uid()]
            if persistent_rule:
                command.append("--exclude")
            result = work(live, *command)
            require(result["purged_items"] > 0, "purge did not affect selected item")
            require(work(live, *command) == result, "purge retry changed the retained result")
        for kind, marker in (("persistent", persistent), ("once", once)):
            require(marker not in json.dumps(show(live, "selected", kind)), "purged selected view leaked")
            require(marker in json.dumps(show(live, "control", kind)), "purge crossed project binding")
        for command in progress_commands["selected"]:
            work(live, *command, expected="PAYLOAD_UNAVAILABLE", forbidden=persistent)
        for command in progress_commands["control"]:
            require(persistent in work(live, *command)["body"],
                    "selected purge invalidated control project's retry")
        history_query = ("SELECT count(*) FROM cairn.work_annotation_history h "
                         "JOIN cairn.work_annotation a USING(annotation_id) "
                         f"WHERE a.binding_id='{projects['selected']['binding']}' "
                         f"AND h.body LIKE '%{persistent}%'")
        require(live.sql(history_query) == "0", "purged annotation history retained copied text")
        report["stages"].append("disabled_maintenance_scope_and_cached_reply")

        external = live.home / "post-backup-work-recovery.json"
        exported = live.operator("recovery-export", args=[external])
        record = json.loads(external.read_text())
        require(record.get("work_exclusions") and record.get("work_purges"),
                "recovery export omitted persistent or one-time work suppression")
        require(live.operator("recovery-inspect", args=[external])["consistent"],
                "current metadata purge is not recovery-consistent")
        report["stages"].append("separate_post_backup_recovery_capture")

        restored_dsn = cluster.create_database("cairn_work_projection_restored")
        checked([cluster.pg_bin / "pg_restore", "--no-owner", "--no-privileges",
                 "--dbname", restored_dsn, backup], "older pg_restore", timeout=120)
        with DisposableHost.start(ProvidedDatabase(restored_dsn), repo=repo, binary=binary,
                                  home=cluster.root / "work-restored-home", roles=(), serve=False) as restored:
            restored.env.update({key: live.env[key] for key in
                                ("CAIRN_PLANE_BIN", "PLANE_CONFIG", "WORK_RESTORE_PLANE_DATA")})
            require(persistent in json.dumps(show(restored, "selected", "persistent")),
                    "restored snapshot was not actually older than purge")
            before = restored.operator("recovery-inspect", args=[external], expected="INTEGRITY_FAILURE")
            require(any(g["reason"] == "METADATA_EXCLUSION_MISSING"
                        for g in before["data"]["gaps"]), "older restore did not detect work suppression gap")
            session = restored.operator("begin-restore", {"request_id": uid(), "target": backup.name,
                "reason": "Reconcile disposable older work projection"})
            work(restored, "show", "--project", "selected", "--item", projects["selected"]["persistent"],
                 expected="RESTORE_PAUSED")
            apply_args = ["--request-id", uid(), "--expected-sha256", exported["sha256"],
                          "--reason", "Restore retained work purges", str(external)]
            applied = restored.operator("recovery-reapply", args=apply_args)
            require(restored.operator("recovery-reapply", args=apply_args) == applied,
                    "recovery reapplication retry changed identity")
            verification = {"session_id": session["session_id"],
                "checkpoint": {"checkpoint_id": checkpoint["checkpoint_id"],
                               "expected_sha256": checkpoint["sha256"],
                               "expected_export_id": checkpoint["export_id"]},
                "recovery": record,
                "fixtures": [{"receipt_id": control_receipt["receipt_id"], "query": ""}]}
            restored.operator("rebuild-restore", {"request_id": uid(), "session_id": session["session_id"]})
            require(restored.operator("verify-restore", verification)["ready"], "restored work not ready")
            restored.operator("resume-restore", {"request_id": uid(), "verification": verification,
                "policy": "local-restore/1", "reason": "Resume checked private work restore"})
            for kind, marker in (("persistent", persistent), ("once", once)):
                require(marker not in json.dumps(show(restored, "selected", kind)),
                        "older payload revived after real restore/reapply/reopen")
                require(marker in json.dumps(show(restored, "control", kind)),
                        "recovery suppression crossed project binding")
            for command in progress_commands["selected"]:
                work(restored, *command, expected="PAYLOAD_UNAVAILABLE", forbidden=persistent)
            for command in progress_commands["control"]:
                require(persistent in work(restored, *command)["body"],
                        "recovery invalidated unrelated cached result")
            require(restored.sql(history_query) == "0", "restore revived copied annotation history")
            report["stages"].append("actual_restore_reapply_resume_read_and_retry")

            # The fresh source acquisition is a new intent: one-time purge allows
            # it, persistent exclusion refuses it. Stage only the private clock.
            project = work(restored, "project", "show", "--project", "selected")
            require(project.get("refresh_in_flight") is False, "unexpected live refresh claim")
            restored.sql("UPDATE cairn.work_project SET next_refresh_allowed_at=clock_timestamp()-interval '1 second' "
                         "WHERE local_slug='selected'")
            work(restored, "refresh", "--project", "selected")
            require(persistent not in json.dumps(show(restored, "selected", "persistent")),
                    "persistent exclusion lost on fresh post-restore acquisition")
            require(once in json.dumps(show(restored, "selected", "once")),
                    "one-time purge incorrectly became a permanent exclusion")
            report["stages"].append("persistent_vs_one_time_after_fresh_acquisition")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--report-directory", type=Path, required=True)
    args = parser.parse_args()
    require(args.binary.is_file(), "explicit candidate executable required")
    args.report_directory.mkdir(parents=True, exist_ok=True)
    report = {"schema": "cairn.work-projection-restore-proof/1", "status": "running", "stages": [],
              "binary_sha256": digest(args.binary), "fixture_sha256": digest(__file__),
              "provider_called": False, "plane": "synthetic CLI boundary", "cluster_cleaned": False}
    cluster = None
    try:
        if os.environ.get("CAIRN_DISPOSABLE_TEST_ROOT"):
            cluster = ProvidedCluster.from_environment()
            report["cluster_cleanup_owner"] = "integration_runner"
        else:
            root = Path(tempfile.mkdtemp(prefix="cairn-work-restore-"))
            cluster = OwnedCluster(root)
            cluster = OwnedCluster.start(root)
            report["cluster_cleanup_owner"] = "fixture"
        run_scenario(cluster, args.binary.resolve(), report)
        require(digest(args.binary) == report["binary_sha256"], "candidate binary changed during proof")
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
