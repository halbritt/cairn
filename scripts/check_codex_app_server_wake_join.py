#!/usr/bin/env python3
"""One bounded provider-free Codex queue-to-Store watcher join probe."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid


REPO = Path(__file__).resolve().parents[1]
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))
import disposable_host
from native_tool_results import (
    tool_result_diagnostics as _tool_result_diagnostics,
    source_result_matches, source_read_succeeded, settlement_succeeded,
)
from codex_loopback_fixture import (
    LoopbackResponsesServer,
    exec_tool_item,
    message_item,
    response_stream,
    stage_codex_binding_and_hooks,
    trust_codex_hooks_via_rpc,
    write_private_file,
)

NATIVE = (Path.home() / ".npm-global/lib/node_modules/@openai/codex/node_modules/"
          "@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex")
TRACE_HOME = Path.home() / ".cache/cairn-220-native-wake-proof"
MODEL = "gpt-6-astra"
VERSION = "codex-cli 0.164.0"
MAX_STDERR = 4096
_BOOTSTRAP_FD = None
_BOOTSTRAP_PATH = None


def _scripted_inbox_tool(results: dict, context: dict, source_body: str) -> tuple[str, dict | None]:
    """Return one native tool action for exact source read, then notice ack."""
    if "source" not in results:
        command = ("printf %s " + shlex.quote(json.dumps(context["read_input"])) +
                   " | " + shlex.join(context["read"]))
        return "source_read", _exec_tool("source", command)
    require(source_read_succeeded(results["source"], context["source"], source_body),
            "exact_source_read_not_observed")
    if "acknowledgement" not in results:
        return "acknowledgement", _exec_tool(
            "acknowledgement", shlex.join(context["acknowledgement"]))
    require(settlement_succeeded(results["acknowledgement"], delivery_id=context["delivery_id"], event_id=context["event_id"]),
            "native_acknowledgement_not_confirmed")
    return "store_verification", None


def _exec_tool(call_id: str, command: str) -> dict:
    return exec_tool_item(call_id, command, max_output_tokens=2000)


def _source_result_matches(result: dict, context: dict, source_body: str) -> dict[str, bool]:
    """Classify exact source identity without retaining the returned payload."""
    return source_result_matches(result, context["source"], source_body)


def _is_expected_source_read_tool_call(item: dict, context: dict, source_body: str) -> bool:
    stage, expected = _scripted_inbox_tool({}, context, source_body)
    return stage == "source_read" and item == expected

sys.path.insert(0, str(REPO / "scripts/fixtures/cairn220_queue_trace"))
from trace_io import append_trace, create_trace


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat(follow_symlinks=False).st_mode)


def _private_trace_home() -> None:
    current = Path("/")
    for component in TRACE_HOME.parts[1:]:
        current = current / component
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode), "trace_home_symlink")
    info = TRACE_HOME.stat(follow_symlinks=False)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o700, "trace_home_not_private")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounded_hash_stream(stream, result: dict[str, object]) -> None:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(1024)
        if not chunk:
            break
        total += len(chunk)
        digest.update(chunk)
    result.update(bytes=total, sha256=digest.hexdigest())


def _checked_run(argv, *, env=None, timeout=30, cwd=None) -> subprocess.CompletedProcess:
    step = _PHASE
    _bootstrap(step, "before")
    try:
        result = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        _bootstrap(step, "failed", "TimeoutExpired")
        raise RuntimeError(step + "_timeout") from None
    if result.returncode:
        _bootstrap(step, "failed", "CalledProcessError", result.returncode)
        raise RuntimeError(step + "_failed")
    _bootstrap(step, "after")
    return result


# The cluster and API startup helpers live in scripts/disposable_host.py; these
# names stay for the stage-trace fixtures and tests that import them from here.
_pg_start_command = disposable_host.pg_start_command
_classify_pg_start_failure = disposable_host.classify_pg_start_failure
_stop_disposable_postgres = disposable_host.stop_postgres
_OutputCapture = disposable_host.OutputCapture
_classify_api_start_output = disposable_host.classify_api_start_output


def _short_socket_dir() -> Path:
    path = TRACE_HOME / ("s-" + secrets.token_hex(4))
    path.mkdir(mode=0o700)
    return path




def _start_disposable_postgres(pg_bin: Path, data_dir: Path, socket_dir: Path,
                               log_path: Path) -> subprocess.CompletedProcess:
    argv = _pg_start_command(pg_bin, data_dir, socket_dir, log_path)
    _bootstrap("pg_start", "before")
    try:
        result = subprocess.run(argv, capture_output=True, timeout=30, check=False)
    except subprocess.TimeoutExpired:
        _bootstrap("pg_start", "failed", "TimeoutExpired")
        raise RuntimeError("pg_start_timeout") from None
    if result.returncode:
        failure_class = _classify_pg_start_failure(result.stdout, result.stderr, log_path)
        _bootstrap("pg_start", "failed", "CalledProcessError", result.returncode,
                   flags={"pg_failure_class": failure_class})
        raise RuntimeError("pg_start_failed")
    _bootstrap("pg_start", "after")
    return result








def _launch_api(binary: Path, api: Path, server_env: dict, socket_path: Path,
                *, timeout: float = 12):
    """Start the local API, keeping only bounded in-memory stdout/stderr for classification."""
    process = subprocess.Popen([str(binary), "serve", "--socket", str(socket_path)], env=server_env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout_capture = _OutputCapture()
    stderr_capture = _OutputCapture()
    readers = [threading.Thread(target=stdout_capture.drain, args=(process.stdout,), daemon=True),
               threading.Thread(target=stderr_capture.drain, args=(process.stderr,), daemon=True)]
    for reader in readers:
        reader.start()
    _bootstrap("api_start", "after")
    _bootstrap("api_ready", "before")
    deadline = time.monotonic() + timeout
    socket_path = Path(socket_path)
    while time.monotonic() < deadline:
        if process.poll() is not None:
            for reader in readers:
                reader.join(timeout=2)
            stdout, out_truncated = stdout_capture.snapshot()
            stderr, err_truncated = stderr_capture.snapshot()
            category = _classify_api_start_output(stdout, stderr,
                truncated=out_truncated or err_truncated, socket_path=socket_path)
            stdout_capture.discard()
            stderr_capture.discard()
            _bootstrap("api_ready", "failed", "CalledProcessError", process.returncode,
                       flags={"api_start_failure_class": category})
            raise RuntimeError("disposable_api_start_failed")
        if socket_path.exists():
            # Successful startup is the diagnostic boundary. Discard all captured
            # bytes now; reader threads continue draining without retaining output.
            stdout_capture.discard()
            stderr_capture.discard()
            _bootstrap("api_ready", "after")
            return process, readers
        time.sleep(0.02)
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    for reader in readers:
        reader.join(timeout=2)
    stdout, out_truncated = stdout_capture.snapshot()
    stderr, err_truncated = stderr_capture.snapshot()
    category = _classify_api_start_output(stdout, stderr,
        truncated=out_truncated or err_truncated, socket_path=socket_path)
    stdout_capture.discard()
    stderr_capture.discard()
    _bootstrap("api_ready", "failed", "TimeoutExpired", flags={"api_start_failure_class": category})
    raise RuntimeError("disposable_api_socket_timeout")


_PHASE = "runner"
_API_FAILURE_CLASS = None


def _bootstrap(step: str, outcome: str, error=None, code=None, flags=None) -> None:
    if _BOOTSTRAP_FD is not None:
        append_trace(_BOOTSTRAP_FD, "bootstrap", outcome, error, code, flags=flags, step=step)


def _api_call(binary: Path, api: Path, token_file: Path, operation: str,
              body: dict, session: dict | None = None,
              socket_path: Path | str | None = None) -> dict:
    argv = [str(binary), "agent", "--socket", str(socket_path or (api / "api.sock")),
            "--token-file", str(token_file)]
    if session:
        argv += ["--agent-id", session["agent_id"], "--execution-id", session["execution_id"]]
    argv.append(operation)
    result = subprocess.run(argv, input=json.dumps(body), text=True, capture_output=True,
                            env={"PATH": "/usr/bin:/bin"}, timeout=20, check=False)
    try:
        envelope = json.loads(result.stdout)
    except (TypeError, ValueError):
        global _API_FAILURE_CLASS
        _API_FAILURE_CLASS = "invalid_json"
        raise RuntimeError("api_response_invalid") from None
    if result.returncode:
        _API_FAILURE_CLASS = "cli_exit"
        raise RuntimeError("api_operation_refused")
    if not isinstance(envelope, dict):
        _API_FAILURE_CLASS = "envelope_not_object"
        raise RuntimeError("api_operation_refused")
    if envelope.get("ok") is not True:
        _API_FAILURE_CLASS = "response_refused" if envelope.get("status") != "OK" else "response_contract_mismatch"
        raise RuntimeError("api_operation_refused")
    _API_FAILURE_CLASS = None
    return envelope.get("data")


def _write_private(path: Path, data: str) -> None:
    write_private_file(path, data, exclusive=True)


def _sandbox_prefix(root: Path, trace_name: str, *, preflight: bool) -> list[str]:
    scripts = REPO / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from trial_task_eval import runtime_sandbox

    command = runtime_sandbox(include_home_go=False) + [
        "--unshare-net",
        "--ro-bind", str(REPO), "/repo",
        "--bind", str(TRACE_HOME), "/trace",
        "--dir", "/home/codex-fixture",
    ]
    if not preflight:
        command += [
            "--bind", str(root), "/fixture",
            "--ro-bind", str(NATIVE.parent), "/opt/codex-bin",
        ]
    command += [
        "--setenv", "HOME", "/home/codex-fixture",
        "--setenv", "CODEX_HOME", "/fixture/profile" if not preflight else "/home/codex-fixture",
        "--setenv", "PATH", "/usr/bin:/bin:/opt/codex-bin",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
        "--setenv", "PYTHONPATH",
        "/repo/scripts/fixtures/cairn220_queue_trace:/repo/integrations/lifecycle",
    ]
    command += ["--setenv", "CAIRN220_QUEUE_TRACE" if preflight else "CAIRN220_TRACE_TARGET",
                "/trace/" + trace_name]
    if not preflight:
        item_trace_name = "item-stage-" + trace_name.removeprefix("run-queue-stage-")
        command += ["--setenv", "CAIRN220_ITEM_TRACE_TARGET", "/trace/" + item_trace_name]
        script_trace_name = "script-stage-" + trace_name.removeprefix("run-queue-stage-")
        command += ["--setenv", "CAIRN220_SCRIPT_TRACE_TARGET", "/trace/" + script_trace_name]
    command += ["--chdir", "/repo" if preflight else "/fixture"]
    return command


def _preflight(root: Path, trace_path: Path) -> dict:
    name = trace_path.name
    command = _sandbox_prefix(root, name, preflight=True)
    command += ["--", "/usr/bin/python3", "/repo/scripts/check_codex_app_server_wake_join.py",
                "--preflight-inner"]
    result = subprocess.run(command, env={"PATH": "/usr/bin:/bin"}, capture_output=True,
                            timeout=15, check=False)
    require(result.returncode == 0, "mounted_path_preflight_failed")
    try:
        report = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise RuntimeError("mounted_path_preflight_report_invalid") from None
    require(report == {"runner": "/repo/scripts/check_codex_app_server_wake_join.py",
                       "trace_created": True, "trace_mode": "0600"},
            "mounted_path_preflight_mismatch")
    info = trace_path.stat(follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and not trace_path.is_symlink() and
            info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
            "host_trace_preflight_mismatch")
    rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
    require(rows == [{"outcome": "started", "schema": "cairn.codex-queue-stage/1", "stage": "trace"}],
            "trace_preflight_contents_mismatch")
    trace_path.unlink()
    return {"runner": "mounted_exact_path", "trace_path": "exclusive_owner_only_0600",
            "trace_preflight": "passed"}


def _host_setup(root: Path, trace_path: Path) -> tuple[Path, Path, dict]:
    global _PHASE
    _PHASE = "pg_config"
    pg_bin = Path(_checked_run(["pg_config", "--bindir"], timeout=5).stdout.decode().strip())
    binary = root / "cairn"
    _PHASE = "go_build"
    _checked_run(["go", "build", "-o", str(binary), "./cmd/cairn"], timeout=180, cwd=REPO)
    api = root / "api"
    api.mkdir(mode=0o700)
    socket_dir = _short_socket_dir()
    data_dir = root / "pgdata"
    api_socket = socket_dir / "api.sock"
    api_process = None
    api_readers = []
    postgres_started = False
    try:
        _PHASE = "pg_initdb"
        _checked_run([str(pg_bin / "initdb"), "-D", str(data_dir), "--auth-local=trust",
                      "--auth-host=reject", "--no-locale", "-E", "UTF8"], timeout=30)
        _PHASE = "pg_start"
        _start_disposable_postgres(pg_bin, data_dir, socket_dir, root / "postgres.log")
        postgres_started = True
        _PHASE = "db_create"
        _checked_run([str(pg_bin / "createdb"), "-h", str(socket_dir), "cairn_wake_join"], timeout=10)
        dsn = f"host={socket_dir} dbname=cairn_wake_join sslmode=disable"
        server_env = {"PATH": "/usr/bin:/bin", "CAIRN_HOME": str(api),
                      "CAIRN_DATABASE_URL": dsn, "CAIRN_TEST_DATABASE_URL": dsn}
        _PHASE = "migrate"
        _checked_run([str(binary), "migrate"], env=server_env, timeout=90)
        identities = []
        tokens = {}
        repo = "fixture:cairn-220-wake-join"
        for role in ("sender", "worker"):
            token = secrets.token_urlsafe(32)
            token_file = api / f"{role}.token"
            _write_private(token_file, token)
            tokens[role] = str(token_file)
            identities.append({"token_sha256": _digest(token.encode()),
                               "principal": f"agent/cairn220-{role}-{uuid.uuid4()}",
                               "repo": repo, "role": "agent", "destination": "hosted"})
        _write_private(api / "identities.json", json.dumps(identities))
        _PHASE = "api_start"
        _bootstrap(_PHASE, "before")
        api_process, api_readers = _launch_api(binary, api, server_env, api_socket)
        _PHASE = "api_ready"
        try:
            _api_call(binary, api, Path(tokens["sender"]), "version", {}, socket_path=api_socket)
        except RuntimeError:
            _bootstrap(_PHASE, "failed", "OtherError", flags={
                "api_failure_class": _API_FAILURE_CLASS or "response_contract_mismatch"})
            raise
        _bootstrap(_PHASE, "after")
        profile = root / "profile"
        profile.mkdir(mode=0o700)
        # A fabricated key exists only to satisfy the isolated Codex profile. Network
        # is unshared; the only route is the loopback Responses stub.
        _write_private(profile / "auth.json", '{"OPENAI_API_KEY":"sk-cairn220-fake"}\n')
        return pg_bin, binary, dict(api=api, api_process=api_process, dsn=dsn, repo=repo,
                                    tokens=tokens, profile=profile, trace_path=trace_path,
                                    socket_dir=socket_dir, api_readers=api_readers,
                                    api_socket=api_socket)
    except Exception:
        if api_process is not None:
            api_process.terminate()
            try:
                api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api_process.kill()
                api_process.wait(timeout=5)
            for reader in api_readers:
                reader.join(timeout=2)
        if (postgres_started or (data_dir / "postmaster.pid").exists()) and not _stop_disposable_postgres(pg_bin, data_dir):
            _bootstrap("pg_cleanup", "failed", "OtherError")
            raise RuntimeError("pg_cleanup_failed") from None
        shutil.rmtree(socket_dir, ignore_errors=True)
        raise


def _postgres_bootstrap_preflight(root: Path, pg_bin: Path) -> None:
    """Exercise the exact startup command against an inert disposable cluster."""
    data_dir = root / "pg-preflight-data"
    # PostgreSQL appends a fixed socket suffix. Keep this path short enough for
    # AF_UNIX even beneath the owner-only cache directory.
    socket_dir = _short_socket_dir()
    log_path = root / "pg-preflight.log"
    _checked_run([str(pg_bin / "initdb"), "-D", str(data_dir), "--auth-local=trust",
                  "--auth-host=reject", "--no-locale", "-E", "UTF8"], timeout=30)
    started = False
    try:
        _start_disposable_postgres(pg_bin, data_dir, socket_dir, log_path)
        started = True
    finally:
        if started or (data_dir / "postmaster.pid").exists():
            if not _stop_disposable_postgres(pg_bin, data_dir):
                _bootstrap("pg_cleanup", "failed", "OtherError")
                raise RuntimeError("pg_cleanup_failed") from None
        shutil.rmtree(socket_dir, ignore_errors=True)


def _run_api_preflight() -> dict:
    """Run the exact disposable PG/API setup without entering bwrap or the watcher."""
    _private_trace_home()
    run_id = uuid.uuid4().hex
    # Match run_once's root name so the default API socket path has identical length.
    root = Path(tempfile.mkdtemp(prefix="fixture-" + run_id + "-", dir=TRACE_HOME))
    root.chmod(0o700)
    trace_path = TRACE_HOME / ("bootstrap-api-preflight-" + run_id + ".jsonl")
    global _BOOTSTRAP_FD, _BOOTSTRAP_PATH
    _BOOTSTRAP_FD = create_trace(str(trace_path))
    _BOOTSTRAP_PATH = trace_path
    _bootstrap("api_preflight", "before")
    pg_bin = None
    api_process = None
    api_readers = []
    socket_dir = None
    cleanup_ok = True
    try:
        pg_bin, _binary, context = _host_setup(root, trace_path)
        api_process = context["api_process"]
        api_readers = context["api_readers"]
        socket_dir = context["socket_dir"]
        _bootstrap("api_preflight", "after")
        return {"result": "api_start_preflight_passed", "watcher_started": False}
    finally:
        if api_process is not None and api_process.poll() is None:
            api_process.terminate()
            try:
                api_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api_process.kill()
                api_process.wait(timeout=5)
        for reader in api_readers:
            reader.join(timeout=2)
        if pg_bin is not None and (root / "pgdata" / "postmaster.pid").exists():
            cleanup_ok = _stop_disposable_postgres(pg_bin, root / "pgdata")
            if not cleanup_ok:
                _bootstrap("pg_cleanup", "failed", "OtherError")
        if (root / "pgdata" / "postmaster.pid").exists():
            cleanup_ok = False
            _bootstrap("pg_cleanup", "failed", "OtherError")
        if socket_dir is not None and cleanup_ok:
            shutil.rmtree(socket_dir, ignore_errors=True)
        if cleanup_ok:
            shutil.rmtree(root, ignore_errors=True)
        else:
            _bootstrap("pg_cleanup", "failed", "OtherError")
        if _BOOTSTRAP_FD is not None:
            os.close(_BOOTSTRAP_FD)
            _BOOTSTRAP_FD = None
        if not cleanup_ok:
            raise RuntimeError("pg_cleanup_failed") from None


def _attach_item_started_observer(rpc, native_id: str, record=None) -> None:
    """Check loaded metadata, then attach this connection through thread/resume."""
    def call(stage, method, params):
        if record:
            record(stage, "before")
        try:
            result = rpc(method, params)
        except Exception as exc:
            if record:
                code = getattr(exc, "code", None)
                flags = None
                if method == "thread/resume" and type(exc).__name__ == "QueueRefused":
                    flags = {"native_refusal_class": _classify_thread_resume_refusal(
                        getattr(exc, "message", None))}
                private_message = (getattr(exc, "message", None)
                                   if method == "thread/resume" and
                                   type(exc).__name__ == "QueueRefused" else None)
                record(stage, "failed", type(exc).__name__, code=code, flags=flags,
                       private_message=private_message)
            raise
        if record:
            record(stage, "after")
        return result

    loaded = call("observer_loaded_list", "thread/loaded/list", dict(limit=100))
    if native_id not in loaded.get("data", []):
        if record:
            record("observer_loaded_list", "failed", "RuntimeError")
        raise RuntimeError("notification_observer_thread_not_loaded")
    resumed = call("observer_resume", "thread/resume", dict(threadId=native_id))
    if resumed.get("thread", {}).get("id") != native_id:
        if record:
            record("observer_resume", "failed", "RuntimeError")
        raise RuntimeError("notification_observer_resume_mismatch")


def _classify_thread_resume_refusal(message) -> str:
    """Map pinned-source resume refusal text to a fixed enum."""
    if not isinstance(message, str):
        return "thread_resume_other"
    if message.startswith("no rollout found for thread id "):
        return "thread_no_persisted_rollout"
    if (message.startswith("thread ") and " is closing; retry " in message and
            (message.endswith("after the thread is closed") or
             message.endswith("thread/resume after the thread is closed"))):
        return "thread_closing"
    if message.startswith("thread not found: "):
        return "thread_not_found"
    if (message.startswith("cannot resume thread ") and
            message.endswith("with history while it is already running")):
        return "history_already_running"
    if (message.startswith("cannot resume running thread ") and
            " with stale path: requested `" in message and ", active `" in message):
        return "stale_rollout_path"
    if message.startswith("cannot resume an unloaded multi-agent v2 sub-agent through its parent;"):
        return "unloaded_v2_child"
    if (message.startswith("failed to enqueue running thread resume for thread ") and
            message.endswith(": thread listener is not running")):
        return "resume_listener_not_running"
    if (message.startswith("failed to enqueue running thread resume for thread ") and
            message.endswith(": thread listener command channel is closed")):
        return "resume_listener_channel_closed"
    return "thread_resume_other"


def _is_missing_rollout_resume_refusal(exc) -> bool:
    return (type(exc).__name__ == "QueueRefused" and
            _classify_thread_resume_refusal(getattr(exc, "message", None)) ==
            "thread_no_persisted_rollout")


def _classify_missing_queue_trace(item_rows: list[dict]) -> str:
    outcomes = {(row.get("stage"), row.get("outcome")) for row in item_rows}
    if ("watcher_start", "before") in outcomes and ("watcher_start", "after") not in outcomes:
        return "watcher_start_incomplete"
    if ("watcher_start", "after") in outcomes:
        return "queue_observer_trace_missing"
    if ("observer_resume", "failed") in outcomes:
        return "observer_resume_failed"
    if ("observer_loaded_list", "failed") in outcomes:
        return "observer_loaded_list_failed"
    for stage in ("observer_connection", "observer_initialize", "observer_initialized"):
        if (stage, "failed") in outcomes:
            return stage + "_failed"
    if ("observer_subscription", "failed") in outcomes:
        return "observer_subscription_failed"
    if ("observer_fallback", "observed") in outcomes:
        return "watcher_not_started_after_native_subscription_fallback"
    if ("observer_subscription", "after") in outcomes:
        return "watcher_not_started_after_observer_setup"
    if ("observer_subscription", "before") in outcomes:
        return "observer_setup_incomplete"
    return "item_observer_not_started"


class _ItemStartedObserver:
    """Observe item/started on a resumed or already-subscribed connection."""

    def __init__(self, queue_module, endpoint: str, process: dict, native_id: str,
                 trace_path: str, fallback_connection):
        from trace_io import append_trace, create_trace

        self._append = append_trace
        self._fd = create_trace(trace_path)
        self._stop = threading.Event()
        self._connection = None
        self._thread = None
        self._delivery_id = None
        self._native_id = native_id
        self._append(self._fd, "trace", "started")
        native_connection = fallback_connection
        def stage_call(stage, operation):
            self._append(self._fd, stage, "before")
            try:
                result = operation()
            except Exception as exc:
                self._append(self._fd, stage, "failed", error=type(exc).__name__)
                raise
            self._append(self._fd, stage, "after")
            return result
        try:
            self._connection, _websocket, _peer = stage_call(
                "observer_connection", lambda: queue_module._open(endpoint, process, "process"))
            rpc = queue_module._Rpc(self._connection, time.monotonic() + 5)
            stage_call("observer_initialize", lambda: rpc("initialize", dict(
                clientInfo=dict(name="cairn220-item-observer", version="1"),
                capabilities=dict(experimentalApi=True))))
            stage_call("observer_initialized", lambda: self._connection.send(
                json.dumps(dict(method="initialized"))))
            self._append(self._fd, "observer_subscription", "before")
            try:
                _attach_item_started_observer(
                    rpc, native_id,
                    lambda stage, outcome, error=None, code=None, flags=None,
                    private_message=None: self._append(
                        self._fd, stage, outcome, error=error, code=code, flags=flags,
                        private_message=private_message))
            except Exception as exc:
                if _is_missing_rollout_resume_refusal(exc):
                    try:
                        self._connection.close(timeout=0)
                    except Exception:
                        pass
                    self._connection = native_connection
                    native_connection = None
                    self._append(self._fd, "observer_fallback", "observed",
                                 flags={"native_refusal_class": "thread_no_persisted_rollout"})
                else:
                    self._append(self._fd, "observer_subscription", "failed",
                                 error=type(exc).__name__)
                    raise
            self._append(self._fd, "observer_subscription", "after")
            if native_connection is not None:
                native_connection.close(timeout=0)
                native_connection = None
            self._connection.settimeout(0.25)
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        except Exception:
            if self._connection is not None:
                try:
                    self._connection.close(timeout=0)
                except Exception:
                    pass
            if native_connection is not None:
                try:
                    native_connection.close(timeout=0)
                except Exception:
                    pass
            os.close(self._fd)
            raise

    def set_delivery_id(self, delivery_id: str) -> None:
        require(isinstance(delivery_id, str) and bool(delivery_id),
                "notification_observer_delivery_id_invalid")
        self._delivery_id = delivery_id

    def record_watcher_start(self, outcome: str, error: str | None = None,
                             exit_zero: bool | None = None) -> None:
        flags = {"exit_zero": exit_zero} if exit_zero is not None else None
        self._append(self._fd, "watcher_start", outcome, error=error, flags=flags)

    def _run(self):
        from websocket import WebSocketTimeoutException, WebSocketException

        seen = 0
        while not self._stop.is_set() and seen < 32:
            try:
                message = json.loads(self._connection.recv())
            except WebSocketTimeoutException:
                continue
            except (OSError, WebSocketException, ValueError, TypeError):
                return
            if not isinstance(message, dict) or message.get("method") != "item/started":
                continue
            params = message.get("params")
            if not isinstance(params, dict):
                continue
            item = params.get("item")
            if not isinstance(item, dict) or item.get("type") != "userMessage":
                continue
            seen += 1
            client_id = item.get("clientId")
            turn_id = params.get("turnId")
            item_id = item.get("id")
            client_digest = _digest(client_id.encode()) if isinstance(client_id, str) else None
            turn_digest = _digest(turn_id.encode()) if isinstance(turn_id, str) else None
            item_digest = _digest(item_id.encode()) if isinstance(item_id, str) else None
            self._append(self._fd, "native_item_started", "observed",
                flags={"delivery_id_matches": client_id == self._delivery_id,
                       "item_thread_matches": params.get("threadId") == self._native_id,
                       "item_is_user_message": True},
                digests={"delivery_id_sha256": client_digest, "turn_id_sha256": turn_digest,
                         "native_item_id_sha256": item_digest})

    def close(self):
        self._stop.set()
        if self._connection is not None:
            try:
                self._connection.close(timeout=0)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        os.close(self._fd)


def _run_postgres_preflight() -> dict:
    _private_trace_home()
    run_id = uuid.uuid4().hex
    root = Path(tempfile.mkdtemp(prefix="pg-preflight-" + run_id + "-", dir=TRACE_HOME))
    root.chmod(0o700)
    trace_path = TRACE_HOME / ("bootstrap-pg-preflight-" + run_id + ".jsonl")
    global _BOOTSTRAP_FD, _BOOTSTRAP_PATH, _PHASE
    _BOOTSTRAP_FD = create_trace(str(trace_path))
    _BOOTSTRAP_PATH = trace_path
    _PHASE = "pg_config"
    keep_root = False
    try:
        pg_bin = Path(_checked_run(["pg_config", "--bindir"], timeout=5).stdout.decode().strip())
        _postgres_bootstrap_preflight(root, pg_bin)
        return {"result": "pg_start_preflight_passed", "startup_command": "shared_pg_ctl_start"}
    finally:
        if (root / "pg-preflight-data" / "postmaster.pid").exists():
            keep_root = True
            _bootstrap("pg_cleanup", "failed", "OtherError")
        if not keep_root:
            shutil.rmtree(root, ignore_errors=True)
        if _BOOTSTRAP_FD is not None:
            os.close(_BOOTSTRAP_FD)
            _BOOTSTRAP_FD = None


def _inside(root_text: str, scripted_handled: bool = False) -> int:
    sys.path.insert(0, "/repo/integrations/lifecycle")
    sys.path.insert(0, "/repo/scripts")
    import codex_queue
    import coordination
    from trace_io import append_trace, create_trace

    root = Path(root_text)
    fixture = json.loads((root / "fixture.json").read_text())
    api_socket = fixture["api_socket"]
    api = root / "api"
    profile = root / "profile"
    workspace = root / "workspace"
    workspace.mkdir(mode=0o700)
    socket_path = root / "native.sock"
    worker_token = api / "worker.token"
    sender_token = api / "sender.token"
    binary = root / "cairn"
    repo = "fixture:cairn-220-wake-join"
    response_event = threading.Event()
    item_observer = None
    expected_event_id = None
    source_read_confirmed = False
    acknowledgement_confirmed = False
    acknowledgement_tool_calls = 0
    script_failure_code = None
    script_trace_fd = create_trace(os.environ["CAIRN220_SCRIPT_TRACE_TARGET"]) if scripted_handled else None
    script_trace_lock = threading.Lock()
    request_index = 0
    pending_tool_role = None
    state_path = None
    expected_read_command_requested = False
    observed_attempt = None
    observed_intent = None

    def trace_script(stage, outcome, *, error=None, flags=None):
        if script_trace_fd is not None:
            with script_trace_lock:
                append_trace(script_trace_fd, stage, outcome, error=error, flags=flags)

    def _context_for_event() -> tuple[Path, dict]:
        matches = []
        for path in (root / "engine" / "state" / "inbox").glob("*.json"):
            try:
                context = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if context.get("event_id") == expected_event_id:
                matches.append((path, context))
        require(len(matches) == 1, "native_inbox_context_not_unique")
        return matches[0]

    def _hook_state_presence() -> tuple[bool, bool]:
        nonlocal observed_attempt, observed_intent
        if state_path is None:
            return False, False
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            return False, False
        if isinstance(state.get("inbox_attempt"), dict):
            observed_attempt = state["inbox_attempt"]
        if isinstance(state.get("inbox_intent"), dict):
            observed_intent = state["inbox_intent"]
        return bool(state.get("inbox_intent")), bool(state.get("inbox_attempt"))

    def handle_response(request, results):
        nonlocal request_index, pending_tool_role, expected_read_command_requested
        nonlocal source_read_confirmed, acknowledgement_confirmed
        nonlocal acknowledgement_tool_calls
        request_index += 1
        if not scripted_handled:
            _hook_state_presence()
            return message_item("msg_cairn220", "Synthetic local response.")
        raw_input = request.get("input", [])
        tool_results = [row for row in raw_input if row.get("type") == "custom_tool_call_output"]
        trace_script("responses_request", "observed", flags={
            "step": "request", "request_index": request_index,
            "input_item_count": len(raw_input), "tool_result_count": len(tool_results),
            "has_input": bool(raw_input), "has_tools": any(
                row.get("type") == "additional_tools" for row in raw_input)})
        trace_script("hook_context", "before", flags={"step": "context_lookup"})
        try:
            _context_path, context = _context_for_event()
        except Exception as exc:
            state_intent, state_attempt = _hook_state_presence()
            trace_script("hook_context", "failed", error=type(exc).__name__,
                         flags={"step": "context_lookup", "context_present": False,
                                "intent_present": state_intent, "attempt_present": state_attempt,
                                "failure_code": "context_not_found"})
            raise
        source_body = "Synthetic CAIRN-220 watcher source"
        state_intent, state_attempt = _hook_state_presence()
        trace_script("hook_context", "after", flags={"step": "context_lookup",
            "context_present": True, "intent_present": state_intent,
            "attempt_present": state_attempt})
        if pending_tool_role:
            result = results.get(pending_tool_role)
            if not result:
                trace_script("native_tool_result", "failed", error="RuntimeError", flags={
                    "result_role": pending_tool_role, "result_class": "other",
                    "result_transport": "missing",
                    "expected_read_command_requested": expected_read_command_requested,
                    "failure_code": "native_tool_result_missing"})
                raise RuntimeError("native_tool_result_missing")
            result_diagnostics = _tool_result_diagnostics(result)
            if pending_tool_role == "source":
                source_matches = _source_result_matches(result, context, source_body)
                exact = all(source_matches.values())
                result_class = "source_exact" if exact else "source_mismatch"
                result_flags = {"result_role": pending_tool_role, "result_class": result_class,
                    "result_transport": "matched",
                    "expected_read_command_requested": expected_read_command_requested,
                    **source_matches, **result_diagnostics}
            else:
                exact = settlement_succeeded(result, delivery_id=context["delivery_id"], event_id=context["event_id"])
                result_class = "ack_handled" if exact else "ack_unconfirmed"
                result_flags = {"result_role": pending_tool_role, "result_class": result_class,
                    "result_transport": "matched", **result_diagnostics}
            trace_script("native_tool_result", "observed" if exact else "refused",
                flags=result_flags)
            require(exact, "source_read_mismatch" if pending_tool_role == "source"
                    else "acknowledgement_not_confirmed")
            pending_tool_role = None
        stage, item = _scripted_inbox_tool(
            results, context, source_body)
        call_role = "source" if stage == "source_read" else (
            "acknowledgement" if stage == "acknowledgement" else None)
        if stage == "acknowledgement":
            source_read_confirmed = True
            acknowledgement_tool_calls += 1
        elif stage == "store_verification":
            inspect = _api_call(binary, api, sender_token, "event-inspect",
                                {"event_id": expected_event_id}, socket_path=api_socket)
            delivery = next(row for row in inspect["deliveries"]
                            if row["delivery_id"] == context["delivery_id"])
            require(delivery.get("state") == "handled" and delivery.get("attempts") == 1
                    and acknowledgement_tool_calls == 1,
                    "store_handled_outcome_not_exactly_once")
            acknowledgement_confirmed = True
            item = message_item(
                "msg_cairn220_done",
                "Read the exact notice source and acknowledged it as handled.",
            )
            response_event.set()
            call_role = None
        if call_role:
            pending_tool_role = call_role
            expected_read_command_requested = False
            if call_role == "source":
                expected_read_command_requested = _is_expected_source_read_tool_call(
                    item, context, source_body)
            trace_script("native_tool_call", "observed", flags={
                "step": stage, "tool_name": item.get("name", "other"),
                "tool_namespace": item.get("namespace", "other"),
                "call_role": item.get("call_id", "other")
                    if item.get("call_id") in ("source", "acknowledgement") else "other",
                "expected_read_command_requested": expected_read_command_requested})
        trace_script("responses_response", "before", flags={
            "response_item_type": item.get("type", "other")})
        return item
    def on_response_error(exc: BaseException) -> None:
        nonlocal script_failure_code
        failure_codes = {
            "native_inbox_context_not_unique": "context_not_found",
            "native_tool_result_missing": "native_tool_result_missing",
            "source_read_mismatch": "source_read_mismatch",
            "exact_source_read_not_observed": "source_read_mismatch",
            "native_acknowledgement_not_confirmed": "acknowledgement_not_confirmed",
            "acknowledgement_not_confirmed": "acknowledgement_not_confirmed",
            "store_handled_outcome_not_exactly_once": "store_handled_not_confirmed",
        }
        script_failure_code = failure_codes.get(str(exc), "other")
        trace_script("responses_response", "failed", error=type(exc).__name__, flags={
            "failure_code": script_failure_code})

    def after_response(item: dict) -> None:
        if scripted_handled:
            trace_script("responses_response", "after", flags={
                "response_item_type": item.get("type", "other")})
        else:
            response_event.set()

    server = LoopbackResponsesServer(
        handle_response,
        model=MODEL,
        response_id_prefix="resp_cairn220_",
        sequence_by_http_events=True,
        max_requests=3,
        max_body_bytes=2_000_000,
        enable_models_endpoint=True,
        max_model_requests=3,
        enforce_bounds_via_http_status=True,
        on_error=on_response_error,
        after_response=after_response,
    )
    server.write_config_toml(
        profile,
        provider_name="CAIRN-220 loopback fixture",
        reasoning_effort="low",
        check_for_update_on_startup=False,
    )
    # Hook definitions are loaded and explicitly trusted on this disposable
    # profile only. The command points at the repository's production hook.
    engine_root = root / "engine"
    bindings = engine_root / "bindings"
    state_dir = engine_root / "state"
    binding = "codex-wake-join"
    config_data = dict(cairn=str(binary), socket=api_socket,
        token_file=str(worker_token), state_dir=str(state_dir), binding=binding,
        harness="codex", repo=repo, model=MODEL, native_delivery=True,
        idle_wakeup="/bin/false", process_names=["codex"], config_home=str(profile))
    _config_path, command = stage_codex_binding_and_hooks(
        engine_root,
        profile,
        binding=binding,
        config_data=config_data,
    )
    server.start()

    child = subprocess.Popen(["/opt/codex-bin/codex", "app-server", "--listen", "unix://" + str(socket_path)],
        env={"HOME": str(profile), "CODEX_HOME": str(profile), "PATH": "/usr/bin:/bin:/opt/codex-bin",
             "LANG": "C.UTF-8", "TMPDIR": "/tmp"}, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    stderr_hash = hashlib.sha256()
    stderr_count = [0]

    def drain_stderr():
        while True:
            chunk = child.stderr.read(1024)
            if not chunk:
                return
            stderr_hash.update(chunk)
            stderr_count[0] += len(chunk)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()
    connection = None
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and child.poll() is None and not socket_path.exists():
            time.sleep(0.02)
        require(child.poll() is None and socket_path.exists(), "app_server_socket_not_ready")
        process = coordination.process_reference(child.pid)
        connection, _websocket, _peer = codex_queue._open(str(socket_path), process, "process")
        rpc = codex_queue._Rpc(connection, time.monotonic() + 15)
        rpc("initialize", dict(clientInfo=dict(name="cairn-220-disposable-fixture", version="1"),
                               capabilities=dict(experimentalApi=True)))
        connection.send(json.dumps(dict(method="initialized")))
        trust_codex_hooks_via_rpc(rpc, workspace=workspace, profile_dir=profile, command=command)
        started = rpc("thread/start", dict(cwd=str(workspace), model=MODEL,
            approvalPolicy="never",
            sandbox="danger-full-access" if scripted_handled else "read-only"))
        native_id = started["thread"]["id"]
        thread_page = rpc("thread/loaded/list", dict(limit=100))
        require(native_id in thread_page.get("data", []), "thread_not_loaded")

        metadata = dict(harness="codex", model=MODEL, observed_model=MODEL,
                        project=workspace.name, workspace=str(workspace), state="idle",
                        delivery_mode="existing-session")
        registered = _api_call(binary, api, worker_token, "agent-register", dict(
            request_id=str(uuid.uuid4()), repo=repo, binding=binding,
            native_session_id=native_id, metadata=metadata), socket_path=api_socket)
        session = {key: registered[key] for key in ("agent_id", "execution_id")}
        state_path = coordination.state_path(config_data, native_id)
        state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        coordination.write_state(state_path, dict(agent=registered, process=process,
                                                    workspace=str(workspace)))
        item_observer = _ItemStartedObserver(codex_queue, str(socket_path), process, native_id,
            os.environ["CAIRN220_ITEM_TRACE_TARGET"], fallback_connection=connection)
        connection = None
        source = _api_call(binary, api, sender_token, "create", dict(request_id=str(uuid.uuid4()), draft=dict(
            kind="note", body="Synthetic CAIRN-220 watcher source", scope=dict(repo=repo, task_id="*", run_id="*"),
            claim_type="self", sensitivity="shareable")), socket_path=api_socket)
        event = _api_call(binary, api, sender_token, "event-publish", dict(
            request_id=str(uuid.uuid4()), kind="notice",
            ref=dict(record_id=source["record_id"], version=source["version"]),
            destination=dict(type="agent", name=registered["inbox"])), socket_path=api_socket)
        event_id = event["event_id"]
        expected_event_id = event_id
        status_before = _api_call(binary, api, sender_token, "event-inspect", {"event_id": event_id},
                                   socket_path=api_socket)
        delivery = status_before["deliveries"][0]
        expected_delivery_id = delivery["delivery_id"]
        item_observer.set_delivery_id(expected_delivery_id)
        # Keep environment minimal; only fake fixture paths and the dedicated trace pass in.
        watcher_env = {"PATH": "/usr/bin:/bin", "HOME": str(profile), "CODEX_HOME": str(profile),
                       "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
                       "PYTHONPATH": "/repo/scripts/fixtures/cairn220_queue_trace:" + str(engine_root),
                       "CAIRN220_QUEUE_TRACE": os.environ["CAIRN220_TRACE_TARGET"]}
        item_observer.record_watcher_start("before")
        try:
            watcher = subprocess.run(["/usr/bin/python3",
                "/repo/scripts/fixtures/cairn220_queue_trace/watcher.py",
                str(engine_root / "coordination.py"),
                "watch", "--config-dir", str(bindings), "--once"], env=watcher_env,
                capture_output=True, timeout=30, check=False)
        except Exception as exc:
            item_observer.record_watcher_start("failed", type(exc).__name__)
            raise
        item_observer.record_watcher_start("after", exit_zero=watcher.returncode == 0)
        watcher_stderr_sha256 = _digest(watcher.stderr)
        deadline = time.monotonic() + 25
        state = {}
        while time.monotonic() < deadline:
            try:
                state = json.loads(state_path.read_text())
            except (OSError, ValueError):
                state = {}
            attempt = state.get("inbox_attempt") or observed_attempt
            intent = state.get("inbox_intent") or observed_intent
            if isinstance(attempt, dict):
                break
            if child.poll() is not None:
                break
            time.sleep(0.05)
        response_event.wait(10)
        try:
            state = json.loads(state_path.read_text())
        except (OSError, ValueError):
            state = {}
        attempt = observed_attempt or state.get("inbox_attempt") or {}
        intent = observed_intent or state.get("inbox_intent") or {}
        actual_delivery = ((attempt.get("delivery") or {}).get("delivery_id"))
        turn_id = attempt.get("native_turn_id") or intent.get("native_turn_id")
        event_after = _api_call(binary, api, sender_token, "event-inspect", {"event_id": event_id},
                                socket_path=api_socket)
        delivery_after = event_after["deliveries"][0]
        trace_rows = []
        summary = {
            "schema": "cairn.codex-app-server-wake-join/2",
            "codex_version": VERSION,
            "network": "bwrap --unshare-net; loopback Responses fixture",
            "production_watcher": True,
            "watcher_exit": watcher.returncode,
            "watcher_stderr_sha256": watcher_stderr_sha256,
            "event_kind": "notice",
            "delivery_state": delivery_after.get("state"),
            "delivery_attempts": delivery_after.get("attempts"),
            "queue_receipt": "not_observed",
            "item_started": "not_observed",
            "user_prompt_submit": "not_observed",
            "store_attempt": "not_observed",
            "delivery_id_match": actual_delivery == expected_delivery_id,
            "native_turn_id_present": isinstance(turn_id, str) and bool(turn_id),
            "response_requests": server.post_count,
            "model_list_requests": server.get_count,
            "scripted_handled_requested": scripted_handled,
            "exact_source_read": source_read_confirmed,
            "acknowledgement": "handled_confirmed" if acknowledgement_confirmed else "not_confirmed",
            "acknowledgement_tool_calls": acknowledgement_tool_calls,
            "script_failure_code": script_failure_code,
            "settlement_exactly_once": acknowledgement_confirmed and acknowledgement_tool_calls == 1
                and delivery_after.get("attempts") == 1,
            "hook_join": False,
        }
        if attempt:
            summary["store_attempt"] = "observed" if actual_delivery == expected_delivery_id else "mismatch"
            summary["user_prompt_submit"] = "observed" if turn_id else "not_observed"
            summary["native_turn_id_sha256"] = _digest(turn_id.encode()) if turn_id else None
        idle_wake = state.get("idle_wake") or {}
        summary["queue_receipt"] = "confirmed" if idle_wake.get("queued_submission_id") else "not_retained"
        summary["queue_start"] = idle_wake.get("last_start", "not_retained")
        summary["hook_join"] = bool(attempt and actual_delivery == expected_delivery_id and
                                    attempt.get("session") == session and turn_id == intent.get("native_turn_id"))
        if not scripted_handled:
            summary["continuation_failure_stage"] = "not_requested"
        elif script_failure_code:
            summary["continuation_failure_stage"] = "script_" + script_failure_code
        elif summary["user_prompt_submit"] != "observed":
            summary["continuation_failure_stage"] = "native_hook_or_prompt_not_observed"
        elif not source_read_confirmed:
            summary["continuation_failure_stage"] = "exact_source_read_not_confirmed"
        elif not acknowledgement_confirmed:
            summary["continuation_failure_stage"] = "handled_ack_not_confirmed"
        else:
            summary["continuation_failure_stage"] = "handled_and_confirmed"
        try:
            with open(os.environ["CAIRN220_TRACE_TARGET"], encoding="utf-8") as stream:
                trace_rows = [json.loads(line) for line in stream]
        except (OSError, ValueError):
            pass
        item_trace_rows = []
        try:
            with open(os.environ["CAIRN220_ITEM_TRACE_TARGET"], encoding="utf-8") as stream:
                item_trace_rows = [json.loads(line) for line in stream]
        except (OSError, ValueError):
            pass
        summary.update(_queue_join_evidence(
            trace_rows, item_trace_rows, expected_delivery_id, turn_id, summary["hook_join"]))
        if summary["exact_queue_hook_store_join"]:
            summary["failure_stage"] = "joined"
        else:
            summary["failure_stage"] = _failure_stage(summary, trace_rows)
        result_path = root / "inside-result.json"
        _write_private(result_path, json.dumps(summary, sort_keys=True))
        if scripted_handled:
            return 0 if _scripted_handled_succeeded(summary, inside=True) else 1
        return 0 if _queue_join_succeeded(summary, inside=True) else 1
    finally:
        if item_observer is not None:
            item_observer.close()
        elif connection is not None:
            try:
                connection.close(timeout=0)
            except Exception:
                pass
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        server.close(timeout=2)
        stderr_thread.join(timeout=2)
        child.stderr.close()
        if script_trace_fd is not None:
            os.close(script_trace_fd)


def _queue_observer_evidence(trace_rows: list[dict]) -> dict:
    attached_at = next((index for index, row in enumerate(trace_rows)
        if row.get("stage") == "observer_attach" and row.get("outcome") == "after"), None)
    return {
        "queue_observer_attached": attached_at is not None,
        "queue_observer_hit": attached_at is not None and any(
            row.get("stage") == "queue_add" and row.get("outcome") == "before"
            for row in trace_rows[attached_at + 1:]),
    }


def _queue_join_evidence(trace_rows: list[dict], item_rows: list[dict],
                         delivery_id: str, turn_id: str | None, hook_join: bool) -> dict:
    """Join actual queue, native-item, and Store identities; attachment alone is not a hit."""
    evidence = _queue_observer_evidence(trace_rows)
    queue_add = next((row for row in reversed(trace_rows) if row.get("stage") == "queue_add"), {})
    queue_start = next((row for row in reversed(trace_rows) if row.get("stage") == "queue_start"), {})
    delivery_digest = _digest(delivery_id.encode())
    observed_items = [row for row in item_rows
        if row.get("stage") == "native_item_started" and row.get("outcome") == "observed"
        and row.get("delivery_id_matches") is True and row.get("item_thread_matches") is True
        and row.get("item_is_user_message") is True
        and row.get("delivery_id_sha256") == delivery_digest]
    started_item = observed_items[-1] if observed_items else {}
    evidence.update(
        queue_add_rpc=queue_add.get("outcome", "not_observed"),
        receipt_confirmed=queue_add.get("outcome") == "after" and queue_add.get("receipt_confirmed") is True
            and isinstance(queue_add.get("receipt_id_sha256"), str) and bool(queue_add["receipt_id_sha256"]),
        delivery_id_matches_receipt=queue_add.get("delivery_id_matches") is True
            and queue_add.get("delivery_id_sha256") == delivery_digest,
        queue_start_rpc=queue_start.get("outcome", "not_observed"),
        queue_start_refusal_class=queue_start.get("native_refusal_class"),
        start_matches_receipt=queue_start.get("receipt_matches_start") is True,
        native_turn_started=queue_start.get("turn_started") is True,
        item_started="observed" if started_item else "not_observed",
        item_notification_trace_rows=len(item_rows),
        native_turn_hash_matches_store_attempt=bool(
            turn_id and started_item.get("turn_id_sha256") == _digest(turn_id.encode())),
    )
    evidence["queue_receipt_to_item_started"] = bool(
        evidence["receipt_confirmed"] and evidence["delivery_id_matches_receipt"] and started_item)
    evidence["exact_queue_hook_store_join"] = bool(
        evidence["queue_observer_attached"] and evidence["queue_observer_hit"]
        and evidence["queue_receipt_to_item_started"] and hook_join
        and evidence["native_turn_hash_matches_store_attempt"])
    return evidence


def _queue_join_succeeded(report: dict, *, inside: bool = False) -> bool:
    if type(report.get("watcher_exit")) is not int or report["watcher_exit"] != 0:
        return False
    if not inside and (type(report.get("outer_exit")) is not int or report["outer_exit"] != 0):
        return False
    return (report.get("queue_observer_attached") is True
            and report.get("queue_observer_hit") is True
            and report.get("exact_queue_hook_store_join") is True
            and report.get("hook_join") is True)


def _scripted_handled_succeeded(report: dict, *, inside: bool = False) -> bool:
    return (_queue_join_succeeded(report, inside=inside) and report.get("exact_source_read") is True
            and report.get("acknowledgement") == "handled_confirmed"
            and report.get("settlement_exactly_once") is True)


def _failure_stage(summary: dict, trace_rows: list[dict]) -> str:
    if not summary.get("queue_observer_attached"):
        return "queue_observer_not_attached"
    if not summary.get("queue_observer_hit"):
        return "queue_observer_not_hit"
    for row in reversed(trace_rows):
        if row.get("outcome") == "refused":
            return row.get("stage", "other") + ":" + row.get("error_class", "OtherError")
    if summary["queue_add_rpc"] in ("before", "not_observed"):
        return "before_queue_add"
    if not summary.get("receipt_confirmed") or not summary.get("delivery_id_matches_receipt"):
        return "queue_add_or_receipt_validation"
    if not summary.get("queue_receipt_to_item_started"):
        return "queue_receipt_to_native_item"
    if not summary.get("hook_join"):
        return "hook_to_store_attempt"
    if not summary.get("native_turn_hash_matches_store_attempt"):
        return "native_item_to_store_turn"
    return "joined" if summary.get("exact_queue_hook_store_join") else "queue_hook_store_join_unconfirmed"


def _preflight_inner() -> int:
    require(Path(__file__).resolve() == Path("/repo/scripts/check_codex_app_server_wake_join.py"),
            "runner_path_not_mounted")
    trace = Path(os.environ["CAIRN220_QUEUE_TRACE"])
    descriptor = create_trace(str(trace))
    try:
        append_trace(descriptor, "trace", "started")
    finally:
        os.close(descriptor)
    info = trace.stat(follow_symlinks=False)
    require(stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o600 and not trace.is_symlink(), "trace_path_not_private")
    print(json.dumps({"runner": "/repo/scripts/check_codex_app_server_wake_join.py",
                      "trace_created": True, "trace_mode": "0600"}, sort_keys=True))
    return 0


def run_once(scripted_handled: bool = False) -> dict:
    _private_trace_home()
    run_id = uuid.uuid4().hex
    trace_name = "run-queue-stage-" + run_id + ".jsonl"
    trace_path = TRACE_HOME / trace_name
    bootstrap_path = TRACE_HOME / ("bootstrap-" + run_id + ".jsonl")
    global _BOOTSTRAP_FD, _BOOTSTRAP_PATH, _PHASE
    _BOOTSTRAP_FD = create_trace(str(bootstrap_path))
    _BOOTSTRAP_PATH = bootstrap_path
    append_trace(_BOOTSTRAP_FD, "bootstrap", "started", step="runner")
    # Prove the exact mounted script and observer trace endpoint before creating
    # the disposable database or launching Codex.
    root = None
    pg_bin = None
    postgres_started = False
    api_process = None
    socket_dir = None
    try:
        require(NATIVE.is_file() and not NATIVE.is_symlink(), "pinned_codex_missing")
        _PHASE = "codex_version"
        version = _checked_run([str(NATIVE), "--version"], timeout=5).stdout.decode().strip()
        require(version == VERSION, "pinned_codex_version_mismatch")
        require(shutil.which("bwrap") is not None, "bwrap_missing")
        require(not trace_path.exists() and not trace_path.is_symlink(), "trace_path_already_exists")
        _PHASE = "mounted_preflight"
        _bootstrap(_PHASE, "before")
        preflight = _preflight(TRACE_HOME, trace_path)
        _bootstrap(_PHASE, "after")
        root = Path(tempfile.mkdtemp(prefix="fixture-" + run_id + "-", dir=TRACE_HOME))
        root.chmod(0o700)
        pg_bin = Path(_checked_run(["pg_config", "--bindir"], timeout=5).stdout.decode().strip())
        _postgres_bootstrap_preflight(root, pg_bin)
        pg_bin, binary, context = _host_setup(root, trace_path)
        socket_dir = context["socket_dir"]
        api_process = context["api_process"]
        postgres_started = True
        profile = context["profile"]
        repo = context["repo"]
        context.update(binary=binary, root=root, profile=profile, repo=repo)
        fixture = {key: str(context[key]) for key in ("api", "profile", "binary")}
        fixture.update(repo=repo, trace_name=trace_name, run_id=run_id,
                       scripted_handled=scripted_handled,
                       api_socket="/trace/" + socket_dir.name + "/api.sock")
        _write_private(root / "fixture.json", json.dumps(fixture, sort_keys=True))
        prefix = _sandbox_prefix(root, trace_name, preflight=False)
        command = prefix + ["--", "/usr/bin/python3", "/repo/scripts/check_codex_app_server_wake_join.py",
                            "--inside", "/fixture"]
        if scripted_handled:
            command.append("--scripted-handled")
        _PHASE = "watcher_bwrap"
        _bootstrap(_PHASE, "before")
        proc = subprocess.Popen(command, env={"PATH": "/usr/bin:/bin"},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate(timeout=90)
        _bootstrap(_PHASE, "after", flags={"exit_zero": proc.returncode == 0})
        item_trace_path = TRACE_HOME / ("item-stage-" + run_id + ".jsonl")
        script_trace_path = TRACE_HOME / ("script-stage-" + run_id + ".jsonl")
        item_trace_rows = []
        item_trace_present = item_trace_path.exists() or item_trace_path.is_symlink()
        if item_trace_present:
            item_trace_info = item_trace_path.stat(follow_symlinks=False)
            require(not stat.S_ISLNK(item_trace_info.st_mode) and stat.S_ISREG(item_trace_info.st_mode)
                    and item_trace_info.st_uid == os.geteuid()
                    and stat.S_IMODE(item_trace_info.st_mode) == 0o600,
                    "item_trace_mode_or_owner_changed")
            item_trace_rows = [json.loads(line) for line in item_trace_path.read_text().splitlines()]
        script_trace_present = script_trace_path.exists() or script_trace_path.is_symlink()
        script_trace_rows = []
        if script_trace_present:
            script_trace_info = script_trace_path.stat(follow_symlinks=False)
            require(not stat.S_ISLNK(script_trace_info.st_mode) and stat.S_ISREG(script_trace_info.st_mode)
                    and script_trace_info.st_uid == os.geteuid()
                    and stat.S_IMODE(script_trace_info.st_mode) == 0o600,
                    "script_trace_mode_or_owner_changed")
            script_trace_rows = [json.loads(line) for line in script_trace_path.read_text().splitlines()]
        trace_present = trace_path.exists() or trace_path.is_symlink()
        if trace_present:
            trace_stat = trace_path.stat(follow_symlinks=False)
            require(not stat.S_ISLNK(trace_stat.st_mode) and stat.S_ISREG(trace_stat.st_mode)
                    and trace_stat.st_uid == os.geteuid() and
                    stat.S_IMODE(trace_stat.st_mode) == 0o600,
                    "trace_mode_or_owner_changed")
            trace_rows = [json.loads(line) for line in trace_path.read_text().splitlines()]
        else:
            trace_rows = []
        result_path = root / "inside-result.json"
        report = json.loads(result_path.read_text()) if result_path.exists() else {}
        report.update(run_id=run_id, runner_preflight=preflight,
                      outer_exit=proc.returncode, outer_stderr_sha256=_digest(stderr),
                      trace_mode="0600", trace_rows=len(trace_rows),
                      trace_error_classes=sorted({row.get("error_class") for row in trace_rows
                                                  if row.get("error_class")}))
        report["bootstrap_trace"] = bootstrap_path.name
        report["queue_trace_present"] = trace_present
        report.update(_queue_observer_evidence(trace_rows))
        if not report["queue_observer_attached"] or not report["queue_observer_hit"]:
            report["exact_queue_hook_store_join"] = False
            report["failure_stage"] = _failure_stage(report, trace_rows)
        report["item_trace_present"] = item_trace_present
        report["script_trace_present"] = script_trace_present if scripted_handled else False
        report["script_trace_rows"] = len(script_trace_rows)
        report["script_trace_name"] = script_trace_path.name if script_trace_present else None
        if scripted_handled and not script_trace_present:
            report["continuation_failure_stage"] = "script_trace_missing"
        if not trace_present:
            report["failure_stage"] = _classify_missing_queue_trace(item_trace_rows)
            report["failure_code"] = "queue_trace_missing"
        _write_private(TRACE_HOME / ("summary-" + run_id + ".json"), json.dumps(report, sort_keys=True))
        return report
    finally:
        cleanup_ok = True
        if api_process is not None:
            api_process.terminate()
            try:
                api_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api_process.kill()
                api_process.wait(timeout=5)
        if postgres_started and pg_bin and root is not None:
            cleanup_ok = _stop_disposable_postgres(pg_bin, root / "pgdata")
            if not cleanup_ok:
                _bootstrap("pg_cleanup", "failed", "OtherError")
        if root is not None and (root / "pgdata" / "postmaster.pid").exists():
            cleanup_ok = False
            _bootstrap("pg_cleanup", "failed", "OtherError")
        if socket_dir is not None and cleanup_ok:
            shutil.rmtree(socket_dir, ignore_errors=True)
        if root is not None and cleanup_ok:
            shutil.rmtree(root, ignore_errors=True)
        if _BOOTSTRAP_FD is not None:
            os.close(_BOOTSTRAP_FD)
            _BOOTSTRAP_FD = None


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--api-preflight":
        try:
            report = _run_api_preflight()
        except Exception as exc:
            code = str(exc) if str(exc) in {"disposable_api_start_failed", "disposable_api_socket_timeout",
                                            "pg_cleanup_failed"} else type(exc).__name__
            report = {"result": "FAIL", "failure_code": code, "watcher_started": False}
            if _BOOTSTRAP_PATH:
                report["bootstrap_trace"] = _BOOTSTRAP_PATH.name
            print(json.dumps(report, sort_keys=True))
            return 1
        print(json.dumps(report, sort_keys=True))
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "--postgres-preflight":
        try:
            report = _run_postgres_preflight()
        except Exception as exc:
            code = str(exc) if str(exc) in {"pg_start_failed", "pg_start_timeout", "pg_cleanup_failed"} else type(exc).__name__
            report = {"result": "FAIL", "failure_code": code}
            if _BOOTSTRAP_PATH:
                report["bootstrap_trace"] = _BOOTSTRAP_PATH.name
            print(json.dumps(report, sort_keys=True))
            return 1
        print(json.dumps(report, sort_keys=True))
        return 0
    if len(sys.argv) == 2 and sys.argv[1] == "--preflight-inner":
        return _preflight_inner()
    if len(sys.argv) == 3 and sys.argv[1] == "--inside":
        return _inside(sys.argv[2])
    if len(sys.argv) == 4 and sys.argv[1] == "--inside" and sys.argv[3] == "--scripted-handled":
        return _inside(sys.argv[2], scripted_handled=True)
    scripted_handled = len(sys.argv) == 2 and sys.argv[1] == "--scripted-handled"
    if len(sys.argv) == 2 and not scripted_handled:
        print(json.dumps({"result": "FAIL", "failure_code": "unsupported_argument"}, sort_keys=True))
        return 1
    try:
        report = run_once(scripted_handled=scripted_handled)
    except Exception as exc:
        code = str(exc) if str(exc) in {
            "pinned_codex_missing", "pinned_codex_version_mismatch", "bwrap_missing",
            "trace_home_symlink", "trace_home_not_private", "trace_path_already_exists",
            "mounted_path_preflight_failed", "mounted_path_preflight_report_invalid",
            "mounted_path_preflight_mismatch", "host_trace_preflight_mismatch",
            "trace_preflight_contents_mismatch", "trace_mode_or_owner_changed",
            "pg_cleanup_failed",
        } or any(str(exc) == step + suffix for step in (
            "pg_config", "go_build", "pg_initdb", "pg_start", "db_create", "migrate",
            "api_start", "api_ready", "codex_version") for suffix in ("_failed", "_timeout")) else type(exc).__name__
        failure = {"schema": "cairn.codex-app-server-wake-join/2",
                   "result": "FAIL", "failure_code": code}
        if _BOOTSTRAP_PATH:
            failure["bootstrap_trace"] = _BOOTSTRAP_PATH.name
        print(json.dumps(failure, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    if scripted_handled:
        return 0 if _scripted_handled_succeeded(report) else 1
    return 0 if _queue_join_succeeded(report) else 1


if __name__ == "__main__":
    sys.exit(main())
