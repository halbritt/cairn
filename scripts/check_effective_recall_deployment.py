#!/usr/bin/env python3
"""Read-only inventory and fixture verification for effective Cairn recall guidance."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time

SCHEMA = "cairn.effective-recall-deployment-check/1"
BINDING_SCHEMA = "cairn.inbox-recall-binding/1"
DISPATCH_SCHEMA = "cairn.recall-dispatch/1"

ENGINE_REQUIRED_CUES = (
    "You may exceed automatic recall byte/call limits when the task needs more context; no user approval needed.",
    "Use inspection_policy:first-fitting-whole/2 when exposed.",
    "Explicit user limits still apply.",
)
ENGINE_STALE_CUES = (
    "Task-requested reads, including full handoffs, may exceed automatic recall limits",
)

BRIDGE_REQUIRED_CUES = (
    "Automatic exact-source read allowance: ",
    "This limits automatic recall only.",
    "If the task needs more context, you may exceed automatic byte/call limits without user approval.",
    "use inspection_policy:first-fitting-whole/2 when exposed.",
    "Explicit user limits still apply.",
)
BRIDGE_STALE_CUES = (
    "It is not a general optional memory budget; do not run additional optional searches from a fallback or notice turn.",
)

AGY_REQUIRED_CUES = (
    "without asking the user",
    "available_tokens",
    "memory_budget_bytes",
    "first-fitting-whole/2",
)

SYNTHETIC_SESSION = "11111111-1111-4111-8111-111111111111"
SYNTHETIC_TURN = "22222222-2222-4222-8222-222222222222"
SYNTHETIC_AGENT = "33333333-3333-4333-8333-333333333333"
SYNTHETIC_EXEC = "44444444-4444-4444-8444-444444444444"
SYNTHETIC_DELIVERY = "55555555-5555-4555-8555-555555555555"
SYNTHETIC_ATTEMPT = "66666666-6666-4666-8666-666666666666"
SYNTHETIC_RECORD = "77777777-7777-4777-8777-777777777777"

FAKE_CAIRN_CLI = """#!/usr/bin/env python3
import json, pathlib, sys
root = pathlib.Path(__file__).parent
fixture = json.loads((root / "fixture.json").read_text())
args = sys.argv[1:]
op = args[5]
if op == "search":
    result = fixture["search"]
elif op == "history":
    if fixture.get("fail_history"):
        print(json.dumps({"ok": False, "status": fixture["fail_history"]}))
        sys.exit(7)
    result = fixture["history"]
elif op == "pull":
    result = fixture["pull"]
else:
    raise SystemExit("unexpected " + op)
print(json.dumps({"ok": True, "status": "OK", "data": result}))
"""


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def inspect_file(path, *, owner_only=False):
    path = Path(path)
    info = {"path": str(path), "exists": path.exists()}
    if not path.is_absolute():
        info["error"] = "path is not absolute"
        return info, None
    if not path.exists():
        info["error"] = "file does not exist"
        return info, None
    stat = path.stat()
    mask = 0o077 if owner_only else 0o022
    info["uid_ok"] = stat.st_uid == os.getuid()
    info["mode_ok"] = (stat.st_mode & mask) == 0
    raw = path.read_bytes()
    info["sha256"] = sha256_bytes(raw)
    if not info["uid_ok"] or not info["mode_ok"]:
        info["error"] = "owner-only permission check failed" if owner_only else "owner-controlled permission check failed"
    return info, raw


def load_python_module(name, path, raw_bytes=None):
    path = Path(path)
    raw = raw_bytes if raw_bytes is not None else path.read_bytes()
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    module.__dict__["_CAIRN_VALIDATED_SOURCE"] = raw
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def check_cues(text, required_cues, stale_cues):
    missing = [cue for cue in required_cues if cue not in text]
    stale = [cue for cue in stale_cues if cue in text]
    return {
        "cues_current": not missing and not stale,
        "missing_cues": missing,
        "stale_cues": stale,
    }


def snapshot_paths(file_paths, state_dirs):
    """Capture metadata snapshot without reading raw session or memory state files."""
    files = {}
    for item in sorted({str(Path(p)) for p in file_paths if p}):
        p = Path(item)
        if p.exists() and p.is_file():
            st = p.stat()
            files[item] = {
                "mtime_ns": st.st_mtime_ns,
                "size": st.st_size,
                "sha256": sha256_bytes(p.read_bytes()),
            }
        else:
            files[item] = {"exists": False}
    dirs = {}
    for item in sorted({str(Path(d)) for d in state_dirs if d}):
        d = Path(item)
        if d.exists() and d.is_dir():
            st = d.stat()
            entries = {}
            for child in sorted(d.iterdir(), key=lambda c: c.name):
                cst = child.lstat()
                entries[child.name] = {"mtime_ns": cst.st_mtime_ns, "size": cst.st_size}
            dirs[item] = {"mtime_ns": st.st_mtime_ns, "entries": entries}
        else:
            dirs[item] = {"exists": False}
    return {"files": files, "state_dirs": dirs}


def _fixture_payload(note_body, task_body, *, fail_history=None):
    payload = {
        "search": {
            "status": "READY",
            "destination": {"name": "hosted", "allow_local": False},
            "selected": [],
            "omitted": {},
            "index": [
                {
                    "record_id": SYNTHETIC_AGENT,
                    "version": 1,
                    "summary": "Fixture guidance",
                    "body_sha256": sha256_bytes(note_body.encode()),
                    "pull_arguments": {
                        "receipt_id": SYNTHETIC_EXEC,
                        "handle": SYNTHETIC_AGENT,
                        "request_id": SYNTHETIC_ATTEMPT,
                    },
                }
            ],
        },
        "history": {
            "historical": True,
            "record_id": SYNTHETIC_RECORD,
            "versions": [
                {
                    "version": 2,
                    "payload_available": True,
                    "body": task_body,
                    "body_bytes": len(task_body.encode()),
                    "body_sha256": sha256_bytes(task_body.encode()),
                }
            ],
        },
        "pull": {
            "selection": {"record": {"record_id": SYNTHETIC_AGENT, "version": 1, "body": note_body}},
            "credits_remaining": 3,
        },
    }
    if fail_history:
        payload["fail_history"] = fail_history
    return payload


def render_ordinary_recall_fixture(engine_path, engine_bytes, memory_config=None):
    note = "Preserve fixture scope until source verification completes."
    task = "Verify effective recall deployment guidance."
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".git").mkdir()
        cli = root / "cairn"
        cli.write_text(FAKE_CAIRN_CLI)
        cli.chmod(0o700)
        (root / "fixture.json").write_text(json.dumps(_fixture_payload(note, task)))
        base = dict(memory_config) if isinstance(memory_config, dict) else {}
        base.pop("inbox_recall_binding", None)
        harness = base.get("harness", "codex")
        mc = dict(
            base,
            cairn=str(cli),
            socket=str(root / "api.sock"),
            token_file=str(root / "token"),
            repo="fixture",
            harness=harness if harness in ("codex", "claude") else "codex",
            state_dir=str(root / "memory"),
            context_bytes=base.get("context_bytes", 9500) if isinstance(base.get("context_bytes"), int) else 9500,
            recall_mode="agent_tools",
            recall_observations=False,
        )
        engine = load_python_module("cairn_check_ordinary_engine", engine_path, engine_bytes)
        event = {
            "session_id": SYNTHETIC_SESSION,
            "turn_id": SYNTHETIC_TURN,
            "prompt_id": "fixture-prompt",
            "cwd": str(root),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Inspect fixture deployment guidance.",
        }
        result = engine.handle(mc, event)
        text = result.get("hookSpecificOutput", {}).get("additionalContext", "")
        cues = check_cues(text, ENGINE_REQUIRED_CUES, ENGINE_STALE_CUES)
        return dict(
            rendered=bool(text),
            has_remaining_memory_bytes=("remaining_memory_bytes" in text),
            **cues,
        )


def _run_inbox_case(root, bridge_mod, bridge_bytes, engine_bytes, harness, kind, fail_history=None):
    (root / ".git").mkdir(parents=True, exist_ok=True)
    cli = root / "cairn"
    cli.write_text(FAKE_CAIRN_CLI)
    cli.chmod(0o700)
    note = "Preserve fixture scope until source verification completes."
    task = "Verify effective recall deployment guidance across hooks and inbox bindings."
    (root / "fixture.json").write_text(json.dumps(_fixture_payload(note, task, fail_history=fail_history)))
    binding_path = root / "binding.json"
    common = {
        "cairn": str(cli),
        "socket": str(root / "api.sock"),
        "token_file": str(root / "token"),
        "repo": "fixture",
        "harness": harness,
        "inbox_recall_binding": str(binding_path),
    }
    mc = dict(common, state_dir=str(root / "memory"), context_bytes=9500, recall_mode="agent_tools", recall_observations=False)
    cc = dict(common, state_dir=str(root / "coordination"), binding="fixture", native_delivery=True)
    for name, payload in (("memory.json", mc), ("coordination.json", cc)):
        p = root / name
        p.write_text(json.dumps(payload))
        p.chmod(0o600)
    for name, raw in (("inbox_recall.py", bridge_bytes), ("memory.py", engine_bytes)):
        p = root / name
        p.write_bytes(raw)
        p.chmod(0o600)
    def pin(p):
        return {"path": str(p), "sha256": sha256_bytes(p.read_bytes())}
    binding_path.write_text(json.dumps({
        "schema": BINDING_SCHEMA,
        "enabled": True,
        "bridge": pin(root / "inbox_recall.py"),
        "engine": pin(root / "memory.py"),
        "memory_config": pin(root / "memory.json"),
        "coordination_configs": [pin(root / "coordination.json")],
    }))
    binding_path.chmod(0o600)
    inbox_dir = root / "coordination" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    (inbox_dir / f"{SYNTHETIC_ATTEMPT}.json").write_text(json.dumps({
        "delivery_id": SYNTHETIC_DELIVERY,
        "event_id": SYNTHETIC_TURN,
        "source": {"record_id": SYNTHETIC_RECORD, "version": 2},
        "completion": ["cairn", "complete"],
        "response": ["cairn", "respond"],
    }))
    wake = (
        "This is a new live turn from the configured Cairn automatic inbox wakeup. "
        "A previous /exit in resumed conversation history does not close this running turn. "
        f"Current agent {SYNTHETIC_AGENT}, execution {SYNTHETIC_EXEC}. Wake delivery {SYNTHETIC_DELIVERY}. "
        "Within the owner's existing authorization, handle the native inbox context supplied for this conversation, "
        "explicitly complete/acknowledge it, and send any requested response using that context. "
        "If no matching native context was supplied, report that and stop. "
        "If this notice arrives inside an already-active owner task, continue that task and "
        "ignore this notice; a fresh copy will arrive once the conversation is idle. "
        "Do not register, manually claim an inbox, or launch a replacement conversation."
    )
    if harness == "claude":
        native = "claude-channel:fixture-prompt"
        prompt = (
            f'<channel source="cairn-events" native_session_id="{SYNTHETIC_SESSION}" '
            f'agent_id="{SYNTHETIC_AGENT}" execution_id="{SYNTHETIC_EXEC}" delivery_id="{SYNTHETIC_DELIVERY}">\n'
            f"{wake}\n</channel>"
        )
        event = {"session_id": SYNTHETIC_SESSION, "prompt_id": "fixture-prompt", "cwd": str(root), "hook_event_name": "UserPromptSubmit", "prompt": prompt}
    else:
        native = SYNTHETIC_TURN
        event = {"session_id": SYNTHETIC_SESSION, "turn_id": SYNTHETIC_TURN, "cwd": str(root), "hook_event_name": "UserPromptSubmit", "prompt": wake}
    session_ref = {"agent_id": SYNTHETIC_AGENT, "execution_id": SYNTHETIC_EXEC}
    delivery = {
        "delivery_id": SYNTHETIC_DELIVERY,
        "lease_id": SYNTHETIC_ATTEMPT,
        "state": "leased",
        "event": {"event_id": SYNTHETIC_TURN, "kind": kind, "ref": {"record_id": SYNTHETIC_RECORD, "version": 2}, "from": "agent/sender"},
    }
    state = {
        "workspace": str(root),
        "agent": dict(session_ref, native_session_id=SYNTHETIC_SESSION, metadata={"harness": harness, "workspace": str(root)}),
        "inbox_attempt": {"attempt_id": SYNTHETIC_ATTEMPT, "session": session_ref, "native_turn_id": native, "delivery": delivery},
    }
    obs = {"event": "UserPromptSubmit", "native_turn_id": native}
    return bridge_mod.output(cc, event, obs, state, "Cairn control.", revalidate=lambda _a: None, outer_deadline=time.monotonic() + 5)


def render_inbox_bridge_fixtures(bridge_path, bridge_bytes, engine_path, engine_bytes, harness="codex"):
    harness = harness if harness in ("codex", "claude") else "codex"
    bridge_mod = load_python_module("cairn_check_inbox_bridge", bridge_path, bridge_bytes)
    results = {}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        admitted_text = _run_inbox_case(base / "admitted_request", bridge_mod, bridge_bytes, engine_bytes, harness, "request")
        results["admitted_request"] = dict(
            rendered=bool(admitted_text),
            status_present=('"status":"task_source"' in admitted_text),
            **check_cues(admitted_text, ENGINE_REQUIRED_CUES, ENGINE_STALE_CUES),
        )
        notice_text = _run_inbox_case(base / "notice_response", bridge_mod, bridge_bytes, engine_bytes, harness, "notice")
        results["notice_response"] = dict(
            rendered=bool(notice_text),
            status_present=('"status":"required_only"' in notice_text),
            **check_cues(notice_text, BRIDGE_REQUIRED_CUES, BRIDGE_STALE_CUES),
        )
        fallback_text = _run_inbox_case(
            base / "source_fallback",
            bridge_mod,
            bridge_bytes,
            engine_bytes,
            harness,
            "request",
            fail_history="API_UNAVAILABLE",
        )
        results["source_fallback"] = dict(
            rendered=bool(fallback_text),
            status_present=('"status":"source_temporarily_unavailable_read_exact_source"' in fallback_text),
            **check_cues(fallback_text, BRIDGE_REQUIRED_CUES, BRIDGE_STALE_CUES),
        )
    results["all_cues_current"] = all(
        results[k]["cues_current"] and results[k]["status_present"]
        for k in ("admitted_request", "notice_response", "source_fallback")
    )
    return results


def parse_command_tokens(cmd_str):
    try:
        argv = shlex.split(cmd_str)
    except ValueError:
        return None
    if not argv:
        return None
    route_path = None
    for i, token in enumerate(argv[:-1]):
        if token.endswith("recall_dispatch.py") and "--route" in argv[i:]:
            r_idx = argv.index("--route", i)
            if r_idx + 1 < len(argv):
                route_path = argv[r_idx + 1]
            if "--" in argv[r_idx:]:
                sep = argv.index("--", r_idx)
                argv = argv[sep + 1:]
            break
    script_path = None
    config_path = None
    is_coordination = False
    for i, token in enumerate(argv):
        if token.endswith(".py") and script_path is None:
            script_path = token
            if i + 1 < len(argv) and argv[i + 1] == "hook":
                is_coordination = True
        if token == "--config" and i + 1 < len(argv):
            config_path = argv[i + 1]
    if not script_path or not config_path:
        return None
    return {
        "kind": "coordination" if is_coordination else "memory",
        "script": script_path,
        "config": config_path,
        "route": route_path,
        "raw_command": cmd_str,
    }


def inspect_ordinary_hook(settings_path, engine_render_cache=None):
    settings_path = Path(settings_path)
    entry = {
        "settings_path": str(settings_path),
        "account_home": str(settings_path.parent),
        "exists": settings_path.exists(),
        "memory_hook": None,
        "coordination_hook": None,
        "dispatch_route": None,
        "cues_current": False,
        "issues": [],
    }
    if not settings_path.exists():
        entry["issues"].append(f"missing hook settings file: {settings_path}")
        return entry, set(), set()
    tracked_files = {str(settings_path)}
    tracked_dirs = set()
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, ValueError) as exc:
        entry["issues"].append(f"unreadable hook settings file {settings_path}: {type(exc).__name__}")
        return entry, tracked_files, tracked_dirs
    hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
    memory_cmd = None
    coord_cmd = None
    for event_name in ("UserPromptSubmit", "SessionStart"):
        for group in hooks.get(event_name, []):
            for h in group.get("hooks", []) if isinstance(group, dict) else []:
                cmd = h.get("command", "") if isinstance(h, dict) else ""
                parsed = parse_command_tokens(cmd)
                if not parsed:
                    continue
                if parsed["kind"] == "memory" and memory_cmd is None:
                    memory_cmd = parsed
                elif parsed["kind"] == "coordination" and coord_cmd is None:
                    coord_cmd = parsed
    if coord_cmd:
        c_info, c_raw = inspect_file(coord_cmd["config"])
        tracked_files.add(coord_cmd["config"])
        tracked_files.add(coord_cmd["script"])
        coord_cfg = json.loads(c_raw) if c_raw else {}
        if isinstance(coord_cfg.get("state_dir"), str):
            tracked_dirs.add(coord_cfg["state_dir"])
        entry["coordination_hook"] = {
            "script": coord_cmd["script"],
            "config_path": coord_cmd["config"],
            "config_sha256": c_info.get("sha256"),
            "binding_name": coord_cfg.get("binding"),
            "inbox_recall_binding": coord_cfg.get("inbox_recall_binding"),
            "config_home": coord_cfg.get("config_home"),
        }
    else:
        entry["issues"].append(f"no coordination hook found in {settings_path}")
    if not memory_cmd:
        entry["issues"].append(f"no memory recall hook found in {settings_path}")
        return entry, tracked_files, tracked_dirs
    return _populate_memory_hook(entry, memory_cmd, tracked_files, tracked_dirs, engine_render_cache)


def _populate_memory_hook(entry, memory_cmd, tracked_files, tracked_dirs, engine_render_cache):
    tracked_files.add(memory_cmd["script"])
    tracked_files.add(memory_cmd["config"])
    eng_info, eng_raw = inspect_file(memory_cmd["script"])
    cfg_info, cfg_raw = inspect_file(memory_cmd["config"])
    mem_cfg = json.loads(cfg_raw) if cfg_raw else {}
    if isinstance(mem_cfg.get("state_dir"), str):
        tracked_dirs.add(mem_cfg["state_dir"])
    render_res = None
    if eng_raw is not None and not eng_info.get("error"):
        cache_key = (eng_info["sha256"], mem_cfg.get("harness", "codex"))
        if engine_render_cache is not None and cache_key in engine_render_cache:
            render_res = engine_render_cache[cache_key]
        else:
            render_res = render_ordinary_recall_fixture(memory_cmd["script"], eng_raw, mem_cfg)
            if engine_render_cache is not None:
                engine_render_cache[cache_key] = render_res
    else:
        entry["issues"].append(f"ordinary hook engine error ({memory_cmd['script']}): {eng_info.get('error')}")
    if cfg_info.get("error"):
        entry["issues"].append(f"ordinary hook config error ({memory_cmd['config']}): {cfg_info.get('error')}")
    cues_ok = bool(render_res and render_res.get("cues_current"))
    if render_res and not cues_ok:
        entry["issues"].append(f"ordinary hook engine has stale or missing recall cue: {memory_cmd['script']}")
    entry["memory_hook"] = {
        "engine_path": memory_cmd["script"],
        "engine_sha256": eng_info.get("sha256"),
        "config_path": memory_cmd["config"],
        "config_sha256": cfg_info.get("sha256"),
        "harness": mem_cfg.get("harness"),
        "recall_mode": mem_cfg.get("recall_mode"),
        "inbox_recall_binding": mem_cfg.get("inbox_recall_binding"),
        "ordinary_recall_fixture": render_res,
    }
    if memory_cmd.get("route"):
        r_path = Path(memory_cmd["route"])
        tracked_files.add(str(r_path))
        route_entry = {"route_path": str(r_path), "active": r_path.exists()}
        if r_path.exists():
            r_data = json.loads(r_path.read_text())
            cand = parse_command_tokens(shlex.join(r_data.get("command", [])))
            if cand:
                tracked_files.add(cand["script"])
                c_eng_info, c_eng_raw = inspect_file(cand["script"])
                c_render = render_ordinary_recall_fixture(cand["script"], c_eng_raw, mem_cfg) if c_eng_raw else None
                route_entry["candidate_engine"] = cand["script"]
                route_entry["candidate_sha256"] = c_eng_info.get("sha256")
                route_entry["candidate_cues_current"] = bool(c_render and c_render.get("cues_current"))
                if not route_entry["candidate_cues_current"]:
                    entry["issues"].append(f"dispatch candidate engine has stale recall cue: {cand['script']}")
                    cues_ok = False
        entry["dispatch_route"] = route_entry
    entry["cues_current"] = cues_ok and not entry["issues"]
    return entry, tracked_files, tracked_dirs


def inspect_agy_instructions(path):
    path = Path(path)
    info = {"path": str(path), "present": path.exists(), "cues_current": False, "issues": []}
    if not path.exists():
        info["issues"].append(f"missing Agy global instructions: {path}")
        return info, set()
    raw = path.read_bytes()
    info["sha256"] = sha256_bytes(raw)
    text = raw.decode("utf-8", errors="replace")
    cues = check_cues(text, AGY_REQUIRED_CUES, ENGINE_STALE_CUES)
    info.update(cues)
    if not cues["cues_current"]:
        info["issues"].append(f"Agy global instructions missing or stale override guidance: {path}")
    return info, {str(path)}


def _check_pinned_spec(spec, label, issues, tracked_files):
    if not isinstance(spec, dict) or not isinstance(spec.get("path"), str) or not isinstance(spec.get("sha256"), str):
        issues.append(f"{label}: invalid descriptor")
        return {"valid": False}, None
    tracked_files.add(spec["path"])
    info, raw = inspect_file(spec["path"], owner_only=False)
    expected = spec["sha256"]
    actual = info.get("sha256")
    hash_match = (actual == expected) and not info.get("error")
    if info.get("error"):
        issues.append(f"{label} ({spec['path']}): {info['error']}")
    elif actual != expected:
        issues.append(f"{label} ({spec['path']}): sha256 mismatch (expected {expected}, got {actual})")
    return {
        "path": spec["path"],
        "expected_sha256": expected,
        "actual_sha256": actual,
        "hash_match": hash_match,
        "uid_ok": info.get("uid_ok", False),
        "mode_ok": info.get("mode_ok", False),
    }, raw


def inspect_inbox_binding(binding_path, hooks_by_home=None, bridge_render_cache=None):
    binding_path = Path(binding_path)
    tracked_files = {str(binding_path)}
    tracked_dirs = set()
    issues = []
    m_info, m_raw = inspect_file(binding_path, owner_only=True)
    result = {
        "binding_path": str(binding_path),
        "manifest_sha256": m_info.get("sha256"),
        "enabled": None,
        "pins_valid": False,
        "contract_valid": False,
        "bridge": None,
        "engine": None,
        "memory_config": None,
        "coordination_configs": [],
        "fixtures": None,
        "cues_current": False,
        "issues": issues,
    }
    if m_info.get("error") or m_raw is None:
        issues.append(f"binding manifest {binding_path}: {m_info.get('error', 'unreadable')}")
        return result, tracked_files, tracked_dirs
    try:
        manifest = json.loads(m_raw)
    except ValueError as exc:
        issues.append(f"binding manifest {binding_path}: invalid JSON ({type(exc).__name__})")
        return result, tracked_files, tracked_dirs
    if not isinstance(manifest, dict) or manifest.get("schema") != BINDING_SCHEMA or type(manifest.get("enabled")) is not bool:
        issues.append(f"binding manifest {binding_path}: invalid schema or enabled flag")
        return result, tracked_files, tracked_dirs
    result["enabled"] = manifest["enabled"]
    if not manifest["enabled"]:
        issues.append(f"binding manifest {binding_path}: binding is disabled")
    return _finish_inbox_binding(result, manifest, hooks_by_home or {}, tracked_files, tracked_dirs, bridge_render_cache)


def _finish_inbox_binding(result, manifest, hooks_by_home, tracked_files, tracked_dirs, bridge_render_cache):
    issues = result["issues"]
    b_spec, b_raw = _check_pinned_spec(manifest.get("bridge"), "bridge", issues, tracked_files)
    e_spec, e_raw = _check_pinned_spec(manifest.get("engine"), "engine", issues, tracked_files)
    m_spec, m_raw = _check_pinned_spec(manifest.get("memory_config"), "memory_config", issues, tracked_files)
    mem_cfg = json.loads(m_raw) if m_raw else {}
    if isinstance(mem_cfg.get("state_dir"), str):
        tracked_dirs.add(mem_cfg["state_dir"])
    coord_specs = manifest.get("coordination_configs")
    coord_entries = []
    coord_cfgs = []
    if not isinstance(coord_specs, list) or not 1 <= len(coord_specs) <= 8:
        issues.append("coordination_configs must be 1..8 entries")
    else:
        for idx, item in enumerate(coord_specs):
            c_spec, c_raw = _check_pinned_spec(item, f"coordination_configs[{idx}]", issues, tracked_files)
            c_cfg = json.loads(c_raw) if c_raw else {}
            if isinstance(c_cfg.get("state_dir"), str):
                tracked_dirs.add(c_cfg["state_dir"])
            home = str(Path(c_cfg["config_home"]).resolve()) if c_cfg.get("config_home") else None
            hook_entry = hooks_by_home.get(home) if home else None
            ord_eng_sha = hook_entry.get("memory_hook", {}).get("engine_sha256") if hook_entry and hook_entry.get("memory_hook") else None
            c_spec.update(
                binding_name=c_cfg.get("binding"),
                harness=c_cfg.get("harness"),
                config_home=home,
                ordinary_hook_present=bool(hook_entry and hook_entry.get("exists")),
                ordinary_engine_sha256=ord_eng_sha,
                bound_engine_matches_ordinary=(ord_eng_sha == e_spec.get("actual_sha256")) if ord_eng_sha else None,
            )
            coord_entries.append(c_spec)
            coord_cfgs.append(c_cfg)
    pins_valid = bool(
        b_spec.get("hash_match")
        and e_spec.get("hash_match")
        and m_spec.get("hash_match")
        and coord_entries
        and all(c.get("hash_match") for c in coord_entries)
    )
    result.update(bridge=b_spec, engine=e_spec, memory_config=m_spec, coordination_configs=coord_entries, pins_valid=pins_valid)
    if b_raw and e_raw:
        b_text = b_raw.decode("utf-8", errors="replace")
        e_text = e_raw.decode("utf-8", errors="replace")
        b_cues = check_cues(b_text, BRIDGE_REQUIRED_CUES, BRIDGE_STALE_CUES)
        e_cues = check_cues(e_text, ENGINE_REQUIRED_CUES, ENGINE_STALE_CUES)
        b_spec.update(b_cues)
        e_spec.update(e_cues)
        if not b_cues["cues_current"]:
            issues.append(f"bound inbox bridge has stale or missing fallback cue: {b_spec.get('path')}")
        if not e_cues["cues_current"]:
            issues.append(f"bound memory engine has stale or missing recall cue: {e_spec.get('path')}")
        _validate_and_render_binding(result, b_spec, b_raw, e_spec, e_raw, mem_cfg, coord_cfgs, bridge_render_cache)
    return result, tracked_files, tracked_dirs


def _validate_and_render_binding(result, b_spec, b_raw, e_spec, e_raw, mem_cfg, coord_cfgs, bridge_render_cache):
    issues = result["issues"]
    if result["pins_valid"]:
        try:
            bridge_mod = load_python_module("cairn_check_validate_bridge", b_spec["path"], b_raw)
            for c_cfg in [mem_cfg, *coord_cfgs]:
                bridge_mod.validate_binding(c_cfg, require_enabled=bool(result["enabled"]))
            result["contract_valid"] = True
        except Exception as exc:
            issues.append(f"validate_binding failed: {exc}")
    harness = mem_cfg.get("harness", "codex")
    cache_key = (b_spec.get("actual_sha256"), e_spec.get("actual_sha256"), harness)
    try:
        if bridge_render_cache is not None and cache_key in bridge_render_cache:
            fixtures = bridge_render_cache[cache_key]
        else:
            fixtures = render_inbox_bridge_fixtures(b_spec["path"], b_raw, e_spec["path"], e_raw, harness=harness)
            if bridge_render_cache is not None:
                bridge_render_cache[cache_key] = fixtures
        result["fixtures"] = fixtures
        if not fixtures.get("all_cues_current"):
            for case_name in ("admitted_request", "notice_response", "source_fallback"):
                if not fixtures.get(case_name, {}).get("cues_current"):
                    issues.append(f"rendered fixture {case_name} has stale or missing recall cue")
        result["cues_current"] = bool(
            b_spec.get("cues_current") and e_spec.get("cues_current") and fixtures.get("all_cues_current")
        )
    except Exception as exc:
        issues.append(f"fixture render failed: {exc}")


def default_hook_settings_paths(home):
    home = Path(home)
    return [
        home / ".codex" / "hooks.json",
        home / ".codex-harm" / "hooks.json",
        home / ".claude" / "settings.json",
        home / ".claude-harm" / "settings.json",
    ]


def _collect_precheck_targets(hook_settings_paths, explicit_bindings, agy_path):
    files = set()
    state_dirs = set()
    discovered_bindings = [str(Path(b)) for b in explicit_bindings]
    for s_path in hook_settings_paths:
        s_path = Path(s_path)
        files.add(str(s_path))
        if not s_path.exists():
            continue
        try:
            data = json.loads(s_path.read_text())
        except (OSError, ValueError):
            continue
        hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
        for event_name in ("UserPromptSubmit", "SessionStart"):
            for group in hooks.get(event_name, []):
                for h in group.get("hooks", []) if isinstance(group, dict) else []:
                    parsed = parse_command_tokens(h.get("command", "") if isinstance(h, dict) else "")
                    if not parsed:
                        continue
                    files.add(parsed["script"])
                    files.add(parsed["config"])
                    if parsed.get("route"):
                        files.add(parsed["route"])
                    cfg_p = Path(parsed["config"])
                    if cfg_p.exists():
                        try:
                            cfg = json.loads(cfg_p.read_text())
                            if isinstance(cfg.get("state_dir"), str):
                                state_dirs.add(cfg["state_dir"])
                            b_ref = cfg.get("inbox_recall_binding")
                            if isinstance(b_ref, str) and b_ref not in discovered_bindings:
                                discovered_bindings.append(b_ref)
                        except (OSError, ValueError):
                            pass
    for b_str in list(discovered_bindings):
        b_path = Path(b_str)
        files.add(str(b_path))
        if not b_path.exists():
            continue
        try:
            m = json.loads(b_path.read_text())
            for key in ("bridge", "engine", "memory_config"):
                if isinstance(m.get(key), dict) and isinstance(m[key].get("path"), str):
                    files.add(m[key]["path"])
            for item in m.get("coordination_configs", []) if isinstance(m.get("coordination_configs"), list) else []:
                if isinstance(item, dict) and isinstance(item.get("path"), str):
                    files.add(item["path"])
        except (OSError, ValueError):
            pass
    if agy_path:
        files.add(str(Path(agy_path)))
    return files, state_dirs, discovered_bindings


def check_deployment(
    *,
    home=None,
    hook_settings=None,
    bindings=None,
    agy_instructions=None,
    skip_agy=False,
    live_observed=False,
):
    home = Path(home) if home is not None else Path.home()
    settings_list = [Path(p) for p in hook_settings] if hook_settings is not None else default_hook_settings_paths(home)
    explicit_bindings = [Path(b) for b in (bindings or ())]
    agy_path = None if skip_agy else (Path(agy_instructions) if agy_instructions is not None else home / ".gemini/config/AGENTS.md")
    pre_files, pre_dirs, discovered_bindings = _collect_precheck_targets(settings_list, explicit_bindings, agy_path)
    for f_str in list(pre_files):
        fp = Path(f_str)
        if fp.suffix == ".json" and fp.exists():
            try:
                obj = json.loads(fp.read_text())
                if isinstance(obj, dict) and isinstance(obj.get("state_dir"), str):
                    pre_dirs.add(obj["state_dir"])
            except (OSError, ValueError):
                pass
    before_snapshot = snapshot_paths(pre_files, pre_dirs)
    engine_cache = {}
    bridge_cache = {}
    ordinary_entries = []
    hooks_by_home = {}
    all_issues = []
    missing_targets = []
    partial_reasons = []
    for s_path in settings_list:
        entry, t_files, t_dirs = inspect_ordinary_hook(s_path, engine_render_cache=engine_cache)
        pre_files.update(t_files)
        pre_dirs.update(t_dirs)
        ordinary_entries.append(entry)
        hooks_by_home[str(Path(entry["account_home"]).resolve())] = entry
        if not entry["exists"]:
            missing_targets.append(str(s_path))
            partial_reasons.append(f"missing hook settings file: {s_path}")
        all_issues.extend(entry["issues"])
    return _finalize_deployment_report(
        ordinary_entries,
        hooks_by_home,
        discovered_bindings,
        agy_path,
        pre_files,
        pre_dirs,
        before_snapshot,
        bridge_cache,
        missing_targets,
        partial_reasons,
        all_issues,
        live_observed=live_observed,
    )


def _finalize_deployment_report(
    ordinary_entries,
    hooks_by_home,
    discovered_bindings,
    agy_path,
    pre_files,
    pre_dirs,
    before_snapshot,
    bridge_cache,
    missing_targets,
    partial_reasons,
    all_issues,
    *,
    live_observed,
):
    binding_entries = []
    disabled_bindings = []
    for b_str in discovered_bindings:
        b_res, t_files, t_dirs = inspect_inbox_binding(b_str, hooks_by_home=hooks_by_home, bridge_render_cache=bridge_cache)
        pre_files.update(t_files)
        pre_dirs.update(t_dirs)
        binding_entries.append(b_res)
        if b_res["enabled"] is False:
            disabled_bindings.append(b_str)
            partial_reasons.append(f"binding manifest {b_str}: binding is disabled")
        all_issues.extend(b_res["issues"])
    agy_entry = None
    if agy_path is not None:
        agy_entry, t_files = inspect_agy_instructions(agy_path)
        pre_files.update(t_files)
        if not agy_entry["present"]:
            missing_targets.append(str(agy_path))
            partial_reasons.append(f"missing Agy global instructions: {agy_path}")
        all_issues.extend(agy_entry["issues"])
    after_snapshot = snapshot_paths(pre_files, pre_dirs)
    read_only_ok = (before_snapshot == after_snapshot)
    if not read_only_ok:
        all_issues.append("live state changed during deployment verification")
    coverage_complete = not missing_targets and not disabled_bindings and bool(ordinary_entries or binding_entries)
    installed_verified = (
        read_only_ok
        and coverage_complete
        and not all_issues
        and all(e["cues_current"] for e in ordinary_entries)
        and all(b["pins_valid"] and b["contract_valid"] and b["cues_current"] and b["enabled"] for b in binding_entries)
        and (agy_entry is None or agy_entry["cues_current"])
    )
    status = "ok" if installed_verified else ("partial" if (partial_reasons and not [i for i in all_issues if i not in partial_reasons]) else "stale_or_invalid")
    return {
        "schema": SCHEMA,
        "ok": installed_verified,
        "status": status,
        "installed_verified": installed_verified,
        "live_observed": bool(live_observed),
        "read_only_verified": read_only_ok,
        "tested_vs_live_observed": {
            "installed_verified": installed_verified,
            "fixture_renders_verified": all(e["cues_current"] for e in ordinary_entries if e["exists"])
            and all(b["cues_current"] for b in binding_entries),
            "pins_verified": all(b["pins_valid"] and b["contract_valid"] for b in binding_entries if b["enabled"]),
            "live_native_turn_observed": bool(live_observed),
            "repo_head_is_not_deployment_proof": True,
            "active_grants_rewritten": False,
            "work_replayed": False,
            "claim_boundary": (
                "Verifies installed hook/binding pins and renders representative recall cues in disposable fixtures; "
                "does not prove a subsequent live native turn observed the updated wording unless separately recorded."
            ),
        },
        "coverage": {
            "complete": coverage_complete,
            "checked_ordinary_hooks": len([e for e in ordinary_entries if e["exists"]]),
            "checked_bindings": [b["binding_path"] for b in binding_entries if b["enabled"]],
            "disabled_bindings": disabled_bindings,
            "missing_targets": missing_targets,
            "partial_reasons": partial_reasons,
        },
        "ordinary_hooks": ordinary_entries,
        "inbox_bindings": binding_entries,
        "agy_instructions": agy_entry,
        "issues": all_issues,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=Path.home(), help="User home directory for default hook/Agy paths")
    parser.add_argument("--hook-settings", action="append", type=Path, help="Explicit hook settings JSON path (repeatable)")
    parser.add_argument("--binding", action="append", type=Path, help="Explicit cairn.inbox-recall-binding/1 manifest path (repeatable)")
    parser.add_argument("--agy-instructions", type=Path, help="Explicit Agy global AGENTS.md path")
    parser.add_argument("--skip-agy", action="store_true", help="Skip Agy global instructions check")
    parser.add_argument("--live-observed", action="store_true", help="Mark that a live native turn was separately observed")
    parser.add_argument("--compact", action="store_true", help="Emit single-line compact JSON")
    args = parser.parse_args(argv)
    report = check_deployment(
        home=args.home,
        hook_settings=args.hook_settings,
        bindings=args.binding,
        agy_instructions=args.agy_instructions,
        skip_agy=args.skip_agy,
        live_observed=args.live_observed,
    )
    if args.compact:
        print(json.dumps(report, separators=(",", ":")))
    else:
        print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
