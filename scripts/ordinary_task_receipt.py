#!/usr/bin/env python3
"""Prospective ordinary-task native receipt seam for CAIRN-227.

This module has no provider launch. Only a future local runner that owns the
subprocess and its stdin/stdout may turn this projection into trial evidence.
The token-efficiency gate does not consume this schema yet.
"""

import argparse
import hashlib
import json
from pathlib import Path


MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 100_000
MAX_RESPONSES = 1_000
MAX_TOKEN_FIELD = 10_000_000
USAGE_FIELDS = ("input_tokens", "cache_read_input_tokens",
                "cache_creation_input_tokens", "output_tokens")


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True,
                                   separators=(",", ":")).encode()).hexdigest()


def _hex(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def frozen_binding(manifest, workflow_id):
    """Bind one prospective run to exact actor/setup/source identities.

    A manifest is declarative selected input, not proof that setup ran or a
    native turn occurred. The future runner must freeze it before launch.
    """
    _require(isinstance(manifest, dict) and manifest.get("schema") ==
             "cairn.token-efficiency-panel-manifest/1", "panel_manifest_schema")
    _require(_hex(manifest.get("fixture_sha256")), "fixture_hash_missing")
    cases = manifest.get("cases")
    runs = manifest.get("prospective_runs")
    sources = manifest.get("sources")
    hashes = manifest.get("source_body_sha256")
    record_hashes = manifest.get("source_record_sha256")
    _require(isinstance(cases, list) and isinstance(runs, list)
             and isinstance(sources, dict) and isinstance(hashes, dict), "panel_shape")
    _require(all(isinstance(row, dict) and isinstance(row.get("workflow_id"), str)
                 for row in runs), "prospective_run_shape")
    _require(len(runs) == len({row["workflow_id"] for row in runs}),
             "duplicate_workflow_id")
    matches = [row for row in runs if isinstance(row, dict) and row.get("workflow_id") == workflow_id]
    _require(len(matches) == 1, "workflow_id_missing_or_ambiguous")
    run = matches[0]
    _require(run.get("arm") in ("baseline", "envelope", "batch", "combined")
             and isinstance(run.get("case"), str)
             and workflow_id == f'{run["arm"]}.{run["case"]}', "workflow_identity_mismatch")
    matched_cases = [row for row in cases if isinstance(row, dict) and row.get("id") == run["case"]]
    _require(len(matched_cases) == 1, "case_missing_or_ambiguous")
    case = matched_cases[0]
    actor = case.get("actor")
    _require(isinstance(actor, dict) and actor.get("schema") ==
             "cairn.token-efficiency-actor-task/1" and actor.get("case") == run["case"]
             and isinstance(actor.get("task"), str) and actor["task"].strip(), "actor_shape")
    for key, value in (("actor_sha256", actor), ("setup_sha256", case.get("setup")),
                       ("acceptance_check_sha256", case.get("acceptance_check"))):
        _require(_sha(value) == case.get(key) == run.get(key), f"{key}_mismatch")
    intervention = run.get("intervention")
    _require((intervention is None or isinstance(intervention, dict))
             and _sha(intervention) == run.get("intervention_sha256"),
             "intervention_sha256_mismatch")
    arm_interventions = manifest.get("arm_interventions")
    if arm_interventions is not None:
        _require(isinstance(arm_interventions, dict)
                 and isinstance(arm_interventions.get(run["case"], {}), dict)
                 and arm_interventions.get(run["case"], {}).get(run["arm"]) == intervention,
                 "intervention_manifest_mismatch")
    _require(set(sources) == set(hashes), "source_set_mismatch")
    _require(isinstance(record_hashes, dict) and set(sources) == set(record_hashes),
             "source_record_set_mismatch")
    for name, source in sources.items():
        _require(isinstance(name, str) and isinstance(source, dict)
                 and isinstance(source.get("body"), str), "source_shape")
        _require(hashlib.sha256(source["body"].encode()).hexdigest() == hashes[name],
                 "source_hash_mismatch")
        _require(_sha(source) == record_hashes[name], "source_record_hash_mismatch")
    return {"schema": "cairn.ordinary-task-binding/1", "workflow_id": workflow_id,
            "case": run["case"], "arm": run["arm"],
            "fixture_sha256": manifest["fixture_sha256"],
            "actor_sha256": run["actor_sha256"],
            "setup_sha256": run["setup_sha256"],
            "acceptance_check_sha256": run["acceptance_check_sha256"],
            "intervention_sha256": run["intervention_sha256"],
            "actor_task_sha256": hashlib.sha256(actor["task"].encode()).hexdigest(),
            "source_body_sha256": dict(sorted(hashes.items())),
            "source_record_sha256": dict(sorted(record_hashes.items()))}


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "native_stream_duplicate_json_key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("native_stream_nonfinite_number")


def _usage(value):
    _require(isinstance(value, dict), "native_usage_missing")
    result = {}
    for field in USAGE_FIELDS:
        count = value.get(field)
        _require(type(count) is int and 0 <= count <= MAX_TOKEN_FIELD, "native_usage_unbounded")
        result[field] = count
    return result


def _native_stream_projection(lines, exit_code):
    """Parse bytes observed in-process from one native Claude subprocess.

    This parser alone is not an attestation. It must not become an API that
    accepts externally replayed stream data as a completed workflow.
    """
    _require(type(exit_code) is int and exit_code == 0, "native_nonzero_exit")
    seen = {}
    terminal = None
    model = None
    size = 0
    events = 0
    for raw in lines:
        _require(isinstance(raw, (str, bytes)), "native_stream_line_shape")
        encoded = raw.encode() if isinstance(raw, str) else raw
        size += len(encoded)
        events += 1
        _require(size <= MAX_STREAM_BYTES and events <= MAX_EVENTS, "native_stream_unbounded")
        _require(encoded.endswith(b"\n"), "native_stream_partial_line")
        try:
            event = json.loads(encoded, object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("native_stream_invalid_json") from error
        _require(isinstance(event, dict) and isinstance(event.get("type"), str),
                 "native_stream_event_shape")
        _require(terminal is None, "native_events_after_terminal")
        typ = event["type"]
        if typ == "assistant":
            message = event.get("message")
            _require(isinstance(message, dict), "native_assistant_shape")
            mid = message.get("id")
            resolved = message.get("model")
            _require(isinstance(mid, str) and 1 <= len(mid) <= 256, "native_response_id_missing")
            _require(isinstance(resolved, str) and 1 <= len(resolved) <= 128,
                     "native_model_missing")
            _require(model is None or model == resolved, "native_model_changed")
            model = resolved
            _require(mid in seen or len(seen) < MAX_RESPONSES, "native_response_limit")
            seen[mid] = resolved  # repeated stream blocks for one message are normal
        elif typ == "result":
            _require(event.get("is_error") is False, "native_result_error")
            _require(isinstance(event.get("result"), str) and event["result"].strip(),
                     "native_answer_missing")
            _require(type(event.get("num_turns")) is int and 0 < event["num_turns"] <= MAX_RESPONSES,
                     "native_turn_count_invalid")
            terminal = _usage(event.get("usage"))
    _require(terminal is not None, "native_terminal_missing")
    _require(seen and model is not None, "native_response_missing")
    return {"model": model, "response_refs": [_response_ref(mid) for mid in seen],
            "observed_responses": len(seen), "usage": terminal,
            "total_tokens": sum(terminal.values()), "stream_bytes": size,
            "stream_events": events}


def _response_ref(mid):
    # Match audit_model_overhead.response_identity followed by
    # workflow_model_report.opaque("response\0" + identity).
    identity = hashlib.sha256(("provider-response/1\0" + mid).encode()).hexdigest()
    return hashlib.sha256(("cairn.workflow/1\0response\0" + identity).encode()).hexdigest()[:24]


def _candidate_receipt(binding, lines, exit_code):
    """Join selected binding and parsed stream inside a future trusted runner.

    This has no standalone evidence status; only a runner-owned launch can
    attest that these lines and exit code came from the selected subprocess.
    """
    _require(isinstance(binding, dict) and binding.get("schema") ==
             "cairn.ordinary-task-binding/1", "binding_schema")
    return {"schema": "cairn.ordinary-task-native-projection/1",
            "binding": binding, "native": _native_stream_projection(lines, exit_code),
            "evidence_status": "parser_only_unjoined"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--workflow-id", required=True)
    args = parser.parse_args()
    binding = frozen_binding(json.loads(args.manifest.read_text()), args.workflow_id)
    # There is deliberately no --stream/--response/--completed input. A future
    # owner-configured runner must own account selection, native launch, budget,
    # effective pins, and receipt publication in one process.
    raise SystemExit(f'{binding["workflow_id"]}: native_launch_not_configured')


if __name__ == "__main__":
    main()
