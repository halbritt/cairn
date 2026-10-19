"""Unit tests for scripts/check_memory_utility_outcomes.py (CAIRN-156, CAIRN-158, CAIRN-159)."""
from pathlib import Path
import json
import subprocess
import sys
import unittest

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from check_memory_utility_outcomes import (
     ARMS,
    CONSUMED_PREFIX_SLOTS,
    CONTROL_CASES,
    CONTROL_PLACEHOLDER,
    FROZEN_CASES,
    INCIDENT_CASES,
    PREDECESSOR_LEDGER_SHA256,
    UtilityEvaluationError,
    build_arm_masked_packet,
    build_frozen_schedule,
    compute_paired_contrasts,
    evaluate_build_or_defer_decision,
    sha256_bytes,
    validate_ledger_events,
    validate_schedule,
)


def make_prefix_events(schedule_rows: list[dict]) -> list[dict]:
    events = []
    for slot in (1, 2):
        row = schedule_rows[slot - 1]
        events.append(
            {
                "type": "admitted",
                "row": row,
                "permission_epoch": "ALLOW/1" if slot == 1 else "ALLOW/2",
            }
        )
        events.append(
            {
                "type": "finished",
                "admission": slot,
                "record": {
                    "run_id": row["run_id"],
                    "outcome": "harness_error",
                    "execution_failure": "native_permission_denial",
                    "execution_failures": ["native_permission_denial"],
                    "trace": {"turns": 8, "completed": True},
                },
            }
        )
    return events


class CheckMemoryUtilityOutcomesTests(unittest.TestCase):
    def test_frozen_schedule_and_consumed_prefix_accounting(self):
        rows = build_frozen_schedule()
        schedule_by_slot = validate_schedule(rows)
        self.assertEqual(len(schedule_by_slot), 72)
        self.assertEqual(len(INCIDENT_CASES), 6)
        self.assertEqual(len(CONTROL_CASES), 6)
        self.assertEqual(len(FROZEN_CASES), 12)
        self.assertEqual(rows[0]["run_id"], "rhumb-changelog.none.s0")
        self.assertEqual(rows[1]["run_id"], "rhumb-changelog.direct.s0")
        self.assertEqual(rows[2]["run_id"], "rhumb-changelog.current.s0")
        self.assertEqual(len(PREDECESSOR_LEDGER_SHA256), 64)

        prefix_events = make_prefix_events(rows)
        prefix_bytes = "".join(json.dumps(e) + "\n" for e in prefix_events).encode("utf-8")
        prefix_sha = sha256_bytes(prefix_bytes)

        all_events = list(prefix_events)
        for slot in range(3, 73):
            row = schedule_by_slot[slot]
            all_events.append(
                {
                    "type": "admitted",
                    "row": row,
                    "permission_epoch": "ALLOW/2+root-per-call-stdio/1",
                }
            )
            all_events.append(
                {
                    "type": "finished",
                    "admission": slot,
                    "record": {
                        "run_id": row["run_id"],
                        "outcome": "correct",
                        "check_outcome": "correct",
                        "execution_failures": [],
                        "native_approval": {
                            "schema": "cairn.native-approval-summary/1",
                            "status": "completed",
                            "failed": None,
                            "pending_requests": 0,
                        },
                        "trace": {"turns": 6, "output_tokens": 900, "cost_usd": 0.12, "completed": True},
                    },
                }
            )
        succ_bytes = "".join(json.dumps(e) + "\n" for e in all_events).encode("utf-8")
        slots = validate_ledger_events(
            all_events,
            schedule_by_slot,
            predecessor_bytes=prefix_bytes,
            successor_bytes=succ_bytes,
            expected_predecessor_sha256=prefix_sha,
        )
        self.assertEqual(len(slots), 72)

        with self.assertRaisesRegex(UtilityEvaluationError, "replay or duplicate admission"):
            validate_ledger_events(prefix_events + [prefix_events[0]], schedule_by_slot)

    def test_arm_masked_packet_strips_memory_metadata_and_arm_labels(self):
        rows = build_frozen_schedule()
        schedule_by_slot = validate_schedule(rows)
        slot3_row = schedule_by_slot[3]
        record = {
            "run_id": slot3_row["run_id"],
            "outcome": "correct",
            "check_outcome": "correct",
            "answer": f"Completed in {slot3_row['run_id']} on current arm using saved notes D-rhumb-changelog.",
            "diff": "+0.0.25\n",
            "actions": [
                {"tool": "mcp__cairn__cairn_search", "input": {"query": "rhumb"}, "result": "D-rhumb-changelog"},
                {"tool": "Bash", "input": {"command": "cat VERSION"}, "result": "0.0.25\n"},
                {
                    "tool": "Bash",
                    "input": {"command": "rm -rf /tmp/x"},
                    "result": "Root denied this exact pending request.",
                    "result_is_error": True,
                },
            ],
        }
        packet, root_entry = build_arm_masked_packet(
            3, slot3_row, record, note_ids=("D-rhumb-changelog",)
        )
        serialized = json.dumps(packet)
        self.assertTrue(packet["arm_masked_id"].startswith("pkt-"))
        self.assertNotIn("rhumb-changelog.current.s0", serialized)
        self.assertNotIn("cairn_search", serialized)
        self.assertNotIn("D-rhumb-changelog", serialized)
        self.assertNotIn("Root denied this exact pending request", serialized)
        self.assertIn(CONTROL_PLACEHOLDER, serialized)
        self.assertEqual(len(packet["actions"]), 2)
        self.assertEqual(root_entry["arm"], "current")
        self.assertIn("memory_tool_calls_removed:1", root_entry["masking_flags"])
        self.assertIn("control_text_results_replaced:1", root_entry["masking_flags"])

    def test_paired_contrasts_and_build_or_defer_recommendations(self):
        rows = build_frozen_schedule()
        schedule_by_slot = validate_schedule(rows)
        events = make_prefix_events(rows)
        for slot in range(3, 73):
            row = schedule_by_slot[slot]
            is_incident = row["case"] in INCIDENT_CASES
            if is_incident:
                outcome = "correct" if row["arm"] == "direct" else ("mistake" if row["arm"] == "none" else "incomplete")
            else:
                outcome = "correct"
            events.append(
                {
                    "type": "admitted",
                    "row": row,
                    "permission_epoch": "ALLOW/2+root-per-call-stdio/1",
                }
            )
            events.append(
                {
                    "type": "finished",
                    "admission": slot,
                    "record": {
                        "run_id": row["run_id"],
                        "outcome": outcome,
                        "check_outcome": outcome,
                        "execution_failures": [],
                        "native_approval": {
                            "schema": "cairn.native-approval-summary/1",
                            "status": "completed",
                            "failed": None,
                            "pending_requests": 0,
                        },
                        "trace": {
                            "turns": 7 if row["arm"] == "current" else 5,
                            "output_tokens": 1100 if row["arm"] == "current" else 800,
                            "cost_usd": 0.18 if row["arm"] == "current" else 0.12,
                            "completed": True,
                        },
                    },
                }
            )
        slots = validate_ledger_events(events, schedule_by_slot)
        pairs = compute_paired_contrasts(schedule_by_slot, slots)
        rhumb_s0 = next(p for p in pairs if p["case"] == "rhumb-changelog" and p["replicate"] == 0)
        self.assertFalse(rhumb_s0["contrasts"]["direct-none"]["usable"])
        self.assertFalse(rhumb_s0["contrasts"]["current-none"]["usable"])

        offline_decision = evaluate_build_or_defer_decision(
            pairs, provenance_mode="offline_deterministic_validation"
        )
        self.assertEqual(offline_decision["decision"], "DEFER")

        live_build_decision = evaluate_build_or_defer_decision(
            pairs, provenance_mode="live_provider_complete"
        )
        self.assertEqual(live_build_decision["decision"], "BUILD")
        self.assertGreater(
            live_build_decision["incident_contrasts"]["direct-none"]["net_harmful_mistakes_avoided"], 0
        )

        cli_proc = subprocess.run(
            [sys.executable, "-B", str(SCRIPTS_DIR / "check_memory_utility_outcomes.py"), "--self-check"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        cli_out = json.loads(cli_proc.stdout)
        self.assertEqual(cli_out["decision"], "DEFER")


if __name__ == "__main__":
    unittest.main()
