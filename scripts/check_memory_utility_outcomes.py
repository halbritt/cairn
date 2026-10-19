#!/usr/bin/env python3
"""Deterministic evaluator, arm-masked packet analyzer, and build/defer decision helper
for CAIRN-156, CAIRN-158, and CAIRN-159.

Preserves the immutable 2/72 consumed pre-repair harness-error prefix
(slot 1: rhumb-changelog.none.s0, slot 2: rhumb-changelog.direct.s0), validates
slot 3..72 ledger accounting without replay, emits blinded arm-masked outcome
packets (arm_masked_id), computes paired outcome contrasts (task success,
harmful mistakes avoided, token/turn overhead), and generates a structured
DEFER / BUILD decision recommendation with explicit provenance separating
offline/deterministic validation from live provider execution paused by the
owner cost gate on 2026-10-16.
"""
from pathlib import Path
import argparse
import hashlib
import hmac
import json
import re
import sys

ARMS = ("none", "direct", "current")
INCIDENT_CASES = (
    "rhumb-changelog",
    "pgpassfile-guard",
    "herdr-update-verify",
    "systemd-exit-check",
    "pgrep-wait",
    "stale-supercov",
)
CONTROL_CASES = (
    "unsup-rhumb-first-picker",
    "unsup-pgpass-port",
    "unsup-herdr-restart",
    "unsup-systemd-reload",
    "unsup-pgrep-pattern",
    "superseded-supercov-flag",
)
FROZEN_CASES = INCIDENT_CASES + CONTROL_CASES
PREDECESSOR_LEDGER_SHA256 = (
    "ac621601384aa2d9a90b83e2ff6a3e17f913a2fef12e2747567062696c8e2454"
)
CONSUMED_PREFIX_SLOTS = {
    1: "rhumb-changelog.none.s0",
    2: "rhumb-changelog.direct.s0",
}
USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_creation_tokens",
    "cost_usd",
    "duration_ms",
    "turns",
)
MEMORY_TOOL_RE = re.compile(r"(?i)(^mcp__cairn|cairn_(search|pull|remember|history|assess)|memory)")
MEMORY_WORDS_RE = re.compile(
    r"(?i)\b(memor(y|ies)|saved notes?|cairn|recall(ed)?|(the|a) note (says|records)|according to (the|my) notes?)\b"
)
CONTROL_TEXT_RE = re.compile(
    r"(?i)(Root denied this exact pending request\.|\b(root|host)\b.{0,40}\b(denied|declined|approv\w*)\b)"
)
CONTROL_PLACEHOLDER = "[tool call not executed]"


class UtilityEvaluationError(ValueError):
    pass


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def build_frozen_schedule(seed: str = "cairn-156") -> list[dict]:
    """Deterministic 72-slot schedule for the 12 frozen cases x 3 arms x 2 replicates."""
    import random

    rows = []
    admission = 1
    for replicate in (0, 1):
        for case_id in FROZEN_CASES:
            if case_id == "rhumb-changelog" and replicate == 0:
                arms_order = ["none", "direct", "current"]
            else:
                arms_order = list(ARMS)
                rng = random.Random(f"{seed}:{case_id}:{replicate}")
                rng.shuffle(arms_order)
            for order, arm in enumerate(arms_order):
                rows.append(
                    {
                        "admission": admission,
                        "case": case_id,
                        "replicate": replicate,
                        "arm": arm,
                        "order": order,
                        "run_id": f"{case_id}.{arm}.s{replicate}",
                        "wording": "task",
                        "native_smoke": admission == 1,
                    }
                )
                admission += 1
    return rows


def validate_schedule(rows: list[dict]) -> dict[int, dict]:
    if len(rows) != 72:
        raise UtilityEvaluationError(f"schedule must have 72 rows, got {len(rows)}")
    if [r.get("admission") for r in rows] != list(range(1, 73)):
        raise UtilityEvaluationError("schedule admissions must be contiguous 1..72")
    if len({r.get("run_id") for r in rows}) != 72:
        raise UtilityEvaluationError("schedule contains duplicate run_id")
    for slot, expected_run_id in CONSUMED_PREFIX_SLOTS.items():
        if rows[slot - 1].get("run_id") != expected_run_id:
            raise UtilityEvaluationError(f"slot {slot} must be {expected_run_id}")
    if rows[2].get("run_id") != "rhumb-changelog.current.s0":
        raise UtilityEvaluationError("slot 3 must be rhumb-changelog.current.s0")
    return {r["admission"]: r for r in rows}


def validate_ledger_events(
    events: list[dict],
    schedule_by_slot: dict[int, dict],
    *,
    predecessor_bytes: bytes | None = None,
    successor_bytes: bytes | None = None,
    expected_predecessor_sha256: str | None = None,
) -> dict[int, dict]:
    """Validate consumed 2/72 prefix preservation and slot 3..72 accounting without replay."""
    if predecessor_bytes is not None and successor_bytes is not None:
        actual_sha = sha256_bytes(predecessor_bytes)
        if expected_predecessor_sha256 and actual_sha != expected_predecessor_sha256:
            raise UtilityEvaluationError(
                f"predecessor sha256 {actual_sha} != {expected_predecessor_sha256}"
            )
        if not successor_bytes.startswith(predecessor_bytes):
            raise UtilityEvaluationError(
                "successor ledger does not preserve predecessor prefix byte-for-byte"
            )
    slots: dict[int, dict] = {}
    seen_runs: dict[str, int] = {}
    last_admitted: int | None = None
    for idx, ev in enumerate(events, start=1):
        ev_type = ev.get("type")
        if ev_type == "admitted":
            row = ev.get("row") or {}
            slot = row.get("admission")
            if slot not in schedule_by_slot or row != schedule_by_slot[slot]:
                raise UtilityEvaluationError(f"event {idx}: schedule mismatch for slot {slot}")
            if slot in slots or row["run_id"] in seen_runs:
                raise UtilityEvaluationError(f"event {idx}: replay or duplicate admission of slot {slot}")
            if last_admitted is not None and slots[last_admitted]["finished"] is None:
                raise UtilityEvaluationError(
                    f"event {idx}: unresolved prior admission {last_admitted}; replay forbidden"
                )
            if slot != len(slots) + 1:
                raise UtilityEvaluationError(f"event {idx}: out-of-order admission {slot}")
            seen_runs[row["run_id"]] = slot
            slots[slot] = {"admitted": ev, "finished": None}
            last_admitted = slot
        elif ev_type == "finished":
            slot = ev.get("admission")
            if slot not in slots or slots[slot]["finished"] is not None or slot != last_admitted:
                raise UtilityEvaluationError(f"event {idx}: invalid or duplicate finish for slot {slot}")
            rec = ev.get("record") or {}
            if rec.get("run_id") != schedule_by_slot[slot]["run_id"]:
                raise UtilityEvaluationError(f"event {idx}: run_id mismatch on finished slot {slot}")
            slots[slot]["finished"] = ev
        else:
            raise UtilityEvaluationError(f"event {idx}: unsupported event type {ev_type!r}")
    for slot, expected_run_id in CONSUMED_PREFIX_SLOTS.items():
        if slot in slots and slots[slot]["finished"] is not None:
            rec = slots[slot]["finished"]["record"]
            if rec.get("outcome") != "harness_error" or rec.get("execution_failure") != "native_permission_denial":
                raise UtilityEvaluationError(
                    f"consumed prefix slot {slot} ({expected_run_id}) must remain an unscored pre-repair harness_error"
                )
    return slots


def nullable_usage(record: dict) -> dict:
    trace = record.get("trace") if isinstance(record.get("trace"), dict) else {}
    out = {}
    for k in USAGE_FIELDS:
        val = trace.get(k)
        out[k] = val if isinstance(val, (int, float)) and not isinstance(val, bool) else None
    return out


def derive_arm_masked_id(run_id: str, salt: str = "cairn-156-blind-mask") -> str:
    digest = hmac.new(salt.encode("utf-8"), run_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"pkt-{digest[:12]}"


def mask_text(text: str | None, run_id: str, note_ids: tuple[str, ...] = ()) -> str | None:
    if text is None:
        return None
    masked = CONTROL_TEXT_RE.sub(CONTROL_PLACEHOLDER, str(text))
    masked = re.sub(re.escape(run_id), "<RUN-ID>", masked)
    masked = re.sub(r"\.(none|direct|current)\.s\d+", ".<ARM>.<REP>", masked)
    masked = re.sub(r"\b(none|direct|current)\s+arm\b", "<ARM> arm", masked, flags=re.IGNORECASE)
    for note_id in note_ids:
        if note_id:
            masked = re.sub(re.escape(note_id), "<NOTE-ID>", masked)
    return masked


def build_arm_masked_packet(
    slot: int,
    schedule_row: dict,
    record: dict,
    *,
    salt: str = "cairn-156-blind-mask",
    note_ids: tuple[str, ...] = (),
) -> tuple[dict, dict]:
    """Produce a blinded arm-masked packet and a separate root-only unblinding entry."""
    run_id = schedule_row["run_id"]
    arm_masked_id = derive_arm_masked_id(run_id, salt=salt)
    raw_actions = record.get("actions") or []
    masked_actions = []
    removed_memory_calls = 0
    replaced_control_results = 0
    for act in raw_actions:
        tool_name = str(act.get("tool") or "")
        if MEMORY_TOOL_RE.search(tool_name):
            removed_memory_calls += 1
            continue
        raw_res = act.get("result")
        if isinstance(raw_res, str) and CONTROL_TEXT_RE.search(raw_res):
            replaced_control_results += 1
            res_text = CONTROL_PLACEHOLDER
        else:
            res_text = mask_text(raw_res, run_id, note_ids) if isinstance(raw_res, str) else raw_res
        masked_actions.append(
            {
                "tool": tool_name,
                "input": mask_text(json.dumps(act.get("input")), run_id, note_ids)
                if act.get("input") is not None
                else None,
                "result": res_text,
                "result_is_error": bool(act.get("result_is_error", False)),
            }
        )
    answer = mask_text(record.get("answer"), run_id, note_ids)
    diff = mask_text(record.get("diff"), run_id, note_ids)
    root_flags = []
    if removed_memory_calls:
        root_flags.append(f"memory_tool_calls_removed:{removed_memory_calls}")
    if replaced_control_results:
        root_flags.append(f"control_text_results_replaced:{replaced_control_results}")
    if isinstance(record.get("answer"), str) and MEMORY_WORDS_RE.search(record["answer"]):
        root_flags.append("answer_mentions_memory_or_notes")
    packet = {
        "schema": "cairn.utility-masked-packet/1",
        "arm_masked_id": arm_masked_id,
        "case": schedule_row["case"],
        "cohort": "incident" if schedule_row["case"] in INCIDENT_CASES else "control",
        "task_wording": record.get("task_wording", "task"),
        "final_answer": answer,
        "workspace_diff": diff,
        "actions": masked_actions,
        "automatic_outcome": record.get("outcome"),
        "check_outcome": record.get("check_outcome"),
        "review": {
            "reviewed_grade": None,
            "reviewed_completion": None,
            "reviewed_harm": None,
        },
    }
    root_entry = {
        "arm_masked_id": arm_masked_id,
        "slot": slot,
        "run_id": run_id,
        "case": schedule_row["case"],
        "replicate": schedule_row["replicate"],
        "arm": schedule_row["arm"],
        "masking_flags": root_flags,
    }
    return packet, root_entry


def epoch_label(admitted_event: dict, record: dict) -> str:
    epoch = admitted_event.get("permission_epoch") or "unrecorded"
    regime = "host_approval" if "native_approval" in record else "no_host_approval"
    return f"{epoch}|{regime}"


def is_scorable_record(slot: int, record: dict) -> tuple[bool, str | None]:
    if slot in CONSUMED_PREFIX_SLOTS:
        return False, "consumed_pre_repair_prefix"
    failures = list(record.get("execution_failures") or [])
    if record.get("execution_failure") and record["execution_failure"] not in failures:
        failures.append(record["execution_failure"])
    if failures or record.get("outcome") in ("harness_error", "provider_error", None):
        return False, f"harness_error:{','.join(sorted(failures)) or 'unscored'}"
    approval = record.get("native_approval")
    if isinstance(approval, dict):
        if approval.get("schema") != "cairn.native-approval-summary/1":
            return False, "approval_observation_unknown"
        if approval.get("failed"):
            return False, f"approval_failed:{approval['failed']}"
        if approval.get("pending_requests", 0) > 0:
            return False, "approval_pending_at_completion"
    return True, None


def compute_paired_contrasts(
    schedule_by_slot: dict[int, dict],
    slots: dict[int, dict],
) -> list[dict]:
    by_cell: dict[tuple[str, int], dict[str, dict]] = {}
    for slot, srow in schedule_by_slot.items():
        entry = slots.get(slot)
        if entry is None or entry.get("finished") is None:
            cell = {
                "slot": slot,
                "usable": False,
                "reason": "not_admitted" if entry is None else "admitted_not_finished",
                "outcome": None,
                "usage": None,
                "epoch_label": None,
            }
        else:
            rec = entry["finished"]["record"]
            ok, reason = is_scorable_record(slot, rec)
            cell = {
                "slot": slot,
                "usable": ok,
                "reason": reason,
                "outcome": rec.get("outcome"),
                "usage": nullable_usage(rec),
                "epoch_label": epoch_label(entry["admitted"], rec),
            }
        by_cell.setdefault((srow["case"], srow["replicate"]), {})[srow["arm"]] = cell

    pairs = []
    for (case_id, rep), arms in sorted(by_cell.items()):
        cohort = "incident" if case_id in INCIDENT_CASES else "control"
        contrasts = {}
        for arm_a, arm_b in (("direct", "none"), ("current", "none"), ("direct", "current")):
            ca, cb = arms[arm_a], arms[arm_b]
            usable = ca["usable"] and cb["usable"]
            unusable_because = None
            if not usable:
                unusable_because = {
                    k: arms[k]["reason"] for k in (arm_a, arm_b) if not arms[k]["usable"]
                }
            elif ca["epoch_label"] != cb["epoch_label"]:
                usable = False
                unusable_because = {
                    "mixed_epoch": {arm_a: ca["epoch_label"], arm_b: cb["epoch_label"]}
                }
            success_delta = None
            harm_avoided_delta = None
            overhead = {"turns_delta": None, "output_tokens_delta": None, "cost_usd_delta": None}
            if usable:
                sa = 1 if ca["outcome"] == "correct" else 0
                sb = 1 if cb["outcome"] == "correct" else 0
                ha = 1 if ca["outcome"] == "mistake" else 0
                hb = 1 if cb["outcome"] == "mistake" else 0
                success_delta = sa - sb
                harm_avoided_delta = hb - ha
                ua, ub = ca["usage"] or {}, cb["usage"] or {}
                for field, key in (
                    ("turns", "turns_delta"),
                    ("output_tokens", "output_tokens_delta"),
                    ("cost_usd", "cost_usd_delta"),
                ):
                    if ua.get(field) is not None and ub.get(field) is not None:
                        overhead[key] = round(ua[field] - ub[field], 6)
            contrasts[f"{arm_a}-{arm_b}"] = {
                "usable": usable,
                "unusable_because": unusable_because,
                "outcomes": {arm_a: ca["outcome"], arm_b: cb["outcome"]},
                "task_success_delta": success_delta,
                "harmful_mistakes_avoided_delta": harm_avoided_delta,
                "overhead": overhead,
            }
        pairs.append({"case": case_id, "cohort": cohort, "replicate": rep, "contrasts": contrasts})
    return pairs


def evaluate_build_or_defer_decision(
    pairs: list[dict],
    *,
    provenance_mode: str = "offline_deterministic_validation",
    paused_at: str = "2026-10-16",
    consumed_slots: int = 2,
) -> dict:
    """Evaluate paired contrasts under PROTOCOL-DRAFT.md build-or-defer rules."""
    if provenance_mode not in ("offline_deterministic_validation", "live_provider_complete"):
        raise UtilityEvaluationError(f"unrecognized provenance_mode: {provenance_mode!r}")

    incident_pairs = [p for p in pairs if p["cohort"] == "incident"]
    control_pairs = [p for p in pairs if p["cohort"] == "control"]

    def summarize_contrast(subset: list[dict], key: str) -> dict:
        usable_rows = [p["contrasts"][key] for p in subset if p["contrasts"][key]["usable"]]
        wins = sum(1 for r in usable_rows if (r["task_success_delta"] or 0) > 0)
        losses = sum(1 for r in usable_rows if (r["task_success_delta"] or 0) < 0)
        ties = sum(1 for r in usable_rows if r["task_success_delta"] == 0)
        harm_avoided = sum(r["harmful_mistakes_avoided_delta"] or 0 for r in usable_rows)
        turns_deltas = [r["overhead"]["turns_delta"] for r in usable_rows if r["overhead"]["turns_delta"] is not None]
        mean_turns_delta = round(sum(turns_deltas) / len(turns_deltas), 4) if turns_deltas else None
        return {
            "usable_pairs": len(usable_rows),
            "total_pairs": len(subset),
            "wins": wins,
            "ties": ties,
            "losses": losses,
            "net_harmful_mistakes_avoided": harm_avoided,
            "mean_turns_delta": mean_turns_delta,
        }

    inc_direct_none = summarize_contrast(incident_pairs, "direct-none")
    inc_current_none = summarize_contrast(incident_pairs, "current-none")
    inc_direct_current = summarize_contrast(incident_pairs, "direct-current")
    ctrl_current_none = summarize_contrast(control_pairs, "current-none")

    control_regression = (
        ctrl_current_none["losses"] > 0 or ctrl_current_none["net_harmful_mistakes_avoided"] < 0
    )
    excess_overhead = (
        inc_current_none["mean_turns_delta"] is not None and inc_current_none["mean_turns_delta"] > 8.0
    )

    if provenance_mode != "live_provider_complete":
        decision = "DEFER"
        rationale = (
            f"Live provider campaign paused by owner cost gate on {paused_at} with "
            f"{consumed_slots}/72 pre-repair slots consumed and unscored; offline/deterministic "
            "validation verifies harness, ledger, masking, and contrast mechanics but does not "
            "substitute for live task outcomes. Defer broad ranking/retrieval expansion."
        )
    elif control_regression:
        decision = "DEFER"
        rationale = "Reproducible harmful control regression blocks promoting retrieval changes."
    elif inc_direct_none["usable_pairs"] == 0 or inc_direct_none["wins"] <= inc_direct_none["losses"]:
        decision = "DEFER"
        rationale = "Direct memory supply shows no net task-completion advantage over none."
    elif inc_direct_current["wins"] <= inc_direct_current["losses"]:
        decision = "DEFER"
        rationale = "Current Cairn retrieval already matches or exceeds direct-note control; defer new retrieval features."
    elif excess_overhead:
        decision = "DEFER"
        rationale = "Excess turn/token overhead on current retrieval exceeds budget threshold."
    else:
        decision = "BUILD"
        rationale = (
            "Direct memory improves incident task completion while current retrieval leaves a "
            "measurable delivery gap without control regression or excess turn overhead."
        )

    return {
        "schema": "cairn.utility-build-or-defer-decision/1",
        "issues": ["CAIRN-156", "CAIRN-157", "CAIRN-158", "CAIRN-159"],
        "decision": decision,
        "rationale": rationale,
        "provenance": {
            "mode": provenance_mode,
            "live_provider_paused_at": paused_at,
            "consumed_pre_repair_slots": consumed_slots,
            "remaining_unrun_live_slots": 72 - consumed_slots,
            "predecessor_ledger_sha256": PREDECESSOR_LEDGER_SHA256,
        },
        "incident_contrasts": {
            "direct-none": inc_direct_none,
            "current-none": inc_current_none,
            "direct-current": inc_direct_current,
        },
        "control_contrasts": {
            "current-none": ctrl_current_none,
        },
        "gates": {
            "control_regression_observed": control_regression,
            "excess_overhead_observed": excess_overhead,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", help="Run deterministic self-check")
    args = parser.parse_args(argv)
    rows = build_frozen_schedule()
    schedule_by_slot = validate_schedule(rows)
    pairs = compute_paired_contrasts(schedule_by_slot, {})
    report = evaluate_build_or_defer_decision(pairs)
    if args.self_check and report["decision"] != "DEFER":
        raise UtilityEvaluationError("expected DEFER for paused live campaign")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
