"""Exercise Codex's native SessionEnd callback without a model/provider call.

The app-server runs in a fresh CODEX_HOME with one trusted test hook. It creates
and archives a thread without starting a turn; the fixture appends synthetic
dialogue to that native thread's rollout and uses Cairn's local selector stub.
The callback's session id is host-emitted correlation testimony, not an
authenticated assertion about who supplied its stdin.
"""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_app_server_session import CodexAppServerSession  # noqa: E402


DIALOGUE = "RAW_CODEX_DIALOGUE_SENTINEL: retain the synthetic handoff."


def _record(role, text):
    content_type = "output_text" if role == "assistant" else "input_text"
    return dict(timestamp="2026-10-25T00:00:00Z", type="response_item",
                payload=dict(type="message", role=role,
                             content=[dict(type=content_type, text=text)]))


def check(codex, binary, root, repo, agent, selector, cli_shim):
    """Run after the disposable API and PostgreSQL probe are ready."""
    root = Path(root)
    fixture = root / "native-codex-capture"
    fixture.mkdir(mode=0o700)
    home = fixture / "home"
    home.mkdir(mode=0o700)
    config_home = home / "codex"
    config_home.mkdir(mode=0o700)
    work = fixture / "work"
    (work / ".git").mkdir(parents=True)
    destination = fixture / "hooks"
    state_dir = destination / "state"
    calls = fixture / "selector-calls"
    session_sentinel = DIALOGUE
    provider_requests = []

    class ProviderGuard(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            provider_requests.append(self.path)
            self.send_response(503)
            self.end_headers()

    provider = ThreadingHTTPServer(("127.0.0.1", 0), ProviderGuard)
    provider_thread = threading.Thread(target=provider.serve_forever, daemon=True)
    provider_thread.start()

    native_config = dict(cairn=str(cli_shim), claude=str(selector), socket=str(root / "api.sock"),
                         token_file=str(root / "hook.token"), repo=repo, harness="codex",
                         model="requested-codex-fixture-model", state_dir=str(state_dir),
                         capture_observations=True)
    destination.mkdir(mode=0o700)
    config_path = destination / "config.json"
    lifecycle = destination / "lifecycle.py"
    from codex_loopback_fixture import stage_lifecycle_runtime
    stage_lifecycle_runtime(destination, entrypoints={"memory.py": "lifecycle.py"})
    config_path.write_text(json.dumps(native_config), encoding="utf-8")
    config_path.chmod(0o600)
    command = shlex.join([sys.executable, str(lifecycle), "--config", str(config_path)])
    (config_home / "hooks.json").write_text(json.dumps({"hooks": {"SessionEnd": [{"hooks": [
        {"type": "command", "command": command, "timeout": 3}
    ]}]}}), encoding="utf-8")
    (config_home / "config.toml").write_text(
        "model = 'fixture-model'\nmodel_provider = 'fixture'\n"
        "[model_providers.fixture]\nname = 'Local fixture only'\n"
        f"base_url = 'http://127.0.0.1:{provider.server_port}'\n"
        "wire_api = 'responses'\nenv_key = 'CAIRN_FIXTURE_OPENAI_KEY'\n"
        "[hooks]\nenabled = true\n", encoding="utf-8")

    # No inherited credentials or host Codex configuration. The disposable
    # fixture does not start a model turn, and the local selector is a script.
    env = {key: os.environ[key] for key in ("PATH", "LANG") if key in os.environ}
    env.update(HOME=str(home), CODEX_HOME=str(config_home), CAIRN_HOME=str(root),
               CAIRN_FIXTURE_OPENAI_KEY="fixture-only",
               CAIRN_CAPTURE_PROBE_MODE="codex_native_saved", CAIRN_CAPTURE_PROBE_CALLS=str(calls),
               CAIRN_CAPTURE_PROBE_REAL_CAIRN=str(binary), CAIRN_CAPTURE_PROBE_FAIL_CREATE="0",
               CAIRN_CAPTURE_PROBE_FAIL_REPORT="0")
    session = CodexAppServerSession.spawn(
        [str(Path(codex).resolve(strict=True)), "app-server", "--stdio"],
        client_name="cairn-capture-fixture", env=env, cwd=fixture, default_timeout=10)
    try:
        hooks = session.call("hooks/list", {"cwds": [str(fixture)]})
        matches = [hook for entry in hooks.get("data", []) for hook in entry.get("hooks", [])
                   if hook.get("command") == command and hook.get("eventName", "").lower() == "sessionend"]
        assert len(matches) == 1, "isolated Codex did not load the single SessionEnd fixture hook"
        hook = matches[0]
        session.call("config/value/write", dict(
            filePath=str(config_home / "config.toml"),
            keyPath="hooks.state." + json.dumps(hook["key"]) + ".trusted_hash",
            value=hook["currentHash"], mergeStrategy="replace"))
        trusted = session.call("hooks/list", {"cwds": [str(fixture)]})
        verified = [entry_hook for entry in trusted.get("data", []) for entry_hook in entry.get("hooks", [])
                    if entry_hook.get("key") == hook["key"]]
        assert len(verified) == 1 and verified[0].get("trustStatus") == "trusted", \
            "Codex did not trust the isolated synthetic SessionEnd handler"

        thread = session.thread(work, "fixture-model", approval_policy=None, ephemeral=False,
                                modelProvider="fixture", allowProviderModelFallback=False)
        native_thread = thread.result["thread"]
        session_id = native_thread["id"]
        rollout = Path(native_thread["path"])
        assert rollout.name.endswith(session_id + ".jsonl") and config_home in rollout.parents, \
            "Codex thread path was outside the isolated native home"
        timestamp = "2026-10-25T00:00:00Z"
        session_meta = dict(timestamp=timestamp, type="session_meta", payload=dict(
            session_id=session_id, id=session_id, timestamp=timestamp, cwd=str(work),
            originator=native_thread.get("originator", "codex"),
            cli_version=native_thread.get("cliVersion", "fixture"),
            source=native_thread.get("source", "cli"), model_provider="fixture", git=None))
        synthetic = [session_meta, _record("user", session_sentinel),
                     _record("assistant", "Synthetic assistant reply for the capture fixture.")]
        rollout.parent.mkdir(parents=True, exist_ok=True)
        with rollout.open("w", encoding="utf-8") as output:
            output.write("\n".join(json.dumps(row) for row in synthetic) + "\n")
        expected_messages = [dict(role="user", text=session_sentinel),
                             dict(role="assistant", text="Synthetic assistant reply for the capture fixture.")]
        expected_digest = hashlib.sha256(engine_bytes(expected_messages, native_config["model"])).hexdigest()

        session.call("thread/archive", {"threadId": session_id})
        state_path = state_dir / (session_id + ".json")
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline and not state_path.exists():
            time.sleep(.05)
        assert state_path.exists(), "native SessionEnd hook did not commit capture state"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 3
        rows = []
        while time.monotonic() < deadline:
            rows = agent("report", "capture-observations", dict(repo=repo, limit=10, offset=0,
                        session_sha256=hashlib.sha256(session_id.encode()).hexdigest()))["rows"]
            if rows:
                break
            time.sleep(.05)
        assert len(rows) == 1, "native Codex callback did not produce one session-filtered observation"
        row = rows[0]
        assert row["hook_event"] == "SessionEnd" and row["harness"] == "codex"
        assert row["session_sha256"] == hashlib.sha256(session_id.encode()).hexdigest()
        assert row["snapshot_sha256"] == expected_digest == state["captured_digest"]
        assert row["outcome"] == "saved" and row["selector_outcome"] == "completed", \
            (row["outcome"], row["selector_outcome"],
             calls.read_text(encoding="utf-8").splitlines() if calls.exists() else [])
        assert state["last_capture"]["outcome"] == "saved" and state["last_capture"]["selector_calls"] == 1
        assert row["requested_model"] == native_config["model"]
        assert row["reported_model"] == "fixture-sonnet-2026-10"
        for field, value in dict(input_tokens=135, output_tokens=20, cache_creation_input_tokens=7,
                                 cache_read_input_tokens=90).items():
            assert row[field] == value, (field, row.get(field))
        assert row["cost_usd"] == 0.003
        assert provider_requests == [], "Codex unexpectedly attempted a model/provider request"
        assert calls.read_text(encoding="utf-8").splitlines() == ["codex_native_saved"]

        record_id = state["last_capture"]["records"][0]
        record = agent("hook", "history", dict(record_id=record_id, version=1))["versions"][0]
        retained_json = json.dumps(row) + json.dumps(state) + json.dumps(record)
        assert session_sentinel not in retained_json
        assert "RAW_SELECTOR_SENTINEL" not in retained_json
        dsn = os.environ.get("CAIRN_TEST_DATABASE_URL")
        assert dsn and os.environ.get("CAIRN_DATABASE_URL") == dsn, "disposable database required"
        for sql in (
            "SELECT row_to_json(c)::text FROM cairn.capture_observation c WHERE repo='" + repo + "'",
            "SELECT body FROM cairn.record_version WHERE repo='" + repo + "'",
        ):
            retained = subprocess.run(["psql", dsn, "-X", "-A", "-t", "-c", sql],
                capture_output=True, text=True, timeout=15, check=True).stdout
            assert session_sentinel not in retained, "raw dialogue sentinel reached PostgreSQL"
            assert "RAW_SELECTOR_SENTINEL" not in retained, "raw selector output reached PostgreSQL"
        print(json.dumps(dict(result="PASS", native_callback="Codex SessionEnd via app-server archive",
            outcome=row["outcome"], selector_outcome=row["selector_outcome"],
            session_joined=True, snapshot_joined=True, callback_session_origin_authenticated=False,
            local_provider_requests=len(provider_requests), selector="local synthetic fixture", usage={key: row[key] for key in
                ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")},
            synthetic_cost_usd=row["cost_usd"], raw_dialogue_retained=False), sort_keys=True))
    finally:
        session.close()
        provider.shutdown()
        provider.server_close()
        provider_thread.join(timeout=1)
        shutil.rmtree(fixture, ignore_errors=True)


def engine_bytes(messages, model):
    """Hash the selector's actual bounded snapshot using the lifecycle codec."""
    import importlib.util
    engine_path = Path(__file__).resolve().parents[1] / "integrations/lifecycle/memory.py"
    spec = importlib.util.spec_from_file_location("codex_capture_snapshot_engine", engine_path)
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    return engine.encoded([engine.CAPTURE_PROMPT, engine.CAPTURE_SCHEMA, model, messages]).encode()
