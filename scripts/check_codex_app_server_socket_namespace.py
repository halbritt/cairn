#!/usr/bin/env python3
"""Loopback-only Codex app-server socket and queue probe inside fixture bwrap."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from typing import BinaryIO


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
NATIVE = (Path.home() / ".npm-global/lib/node_modules/@openai/codex/node_modules/"
          "@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex")
MAX_STDERR = 4096
STARTUP_SECONDS = 8
READY_SECONDS = 20
MODEL = "gpt-6-astra"


def _read_bounded(stream: BinaryIO, result: dict[str, object]) -> None:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(1024)
        if not chunk:
            break
        if total < MAX_STDERR:
            chunks.append(chunk[:MAX_STDERR - total])
        total += len(chunk)
    result["stderr"] = b"".join(chunks).decode("utf-8", errors="replace")
    result["stderr_truncated"] = total > MAX_STDERR


def _start_time(pid: int) -> int:
    return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])


def _safe_diagnostic(value: object, root: Path) -> str:
    text = str(value or "").replace(str(root), "<fixture-root>")
    text = re.sub(r"(?i)sk-[A-Za-z0-9_-]{8,}", "<redacted-key>", text)
    text = re.sub(r"(?i)Bearer\s+\S+", "Bearer <redacted>", text)
    return text.encode("utf-8", errors="replace")[:MAX_STDERR].decode("utf-8", errors="replace")


def _inside(root_text: str) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    sys.path.insert(0, "/probe")
    import codex_queue
    from check_codex_inbox import response_stream

    root = Path(root_text)
    profile = Path(os.environ["CODEX_HOME"])
    endpoint = profile / "app.sock"
    ready = root / "probe-ready.json"
    stop = root / "probe-stop"
    requests: list[dict[str, object]] = []
    response_done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_: object) -> None:
            pass

        def do_GET(self) -> None:
            if len(requests) >= 4:
                self.send_error(413)
                return
            requests.append({"method": "GET", "path": self.path})
            if self.path != "/v1/models":
                self.send_error(404)
                return
            body = json.dumps({"object": "list", "data": [
                {"id": MODEL, "object": "model", "created": 0, "owned_by": "fixture"}
            ]}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            if len(requests) >= 4:
                self.send_error(413)
                return
            requests.append({"method": "POST", "path": self.path, "bytes": size})
            if self.path != "/v1/responses" or not 0 < size <= 2_000_000:
                self.send_error(404)
                return
            self.rfile.read(size)
            body = response_stream("resp_cairn220", dict(
                type="message", id="msg_cairn220", role="assistant", status="completed",
                content=[dict(type="output_text", text="Synthetic local response.", annotations=[])]))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            response_done.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    # A queue/add on an idle thread may dispatch immediately; confine that turn to loopback.
    config = (
        f'model = "{MODEL}"\nmodel_provider = "fixture"\n'
        'model_reasoning_effort = "low"\ncheck_for_update_on_startup = false\n'
        '[model_providers.fixture]\nname = "CAIRN-220 loopback fixture"\n'
        f'base_url = "http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        'request_max_retries = 0\nstream_max_retries = 0\n'
    )
    (profile / "config.toml").write_text(config, encoding="utf-8")
    (profile / "config.toml").chmod(0o600)
    (profile / "auth.json").write_text('{"OPENAI_API_KEY":"sk-cairn220-fake"}\n', encoding="utf-8")
    (profile / "auth.json").chmod(0o600)
    server_thread.start()

    native = "/opt/codex-bin/codex"
    child = subprocess.Popen([native, "app-server", "--listen", "unix://" + str(endpoint)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    diagnostics: dict[str, object] = {}
    stderr_thread = threading.Thread(target=_read_bounded, args=(child.stderr, diagnostics), daemon=True)
    stderr_thread.start()
    try:
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline and child.poll() is None and not endpoint.is_socket():
            time.sleep(0.025)
        if child.poll() is not None or not endpoint.is_socket():
            raise RuntimeError("app_server_socket_not_ready")

        transport = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        transport.settimeout(4)
        transport.connect(str(endpoint))
        peer_pid, peer_uid, peer_gid = struct.unpack(
            "3i", transport.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        transport.close()
        if peer_pid != child.pid or peer_uid != os.geteuid():
            raise RuntimeError("socket_peer_credential_mismatch")

        process = {"pid": child.pid, "start": _start_time(child.pid),
                   "boot": Path("/proc/sys/kernel/random/boot_id").read_text().strip()}
        connection, _websocket, _identity = codex_queue._open(str(endpoint), process, "process")
        try:
            rpc = codex_queue._Rpc(connection, time.monotonic() + 12)
            rpc("initialize", dict(clientInfo=dict(name="cairn-220-socket-probe", version="1"),
                                   capabilities=dict(experimentalApi=True)))
            connection.send(json.dumps(dict(method="initialized")))
            started = rpc("thread/start", dict(cwd=str(root), model=MODEL,
                approvalPolicy="never", sandbox="danger-full-access"))
            thread_id = started["thread"]["id"]
            loaded = rpc("thread/loaded/list", dict(limit=100))
            if thread_id not in loaded.get("data", []):
                raise RuntimeError("new_thread_missing_from_loaded_list")
            client_id = str(uuid.uuid4())
            queued = rpc("thread/queue/add", dict(threadId=thread_id,
                clientUserMessageId=client_id,
                input=[dict(type="text", text="CAIRN-220 local socket probe.")]))
            submission = queued.get("queuedSubmission", {})
            if submission.get("clientUserMessageId") != client_id or not submission.get("id"):
                raise RuntimeError("queue_add_response_mismatch")
        finally:
            connection.close(timeout=0)

        response_done.wait(5)
        report = {
            "schema": "cairn.codex-app-server-socket-probe/1",
            "codex_version": "0.164.0",
            "network": "bwrap --unshare-net; loopback fixture only",
            "observer": "isolated probe using production codex_queue client",
            "socket_inside": "unix socket",
            "socket_peer": {"pid_matches_app_server": True, "uid_matches_watcher": True,
                             "gid": peer_gid},
            "thread_loaded": True,
            "queue_method": "thread/queue/add",
            "queue_add": "accepted",
            "queued_submission_id_present": True,
            "client_user_message_id_matches": True,
            "local_responses_requests": sum(r["path"] == "/v1/responses" for r in requests),
            "local_response_stub_replied": response_done.is_set(),
            "local_model_requests": sum(r["path"] == "/v1/models" for r in requests),
            "unexpected_local_paths": [r["path"] for r in requests
                                       if r["path"] not in ("/v1/models", "/v1/responses")],
            "native_item_started": "not_observed",
            "explicit_queue_start_called": False,
            "production_watcher": False,
            "cairn_api_mounted": False,
        }
        ready.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        deadline = time.monotonic() + READY_SECONDS
        while time.monotonic() < deadline and not stop.exists():
            if child.poll() is not None:
                raise RuntimeError("app_server_exited_after_queue_add")
            time.sleep(0.025)
        if not stop.exists():
            raise RuntimeError("host_socket_observation_timeout")
        if child.poll() is not None:
            raise RuntimeError("app_server_exited_before_cleanup")
    except Exception as exc:
        failure = {"schema": "cairn.codex-app-server-socket-probe/1",
                   "error_class": type(exc).__name__, "error_code": str(exc)[:120]}
        if isinstance(exc, codex_queue.QueueRefused):
            failure["native_refusal"] = {"code": exc.code, "message": exc.message[:160]}
        ready.write_text(json.dumps(failure, sort_keys=True) + "\n", encoding="utf-8")
        return 1
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        stderr_thread.join(timeout=2)
        child.stderr.close()
        if ready.exists():
            result = json.loads(ready.read_text(encoding="utf-8"))
            result.update({k: diagnostics.get(k) for k in ("stderr", "stderr_truncated")})
            ready.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    return 0


def run_probe() -> dict[str, object]:
    if not NATIVE.is_file():
        raise RuntimeError(f"pinned Codex binary unavailable: {NATIVE}")
    if shutil.which("bwrap") is None:
        raise RuntimeError("bwrap unavailable")
    version = subprocess.run([str(NATIVE), "--version"], capture_output=True,
                             text=True, timeout=5, check=True).stdout.strip()
    if version != "codex-cli 0.164.0":
        raise RuntimeError(f"expected pinned Codex 0.164.0, found {version}")

    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    from trial_task_eval import runtime_sandbox

    cache = Path.home() / ".cache"
    cache.mkdir(mode=0o700, exist_ok=True)
    report: dict[str, object]
    with tempfile.TemporaryDirectory(prefix="c220-", dir=cache) as temp:
        root = Path(temp)
        os.chmod(root, 0o700)
        profile = root / "codex-home"
        profile.mkdir(mode=0o700)
        shutil.copyfile(__file__, root / "probe.py")
        os.chmod(root / "probe.py", 0o600)
        # Bind the existing queue client so the probe follows its production
        # SO_PEERCRED, WebSocket, thread-loaded, and queue/add checks.
        queue_module = REPO / "integrations/lifecycle/codex_queue.py"
        (root / "probe-modules").mkdir(mode=0o700)
        shutil.copyfile(queue_module, root / "probe-modules/codex_queue.py")
        os.chmod(root / "probe-modules/codex_queue.py", 0o600)
        shutil.copyfile(SCRIPTS / "check_codex_inbox.py",
                        root / "probe-modules/check_codex_inbox.py")
        os.chmod(root / "probe-modules/check_codex_inbox.py", 0o600)
        runtime = runtime_sandbox(include_home_go=False)
        args = runtime + [
            "--unshare-net", "--bind", str(root), str(root),
            "--ro-bind", str(NATIVE.parent), "/opt/codex-bin",
            "--ro-bind", str(root / "probe-modules"), "/probe",
            "--setenv", "HOME", str(profile),
            "--setenv", "CODEX_HOME", str(profile),
            "--setenv", "PATH", "/usr/bin:/bin",
            "--setenv", "LANG", "C.UTF-8",
            "--chdir", str(root),
            "--", "/usr/bin/python3", str(root / "probe.py"), "--inside", str(root),
        ]
        env = {"PATH": "/usr/bin:/bin"}
        process = subprocess.Popen(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        outer_stderr: dict[str, object] = {}
        drain = threading.Thread(target=_read_bounded, args=(process.stderr, outer_stderr), daemon=True)
        drain.start()
        ready_path = root / "probe-ready.json"
        deadline = time.monotonic() + STARTUP_SECONDS + 15
        while time.monotonic() < deadline and process.poll() is None and not ready_path.exists():
            time.sleep(0.025)
        if not ready_path.exists():
            process.kill()
            process.wait()
            drain.join(timeout=2)
            process.stderr.close()
            raise RuntimeError("sandbox_probe_did_not_report")
        report = json.loads(ready_path.read_text(encoding="utf-8"))
        report["codex_binary_sha256"] = hashlib.sha256(NATIVE.read_bytes()).hexdigest()
        if report.get("queue_add") != "accepted":
            (root / "probe-stop").touch()
            process.wait(timeout=5)
            drain.join(timeout=2)
            process.stderr.close()
            report = json.loads(ready_path.read_text(encoding="utf-8"))
            report["stderr"] = _safe_diagnostic(report.get("stderr"), root)
            report["outer_bwrap_stderr"] = _safe_diagnostic(outer_stderr.get("stderr"), root)
            report["outer_bwrap_stderr_truncated"] = bool(outer_stderr.get("stderr_truncated"))
            report["output_redacted"] = True
        else:
            endpoint = profile / "app.sock"
            link = os.readlink(endpoint) if endpoint.is_symlink() else None
            target = Path(link) if link else None
            report["socket_outside"] = {
                "directory_entry_exists": endpoint.is_symlink() or endpoint.exists(),
                "entry_is_symlink": endpoint.is_symlink(),
                "entry_is_socket": endpoint.is_socket(),
                "target_under_private_tmp": bool(link and link.startswith("/tmp/codex-daemon-")),
                "target_exists": bool(target and target.exists()),
                "target_is_socket": bool(target and target.is_socket()),
            }
            (root / "probe-stop").touch()
            process.wait(timeout=5)
            drain.join(timeout=2)
            process.stderr.close()
            final = json.loads(ready_path.read_text(encoding="utf-8"))
            if final.get("queue_add") != "accepted":
                report.update(final)
            report.update({k: final.get(k) for k in ("stderr_truncated",)})
            report["stderr"] = _safe_diagnostic(final.get("stderr"), root)
            report["outer_bwrap_stderr"] = _safe_diagnostic(outer_stderr.get("stderr"), root)
            report["outer_bwrap_stderr_truncated"] = bool(outer_stderr.get("stderr_truncated"))
        report["output_redacted"] = True
    report["scratch_removed"] = not root.exists()
    return report


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--inside":
        return _inside(sys.argv[2])
    result = run_probe()
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("queue_add") == "accepted" else 1


if __name__ == "__main__":
    sys.exit(main())
