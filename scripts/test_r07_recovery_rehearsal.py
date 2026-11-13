"""Pure recovery-artifact/guard tests. Never start an API, cluster or compiler."""

import hashlib
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

import check_r07_recovery_rehearsal as rehearsal
from disposable_host import HostRefusal, write_private


class RecoveryFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="r07-unit-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)

    def bundle(self):
        directory = self.root / "bundle"
        directory.mkdir(mode=0o700)
        record = {"schema": "cairn.recovery-record/1", "root_grant_id": str(uuid.uuid4()), "sha256": "b" * 64}
        body = json.dumps(record).encode() + b"\n"
        write_private(directory / "part-000000001.json", body.decode())
        header = {"schema": "cairn.recovery-bundle/1", "root_grant_id": record["root_grant_id"], "count": 1}
        part = {"index": 1, "file": "part-000000001.json", "sha256": hashlib.sha256(body).hexdigest(), "record_sha256": record["sha256"]}
        prefix = (json.dumps(header) + "\n" + json.dumps(part) + "\n").encode()
        checksum = hashlib.sha256(prefix).hexdigest()
        write_private(directory / "manifest.jsonl", prefix.decode() + json.dumps({"sha256": checksum}) + "\n")
        return directory, checksum, record


class RecoveryBundleTests(RecoveryFixture):
    def test_complete_owned_bundle_retains_exact_original_record(self):
        directory, checksum, record = self.bundle()
        before = {f.name: f.read_bytes() for f in directory.iterdir()}
        parts = rehearsal.recovery_parts(directory, checksum)
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["record"], record)
        self.assertEqual(parts[0]["record_sha256"], record["sha256"])
        self.assertEqual(before, {f.name: f.read_bytes() for f in directory.iterdir()})

    def test_changed_external_manifest_expectation_refuses(self):
        directory, _, _ = self.bundle()
        with self.assertRaisesRegex(HostRefusal, "external manifest"):
            rehearsal.recovery_parts(directory, "0" * 64)

    def test_changed_part_bytes_refuse_without_repairing_them(self):
        directory, checksum, _ = self.bundle()
        part = directory / "part-000000001.json"
        part.write_text('{"changed":true}\n')
        before = part.read_bytes()
        with self.assertRaisesRegex(HostRefusal, "bytes changed"):
            rehearsal.recovery_parts(directory, checksum)
        self.assertEqual(part.read_bytes(), before)

    def test_missing_part_refuses(self):
        directory, checksum, _ = self.bundle()
        (directory / "part-000000001.json").unlink()
        with self.assertRaises(FileNotFoundError):
            rehearsal.recovery_parts(directory, checksum)

    def test_symlink_part_refuses_without_following_it(self):
        directory, checksum, _ = self.bundle()
        part = directory / "part-000000001.json"
        outside = self.root / "outside"
        part.rename(outside)
        part.symlink_to(outside)
        with self.assertRaisesRegex(HostRefusal, "regular file"):
            rehearsal.recovery_parts(directory, checksum)
        self.assertTrue(part.is_symlink())

    def test_nonprivate_part_refuses(self):
        directory, checksum, _ = self.bundle()
        (directory / "part-000000001.json").chmod(0o644)
        with self.assertRaisesRegex(HostRefusal, "private owned"):
            rehearsal.recovery_parts(directory, checksum)

    def test_manifest_symlink_is_not_followed(self):
        directory, checksum, _ = self.bundle()
        manifest = directory / "manifest.jsonl"
        outside = self.root / "manifest-outside"
        manifest.rename(outside)
        manifest.symlink_to(outside)
        with self.assertRaisesRegex(HostRefusal, "manifest must be"):
            rehearsal.recovery_parts(directory, checksum)

    def test_unsupported_bundle_schema_refuses(self):
        directory, _, _ = self.bundle()
        manifest = directory / "manifest.jsonl"
        lines = manifest.read_text().splitlines(keepends=True)
        header = json.loads(lines[0])
        header["schema"] = "unsupported-recovery/1"
        lines[0] = json.dumps(header) + "\n"
        prefix = "".join(lines[:-1])
        checksum = hashlib.sha256(prefix.encode()).hexdigest()
        manifest.write_text(prefix + json.dumps({"sha256": checksum}) + "\n")
        with self.assertRaisesRegex(HostRefusal, "unsupported recovery"):
            rehearsal.recovery_parts(directory, checksum)

    def test_duplicate_manifest_field_refuses(self):
        directory, _, _ = self.bundle()
        manifest = directory / "manifest.jsonl"
        manifest.write_text('{"schema":"a","schema":"b"}\n{}\n{}\n')
        with self.assertRaisesRegex(HostRefusal, "duplicate"):
            rehearsal.recovery_parts(directory, "0" * 64)

    def test_path_escape_refuses_even_with_recomputed_manifest_digest(self):
        directory, _, _ = self.bundle()
        manifest = directory / "manifest.jsonl"
        header, part, _ = [json.loads(line) for line in manifest.read_text().splitlines()]
        part["file"] = "../outside"
        prefix = json.dumps(header) + "\n" + json.dumps(part) + "\n"
        checksum = hashlib.sha256(prefix.encode()).hexdigest()
        manifest.write_text(prefix + json.dumps({"sha256": checksum}) + "\n")
        with self.assertRaisesRegex(HostRefusal, "path or position"):
            rehearsal.recovery_parts(directory, checksum)


class CheckpointTests(unittest.TestCase):
    def header(self):
        return {"schema": "cairn.audit-checkpoint-header/1", "digest_schema": "cairn.audit-checkpoint/1",
                "checkpoint_id": str(uuid.uuid4()), "sha256": "a" * 64, "export_id": "synthetic.dump"}

    def test_header_expectation_does_not_claim_payload_or_import_members(self):
        header = self.header()
        expectation = rehearsal.checkpoint_expectation(header)
        self.assertEqual(expectation, {"checkpoint_id": header["checkpoint_id"],
            "expected_sha256": header["sha256"], "expected_export_id": header["export_id"]})
        self.assertNotIn("members", expectation)

    def test_unsupported_or_incomplete_checkpoint_refuses(self):
        for field, value in [("schema", "unknown"), ("digest_schema", "unknown"), ("export_id", ""), ("sha256", "bad")]:
            with self.subTest(field=field):
                header = self.header()
                header[field] = value
                with self.assertRaises(HostRefusal):
                    rehearsal.checkpoint_expectation(header)
        header = self.header()
        header["members"] = []
        with self.assertRaisesRegex(HostRefusal, "bounded"):
            rehearsal.checkpoint_expectation(header)


class RoutingAndPinsTests(RecoveryFixture):
    def test_missing_requested_tool_refuses_without_a_substitute(self):
        with patch.object(rehearsal.shutil, "which", return_value=None):
            with self.assertRaisesRegex(HostRefusal, "required tool pg_dump is unavailable"):
                rehearsal.resolve_tool("pg_dump")

    def test_fixture_routing_cannot_borrow_live_destinations_and_restores_parent(self):
        values = {"CAIRN_DATABASE_URL": "production-forbidden", "CAIRN_HOME": "/never-open",
                  "CAIRN_AGENT_TOKEN": "never-read", "PGSERVICE": "live-forbidden",
                  "PGPASSFILE": "/never-read", "UNRELATED": "retained"}
        with patch.dict(os.environ, values):
            original = dict(os.environ)
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                with rehearsal.fixture_routing("/pinned-pg"):
                    self.assertNotIn("CAIRN_DATABASE_URL", os.environ)
                    self.assertNotIn("CAIRN_HOME", os.environ)
                    self.assertNotIn("PGSERVICE", os.environ)
                    self.assertEqual(os.environ["PGPASSFILE"], "/dev/null")
                    self.assertEqual(os.environ["HOME"], original["HOME"])
                    self.assertEqual(os.environ.get("CODEX_HOME"), original.get("CODEX_HOME"))
                    self.assertEqual(os.environ["PATH"], original["PATH"])
                    raise RuntimeError("fixture failure")
            self.assertTrue(dict(os.environ) == original, "fixture routing did not restore its environment")

    def test_image_mutation_refuses_before_any_execution(self):
        image = self.root / "owned-image"
        write_private(image, "unit data only, never executed")
        image.chmod(0o500)
        pin = rehearsal.fingerprint(image)
        rehearsal.require_image(pin)
        image.chmod(0o700)
        image.write_text("changed unit data, never executed")
        image.chmod(0o500)
        with self.assertRaisesRegex(HostRefusal, "changed"):
            rehearsal.require_image(pin)

    def test_repinning_unchanged_image_preserves_first_before_use_receipt(self):
        image = self.root / "owned-image"
        write_private(image, "unit fixture, never executed")
        image.chmod(0o500)
        trace = rehearsal.Receipts(self.root / "receipts")
        first = trace.pin(image)
        original = (trace.directory / "pin-0001.json").read_bytes()
        self.assertEqual(trace.pin(image), first)
        self.assertEqual((trace.directory / "pin-0001.json").read_bytes(), original)
        self.assertEqual(len(list(trace.directory.glob("pin-*.json"))), 1)

    def test_unpinned_command_refuses_before_process_creation(self):
        image = self.root / "unit-image"
        write_private(image, "not executable program data")
        image.chmod(0o500)
        trace = rehearsal.Receipts(self.root / "receipts")
        with patch.object(rehearsal.subprocess, "run", side_effect=AssertionError("no process allowed")) as spawn:
            with self.assertRaisesRegex(HostRefusal, "not pinned"):
                trace.command("unit-command", [image])
            spawn.assert_not_called()

    def test_timeout_retains_partial_raw_receipt_and_fails(self):
        image = self.root / "unit-image"
        write_private(image, "unit data only, never executed")
        image.chmod(0o500)
        trace = rehearsal.Receipts(self.root / "receipts")
        trace.pin(image)
        timeout = subprocess.TimeoutExpired([str(image)], 1, output=b"partial stdout", stderr=b"partial stderr")
        with patch.object(rehearsal.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(HostRefusal, "partial raw receipt retained"):
                trace.command("unit-timeout", [image], timeout=1)
        self.assertEqual((trace.directory / "0001-unit-timeout.stdout").read_text(), "partial stdout")
        self.assertEqual((trace.directory / "0001-unit-timeout.stderr").read_text(), "partial stderr")
        result = json.loads((trace.directory / "0001-unit-timeout.result.json").read_text())
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit"])
        self.assertEqual(len(trace.calls), 1)


class EnvelopeTests(unittest.TestCase):
    def encoded(self, ok, status):
        return json.dumps({"schema": "cairn.response/1", "ok": ok, "status": status, "data": {"fixture": True}})

    def test_success_and_real_refusal_have_distinct_contracts(self):
        self.assertEqual(rehearsal.checked_envelope(self.encoded(True, "OK"), "", 0, "OK"), {"fixture": True})
        refusal = rehearsal.checked_envelope(self.encoded(False, "RESTORE_PAUSED"), "", 7, "RESTORE_PAUSED")
        self.assertEqual(refusal["status"], "RESTORE_PAUSED")

    def test_unknown_or_disagreeing_response_cannot_become_success(self):
        for body, exit_code in [(self.encoded(True, "OK"), 1), (self.encoded(False, "RESTORE_PAUSED"), 0),
                                (self.encoded(False, "OTHER"), 7), ("{}", 0), ("not json", 1)]:
            with self.subTest(body=body):
                with self.assertRaises(HostRefusal):
                    rehearsal.checked_envelope(body, "", exit_code, "OK")


class DependencyInventoryTests(RecoveryFixture):
    def inventory_input(self):
        source, external, goroot = (self.root / name for name in ("source", "external", "goroot"))
        for path in [source / "cmd/cairn", external, goroot / "src/unsafe", goroot / "pkg/include"]:
            path.mkdir(mode=0o700, parents=True)
        for path, body in [(source / "go.mod", "module example.test/root\n"), (source / "go.sum", ""),
                (source / "cmd/cairn/main.go", "package main\n"), (source / "cmd/cairn/schema.sql", "SELECT 1;\n"),
                (external / "dep.go", "package dep\n"), (external / "go.mod", "module example.test/dep\n"),
                (goroot / "src/unsafe/unsafe.go", "package unsafe\n"), (goroot / "VERSION", "go1.25.0\n"),
                (goroot / "pkg/include/textflag.h", "unit assembly header\n")]:
            write_private(path, body)
        main = {"Path": "example.test/root", "Main": True, "Dir": str(source), "GoMod": str(source / "go.mod")}
        external_module = {"Path": "example.test/dep", "Version": "v1.2.3", "Sum": "h1:synthetic",
                           "Dir": str(external), "GoMod": str(external / "go.mod")}
        packages = [
            {"ImportPath": "unsafe", "Name": "unsafe", "Standard": True,
             "Dir": str(goroot / "src/unsafe"), "GoFiles": ["unsafe.go"]},
            {"ImportPath": "example.test/dep", "Name": "dep", "Dir": str(external), "Module": external_module,
             "GoFiles": ["dep.go"], "Imports": ["unsafe"], "Deps": ["unsafe"]},
            {"ImportPath": "example.test/root/cmd/cairn", "Name": "main", "Dir": str(source / "cmd/cairn"),
             "Module": main, "GoFiles": ["main.go"], "EmbedFiles": ["schema.sql"],
             "Imports": ["example.test/dep"], "Deps": ["unsafe", "example.test/dep"], "Match": ["./cmd/cairn"]},
        ]
        toolchain = {"GOROOT": str(goroot), "GOVERSION": "go1.25.0", "GOFLAGS": "", "GOWORK": ""}
        return source, toolchain, packages

    def stream(self, packages):
        return "\n".join(json.dumps(package) for package in packages) + "\n"

    def test_selected_closure_pins_sources_embeds_modules_and_toolchain_headers(self):
        source, toolchain, packages = self.inventory_input()
        inventory = rehearsal.dependency_inventory(self.stream(packages), toolchain, source)
        self.assertEqual(inventory["package_count"], 3)
        self.assertEqual(set(inventory["modules"]), {"example.test/root", "example.test/dep"})
        self.assertIn(str(source / "cmd/cairn/schema.sql"), inventory["files"])
        self.assertIn(str(Path(toolchain["GOROOT"]) / "pkg/include/textflag.h"), inventory["files"])
        rehearsal.verify_dependency_sources(inventory)
        (source / "cmd/cairn/schema.sql").write_text("changed embedded source")
        with self.assertRaisesRegex(HostRefusal, "source changed"):
            rehearsal.verify_dependency_sources(inventory)

    def test_partial_error_or_malformed_stream_cannot_pass(self):
        _, _, packages = self.inventory_input()
        cases = [self.stream(packages[:-1]), self.stream(packages[1:]), self.stream(packages)[:-4],
                 self.stream(packages) + "garbage", "[]", self.stream(packages + [packages[0]])]
        for body in cases:
            with self.subTest(case=cases.index(body)):
                with self.assertRaises(HostRefusal):
                    rehearsal.parse_dependencies(body)
        for field, value in [("Error", {"Err": "offline missing selected module"}),
                             ("DepsErrors", [{"Err": "missing source"}]), ("Incomplete", True)]:
            broken = copy.deepcopy(packages)
            broken[-1][field] = value
            with self.subTest(field=field):
                with self.assertRaises(HostRefusal):
                    rehearsal.parse_dependencies(self.stream(broken))

    def test_go_command_failure_with_partial_stdout_keeps_raw_receipts_and_refuses(self):
        _, _, packages = self.inventory_input()
        image = self.root / "go-unit-data"
        write_private(image, "never executed")
        image.chmod(0o500)
        trace = rehearsal.Receipts(self.root / "receipts")
        trace.pin(image)
        output = self.stream(packages[:-1])
        response = subprocess.CompletedProcess([str(image)], 1, output, "module lookup disabled by GOPROXY=off\n")
        with patch.object(rehearsal.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(HostRefusal, "exit 1"):
                trace.command("go-selected-deps", [image, "list", "-deps", "-json", "./cmd/cairn"])
        self.assertEqual((trace.directory / "0001-go-selected-deps.stdout").read_text(), output)
        self.assertEqual(trace.calls[0]["exit"], 1)

    def test_local_replacement_source_and_build_identity_are_retained(self):
        source, toolchain, packages = self.inventory_input()
        module = packages[1]["Module"]
        module["Replace"] = {"Path": "../external", "Dir": module["Dir"], "GoMod": module["GoMod"]}
        inventory = rehearsal.dependency_inventory(self.stream(packages), toolchain, source)
        self.assertEqual(inventory["modules"]["example.test/dep"]["Replace"]["Path"], "../external")
        metadata = ("/owned/cairn: go1.25.0\n\tpath\texample.test/root/cmd/cairn\n"
                    "\tmod\texample.test/root\t(devel)\n\tdep\texample.test/dep\tv1.2.3\n"
                    "\t=>\t../external\t(devel)\n\tbuild\tvcs.revision=owned-commit\n\tbuild\tvcs.modified=false\n")
        rehearsal.verify_buildinfo(metadata, inventory, "owned-commit")
        for changed in [metadata.replace("../external", "../different"), metadata.replace("v1.2.3", "v1.2.4"),
                        metadata.replace("vcs.modified=false", "vcs.modified=true"),
                        metadata + "\tdep\tunused.example/extra\tv9.0.0\n"]:
            with self.assertRaises(HostRefusal):
                rehearsal.verify_buildinfo(changed, inventory, "owned-commit")

    def buildinfo_input(self):
        source, toolchain, packages = self.inventory_input()
        inventory = rehearsal.dependency_inventory(self.stream(packages), toolchain, source)
        metadata = ("/owned/cairn: go1.25.0\n\tpath\texample.test/root/cmd/cairn\n"
                    "\tmod\texample.test/root\t(devel)\n"
                    "\tdep\texample.test/dep\tv1.2.3\th1:synthetic\n")
        return source, inventory, metadata

    def test_unstamped_linked_worktree_retains_false_verification_receipt(self):
        source, inventory, metadata = self.buildinfo_input()
        write_private(source / ".git", "gitdir: /unit/linked-worktree\n")
        trace = Mock(spec=rehearsal.Receipts)
        trace.pin.side_effect = lambda path: {"path": str(path)}
        def command(label, argv, **kwargs):
            stdout = str(self.root) if label == "pg-bindir" else metadata if label == "cairn-build-metadata" else ""
            return subprocess.CompletedProcess(argv, 0, stdout, "")
        trace.command.side_effect = command
        with patch.object(rehearsal, "SOURCE", source), \
                patch.object(rehearsal, "prepare_go_inventory", return_value=({"path": "/unit/go"}, inventory)), \
                patch.object(rehearsal, "resolve_tool", return_value=self.root / "unit-pg-config"):
            rehearsal.prepare_tools(trace, self.root, {"commit": "owned-commit"})
        receipt = json.loads((self.root / "cairn-build-verification.json").read_text())
        self.assertEqual(receipt, {"vcs_stamped": False})

    def test_unstamped_primary_missing_or_symlink_git_context_refuses(self):
        source, inventory, metadata = self.buildinfo_input()
        marker = source / ".git"
        for kind in ["missing", "directory", "symlink"]:
            if kind == "directory":
                marker.mkdir()
            elif kind == "symlink":
                marker.rmdir()
                target = self.root / "unit-git-marker"
                write_private(target, "gitdir: /unit/linked-worktree\n")
                marker.symlink_to(target)
            with self.subTest(context=kind):
                with self.assertRaisesRegex(HostRefusal, "linked Git worktree"):
                    rehearsal.verify_buildinfo(metadata, inventory, "owned-commit", source=source)

    def test_matching_vcs_stamp_records_true_in_primary_checkout(self):
        source, inventory, metadata = self.buildinfo_input()
        (source / ".git").mkdir()
        metadata += "\tbuild\tvcs.revision=owned-commit\n\tbuild\tvcs.modified=false\n"
        self.assertEqual(rehearsal.verify_buildinfo(metadata, inventory, "owned-commit", source=source),
                         {"vcs_stamped": True})

    def test_present_partial_conflicting_or_modified_stamp_cannot_use_unstamped_path(self):
        source, inventory, metadata = self.buildinfo_input()
        write_private(source / ".git", "gitdir: /unit/linked-worktree\n")
        cases = ["\tbuild\tvcs.revision=owned-commit\n", "\tbuild\tvcs.modified=false\n",
                 "\tbuild\tvcs.revision=wrong-commit\n\tbuild\tvcs.modified=false\n",
                 "\tbuild\tvcs.revision=owned-commit\n\tbuild\tvcs.modified=true\n"]
        for settings in cases:
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(HostRefusal, "clean frozen source"):
                    rehearsal.verify_buildinfo(metadata + settings, inventory, "owned-commit", source=source)


class SafeDiagnosticTests(unittest.TestCase):
    def test_failed_full_environment_invariant_does_not_print_values(self):
        original_routing = rehearsal.fixture_routing
        canary = "r07-unit-private-environment-value"
        @contextmanager
        def incomplete_restore(pg_bin):
            try:
                with original_routing(pg_bin):
                    yield
            finally:
                os.environ["R07_UNIT_PRIVATE_VALUE"] = canary
        case = RoutingAndPinsTests("test_fixture_routing_cannot_borrow_live_destinations_and_restores_parent")
        result = unittest.TestResult()
        with patch.object(rehearsal, "fixture_routing", incomplete_restore):
            case.run(result)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(len(result.errors), 0)
        diagnostic = result.failures[0][1]
        self.assertTrue(canary not in diagnostic, "environment values leaked into failure diagnostics")
        self.assertTrue("fixture routing did not restore its environment" in diagnostic,
                        "full environment preservation did not produce its fixed diagnostic")


if __name__ == "__main__":
    unittest.main()
