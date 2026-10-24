#!/usr/bin/env python3
"""Classify selected recovery evidence against a payload-free workflow report.

This does not infer failure from a log substring or assign a whole native turn's
usage to Cairn. Evidence is an explicitly selected local metadata export.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import re

from workflow_model_report import event_uuid, opaque


PHASES = frozenset(("before_admission", "admission", "grant", "source", "tool",
                    "settlement", "reply", "recovery"))
OUTCOMES = frozenset(("refused", "failed", "held", "recovered"))
RECOVERY = frozenset(("pending", "retry_same_intent", "held", "committed", "unknown"))
OPERATORS = frozenset(("none", "manual_retry", "self_drain", "intervention", "unknown"))


def read_evidence(path, max_observations):
    source = json.loads(path.read_text())
    if source.get("schema") != "cairn.recovery-evidence/1" or not isinstance(source.get("observations"), list):
        raise ValueError("unsupported recovery evidence schema")
    if len(source["observations"]) > max_observations:
        raise ValueError("recovery evidence exceeds max-observations")
    observations = []
    seen = set()
    for item in source["observations"]:
        if not isinstance(item, dict):
            raise ValueError("invalid recovery observation")
        event = opaque(event_uuid(item.get("event_id")))
        delivery = item.get("delivery_id")
        if delivery is not None:
            delivery = opaque(event_uuid(delivery))
        attempt = item.get("attempt_id")
        if attempt is not None:
            attempt = opaque(event_uuid(attempt))
        phase, outcome = item.get("phase"), item.get("outcome")
        recovery, operator = item.get("recovery_state"), item.get("operator_action")
        revision = item.get("state_revision")
        if (phase not in PHASES or outcome not in OUTCOMES or recovery not in RECOVERY
                or operator not in OPERATORS or (revision is not None and
                (type(revision) is not int or revision < 0))):
            raise ValueError("invalid recovery classification")
        code = item.get("code")
        if code is not None and (not isinstance(code, str) or
                                 re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", code) is None):
            raise ValueError("recovery code must be a bounded machine code")
        reminder = item.get("reminder_emitted")
        if reminder is not None and type(reminder) is not bool:
            raise ValueError("reminder_emitted must be boolean or null")
        if reminder is not None and (attempt is None or revision is None):
            raise ValueError("reminder evidence requires attempt and state revision")
        key = (event, delivery, attempt, phase, revision)
        if key in seen:
            raise ValueError("duplicate recovery observation identity")
        seen.add(key)
        observations.append(dict(event=event, delivery=delivery, attempt=attempt,
                                 phase=phase, outcome=outcome, recovery_state=recovery,
                                 operator_action=operator, code=code,
                                 state_revision=revision, reminder_emitted=reminder))
    return observations


def compile_report(workflow_report, observations):
    if workflow_report.get("schema") != "cairn.workflow-model-report/1":
        raise ValueError("unsupported workflow report schema")
    events = {row["event"]: row for row in workflow_report["workflows"]}
    rows = []
    coverage = collections.Counter()
    by_phase = collections.defaultdict(lambda: dict(observations=0, joined_delivery=0,
        provider_turns=0, observed_responses=0, missing_provider=0,
        operator_actions=collections.Counter(), recovery_states=collections.Counter()))
    reminder_keys = collections.Counter()
    for observation in observations:
        workflow = events.get(observation["event"])
        matches = ([row for row in workflow["deliveries"] if row["delivery"] == observation["delivery"]]
                   if workflow and observation["delivery"] else [])
        if len(matches) > 1:
            raise ValueError("ambiguous delivery identity in workflow report")
        delivery = matches[0] if matches else None
        provider = delivery.get("provider") if delivery else None
        turn = delivery.get("turn_ref") if delivery else None
        row = dict(observation=observation, workflow_present=workflow is not None,
                   delivery_join="matched" if delivery else ("not_applicable" if observation["delivery"] is None
                                                               else "missing"),
                   provider_join="joined" if provider else "unknown",
                   turn=turn)
        rows.append(row)
        coverage[row["delivery_join"]] += 1
        phase = by_phase[observation["phase"]]
        phase["observations"] += 1
        phase["joined_delivery"] += bool(delivery)
        phase["missing_provider"] += provider is None
        phase["operator_actions"][observation["operator_action"]] += 1
        phase["recovery_states"][observation["recovery_state"]] += 1
        if observation["reminder_emitted"] is True:
            reminder_keys[(observation["attempt"], observation["state_revision"])] += 1
    # A native turn can contain multiple deliveries or failure observations.
    # Count it once per phase and never attribute its whole usage to an event.
    for phase_name, summary in by_phase.items():
        unique_turns = {}
        for row in rows:
            if row["observation"]["phase"] != phase_name or not row["turn"]:
                continue
            workflow = events[row["observation"]["event"]]
            delivery = next(d for d in workflow["deliveries"] if d["delivery"] == row["observation"]["delivery"])
            unique_turns.setdefault(row["turn"], delivery["provider"])
        summary["provider_turns"] = len(unique_turns)
        summary["observed_responses"] = (None if any(p.get("duplicate_response_id") for p in unique_turns.values())
                                         else sum(p["usage"]["observed_responses"] for p in unique_turns.values()))
        summary["operator_actions"] = dict(summary["operator_actions"])
        summary["recovery_states"] = dict(summary["recovery_states"])
    return dict(schema="cairn.recovery-model-report/1", coverage=dict(coverage),
                phases=dict(sorted(by_phase.items())),
                repeated_emitted_reminder_revisions=sum(n - 1 for n in reminder_keys.values() if n > 1),
                observations=rows,
                limitations=["Selected classifications do not prove an unobserved model turn was free.",
                             "Provider response counts belong to whole native turns, not marginal recovery cost.",
                             "A repeated reminder revision is a diagnostic; it does not prove duplicate model ingestion.",
                             "Unselected failure paths, operator actions and relay outcomes remain unknown."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow-report", type=Path, required=True)
    parser.add_argument("--recovery-evidence", type=Path, required=True)
    parser.add_argument("--max-observations", type=int, default=10000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.max_observations < 1:
        parser.error("max-observations must be positive")
    report = compile_report(json.loads(args.workflow_report.read_text()),
                            read_evidence(args.recovery_evidence, args.max_observations))
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(dict(output=str(args.output), observations=len(report["observations"]))))


if __name__ == "__main__":
    main()
