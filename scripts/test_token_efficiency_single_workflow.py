"""Provider-free one-workflow admission and native-join checks."""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import token_efficiency_gate as gate
import token_efficiency_single_workflow as single


class SingleWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture_path = self.root / "fixture.json"
        self.controller_path = self.root / "controller.json"
        self.fixture = {
            "schema": "cairn.token-efficiency-fixture/1",
            "cases": [
                {"id": case, "actor_task": f"Do bounded work for {case}.",
                 "setup": {"sources": [], "events": [], "transitions": []},
                 "acceptance_check": {"expected": case}}
                for case in gate.CASES
            ],
        }
        self.fixture["cases"][6]["setup"]["events"] = [
            {"id": "one", "kind": "request", "recipient": "worker", "body": "A request body."}
        ]
        self.fixture["cases"][19]["setup"]["capture"] = {
            "harness_ref": "plan.model.harness", "model_ref": "plan.capture_selector.id"
        }
        self.fixture_path.write_text(json.dumps(self.fixture))
        self.controller = {
            "schema": "cairn.token-efficiency-panel-manifest/1",
            "fixture_sha256": hashlib.sha256(self.fixture_path.read_bytes()).hexdigest(),
            "provider_called": False,
            "cases": [],
            "prospective_runs": [],
        }
        for row in self.fixture["cases"]:
            case = row["id"]
            actor = {"schema": "cairn.token-efficiency-actor-task/1", "case": case,
                     "task": row["actor_task"]}
            self.controller["cases"].append({
                "id": case,
                "actor_sha256": gate.sha(actor),
                "setup_sha256": gate.sha(row["setup"]),
                "acceptance_check_sha256": gate.sha(row["acceptance_check"]),
            })
            for arm in gate.ARMS:
                self.controller["prospective_runs"].append({
                    "workflow_id": f"{arm}.{case}", "case": case, "arm": arm,
                    "actor_sha256": gate.sha(actor), "setup_sha256": gate.sha(row["setup"]),
                    "acceptance_check_sha256": gate.sha(row["acceptance_check"]),
                    "intervention_sha256": gate.sha(None),
                })
        self.controller_path.write_text(json.dumps(self.controller))
        self.controller_path.chmod(0o600)

    def test_only_baseline_single_notice_can_be_frozen(self):
        selection = single.select_workflow(self.fixture_path, self.controller_path,
                                           case="single_notice", arm="baseline")
        self.assertEqual(selection["workflow_id"], "baseline.single_notice")
        self.assertEqual(selection["acceptance_check_sha256"],
                         gate.sha(self.fixture["cases"][0]["acceptance_check"]))
        for case, expected in (("concurrent_receivers", "concurrency"),
                               ("unchanged_capture", "capture"),
                               ("long_source", "unsupported")):
            with self.subTest(case=case):
                with self.assertRaisesRegex(ValueError, expected):
                    single.select_workflow(self.fixture_path, self.controller_path,
                                           case=case, arm="baseline")
        with self.assertRaisesRegex(ValueError, "baseline"):
            single.select_workflow(self.fixture_path, self.controller_path,
                                   case="single_notice", arm="combined")

    def test_output_parent_symlink_cannot_resolve_into_repository(self):
        repo_alias = self.root / "repo-alias"
        repo_alias.symlink_to(single.ROOT, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            single.create_owner_only_dir(repo_alias / "capacity-receipts")
        self.assertFalse((single.ROOT / "capacity-receipts").exists())

    def test_stage_capacity_must_be_fresh_and_exactly_bound(self):
        now = datetime.now(timezone.utc)
        evidence = {
            "schema": "cairn.provider-capacity-evidence/1", "provider": "openai",
            "route": "subscription", "harness": "codex", "account": "codex-home:.codex",
            "source_type": "caller_recorded", "measurement": "utilization_status",
            "ordinary_usage_allowed": True, "used_percent": 7,
            "observed_at": now.isoformat(), "valid_until": (now + timedelta(seconds=60)).isoformat(),
            "workflow_id": "baseline.single_notice", "stage_id": "baseline.single_notice.recipient",
        }
        policy = {"purpose": "task", "ceiling_used_percent": 20,
                  "reserve_percentage_points": 1}
        single.validate_stage_capacity(evidence, policy, workflow_id="baseline.single_notice",
                                       stage_id="baseline.single_notice.recipient", now=now)
        bad = copy.deepcopy(evidence)
        bad["stage_id"] = "another-stage"
        with self.assertRaisesRegex(ValueError, "does not identify"):
            single.validate_stage_capacity(bad, policy, workflow_id="baseline.single_notice",
                                           stage_id="baseline.single_notice.recipient", now=now)
        bad = copy.deepcopy(evidence)
        bad["observed_at"] = (now - timedelta(seconds=61)).isoformat()
        with self.assertRaisesRegex(ValueError, "older than 60 seconds"):
            single.validate_stage_capacity(bad, policy, workflow_id="baseline.single_notice",
                                           stage_id="baseline.single_notice.recipient", now=now)

    def test_native_join_requires_exact_workload_event_attempt_and_pin(self):
        manifest = single.fixture_manifest(self.fixture_path, self.controller_path,
                                           case="single_notice", arm="baseline")
        manifest["limits"] = {"elapsed_seconds": 10, "input_tokens": 1000,
                              "output_tokens": 100}
        now = datetime.now(timezone.utc)
        event, attempt = "a" * 24, "b" * 24
        provider = {
            "harness": "codex", "account": "codex-home:.codex",
            "models": ["gpt-6-luna"], "efforts": ["low"], "pin": "inbox-pin",
            "duplicate_response_id": False, "native_completed": True,
            "started_at": (now - timedelta(seconds=2)).isoformat(),
            "ended_at": (now - timedelta(seconds=1)).isoformat(),
            "response_refs": ["c" * 24],
            "usage": {"observed_responses": 1, "usage_records": 1,
                      "tokens": {field: {"total": 1} for field in gate.TOKEN_FIELDS}},
        }
        provider["usage"]["tokens"]["input"]["total"] = 1000
        provider["usage"]["tokens"]["cached"]["total"] = 1000
        capacity = {
            "schema": "cairn.provider-capacity-evidence/1", "provider": "openai",
            "route": "subscription", "harness": "codex", "account": "codex-home:.codex",
            "source_type": "caller_recorded", "measurement": "utilization_status",
            "ordinary_usage_allowed": True, "used_percent": 7,
            "observed_at": (now - timedelta(seconds=3)).isoformat(),
            "valid_until": (now + timedelta(seconds=20)).isoformat(),
            "workflow_id": "baseline.single_notice",
            "stage_id": "baseline.single_notice.recipient",
        }
        initial_capacity = {key: value for key, value in capacity.items()
                            if key not in ("workflow_id", "stage_id")}
        initial_path = self.root / "initial-capacity.json"
        initial_path.write_text(json.dumps(initial_capacity) + "\n")
        initial_path.chmod(0o600)
        stage_path = self.root / "stage-capacity.json"
        stage_path.write_text(json.dumps(capacity) + "\n")
        stage_path.chmod(0o600)
        dry_run_path = self.root / "dry-run-stage-capacity.json"
        dry_run_path.write_text(json.dumps(capacity) + "\n")
        dry_run_path.chmod(0o600)
        observation = {
            "schema": "cairn.token-efficiency-single-workflow-observation/1",
            "workflow_id": "baseline.single_notice", "case": "single_notice", "arm": "baseline",
            "state": "observed", "event": event,
            "stages": [{"stage_id": "baseline.single_notice.recipient", "state": "observed",
                        "attempt": attempt, "model": "gpt-6-luna", "effort": "low",
                        "route": "subscription", "pins": {"api": "api-pin", "ordinary": "ordinary-pin",
                                                                "inbox": "inbox-pin", "client": "client-pin"},
                        "capacity_check": {"evidence_path": str(stage_path),
                                           "evidence_sha256": hashlib.sha256(stage_path.read_bytes()).hexdigest()} }],
        }
        manifest.update(stage_role="recipient", model={"harness": "codex", "account": "codex-home:.codex",
                                                         "id": "gpt-6-luna", "effort": "low",
                                                         "route": "subscription"},
                        pins={"api": "api-pin", "ordinary": "ordinary-pin",
                              "inbox": "inbox-pin", "client": "client-pin"},
                        frozen_at=now.isoformat(),
                        capacity_admission={"max_concurrent_workflows": 1, "accounts": [{
                            "harness": "codex", "account": "codex-home:.codex", "purpose": "task",
                            "ceiling_used_percent": 20, "reserve_percentage_points": 1,
                            "evidence_path": str(initial_path),
                            "evidence_sha256": hashlib.sha256(initial_path.read_bytes()).hexdigest(),
                        }]},
                        dry_run_stage_capacity_check={"evidence_path": str(dry_run_path),
                                                      "evidence_sha256": hashlib.sha256(
                                                          dry_run_path.read_bytes()).hexdigest()})
        native = {"schema": "cairn.workflow-model-report/1", "workflows": [{
            "event": event, "workload": "baseline.single_notice", "publication": "committed",
            "sender_provider": None,
            "deliveries": [{"attempt": attempt, "turn_join": "binding",
                            "settlement": {"status": "committed"}, "provider": provider}],
        }]}
        pins = {"api": "api-pin", "ordinary": "ordinary-pin", "inbox": "inbox-pin", "client": "client-pin"}

        def verify():
            return single.verify_native_join(
                manifest, observation, native, pins=pins, now=now,
                capacity_policy={"purpose": "task", "ceiling_used_percent": 20,
                                 "reserve_percentage_points": 1})

        result = verify()
        self.assertTrue(result["exact_native_join"])
        self.assertEqual(result["independent_task_acceptance"], "unassessed")
        self.assertEqual(result["observed_limits"]["input_tokens"], 1000)
        self.assertEqual(result["observed_limits"]["output_tokens"], 1)
        self.assertEqual(result["observed_limits"]["elapsed_seconds"], 1.0)
        self.assertFalse(result["provider_hard_cap"])
        limits = manifest.pop("limits")
        with self.assertRaisesRegex(ValueError, "frozen native elapsed and token ceilings"):
            verify()
        manifest["limits"] = limits
        provider["usage"]["tokens"]["input"]["total"] = 1001
        with self.assertRaisesRegex(ValueError, "input token ceiling"):
            verify()
        provider["usage"]["tokens"]["input"]["total"] = 1000
        provider["usage"]["tokens"]["output"]["total"] = 101
        with self.assertRaisesRegex(ValueError, "output token ceiling"):
            verify()
        provider["usage"]["tokens"]["output"]["total"] = None
        with self.assertRaisesRegex(ValueError, "output token usage unavailable"):
            verify()
        provider["usage"]["tokens"]["output"]["total"] = 1
        started_at = provider["started_at"]
        provider["started_at"] = (now - timedelta(seconds=12)).isoformat()
        with self.assertRaisesRegex(ValueError, "elapsed ceiling"):
            verify()
        provider["started_at"] = started_at
        provider["usage"]["tokens"]["input"]["total"] = None
        with self.assertRaisesRegex(ValueError, "input token usage unavailable"):
            verify()
        provider["usage"]["tokens"]["input"]["total"] = 1000
        usage_records = provider["usage"].pop("usage_records")
        with self.assertRaisesRegex(ValueError, "usage records incomplete"):
            verify()
        provider["usage"]["usage_records"] = usage_records
        started_at = provider.pop("started_at")
        with self.assertRaises(ValueError):
            verify()
        provider["started_at"] = started_at
        ended_at = provider.pop("ended_at")
        with self.assertRaises(ValueError):
            verify()
        provider["ended_at"] = ended_at
        native["workflows"][0]["workload"] = "baseline.actionable_response"
        with self.assertRaisesRegex(ValueError, "workload mismatch"):
            verify()

    def test_freeze_writes_admission_evidence_without_starting_a_provider_task(self):
        now = datetime.now(timezone.utc)

        def capacity(codex_home=None, *, workflow_id=None, stage_id=None):
            row = {
                "schema": "cairn.provider-capacity-evidence/1", "provider": "openai",
                "route": "subscription", "harness": "codex", "account": "codex-home:.codex",
                "source_type": "caller_recorded", "measurement": "utilization_status",
                "ordinary_usage_allowed": True, "used_percent": 7,
                "observed_at": (now - timedelta(seconds=1)).isoformat(),
                "valid_until": (now + timedelta(seconds=59)).isoformat(),
            }
            if workflow_id:
                row.update(workflow_id=workflow_id, stage_id=stage_id)
            return row

        pins = {
            "account": {"handle": "codex-home:.codex"},
            "source": {"revision": "a" * 40, "dirty": False,
                       "runner_sha256": "a" * 64, "gate_sha256": "b" * 64},
            "installed": {
                "cairn_api_revision": "8" * 40,
                "cairn_cli_revision": "9" * 40,
                "ordinary_engine_sha256": "a" * 64,
                "ordinary_config_sha256": "b" * 64,
                "coordination_engine_sha256": "c" * 64,
                "coordination_config_sha256": "d" * 64,
                "inbox_binding_sha256": "e" * 64,
                "inbox_bridge_sha256": "f" * 64,
                "codex_version": "codex-cli 0.164.0",
                "codex_binary_sha256": "1" * 64,
                "ordinary_hook_settings_sha256": "2" * 64,
            },
        }
        with patch.object(single, "runtime_pin_closure", return_value=pins), \
                patch.object(single, "codex_capacity_snapshot", side_effect=capacity) as status_read:
            receipt = single.freeze_dry_run(
                fixture_path=self.fixture_path, controller_path=self.controller_path,
                codex_home=Path.home() / ".codex", output_dir=self.root / "dry-run",
                elapsed_seconds=60, input_tokens=1000, output_tokens=100,
            )
        self.assertEqual(status_read.call_count, 2)
        self.assertFalse(receipt["provider_request_made"])
        self.assertFalse(receipt["provider_task_started"])
        self.assertFalse(receipt["live_admission_authorized"])
        manifest = json.loads((self.root / "dry-run/manifest.json").read_text())
        single.verify_frozen_manifest(manifest)
        self.assertEqual(manifest["case"], "single_notice")
        self.assertEqual(manifest["model"]["id"], "gpt-6-luna")
        self.assertEqual(manifest["full_panel_status"], "incomplete")
        self.assertEqual(manifest["runtime_pin_closure"]["source"]["revision"], "a" * 40)
        self.assertEqual(manifest["runtime_pin_closure"]["installed"]["cairn_api_revision"], "8" * 40)
        self.assertEqual(manifest["runtime_pin_closure"]["installed"]["cairn_cli_revision"], "9" * 40)
        self.assertEqual((self.root / "dry-run/manifest.json").stat().st_mode & 0o777, 0o600)
        malformed = copy.deepcopy(manifest)
        malformed["limits"]["retry_count"] = 0
        unsigned = dict(malformed)
        unsigned.pop("manifest_sha256")
        malformed["manifest_sha256"] = gate.sha(unsigned)
        with self.assertRaisesRegex(ValueError, "elapsed and token ceilings"):
            single.verify_frozen_manifest(malformed)
        manifest["release_decision"] = "ship"
        with self.assertRaisesRegex(ValueError, "manifest hash mismatch"):
            single.verify_frozen_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
