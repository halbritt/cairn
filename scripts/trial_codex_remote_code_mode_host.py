#!/usr/bin/env python3
"""Provider-free proof for split Codex app-server/code-mode-host isolation.

The app-server uses a local-only Responses stub and a synthetic auth marker.
The standalone code-mode host runs under bwrap with only a synthetic actor
workspace and system runtime mounted. No subscription credentials are read.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import selectors
import shlex
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time


MODEL = "gpt-6-luna"
MARKER = "SYNTHETIC_AUTH_MARKER_MUST_NOT_LEAK_62a91c"
BENIGN = "REMOTE_CODE_MODE_BWRAP_OK_31b8d4"


def response_stream(response_id: str, item: dict) -> bytes:
    response = dict(id=response_id, object="response", created_at=0,
                    status="in_progress", output=[], model=MODEL)
    events = [dict(type="response.created", response=response),
              dict(type="response.output_item.added", output_index=0,
                   item=dict(item, status="in_progress"))]
    if item["type"] == "message":
        part = item["content"][0]
        events.extend([
            dict(type="response.content_part.added", item_id=item["id"],
                 output_index=0, content_index=0, part=dict(part, text="")),
            dict(type="response.output_text.delta", item_id=item["id"],
                 output_index=0, content_index=0, delta=part["text"]),
            dict(type="response.output_text.done", item_id=item["id"],
                 output_index=0, content_index=0, text=part["text"]),
            dict(type="response.content_part.done", item_id=item["id"],
                 output_index=0, content_index=0, part=part),
        ])
    events.extend([
        dict(type="response.output_item.done", output_index=0, item=item),
        dict(type="response.completed", response=dict(
            response, status="completed", output=[item],
            usage=dict(input_tokens=2, output_tokens=2, total_tokens=4))),
    ])
    return "".join(
        "event: " + event["type"] + "\ndata: " +
        json.dumps(dict(event, sequence_number=index)) + "\n\n"
        for index, event in enumerate(events)
    ).encode()


def exec_tool(call_id: str, command: str) -> dict:
    program = "text(await tools.exec_command(" + json.dumps(dict(
        cmd=command, max_output_tokens=1000)) + "));"
    return dict(type="custom_tool_call", id="ctc_" + call_id,
                name="exec", namespace="functions", call_id=call_id,
                input=program)


class Rpc:
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)
        self.buffer = bytearray()
        self.notifications: queue.Queue[dict] = queue.Queue()
        self.next_id = 1

    def send(self, method: str, params: dict | None = None) -> int:
        ident = self.next_id
        self.next_id += 1
        wire = dict(jsonrpc="2.0", id=ident, method=method, params=params or {})
        self.process.stdin.write(json.dumps(wire, separators=(",", ":")).encode() + b"\n")
        self.process.stdin.flush()
        return ident

    def _read_line(self, deadline: float) -> dict:
        while time.monotonic() < deadline:
            if b"\n" in self.buffer:
                line, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                return json.loads(line)
            ready = self.selector.select(max(0, deadline - time.monotonic()))
            if not ready:
                break
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                raise RuntimeError("app-server stdout closed")
            self.buffer.extend(chunk)
            if len(self.buffer) > 4 * 1024 * 1024:
                raise RuntimeError("app-server response exceeded fixture bound")
        raise TimeoutError("app-server RPC deadline")

    def result(self, ident: int, timeout: float = 10) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            row = self._read_line(deadline)
            if row.get("id") == ident:
                if "error" in row:
                    raise RuntimeError("app-server RPC error: " +
                                       str(row["error"].get("message", "unknown")))
                return row.get("result") or {}
            if "method" in row:
                self.notifications.put(row)

    def notification(self, method: str, turn_id: str | None,
                     timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                row = self.notifications.get_nowait()
            except queue.Empty:
                row = self._read_line(deadline)
                if "id" in row:
                    continue
            if row.get("method") != method:
                continue
            params = row.get("params") or {}
            current = (params.get("turn") or {}).get("id") or params.get("turnId")
            if turn_id is None or current == turn_id:
                return row
        raise TimeoutError("missing app-server notification " + method)


def free_tcp_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def app_server_config(path: Path, response_port: int) -> None:
    path.write_text(
        'model = "gpt-6-luna"\nmodel_provider = "fixture"\n'
        'approval_policy = "never"\ncheck_for_update_on_startup = false\n'
        '[analytics]\nenabled = false\n'
        '[history]\npersistence = "none"\n'
        '[model_providers.fixture]\nname = "Local provider-free fixture"\n'
        f'base_url = "http://127.0.0.1:{response_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        'request_max_retries = 0\nstream_max_retries = 0\n', encoding="utf-8")
    with path.open("a", encoding="utf-8") as config:
        config.write("\n[features.code_mode_host]\ndisable_in_process_fallback = true\n")
    path.chmod(0o600)


def sandbox_base(workspace: Path) -> list[str]:
    command = ["bwrap", "--die-with-parent", "--unshare-pid", "--unshare-ipc",
               "--unshare-uts", "--cap-drop", "ALL", "--clearenv",
               "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp",
               "--dir", "/home"]
    for path in ("/usr/bin", "/usr/lib", "/usr/lib64", "/usr/share/ca-certificates",
                 "/etc/ssl/certs", "/etc/resolv.conf", "/etc/hosts",
                 "/etc/nsswitch.conf", "/etc/ld.so.cache"):
        if Path(path).exists():
            command += ["--ro-bind", str(Path(path).resolve()), path]
    for path in ("/bin", "/sbin", "/lib", "/lib64"):
        target = Path(path)
        if target.is_symlink():
            command += ["--symlink", os.readlink(target), path]
        elif target.exists():
            command += ["--ro-bind", path, path]
    command += ["--dir", "/work", "--ro-bind", str(workspace), "/work",
                "--chdir", "/work", "--setenv", "HOME", "/home",
                "--setenv", "CODEX_HOME", "/home/codex-home",
                "--setenv", "PATH", "/usr/bin:/bin", "--setenv", "LANG", "C.UTF-8"]
    return command


def sandbox_host_command(workspace: Path, host_bin: Path, port: int) -> list[str]:
    command = sandbox_base(workspace)
    command += ["--dir", "/tmp/hostbin", "--ro-bind", str(host_bin),
                "/tmp/hostbin/codex-code-mode-host", "--",
                "/tmp/hostbin/codex-code-mode-host", "--listen",
                f"grpc://127.0.0.1:{port}"]
    return command


def marker_outputs(request: dict) -> list[str]:
    inputs = request.get("input") or []
    return [json.dumps(item, sort_keys=True) for item in inputs
            if item.get("type") == "custom_tool_call_output"]


def process_group_has_live_members(pgid: int) -> bool:
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat_line = (entry / "stat").read_text()
            after_name = stat_line.rsplit(")", 1)[1].split()
            state, process_group = after_name[0], int(after_name[2])
        except (OSError, ValueError, IndexError):
            continue
        if process_group == pgid and state != "Z":
            return True
    return False


def run_probe(root: Path, codex_entry: Path, host_bin: Path) -> dict:
    if not shutil.which("bwrap"):
        raise RuntimeError("bwrap_unavailable")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    app_home = root / "app-home"
    actor_workspace = root / "actor-workspace"
    app_home.mkdir(mode=0o700)
    actor_workspace.mkdir(mode=0o700)
    marker_file = app_home / "auth.json"
    marker_file.write_text(json.dumps(dict(auth_mode="synthetic", token=MARKER)) + "\n",
                           encoding="utf-8")
    marker_file.chmod(0o600)
    (actor_workspace / "task.txt").write_text("synthetic actor workspace\n", encoding="utf-8")

    response_server: ThreadingHTTPServer
    response_requests: list[dict] = []
    response_failures: list[str] = []
    stall_reached = threading.Event()
    release_stall = threading.Event()
    first_turn_finished = threading.Event()
    stub_state = {"issued_benign": False, "issued_marker": False,
                  "marker_leaked": False, "stall": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            if self.path != "/v1/responses" or not 0 < size <= 2 * 1024 * 1024:
                self.send_error(400)
                return
            try:
                request = json.loads(self.rfile.read(size))
                response_requests.append(request)
                all_tools = [row for row in (request.get("input") or [])
                             if row.get("type") == "custom_tool_call_output"]
                if stub_state["stall"]:
                    stall_reached.set()
                    release_stall.wait(5)
                    try:
                        self.send_error(503)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # The app-server correctly closed the interrupted request.
                    return
                if not stub_state["issued_benign"]:
                    stub_state["issued_benign"] = True
                    item = exec_tool("benign", "printf " + shlex.quote(BENIGN))
                elif not stub_state["issued_marker"]:
                    prior = json.dumps(all_tools[0], sort_keys=True)
                    if BENIGN not in prior:
                        raise AssertionError("benign_exec_result_missing")
                    stub_state["issued_marker"] = True
                    item = exec_tool("marker", "cat " + shlex.quote(str(marker_file)))
                else:
                    prior = json.dumps(all_tools[-1], sort_keys=True)
                    if MARKER in prior:
                        stub_state["marker_leaked"] = True
                    elif not any(fragment in prior.lower() for fragment in
                                 ("no such file", "not found", "permission denied")):
                        raise AssertionError("marker_read_was_not_denied")
                    item = dict(type="message", id="msg_done", role="assistant",
                                status="completed", content=[dict(
                                    type="output_text", text="Isolation probe complete.",
                                    annotations=[])])
                body = response_stream("resp_" + str(len(response_requests)), item)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                if len(all_tools) == 2:
                    first_turn_finished.set()
            except (AssertionError, ValueError, KeyError, TypeError) as error:
                response_failures.append(str(error))
                self.send_error(500)

    response_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    response_server.daemon_threads = True
    server_thread = threading.Thread(target=response_server.serve_forever, daemon=True)
    server_thread.start()

    app_server_config(app_home / "config.toml", response_server.server_port)
    mode_port = free_tcp_port()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("OPENAI_") and not key.startswith("OPENROUTER_")
           and key not in ("CODEX_HOME", "CODEX_API_KEY", "ACCESS_TOKEN")}
    env.update(CODEX_HOME=str(app_home), HOME=str(app_home),
               PATH="/usr/bin:/bin", LANG="C.UTF-8", NO_PROXY="127.0.0.1,localhost",
               no_proxy="127.0.0.1,localhost")
    host = None
    app = None
    app_rpc = None
    report = None
    cleanup_ok = []
    try:
        # Establish that the bwrap view itself cannot read the app-server's
        # synthetic auth fixture before testing Codex dispatch through it.
        direct = subprocess.run(sandbox_base(actor_workspace) + ["--", "/bin/cat",
                                  str(marker_file)], capture_output=True, timeout=5)
        if direct.returncode == 0 or MARKER.encode() in direct.stdout + direct.stderr:
            raise RuntimeError("bwrap_baseline_failed_to_hide_synthetic_auth_marker")

        host_command = sandbox_host_command(actor_workspace, host_bin, mode_port)
        host = subprocess.Popen(host_command, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, start_new_session=True)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if host.poll() is not None:
                detail = host.stderr.read(4096).decode(errors="replace")
                raise RuntimeError("remote_code_mode_host_failed_to_start: " + detail[:500])
            try:
                with socket.create_connection(("127.0.0.1", mode_port), timeout=.2):
                    break
            except OSError:
                time.sleep(.05)
        else:
            raise TimeoutError("remote_code_mode_host_listener_timeout")

        app_command = ["/usr/bin/node", str(codex_entry), "app-server", "--listen",
                       "stdio://", "--code-mode-host", f"http://127.0.0.1:{mode_port}",
                       "--enable", "code_mode", "--enable", "code_mode_host",
                       "--enable", "code_mode_only", "--enable", "code_mode_interrupt"]
        app = subprocess.Popen(app_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, env=env, start_new_session=True)
        app_rpc = Rpc(app)
        init_id = app_rpc.send("initialize", dict(
            clientInfo=dict(name="cairn-remote-code-mode-host-probe", title="Cairn probe",
                            version="1"), capabilities=dict(experimentalApi=True)))
        app_rpc.result(init_id, 10)
        app_rpc.send("initialized")
        thread_result = app_rpc.send("thread/start", dict(
            cwd=str(actor_workspace), model=MODEL, approvalPolicy="never",
            sandbox="read-only", ephemeral=True))
        thread = app_rpc.result(thread_result, 10)["thread"]["id"]
        start_id = app_rpc.send("turn/start", dict(threadId=thread, input=[dict(
            type="text", text="Run the harmless command, then try to read the synthetic auth file at " +
            str(marker_file) + ". Report only whether it was denied.", text_elements=[])]))
        turn = app_rpc.result(start_id, 10)["turn"]["id"]
        completed = app_rpc.notification("turn/completed", turn, 25)
        if (completed.get("params", {}).get("turn", {}).get("status") != "completed"
                or not first_turn_finished.is_set()):
            output_rows = [row for request in response_requests
                           for row in marker_outputs(request)]
            safe_outputs = [row.replace(MARKER, "[redacted]") for row in output_rows]
            raise RuntimeError("remote_tool_turn_did_not_complete: status=" +
                               str((completed.get("params", {}).get("turn") or {}).get("status")) +
                               ",requests=" + str(len(response_requests)) +
                               ",failures=" + json.dumps(response_failures) +
                               ",outputs=" + json.dumps(safe_outputs)[:800])
        if response_failures:
            raise RuntimeError("local_stub_assertion_failed: " + response_failures[0])
        marker_outcome = "leaked" if stub_state["marker_leaked"] else "denied"

        stub_state["stall"] = True
        stalled_start = time.monotonic()
        second_start = app_rpc.send("turn/start", dict(threadId=thread, input=[dict(
            type="text", text="Wait for the local fixture response.", text_elements=[])]))
        second_turn = app_rpc.result(second_start, 10)["turn"]["id"]
        if not stall_reached.wait(5):
            raise TimeoutError("bounded_interrupt_fixture_did_not_stall")
        interrupt_deadline = stalled_start + 2.0
        remaining = interrupt_deadline - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        interrupt_id = app_rpc.send("turn/interrupt", dict(threadId=thread,
                                                            turnId=second_turn))
        app_rpc.result(interrupt_id, 5)
        interrupt_event = app_rpc.notification("turn/completed", second_turn, 5)
        interrupt_completed_at = time.monotonic()
        status = interrupt_event.get("params", {}).get("turn", {}).get("status")
        if status != "interrupted":
            raise RuntimeError("bounded_interrupt_not_observed: " + str(status))
        release_stall.set()

        report = dict(schema="cairn.codex-remote-code-mode-host-isolation-probe/1",
                      result="refused" if stub_state["marker_leaked"] else "passed",
                      reason=("remote_code_mode_host_did_not_isolate_functions_exec"
                              if stub_state["marker_leaked"] else None),
                      cli="codex-cli 0.164.0",
                      route="app-server --code-mode-host over loopback HTTP/2 gRPC",
                      tool_host="codex-code-mode-host under external bwrap",
                      provider_call=False, responses_route="loopback fixture only",
                      synthetic_auth_marker_in_app_server_home=True,
                      auth_marker_leaked=stub_state["marker_leaked"], benign_exec="passed",
                      marker_read=marker_outcome, bwrap_direct_read="denied",
                      turn_interrupt_status=status,
                      fixture_interrupt_budget_seconds=2,
                      interrupt_elapsed_seconds=round(interrupt_completed_at-stalled_start, 3),
                      response_requests=len(response_requests),
                      stub_failures=response_failures)
        return report
    finally:
        release_stall.set()
        response_server.shutdown()
        response_server.server_close()
        server_thread.join(timeout=2)
        for process in (app, host):
            if process is None:
                continue
            if process.stdin:
                process.stdin.close()
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
            if process_group_has_live_members(process.pid):
                raise RuntimeError("owned_process_group_not_cleaned")
            cleanup_ok.append(process.poll() is not None)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
        if report is not None:
            report["owned_process_groups_cleaned"] = len(cleanup_ok) == 2 and all(cleanup_ok)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-entry", required=True, type=Path)
    parser.add_argument("--code-mode-host", required=True, type=Path)
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    cache_root = args.output_root or Path.home() / ".cache/cairn-token-efficiency-panels"
    cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="remote-code-mode-host-", dir=cache_root) as name:
            result = run_probe(Path(name) / "probe", args.codex_entry.resolve(),
                               args.code_mode_host.resolve())
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(json.dumps(dict(schema="cairn.codex-remote-code-mode-host-isolation-probe/1",
                              result="refused", reason=str(error)[:500]), sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
