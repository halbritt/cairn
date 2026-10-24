"""Provider-free checks for the prepared long-source actor packet."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import preflight_token_efficiency_panel as panel
import stage_token_efficiency_long_source as stage


class LongSourcePacketTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cairn-postgres.", dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        private_parent = self.root / "exports"
        private_parent.mkdir(mode=0o700)
        prior_parent = panel.PRIVATE_PARENT
        panel.PRIVATE_PARENT = private_parent
        self.addCleanup(setattr, panel, "PRIVATE_PARENT", prior_parent)
        data, fixture_hash = panel.read_fixture()
        self.data = data
        self.export = private_parent / "panel"
        panel.export(data, fixture_hash, self.export)

    def test_all_arms_prepare_exact_whole_sources_without_actor_or_grade(self):
        history = self.data["sources"]["relay_history"]
        current = self.data["sources"]["relay_current"]
        for arm in ("baseline", "envelope", "batch", "combined"):
            with self.subTest(arm=arm):
                output = self.root / arm
                report = stage.stage(self.export, f"{arm}.long_source", output, self.root)
                packet_bytes = (output / "actor/long_source.json").read_bytes()
                packet = json.loads(packet_bytes)
                self.assertEqual(report["actor_packet_sha256"],
                                 hashlib.sha256(packet_bytes).hexdigest())
                self.assertEqual(report["actor_packet_bytes"], len(packet_bytes))
                self.assertEqual((report["historical_whole"], report["current_whole"]), (120, 1))
                self.assertEqual(report["evidence_status"], "prepared_unread")
                self.assertFalse(report["provider_called"])
                self.assertEqual(report["native_turn_join"], "unimplemented")
                self.assertEqual(report["independent_task_acceptance"], "unassessed")
                self.assertEqual(len(packet["sections"]), 121)
                self.assertEqual([part["position"] for part in packet["sections"]],
                                 list(range(1, 122)))
                for part in packet["sections"][:120]:
                    self.assertEqual((part["source_id"], part["source_extent"], part["body"]),
                                     ("relay_history", "whole", history["body"]))
                    self.assertEqual(part["source_body_sha256"],
                                     hashlib.sha256(history["body"].encode()).hexdigest())
                    self.assertEqual(part["source_record_sha256"], panel.sha(history))
                last = packet["sections"][-1]
                self.assertEqual((last["source_id"], last["source_extent"], last["body"]),
                                 ("relay_current", "whole", current["body"]))
                self.assertEqual(last["source_body_sha256"],
                                 hashlib.sha256(current["body"].encode()).hexdigest())
                self.assertEqual(last["source_record_sha256"], panel.sha(current))
                check = next(row["acceptance_check"] for row in self.data["cases"]
                             if row["id"] == "long_source")
                self.assertNotIn("acceptance_check", packet_bytes.decode())
                for clauses in check.values():
                    for clause in clauses:
                        self.assertNotIn(clause, packet_bytes.decode())
                self.assertEqual(json.loads((output / "receipt.json").read_text()), report)
                self.assertEqual(stage.verify(self.export, f"{arm}.long_source", output, self.root),
                                 report)

    def test_verifier_refuses_partial_or_altered_actor_packet(self):
        output = self.root / "altered"
        stage.stage(self.export, "baseline.long_source", output, self.root)
        packet_path = output / "actor/long_source.json"
        packet = json.loads(packet_path.read_text())
        packet["sections"][0]["source_extent"] = "partial_span"
        packet_path.write_text(json.dumps(packet))
        with self.assertRaisesRegex(ValueError, "source or extent drift"):
            stage.verify(self.export, "baseline.long_source", output, self.root)

    def test_refuses_drift_and_outside_disposable_output(self):
        with self.assertRaisesRegex(ValueError, "long-source workflow required"):
            stage.stage(self.export, "baseline.single_notice", self.root / "wrong-case", self.root)
        self.assertFalse((self.root / "wrong-case").exists())
        actor_path = self.export / "actor/long_source.json"
        actor = json.loads(actor_path.read_text())
        actor["task"] += " changed"
        actor_path.write_text(json.dumps(actor))
        with self.assertRaisesRegex(ValueError, "actor export drift"):
            stage.stage(self.export, "baseline.long_source", self.root / "no-packet", self.root)
        self.assertFalse((self.root / "no-packet").exists())
        with self.assertRaisesRegex(ValueError, "direct child"):
            stage.stage(self.export, "baseline.long_source", self.root / "exports/no-packet", self.root)

    def test_preexisting_output_is_not_removed_or_changed(self):
        output = self.root / "existing"
        output.mkdir(mode=0o700)
        sentinel = output / "sentinel.txt"
        sentinel.write_text("keep this fixture")
        with self.assertRaises(FileExistsError):
            stage.stage(self.export, "baseline.long_source", output, self.root)
        self.assertEqual(sentinel.read_text(), "keep this fixture")
        self.assertEqual(list(output.iterdir()), [sentinel])


if __name__ == "__main__":
    unittest.main()
