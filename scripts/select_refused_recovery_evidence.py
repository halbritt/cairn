#!/usr/bin/env python3
"""Select exact refused settlement journals for a private CAIRN-226 cohort.

This reads retained local helper state. A journal refusal is an observed helper
result, not proof of provider work, operator action, eventual recovery, or task
acceptance. No source, command input, prompt, or model output is exported.
"""

import argparse
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid


CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z")
MAX_RECORDED_AT = 4_102_444_800  # 2100-01-01 UTC; refuse malformed/future journal times.


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def canonical_uuid(value, label, drain=False):
    require(isinstance(value, str), f"{label}_missing")
    prefix = "drain-" if drain and value.startswith("drain-") else ""
    raw = value[len(prefix):]
    try:
        canonical = str(uuid.UUID(raw))
    except ValueError as error:
        raise ValueError(f"{label}_invalid") from error
    require(raw == canonical, f"{label}_noncanonical")
    return prefix + canonical


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "json_duplicate_key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("json_nonfinite_number")


def _finite_float(value):
    parsed = float(value)
    require(math.isfinite(parsed), "json_nonfinite_number")
    return parsed


def _read_object(path, label):
    require(path.is_file() and not path.is_symlink(), f"{label}_missing_or_symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant, parse_float=_finite_float)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label}_unreadable") from error
    require(isinstance(value, dict), f"{label}_not_object")
    return value


def select(state_root, max_journals=10000, max_observations=1000):
    require(state_root.is_dir() and not state_root.is_symlink(), "state_root_invalid")
    require(type(max_journals) is int and max_journals > 0 and
            type(max_observations) is int and max_observations > 0, "limits_invalid")
    journals = sorted(state_root.glob("*/intents/*.json"))
    require(len(journals) <= max_journals, "journal_limit")
    observations = []
    seen = set()
    for path in journals:
        journal = _read_object(path, "journal")
        require(journal.get("schema") == "cairn.inbox-intent/1", "journal_schema_mismatch")
        completion = journal.get("completion")
        if completion is None:
            continue
        require(isinstance(completion, dict), "journal_completion_invalid")
        if completion.get("status") != "refused":
            continue
        context = _read_object(path.parent.parent / "inbox" / path.name, "context")
        require(context.get("schema") == "cairn.session-inbox/1", "context_schema_mismatch")
        require(journal.get("kind") == context.get("kind") and
                journal.get("kind") in ("request", "notice", "response"), "kind_mismatch")
        require(completion.get("kind") in ("ack", "complete") and
                type(completion.get("recorded_at")) in (int, float) and
                0 < completion["recorded_at"] <= MAX_RECORDED_AT,
                "refusal_operation_missing")
        code = completion.get("code")
        require(isinstance(code, str) and CODE.fullmatch(code), "refusal_code_invalid")
        canonical_uuid(completion.get("request_id"), "request_id")
        event = canonical_uuid(context.get("event_id"), "event_id")
        delivery = canonical_uuid(context.get("delivery_id"), "delivery_id")
        attempt = canonical_uuid(context.get("attempt_id"), "attempt_id", drain=True)
        request_ids = context.get("request_ids")
        require(isinstance(request_ids, dict), "context_request_ids_invalid")
        require(path.stem == attempt and journal.get("delivery_id") == delivery and
                journal.get("attempt_id") == attempt and
                request_ids.get("completion") == completion["request_id"],
                "journal_context_identity_mismatch")
        identity = (event, delivery, attempt)
        require(identity not in seen, "duplicate_refusal_identity")
        seen.add(identity)
        observations.append(dict(event_id=event, delivery_id=delivery,
                                 attempt_id=attempt, phase="settlement",
                                 outcome="refused", code=code,
                                 recovery_state="unknown", operator_action="unknown",
                                 model_invocation="unknown"))
        require(len(observations) <= max_observations, "observation_limit")
    return {"schema": "cairn.recovery-evidence/1", "observations": observations}


def write_private(path, value):
    parent = path.parent
    require(parent.is_dir() and not parent.is_symlink(), "output_parent_invalid")
    mode = stat.S_IMODE(parent.stat().st_mode)
    require(mode & 0o077 == 0, "output_parent_not_private")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--max-journals", type=int, default=10000)
    parser.add_argument("--max-observations", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selected = select(args.state_root, args.max_journals, args.max_observations)
    write_private(args.output, selected)
    counts = {}
    for row in selected["observations"]:
        counts[row["code"]] = counts.get(row["code"], 0) + 1
    print(json.dumps({"observations": len(selected["observations"]),
                      "by_code": dict(sorted(counts.items()))}))


if __name__ == "__main__":
    main()
