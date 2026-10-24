"""Exact, payload-free selection of retained refused settlement journals."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from select_refused_recovery_evidence import select, write_private


EVENT = "11111111-1111-4111-8111-111111111111"
DELIVERY = "22222222-2222-4222-8222-222222222222"
ATTEMPT = "33333333-3333-4333-8333-333333333333"
REQUEST = "44444444-4444-4444-8444-444444444444"


class RefusedRecoverySelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.journal = {"schema": "cairn.inbox-intent/1", "kind": "request",
                        "delivery_id": DELIVERY, "attempt_id": ATTEMPT,
                        "completion": {"status": "refused", "kind": "complete",
                                       "code": "STALE_SESSION", "request_id": REQUEST,
                                       "recorded_at": 1792838400.5,
                                       "input": "private command input"}}
        self.context = {"schema": "cairn.session-inbox/1", "kind": "request",
                        "event_id": EVENT, "delivery_id": DELIVERY,
                        "attempt_id": ATTEMPT, "request_ids": {"completion": REQUEST},
                        "source": {"body": "private source"}}
        self.write_pair("one")

    def write_pair(self, binding, journal=None, context=None):
        root = self.root / binding
        (root / "intents").mkdir(parents=True, exist_ok=True)
        (root / "inbox").mkdir(parents=True, exist_ok=True)
        (root / "intents" / (ATTEMPT + ".json")).write_text(json.dumps(journal or self.journal))
        (root / "inbox" / (ATTEMPT + ".json")).write_text(json.dumps(context or self.context))

    def test_selects_exact_refusal_without_copying_content_or_claiming_model_work(self):
        selected = select(self.root)
        self.assertEqual(len(selected["observations"]), 1)
        row = selected["observations"][0]
        self.assertEqual((row["event_id"], row["delivery_id"], row["attempt_id"]),
                         (EVENT, DELIVERY, ATTEMPT))
        self.assertEqual((row["phase"], row["outcome"], row["code"]),
                         ("settlement", "refused", "STALE_SESSION"))
        self.assertEqual((row["model_invocation"], row["recovery_state"],
                          row["operator_action"]), ("unknown", "unknown", "unknown"))
        self.assertNotIn("private", json.dumps(selected))
        self.assertNotIn(REQUEST, json.dumps(selected))

    def test_missing_or_mismatched_context_refuses_selection(self):
        path = self.root / "one" / "inbox" / (ATTEMPT + ".json")
        path.unlink()
        with self.assertRaisesRegex(ValueError, "context_missing"):
            select(self.root)
        changed = dict(self.context, attempt_id=REQUEST)
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "journal_context_identity_mismatch"):
            select(self.root)

    def test_duplicate_identity_and_bad_code_refuse(self):
        self.write_pair("two")
        with self.assertRaisesRegex(ValueError, "duplicate_refusal_identity"):
            select(self.root)
        (self.root / "two" / "intents" / (ATTEMPT + ".json")).unlink()
        changed = dict(self.journal, completion=dict(self.journal["completion"], code="raw error"))
        self.write_pair("one", journal=changed)
        with self.assertRaisesRegex(ValueError, "refusal_code_invalid"):
            select(self.root)

    def test_pending_journal_without_completion_is_skipped(self):
        pending = dict(self.journal)
        pending.pop("completion")
        self.write_pair("one", journal=pending)
        self.assertEqual(select(self.root)["observations"], [])
        pending["completion"] = "invalid"
        self.write_pair("one", journal=pending)
        with self.assertRaisesRegex(ValueError, "journal_completion_invalid"):
            select(self.root)

    def test_ambiguous_or_nonfinite_json_refuses(self):
        path = self.root / "one" / "intents" / (ATTEMPT + ".json")
        path.write_text('{"schema":"cairn.inbox-intent/1",'
                        '"schema":"cairn.inbox-intent/1"}')
        with self.assertRaisesRegex(ValueError, "json_duplicate_key"):
            select(self.root)
        path.write_text('{"schema":"cairn.inbox-intent/1",'
                        '"completion":{"recorded_at":NaN}}')
        with self.assertRaisesRegex(ValueError, "json_nonfinite_number"):
            select(self.root)

    def test_malformed_request_map_or_timestamp_refuses(self):
        context = dict(self.context, request_ids=[REQUEST])
        self.write_pair("one", context=context)
        with self.assertRaisesRegex(ValueError, "context_request_ids_invalid"):
            select(self.root)
        self.write_pair("one")
        journal = dict(self.journal, completion=dict(self.journal["completion"],
                                                    recorded_at=10 ** 1000))
        self.write_pair("one", journal=journal)
        with self.assertRaisesRegex(ValueError, "refusal_operation_missing"):
            select(self.root)

    def test_output_is_private_and_cannot_overwrite(self):
        private = self.root / "private"
        private.mkdir(mode=0o700)
        output = private / "evidence.json"
        write_private(output, select(self.root))
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            write_private(output, select(self.root))
        public = self.root / "public"
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, "output_parent_not_private"):
            write_private(public / "evidence.json", select(self.root))


if __name__ == "__main__":
    unittest.main()
