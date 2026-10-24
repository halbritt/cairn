#!/usr/bin/env python3
"""Aggregate retained native request-shape exposure without retaining payloads.

Claude api-request-shape is a request declaration, not billed or uncached input.
Codex logs in this audit have no equivalent request-shape record. This script
does not infer hidden schemas or attribute response usage to a tool declaration.
"""

import argparse
import collections
import datetime as dt
import json
from pathlib import Path

import tiktoken


ENCODING = tiktoken.get_encoding("o200k_base")


def instant(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def proxy_tokens(value):
    return len(ENCODING.encode(value, disallowed_special=()))


def schema_measure(tools):
    selected = [tool for tool in tools if isinstance(tool, dict)
                and "cairn" in str(tool.get("name", "")).lower()]
    if not selected:
        return dict(tool_count=0, bytes=0, proxy_tokens=0)
    wire = json.dumps(selected, separators=(",", ":"), ensure_ascii=False)
    return dict(tool_count=len(selected), bytes=len(wire.encode()),
                proxy_tokens=proxy_tokens(wire))


def records(path, errors):
    with path.open("rb") as stream:
        remaining = path.stat().st_size
        while remaining:
            line = stream.readline(remaining)
            if not line:
                break
            remaining -= len(line)
            if not line.endswith(b"\n"):
                errors["partial_lines"] += 1
                continue
            try:
                yield json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                errors["invalid_lines"] += 1


def usage_of(message):
    usage = message.get("usage") or {}
    fields = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    if not all(type(usage.get(field)) is int and usage[field] >= 0 for field in fields):
        return None
    return {"input": sum(usage[field] for field in fields),
            "cache_creation": usage["cache_creation_input_tokens"],
            "cache_read": usage["cache_read_input_tokens"]}


def inspect_claude(path, since, until):
    errors = collections.Counter()
    shapes = {}
    requests = []
    active = None
    after_compaction = False
    seen_digests = set()
    seen_request = False
    first_cairn_call = False
    seen_pull = False
    seen_write = False
    for record in records(path, errors):
        kind = record.get("type")
        if kind == "api-request-shape":
            shape = record.get("shape") or {}
            if isinstance(shape.get("tools"), list) and record.get("shapeHash"):
                shapes[record["shapeHash"]] = schema_measure(shape["tools"])
            continue
        if kind == "system" and record.get("subtype") == "compact_boundary":
            after_compaction = True
        when = instant(record.get("timestamp"))
        if kind == "assistant" and (when is None or not since <= when < until):
            for block in (record.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name", ""))
                    first_cairn_call |= "cairn" in name.lower()
                    seen_pull |= "cairn_pull" in name
                    seen_write |= "cairn_remember" in name or "cairn_edit" in name
            continue
        if kind == "api-request" and (when is None or not since <= when < until):
            seen_request = True
            after_compaction = False
            active = None
            if record.get("requestDigest"):
                seen_digests.add(record["requestDigest"])
            continue
        if when is None or not since <= when < until:
            continue
        if kind == "api-request":
            digest = record.get("requestDigest")
            active = dict(shape=shapes.get(record.get("shapeHash")),
                          file_first=not seen_request, post_compaction=after_compaction,
                          same_digest_repeat=bool(digest and digest in seen_digests),
                          first_cairn_use=False, after_first_cairn_use=first_cairn_call,
                          first_pull=False, first_write=False,
                          usage={}, assistant_without_id=0)
            requests.append(active)
            seen_request = True
            after_compaction = False
            if digest:
                seen_digests.add(digest)
        elif kind == "assistant" and active is not None:
            message = record.get("message") or {}
            message_id = message.get("id")
            if message_id:
                # Streaming blocks repeat this ID. The final observed usage wins.
                usage = usage_of(message)
                if usage is not None:
                    active["usage"][message_id] = usage
            else:
                active["assistant_without_id"] += 1
            for block in message.get("content") or []:
                if (isinstance(block, dict) and block.get("type") == "tool_use"
                        and "cairn" in str(block.get("name", "")).lower()):
                    name = str(block.get("name", ""))
                    if not first_cairn_call:
                        active["first_cairn_use"] = True
                    first_cairn_call = True
                    if "cairn_pull" in name and not seen_pull:
                        active["first_pull"] = True
                        seen_pull = True
                    if ("cairn_remember" in name or "cairn_edit" in name) and not seen_write:
                        active["first_write"] = True
                        seen_write = True
    return requests, errors


def aggregate(requests, errors):
    phases = {name: [] for name in ("all", "file_first", "first_cairn_use", "first_pull", "first_write", "same_digest_repeat",
                                    "post_compaction", "after_first_cairn_use", "other")}
    for request in requests:
        phases["all"].append(request)
        labels = [name for name in phases if name not in ("all", "other") and request[name]]
        for name in labels or ["other"]:
            phases[name].append(request)
    result = {}
    for name, rows in phases.items():
        present = [r for r in rows if r["shape"] is not None]
        usages = [usage for row in rows for usage in row["usage"].values()]
        result[name] = dict(requests=len(rows), shapes_observed=len(present),
                            requests_with_cairn_tools=sum(r["shape"]["tool_count"] > 0 for r in present),
                            requests_with_usage=sum(bool(r["usage"]) for r in rows),
                            cairn_tool_count=sum(r["shape"]["tool_count"] for r in present),
                            cairn_schema_bytes=sum(r["shape"]["bytes"] for r in present),
                            cairn_schema_proxy_tokens=sum(r["shape"]["proxy_tokens"] for r in present),
                            responses_with_usage=len(usages),
                            response_input_tokens=sum(u["input"] for u in usages),
                            response_input_less_cached=sum(u["input"] - u["cache_read"] for u in usages),
                            response_cache_creation_tokens=sum(u["cache_creation"] for u in usages),
                            response_cache_read_tokens=sum(u["cache_read"] for u in usages))
    return dict(total_requests=len(requests), requests_without_shape=sum(r["shape"] is None for r in requests),
                requests_by_cairn_tool_count=dict(sorted(collections.Counter(
                    str(r["shape"]["tool_count"]) for r in requests if r["shape"] is not None).items())),
                assistants_without_id=sum(r["assistant_without_id"] for r in requests),
                errors=dict(errors), phases=result,
                phase_note="Labels overlap; schema bytes are declared request-shape sizes, not uncached or billed tokens.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    since, until = instant(args.since), instant(args.until)
    if since is None or until is None or since.tzinfo is None or until.tzinfo is None or since >= until:
        parser.error("require timezone-aware since < until")
    manifest = json.loads(args.manifest.read_text())
    if not isinstance(manifest, list):
        parser.error("manifest must be a list")
    requests = []
    errors = collections.Counter()
    homes = collections.Counter()
    for item in manifest:
        if item.get("harness") != "claude":
            continue
        path = Path(item["path"])
        rows, found = inspect_claude(path, since, until)
        requests.extend(rows)
        errors.update(found)
        homes[item.get("home", "unknown")] += 1
    output = dict(schema="cairn.schema-exposure-audit/1", since=args.since, until=args.until,
                  proxy_tokenizer="tiktoken o200k_base; not verified for deployed models",
                  claude_files=sum(homes.values()), claude=aggregate(requests, errors),
                  codex=dict(request_schema="unavailable_in_retained_native_log",
                             note="Codex tool calls and response usage do not reveal per-request tool schemas."))
    args.output.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(dict(claude_files=output["claude_files"], requests=len(requests), output=str(args.output))))


if __name__ == "__main__":
    main()
