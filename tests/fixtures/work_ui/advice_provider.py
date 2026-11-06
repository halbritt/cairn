#!/usr/bin/python3
"""Synthetic subprocess boundary for WC05 browser proof; never a model client.

Registered only in the disposable fixture, with this exact executable hash.
Its capability/result envelopes exercise Cairn's adapter contract. They are not
evidence of native Codex, subscription authentication, or model usefulness.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import uuid


UPSTREAM = "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"
PATCH = hashlib.sha256(b"cairn-work-ui-synthetic-advice-provider-v1").hexdigest()
MODEL = "synthetic-work-ui-model"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["cairn-advice"])
    parser.add_argument("--capabilities", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--timeout-ms", type=int)
    parser.add_argument("--max-output-bytes", type=int)
    args = parser.parse_args()
    home = Path(os.environ["CODEX_HOME"])
    require(home.is_absolute() and home.is_dir() and not home.is_symlink(), "private fixture home required")
    require(home.stat().st_uid == os.getuid() and stat.S_IMODE(home.stat().st_mode) == 0o700,
            "fixture home must be owned and mode 0700")
    marker = home / "synthetic-fixture.json"
    config = json.loads(marker.read_text())
    require(config.get("schema") == "cairn.synthetic-advice-fixture/1", "synthetic fixture marker required")
    require(not any(name in os.environ for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "CAIRN_TOKEN")),
            "provider subprocess inherited an unexpected credential variable")
    if args.capabilities:
        require(args.model is None and args.timeout_ms is None and args.max_output_bytes is None,
                "capability probe included generation flags")
        value = dict(schema="cairn.codex-advice-capabilities/1", request_schema="cairn.codex-advice-request/1",
            result_schema="cairn.codex-advice-result/1", provider="openai", auth_method="chatgpt_subscription",
            one_request=True, tools=False, retries=False, upstream_commit=UPSTREAM, patch_sha256=PATCH)
        print(json.dumps(value))
        return 0
    require(args.model == MODEL and args.timeout_ms is not None and 10000 <= args.timeout_ms <= 180000
            and args.max_output_bytes is not None and 1024 <= args.max_output_bytes <= 262144,
            "generation arguments differ from the registered synthetic profile")
    raw = sys.stdin.buffer.read(262145)
    require(len(raw) <= 262144, "stdin exceeded native adapter bound")
    request = json.loads(raw)
    require(set(request) == {"schema", "request_id", "prompt"}
            and request["schema"] == "cairn.codex-advice-request/1", "unexpected request envelope")
    uuid.UUID(request["request_id"])
    prompt = request["prompt"]
    require(isinstance(prompt, str) and "\nInput Snapshot:\n" in prompt, "canonical snapshot marker missing")
    snapshot_text = prompt.split("\nInput Snapshot:\n", 1)[1]
    snapshot, end = json.JSONDecoder().raw_decode(snapshot_text)
    require(snapshot_text[end:].startswith("\n\nInstructions:"), "canonical snapshot has unexpected trailing framing")
    require(set(snapshot).issubset({"binding_id", "plane_item_uuid", "identifier", "title", "current_state",
                                  "existing_blockers", "candidate_artifacts", "source_references"}),
            "snapshot contains an unreviewed field")
    uuid.UUID(snapshot["binding_id"])
    uuid.UUID(snapshot["plane_item_uuid"])
    mode = config["mode"]
    observation = dict(kind="generation", mode=mode, pid=os.getpid(), monotonic_ns=time.monotonic_ns(),
        request_id=request["request_id"], request_sha256=hashlib.sha256(raw).hexdigest(),
        prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
        snapshot_sha256=hashlib.sha256(snapshot_text[:end].encode()).hexdigest(), snapshot=snapshot)
    log = home / "invocations.jsonl"
    with log.open("a") as output:
        output.write(json.dumps(observation, sort_keys=True) + "\n")
    log.chmod(0o600)
    envelope = dict(schema="cairn.codex-advice-result/1", request_id=request["request_id"], ok=True,
                    model=MODEL, provider="openai", auth_method="chatgpt_subscription")
    sources = [source["id"] for source in snapshot.get("source_references", [])]
    actions = []
    if mode == "all":
        require(snapshot.get("candidate_artifacts"), "all-actions fixture requires an actual selected artifact")
        selected_text = snapshot.get("source_references", [{}])[0].get("body", "No selected source text")
        actions = [dict(action_type="annotate_progress", text="Synthetic reviewed progress: " + selected_text, source_references=sources,
                        rationale="Selected source supports this proposed summary."),
                   dict(action_type="annotate_blocker", blocker_action="create",
                        text='Synthetic blocker <img src=x onerror="window.adviceInjected=true">', source_references=sources,
                        rationale="The operator can review this selected blocker."),
                   dict(action_type="link_artifact", artifact_revision_id=snapshot["candidate_artifacts"][0]["revision_uuid"],
                        role="evidence", rationale="Link the exact selected registered revision.")]
    elif mode == "resolve":
        blocker = next((row for row in snapshot.get("existing_blockers", []) if row["blocker_id"] == config.get("blocker_id")), None)
        require(blocker is not None, "the requested real blocker was not present in the input snapshot")
        actions = [dict(action_type="annotate_blocker", blocker_action="resolve", existing_blocker_id=blocker["blocker_id"],
                        expected_revision=blocker["revision"], source_references=sources,
                        rationale="Withdraw the accepting operator's exact selected blocker annotation.")]
    elif mode == "quota":
        envelope["ok"] = False
        envelope["error"] = dict(code="QUOTA_EXHAUSTED", message="Synthetic subscription quota refusal")
        print(json.dumps(envelope))
        return 1
    elif mode not in ("none", "malformed"):
        raise RuntimeError("unknown synthetic provider case")
    envelope["result"] = "{malformed" if mode == "malformed" else json.dumps(dict(summary="Synthetic boundary fixture result", actions=actions))
    print(json.dumps(envelope))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
