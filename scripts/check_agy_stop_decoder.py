"""Verify the native Agy CLI Stop protojson decoder accepts Cairn Stop replies (zero quota)."""
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading

MODEL = "Gemini 3.8 Flash (Low)"


def _start_local_gemini():
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            size = int(self.headers.get("Content-Length", "0"))
            calls.append(json.loads(self.rfile.read(size)))
            resp = {
                "candidates": [{"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP", "index": 0}],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 1, "totalTokenCount": 6},
                "modelVersion": "gemini-3.8-flash",
            }
            body = ("data: " + json.dumps(resp) + "\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, calls


def _stop_errors(log_path: Path) -> list[str]:
    return [
        line for line in log_path.read_text(errors="replace").splitlines()
        if "stop hook" in line.lower() and ("fail" in line.lower() or "error" in line.lower() or "unknown field" in line.lower())
    ]


def main() -> int:
    agy_bin = shutil.which("agy")
    if not agy_bin:
        sys.stderr.write("agy binary not found on PATH\n")
        return 2
    repo_root = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="cairn-agy-stop-") as tmp:
        root = Path(tmp)
        lifecycle_bin = root / "cairn-lifecycle"
        subprocess.run(["go", "build", "-o", str(lifecycle_bin), "./cmd/cairn-lifecycle"], cwd=repo_root, check=True)
        server, calls = _start_local_gemini()
        try:
            home, acct, work = root / "home", root / "acct", root / "work"
            for d in (home / ".gemini" / "config", acct, work / ".agents"):
                d.mkdir(parents=True, mode=0o700)
            subprocess.run(["git", "init", "-q", str(work)], check=True)
            settings = acct / "settings.json"
            settings.write_text(json.dumps({"modelProvider": "gemini"}))
            env = {
                "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb", "HOME": str(home),
                "ANTIGRAVITY_CLI_CONFIG_DIR": str(acct), "GEMINI_API_KEY": "r04-synthetic-unused",
                "GOOGLE_GEMINI_BASE_URL": f"http://127.0.0.1:{server.server_port}", "NO_PROXY": "127.0.0.1,localhost",
                # The check drives agy --print; since CAIRN-818 the hook skips
                # enrollment for print sessions unless this opt-in is set.
                "CAIRN_ALLOW_PRINT_ENROLLMENT": "1",
            }
            hooks_path = work / ".agents" / "hooks.json"

            # 1. Negative control: PreInvocation injectSteps emitted on Stop is rejected by Agy's Stop decoder.
            hooks_path.write_text(json.dumps({"cairn-coordination": {"Stop": [{
                "type": "command", "command": "printf '{\"injectSteps\":[{\"ephemeralMessage\":\"bad\"}]}\\n'", "timeout": 10,
            }]}}))
            bad_log = root / "bad.log"
            subprocess.run([agy_bin, "--new-project", "--settings", str(settings), "--model", MODEL,
                            "--log-file", str(bad_log), "--print-timeout", "20s", "--print", "hi"],
                           cwd=work, env=env, check=True, capture_output=True, timeout=30)
            if not any("injectSteps" in line for line in _stop_errors(bad_log)):
                raise AssertionError("expected native Agy Stop decoder to reject injectSteps")
            return _verify_go_handler(agy_bin, lifecycle_bin, root, work, settings, hooks_path, env, calls)
        finally:
            server.shutdown()
            server.server_close()


def _verify_go_handler(agy_bin, lifecycle_bin, root, work, settings, hooks_path, env, provider_calls) -> int:
    api_calls = root / "calls.jsonl"
    pending_flag = root / "pending.flag"
    api_mock = root / "fake-cairn"
    api_mock.write_text(f"""#!/usr/bin/env python3
import json, pathlib, sys
op = sys.argv[-1]
req = json.loads(sys.stdin.read() or "{{}}")
with open({str(api_calls)!r}, "a") as f:
    f.write(json.dumps({{"op": op, "req": req}}) + "\\n")
agent = {{
    "agent_id": "767f45f2-9a3e-4171-833d-34c44100d60b",
    "execution_id": "9d124ee1-bde5-418b-a05e-6fe03f25c772",
    "display_name": "agent-test",
    "inbox": "agent/767f45f2-9a3e-4171-833d-34c44100d60b",
    "native_session_id": req.get("native_session_id", "c1"),
    "context_revision": 1,
    "metadata": req.get("metadata") or {{"harness": "agy", "project": "work", "workspace": {str(work)!r}, "state": "busy"}},
}}
pf = pathlib.Path({str(pending_flag)!r})
if op == "session-inbox-claim":
    if pf.exists():
        pf.unlink()
        data = {{"attempt": {{"attempt_id": req["request_id"],
            "session": {{"agent_id": agent["agent_id"], "execution_id": agent["execution_id"]}},
            "delivery": {{"delivery_id": "55555555-5555-4555-8555-555555555555",
                "lease_id": "66666666-6666-4666-8666-666666666666", "state": "leased",
                "event": {{"event_id": "55555555-5555-4555-8555-555555555555", "kind": "request",
                    "from": "agent/sender", "ref": {{"record_id": "77777777-7777-4777-8777-777777777777", "version": 1}}}}}}}}}}
    else:
        data = {{"attempt": None}}
elif op == "event-renew":
    data = {{"delivery_id": "55555555-5555-4555-8555-555555555555",
            "lease_id": "66666666-6666-4666-8666-666666666666", "state": "leased",
            "event": {{"event_id": "55555555-5555-4555-8555-555555555555", "kind": "request",
                "from": "agent/sender", "ref": {{"record_id": "77777777-7777-4777-8777-777777777777", "version": 1}}}}}}
elif op == "session-inbox-reconcile":
    if req.get("reason") == "delivery_completed":
        print(json.dumps({{"ok": False, "status": "DELIVERY_ACTIVE"}}))
        sys.exit(1)
    data = {{"released": True}}
else:
    data = agent
print(json.dumps({{"ok": True, "data": data}}))
""")
    api_mock.chmod(0o700)
    coord_cfg = root / "coord.json"
    coord_cfg.write_text(json.dumps({
        "binding": "agy-test", "harness": "agy", "repo": str(work), "state_dir": str(root / "state"),
        "cairn": str(api_mock), "socket": str(root / "s.sock"), "token_file": str(root / "t.tok"),
        "native_delivery": True, "process_names": ["agy"],
    }))
    coord_cfg.chmod(0o600)

    for mode_name, py_mut, seed_pending, want_state, want_min_turns in (
        ("false", "d['fullyIdle'] = False", False, "busy", 1),
        ("absent", "d.pop('fullyIdle', None)", False, "busy", 1),
        ("true_idle", "d['fullyIdle'] = True", False, "idle", 1),
        ("true_continue", "d['fullyIdle'] = True", True, "idle", 2),
    ):
        shim = root / f"stop-{mode_name}.py"
        shim.write_text(
            "#!/usr/bin/env python3\nimport json, subprocess, sys\nd = json.loads(sys.stdin.read())\n"
            f"{py_mut}\n"
            f"r = subprocess.run([{str(lifecycle_bin)!r}, 'coordination', 'hook', '--config', {str(coord_cfg)!r}, '--event', 'Stop'], "
            "input=json.dumps(d).encode(), stdout=subprocess.PIPE, stderr=sys.stderr, check=True)\nsys.stdout.buffer.write(r.stdout)\n"
        )
        shim.chmod(0o700)
        hooks_path.write_text(json.dumps({"cairn-coordination": {
            "PreInvocation": [{"type": "command", "command": f"{lifecycle_bin} coordination hook --config {coord_cfg} --event PreInvocation", "timeout": 10}],
            "Stop": [{"type": "command", "command": str(shim), "timeout": 10}],
        }}))
        api_calls.unlink(missing_ok=True)
        shutil.rmtree(root / "state", ignore_errors=True)
        before_turns = len(provider_calls)
        if seed_pending:
            # Seed pending delivery ONLY after PreInvocation so Stop claims and continues.
            pre_shim = root / f"pre-{mode_name}.py"
            pre_shim.write_text(
                "#!/usr/bin/env python3\nimport pathlib, subprocess, sys\n"
                f"r = subprocess.run([{str(lifecycle_bin)!r}, 'coordination', 'hook', '--config', {str(coord_cfg)!r}, '--event', 'PreInvocation'], "
                "input=sys.stdin.buffer.read(), stdout=subprocess.PIPE, stderr=sys.stderr, check=True)\n"
                f"if not pathlib.Path({str(root / 'seeded')!r}).exists():\n"
                f"    pathlib.Path({str(root / 'seeded')!r}).touch(); pathlib.Path({str(pending_flag)!r}).touch()\n"
                "sys.stdout.buffer.write(r.stdout)\n"
            )
            pre_shim.chmod(0o700)
            hooks_path.write_text(json.dumps({"cairn-coordination": {
                "PreInvocation": [{"type": "command", "command": str(pre_shim), "timeout": 10}],
                "Stop": [{"type": "command", "command": str(shim), "timeout": 10}],
            }}))
        log_path = root / f"good-{mode_name}.log"
        subprocess.run([agy_bin, "--new-project", "--settings", str(settings), "--model", MODEL,
                        "--log-file", str(log_path), "--print-timeout", "20s", "--print", "hi"],
                       cwd=work, env=env, check=True, capture_output=True, timeout=30)
        if errs := _stop_errors(log_path):
            raise AssertionError(f"Agy Stop decoder errors in {mode_name}: {errs}")
        calls = [json.loads(line) for line in api_calls.read_text().splitlines()]
        states = [c["req"]["metadata"]["state"] for c in calls if c["op"] in ("agent-register", "agent-context")]
        if states[-1] != want_state or len(provider_calls) - before_turns < want_min_turns:
            raise AssertionError(f"{mode_name}: states={states}, turns={len(provider_calls) - before_turns}")
    print("Native Agy Stop decoder verified for false, absent, idle, and continue replies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
