"""Self-contained unit tests for scripts/trial_approval_control.py (CAIRN-157).

Exercises atomic hardlink publication (nlink == 2 while staged link is held),
policy hash and identity binding (A1..A3 allow, D1..D3 deny), union vs sum
approval wait interval accounting (total_wait_seconds vs
total_request_wait_seconds), cancellation and timeout before teardown, and
counted slot-3 (rhumb-changelog.current.s0) admission validation preserving
the immutable consumed 2/72 prefix.
"""
from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import trial_approval_control as control
from trial_approval_control import (
    ApprovalControl,
    ApprovalError,
    decide,
    decision_for,
    digest,
    publish,
    read_private,
)

PREDECESSOR_LEDGER_SHA256 = (
    "ac621601384aa2d9a90b83e2ff6a3e17f913a2fef12e2747567062696c8e2454"
)
BINDING = dict(
    run_id="rhumb-changelog.current.s0",
    run_execution_id="offline-run-slot3",
    freeze_sha256="a" * 64,
    root_execution_id="offline-root-1",
    admission=3,
    approval_policy_sha256="b" * 64,
)
COMMAND = "printf '%s\\n' 'synthetic exact input' > marker"


def frame(value):
    return (json.dumps(value) + "\n").encode("utf-8")


def permission(ident="request1", tool="Bash", tool_id="toolu_state", cmd=COMMAND):
    return dict(
        type="control_request",
        request_id=ident,
        request=dict(
            subtype="can_use_tool",
            tool_name=tool,
            input={"command": cmd},
            tool_use_id=tool_id,
        ),
    )


def ready(base_dir, binding=None):
    b = dict(BINDING if binding is None else binding)
    host = ApprovalControl(Path(base_dir) / "approval", b, b'{"type":"user"}\n')
    host.wrote(len(host.suffix()))
    host.receive(
        frame(
            dict(
                type="control_response",
                response=dict(subtype="success", request_id=host.init_id, response={}),
            )
        )
    )
    host.wrote(len(host.suffix()))
    host.receive(
        frame(
            dict(
                type="system",
                subtype="init",
                session_id="state-session",
                model="claude-opus-5-5",
            )
        )
    )
    return host


class ApprovalControlTests(unittest.TestCase):
    def approve(
        self,
        host,
        ident="request1",
        behavior="allow",
        clause="A3",
        review_note="Offline fixture scope reviewed.",
    ):
        path = host.requests / (digest(ident.encode("utf-8")) + ".json")
        return decide(
            path,
            expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            behavior=behavior,
            root_execution_id=BINDING["root_execution_id"],
            clause=clause,
            review_note=review_note,
        )

    def test_poller_accepts_complete_decision_while_publisher_holds_second_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            host = ready(p)
            host.receive(frame(permission()))
            request = next((p / "approval/requests").glob("*.json"))
            linked = threading.Event()
            release = threading.Event()
            errors = []
            real_link = os.link

            def paused_link(source, target, **kwargs):
                real_link(source, target, **kwargs)
                if Path(target).parent == p / "approval/decisions":
                    linked.set()
                    if not release.wait(4):
                        raise AssertionError("publication barrier not released")

            def root_publish():
                try:
                    decide(
                        request,
                        expected_sha256=hashlib.sha256(request.read_bytes()).hexdigest(),
                        behavior="allow",
                        root_execution_id=BINDING["root_execution_id"],
                        clause="A3",
                        review_note="Offline fixture scope reviewed.",
                    )
                except BaseException as exc:
                    errors.append(exc)

            publisher = threading.Thread(target=root_publish)
            with patch.object(os, "link", side_effect=paused_link):
                try:
                    publisher.start()
                    self.assertTrue(linked.wait(2))
                    target = p / "approval/decisions" / request.name
                    self.assertEqual(target.stat().st_nlink, 2)
                    _, value = read_private(target)
                    self.assertEqual(value["behavior"], "allow")
                    host.poll()
                    host.wrote(len(host.suffix()))
                    self.assertTrue(publisher.is_alive())
                    host.close("completed")
                    summary = json.loads((p / "approval/summary.json").read_text())
                    self.assertEqual(summary["responses_completed"], 1)
                finally:
                    release.set()
                    publisher.join(4)
            self.assertEqual(errors, [])
            self.assertEqual(target.stat().st_nlink, 1)

    def test_malformed_frame_poison_cannot_be_caught_and_resumed(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = ApprovalControl(Path(tmp) / "approval", BINDING, b'{"type":"user"}\n')
            with self.assertRaises(ApprovalError):
                host.receive(b"{malformed")
            with self.assertRaises(ApprovalError):
                host.poll()
            host.close("approval_protocol_failure")

    def test_policy_clause_and_identity_binding_allow_and_deny(self):
        with tempfile.TemporaryDirectory() as tmp:
            host = ready(Path(tmp))
            host.receive(frame(permission()))
            for behavior, clause, note in [
                ("allow", "D1", "Reviewed."),
                ("deny", "A1", "Reviewed."),
                ("allow", "A3", " "),
                ("allow", "A4", "Reviewed."),
            ]:
                with self.subTest(clause=clause, note=note), self.assertRaises(ApprovalError):
                    self.approve(host, behavior=behavior, clause=clause, review_note=note)
            for idx, (behavior, clause) in enumerate(
                [("allow", "A1"), ("allow", "A2"), ("allow", "A3"), ("deny", "D1"), ("deny", "D2"), ("deny", "D3")],
                start=2,
            ):
                ident = f"request{idx}"
                host.receive(frame(permission(ident=ident, tool_id=f"toolu_clause{idx}")))
                path = self.approve(host, ident=ident, behavior=behavior, clause=clause, review_note="Host-only review.")
                value = json.loads(path.read_text())
                self.assertEqual(value["binding"]["approval_policy_sha256"], BINDING["approval_policy_sha256"])
                self.assertEqual(value["clause"], clause)
            self.approve(host, ident="request1", behavior="allow", clause="A3", review_note="Reviewed.")
            host.poll()
            while host.suffix():
                native = json.loads(host.suffix())
                resp = native["response"]["response"]
                if resp["behavior"] == "allow":
                    self.assertEqual(set(resp), {"behavior", "updatedInput"})
                else:
                    self.assertEqual(
                        resp,
                        {"behavior": "deny", "message": "Root denied this exact pending request.", "interrupt": True},
                    )
                host.wrote(len(host.suffix()))
            host.close("completed")
            summary = json.loads((host.directory / "summary.json").read_text())
            self.assertEqual(summary["responses_completed"], 7)
            self.assertEqual(summary["denied_decisions"], 3)

    def test_forged_malformed_unknown_and_rewritten_decisions_fail_closed(self):
        mutations = [
            "policy", "clause", "missing_note", "run", "float_admission",
            "root", "input", "request", "nonce", "extra", "behavior",
            "unknown", "malformed", "symlink",
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                host = ready(p)
                host.receive(frame(permission()))
                path = next((p / "approval/requests").glob("*.json"))
                raw = path.read_bytes()
                req = json.loads(raw)
                value = decision_for(
                    req, raw, "allow", BINDING["root_execution_id"],
                    clause="A3", review_note="Offline fixture scope reviewed.",
                )
                name = path.name
                if mutation == "policy": value["binding"] = dict(value["binding"], approval_policy_sha256="0" * 64)
                elif mutation == "clause": value["clause"] = "D1"
                elif mutation == "missing_note": value.pop("review_note")
                elif mutation == "run": value["binding"] = dict(value["binding"], run_execution_id="another-run")
                elif mutation == "float_admission": value["binding"] = dict(value["binding"], admission=3.0)
                elif mutation == "root": value["root_execution_id"] = "not-root"
                elif mutation == "input": value["input_sha256"] = "0" * 64
                elif mutation == "request": value["request_id"] = "another-request"
                elif mutation == "nonce": value["pending_nonce"] = "another-nonce"
                elif mutation == "extra": value["updatedInput"] = {"command": "replacement command"}
                elif mutation == "behavior": value["behavior"] = "allow-all"
                elif mutation == "unknown": name = "unbound-decision.json"
                target = p / "approval/decisions" / name
                if mutation == "malformed":
                    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.write(fd, b'{"behavior":"allow","behavior":"deny"}\n')
                    os.close(fd)
                elif mutation == "symlink":
                    target.symlink_to(path)
                else:
                    publish(target, value)
                with self.assertRaises(ApprovalError):
                    host.poll()
                host.close("approval_protocol_failure")
                summary = json.loads((p / "approval/summary.json").read_text())
                self.assertEqual(summary["responses_completed"], 0)

    def test_partial_response_and_overlapping_pending_waits_have_exact_monotonic_accounting(self):
        clock = [1_000_000_000]
        with tempfile.TemporaryDirectory() as tmp, patch(
            "trial_approval_control.time.monotonic_ns", side_effect=lambda: clock[0]
        ):
            host = ready(Path(tmp))
            host.set_deadline(181_000_000_000)
            clock[0] = 2_000_000_000
            host.receive(frame(permission()))
            clock[0] = 3_000_000_000
            host.receive(frame(permission("request2", tool_id="toolu_two")))
            clock[0] = 4_000_000_000
            self.approve(host)
            host.poll()
            reply = host.suffix()
            self.assertNotIn(b"review_note", reply)
            self.assertNotIn(b"clause", reply)
            host.wrote(len(reply) - 1)
            clock[0] = 5_000_000_000
            host.wrote(1)
            clock[0] = 6_000_000_000
            self.approve(host, "request2")
            host.poll()
            host.wrote(len(host.suffix()))
            clock[0] = 10_000_000_000
            host.close("completed")
            summary = json.loads((host.directory / "summary.json").read_text())
            rows = summary["request_waits"]
            self.assertEqual(summary["total_wait_seconds"], 4)
            self.assertEqual(summary["total_request_wait_seconds"], 6)
            self.assertEqual(
                [
                    (x["pending_monotonic_ns"], x["response_complete_monotonic_ns"], x["wait_seconds"])
                    for x in rows
                ],
                [(2_000_000_000, 5_000_000_000, 3), (3_000_000_000, 6_000_000_000, 3)],
            )
            self.assertEqual(rows[0]["decision_observed_monotonic_ns"], 4_000_000_000)

    def test_timeout_and_cancel_freeze_pending_before_teardown_and_refuse_late_decision(self):
        clock = [1_000_000_000]
        with tempfile.TemporaryDirectory() as tmp, patch(
            "trial_approval_control.time.monotonic_ns", side_effect=lambda: clock[0]
        ):
            host = ready(Path(tmp))
            host.set_deadline(5_000_000_000)
            clock[0] = 2_000_000_000
            host.receive(frame(permission()))
            clock[0] = 5_000_000_000
            with self.assertRaisesRegex(ApprovalError, "no_longer_pending"):
                self.approve(host)
            host.stop("timeout")
            with self.assertRaises(ApprovalError):
                host.poll()
            clock[0] = 90_000_000_000
            host.close("timeout")
            summary = json.loads((host.directory / "summary.json").read_text())
            row = summary["request_waits"][0]
            self.assertEqual((row["end_reason"], row["ended_monotonic_ns"], row["wait_seconds"]), ("timeout", 5_000_000_000, 3))
            self.assertIsNone(row["response_complete_monotonic_ns"])
            self.assertEqual(summary["total_wait_seconds"], 3)

        clock = [1_000_000_000]
        with tempfile.TemporaryDirectory() as tmp, patch(
            "trial_approval_control.time.monotonic_ns", side_effect=lambda: clock[0]
        ):
            host = ready(Path(tmp))
            clock[0] = 2_000_000_000
            host.receive(frame(permission()))
            clock[0] = 4_000_000_000
            with self.assertRaises(ApprovalError):
                host.receive(frame(dict(type="control_cancel_request", request_id="request1")))
            with self.assertRaisesRegex(ApprovalError, "no_longer_pending"):
                self.approve(host)
            clock[0] = 9_000_000_000
            host.close("approval_protocol_failure")
            row = json.loads((host.directory / "summary.json").read_text())["request_waits"][0]
            self.assertEqual((row["end_reason"], row["wait_seconds"]), ("native_permission_cancelled", 2))
            self.assertIsNone(row["response_complete_monotonic_ns"])

    def test_protocol_state_and_counted_slot3_admission_validation(self):
        for bad_slot in (1, 2, 73):
            with self.subTest(bad_slot=bad_slot), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ApprovalError, "binding_campaign_identity"):
                    ApprovalControl(
                        Path(tmp) / "approval",
                        dict(BINDING, admission=bad_slot),
                        b'{"type":"user"}\n',
                    )
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            host = ApprovalControl(p / "approval", BINDING, b'{"type":"user"}\n')
            stored_binding = json.loads((p / "approval/binding.json").read_text())
            self.assertEqual(stored_binding["admission"], 3)
            self.assertEqual(stored_binding["run_id"], "rhumb-changelog.current.s0")
            self.assertEqual(len(PREDECESSOR_LEDGER_SHA256), 64)
            host.wrote(len(host.suffix()))
            host.receive(
                frame(
                    dict(
                        type="control_response",
                        response=dict(subtype="success", request_id=host.init_id, response={}),
                    )
                )
            )
            host.wrote(len(host.suffix()) - 1)
            with self.assertRaises(ApprovalError):
                host.receive(frame(permission()))
            host.close("approval_protocol_failure")

        for tool in ["mcp__cairn__cairn_remember", "Task", "Agent", "unknown_tool"]:
            with self.subTest(tool=tool), tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp)
                host = ready(p)
                with self.assertRaises(ApprovalError):
                    host.receive(frame(permission(tool=tool)))
                host.close("approval_protocol_failure")

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            host = ready(p)
            host.receive(frame(permission()))
            request = next((p / "approval/requests").glob("*.json"))
            cli = [sys.executable, "-B", str(SCRIPTS_DIR / "trial_approval_control.py")]
            shown = subprocess.run(
                cli + ["show", "--request", str(request)],
                capture_output=True, text=True, timeout=3, check=True,
            )
            value = json.loads(shown.stdout)
            self.assertEqual(value["request"]["native_request"]["request"]["input"], {"command": COMMAND})
            argv = cli + [
                "decide", "--request", str(request),
                "--expected-sha256", value["request_sha256"],
                "--behavior", "allow",
                "--root-execution-id", BINDING["root_execution_id"],
                "--clause", "A3",
                "--review-note", "Offline fixture scope reviewed.",
            ]
            subprocess.run(argv, capture_output=True, timeout=3, check=True)
            self.assertNotEqual(subprocess.run(argv, capture_output=True, timeout=3).returncode, 0)
            host.poll()
            reply = json.loads(host.suffix())
            self.assertEqual(
                reply["response"]["response"],
                {"behavior": "allow", "updatedInput": {"command": COMMAND}},
            )
            host.wrote(len(host.suffix()))
            host.close("completed")


if __name__ == "__main__":
    unittest.main()
