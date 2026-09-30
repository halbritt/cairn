"""Offline contracts for provider failure classification and task admission."""
import json
import contextlib
import io
import tempfile
import threading
from unittest.mock import Mock, patch
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trial_task_eval as te  # noqa: E402


def claude_cap():
    # Native terminal shape, with synthetic content rather than a retained session.
    message = "You've hit your weekly limit · resets 9am (America/Los_Angeles)"
    return [dict(type="assistant", error="rate_limit", message=dict(
                model="<synthetic>", content=[dict(type="text", text=message)])),
            dict(type="result", subtype="success", is_error=True,
                 terminal_reason="api_error", api_error_status=429, result=message,
                 num_turns=3, total_cost_usd=.04)]


class ProviderAdmissionTest(unittest.TestCase):
    def test_terminal_weekly_cap_retains_failure_and_cost(self):
        trace = te.parse_stream("\n".join(map(json.dumps, claude_cap())))
        self.assertEqual(trace.get("admission_failure"), dict(
            provider="claude", reason="capacity_exhausted", api_error_status=429))
        self.assertEqual(te.execution_failure(trace, 1), "nonzero_exit")
        self.assertEqual(trace["cost_usd"], .04)
        self.assertEqual(trace["turns"], 3)


    def test_authentication_is_terminal_metadata_not_answer_text(self):
        terminal = dict(type="result", is_error=True, terminal_reason="api_error",
                        api_error_status=401, result="Authentication required")
        trace = te.parse_stream(json.dumps(terminal))
        self.assertEqual(trace["admission_failure"]["reason"], "authentication_failed")
        for changes in [dict(is_error=False), dict(terminal_reason="max_turns"),
                        dict(api_error_status="401"), dict(api_error_status=403)]:
            with self.subTest(changes=changes):
                self.assertIsNone(te.parse_stream(json.dumps(dict(terminal, **changes)))["admission_failure"])

    def test_transient_errors_tool_text_and_recovered_calls_do_not_stop(self):
        cap = claude_cap()
        variants = [
            [dict(type="result", is_error=False, result=cap[-1]["result"])],
            [dict(type="user", message=dict(content=[dict(type="tool_result", is_error=True,
                  content=json.dumps(cap[-1]))]))],
            [dict(type="system", subtype="api_error", status=429),
             dict(type="result", is_error=True, terminal_reason="api_error", api_error_status=429,
                  result="Too many requests; retry later")],
            [cap[0], dict(cap[1], result="Request rate exceeded")],
            [dict(type="assistant", message=cap[0]["message"]), cap[1]],
            [cap[0], dict(cap[1], is_error=False, terminal_reason="end_turn")],
            [*cap, dict(type="result", is_error=False, result="Recovered and completed")],
            [cap[0]],  # incomplete stream is not a confirmed terminal cap
        ]
        for events in variants:
            with self.subTest(events=events):
                self.assertIsNone(te.parse_stream("\n".join(map(json.dumps, events)))["admission_failure"])


class SchedulingTest(unittest.TestCase):
    def test_confirmed_cap_stops_future_tasks_and_writes_partial_report(self):
        launched = []

        def attempt(case, arm, seed, order, args, stores, out):
            launched.append(case["id"])
            trace = te.parse_stream("\n".join(map(json.dumps, claude_cap())))
            return dict(run_id=f"{case['id']}.{arm}.s{seed}", case=case["id"], arm=arm,
                        seed=seed, outcome="harness_error", execution_failure=te.execution_failure(trace, 1),
                        trace=trace, seconds=7.3)

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "results"
            with patch.object(te, "run_agent", side_effect=attempt), patch.object(
                    te, "run", return_value=Mock(stdout=b"offline provider")), contextlib.redirect_stdout(io.StringIO()):
                code = te.main(["agent", "--legacy-fixtures", "--output", str(out), "--arms", "none", "--parallel", "1",
                                "--cases", "db-coverage", "deploy-stamp", "local-ci", "rhumb-ci-scope"])
            report = json.loads((out / "agent.json").read_text())
            plan = json.loads((out / "plan.json").read_text())
        self.assertEqual(launched, ["db-coverage"])
        self.assertEqual(code, 2)
        self.assertEqual(len(plan["runs"]), 4)
        self.assertEqual(len(report["records"]), 1)
        self.assertEqual(report["records"][0]["execution_failure"], "nonzero_exit")
        self.assertEqual(report["admission"]["state"], "stopped_provider_failure")
        self.assertEqual(report["admission"]["stop"]["run_id"], "db-coverage.none.s0")
        self.assertEqual([r["case"] for r in report["admission"]["not_started"]],
                         ["deploy-stamp", "local-ci", "rhumb-ci-scope"])
        self.assertEqual(report["summary"]["none"]["runs"], 1)


    def test_inflight_attempt_finishes_but_no_replacement_is_admitted(self):
        peer_started, failure_observed = threading.Event(), threading.Event()
        launched = []

        def attempt(case, arm, seed, order, args, stores, out):
            launched.append(case["id"])
            if case["id"] == "db-coverage":
                self.assertTrue(peer_started.wait(3), "parallel peer never started")
                trace = te.parse_stream("\n".join(map(json.dumps, claude_cap())))
                return dict(run_id=f"{case['id']}.{arm}.s{seed}", case=case["id"], arm=arm,
                            seed=seed, outcome="harness_error", trace=trace)
            peer_started.set()
            self.assertTrue(failure_observed.wait(3), "controller never retained first failure")
            return dict(run_id=f"{case['id']}.{arm}.s{seed}", case=case["id"], arm=arm,
                        seed=seed, outcome="correct", trace={})

        def output(message, **kwargs):
            if json.loads(message).get("run_id") == "db-coverage.none.s0":
                failure_observed.set()

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "results"
            with patch.object(te, "run_agent", side_effect=attempt), patch.object(
                    te, "run", return_value=Mock(stdout=b"offline provider")), patch("builtins.print", side_effect=output):
                code = te.main(["agent", "--legacy-fixtures", "--output", str(out), "--arms", "none", "--parallel", "2",
                                "--cases", "db-coverage", "deploy-stamp", "local-ci", "rhumb-ci-scope"])
            report = json.loads((out / "agent.json").read_text())
        self.assertEqual(code, 2)
        self.assertEqual(set(launched), {"db-coverage", "deploy-stamp"})
        self.assertEqual(len(report["records"]), 2)
        self.assertEqual(report["records"][1]["outcome"], "correct")
        self.assertEqual(len(report["admission"]["not_started"]), 2)

    def test_ordinary_failures_continue_and_exceptions_remain_attempts(self):
        launched = []

        def attempt(case, arm, seed, order, args, stores, out):
            launched.append(case["id"])
            if case["id"] == "db-coverage":
                raise RuntimeError("fixture setup failed")
            return dict(run_id=f"{case['id']}.{arm}.s{seed}", case=case["id"], arm=arm,
                        seed=seed, outcome="mistake", trace={})

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "results"
            with patch.object(te, "run_agent", side_effect=attempt), patch.object(
                    te, "run", return_value=Mock(stdout=b"offline provider")), contextlib.redirect_stdout(io.StringIO()):
                code = te.main(["agent", "--legacy-fixtures", "--output", str(out), "--arms", "none", "--parallel", "1",
                                "--cases", "db-coverage", "deploy-stamp", "local-ci"])
            report = json.loads((out / "agent.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(len(launched), 3)
        self.assertEqual(report["records"][0]["outcome"], "harness_error")
        self.assertEqual(report["admission"], dict(state="complete", stop=None, planned=3, admitted=3, not_started=[]))


    def test_retained_cap_stops_admission_even_if_grading_raises(self):
        launched = []

        def attempt(case, arm, seed, order, args, stores, out):
            launched.append(case["id"])
            base = out / "runs" / f"{case['id']}.{arm}.s{seed}"
            base.mkdir(parents=True)
            (base / "stream.jsonl").write_text("\n".join(map(json.dumps, claude_cap())))
            raise RuntimeError("grading output unavailable")

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "results"
            with patch.object(te, "run_agent", side_effect=attempt), patch.object(
                    te, "run", return_value=Mock(stdout=b"offline provider")), contextlib.redirect_stdout(io.StringIO()):
                code = te.main(["agent", "--legacy-fixtures", "--output", str(out), "--arms", "none", "--parallel", "1",
                                "--cases", "db-coverage", "deploy-stamp"])
            report = json.loads((out / "agent.json").read_text())
        self.assertEqual(launched, ["db-coverage"])
        self.assertEqual(code, 2)
        self.assertEqual(report["records"][0]["outcome"], "harness_error")
        self.assertEqual(report["records"][0]["error"], "grading output unavailable")
        self.assertEqual(report["records"][0]["trace"]["cost_usd"], .04)
        self.assertEqual(len(report["admission"]["not_started"]), 1)


    def test_partial_report_still_closes_owned_memory_store(self):
        store = Mock(binary=Path("/bin/true"), backend="lexical", readiness=None, env={})
        trace = te.parse_stream("\n".join(map(json.dumps, claude_cap())))

        def attempt(case, arm, seed, order, args, stores, out):
            return dict(run_id=f"{case['id']}.{arm}.s{seed}", case=case["id"], arm=arm,
                        seed=seed, outcome="harness_error", trace=trace)

        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "results"
            hook = Path(directory) / "memory.py"
            hook.write_text("# offline hook identity only\n")
            with patch.object(te, "run_agent", side_effect=attempt), patch.object(te, "TrialStore", return_value=store), \
                    patch.object(te, "cairn_json", return_value={"revision": "fixture"}), \
                    patch.object(te, "run", return_value=Mock(stdout=b"offline provider")), \
                    contextlib.redirect_stdout(io.StringIO()):
                code = te.main(["agent", "--legacy-fixtures", "--output", str(out), "--arms", "--memory", f"baseline:/bin/true:{hook}",
                                "--parallel", "1", "--cases", "db-coverage", "deploy-stamp"])
            report = json.loads((out / "agent.json").read_text())
        self.assertEqual(code, 2)
        store.stop.assert_called_once()
        self.assertEqual(len(report["admission"]["not_started"]), 1)
        self.assertEqual(report["memory"]["baseline"]["backend"], "lexical")


if __name__ == "__main__":
    unittest.main()
