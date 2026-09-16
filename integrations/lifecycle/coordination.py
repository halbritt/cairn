#!/usr/bin/env python3
"""Associate native sessions with Cairn UUIDs; watch their host process liveness."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid


class CoordinationError(Exception):
    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code


def process_reference(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    fields = raw.rsplit(")", 1)[1].split()
    return dict(pid=pid, start=int(fields[19]), state=fields[0], parent=int(fields[1]),
                boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip())


def process_alive(ref):
    try:
        current = process_reference(ref["pid"])
    except (FileNotFoundError, ProcessLookupError):
        return False
    return (current["state"] not in ("Z", "X") and
            all(current[k] == ref[k] for k in ("pid", "start", "boot")))


def same_process(a, b):
    return all(a[k] == b[k] for k in ("pid", "start", "boot"))


def owner_process(config, event):
    """Plugins supply their own PID; command hooks find a named native ancestor."""
    declared = event.get("host_pid")
    if declared is not None and (type(declared) is not int or declared < 2):
        raise CoordinationError("INVALID_HOST", "invalid native process ID")
    pid = os.getppid()
    for _ in range(128):
        if pid < 2:
            break
        ref = process_reference(pid)
        name = Path(f"/proc/{pid}/comm").read_text().strip()
        if pid == declared or (declared is None and name in config.get("process_names", [])):
            return ref
        pid = ref["parent"]
    raise CoordinationError("INVALID_HOST", "native process is not an observed ancestor")


def normalize(config, event, event_name=None):
    harness = config["harness"]
    name = event_name or event.get("hook_event_name")
    if harness == "agy":
        native = event.get("conversationId")
        roots = event.get("workspacePaths")
        cwd = roots[0] if isinstance(roots, list) and roots else None
        model = event.get("modelName", "")
    else:
        native, cwd, model = event.get("session_id"), event.get("cwd"), event.get("model", "")
    if not isinstance(native, str) or not native.strip() or len(native) > 256 or any(ord(c) < 32 for c in native):
        raise CoordinationError("INVALID_HOST", "native conversation ID required")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
        raise CoordinationError("INVALID_HOST", "native workspace must be an existing absolute directory")
    if not isinstance(model, str) or len(model) > 256:
        raise CoordinationError("INVALID_HOST", "invalid observed model")
    phases = {"SessionStart": "start", "UserPromptSubmit": "busy", "PreInvocation": "busy",
              "TurnStart": "busy", "Stop": "idle", "TurnEnd": "idle", "SessionEnd": "leave"}
    if name not in phases:
        raise CoordinationError("INVALID_HOST", "unsupported coordination hook event")
    phase = phases[name]
    if harness == "agy" and name == "Stop" and event.get("fullyIdle") is not True:
        phase = "busy"
    workspace = str(Path(cwd).resolve())
    root = Path(workspace)
    for parent in [root, *root.parents]:
        if (parent / ".git").exists():
            root = parent
            break
    return dict(native_id=native, workspace=workspace, project=root.name or workspace,
                observed_model=model, phase=phase, event=name)


def write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        pending = Path(file.name)
        try:
            json.dump(state, file, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
            file.close()
            pending.replace(path)
        finally:
            pending.unlink(missing_ok=True)


@contextmanager
def session_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path.with_suffix(".lock")), os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CoordinationError("SESSION_BUSY", "another lifecycle operation is active") from exc
        yield


def call(config, operation, request, timeout=4):
    command = [config["cairn"], "agent", "--socket", config["socket"],
               "--token-file", config["token_file"], operation]
    result = subprocess.run(command, input=json.dumps(request), capture_output=True, text=True, timeout=timeout)
    try:
        response = json.loads(result.stdout)
    except ValueError as exc:
        raise CoordinationError("API_UNAVAILABLE", "Cairn did not return a JSON response") from exc
    if result.returncode or not response.get("ok"):
        raise CoordinationError(response.get("status", "API_UNAVAILABLE"), "Cairn refused the lifecycle operation")
    return response.get("data")


def session_ref(agent):
    return {k: agent[k] for k in ("agent_id", "execution_id")}


def registration(config, observation, metadata=None):
    return dict(request_id=str(uuid.uuid4()), repo=config["repo"], binding=config["binding"],
                native_session_id=observation["native_id"], metadata=metadata or dict(
                    harness=config["harness"], model=config.get("model", ""),
                    observed_model=observation["observed_model"], project=observation["project"],
                    workspace=observation["workspace"], state="idle" if observation["phase"] == "start" else observation["phase"],
                    delivery_mode="existing-session"))


def state_path(config, native):
    key = hashlib.sha256((config["binding"] + "\0" + native).encode()).hexdigest()
    return Path(config["state_dir"]) / (key + ".json")


def heartbeat(config, state):
    return call(config, "agent-heartbeat", session_ref(state["agent"]))


def finish_presence(config, state, path, timeout=4):
    # Persist the logical end before contacting the API. A long-lived gateway
    # process must not cause its finished conversation to be revived on recovery.
    state['ending'] = True
    write_state(path, state)
    if not state.get('agent'):
        state['agent'] = call(config, 'agent-register', state['registration'], timeout=timeout)
        write_state(path, state)
    try:
        call(config, 'agent-leave', session_ref(state['agent']), timeout=timeout)
    except CoordinationError as exc:
        if exc.code not in ('STALE_SESSION', 'NOT_FOUND'):
            raise
    state['retired'] = True
    write_state(path, state)


def handle(config, event, event_name=None):
    if any(os.environ.get(k) == "1" for k in ("CAIRN_COORDINATION_DISABLED", "CAIRN_LIFECYCLE_DISABLED", "CAIRN_LIFECYCLE_CHILD")) or os.environ.get("CAIRN_WAKE_CONTEXT"):
        return {}
    if config.get("config_home") and config["harness"] in ("codex", "claude"):
        variable = "CODEX_HOME" if config["harness"] == "codex" else "CLAUDE_CONFIG_DIR"
        active_home = Path(os.environ.get(variable, str(Path.home() / ("." + config["harness"])))).resolve()
        if active_home != Path(config["config_home"]).resolve():
            return {}  # Merged config layers can include another account's hooks.
    observation = normalize(config, event, event_name)
    workspace = Path(observation["workspace"])
    if any((p / name).exists() for p in [workspace, *workspace.parents]
           for name in (".cairn-no-memory", ".cairn-no-coordination")):
        return {}
    process = owner_process(config, event)
    path = state_path(config, observation["native_id"])
    with session_lock(path):
        state = json.loads(path.read_text()) if path.exists() else {}
        if state.get('ending') and not state.get('retired'):
            finish_presence(config, state, path)
        if state and not same_process(process, state["process"]) and process_alive(state["process"]) and not state.get("retired"):
            raise CoordinationError("SESSION_BUSY", "this conversation is associated with another live process")
        if observation["phase"] == "leave":
            if state and same_process(process, state["process"]) and not state.get('retired'):
                finish_presence(config, state, path, timeout=1.5)
            return {}
        if not state or state.get("retired") or not same_process(process, state["process"]):
            retained = None
            if state.get("agent"):
                page = call(config, "agent-directory", dict(repo=config["repo"], agent_id=state["agent"]["agent_id"], include_offline=True))
                if page["agents"]:
                    retained = dict(page["agents"][0]["metadata"])
                    retained.update(harness=config["harness"], model=config.get("model", ""))
                    if retained["workspace"] != observation["workspace"]:
                        retained.update(project=observation["project"], workspace=observation["workspace"])
                        retained.pop("project_aliases", None)
                    if observation["observed_model"]:
                        retained["observed_model"] = observation["observed_model"]
                    retained["state"] = "idle" if observation["phase"] == "start" else observation["phase"]
            state = dict(schema="cairn.native-session/1", process=process,
                         registration=registration(config, observation, retained), workspace=observation["workspace"])
            # Retain the retry UUID before the API call, including across crashes.
            write_state(path, state)
        if not state.get("agent"):
            state["agent"] = call(config, "agent-register", state["registration"])
            write_state(path, state)
        else:
            try:
                state["agent"] = heartbeat(config, state)
            except CoordinationError as exc:
                if exc.code != "STALE_SESSION":
                    raise
                page = call(config, "agent-directory", dict(repo=config["repo"], agent_id=state["agent"]["agent_id"], include_offline=True))
                current = page["agents"][0] if page["agents"] else None
                # Restore can invalidate an otherwise unchanged execution. A
                # real hook may resume it; a watcher never replaces executions.
                if current is None or current["execution_id"] != state["agent"]["execution_id"] or current["stopped"]:
                    raise
                state["registration"] = registration(config, observation, current["metadata"])
                state.pop("agent")
                write_state(path, state)
                state["agent"] = call(config, "agent-register", state["registration"])
                write_state(path, state)
        current = state["agent"]
        metadata = dict(current["metadata"])
        if observation["workspace"] != state["workspace"]:
            metadata.update(project=observation["project"], workspace=observation["workspace"])
            metadata.pop("project_aliases", None)
        if observation["observed_model"]:
            metadata["observed_model"] = observation["observed_model"]
        if observation["phase"] in ("busy", "idle"):
            metadata["state"] = observation["phase"]
        if metadata != current["metadata"]:
            state["agent"] = call(config, "agent-context", dict(request_id=str(uuid.uuid4()),
                session=session_ref(current), expected_revision=current["context_revision"], metadata=metadata))
        state["workspace"] = observation["workspace"]
        write_state(path, state)
        if observation["phase"] == "idle":
            return {}
        agent = state["agent"]
        message = (f"Cairn session {agent['display_name']} has inbox {agent['inbox']}. "
                   f"Use agent UUID {agent['agent_id']} and execution UUID {agent['execution_id']} "
                   f"with the existing profile token at {config['token_file']} for session inbox work. "
                   "Use cairn agents context to maintain a concise selected task description; "
                   "harness/model/project are metadata. Ordinary memory keeps its existing profile. "
                   "Presence is maintained by the host watcher. Native message delivery is not installed yet.")
        if config["harness"] == "agy":
            return {"injectSteps": [{"ephemeralMessage": message}]}
        return {"hookSpecificOutput": {"hookEventName": observation["event"], "additionalContext": message}}


def watch_once(config):
    for path in sorted(Path(config["state_dir"]).glob("*.json")):
        try:
            with session_lock(path):
                state = json.loads(path.read_text())
                if state.get("retired"):
                    continue
                if state.get('ending'):
                    finish_presence(config, state, path)
                elif process_alive(state["process"]):
                    if not state.get("agent"):
                        state["agent"] = call(config, "agent-register", state["registration"])
                    else:
                        state["agent"] = heartbeat(config, state)
                elif not state.get("agent"):
                    # An uncertain registration may have committed; without an
                    # observed response it expires naturally within 90 seconds.
                    state["retired"] = True
                else:
                    finish_presence(config, state, path)
                write_state(path, state)
        except CoordinationError as exc:
            if exc.code == "SESSION_BUSY":
                continue  # The hook owns presence until it releases this lock.
            print(f"Cairn presence {config['binding']}: {exc}", file=sys.stderr)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            print(f"Cairn presence {config['binding']}: {type(exc).__name__}; presence may expire", file=sys.stderr)


def validate_config(config):
    for key in ("cairn", "socket", "token_file", "state_dir"):
        if not isinstance(config.get(key), str) or not Path(config[key]).is_absolute():
            raise CoordinationError("INVALID_CONFIG", f"absolute {key} required")
    if config.get("harness") not in ("codex", "claude", "agy", "opencode", "hermes") or not config.get("repo") or not config.get("binding"):
        raise CoordinationError("INVALID_CONFIG", "harness, collection and binding required")
    binding = config['binding']
    if not isinstance(binding, str) or len(binding) > 128 or binding in ('.', '..') or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in binding):
        raise CoordinationError("INVALID_CONFIG", "invalid binding name")
    return config


def load_config(path):
    return validate_config(json.loads(path.read_text()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("hook", "watch"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--event")
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    try:
        if args.mode == "hook":
            if args.config is None:
                parser.error("hook requires --config")
            raw = sys.stdin.buffer.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise CoordinationError("INVALID_HOST", "hook input exceeds limit")
            print(json.dumps(handle(load_config(args.config), json.loads(raw), args.event)))
        else:
            if args.config_dir is None:
                parser.error("watch requires --config-dir")
            stop = threading.Event()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            with session_lock(args.config_dir / ".watch"):
                while not stop.is_set():
                    started = time.monotonic()
                    for path in sorted(args.config_dir.glob("*.json")):
                        watch_once(load_config(path))
                    if args.once:
                        break
                    stop.wait(max(1, 30 - (time.monotonic() - started)))
    except (CoordinationError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        detail = str(exc) if isinstance(exc, CoordinationError) else type(exc).__name__
        print(f"Cairn coordination: {detail}; session presence unavailable", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
