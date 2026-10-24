"""Synthetic CAIRN-227 panel: mechanical readiness never becomes task acceptance."""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import token_efficiency_gate as gate
import preflight_token_efficiency_panel as panel


class TokenEfficiencyGateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = Path(directory.name) / "fixture.json"
        fixture_cases = [dict(id=case, acceptance_check={"task_result": case}) for case in gate.CASES]
        self.fixture_body = json.dumps(dict(schema="cairn.token-efficiency-fixture/1", cases=fixture_cases))
        self.fixture.write_text(self.fixture_body)
        self.model = dict(harness="codex", account="fixture", id="gpt-6-sol", effort="medium",
                          route="subscription")
        self.pins = dict(api="api-pin", ordinary="ordinary-pin", inbox="inbox-pin", client="client-pin")
        arms = {arm: dict(commit="a" * 40, pins=dict(self.pins)) for arm in gate.ARMS}
        runs, observations, workflows = [], [], []
        sequence = 0
        self.response_seq = 0
        for arm in gate.ARMS:
            for case in gate.CASES:
                sequence += 1
                workflow_id = f"{arm}.{case}"
                sender_id, recipient_id = f"{workflow_id}.sender", f"{workflow_id}.recipient"
                event, attempt = f"{sequence:024x}", f"{sequence + 1000:024x}"
                runs.append(dict(workflow_id=workflow_id, case=case, arm=arm, stages=[
                    dict(stage_id=sender_id, role="sender", pin="client"),
                    dict(stage_id=recipient_id, role="recipient", pin="inbox")]))
                common = dict(state="observed", model=self.model["id"], effort=self.model["effort"],
                              route=self.model["route"], pins=dict(self.pins))
                observations.append(dict(workflow_id=workflow_id, case=case, arm=arm, event=event,
                                         state="observed", stages=[dict(stage_id=sender_id, **common),
                                                                   dict(stage_id=recipient_id, attempt=attempt,
                                                                        **common)]))
                workflows.append(dict(event=event, workload=workflow_id, publication="committed",
                                      sender_provider=dict(self.provider("client"), turn_join="binding"),
                                      deliveries=[dict(attempt=attempt, turn_join="binding",
                                                       settlement=dict(status="committed"),
                                                       provider=self.provider("inbox"))]))
        self.plan = dict(schema="cairn.token-efficiency-plan/1", cases=list(gate.CASES),
                         acceptance_checks={row["id"]: gate.sha(row["acceptance_check"])
                                            for row in fixture_cases},
                         fixture_sha256=hashlib.sha256(self.fixture.read_bytes()).hexdigest(),
                         model=self.model, limits=dict(elapsed_seconds=900, input_tokens=100000,
                                                       output_tokens=10000), arms=arms, runs=runs)
        self.frozen = gate.freeze(self.plan, self.fixture)
        self.observations = dict(schema="cairn.token-efficiency-observations/1",
                                 plan_sha256=self.frozen["plan_sha256"], runs=observations)
        self.native = dict(schema="cairn.workflow-model-report/1", workflows=workflows)

    def provider(self, pin):
        self.response_seq += 1
        return dict(harness=self.model["harness"], account=self.model["account"],
                    models=[self.model["id"]], efforts=[self.model["effort"]],
                    pin=self.pins[pin], duplicate_response_id=False, native_completed=True,
                    response_refs=[f"{self.response_seq:024x}"],
                    usage=dict(observed_responses=1, usage_records=1,
                               tokens={field: dict(total=10) for field in gate.TOKEN_FIELDS}))

    def enable_capture_selector(self):
        self.plan["capture_selector"] = dict(harness="claude", account="capture-account",
                                             id="claude-fixture", route="subscription",
                                             effort_configuration="unset")
        for arm in gate.ARMS:
            self.plan["arms"][arm]["pins"]["capture"] = f"capture-pin-{arm}"
        for run in self.observations["runs"]:
            for stage in run["stages"]:
                stage["pins"] = dict(self.plan["arms"][run["arm"]]["pins"])

    def capture_provider(self, arm, efforts=None):
        self.response_seq += 1
        return dict(harness="claude", account="capture-account", models=["claude-fixture"],
                    efforts=efforts, pin=self.plan["arms"][arm]["pins"]["capture"],
                    duplicate_response_id=False, response_refs=[f"{self.response_seq:024x}"],
                    usage=dict(observed_responses=1,
                               tokens={field: dict(total=10) for field in gate.TOKEN_FIELDS}))

    def test_caller_only_stages_cannot_make_observed_workflow_ready(self):
        self.enable_capture_selector()
        planned = self.plan["runs"][0]
        planned["stages"][0]["role"] = "coordinator"
        planned["stages"][1].update(role="capture", pin="capture", capture_phase="seed")
        self.frozen = gate.freeze(self.plan, self.fixture)
        self.observations["plan_sha256"] = self.frozen["plan_sha256"]
        for stage, pin in zip(self.observations["runs"][0]["stages"], ("client", "inbox")):
            stage.pop("attempt", None)
            stage["evidence_ref"] = "caller_selected"
            stage["provider"] = self.provider(pin)
        capture_stage = self.observations["runs"][0]["stages"][1]
        capture_stage.update(model="claude-fixture", effort=None,
                             provider=self.capture_provider("baseline", efforts=[]))
        self.native["workflows"][0].update(sender_provider=None, deliveries=[])
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["counts"]["unjoined_observed_runs"], 1)
        self.assertEqual(result["counts"]["unknown_effort_stages"], 1)
        self.assertTrue(any("native joined stage" in issue for issue in result["problems"]))

    def test_actual_capture_fixture_requires_seed_and_repeat_stages_in_every_arm(self):
        fixture, fixture_hash = panel.read_fixture()
        self.enable_capture_selector()
        self.plan["fixture_sha256"] = fixture_hash
        self.plan["acceptance_checks"] = {row["id"]: gate.sha(row["acceptance_check"])
                                          for row in fixture["cases"]}
        capture_runs = [run for run in self.plan["runs"] if run["case"] == "unchanged_capture"]
        for run in capture_runs:
            for phase in ("seed", "repeat"):
                run["stages"].append(dict(stage_id=f"{run['workflow_id']}.capture.{phase}",
                                          role="capture", pin="capture", capture_phase=phase))
        frozen = gate.freeze(self.plan, panel.FIXTURE)
        observations = copy.deepcopy(self.observations)
        observations["plan_sha256"] = frozen["plan_sha256"]
        for run in observations["runs"]:
            if run["case"] == "unchanged_capture":
                for phase in ("seed", "repeat"):
                    run["stages"].append(dict(stage_id=f"{run['workflow_id']}.capture.{phase}",
                                              state="not_reached"))
        observed = gate.report(frozen, panel.FIXTURE, observations, self.native)
        self.assertEqual(observed["state"], "incomplete")
        self.assertEqual(sum("observed capture requires provider-backed seed stage" in problem
                             for problem in observed["problems"]), 4)
        self.assertEqual(sum("observed capture requires selected repeat skip stage" in problem
                             for problem in observed["problems"]), 4)
        missing_selector = copy.deepcopy(self.plan)
        missing_selector.pop("capture_selector")
        with self.assertRaisesRegex(ValueError, "capture stage requires frozen selector"):
            gate.freeze(missing_selector, panel.FIXTURE)
        missing_repeat = copy.deepcopy(self.plan)
        next(run for run in missing_repeat["runs"] if run["case"] == "unchanged_capture")["stages"].pop()
        with self.assertRaisesRegex(ValueError, "seed and repeat capture stages required"):
            gate.freeze(missing_repeat, panel.FIXTURE)
        wrong_route = copy.deepcopy(self.plan)
        wrong_route["capture_selector"]["route"] = "OpenRouter"
        with self.assertRaisesRegex(ValueError, "capture selector route must be subscription"):
            gate.freeze(wrong_route, panel.FIXTURE)

    def test_capture_provider_uses_auxiliary_identity_and_pin_without_inventing_effort(self):
        self.enable_capture_selector()
        slot = self.plan["runs"][0]["stages"][1]
        slot.update(role="capture", pin="capture", capture_phase="seed")
        self.frozen = gate.freeze(self.plan, self.fixture)
        self.observations["plan_sha256"] = self.frozen["plan_sha256"]
        stage = self.observations["runs"][0]["stages"][1]
        stage.pop("attempt", None)
        stage.update(model="claude-fixture", effort=None, evidence_ref="selected_capture",
                     provider=self.capture_provider("baseline", efforts=[]))
        ready = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(ready["state"], "ready_for_human_grade")
        self.assertEqual(ready["counts"]["unknown_effort_stages"], 1)
        self.assertEqual(ready["counts"]["unknown_provider_stages"], 0)
        for field, wrong in (("response_refs", []), ("response_refs", ["caller-only"]),
                             ("response_refs", ["a" * 24, "a" * 24]),
                             ("usage", {"observed_responses": 0})):
            with self.subTest(field=field, wrong=wrong):
                original = stage["provider"][field]
                stage["provider"][field] = wrong
                result = gate.report(self.frozen, self.fixture, self.observations, self.native)
                self.assertEqual(result["state"], "incomplete")
                self.assertEqual(result["counts"]["unknown_provider_stages"], 1)
                self.assertTrue(any("selected capture provider response missing" in problem
                                    for problem in result["problems"]))
                stage["provider"][field] = original
        for field, wrong in (("models", ["gpt-6-sol"]), ("account", "wrong"),
                             ("pin", "client-pin"), ("efforts", "high")):
            with self.subTest(field=field):
                original = stage["provider"][field]
                stage["provider"][field] = wrong
                result = gate.report(self.frozen, self.fixture, self.observations, self.native)
                self.assertEqual(result["state"], "incomplete")
                self.assertTrue(any("provider model/account/effort/pin mismatch" in problem
                                    for problem in result["problems"]))
                stage["provider"][field] = original
        for field, wrong, reason in (("route", "OpenRouter", "route drift"),
                                     ("model", "gpt-6-sol", "capture selector model/effort drift"),
                                     ("effort", "medium", "capture selector model/effort drift")):
            with self.subTest(field=field):
                original = stage[field]
                stage[field] = wrong
                with self.assertRaisesRegex(ValueError, reason):
                    gate.report(self.frozen, self.fixture, self.observations, self.native)
                stage[field] = original

    def test_repeat_skip_is_selected_zero_call_testimony_and_never_ready(self):
        self.enable_capture_selector()
        slot = self.plan["runs"][0]["stages"][1]
        slot.update(role="capture", pin="capture", capture_phase="repeat")
        self.frozen = gate.freeze(self.plan, self.fixture)
        self.observations["plan_sha256"] = self.frozen["plan_sha256"]
        stage = self.observations["runs"][0]["stages"][1]
        stage.pop("attempt", None)
        stage.update(state="skipped", model="claude-fixture", effort=None,
                     skip_reason="unchanged_confirmed_snapshot",
                     capture_observation_ref="11111111-1111-4111-8111-111111111111",
                     snapshot_sha256="a" * 64, selector_calls=0, new_records=0)
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["counts"]["skipped_capture_stages"], 1)
        self.assertEqual(result["counts"]["unknown_effort_stages"], 1)
        self.assertTrue(any("selected hook testimony" in issue for issue in result["problems"]))
        for field, wrong in (("provider", self.capture_provider("baseline")),
                             ("selector_calls", 1), ("new_records", 1),
                             ("capture_observation_ref", "caller-only")):
            with self.subTest(field=field):
                original = stage.get(field)
                stage[field] = wrong
                with self.assertRaisesRegex(ValueError, "selected capture skip shape"):
                    gate.report(self.frozen, self.fixture, self.observations, self.native)
                if original is None:
                    stage.pop(field)
                else:
                    stage[field] = original

    def test_native_join_requires_completed_model_work(self):
        self.native["workflows"][0]["sender_provider"]["native_completed"] = False
        self.native["workflows"][0]["deliveries"][0]["provider"]["usage"]["observed_responses"] = 0
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertTrue(any("native turn incomplete" in issue for issue in result["problems"]))
        self.assertTrue(any("native response missing" in issue for issue in result["problems"]))

    def test_native_response_identity_cannot_be_replaced_by_a_count(self):
        self.native["workflows"][0]["sender_provider"].pop("response_refs")
        self.native["workflows"][0]["deliveries"][0]["provider"]["response_refs"] = []
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(sum("native response missing" in issue for issue in result["problems"]), 2)

    def test_explicit_native_publication_and_attempt_failure_block_observed_stage(self):
        self.native["workflows"][0]["publication"] = "failed"
        self.native["workflows"][0]["deliveries"][0]["settlement"]["status"] = "failed"
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertTrue(any("native publication not committed" in issue for issue in result["problems"]))
        self.assertTrue(any("native attempt not settled" in issue for issue in result["problems"]))
        self.assertEqual(result["counts"]["observed_runs"], 88)

    def test_complete_panel_is_ready_for_human_grade_with_unknown_usage_and_failed_run(self):
        first = self.observations["runs"][0]
        first.update(state="failed", failure_disposition="processing_failed")
        first["stages"][0] = dict(stage_id=first["stages"][0]["stage_id"], state="failed",
                                  failure_disposition="provider_timeout")
        first["stages"][1] = dict(stage_id=first["stages"][1]["stage_id"], state="not_reached")
        self.native["workflows"][1]["sender_provider"]["usage"]["tokens"]["reasoning"]["total"] = None
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "ready_for_human_grade")
        self.assertEqual(result["independent_task_acceptance"], "unassessed")
        self.assertEqual(result["release_decision"], "defer")
        self.assertEqual(result["counts"]["planned_runs"], 88)
        self.assertEqual(result["counts"]["failed_runs"], 1)
        self.assertEqual(result["counts"]["observed_runs"], 87)
        self.assertEqual(result["counts"]["planned_stages"], 176)
        self.assertEqual(result["counts"]["unknown_usage_fields"]["reasoning"], 1)

    def test_missing_run_or_stage_stays_in_denominator(self):
        self.observations["runs"].pop()
        self.observations["runs"][0]["stages"].pop()
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["counts"]["planned_runs"], 88)
        self.assertEqual(result["counts"]["missing_runs"], 1)
        self.assertEqual(result["counts"]["missing_stages"], 3)

    def test_byte_only_and_wrong_native_workload_cannot_claim_readiness(self):
        self.native["workflows"][0]["sender_provider"] = None
        self.native["workflows"][1]["workload"] = "wrong_project_memory"
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["counts"]["unknown_provider_stages"], 1)
        self.assertTrue(any("workload" in issue for issue in result["problems"]))

    def test_same_case_event_swapped_between_arms_is_incomplete(self):
        first, second = self.observations["runs"][0], self.observations["runs"][len(gate.CASES)]
        first["event"], second["event"] = second["event"], first["event"]
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(sum("native workload" in issue for issue in result["problems"]), 2)

    def test_exact_stage_event_and_pin_joins_refuse_drift(self):
        for mutate, message in (
            (lambda: self.observations["runs"][0]["stages"][0].update(stage_id="other"), "unplanned stage"),
            (lambda: self.observations["runs"][0]["stages"][0].update(route="OpenRouter"), "route drift"),
            (lambda: self.observations["runs"][0]["stages"][0].update(model="wrong"), "model/effort drift"),
            (lambda: self.observations["runs"][0]["stages"][0].update(pins={}), "pin drift"),
            (lambda: self.observations["runs"][1].update(event=self.observations["runs"][0]["event"]),
             "one native event"),
        ):
            with self.subTest(message=message):
                original = copy.deepcopy(self.observations)
                mutate()
                with self.assertRaisesRegex(ValueError, message):
                    gate.report(self.frozen, self.fixture, self.observations, self.native)
                self.observations = original

    def test_frozen_inputs_and_all_22_cases_are_checked(self):
        self.plan["cases"].pop()
        with self.assertRaisesRegex(ValueError, "22 ordered case IDs"):
            gate.freeze(self.plan, self.fixture)
        self.plan["cases"] = list(gate.CASES)
        self.fixture.write_text(self.fixture_body + " ")
        with self.assertRaisesRegex(ValueError, "fixture hash mismatch"):
            gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.fixture.write_text(self.fixture_body)
        self.frozen["plan"]["limits"]["elapsed_seconds"] = 1
        with self.assertRaisesRegex(ValueError, "frozen plan hash mismatch"):
            gate.report(self.frozen, self.fixture, self.observations, self.native)

    def test_failure_and_unknown_remain_explicit(self):
        row = self.observations["runs"][0]
        row["state"] = "failed"
        with self.assertRaisesRegex(ValueError, "failure disposition"):
            gate.report(self.frozen, self.fixture, self.observations, self.native)
        row["failure_disposition"] = "processing_failed"
        row["stages"][0]["state"] = "unknown"
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertEqual(result["counts"]["unknown_stages"], 1)
        self.assertEqual(result["counts"]["failed_runs"], 1)

    def test_unrun_without_an_event_is_counted(self):
        row = self.observations["runs"][0]
        row.update(state="unrun", event=None,
                   stages=[dict(stage_id=stage["stage_id"], state="not_reached") for stage in row["stages"]])
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["counts"]["unrun_runs"], 1)
        self.assertEqual(result["state"], "incomplete")

    def test_native_provider_pin_drift_is_incomplete(self):
        self.native["workflows"][0]["sender_provider"]["pin"] = "wrong-pin"
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "incomplete")
        self.assertTrue(any("provider model/account/effort/pin" in issue for issue in result["problems"]))

    def test_caller_claim_of_acceptance_never_becomes_an_independent_grade(self):
        self.observations["accepted_task_outcomes"] = 88
        self.observations["runs"][0]["accepted"] = True
        self.native["workflows"][0]["assessment"] = "accepted"
        result = gate.report(self.frozen, self.fixture, self.observations, self.native)
        self.assertEqual(result["state"], "ready_for_human_grade")
        self.assertEqual(result["independent_task_acceptance"], "unassessed")
        self.assertEqual(result["release_decision"], "defer")

    def test_cli_freeze_and_report_write_separate_owner_only_outputs(self):
        root = self.fixture.parent
        plan, observations, native = (root / name for name in ("plan.json", "observations.json", "native.json"))
        for path, value in ((plan, self.plan), (observations, self.observations), (native, self.native)):
            path.write_text(json.dumps(value))
        frozen, output = root / "frozen.json", root / "report.json"
        script = Path(gate.__file__)
        for command in (["freeze", "--plan", str(plan), "--fixture", str(self.fixture),
                         "--output", str(frozen)],
                        ["report", "--frozen", str(frozen), "--fixture", str(self.fixture),
                         "--observations", str(observations), "--workflow-report", str(native),
                         "--output", str(output)]):
            completed = subprocess.run([sys.executable, str(script), *command],
                                       capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(output.read_text())["state"], "ready_for_human_grade")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
