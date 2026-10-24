#!/usr/bin/env python3
"""Read-only native-log audit. Emit metrics, never message bodies or credentials.

Provider usage is authoritative only for the entire response. Content counts use
o200k_base as a proxy, not an asserted tokenizer for these deployed models.
Claude Cairn-containing message counts are upper bounds for mixed messages.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

import tiktoken

ENC = tiktoken.get_encoding("o200k_base")
WAKE = "This is a new live turn from the configured Cairn automatic inbox wakeup."
MARKERS = ("Cairn session agent-", "Cairn has a ", "Cairn candidates/openings",
           '"schema":"cairn.', '"schema": "cairn.', "Cairn inbox:",
           "Cairn automatic inbox", "Cairn memory", "Cairn recall")


def timestamp(value):
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def counts(text):
    return {"bytes": len(text.encode()), "proxy_tokens": len(ENC.encode(text, disallowed_special=()))}


def text_of(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(text_of(v) for v in value)
    if isinstance(value, dict):
        return "\n".join(text_of(value[k]) for k in ("text", "content", "output") if k in value)
    return ""


def distribution(values):
    if not values:
        return {"n": 0}
    values = sorted(values)
    return {"n": len(values), "sum": sum(values), "min": values[0],
            "p50": values[math.ceil(len(values) * .5)-1],
            "p95": values[math.ceil(len(values) * .95)-1], "max": values[-1]}


def blank_turn():
    return dict(wake=False, kinds=[], completed=False, usage=[], tools=0,
                cairn_tools=0, memory_tools=0, compactions=0, contexts=[],
                start=None, end=None, models=[])


def tool_kind(name, body):
    if "cairn" not in (name + body).lower():
        return None
    if any(s in name + body for s in ("cairn_search", "cairn_pull", "cairn_remember", "cairn_edit", " agent ")):
        return "memory"
    if any(s in name + body for s in ("coordination.py", " inbox ", " publish ", " agents ")):
        return "coordination"
    return "other_cairn_mention"


def records(path, errors):
    # Snapshot the initial length: an actively appended partial final line is
    # counted explicitly and is never treated as a complete record.
    with path.open("rb") as stream:
        remaining = path.stat().st_size
        while remaining > 0:
            raw = stream.readline(remaining)
            if not raw:
                break
            remaining -= len(raw)
            if not raw.endswith(b"\n"):
                errors["partial_lines"] += 1
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                errors["invalid_json_lines"] += 1


def analyze_codex(path, since, until):
    turns = collections.defaultdict(blank_turn)
    errors = collections.Counter()
    current = None
    usage_ids = set()
    versions = set()
    recall_metrics = []
    for r in records(path, errors):
        p = r.get("payload", {})
        typ = r.get("type")
        if typ == "session_meta":
            versions.add(p.get("cli_version", "unknown"))
        ts = timestamp(r.get("timestamp"))
        if ts is None or not since <= ts < until:
            continue
        if typ == "turn_context":
            current = p.get("turn_id")
            if current:
                turns[current]["models"].append(p.get("model", "unknown"))
        if typ == "event_msg" and p.get("type") == "task_started":
            current = p.get("turn_id")
            if current:
                turns[current]["start"] = ts
        if not current:
            errors["records_without_turn"] += 1
            continue
        t = turns[current]
        if typ == "token_usage_record":
            rid = p.get("response_id")
            if not rid:
                errors["usage_without_response_id"] += 1
                continue
            if rid in usage_ids:
                errors["duplicate_usage_records"] += 1
                continue
            usage_ids.add(rid)
            u = p.get("usage", {})
            target = turns[p.get("turn_id") or current]
            target["usage"].append({"input": u.get("input_tokens"),
                "cached": u.get("cached_input_tokens"),
                "write": u.get("cache_write_input_tokens"),
                "output": u.get("output_tokens"),
                "reasoning": u.get("reasoning_output_tokens")})
        elif typ == "event_msg" and p.get("type") == "task_complete":
            target = turns[p.get("turn_id") or current]
            target.update(completed=True, end=ts)
        elif typ == "compacted":
            t["compactions"] += 1
        elif typ == "response_item":
            pt = p.get("type")
            if pt in ("function_call", "custom_tool_call"):
                t["tools"] += 1
                kind = tool_kind(p.get("name", ""), str(p.get("arguments", p.get("input", ""))))
                if kind and kind != "other_cairn_mention":
                    t["cairn_tools"] += 1
                    t["memory_tools"] += kind == "memory"
            if pt == "message":
                text = text_of(p.get("content"))
                if p.get("role") in ("user", "developer", "system"):
                    if text.startswith(WAKE) or (text.startswith("<channel") and WAKE in text):
                        t["wake"] = True
                    for k in ("notice", "response", "request"):
                        if "Cairn has a " + k in text:
                            t["kinds"].append(k)
                    if any(m in text for m in MARKERS):
                        t["contexts"].append(counts(text))
                    at = text.find('{"selected":')
                    if "Cairn candidates/openings" in text and at >= 0:
                        try:
                            recall = json.JSONDecoder().raw_decode(text[at:])[0]
                        except json.JSONDecodeError:
                            errors["invalid_recall_json"] += 1
                            continue
                        row = dict(total_bytes=len(text.encode()), source_bytes=0, partial=0, whole=0,
                                   query_truncated=recall.get("candidate_search", {}).get("discovery", {}).get("query_projection", {}).get("truncated"))
                        for item in recall.get("candidate_bodies", []):
                            response = item.get("response", {})
                            record = response.get("selection", {}).get("record", {})
                            span = response.get("span") or {}
                            row["source_bytes"] += len((record.get("body", "") + span.get("body", "")).encode())
                            row["partial"] += response.get("source_extent") == "partial_span"
                            row["whole"] += bool(record.get("body"))
                        for selection in recall.get("selected", []):
                            row["source_bytes"] += len(selection.get("record", {}).get("body", "").encode())
                        recall_metrics.append(row)
    return dict(turns=dict(turns), errors=dict(errors), versions=sorted(versions), recall_metrics=recall_metrics)


def analyze_claude(path, since, until):
    turns = collections.defaultdict(blank_turn)
    errors = collections.Counter()
    current = None
    messages, shapes, requests, responses = {}, {}, [], {}
    versions = set()
    exposures = collections.Counter()
    observed_messages = {}
    request_metrics = []
    tool_ids = set()
    for r in records(path, errors):
        typ = r.get("type")
        if r.get("version"):
            versions.add(r["version"])
        if typ == "api-request-blob":
            # Count text blocks, never base64 image data as text tokens. Tool
            # arguments and image accounting are outside this attribution view.
            text = text_of(r["message"])
            messages[r["hash"]] = (counts(text), any(m in text for m in MARKERS))
            continue
        if typ == "api-request-shape":
            shape = r.get("shape", {})
            tools = [v for v in shape.get("tools", []) if "cairn" in str(v.get("name", "")).lower()]
            shapes[r["shapeHash"]] = dict(cairn_tools=len(tools), **counts(compact(tools)))
            continue
        ts = timestamp(r.get("timestamp"))
        in_window = ts is not None and since <= ts < until
        # Reconstruct even pre-window requests because keep/tail is incremental.
        if typ == "api-request":
            keep = r.get("keep", 0)
            if keep > len(requests):
                errors["invalid_request_keep"] += 1
                requests = []
            requests = requests[:keep] + r.get("tail", [])
            if in_window:
                selected = [h for h in requests if h in messages and messages[h][1]]
                for h in selected:
                    exposures[h] += 1
                    observed_messages[h] = messages[h][0]
                request_metrics.append(dict(cairn_message_bytes=sum(messages[h][0]["bytes"] for h in selected),
                    cairn_message_proxy_tokens=sum(messages[h][0]["proxy_tokens"] for h in selected),
                    direct_tail_cairn_bytes=sum(messages[h][0]["bytes"] for h in r.get("tail", []) if h in messages and messages[h][1]),
                    missing_blobs=sum(h not in messages for h in requests),
                    cairn_schema=shapes.get(r.get("shapeHash")),
                    query_source=r.get("querySource")))
            continue
        if not in_window:
            continue
        p = r.get("message", {})
        if typ == "user" and r.get("promptId"):
            current = r["promptId"]
            t = turns[current]
            if t["start"] is None:
                t["start"] = ts
            text = text_of(p.get("content"))
            if text.startswith(WAKE) or (text.startswith("<channel") and WAKE in text):
                t["wake"] = True
        if not current:
            errors["records_without_prompt"] += 1
            continue
        t = turns[current]
        if typ == "assistant":
            t["models"].append(p.get("model", "unknown"))
            mid = p.get("id")
            u = p.get("usage")
            if mid and u:
                # Streaming blocks repeat message id; retain the last usage,
                # never sum each block's duplicate input accounting.
                input_fields = [u.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
                responses[mid] = (current, {"input": sum(input_fields) if all(v is not None for v in input_fields) else None,
                    "cached": u.get("cache_read_input_tokens"), "write": u.get("cache_creation_input_tokens"),
                    "output": u.get("output_tokens"),
                    "reasoning": (u.get("output_tokens_details") or {}).get("thinking_tokens")})
            for block in p.get("content", []):
                if block.get("type") == "tool_use":
                    if block.get("id") in tool_ids:
                        continue
                    tool_ids.add(block.get("id"))
                    t["tools"] += 1
                    kind = tool_kind(block.get("name", ""), compact(block.get("input", {})))
                    if kind and kind != "other_cairn_mention":
                        t["cairn_tools"] += 1
                        t["memory_tools"] += kind == "memory"
        if typ == "system" and r.get("subtype") == "turn_duration":
            t.update(completed=True, end=ts)
        if typ == "system" and r.get("subtype") == "compact_boundary":
            t["compactions"] += 1
        if typ in ("user", "attachment", "system"):
            text = text_of(p.get("content")) if typ == "user" else compact(r.get("attachment", r.get("hookAdditionalContext", [])))
            for k in ("notice", "response", "request"):
                if "Cairn has a " + k in text:
                    t["kinds"].append(k)
            if any(m in text for m in MARKERS):
                t["contexts"].append(counts(text))
    for turn, usage in responses.values():
        turns[turn]["usage"].append(usage)
    return dict(turns=dict(turns), errors=dict(errors), versions=sorted(versions),
                request_metrics=request_metrics,
                exposure_repetitions=distribution(list(exposures.values())),
                unique_cairn_message_bytes=sum(v["bytes"] for v in observed_messages.values()),
                unique_cairn_message_proxy_tokens=sum(v["proxy_tokens"] for v in observed_messages.values()))


def summarize(sessions):
    groups = collections.defaultdict(list)
    for s in sessions:
        for turn in s["turns"].values():
            kinds = sorted(set(turn["kinds"]))
            category = ("wake_" if turn["wake"] else "active_") + ("+".join(kinds) or "unclassified")
            groups[s["harness"] + ":" + category].append(turn)
    result = {}
    for group, turns in sorted(groups.items()):
        row = {"turns": len(turns), "native_completed": sum(t["completed"] for t in turns),
               "models": sorted({m for t in turns for m in t["models"]}),
               "compactions": sum(t["compactions"] for t in turns)}
        row["missing_usage_fields"] = dict(collections.Counter(k for t in turns for u in t["usage"] for k, v in u.items() if v is None))
        for field in ("input", "cached", "write", "output", "reasoning"):
            values = [sum(u[field] for u in t["usage"] if u[field] is not None) for t in turns if t["usage"] and any(u[field] is not None for u in t["usage"])]
            row[field] = distribution(values)
        for field in ("tools", "cairn_tools", "memory_tools"):
            row[field] = distribution([t[field] for t in turns])
        row["model_responses"] = distribution([len(t["usage"]) for t in turns])
        row["duration_seconds"] = distribution([t["end"]-t["start"] for t in turns if t["start"] is not None and t["end"] is not None])
        result[group] = row
    return result


def join_journals(sessions, root):
    """Join local committed command evidence; this does not attest task success."""
    turns = {key: value for session in sessions for key, value in session["turns"].items()}
    joined = collections.Counter()
    for path in root.glob("*/inbox/*.json"):
        context = json.loads(path.read_text())
        turn = turns.get((context.get("native_turn_id") or "").removeprefix("claude-channel:"))
        if turn is None:
            continue
        kind = context["kind"]
        turn["kinds"].append(kind)
        # Derive journal location from the inspected tree, not file-supplied paths.
        journal_path = path.parent.parent / "intents" / (path.stem + ".json")
        journal = json.loads(journal_path.read_text()) if journal_path.exists() else {}
        completion, response = journal.get("completion", {}), journal.get("response", {})
        argv = completion.get("argv", [])
        disposition = argv[argv.index("--disposition")+1] if "--disposition" in argv else "handled"
        turn.setdefault("durable", []).append(dict(kind=kind,
            committed=completion.get("status") == "committed", action=completion.get("kind"),
            disposition=disposition, code=completion.get("code"),
            reply_committed=response.get("status") == "committed"))
        joined[kind] += 1
    return dict(joined)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--per-home", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, help="Private frozen list of home/harness/path objects; otherwise select latest modified files")
    parser.add_argument("--write-manifest", type=Path, help="Save selected paths locally for reproducibility; do not publish this file")
    parser.add_argument("--coordination-state", type=Path, help="Optional read-only local journal join")
    args = parser.parse_args()
    since, until = timestamp(args.since), timestamp(args.until)
    if since >= until or not 1 <= args.per_home <= 50:
        parser.error("require since < until and 1 <= per-home <= 50")
    sessions = []
    manifest = []
    for home, sub, harness in [(".codex", "sessions", "codex"), (".codex-harm", "sessions", "codex"),
                               (".codex-3", "sessions", "codex"), (".claude", "projects", "claude"),
                               (".claude-harm", "projects", "claude")]:
        paths = [p for p in (Path.home()/home/sub).rglob("*.jsonl") if "subagents" not in p.parts and p.stat().st_mtime >= since]
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for p in paths[:args.per_home]:
            manifest.append(dict(home=home, harness=harness, path=str(p)))
    if args.manifest:
        manifest = json.loads(args.manifest.read_text())
    if args.write_manifest:
        args.write_manifest.write_text(json.dumps(manifest, indent=2)+"\n")
        args.write_manifest.chmod(0o600)
    for item in manifest:
        p, harness = Path(item["path"]), item["harness"]
        if harness not in ("codex", "claude"):
            parser.error("manifest harness must be codex or claude")
        result = (analyze_codex if harness == "codex" else analyze_claude)(p, since, until)
        result.update(home=item["home"], harness=harness, path_sha256=hashlib.sha256(str(p).encode()).hexdigest(),
                      snapshot_size=p.stat().st_size)
        sessions.append(result)
    journal_joins = join_journals(sessions, args.coordination_state) if args.coordination_state else None
    output = dict(schema="cairn.token-audit/1", since=args.since, until=args.until, per_home=args.per_home,
                  proxy_tokenizer="tiktoken 0.12.0 o200k_base; not verified for deployed models",
                  summary=summarize(sessions), sessions=sessions, journal_joins=journal_joins)
    args.output.write_text(json.dumps(output, indent=2)+"\n")
    print(json.dumps(dict(sessions=len(sessions), output=str(args.output), groups=list(output["summary"]))))


if __name__ == "__main__":
    main()
