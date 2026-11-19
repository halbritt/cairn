#!/usr/bin/env python3
"""Compile a payload-free coordinator load report over Codex and Claude transcripts.

Classifies completed turns by trigger (inbox_wakeup, goal_continuation, owner),
wakeup turns by outcome (acknowledge_only, decision_or_action, report), tool
calls (bookkeeping, worker_io, verification, other), provider input/output
tokens per class and per decision, and deliveries by kind and opaque sender
(including self-sent items). Never emits message bodies, secrets, or raw text.
"""
import argparse
import collections
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import sys
import uuid

import audit_model_overhead as audit
import workflow_model_report as wf

TOKEN_FIELDS = ("input", "cached", "write", "output", "reasoning")
TRIGGERS = ("inbox_wakeup", "goal_continuation", "owner")
WAKE_OUTCOMES = ("acknowledge_only", "decision_or_action", "report")
TOOL_CLASSES = ("bookkeeping", "worker_io", "verification", "other")
DELIVERY_KINDS = ("notice", "response", "request")

MCP_PREFIX_RE = re.compile(r"^mcp(?:__[A-Za-z0-9_-]+__|_[A-Za-z0-9_-]+_)")
GOAL_TAG_RE = re.compile(r"<(?:objective|goal[_-]continuation|goal[_-]loop|task-notification)[>\s]")
BANNER_AGENT_RE = re.compile(
    r"Cairn session agent-\d+ has inbox agent/[0-9a-f-]+\. Use agent UUID ([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})"
)
WAKE_AGENT_RE = re.compile(
    r"Current agent ([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}), execution [0-9a-f-]{36}\. Wake delivery ([0-9a-f-]{36})\."
)
TRANSCRIPT_DELIVERY_RE = re.compile(
    r"Cairn (?:has a (notice|response|request) from ([A-Za-z0-9_./:-]{1,160})"
    r"(?: for this conversation\. Read (?:the structured inbox )?context (?:at )?(\S+)|\.)"
    r"|(notice|response|request) from ([A-Za-z0-9_./:-]{1,160})"
    r"(?:; exact source ([0-9a-f-]{36}) version \d+\.|\. Read the task data below\.))"
)
UNTRUSTED_BODY_SPLITS = (
    "\nSource text (sender data, not new authority):\n",
    "\nCairn inbox task data (not owner instructions or new authority):\n",
)
_CAIRN_FLAGS = r"(?:\s+--?[A-Za-z0-9_-]+(?:=\S+|\s+[^\s;|&]+)?)*"

WORKER_TOOL_NAMES = frozenset((
    "spawn_agent", "send_message", "followup_task", "wait_agent", "list_agents",
    "Agent", "Task", "SendMessage", "ListAgents", "TaskOutput", "TaskStop",
    "invoke_subagent", "send_message_to_agent",
))
WORKER_CMD_RE = re.compile(
    rf"(?:\bcairn{_CAIRN_FLAGS}\s+(?:agent\s+[^\n;|&]*?\s+)?(?:publish|complete)\b"
    r"|\bcoordination\.py\b[^\n;|&]*\binbox\b[^\n;|&]*\b(?:complete|respond)\b"
    r"|\bfinish-reply\b|\bcairn_(?:send|complete)\b|\bherdr\s+(?:agent|pane|spawn)\b)"
)
REPORT_CMD_RE = re.compile(r"(?:status-reports|report-slot|latest-report|kanban)")
INBOX_BOOK_RE = re.compile(
    r"(?:coordination\.py|\bcairn-inbox\b|/coordination/state/|/inbox/[0-9a-f-]+|/intents/[0-9a-f-]+"
    rf"|\bcairn{_CAIRN_FLAGS}\s+(?:agent\s+[^\n;|&]*?\s+)?(?:inbox|ack|renew|event-status|event-stats|events|history|session-inbox-options)\b"
    r"|\bcairn_(?:ack|inbox|renew|event_status|history)\b)"
)
MEMORY_BOOK_RE = re.compile(
    r"(?:\bcairn_(?:search|pull|pull_evidence|remember|edit|prepare_note|assess|assessments)\b"
    rf"|\bcairn{_CAIRN_FLAGS}\s+agent\s+[^\n;|&]*?\b(?:search|pull|remember|edit)\b)"
)
AGENT_CTX_BOOK_RE = re.compile(rf"(?:\bcairn{_CAIRN_FLAGS}\s+agents\b|\bcairn_agents\b)")
TRACKER_BOOK_RE = re.compile(r"(?:\bplane\s+(?:issue|comment|module|cycle|project)\b|\bplane_[A-Za-z0-9_]+\b)")
STATE_BOOK_RE = re.compile(r"(?:cairn-hybrid-execution|\.cache/cairn|watchdog/config\.json|/board/)")
VERIFICATION_RE = re.compile(
    r"(?:\bgit\s+(?:-C\s+\S+\s+)?(?:status|diff|log|show|rev-parse|branch|worktree|check|cat-file|fetch|merge-base)\b"
    r"|\bgo\s+(?:test|vet|build)\b|\bcargo\s+(?:test|check|clippy|build)\b"
    r"|\bpytest\b|\bunittest\b|\bmake\s+(?:check|test|test-integration|verify)\b"
    r"|\bmise\s+run\b|\bsha256sum\b|\bverify_docs\.py\b|\bdiff\s+-u\b)"
)
ACTION_EDIT_TOOLS = frozenset((
    "Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch", "write_file", "edit_file",
))


def canonical_uuid(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = str(uuid.UUID(value))
    except ValueError:
        return None
    return parsed if parsed == value else None


def parse_window_timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.timestamp()


def _record_timestamp(value, errors):
    if not isinstance(value, str) or not value:
        return None
    try:
        return audit.timestamp(value)
    except ValueError:
        errors["invalid_timestamps"] += 1
        return None


def _is_wake_text(text):
    stripped = text.lstrip()
    return stripped.startswith(audit.WAKE) or (stripped.startswith("<channel") and audit.WAKE in stripped)


def _claude_prompt_text(content, record):
    if record.get("toolUseResult"):
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        has_prompt_block = False
        for item in content:
            if isinstance(item, str):
                has_prompt_block = True
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") != "tool_result":
                has_prompt_block = True
                parts.append(audit.text_of(item))
        return "\n".join(parts) if has_prompt_block else None
    return None


def share(part, whole):
    if not whole:
        return None
    return round(part / whole, 6)


def _normalize_tool_name(name):
    if not isinstance(name, str):
        return ""
    return MCP_PREFIX_RE.sub("", name)


def classify_tool_call(name, raw_args):
    norm_name = _normalize_tool_name(name)
    args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args, separators=(",", ":"))
    combined = norm_name + " " + args_str
    if norm_name in WORKER_TOOL_NAMES or WORKER_CMD_RE.search(combined):
        return "worker_io", "worker_dispatch"
    if REPORT_CMD_RE.search(combined):
        return "bookkeeping", "report"
    if INBOX_BOOK_RE.search(combined):
        return "bookkeeping", "inbox_lifecycle"
    if MEMORY_BOOK_RE.search(combined):
        return "bookkeeping", "memory_notes"
    if AGENT_CTX_BOOK_RE.search(combined):
        return "bookkeeping", "agent_context"
    if TRACKER_BOOK_RE.search(combined):
        return "bookkeeping", "tracker"
    if VERIFICATION_RE.search(combined):
        return "verification", "git_or_test"
    if STATE_BOOK_RE.search(combined):
        return "bookkeeping", "coordinator_state"
    if norm_name in ACTION_EDIT_TOOLS:
        return "other", "repo_edit"
    return "other", "general"


def classify_wakeup_outcome(turn):
    cats = set(turn["tool_categories"])
    subs = set(turn["tool_subcategories"])
    if "worker_io" in cats or "verification" in cats or "repo_edit" in subs:
        return "decision_or_action"
    if "report" in subs:
        return "report"
    return "acknowledge_only"


def _blank_turn():
    return dict(
        completed=False,
        prompt_seen=False,
        wake=False,
        goal=False,
        start=None,
        end=None,
        usage=[],
        responses=[],
        tool_categories=[],
        tool_subcategories=[],
        transcript_deliveries=[],
        seen_delivery_keys=set(),
        wake_delivery_ids=[],
        compactions=0,
    )


def _control_portion(text):
    out = text
    for marker in UNTRUSTED_BODY_SPLITS:
        out = out.split(marker, 1)[0]
    return out


def _extract_agent_and_deliveries(text, observed_agents, turn):
    ctrl = _control_portion(text)
    for match in BANNER_AGENT_RE.finditer(ctrl):
        observed_agents[match.group(1)] += 1
    for match in WAKE_AGENT_RE.finditer(ctrl):
        observed_agents[match.group(1)] += 1
        if match.group(2) not in turn["wake_delivery_ids"]:
            turn["wake_delivery_ids"].append(match.group(2))
    for match in TRANSCRIPT_DELIVERY_RE.finditer(ctrl):
        kind = match.group(1) or match.group(4)
        sender = (match.group(2) or match.group(5) or "").rstrip(".,;)")
        ident = match.group(3) or match.group(6) or (turn["wake_delivery_ids"][-1] if turn["wake_delivery_ids"] else "turn")
        dedup_key = (kind, sender, ident)
        if dedup_key not in turn["seen_delivery_keys"]:
            turn["seen_delivery_keys"].add(dedup_key)
            turn["transcript_deliveries"].append((kind, sender))


def _nonneg_int(val):
    return val if type(val) is int and val >= 0 else None


def parse_codex_transcript(path, since, until):
    turns = collections.defaultdict(_blank_turn)
    errors = collections.Counter()
    observed_agents = collections.Counter()
    seen_rid_turn = {}
    pre_window_turns = set()
    current = None
    for record in audit.records(path, errors):
        if not isinstance(record, dict):
            errors["non_object_records"] += 1
            continue
        raw_payload = record.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        rtype = record.get("type")
        ts = _record_timestamp(record.get("timestamp"), errors)
        if ts is None:
            continue
        raw_tid = payload.get("turn_id")
        tid = raw_tid if isinstance(raw_tid, str) and raw_tid else None
        if ts < since:
            if (rtype == "turn_context" or (rtype == "event_msg" and payload.get("type") == "task_started")) and tid:
                current = tid
                pre_window_turns.add(tid)
            elif tid:
                pre_window_turns.add(tid)
            elif current:
                pre_window_turns.add(current)
            continue
        if ts >= until:
            continue
        if (rtype == "turn_context" or (rtype == "event_msg" and payload.get("type") == "task_started")) and tid:
            current = tid
            if turns[current]["start"] is None and current not in pre_window_turns:
                turns[current]["start"] = ts
                turns[current]["prompt_seen"] = True
        if not current:
            errors["records_without_turn"] += 1
            continue
        turn = turns[current]
        if rtype == "token_usage_record":
            target_id = tid or current
            target = turns[target_id]
            rid = payload.get("response_id")
            if not isinstance(rid, str) or not rid:
                errors["usage_without_response_id"] += 1
                target["responses"].append(f"missing:codex:{errors['usage_without_response_id']}")
                continue
            rhash = audit.response_identity(rid)
            if rid in seen_rid_turn:
                errors["duplicate_usage_records"] += 1
                if seen_rid_turn[rid] != target_id:
                    target["responses"].append(rhash)
                continue
            seen_rid_turn[rid] = target_id
            raw_usage = payload.get("usage")
            usage = raw_usage if isinstance(raw_usage, dict) else {}
            target["responses"].append(rhash)
            target["usage"].append({
                "input": _nonneg_int(usage.get("input_tokens")),
                "cached": _nonneg_int(usage.get("cached_input_tokens")),
                "write": _nonneg_int(usage.get("cache_write_input_tokens")),
                "output": _nonneg_int(usage.get("output_tokens")),
                "reasoning": _nonneg_int(usage.get("reasoning_output_tokens")),
            })
        elif rtype == "event_msg" and payload.get("type") == "task_complete":
            target_id = tid or current
            target = turns[target_id]
            if target_id not in pre_window_turns and target["prompt_seen"]:
                target["completed"] = True
                target["end"] = ts
        elif rtype == "compacted":
            turn["compactions"] += 1
        elif rtype == "response_item":
            ptype = payload.get("type")
            if ptype in ("function_call", "custom_tool_call"):
                cat, sub = classify_tool_call(
                    payload.get("name", ""),
                    payload.get("arguments", payload.get("input", "")),
                )
                turn["tool_categories"].append(cat)
                turn["tool_subcategories"].append(sub)
            elif ptype == "message" and payload.get("role") in ("user", "developer", "system"):
                text = audit.text_of(payload.get("content"))
                ctrl = _control_portion(text)
                if _is_wake_text(ctrl):
                    turn["wake"] = True
                if GOAL_TAG_RE.search(ctrl):
                    turn["goal"] = True
                _extract_agent_and_deliveries(text, observed_agents, turn)
    return dict(harness="codex", turns=dict(turns), errors=dict(errors), observed_agents=observed_agents)


def _claude_hook_context_text(record):
    parts = []
    att = record.get("attachment")
    if isinstance(att, str):
        parts.append(att)
    elif isinstance(att, dict) and att.get("type") == "hook_additional_context":
        parts.append(audit.text_of(att.get("content")))
    hook_ctx = record.get("hookAdditionalContext")
    if hook_ctx:
        parts.append(audit.text_of(hook_ctx))
    return "\n".join(p for p in parts if p)


def parse_claude_transcript(path, since, until):
    turns = collections.defaultdict(_blank_turn)
    errors = collections.Counter()
    observed_agents = collections.Counter()
    responses = {}
    tool_ids = set()
    pre_window_prompts = set()
    current = None
    for record in audit.records(path, errors):
        if not isinstance(record, dict):
            errors["non_object_records"] += 1
            continue
        rtype = record.get("type")
        if rtype in ("api-request-blob", "api-request-shape", "api-request"):
            continue
        ts = _record_timestamp(record.get("timestamp"), errors)
        if ts is None:
            continue
        raw_pid = record.get("promptId")
        pid = raw_pid if isinstance(raw_pid, str) and raw_pid else None
        if ts < since:
            if rtype == "user" and pid:
                current = pid
                pre_window_prompts.add(pid)
            elif pid:
                pre_window_prompts.add(pid)
            elif current:
                pre_window_prompts.add(current)
            continue
        if ts >= until:
            continue
        raw_msg = record.get("message")
        msg = raw_msg if isinstance(raw_msg, dict) else {}
        if rtype == "user" and pid:
            current = pid
            turn = turns[current]
            text = _claude_prompt_text(msg.get("content"), record)
            if text is not None and current not in pre_window_prompts:
                if turn["start"] is None:
                    turn["start"] = ts
                turn["prompt_seen"] = True
                ctrl = _control_portion(text)
                if _is_wake_text(ctrl):
                    turn["wake"] = True
                origin = record.get("origin")
                origin_kind = origin.get("kind") if isinstance(origin, dict) else None
                if GOAL_TAG_RE.search(ctrl) or origin_kind in ("task-notification", "auto-continuation", "peer"):
                    turn["goal"] = True
                _extract_agent_and_deliveries(text, observed_agents, turn)
            continue
        if not current:
            errors["records_without_prompt"] += 1
            continue
        turn = turns[current]
        if rtype == "assistant":
            raw_mid = msg.get("id")
            mid = raw_mid if isinstance(raw_mid, str) and raw_mid else None
            raw_usage = msg.get("usage")
            usage = raw_usage if isinstance(raw_usage, dict) else None
            if mid:
                rhash = audit.response_identity(mid)
                if rhash not in turn["responses"]:
                    turn["responses"].append(rhash)
            elif usage is not None:
                errors["usage_without_response_id"] += 1
                turn["responses"].append(f"missing:claude:{errors['usage_without_response_id']}")
            if mid and usage is not None:
                in_parts = [_nonneg_int(usage.get(k)) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
                details = usage.get("output_tokens_details")
                details_dict = details if isinstance(details, dict) else {}
                responses[(current, mid)] = {
                    "input": sum(in_parts) if all(v is not None for v in in_parts) else None,
                    "cached": _nonneg_int(usage.get("cache_read_input_tokens")),
                    "write": _nonneg_int(usage.get("cache_creation_input_tokens")),
                    "output": _nonneg_int(usage.get("output_tokens")),
                    "reasoning": _nonneg_int(details_dict.get("thinking_tokens")),
                }
            content_blocks = msg.get("content")
            for block in content_blocks if isinstance(content_blocks, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    raw_tid = block.get("id")
                    tid = raw_tid if isinstance(raw_tid, str) and raw_tid else None
                    if tid and tid in tool_ids:
                        continue
                    if tid:
                        tool_ids.add(tid)
                    cat, sub = classify_tool_call(block.get("name", ""), block.get("input", {}))
                    turn["tool_categories"].append(cat)
                    turn["tool_subcategories"].append(sub)
        elif rtype == "system":
            sub = record.get("subtype")
            if sub == "turn_duration":
                if current not in pre_window_prompts and turn["prompt_seen"]:
                    turn["completed"] = True
                    turn["end"] = ts
            elif sub == "compact_boundary":
                turn["compactions"] += 1
        if rtype in ("attachment", "system"):
            extra = _claude_hook_context_text(record)
            if extra:
                ctrl = _control_portion(extra)
                if _is_wake_text(ctrl):
                    turn["wake"] = True
                if GOAL_TAG_RE.search(ctrl):
                    turn["goal"] = True
                _extract_agent_and_deliveries(extra, observed_agents, turn)
    for (turn_id, _mid), usage in responses.items():
        turns[turn_id]["usage"].append(usage)
    return dict(harness="claude", turns=dict(turns), errors=dict(errors), observed_agents=observed_agents)


def _sum_tokens(turns, duplicate_ids):
    has_dup = any(r in duplicate_ids for t in turns for r in t["responses"])
    out = {}
    for field in TOKEN_FIELDS:
        vals = [u[field] for t in turns for u in t["usage"] if type(u.get(field)) is int and u[field] >= 0]
        total_responses = sum(len(t["responses"]) for t in turns)
        complete = not has_dup and (total_responses == 0 or len(vals) == total_responses)
        out[field] = dict(
            total=sum(vals) if complete else None,
            known_sum=sum(vals),
            missing_records=max(0, total_responses - len(vals)),
        )
    return out


def _per_decision(tokens, decision_count):
    if decision_count <= 0:
        return {field: None for field in ("input", "output", "reasoning")}
    result = {}
    for field in ("input", "output", "reasoning"):
        total = tokens[field]["total"]
        result[field] = round(total / decision_count, 2) if total is not None else None
    return result


def _exact_share(part_tok, whole_tok):
    part_total = part_tok["total"]
    whole_total = whole_tok["total"]
    if part_total is None or whole_total is None:
        return None
    return share(part_total, whole_total)


def _read_text_redacted(path, role):
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"{role}: {exc.strerror or 'I/O error'}") from None


def load_state_deliveries(state_root, agent_id, since, until, max_deliveries=20000, errors=None):
    if state_root is None:
        return None
    if state_root.is_symlink():
        raise ValueError("coordination state root must not be a symlink")
    if not state_root.is_dir():
        raise ValueError("coordination state root must be an existing directory")
    if agent_id is None:
        raise ValueError("pass --agent-id when --coordination-state is set without a unique transcript agent UUID")
    inbox_dir = state_root / "inbox"
    direct = list(inbox_dir.glob("*.json")) if not inbox_dir.is_symlink() and inbox_dir.is_dir() else []
    nested = list(state_root.glob("*/inbox/*.json"))
    candidates = sorted(set(direct + nested))
    selected = []
    seen_deliveries = set()
    for path in candidates:
        if path.is_symlink() or path.parent.is_symlink() or path.parent.parent.is_symlink():
            continue
        try:
            mtime = path.stat().st_mtime
            if not (since <= mtime < until):
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if errors is not None:
                errors["corrupt_state_files"] += 1
            continue
        if not isinstance(doc, dict):
            if errors is not None:
                errors["corrupt_state_files"] += 1
            continue
        if doc.get("agent_id") != agent_id:
            continue
        raw_did = doc.get("delivery_id")
        did = raw_did if isinstance(raw_did, str) and raw_did else path.stem
        if did in seen_deliveries:
            continue
        kind = doc.get("kind")
        sender = doc.get("sender")
        if kind in DELIVERY_KINDS and isinstance(sender, str) and sender:
            seen_deliveries.add(did)
            selected.append((kind, sender))
            if len(selected) > max_deliveries:
                raise ValueError("coordination state exceeds max-deliveries")
    return selected


def summarize_deliveries(items, agent_id, evidence):
    self_inbox = f"agent/{agent_id}" if agent_id else None
    by_kind = {k: 0 for k in DELIVERY_KINDS}
    self_by_kind = {k: 0 for k in DELIVERY_KINDS}
    sender_counts = collections.defaultdict(lambda: {k: 0 for k in DELIVERY_KINDS})
    for kind, sender in items:
        if kind not in by_kind:
            continue
        by_kind[kind] += 1
        sender_counts[sender][kind] += 1
        if self_inbox and sender == self_inbox:
            self_by_kind[kind] += 1
    total = sum(by_kind.values())
    self_total = sum(self_by_kind.values())
    ext_total = total - self_total
    senders = []
    for sender, counts in sender_counts.items():
        stotal = sum(counts.values())
        senders.append(dict(
            sender_ref=wf.opaque("sender\0" + sender),
            self_sent=bool(self_inbox and sender == self_inbox),
            total=stotal,
            share=share(stotal, total),
            by_kind=dict(counts),
        ))
    senders.sort(key=lambda row: (-row["total"], row["sender_ref"]))
    return dict(
        evidence=evidence,
        total=total,
        by_kind=by_kind,
        self_sent=dict(total=self_total, share=share(self_total, total), by_kind=self_by_kind),
        external=dict(total=ext_total, share=share(ext_total, total)),
        by_sender=senders,
    )


EVENT_STATS_FIELDS = (
    "published", "deliveries", "pending", "leased", "expired_leases",
    "handled", "ignored", "failed", "redeliveries", "oldest_outstanding_seconds",
)


def read_event_stats(path):
    if path is None:
        return None
    raw = json.loads(_read_text_redacted(path, "event-stats"))
    if isinstance(raw, dict) and isinstance(raw.get("data"), dict):
        raw = raw["data"]
    if not isinstance(raw, dict):
        raise ValueError("event-stats must be a JSON object")
    out = {}
    for key in EVENT_STATS_FIELDS:
        val = raw.get(key)
        if type(val) in (int, float) and math.isfinite(val):
            out[key] = val
    return out


def compile_coordinator_load(sessions, *, agent_id=None, state_root=None,
                             since=0.0, until=0.0, event_stats=None, max_deliveries=20000):
    if agent_id is not None and canonical_uuid(agent_id) is None:
        raise ValueError("agent_id must be a canonical lowercase UUID")
    observed_agents = collections.Counter()
    for sess in sessions:
        observed_agents.update(sess.get("observed_agents") or {})
    resolved_agent = agent_id
    if resolved_agent is None and len(observed_agents) == 1:
        resolved_agent = next(iter(observed_agents))

    completed_turns = []
    incomplete_count = 0
    compactions = 0
    errors = collections.Counter()
    for sess in sessions:
        errors.update(sess.get("errors") or {})
        for turn in sess["turns"].values():
            compactions += turn["compactions"]
            if turn["completed"]:
                completed_turns.append(turn)
            else:
                incomplete_count += 1

    response_counts = collections.Counter(r for t in completed_turns for r in t["responses"])
    duplicate_ids = {r for r, count in response_counts.items() if count > 1}

    by_trigger_turns = {tr: [] for tr in TRIGGERS}
    for turn in completed_turns:
        trig = "inbox_wakeup" if turn["wake"] else ("goal_continuation" if turn["goal"] else "owner")
        by_trigger_turns[trig].append(turn)

    total_turns = len(completed_turns)
    all_tokens = _sum_tokens(completed_turns, duplicate_ids)
    trigger_summary = {}
    for trig in TRIGGERS:
        sub = by_trigger_turns[trig]
        tok = _sum_tokens(sub, duplicate_ids)
        trigger_summary[trig] = dict(
            turns=len(sub),
            share=share(len(sub), total_turns),
            input_token_share=_exact_share(tok["input"], all_tokens["input"]),
            output_token_share=_exact_share(tok["output"], all_tokens["output"]),
            tokens=tok,
        )

    wake_turns = by_trigger_turns["inbox_wakeup"]
    by_outcome = {o: [] for o in WAKE_OUTCOMES}
    for turn in wake_turns:
        by_outcome[classify_wakeup_outcome(turn)].append(turn)
    wake_total = len(wake_turns)
    wake_tokens = trigger_summary["inbox_wakeup"]["tokens"]
    outcome_summary = {}
    for outcome in WAKE_OUTCOMES:
        sub = by_outcome[outcome]
        outcome_summary[outcome] = dict(
            turns=len(sub),
            share_of_wakeups=share(len(sub), wake_total),
            share_of_all_turns=share(len(sub), total_turns),
            tokens=_sum_tokens(sub, duplicate_ids),
        )
    decisions = len(by_outcome["decision_or_action"])

    tool_counts = {c: 0 for c in TOOL_CLASSES}
    bookkeeping_sub = collections.Counter()
    for turn in completed_turns:
        for cat, sub in zip(turn["tool_categories"], turn["tool_subcategories"]):
            tool_counts[cat] += 1
            if cat == "bookkeeping":
                bookkeeping_sub[sub] += 1
    total_tools = sum(tool_counts.values())
    tool_summary = {
        cat: dict(calls=tool_counts[cat], share=share(tool_counts[cat], total_tools))
        for cat in TOOL_CLASSES
    }
    tool_summary["bookkeeping"]["subcategories"] = dict(sorted(bookkeeping_sub.items()))

    state_delivs = load_state_deliveries(
        state_root, resolved_agent, since, until, max_deliveries=max_deliveries, errors=errors
    )
    if state_delivs is not None:
        deliveries = summarize_deliveries(state_delivs, resolved_agent, "coordination_state")
    else:
        tx_delivs = [item for t in completed_turns for item in t["transcript_deliveries"]]
        deliveries = summarize_deliveries(tx_delivs, resolved_agent, "transcript")

    return dict(
        schema="cairn.coordinator-load-report/1",
        agent_ref=wf.opaque("agent\0" + resolved_agent) if resolved_agent else None,
        self_attribution_known=resolved_agent is not None,
        harnesses=sorted({sess["harness"] for sess in sessions}),
        turns=dict(
            completed=total_turns,
            incomplete=incomplete_count,
            compactions=compactions,
            tokens=all_tokens,
            by_trigger=trigger_summary,
        ),
        wakeup_outcomes=dict(
            total_wakeups=wake_total,
            acknowledge_only_share=share(len(by_outcome["acknowledge_only"]), wake_total),
            by_outcome=outcome_summary,
            tokens_per_decision=dict(
                decision_turns=decisions,
                wakeup_tokens_per_decision=_per_decision(wake_tokens, decisions),
                session_tokens_per_decision=_per_decision(all_tokens, decisions),
                decision_turn_avg_tokens=_per_decision(outcome_summary["decision_or_action"]["tokens"], decisions),
            ),
        ),
        tool_calls=dict(total=total_tools, by_class=tool_summary),
        deliveries=deliveries,
        event_stats=event_stats,
        parse_errors=dict(errors),
        limitations=[
            "Turn outcome and tool-call classes use deterministic command/tool-name heuristics without reading prose.",
            "Provider token counts are whole-turn response usage; duplicate response IDs null exact totals.",
            "Incomplete turns at window boundaries are counted in turns.incomplete and excluded from completed rollups.",
            "Self-sent attribution requires --agent-id or a single observed Cairn session banner in the transcript.",
            "Retained coordination-state inbox contexts are filtered by file mtime and agent_id.",
        ],
    )


def load_coordinator_sessions(manifest_items, since, until):
    if not isinstance(manifest_items, list) or not manifest_items:
        raise ValueError("manifest must be a nonempty list of session descriptors")
    sessions = []
    for idx, item in enumerate(manifest_items):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"]:
            raise ValueError("session descriptor must be an object with a nonempty path")
        harness = item.get("harness")
        if harness not in ("codex", "claude"):
            raise ValueError("coordinator load parser supports codex and claude only")
        path = Path(item["path"])
        try:
            parsed = (parse_codex_transcript if harness == "codex" else parse_claude_transcript)(path, since, until)
        except OSError as exc:
            raise ValueError(f"transcript[{idx}]: {exc.strerror or 'I/O error'}") from None
        sessions.append(parsed)
    return sessions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="inclusive window start (ISO-8601 with timezone)")
    parser.add_argument("--until", required=True, help="exclusive window end (ISO-8601 with timezone)")
    parser.add_argument("--manifest", type=Path, help="JSON list of {harness, path} descriptors")
    parser.add_argument("--transcript", type=Path, help="single transcript JSONL path")
    parser.add_argument("--harness", choices=("codex", "claude"), help="harness for --transcript")
    parser.add_argument("--agent-id", help="coordinator Cairn agent UUID (auto-detected if unique)")
    parser.add_argument("--coordination-state", type=Path, help="coordination state root or binding state dir")
    parser.add_argument("--event-stats", type=Path, help="optional JSON output from cairn event-stats")
    parser.add_argument("--max-deliveries", type=int, default=20000)
    parser.add_argument("--output", type=Path, help="optional private 0600 output JSON path")
    args = parser.parse_args(argv)

    since, until = parse_window_timestamp(args.since), parse_window_timestamp(args.until)
    if since is None or until is None or since >= until or args.max_deliveries < 1:
        parser.error("require valid timezone-aware ISO-8601 since < until and positive --max-deliveries")
    if (args.manifest is None) == (args.transcript is None):
        parser.error("pass exactly one of --manifest or --transcript")
    if args.transcript is not None and args.harness is None:
        parser.error("--transcript requires --harness codex|claude")
    if args.manifest is not None and args.harness is not None:
        parser.error("--harness applies only to --transcript")

    try:
        items = (json.loads(_read_text_redacted(args.manifest, "manifest")) if args.manifest is not None
                 else [dict(harness=args.harness, path=str(args.transcript))])
        sessions = load_coordinator_sessions(items, since, until)
        report = compile_coordinator_load(
            sessions,
            agent_id=args.agent_id,
            state_root=args.coordination_state,
            since=since,
            until=until,
            event_stats=read_event_stats(args.event_stats),
            max_deliveries=args.max_deliveries,
        )
        report["window"] = dict(since=args.since, until=args.until)
        rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output is not None:
            try:
                fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(rendered)
            except OSError as exc:
                raise ValueError(f"output: {exc.strerror or 'I/O error'}") from None
            print(json.dumps(dict(
                output=str(args.output),
                completed_turns=report["turns"]["completed"],
                deliveries=report["deliveries"]["total"],
                self_sent=report["deliveries"]["self_sent"]["total"],
            ), sort_keys=True, allow_nan=False))
            return 0
    except ValueError as exc:
        parser.exit(2, f"{parser.prog}: error: {exc}\n")
    sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
