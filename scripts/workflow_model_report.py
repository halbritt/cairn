#!/usr/bin/env python3
"""Join local native usage and inbox journals into a payload-free workflow report.

The input manifest is private and selects exact native log files. Optional event
metadata is an authorized, selected export, not a replacement for Cairn state.
Unknown usage, sender publication, and task acceptance stay unknown.
"""
import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

import audit_model_overhead as audit


FIELDS = ("input", "cached", "write", "output", "reasoning")
HARNESS = ("codex", "claude", "agy", "hermes", "opencode", "relay")
LABEL = re.compile(r"[a-zA-Z0-9_.:/-]{1,128}\Z")


def opaque(value):
    return hashlib.sha256(("cairn.workflow/1\0" + value).encode()).hexdigest()[:24]


def event_uuid(value):
    if not isinstance(value, str):
        raise ValueError("event_id must be a UUID")
    return str(uuid.UUID(value))


def attempt_ref(value):
    """Opaque only canonical native or self-drain attempt identities."""
    if not isinstance(value, str):
        return None
    prefix = "drain-" if value.startswith("drain-") else ""
    raw = value[len(prefix):]
    try:
        canonical = str(uuid.UUID(raw))
    except ValueError:
        return None
    return opaque(prefix + canonical) if raw == canonical else None


def safe_label(value):
    return value if isinstance(value, str) and LABEL.fullmatch(value) else None


def read_links(path):
    if path is None:
        return {}, {}
    source = json.loads(path.read_text())
    if source.get("schema") != "cairn.workflow-links/1":
        raise ValueError("unsupported workflow links schema")
    events, assessments = {}, {}
    for item in source.get("events", []):
        key = event_uuid(item.get("event_id"))
        if key in events:
            raise ValueError("duplicate event metadata")
        state = item.get("publication_state")
        if state not in (None, "committed", "pending", "failed"):
            raise ValueError("invalid publication state")
        kind = item.get("kind")
        if kind is not None and kind not in ("notice", "response", "request"):
            raise ValueError("invalid event kind")
        cause = item.get("causation_id")
        owner_input_joined = item.get("owner_input_joined")
        if owner_input_joined is not None and type(owner_input_joined) is not bool:
            raise ValueError("owner_input_joined must be boolean or null")
        wake_state = item.get("wake_state")
        if wake_state not in (None, "started", "refused", "failed", "not_started"):
            raise ValueError("invalid wake state")
        retry_count = item.get("retry_count")
        if retry_count is not None and (type(retry_count) is not int or retry_count < 0):
            raise ValueError("retry_count must be nonnegative or null")
        workload = item.get("workload")
        if workload is not None and (not isinstance(workload, str) or
                                     not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", workload)):
            raise ValueError("workload must be an opaque label")
        events[key] = dict(publication_state=state,
                           causation_id=event_uuid(cause) if cause else None,
                           workload=workload, kind=kind,
                           sender_turn_id=item.get("sender_turn_id"),
                           sender_binding=safe_label(item.get("sender_binding")),
                           refusal_code=safe_label(item.get("refusal_code")),
                           owner_input_joined=owner_input_joined,
                           wake_state=wake_state, retry_count=retry_count)
    for item in source.get("assessments", []):
        key = event_uuid(item.get("event_id"))
        if key in assessments:
            raise ValueError("duplicate assessment")
        outcome = item.get("outcome")
        if outcome not in ("accepted", "rejected", "unassessed"):
            raise ValueError("invalid assessment outcome")
        assessments[key] = outcome
    return events, assessments


def load_sessions(manifest, since, until):
    sessions = []
    for item in manifest:
        harness = item.get("harness")
        if harness not in ("codex", "claude"):
            raise ValueError("native usage parser supports codex and claude only")
        path = Path(item["path"])
        result = (audit.analyze_codex if harness == "codex" else audit.analyze_claude)(path, since, until)
        sessions.append(dict(harness=harness, home=safe_label(item.get("home")),
                             binding=safe_label(item.get("binding")), pin=safe_label(item.get("pin")),
                             turns=result["turns"], errors=result["errors"]))
    return sessions


def usage_summary(turn, harness):
    responses = turn.get("responses", [])
    usage = turn.get("usage", [])
    fields = {}
    for name in FIELDS:
        known = [row[name] for row in usage if type(row.get(name)) is int and row[name] >= 0]
        complete = bool(responses) and len(usage) == len(responses) and len(known) == len(responses)
        fields[name] = dict(total=sum(known) if complete else None,
                            known_sum=sum(known), missing_records=max(0, len(responses)-len(known)))
    return dict(observed_responses=len(responses), usage_records=len(usage),
                response_coverage="usage_records_only" if harness == "codex" else "assistant_message_ids",
                tokens=fields)


def turn_index(sessions):
    by_turn = collections.defaultdict(list)
    for session in sessions:
        for turn_id, turn in session["turns"].items():
            by_turn[turn_id].append((session, turn))
    return by_turn


def matching_turn(context, index):
    turn_id = (context.get("native_turn_id") or "").removeprefix("claude-channel:")
    matches = index.get(turn_id, []) if turn_id else []
    binding = context.get("binding")
    if binding:
        exact = [item for item in matches if item[0]["binding"] == binding]
        if exact:
            matches = exact
    if len(matches) != 1:
        return None, "ambiguous" if matches else "missing"
    session, turn = matches[0]
    return (session, turn), "binding" if session["binding"] == binding else "unique_turn_id"


def journal_for(path):
    journal_path = path.parent.parent / "intents" / (path.stem + ".json")
    return json.loads(journal_path.read_text()) if journal_path.exists() else {}


def settlement(journal):
    completion = journal.get("completion") or {}
    reply = journal.get("response") or {}
    argv = completion.get("argv") or []
    disposition = argv[argv.index("--disposition") + 1] if "--disposition" in argv and argv.index("--disposition") + 1 < len(argv) else "handled"
    code = completion.get("code")
    return dict(action=safe_label(completion.get("kind")), status=safe_label(completion.get("status")),
                disposition=safe_label(disposition) if completion.get("status") == "committed" else None,
                code=safe_label(code),
                code_extent="absent" if code is None else ("bounded" if safe_label(code) else "omitted_unbounded"),
                reply_status=safe_label(reply.get("status")))


def provider_projection(session, turn, duplicate_ids):
    start, end = turn.get("start"), turn.get("end")
    usage = usage_summary(turn, session["harness"])
    duplicated = any(response in duplicate_ids for response in turn.get("responses", []))
    if duplicated:
        for field in FIELDS:
            usage["tokens"][field]["total"] = None
    return dict(harness=session["harness"], account=session["home"], pin=session["pin"],
                models=sorted({safe_label(v) for v in turn["models"] if safe_label(v)}),
                efforts=sorted({safe_label(v) for v in turn["efforts"] if safe_label(v)}) or None,
                response_refs=[opaque("response\0" + value) for value in turn["responses"]],
                activity="idle_wake" if turn["wake"] else "active_or_unclassified",
                native_completed=turn["completed"], tools=turn["tools"],
                cairn_tools=turn["cairn_tools"], compactions=turn["compactions"],
                duration_seconds=end-start if start is not None and end is not None else None,
                duplicate_response_id=duplicated, usage=usage)


def dimension_coverage(workflows):
    grouped = {}
    for workflow in workflows:
        stages = [("sender", workflow["sender_provider"])]
        stages.extend(("recipient", row["provider"]) for row in workflow["deliveries"])
        for stage, provider in stages:
            if stage == "sender" and provider is None and workflow["publication_evidence"] != "selected_event_metadata":
                continue
            labels = provider or {}
            models, efforts = labels.get("models") or [], labels.get("efforts") or []
            key = (stage, labels.get("harness"), labels.get("account"),
                   models[0] if len(models) == 1 else ("mixed" if models else None),
                   efforts[0] if len(efforts) == 1 else ("mixed" if efforts else None),
                   labels.get("pin"), workflow["workload"])
            row = grouped.setdefault(key, dict(stage=stage, harness=key[1], account=key[2],
                                               model=key[3], effort=key[4], pin=key[5],
                                               workload=key[6], observations=0,
                                               joined_provider=0, responses=0,
                                               no_response_observation=0,
                                               missing_usage_records=0,
                                               missing_usage_fields={field: 0 for field in FIELDS}))
            row["observations"] += 1
            if provider:
                row["joined_provider"] += 1
                usage = provider["usage"]
                row["responses"] += usage["observed_responses"]
                row["no_response_observation"] += usage["observed_responses"] == 0
                row["missing_usage_records"] += max(0, usage["observed_responses"] - usage["usage_records"])
                for field in FIELDS:
                    row["missing_usage_fields"][field] += usage["tokens"][field]["missing_records"]
    return sorted(grouped.values(), key=lambda row: tuple(str(v) for v in
                 (row["stage"], row["harness"], row["account"], row["model"],
                  row["effort"], row["pin"], row["workload"])))


def compile_report(sessions, state_root, events=None, assessments=None, max_deliveries=10000):
    events, assessments = events or {}, assessments or {}
    contexts = sorted(state_root.glob("*/inbox/*.json"))
    if len(contexts) > max_deliveries:
        raise ValueError("coordination state exceeds max-deliveries; select a bounded fixture")
    index = turn_index(sessions)
    response_ids = collections.Counter(response for session in sessions
                                        for turn in session["turns"].values()
                                        for response in turn.get("responses", []))
    duplicate_ids = {response for response, count in response_ids.items() if count > 1}
    rows = collections.defaultdict(list)
    coverage = collections.Counter()
    turn_claims = collections.Counter()
    for path in contexts:
        context = json.loads(path.read_text())
        event_id = event_uuid(context.get("event_id"))
        context["binding"] = path.parent.parent.name
        joined, join_basis = matching_turn(context, index)
        coverage["turn_" + join_basis] += 1
        journal = journal_for(path)
        source = context.get("source") or {}
        version = source.get("version")
        row = dict(delivery=opaque(context["delivery_id"]), attempt=attempt_ref(context.get("attempt_id")),
                   kind=safe_label(context.get("kind")),
                   admission="context_retained", turn_join=join_basis,
                   source=dict(record=opaque(source["record_id"]) if isinstance(source.get("record_id"), str) else None,
                               version=version if type(version) is int and version > 0 else None),
                   settlement=settlement(journal), provider=None)
        if joined:
            session, turn = joined
            turn_key = (session["harness"], session["home"], context.get("native_turn_id"))
            turn_claims[turn_key] += 1
            row["turn_ref"] = opaque("\0".join(str(v) for v in turn_key))
            row["provider"] = provider_projection(session, turn, duplicate_ids)
        rows[event_id].append(row)
    response_by_cause = collections.defaultdict(list)
    for event_id, metadata in events.items():
        if metadata["causation_id"]:
            response_by_cause[metadata["causation_id"]].append(event_id)
    ref_counts = collections.Counter(row["turn_ref"] for deliveries in rows.values()
                                     for row in deliveries if row.get("turn_ref"))
    workflows = []
    for event_id in sorted(set(rows) | set(events)):
        deliveries = rows[event_id]
        metadata = events.get(event_id, {})
        for row in deliveries:
            if row.get("turn_ref"):
                # A turn can contain more than one admitted delivery. Its usage
                # remains attached to the turn, never claimed as an event total.
                row["shared_turn"] = ref_counts[row["turn_ref"]] > 1
        sender = None
        if metadata.get("sender_turn_id"):
            joined, basis = matching_turn(dict(native_turn_id=metadata["sender_turn_id"],
                                               binding=metadata.get("sender_binding")), index)
            coverage["sender_turn_" + basis] += 1
            if joined:
                sender = provider_projection(*joined, duplicate_ids)
                sender["turn_join"] = basis
        workflows.append(dict(event=opaque(event_id), kind=deliveries[0]["kind"] if deliveries else metadata.get("kind"),
                              kind_conflict=bool(metadata.get("kind") and deliveries and
                                                 any(row["kind"] != metadata["kind"] for row in deliveries)),
                              workload=metadata.get("workload"),
                              publication=metadata.get("publication_state", "unknown"),
                              publication_evidence="selected_event_metadata" if metadata.get("publication_state") else "unavailable",
                              sender_provider=sender,
                              refusal_code=metadata.get("refusal_code"),
                              owner_input_joined=metadata.get("owner_input_joined"),
                              wake_state=metadata.get("wake_state"),
                              retry_count=metadata.get("retry_count"),
                              deliveries=deliveries,
                              response_events=[opaque(v) for v in sorted(response_by_cause[event_id])],
                              assessment=assessments.get(event_id, "unknown"),
                              assessment_evidence="selected_event_metadata" if event_id in assessments else "unavailable"))
    traced = {row["provider"]["harness"] for deliveries in rows.values()
              for row in deliveries if row["provider"]}
    unsupported = [dict(harness=h, reason=("no_joined_delivery" if h in {s["harness"] for s in sessions}
                                           else "native_provider_usage_parser_unavailable"))
                   for h in HARNESS if h not in traced]
    covered_events = set(rows) | set(events)
    coverage.update(deliveries=len(contexts), events=len(covered_events),
                    attempt_known=sum(row["attempt"] is not None for deliveries in rows.values()
                                      for row in deliveries),
                    attempt_unknown=sum(row["attempt"] is None for deliveries in rows.values()
                                        for row in deliveries),
                    input_sessions=len(sessions),
                    duplicate_provider_response_ids=sum(n > 1 for n in response_ids.values()),
                    assessed_events=sum(k in covered_events for k in assessments),
                    publication_metadata_events=sum(v.get("publication_state") is not None for v in events.values()),
                    owner_input_known=sum(v.get("owner_input_joined") is not None for v in events.values()),
                    wake_state_known=sum(v.get("wake_state") is not None for v in events.values()),
                    retry_count_known=sum(v.get("retry_count") is not None for v in events.values()),
                    shared_turns=sum(v > 1 for v in turn_claims.values()))
    return dict(schema="cairn.workflow-model-report/1", coverage=dict(coverage),
                context_scope="all retained inbox contexts under the selected state root",
                coverage_by_dimension=dimension_coverage(workflows),
                unsupported=unsupported, workflows=workflows,
                limitations=["Provider usage is whole-turn/response telemetry, not Cairn marginal cost.",
                             "A committed settlement is handling; only explicit assessment states task acceptance.",
                             "Missing provider fields and unobserved harnesses are unknown, not zero.",
                             "Sender publication and response lineage require selected authorized event metadata."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--coordination-state", type=Path, required=True)
    parser.add_argument("--event-metadata", type=Path)
    parser.add_argument("--max-deliveries", type=int, default=10000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    since, until = audit.timestamp(args.since), audit.timestamp(args.until)
    if since is None or until is None or since >= until or args.max_deliveries < 1:
        parser.error("require since < until and positive max-deliveries")
    manifest = json.loads(args.manifest.read_text())
    events, assessments = read_links(args.event_metadata)
    report = compile_report(load_sessions(manifest, since, until), args.coordination_state,
                            events, assessments, args.max_deliveries)
    report["provider_window"] = dict(since=args.since, until=args.until)
    output = json.dumps(report, indent=2) + "\n"
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(output)
    print(json.dumps(dict(output=str(args.output), **report["coverage"])))


if __name__ == "__main__":
    main()
