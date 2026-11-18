"""Checkpoint references stay inspectable without granting execution authority."""

from contextlib import redirect_stdout, redirect_stderr
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import development_checkpoint as checkpoint


RECORD_ID = "14107117-4d2b-4bca-b107-1bff0e5237cc"


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.txt"
        self.source.write_text("source\n")
        self.spec = self.root / "spec.json"
        self.output = self.root / "checkpoint.json"
        self.declaration = {
            "schema": checkpoint.SCHEMA,
            "project": "fixture",
            "goal": "finish the existing task",
            "owner": {"name": "worker", "provenance": "test assignment"},
            "workspace": ".",
            "next_action": "inspect the original operation before retry",
            "references": [{"name": "source", "kind": "file", "path": "source.txt"}],
            "effects": [{"operation_id": "stable-operation", "outcome": "unknown",
                         "reconciliation": "inspect the authoritative ledger", "source": "source"}],
            "questions": ["Did the original operation commit?"],
        }

    def pack(self, clients=None):
        self.spec.write_text(json.dumps(self.declaration))
        return checkpoint.pack(self.spec, self.output, clients or checkpoint.Clients())

    def fake_cairn(self, *, current=1, status=None, body="selected source", payload=True):
        response = {"ok": True, "data": {
            "record_id": RECORD_ID, "current_version": current, "current_lifecycle": "active",
            "versions": [{"version": 1, "payload_available": payload, "body": body,
                          "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
                          "written_at": "2026-11-17T12:00:00Z", "observed_writer": "agent/fixture"}],
        }}
        if status:
            response = {"ok": False, "status": status}
        response_path = self.root / "cairn-response.json"
        response_path.write_text(json.dumps(response))
        script = self.root / "cairn"
        captured = self.root / "cairn-request.json"
        script.write_text(f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n"
                          f"Path({str(captured)!r}).write_text(json.dumps({{'argv':sys.argv[1:],'request':json.load(sys.stdin)}}))\n"
                          f"print(Path({str(response_path)!r}).read_text())\n")
        script.chmod(0o700)
        return checkpoint.Clients(cairn=str(script), token_file=str(self.root / "never-read.token"))

    def fake_evidence(self, *, freshness="current", outcome="findings-open"):
        receipt = self.root / "evidence.json"
        receipt.write_text('{"selected":"receipt"}\n')
        sha = hashlib.sha256(receipt.read_bytes()).hexdigest()
        head = "1" * 40
        reports = {
            "inspect": {"schema": "development-evidence-inspection/1", "valid": True,
                        "receipt_sha256": sha, "diagnostics": [], "artifacts": [],
                        "record": {"subject": {"candidate": head, "snapshot": None},
                                   "recorded_at": "2026-11-17T12:00:00Z", "outcome": outcome,
                                   "observations": []}},
            "compare": {"schema": "development-evidence-comparison/1", "freshness": freshness,
                        "receipt_sha256": sha, "outcome": outcome, "reasons": [], "limits": [],
                        "current_subject": {"head": head, "candidate": head, "dirty": False, "snapshot": None}},
        }
        response_path = self.root / "evidence-response.json"
        response_path.write_text(json.dumps(reports))
        captured = self.root / "evidence-calls.jsonl"
        script = self.root / "evidence-tool"
        script.write_text(f"#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n"
                          f"with open({str(captured)!r}, 'a') as stream: stream.write(json.dumps(sys.argv[1:])+'\\n')\n"
                          f"print(json.dumps(json.loads(Path({str(response_path)!r}).read_text())[sys.argv[1]]))\n"
                          f"sys.exit({{'current':0,'stale':1,'unknown':2,'missing':3}}[{freshness!r}] if sys.argv[1]=='compare' else 0)\n")
        script.chmod(0o700)
        self.declaration["references"].append({"name": "review", "kind": "evidence",
                                               "role": "reviewed", "path": "evidence.json"})
        return checkpoint.Clients(evidence_tool=str(script))

    def test_pack_and_inspect_preserve_context_and_selected_bytes(self):
        packed = self.pack()
        self.assertEqual(packed["reference_count"], 1)
        envelope = json.loads(self.output.read_text())
        self.assertEqual(envelope["references"][0]["path"], str(self.source))
        self.assertEqual(envelope["references"][0]["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        inspected = checkpoint.inspect_checkpoint(self.output, checkpoint.Clients())
        self.assertEqual(inspected["reference_status"], "current")
        self.assertEqual(inspected["declared"]["owner"], self.declaration["owner"])
        self.assertNotIn("body", envelope["references"][0])
        with self.assertRaises(FileExistsError):
            self.pack()

    def test_changed_and_missing_file_do_not_retain_current_status(self):
        self.pack()
        expected_sha = json.loads(self.output.read_text())["references"][0]["sha256"]
        self.source.write_text("changed\n")
        changed = checkpoint.inspect_checkpoint(self.output, checkpoint.Clients())
        self.assertEqual(changed["reference_status"], "stale")
        self.assertEqual(changed["references"][0]["sha256"], expected_sha)
        self.assertNotEqual(changed["references"][0]["observed_sha256"], expected_sha)
        self.source.unlink()
        missing = checkpoint.inspect_checkpoint(self.output, checkpoint.Clients())
        self.assertEqual(missing["reference_status"], "missing")
        view = checkpoint.status_view(missing)
        self.assertEqual(view["effects"][0]["source_status"], "missing")
        self.assertEqual(view["effects"][0]["observed_outcome"], "unknown")
        self.assertEqual(view["task_outcome"], "unverified")

    def test_reference_symlink_is_not_silently_followed(self):
        target = self.root / "target"
        target.write_text("bytes")
        self.source.unlink()
        self.source.symlink_to(target)
        with self.assertRaisesRegex(checkpoint.CheckpointError, "regular file"):
            self.pack()
        self.assertFalse(self.output.exists())

    def test_declared_commands_are_never_executed(self):
        marker = self.root / "executed"
        self.declaration["entrypoint"] = {
            "argv": [sys.executable, "-c", f"open({str(marker)!r},'w').write('bad')"],
            "cwd": ".", "versions": {"python": "3"},
        }
        self.declaration["effects"][0]["reconciliation"] = f"touch {marker}"
        self.pack()
        result = checkpoint.status_view(checkpoint.inspect_checkpoint(self.output, checkpoint.Clients()))
        self.assertEqual(result["declared"]["entrypoint"]["argv"], self.declaration["entrypoint"]["argv"])
        self.assertFalse(marker.exists())

    def test_exact_cairn_source_is_read_with_the_existing_token_path(self):
        self.declaration["references"] = [{"name": "source", "kind": "cairn",
                                           "record_id": RECORD_ID, "version": 1}]
        clients = self.fake_cairn()
        self.pack(clients)
        inspected = checkpoint.inspect_checkpoint(self.output, clients)
        self.assertEqual(inspected["reference_status"], "current")
        request = json.loads((self.root / "cairn-request.json").read_text())
        self.assertEqual(request["argv"], ["agent", "--token-file", clients.token_file, "history"])
        self.assertEqual(request["request"], {"record_id": RECORD_ID, "version": 1})
        self.assertFalse(Path(clients.token_file).exists())
        self.assertNotIn("selected source", self.output.read_text())

    def test_newer_cairn_version_marks_selected_context_stale(self):
        self.declaration["references"] = [{"name": "source", "kind": "cairn",
                                           "record_id": RECORD_ID, "version": 1}]
        clients = self.fake_cairn()
        self.pack(clients)
        clients = self.fake_cairn(current=2)
        observed = checkpoint.inspect_checkpoint(self.output, clients)
        self.assertEqual(observed["reference_status"], "stale")
        self.assertEqual(observed["references"][0]["current_version"], 2)

    def test_missing_or_unavailable_cairn_source_is_explicit(self):
        self.declaration["references"] = [{"name": "source", "kind": "cairn",
                                           "record_id": RECORD_ID, "version": 1}]
        self.pack(self.fake_cairn())
        for status, expected in (("NOT_FOUND", "missing"), ("DESTINATION_PROHIBITED", "unknown")):
            with self.subTest(status=status):
                observed = checkpoint.inspect_checkpoint(self.output, self.fake_cairn(status=status))
                self.assertEqual(observed["reference_status"], expected)
        unavailable = checkpoint.inspect_checkpoint(self.output, self.fake_cairn(payload=False))
        self.assertEqual(unavailable["reference_status"], "unknown")

    def test_cairn_identity_and_digest_mismatches_are_unknown(self):
        self.declaration["references"] = [{"name": "source", "kind": "cairn",
                                           "record_id": RECORD_ID, "version": 1}]
        clients = self.fake_cairn()
        self.pack(clients)
        response_path = self.root / "cairn-response.json"
        original = json.loads(response_path.read_text())
        for field, replacement in (("version", 2), ("body_sha256", "0" * 64)):
            with self.subTest(field=field):
                response = copy.deepcopy(original)
                response["data"]["versions"][0][field] = replacement
                response_path.write_text(json.dumps(response))
                observed = checkpoint.inspect_checkpoint(self.output, clients)
                self.assertEqual(observed["reference_status"], "unknown")

    def test_bad_declarations_are_rejected_before_output(self):
        for field, replacement in (("schema", "future/99"), ("owner", "worker"),
                                   ("references", []), ("effects", [{"command": "touch bad"}])):
            with self.subTest(field=field):
                original = copy.deepcopy(self.declaration)
                self.declaration[field] = replacement
                with self.assertRaises(checkpoint.CheckpointError):
                    self.pack()
                self.assertFalse(self.output.exists())
                self.declaration = original

    def test_status_reuses_evidence_tool_and_keeps_outcome_separate(self):
        clients = self.fake_evidence()
        self.declaration["findings"] = [{"id": "F1", "summary": "reported issue",
                                         "disposition": "open", "source": "review"}]
        self.pack(clients)
        view = checkpoint.status_view(checkpoint.inspect_checkpoint(self.output, clients, "main"))
        review = view["candidates"]["reviewed"][0]
        self.assertEqual(view["reference_status"], "current")
        self.assertEqual(review["candidate"], "1" * 40)
        self.assertEqual(review["outcome"], "findings-open")
        self.assertEqual(view["current_subject"]["candidate"], "1" * 40)
        self.assertEqual(view["findings"][0]["observed_disposition"], "unverified")
        calls = [json.loads(line) for line in (self.root / "evidence-calls.jsonl").read_text().splitlines()]
        self.assertEqual(calls[0], ["inspect", str(self.root / "evidence.json"), "--json"])
        self.assertEqual(calls[1], ["compare", str(self.root / "evidence.json"), "--repo", str(self.root),
                                   "--json", "--base", "main"])

    def test_stale_or_unavailable_evidence_is_not_a_clearance(self):
        clients = self.fake_evidence(freshness="stale", outcome="clear-within-scope")
        self.pack(clients)
        view = checkpoint.status_view(checkpoint.inspect_checkpoint(self.output, clients))
        self.assertEqual(view["reference_status"], "stale")
        self.assertEqual(view["candidates"]["reviewed"][0]["outcome"], "clear-within-scope")
        self.assertEqual(view["task_outcome"], "unverified")
        unavailable = checkpoint.Clients(evidence_tool=str(self.root / "no-such-tool"))
        view = checkpoint.status_view(checkpoint.inspect_checkpoint(self.output, unavailable))
        self.assertEqual(view["reference_status"], "unknown")
        self.assertIsNone(view["candidates"]["reviewed"][0]["candidate"])

    def test_changed_receipt_does_not_replace_selected_review(self):
        clients = self.fake_evidence()
        self.pack(clients)
        (self.root / "evidence.json").write_text('{"different":"receipt"}')
        view = checkpoint.status_view(checkpoint.inspect_checkpoint(self.output, clients))
        self.assertEqual(view["reference_status"], "stale")
        self.assertIsNone(view["candidates"]["reviewed"][0]["outcome"])
        self.assertFalse((self.root / "evidence-calls.jsonl").exists())

    def test_evidence_changing_between_reads_cannot_be_reported_current(self):
        clients = self.fake_evidence()
        self.pack(clients)
        response_path = self.root / "evidence-response.json"
        reports = json.loads(response_path.read_text())
        reports["compare"]["receipt_sha256"] = "0" * 64
        response_path.write_text(json.dumps(reports))
        observed = checkpoint.inspect_checkpoint(self.output, clients)
        self.assertEqual(observed["reference_status"], "unknown")
        self.assertIn("changed during inspection", observed["references"][1]["reason"])

    def test_evidence_exit_status_cannot_contradict_current_claim(self):
        clients = self.fake_evidence()
        self.pack(clients)
        script = Path(clients.evidence_tool)
        script.write_text(script.read_text().rsplit("sys.exit", 1)[0] + "sys.exit(2 if sys.argv[1]=='compare' else 0)\n")
        observed = checkpoint.inspect_checkpoint(self.output, clients)
        self.assertEqual(observed["reference_status"], "unknown")
        self.assertIn("exit code disagree", observed["references"][1]["reason"])

    def test_invalid_cli_encoding_is_unknown_instead_of_an_uncaught_exception(self):
        self.declaration["references"] = [{"name": "source", "kind": "cairn",
                                           "record_id": RECORD_ID, "version": 1}]
        clients = self.fake_cairn()
        self.pack(clients)
        Path(clients.cairn).write_text(f"#!{sys.executable}\nimport os\nos.write(1,bytes([255]))\n")
        observed = checkpoint.inspect_checkpoint(self.output, clients)
        self.assertEqual(observed["reference_status"], "unknown")
        self.assertIn("source command unavailable", observed["references"][0]["reason"])

    def test_checkpoint_change_during_inspection_rejects_the_mixed_snapshot(self):
        self.declaration["references"] = [{"name": "source", "kind": "cairn",
                                           "record_id": RECORD_ID, "version": 1}]
        clients = self.fake_cairn()
        self.pack(clients)
        script = Path(clients.cairn)
        script.write_text(script.read_text() + f"Path({str(self.output)!r}).write_text('{{\"changed\":true}}')\n")
        with self.assertRaisesRegex(checkpoint.CheckpointError, "checkpoint changed during inspection"):
            checkpoint.inspect_checkpoint(self.output, clients)

    def test_cli_reports_missing_refs_and_malformed_envelope_separately(self):
        self.pack()
        self.source.unlink()
        output = io.StringIO()
        with redirect_stdout(output):
            code = checkpoint.main(["status", str(self.output), "--text"])
        self.assertEqual(code, 1)
        self.assertIn("References: missing", output.getvalue())
        self.output.write_text("{")
        errors = io.StringIO()
        with redirect_stderr(errors):
            code = checkpoint.main(["inspect", str(self.output)])
        self.assertEqual(code, 2)
        self.assertIn("cannot read JSON", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
