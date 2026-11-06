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


def main(before_input=None, action_hook=None):
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
        with (home / "capabilities.jsonl").open("a") as output:
            output.write(json.dumps(dict(kind="capabilities", pid=os.getpid())) + "\n")
        (home / "capabilities.jsonl").chmod(0o600)
        print(json.dumps(value))
        return 0
    require(args.model == MODEL and args.timeout_ms is not None and 10000 <= args.timeout_ms <= 180000
            and args.max_output_bytes is not None and 1024 <= args.max_output_bytes <= 262144,
            "generation arguments differ from the registered synthetic profile")
    if before_input is not None:
        before_input(home, config)
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
    custom = action_hook(home, config, snapshot) if action_hook is not None else None
    if custom is not None:
        actions = custom
    elif mode == "all":
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


def restore_write(home, name, value):
    path = home / name
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def restore_identity(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return dict(pid=pid, pgid=int(fields[2]), start_ticks=int(fields[19]),
                boot_id=Path("/proc/sys/kernel/random/boot_id").read_text().strip())


def restore_before_input(home, config):
    if config["mode"] != "hold_family":
        return
    config["fixture_deadline_monotonic"] = time.monotonic() + 175
    restore_write(home, "before-input.json", dict(identity=restore_identity(os.getpid()), input_consumed=False))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if (home / "allow-input").is_file():
            return
        time.sleep(0.02)
    raise RuntimeError("input-consumption gate exceeded 15 seconds")


def restore_actions(home, config, snapshot):
    mode = config["mode"]
    if mode == "hold_family":
        deadline = config["fixture_deadline_monotonic"]
        child = os.fork()
        if child == 0:
            for fd in (0, 1, 2):
                os.close(fd)
            while time.monotonic() < deadline:
                time.sleep(0.05)
            os._exit(124)
        restore_write(home, "family-ready.json", dict(leader=restore_identity(os.getpid()), child=restore_identity(child)))
        while time.monotonic() < deadline:
            time.sleep(0.05)
        raise SystemExit(124)  # No result can accidentally complete the held job.
    if mode not in ("restore_copies", "restore_progress"):
        return None
    require(len(snapshot.get("source_references", [])) == 1, "one actual selected restore source required")
    selected = snapshot["source_references"][0]["body"]
    actions = [dict(action_type="annotate_progress", text="Selected restore progress: " + selected,
                    rationale="Copied selected explanation: " + selected)]
    if mode == "restore_copies":
        require(len(snapshot.get("candidate_artifacts", [])) == 1, "one registered report revision required")
        actions += [dict(action_type="annotate_blocker", blocker_action="create", text="Pending restore blocker: " + selected,
                         rationale="Pending copied explanation: " + selected),
                    dict(action_type="link_artifact", artifact_revision_id=snapshot["candidate_artifacts"][0]["revision_uuid"],
                         role="evidence", rationale="Copied selected report explanation: " + selected)]
    # Optional output source_references are deliberately absent. The immutable
    # selected input is the real privacy dependency under test.
    return actions


if __name__ == "__main__":
    raise SystemExit(main(before_input=restore_before_input, action_hook=restore_actions))
