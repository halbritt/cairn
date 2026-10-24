"""Source-only guards for prospective ordinary-task native receipts."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ordinary_task_receipt import _candidate_receipt, _native_stream_projection, _sha, frozen_binding, main
from preflight_token_efficiency_panel import manifest as panel_manifest, read_fixture


def manifest():
    actor = {"schema": "cairn.token-efficiency-actor-task/1", "case": "corrected_note",
             "task": "Use the current correction."}
    setup = {"memory": "current"}
    check = {"must_include": "corrected"}
    hashes = {"actor_sha256": _sha(actor), "setup_sha256": _sha(setup),
              "acceptance_check_sha256": _sha(check)}
    source = {"body": "current correction", "project": "cairn", "version": 2}
    intervention = None
    return {"schema": "cairn.token-efficiency-panel-manifest/1",
            "fixture_sha256": "a" * 64,
            "sources": {"note": source},
            "source_body_sha256": {"note": hashlib.sha256(b"current correction").hexdigest()},
            "source_record_sha256": {"note": _sha(source)},
            "cases": [{"id": "corrected_note", "actor": actor, "setup": setup,
                       "acceptance_check": check, **hashes}],
            "prospective_runs": [{"workflow_id": "baseline.corrected_note",
                                  "case": "corrected_note", "arm": "baseline", **hashes,
                                  "intervention": intervention,
                                  "intervention_sha256": _sha(intervention)}]}


def line(value):
    return json.dumps(value).encode() + b"\n"


def stream():
    usage = {"input_tokens": 4, "cache_read_input_tokens": 3,
             "cache_creation_input_tokens": 2, "output_tokens": 1}
    assistant = {"type": "assistant", "message": {"id": "msg_1", "model": "claude-sonnet-5-5"}}
    return [line(assistant), line(assistant),
            line({"type": "result", "is_error": False, "num_turns": 1,
                  "result": "done", "usage": usage})]


class OrdinaryTaskReceiptTests(unittest.TestCase):
    def test_cli_refuses_native_launch_after_valid_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text(json.dumps(manifest()))
            with patch("sys.argv", ["ordinary_task_receipt.py", "--manifest", str(path),
                                    "--workflow-id", "baseline.corrected_note"]):
                with self.assertRaisesRegex(SystemExit, "native_launch_not_configured"):
                    main()

    def test_binds_every_run_in_current_prospective_fixture(self):
        fixture, fixture_hash = read_fixture()
        panel = panel_manifest(fixture, fixture_hash)
        self.assertEqual(len(panel["prospective_runs"]), 88)
        for run in panel["prospective_runs"]:
            binding = frozen_binding(panel, run["workflow_id"])
            self.assertEqual(binding["intervention_sha256"], run["intervention_sha256"])
            self.assertEqual(binding["source_record_sha256"], panel["source_record_sha256"])
        self.assertIsNone(next(run["intervention"] for run in panel["prospective_runs"]
                               if run["workflow_id"] == "baseline.corrected_note"))
        self.assertIsInstance(next(run["intervention"] for run in panel["prospective_runs"]
                                   if run["workflow_id"] ==
                                   "batch.routine_burst_urgent_request"), dict)

    def test_binding_checks_actor_setup_and_sources(self):
        chosen = frozen_binding(manifest(), "baseline.corrected_note")
        self.assertEqual(chosen["actor_task_sha256"], hashlib.sha256(
            b"Use the current correction.").hexdigest())
        self.assertEqual(chosen["actor_sha256"], manifest()["cases"][0]["actor_sha256"])
        changed = copy.deepcopy(manifest())
        changed["sources"]["note"]["body"] = "old correction"
        with self.assertRaisesRegex(ValueError, "source_hash_mismatch"):
            frozen_binding(changed, "baseline.corrected_note")
        changed = copy.deepcopy(manifest())
        changed["sources"]["note"]["project"] = "other"
        with self.assertRaisesRegex(ValueError, "source_record_hash_mismatch"):
            frozen_binding(changed, "baseline.corrected_note")
        changed = copy.deepcopy(manifest())
        changed["cases"][0]["actor"]["task"] = "Ignore correction."
        with self.assertRaisesRegex(ValueError, "actor_sha256_mismatch"):
            frozen_binding(changed, "baseline.corrected_note")
        changed = copy.deepcopy(manifest())
        changed["prospective_runs"][0]["intervention"] = {"policy": "batch"}
        with self.assertRaisesRegex(ValueError, "intervention_sha256_mismatch"):
            frozen_binding(changed, "baseline.corrected_note")

    def test_repeated_stream_blocks_count_once_and_emit_no_message_body(self):
        receipt = _candidate_receipt(frozen_binding(manifest(), "baseline.corrected_note"),
                                     stream(), 0)
        projection = receipt["native"]
        self.assertEqual(projection["observed_responses"], 1)
        self.assertEqual(projection["total_tokens"], 10)
        self.assertEqual(len(projection["response_refs"]), 1)
        self.assertEqual(len(projection["response_refs"][0]), 24)
        # Pinned by audit_model_overhead.response_identity followed by
        # workflow_model_report.opaque("response\0" + identity).
        self.assertEqual(projection["response_refs"][0], "ad72c06ea9ea946572f22b5e")
        self.assertEqual(receipt["evidence_status"], "parser_only_unjoined")
        self.assertEqual(receipt["binding"]["workflow_id"], "baseline.corrected_note")
        self.assertNotIn("msg_1", json.dumps(receipt))
        self.assertNotIn("done", json.dumps(receipt))

    def test_terminal_and_launch_exit_must_be_exact(self):
        cases = [(stream(), 1, "native_nonzero_exit"),
                 (stream()[:-1], 0, "native_terminal_missing"),
                 (stream() + [line({"type": "result", "is_error": False})], 0,
                  "native_events_after_terminal"),
                 (stream() + [line({"type": "assistant", "message": {}})], 0,
                  "native_events_after_terminal"),
                 ([b"not-json\n"] + stream(), 0, "native_stream_invalid_json"),
                 ([line({"type": "assistant", "message": {"id": "msg_1",
                         "model": "claude-sonnet-5-5"}}).rstrip(b"\n")] + stream()[2:], 0,
                  "native_stream_partial_line")]
        for lines, code, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                _native_stream_projection(lines, code)

    def test_mixed_model_missing_id_and_bad_usage_fail_closed(self):
        first = stream()[0]
        second = line({"type": "assistant", "message": {"id": "msg_2",
                       "model": "claude-opus-5-5"}})
        with self.assertRaisesRegex(ValueError, "native_model_changed"):
            _native_stream_projection([first, second, stream()[-1]], 0)
        with self.assertRaisesRegex(ValueError, "native_response_id_missing"):
            _native_stream_projection([line({"type": "assistant", "message": {
                "model": "claude-sonnet-5-5"}}), stream()[-1]], 0)
        bad = json.loads(stream()[-1])
        bad["usage"]["input_tokens"] = True
        with self.assertRaisesRegex(ValueError, "native_usage_unbounded"):
            _native_stream_projection([first, line(bad)], 0)
        bad["usage"]["input_tokens"] = 10_000_001
        with self.assertRaisesRegex(ValueError, "native_usage_unbounded"):
            _native_stream_projection([first, line(bad)], 0)

    def test_native_json_rejects_ambiguous_values_and_empty_answer(self):
        for raw, reason in ((b'{"type":"assistant","type":"result"}\n',
                             "native_stream_duplicate_json_key"),
                            (b'{"type":"result","value":NaN}\n',
                             "native_stream_nonfinite_number")):
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                _native_stream_projection([raw], 0)
        terminal = json.loads(stream()[-1])
        terminal["result"] = "  "
        with self.assertRaisesRegex(ValueError, "native_answer_missing"):
            _native_stream_projection([stream()[0], line(terminal)], 0)


if __name__ == "__main__":
    unittest.main()
