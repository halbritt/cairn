#!/usr/bin/env python3
"""Frozen, model-free capture admission comparison over synthetic dialogue.

Runs the real capture path with a local selector stub. The candidate is shadow
only: this program never suppresses capture or calls a provider/database.
"""

import argparse
import copy
import importlib.util
import json
import tempfile
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "integrations/lifecycle/memory.py"
spec = importlib.util.spec_from_file_location("capture_admission_hook", SOURCE)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

EMPTY = dict(memories=[], workstream=None, checkpoint=None)
CORRECTION = dict(memories=[dict(record_id=None, kind="preference", title="Migration endpoint",
                                 body="Use the corrected endpoint for the migration.")],
                  workstream=None, checkpoint=None)
HANDOFF = dict(memories=[], workstream="Migration", checkpoint="The migration remains unfinished.")
BASE = [dict(role="user", text="Plan the migration."),
        dict(role="assistant", text="I recorded the plan.")]


class LocalMemory:
    def __init__(self):
        self.config = dict(harness="claude", repo="/synthetic", model="fixture-model")
        self.capture_observation = {}

    def checkpoint(self, _title):
        return None


def wake():
    coordination = SOURCE.with_name("coordination.py")
    found = importlib.util.spec_from_file_location("capture_admission_coordination", coordination)
    module = importlib.util.module_from_spec(found)
    found.loader.exec_module(module)
    return module.wake_message(dict(
        session=dict(agent_id="11111111-1111-4111-8111-111111111111",
                     execution_id="22222222-2222-4222-8222-222222222222"),
        delivery_id="33333333-3333-4333-8333-333333333333"))


def run_case(event, confirmed, addition, selector, *, source_failure=False):
    memory = LocalMemory()
    state = copy.deepcopy(confirmed)
    event = dict(event, messages=BASE + addition)
    calls, saved = [], []

    def choose(*_args, **_kwargs):
        calls.append("selector")
        if selector == "quota_failure":
            raise hook.HookError("synthetic quota refusal", code="API_UNAVAILABLE")
        return dict(structured_output={"empty": EMPTY, "correction": CORRECTION,
                                       "handoff": HANDOFF}[selector])

    def source(*_args):
        if source_failure:
            raise hook.HookError("synthetic source refusal", code="PAYLOAD_UNAVAILABLE")
        return []

    def save(_memory, body, kind, old):
        saved.append(dict(kind=kind, revised=old is not None))
        return "synthetic-record"

    outcome = "completed"
    with patch.object(hook, "handoff_candidates", side_effect=source), \
         patch.object(hook, "durable_candidates", return_value=[]), \
         patch.object(hook, "select_json", side_effect=choose), \
         patch.object(hook, "save_note", side_effect=save):
        try:
            hook.capture(memory, event, state)
        except hook.HookError as exc:
            outcome = exc.code or "error"
    return dict(candidate=memory.capture_observation.get("admission_candidate") == "transport_only",
                selector_calls=len(calls), selected_writes=len(saved), outcome=outcome,
                confirmed=bool(confirmed.get("captured_digest")),
                marker_advanced=state.get("captured_digest") != confirmed.get("captured_digest"))


def evaluate():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / ".git").mkdir()
        event = dict(hook_event_name="PreCompact", session_id="fixture-session", cwd=str(root))
        seed = LocalMemory()
        confirmed = {}
        with patch.object(hook, "handoff_candidates", return_value=[]), \
             patch.object(hook, "durable_candidates", return_value=[]), \
             patch.object(hook, "select_json", return_value=dict(structured_output=EMPTY)):
            hook.capture(seed, dict(event, messages=BASE), confirmed)
        notification = dict(role="user", text=wake())
        cases = {
            "same_snapshot": run_case(event, confirmed, [], "empty"),
            "transport_only": run_case(event, confirmed, [notification], "empty"),
            "owner_correction": run_case(event, confirmed, [notification,
                dict(role="user", text="Correction: use the new endpoint.")], "correction"),
            "unfinished_handoff": run_case(event, confirmed, [
                dict(role="user", text="The migration is unfinished; preserve the handoff.")], "handoff"),
            "status_only": run_case(event, confirmed, [
                dict(role="user", text="Any status?")], "empty"),
            "quota_failure": run_case(event, confirmed, [notification], "quota_failure"),
            "source_failure": run_case(event, confirmed, [notification], "empty", source_failure=True),
        }
        required = ("owner_correction", "unfinished_handoff")
        uncertain = ("quota_failure", "source_failure")
        return dict(schema="cairn.capture-admission-fixture/1", fixture_count=len(cases), cases=cases,
                    comparison=dict(unchanged_selector_calls=sum(v["selector_calls"] for v in cases.values()),
                        candidate_cases=sum(v["candidate"] for v in cases.values()),
                        candidate_selector_calls=sum(v["selector_calls"] for v in cases.values() if v["candidate"]),
                        required_selected_writes=sum(cases[name]["selected_writes"] for name in required),
                        candidate_required_misses=sum(cases[name]["selected_writes"] for name in required
                                                      if cases[name]["candidate"]),
                        candidate_uncertain_cases=sum(cases[name]["candidate"] for name in uncertain),
                        unchanged_empty_status_calls=cases["status_only"]["selector_calls"]),
                    limits=["synthetic fixture, no provider tokens or accepted task outcomes",
                            "no state-commit or concurrent-hook simulation",
                            "candidate is observed; no production capture is skipped"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate()
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(schema=report["schema"], fixture_count=report["fixture_count"], output=str(args.output))))


if __name__ == "__main__":
    main()
