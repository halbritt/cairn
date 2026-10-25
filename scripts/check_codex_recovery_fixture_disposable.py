#!/usr/bin/env python3
"""Run the Codex recovery report against a disposable Store and loopback model stub."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]


def check(candidate_root, codex_binary):
    candidate_root = Path(candidate_root).resolve(strict=True)
    if not (candidate_root / "codex").is_dir():
        raise ValueError("candidate root must contain the staged codex directory")
    binary = candidate_root / "bin/cairn"
    binary.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    subprocess.run(["go", "build", "-o", str(binary), "./cmd/cairn"], cwd=ROOT,
                   check=True, capture_output=True, timeout=180)

    pg_bin = Path(subprocess.run(["pg_config", "--bindir"], check=True,
        capture_output=True, text=True, timeout=5).stdout.strip())
    root = Path(tempfile.mkdtemp(prefix="cairn-codex-recovery-"))
    root.chmod(0o700)
    data, socket = root / "data", root / "socket"
    socket.mkdir(mode=0o700)
    postgres_started = False
    api = None
    try:
        subprocess.run([str(pg_bin / "initdb"), "-D", str(data), "--auth-local=trust",
                        "--auth-host=reject", "--no-locale", "-E", "UTF8"],
                       check=True, capture_output=True, text=True, timeout=30)
        subprocess.run([str(pg_bin / "pg_ctl"), "-D", str(data), "-l", str(root / "postgres.log"),
                        "-o", f"-F -k {socket} -c listen_addresses=''", "-w", "start"],
                       check=True, capture_output=True, text=True, timeout=30)
        postgres_started = True
        subprocess.run([str(pg_bin / "createdb"), "-h", str(socket), "cairn_test"],
                       check=True, capture_output=True, text=True, timeout=10)
        dsn = f"host={socket} dbname=cairn_test sslmode=disable"
        env = dict(os.environ, CAIRN_DATABASE_URL=dsn, CAIRN_TEST_DATABASE_URL=dsn,
                   CAIRN_HOME=str(root))
        subprocess.run([str(binary), "migrate"], env=env, check=True,
                       capture_output=True, text=True, timeout=60)
        repo = "fixture:cairn-226-codex-recovery"
        identities = []
        for name in ("alice", "bob", "carol"):
            token = secrets.token_urlsafe(32)
            path = root / (name + ".token")
            path.write_text(token, encoding="utf-8")
            path.chmod(0o600)
            identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                principal="agent/" + name, repo=repo, role="agent", destination="hosted"))
        identity_path = root / "identities.json"
        identity_path.write_text(json.dumps(identities), encoding="utf-8")
        identity_path.chmod(0o600)
        api = subprocess.Popen([str(binary), "serve"], env=env, stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        while not (root / "api.sock").exists():
            if api.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("disposable Cairn API did not start: " + api.stderr.read())
            time.sleep(0.02)

        def api_call(name, command, *args, body=None, expected="OK", raw=False):
            common = ["--token-file", str(root / (name + ".token"))]
            argv = [str(binary), "agent", *common, command, *args] if raw else [str(binary), command]
            if not raw:
                if command == "agents":
                    argv += [args[0], *common, *args[1:]]
                elif command == "inbox":
                    argv += ["next", *common, *args]
                else:
                    argv += [*common, *args]
            result = subprocess.run(argv, input=body, env=env, text=True,
                                    capture_output=True, timeout=20)
            payload = json.loads(result.stdout)
            if payload.get("status") != expected or (result.returncode == 0) != (expected == "OK"):
                raise AssertionError((command, payload, result.stderr[-500:]))
            return payload.get("data")

        spec = importlib.util.spec_from_file_location(
            "codex_recovery_fixture", ROOT / "scripts/check_codex_compact_native.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        config = dict(cairn=str(binary), socket=str(root / "api.sock"),
                      token_file=str(root / "alice.token"), repo=repo)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            module.check(codex_binary, root / "native-codex-notice", config,
                         api_call, candidate_root, scenario="notice")
        fixture = json.loads(output.getvalue())
        report = fixture.get("recovery_report")
        if (fixture.get("result") != "PASS" or fixture.get("model") != "gpt-6-luna" or
                fixture.get("provider_call") is not False or
                fixture.get("notice_pending_before_and_after_compact") is not True or
                fixture.get("source_read_after_compact") is not True or
                fixture.get("settlement_after_compact") is not True or
                fixture.get("loopback_response_requests") != 5):
            raise AssertionError("Codex fixture did not produce the bounded provider-free compaction receipt")
        hooks = fixture.get("observed_hook_events", [])
        precompact = next((row for row in hooks if row.get("phase") == "exit"
                           and row.get("event") == "PreCompact"), None)
        compact_start = next((row for row in hooks if row.get("phase") == "exit"
                              and row.get("event") == "SessionStart"
                              and row.get("source") == "compact" and row.get("exit_code") == 0), None)
        if (not precompact or precompact.get("exit_code") != 0 or not compact_start or
                fixture.get("native_compact_terminal_notification") != "turn/completed" or
                fixture.get("continuation_after_compact_completed") is not True):
            raise AssertionError("Codex compact receipt omitted terminal hooks or continuation evidence")
        return dict(result="PASS", environment="disposable PostgreSQL and loopback Codex stub",
                    model="gpt-6-luna", provider_call=False,
                    native_compaction={key: fixture.get(key) for key in (
                        "registration_turn_id", "native_manual_compact_turn_id",
                        "native_compact_terminal_notification", "continuation_turn_id",
                        "attempt_native_turn_id",
                        "observed_hook_events", "loopback_response_requests",
                        "notice_event_id", "notice_delivery_id",
                        "attempt_id",
                        "notice_pending_before_and_after_compact",
                        "source_read_after_compact", "settlement_after_compact")},
                    recovery_report=report)
    finally:
        if api is not None:
            api.terminate()
            try:
                api.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                api.kill()
                api.communicate(timeout=5)
        if postgres_started:
            subprocess.run([str(pg_bin / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"],
                           check=True, capture_output=True, text=True, timeout=20)
        shutil.rmtree(root)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, required=True,
                        help="offline Codex candidate from stage_token_efficiency_canary.py")
    parser.add_argument("--codex-binary", default=shutil.which("codex"),
                        help="Codex CLI binary; defaults to PATH")
    args = parser.parse_args()
    if not args.codex_binary:
        parser.error("Codex CLI binary is required")
    print(json.dumps(check(args.candidate_root, str(Path(args.codex_binary).resolve(strict=True))),
                     sort_keys=True))
