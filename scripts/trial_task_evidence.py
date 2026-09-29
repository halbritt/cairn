#!/usr/bin/env python3
"""Inspect retained trial MCP deliveries without regrading or rerunning agents.

Only fixture IDs, hashes, byte counts and outcomes leave this reader. Search
previews and actual body deliveries are separate; neither establishes that the
agent followed the guidance. Raw responses remain in the original local files.
"""
import argparse
import hashlib
import json
from pathlib import Path


def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()


def completed_calls(text, harness):
    """Correlate provider tool IDs; started or failed calls do not prove delivery."""
    if harness not in ("claude", "codex"):
        raise ValueError("unsupported harness " + harness)
    calls = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if harness == "claude":
            for part in (event.get("message") or {}).get("content", []):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use" and part.get("name", "").startswith("mcp__cairn__"):
                    calls.setdefault(part["id"], dict(tool=part["name"].removeprefix("mcp__cairn__"),
                                                     line=line_number, status="incomplete", content=[]))
                elif part.get("type") == "tool_result" and part.get("tool_use_id") in calls:
                    call = calls[part["tool_use_id"]]
                    call.update(status="failed" if part.get("is_error") else "completed", content=part.get("content", []))
        elif event.get("type") in ("item.started", "item.completed"):
            item = event.get("item") or {}
            if item.get("type") != "mcp_tool_call" or item.get("server") != "cairn":
                continue
            call = calls.setdefault(item["id"], dict(tool=item["tool"], line=line_number, status="incomplete", content=[]))
            if event["type"] == "item.completed":
                result = item.get("result") or {}
                call.update(status="completed" if item.get("status") == "completed" and not item.get("error")
                            and not result.get("isError") else "failed", content=result.get("content", []))
    return list(calls.values())


def content_text(content):
    if isinstance(content, str):
        return [content]
    return [part["text"] for part in content if isinstance(part, dict)
            and part.get("type") == "text" and isinstance(part.get("text"), str)]


def observed_deliveries(payload, corpus):
    """Match source bytes to the supplied frozen corpus, never by fuzzy wording."""
    by_hash = {sha256(note["body"]): note for note in corpus}
    if len(by_hash) != len(corpus):
        raise ValueError("ambiguous duplicate body hashes in corpus")
    deliveries = []
    if payload.get("schema") == "cairn.mcp-search/1":
        if payload.get("scope", {}).get("repo") != "trial:task-eval":
            raise ValueError("search response is outside the trial collection")
        for entry in payload.get("index", []):
            digest = entry.get("body_sha256")
            note = by_hash.get(digest)
            deliveries.append(dict(extent="preview", note=note["id"] if note else None,
                                   record_id=entry.get("record_id"), version=entry.get("version"),
                                   source_sha256=digest, bytes=len(entry.get("summary", "").encode())))
    selections = list(payload.get("selected", []))
    if "selection" in payload:
        selections.append(payload["selection"])
    selections += payload.get("competing", [])
    for selection in selections:
        record = selection.get("record", {})
        if record.get("scope", {}).get("repo") != "trial:task-eval":
            raise ValueError("body response is outside the trial collection")
        body = record.get("body")
        if not isinstance(body, str):
            raise ValueError("body response has no text")
        span = payload.get("span") if selection is payload.get("selection") else None
        extent = "full_body"
        if span is not None:
            if not isinstance(span, dict) or body != "" or not isinstance(span.get("body"), str):
                raise ValueError("invalid partial span response")
            body, extent = span["body"], "partial_span"
            digest = span.get("source_sha256")
            if (not all(type(span.get(key)) is int for key in ("offset", "end", "total_bytes"))
                    or not 0 <= span["offset"] < span["end"] <= span["total_bytes"]
                    or span["end"] - span["offset"] != len(body.encode()) or sha256(body) != span.get("sha256")):
                raise ValueError("invalid partial span bytes")
            note = by_hash.get(digest)
            if note and (len(note["body"].encode()) != span["total_bytes"]
                         or note["body"].encode()[span["offset"]:span["end"]] != body.encode()):
                raise ValueError("partial span does not match frozen source")
        else:
            digest = sha256(body)
        note = by_hash.get(digest)
        deliveries.append(dict(extent=extent, note=note["id"] if note else None,
                               record_id=record.get("record_id"), version=record.get("version"),
                               source_sha256=digest, bytes=len(body.encode())))
    return deliveries


def analyze_stream(text, harness, corpus):
    calls = []
    for observed in completed_calls(text, harness):
        pieces = content_text(observed["content"])
        call = dict(tool=observed["tool"], line=observed["line"], status=observed["status"],
                    response_text_bytes=sum(len(piece.encode()) for piece in pieces), deliveries=[], unparsed_blocks=0,
                    unrecognized_payloads=0)
        if call["status"] == "completed":
            for piece in pieces:
                try:
                    payload = json.loads(piece)
                except ValueError:
                    call["unparsed_blocks"] += 1
                    continue
                if not isinstance(payload, dict):
                    call["unparsed_blocks"] += 1
                    continue
                if payload.get("schema") != "cairn.mcp-search/1" and "selection" not in payload:
                    call["unrecognized_payloads"] += 1
                    continue
                call["deliveries"] += observed_deliveries(payload, corpus)
        calls.append(call)
    all_deliveries = [delivery for call in calls for delivery in call["deliveries"]]
    return dict(calls=calls,
                preview_notes=sorted({d["note"] for d in all_deliveries if d["extent"] == "preview" and d["note"]}),
                body_notes=sorted({d["note"] for d in all_deliveries if d["extent"] != "preview" and d["note"]}),
                body_bytes=sum(d["bytes"] for d in all_deliveries if d["extent"] != "preview"),
                unmapped_deliveries=sum(d["note"] is None for d in all_deliveries),
                response_text_bytes=sum(c["response_text_bytes"] for c in calls))


def analyze_report(path, corpus_path):
    path, corpus_path = Path(path), Path(corpus_path)
    report = json.loads(path.read_text())
    corpus_bytes = corpus_path.read_bytes()
    digest = hashlib.sha256(corpus_bytes).hexdigest()
    if report["frozen"]["corpus_sha256"] != digest:
        raise ValueError("corpus does not match this report's frozen hash")
    corpus = json.loads(corpus_bytes)["notes"]
    rows = []
    root = path.parent / "runs"
    for record in report["records"]:
        source = root / record["run_id"] / "stream.jsonl"
        if not source.resolve().is_relative_to(root.resolve()):
            raise ValueError("run path is outside the report directory")
        row = dict(run_id=record["run_id"], case=record["case"], arm=record["arm"], seed=record["seed"],
                   original_outcome=record["outcome"], harness=record.get("harness", report.get("harness", "claude")))
        if source.is_file():
            raw = source.read_bytes()
            row.update(source_sha256=hashlib.sha256(raw).hexdigest(),
                       **analyze_stream(raw.decode(), row["harness"], corpus))
        else:
            row["missing_stream"] = True
        rows.append(row)
    return dict(schema="cairn.task-eval.delivery-evidence/1", report=str(path.resolve()),
                report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), corpus_sha256=digest,
                analyzer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), records=rows,
                limits=["No grades are changed and no agent or service is called.",
                        "MCP response text bytes include JSON envelopes and repeated calls, but not provider tool schemas or protocol framing.",
                        "Unmapped deliveries may be synthetic distractors or other bodies; they are not silently classified as safe or irrelevant.",
                        "Hook and direct-prompt delivery are recorded separately by the original evaluator, not added to these counts.",
                        "Observed delivery does not establish applicability, truth, successful action or task completion."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="retained agent.json")
    parser.add_argument("--corpus", type=Path, default=Path(__file__).resolve().parents[1] / "trials/task-eval/corpus.json")
    parser.add_argument("--output", type=Path, required=True, help="new evidence JSON; existing files are not overwritten")
    args = parser.parse_args()
    result = analyze_report(args.report, args.corpus)
    with args.output.open("x") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(dict(records=len(result["records"]), output=str(args.output))))


if __name__ == "__main__":
    main()
