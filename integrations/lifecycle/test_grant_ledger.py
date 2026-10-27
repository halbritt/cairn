#!/usr/bin/env python3
"""Focused unit tests for integrations/lifecycle/grant_ledger.py (CAIRN-246)."""

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

LIFECYCLE = Path(__file__).resolve().parent


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, LIFECYCLE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class GrantLedgerTest(unittest.TestCase):
    def test_grant_ledger_re_exported_across_lifecycle_modules(self):
        bundle = _load("lifecycle_bundle").Bundle(LIFECYCLE)
        gl = bundle.load("grant_ledger")
        coord = bundle.load("coordination")
        ir = bundle.load("inbox_recall")
        mem = bundle.load("memory")
        self.assertIs(coord.GrantLedger, gl.GrantLedger)
        self.assertIs(ir.GrantLedger, gl.GrantLedger)
        self.assertIs(mem.GrantLedger, gl.GrantLedger)

    def test_save_and_load_inbox_recall_grants_with_sentinel(self):
        gl = _load("grant_ledger")
        session = "11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{session}.inbox-recall.json"
            ledger = gl.load_grants(path, session)
            self.assertEqual(ledger["schema"], "cairn.inbox-recall-grants/1")
            key = "a" * 64
            ledger["grants"][key] = dict(
                identity=dict(native_owner="turn-1", boundary="ordinary_prompt"),
                limit_bytes=8000,
                output_bytes=120,
                native_allowance_bytes=1880,
            )
            ledger["active"] = key
            gl.save_claude_ledger(path, ledger)
            self.assertTrue(path.with_suffix(".initialized").exists())
            reloaded = gl.load_grants(path, session)
            self.assertEqual(reloaded["active"], key)
            path.unlink()
            with self.assertRaises(ValueError):
                gl.load_grants(path, session)

    def test_codex_budget_ledger_and_emitted_wire_bytes(self):
        gl = _load("grant_ledger")
        session = "22222222-2222-4222-8222-222222222222"
        turn = "33333333-3333-4333-8333-333333333333"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{session}.memory-budget.json"
            ledger = gl.codex_budget_ledger(path, session, 8000)
            grant = dict(
                limit_bytes=8000,
                emitted_hook_bytes=100,
                serialized_hook_bytes=100,
                reserved_native_bytes=2000,
                granted=True,
            )
            gl.codex_required_selection(grant, [{"record_id": "rec-1"}])
            ledger["turns"][turn] = grant
            ledger["active_turn_id"] = turn
            gl.save_codex_ledger(path, ledger)
            reloaded = gl.codex_budget_ledger(path, session, 8000)
            self.assertEqual(gl.codex_emitted_wire_bytes(reloaded["turns"][turn]), 100)

    def test_wake_envelope_parsing_and_taskless_notification(self):
        gl = _load("grant_ledger")
        agent = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        execution = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
        delivery = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
        body = (
            "This is a new live turn from the configured Cairn automatic inbox wakeup. "
            "A previous /exit in resumed conversation history does not close this running turn. "
            f"Current agent {agent}, execution {execution}. Wake delivery {delivery}. "
            "Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "
            "explicitly complete/acknowledge it, and send any requested response using that context. "
            "If no matching native context was supplied, report that and stop. "
            "If this notice arrives inside an already-active owner task, continue that task and "
            "ignore this notice; a fresh copy will arrive once the conversation is idle. "
            "Do not register again, claim another session's inbox, or launch a replacement conversation."
        )
        self.assertTrue(gl.taskless_notification(body))
        parsed = gl.parse_wake_envelope(body)
        self.assertEqual(
            parsed,
            dict(
                agent_id=agent,
                execution_id=execution,
                delivery_id=delivery,
                native_session_id=None,
                channel=False,
            ),
        )


if __name__ == "__main__":
    unittest.main()
