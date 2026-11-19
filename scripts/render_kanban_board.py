#!/usr/bin/env python3
"""Deterministic Kanban table renderer from Cairn's workstream status board and Plane (CAIRN-466).

Makes zero model calls. Reads the bounded workstream status board (`cairn status get`)
and Plane work items (`plane issue list`), computes the `leaf-done-v1` campaign
completion metric (completed noncancelled leaf descendants / all noncancelled leaves),
and renders the deterministic Markdown Kanban report.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

DEFAULT_CAIRN301_AREAS = [
    {"area": "Foundation", "stream": "foundation"},
    {"area": "Semantic", "stream": "semantic"},
    {"area": "Coordination / execution", "stream": "coordination"},
    {"area": "Release", "stream": "release"},
    {"area": "Audit", "stream": "audit"},
    {"area": "Documentation", "stream": "documentation"},
    {"area": "Integration / testing", "stream": "integration"},
    {"area": "All-host deployment", "stream": "deployment"},
    {"area": "24-hour burn-in", "stream": "burn-in"},
    {"area": "Retirement / final checks", "stream": "retirement"},
]

ALLOWED_STATE_GROUPS = {"backlog", "unstarted", "started", "completed", "cancelled"}


def _read_text_redacted(path_str: str, label: str) -> str:
    try:
        return Path(path_str).read_text(encoding="utf-8")
    except OSError as exc:
        reason = exc.strerror or type(exc).__name__
        raise RuntimeError(f"cannot read {label}: {reason}") from None


def compute_leaf_completion(items: list[dict], campaign: str, project: str = "CAIRN") -> dict:
    prefix = f"{project.upper()}-"
    by_id: dict[str, dict] = {}
    root = None
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise ValueError("each Plane work item must be an object with string id")
        item_id = item["id"]
        if item_id in by_id:
            raise ValueError("duplicate Plane work item id in payload")
        by_id[item_id] = item
        seq_label = f"{prefix}{item['sequence_id']}" if "sequence_id" in item else None
        if item_id == campaign or seq_label == campaign or item.get("identifier") == campaign:
            root = item
    if root is None:
        raise ValueError(f"campaign {campaign!r} not found in Plane items")
    root_id = root["id"]

    # Detect cycles across parent links before computing descendants.
    for item_id, item in by_id.items():
        seen = {item_id}
        cur = item.get("parent")
        while cur is not None:
            if cur in seen:
                raise ValueError("cycle detected in Plane parent hierarchy")
            seen.add(cur)
            parent_obj = by_id.get(cur)
            cur = parent_obj.get("parent") if parent_obj is not None else None

    selected = {root_id}
    while True:
        expanded = selected | {x["id"] for x in items if x.get("parent") in selected}
        if expanded == selected:
            break
        selected = expanded
    descendants = [x for x in items if x["id"] in selected and x["id"] != root_id]
    for d in descendants:
        st = d.get("state")
        if not isinstance(st, dict) or st.get("group") not in ALLOWED_STATE_GROUPS:
            raise ValueError("Plane items require expanded state objects with known group")
    noncancelled = [x for x in descendants if x["state"]["group"] != "cancelled"]
    noncancelled_parents = {x.get("parent") for x in noncancelled}
    eligible = [x for x in noncancelled if x["id"] not in noncancelled_parents]
    if not eligible:
        raise ValueError("campaign has no eligible noncancelled leaf work items")
    done = [x for x in eligible if x["state"]["group"] == "completed"]
    pct = 100.0 * len(done) / len(eligible)
    label = f"{prefix}{root['sequence_id']}" if "sequence_id" in root and "-" not in campaign else campaign
    return {
        "method": "leaf-done-v1",
        "campaign": label,
        "campaign_id": root_id,
        "done": len(done),
        "total": len(eligible),
        "percent": pct,
        "display_percent": round(pct),
    }


def _escape_cell(text: str) -> str:
    cleaned = " ".join((text or "").replace("\r", " ").replace("\n", " ").split())
    return cleaned.replace("\\", "\\\\").replace("|", "\\|")


def _render_stream_row(area_label: str, st: dict) -> str:
    state = str(st.get("state") or "active")
    done_col = _escape_cell(st.get("last_landed") or ("Completed" if state == "done" else "—"))
    active_parts: list[str] = []
    if st.get("current"):
        active_parts.append(str(st["current"]))
    elif state == "done":
        active_parts.append("None")
    if st.get("next_checkpoint") and state != "done":
        active_parts.append(f"Next checkpoint: {st['next_checkpoint']}")
    active_col = _escape_cell("; ".join(active_parts) if active_parts else "—")
    return f"| {_escape_cell(area_label)} | {_escape_cell(state)} | {done_col} | {active_col} |"


def render_kanban_markdown(
    board: dict,
    metric: dict | None,
    *,
    areas: list[dict] | None = None,
    now: datetime | None = None,
    tz_name: str = "America/Los_Angeles",
) -> str:
    if now is not None and now.tzinfo is None:
        raise ValueError("timestamp must include an explicit UTC offset")
    ts = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(tz_name))
    time_label = ts.strftime("%H:%M %Z")
    raw_streams = board.get("streams")
    if raw_streams is None:
        raw_streams = []
    if not isinstance(raw_streams, list):
        raise ValueError("board streams must be a JSON array")
    streams = [s for s in raw_streams if isinstance(s, dict) and isinstance(s.get("stream"), str)]
    streams_by_name = {s["stream"]: s for s in streams}

    lines: list[str] = []
    if metric is not None:
        lines.append(
            f"**{metric['campaign']} — {time_label}: estimated completion "
            f"{metric['display_percent']}% ({metric['done']}/{metric['total']} work items Done).** "
            "Equal leaf-ticket count; no partial credit."
        )
    else:
        lines.append(f"**Workstream Kanban — {time_label}**")
    lines.append("")
    lines.append("| Area | State | Done | Active / remaining |")
    lines.append("|---|---|---|---|")

    seen_streams: set[str] = set()
    area_specs = areas if areas is not None else [{"area": s["stream"], "stream": s["stream"]} for s in streams]
    for spec in area_specs:
        if not isinstance(spec, dict) or not isinstance(spec.get("area"), str) or not isinstance(spec.get("stream"), str):
            raise ValueError("each area mapping must be an object with string area and stream")
        stream_key = spec["stream"]
        seen_streams.add(stream_key)
        st = streams_by_name.get(stream_key)
        if st is None:
            lines.append(f"| {_escape_cell(spec['area'])} | unstarted | Not started | Outstanding |")
        else:
            lines.append(_render_stream_row(spec["area"], st))

    for stream_key in sorted(set(streams_by_name) - seen_streams):
        lines.append(_render_stream_row(stream_key, streams_by_name[stream_key]))

    blockers = [
        f"- **{_escape_cell(s['stream'])}**: {_escape_cell(str(s['blocker']))}"
        for s in streams
        if str(s.get("blocker") or "").strip() and s.get("state") != "done"
    ]
    decisions = [
        f"- **{_escape_cell(s['stream'])}**: {_escape_cell(str(s['decision_needed']))}"
        for s in streams
        if str(s.get("decision_needed") or "").strip() and s.get("state") != "done"
    ]
    if blockers:
        lines.extend(["", "### Blockers", *blockers])
    if decisions:
        lines.extend(["", "### Decisions needed", *decisions])
    return "\n".join(lines) + "\n"


def _unwrap_board_doc(doc: object) -> dict:
    if isinstance(doc, dict) and doc.get("schema") == "cairn.response/1":
        if not doc.get("ok"):
            raise RuntimeError(f"cairn status get refused: {doc.get('status')}")
        doc = doc.get("data") or {}
    if not isinstance(doc, dict):
        raise ValueError("workstream board payload must be a JSON object")
    return doc


def _load_board(args: argparse.Namespace) -> dict:
    if args.board_json is not None:
        return _unwrap_board_doc(json.loads(_read_text_redacted(args.board_json, "board JSON")))
    all_streams: list[dict] = []
    after = ""
    repo_val = ""
    for _ in range(100):
        cmd = [args.cairn, "status", "get", "--limit", "200"]
        if after:
            cmd.extend(["--after", after])
        if args.token_file:
            cmd.extend(["--token-file", args.token_file])
        if args.socket:
            cmd.extend(["--socket", args.socket])
        if args.repo:
            cmd.extend(["--repo", args.repo])
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            raise RuntimeError("cairn status get timed out") from None
        if proc.returncode != 0:
            raise RuntimeError(f"cairn status get exited {proc.returncode}")
        page = _unwrap_board_doc(json.loads(proc.stdout))
        repo_val = page.get("repo") or repo_val
        page_streams = page.get("streams") or []
        if not isinstance(page_streams, list):
            raise ValueError("board streams must be a JSON array")
        all_streams.extend(page_streams)
        if not page.get("more"):
            return {"repo": repo_val, "streams": all_streams}
        next_after = page.get("next_after") or ""
        if not next_after or next_after <= after:
            raise RuntimeError("cairn status get pagination cursor did not advance")
        after = next_after
    raise RuntimeError("cairn status get exceeded maximum page count")


def _load_plane_items(args: argparse.Namespace) -> list[dict]:
    if args.plane_json is not None:
        doc = json.loads(_read_text_redacted(args.plane_json, "Plane JSON"))
    else:
        cmd = [
            args.plane, "issue", "list", "-p", args.project,
            "--fields", "id,sequence_id,name,state,parent,estimate_point,completed_at",
            "--expand", "state",
        ]
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            raise RuntimeError("plane issue list timed out") from None
        if proc.returncode != 0:
            raise RuntimeError(f"plane issue list exited {proc.returncode}")
        doc = json.loads(proc.stdout)
    if isinstance(doc, dict) and "ok" in doc:
        if doc.get("ok") is not True:
            raise RuntimeError("Plane issue list returned ok=false")
        meta = doc.get("meta")
        pagination = meta.get("pagination") if isinstance(meta, dict) else None
        if not isinstance(pagination, dict) or pagination.get("drained") is not True:
            raise RuntimeError("Plane response requires meta.pagination.drained=true")
        items = doc.get("data")
    else:
        items = doc
    if not isinstance(items, list):
        raise ValueError("Plane items payload must be a JSON array")
    return items


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--board-json", help="Path to saved `cairn status get` JSON output")
    p.add_argument("--plane-json", help="Path to saved `plane issue list --expand state` JSON output")
    p.add_argument("--cairn", default="cairn", help="cairn CLI executable")
    p.add_argument("--token-file", help="Cairn API token file")
    p.add_argument("--socket", help="Cairn API socket")
    p.add_argument("--repo", help="Cairn collection repository")
    p.add_argument("--plane", default="plane", help="plane CLI executable")
    p.add_argument("--project", default="CAIRN", help="Plane project slug")
    p.add_argument("--campaign", help="Campaign identifier or UUID (e.g. CAIRN-301)")
    p.add_argument("--cairn301-areas", action="store_true", help="Use the canonical CAIRN-301 area table")
    p.add_argument("--areas-json", help="Optional JSON array of {area, stream} mappings")
    p.add_argument("--timezone", default="America/Los_Angeles", help="IANA timezone for report header")
    p.add_argument("--now", help="Fixed ISO-8601 timestamp with UTC offset")
    p.add_argument("--json", action="store_true", help="Emit JSON {metric, markdown}")
    args = p.parse_args(argv)
    try:
        if args.plane_json is not None and not args.campaign:
            raise ValueError("--plane-json requires --campaign")
        board = _load_board(args)
        metric = None
        if args.campaign:
            items = _load_plane_items(args)
            metric = compute_leaf_completion(items, args.campaign, project=args.project)
        areas = None
        if args.areas_json:
            loaded_areas = json.loads(_read_text_redacted(args.areas_json, "areas JSON"))
            if not isinstance(loaded_areas, list):
                raise ValueError("areas JSON must be a JSON array")
            areas = loaded_areas
        elif args.cairn301_areas or args.campaign == "CAIRN-301":
            areas = DEFAULT_CAIRN301_AREAS
        now_dt = datetime.fromisoformat(args.now) if args.now else None
        md = render_kanban_markdown(board, metric, areas=areas, now=now_dt, tz_name=args.timezone)
        if args.json:
            sys.stdout.write(json.dumps({"metric": metric, "markdown": md}, allow_nan=False) + "\n")
        else:
            sys.stdout.write(md)
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        sys.stderr.write(f"render_kanban_board: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
