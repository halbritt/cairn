#!/usr/bin/env python3
"""Exercise the packaged command locally without an authenticated request or model call."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


REQUEST = {"schema": "cairn.codex-advice-request/1",
           "request_id": "431bda54-eb71-41ac-8412-2c47617cc851", "prompt": "fixture input"}


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def decoded(output):
    value = json.loads(output)
    assert isinstance(value, dict)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    binary = Path(manifest["binary_path"]).resolve(strict=True)
    assert manifest["schema"] == "cairn.codex-advice/1"
    assert digest(binary) == manifest["binary_sha256"]
    with tempfile.TemporaryDirectory(prefix="cairn-advice-command-") as temporary:
        root = Path(temporary)
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(root), "CODEX_HOME": str(root / "profile"), "LANG": "C.UTF-8"}
        profile = root / "profile"
        fallback = root / ".codex"
        profile.mkdir()
        fallback.mkdir()
        for directory in (profile, fallback):
            (directory / ".env").write_text("OPENAI_BASE_URL=https://dotenv-canary.invalid\nCAIRN_ADVICE_CANARY=unread\n")
        initial_files = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
        # Observe actual file opens/accesses, not just unchanged canary contents.
        libc = ctypes.CDLL(None, use_errno=True)
        libc.inotify_init1.argtypes = [ctypes.c_int]
        libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
        watch = libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        if watch < 0:
            raise OSError(ctypes.get_errno(), "inotify unavailable; capability privacy check unrun")
        for directory in (profile, fallback):
            if libc.inotify_add_watch(watch, os.fsencode(directory / ".env"), 0x00000001 | 0x00000020) < 0:
                os.close(watch)
                raise OSError(ctypes.get_errno(), "cannot watch capability canary")
        try:
            probe = subprocess.run([str(binary), "cairn-advice", "--capabilities"], cwd=root,
                                   env=environment, capture_output=True, timeout=15, check=True)
            try:
                events = os.read(watch, 65536)
            except BlockingIOError:
                events = b""
            assert not events, "capability probe opened or read a selected/HOME .env file"
            for directory in (profile, fallback):
                with (directory / ".env").open("rb") as canary:
                    canary.read(1)
                assert os.read(watch, 65536), "canary access observation was not active"
        finally:
            os.close(watch)
        assert not probe.stderr
        expected = dict(schema="cairn.codex-advice-capabilities/1", request_schema=REQUEST["schema"],
                        result_schema="cairn.codex-advice-result/1", provider="openai",
                        auth_method="chatgpt_subscription", one_request=True, tools=False,
                        retries=False, upstream_commit=manifest["upstream_commit"],
                        patch_sha256=manifest["patch_sha256"])
        assert json.dumps(decoded(probe.stdout), sort_keys=True) == json.dumps(expected, sort_keys=True)
        assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == initial_files
        command = [str(binary), "cairn-advice", "--model", "gpt-fixture", "--timeout-ms", "10000", "--max-output-bytes", "1024"]

        def refusal(body, code, env=None):
            result = subprocess.run(command, input=body, cwd=root, env=env or environment,
                                    capture_output=True, timeout=15)
            assert result.returncode != 0
            assert not result.stderr
            assert len(result.stdout) <= 1024
            value = decoded(result.stdout)
            assert set(value) == {"schema", "request_id", "ok", "error"}
            assert value["schema"] == expected["result_schema"] and value["ok"] is False
            assert value["error"]["code"] == code
            return value

        refusal(b"{}", "INVALID_REQUEST")
        refusal(json.dumps(REQUEST).encode() * 2, "INVALID_REQUEST")
        refusal(json.dumps(dict(REQUEST, provider="other")).encode(), "INVALID_REQUEST")
        refusal(b" " * 262145, "INVALID_REQUEST")
        # This guard runs before profile/auth loading. Never ask an actual provider.
        value = refusal(json.dumps(REQUEST).encode(), "POLICY_REFUSED",
                        dict(environment, OPENAI_API_KEY="forbidden-fixture-value"))
        assert value["request_id"] == REQUEST["request_id"]
        assert "forbidden-fixture-value" not in json.dumps(value)
        assert sorted(str(path.relative_to(root)) for path in root.rglob("*")) == initial_files

        process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            # Keep stdin open, so no request can reach authentication or transport.
            ready_by = time.monotonic() + 5
            while True:
                assert process.poll() is None, "command exited before cancellation readiness"
                status = Path(f"/proc/{process.pid}/status").read_text()
                mask = next(line.split()[1] for line in status.splitlines() if line.startswith("SigCgt:"))
                if int(mask, 16) & (1 << (signal.SIGTERM - 1)):
                    break
                assert time.monotonic() < ready_by, "SIGTERM handler was not installed"
                time.sleep(0.01)
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)  # Keep stdin open until cancellation wins.
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode != 0 and not stderr
            assert decoded(stdout)["error"]["code"] == "CANCELLED"
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate(timeout=5)
        expired = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        try:
            expired.wait(timeout=15)  # EOF is never sent; the native deadline must win.
            stdout, stderr = expired.communicate(timeout=5)
            assert expired.returncode != 0 and not stderr
            assert decoded(stdout)["error"]["code"] == "TIMEOUT"
        finally:
            if expired.poll() is None:
                os.killpg(expired.pid, signal.SIGKILL)
                expired.communicate(timeout=5)
        try:
            os.killpg(expired.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("expired command group still exists")
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            pass
        else:
            raise AssertionError("owned command group still exists")
        alternate = subprocess.run([str(binary), "-c", "model=\"gpt-fixture\"", "cairn-advice", "--capabilities"],
                                   cwd=root, env=environment, capture_output=True, timeout=15)
        assert alternate.returncode != 0 and not alternate.stdout
        assert b"cairn-advice must be the first argument" in alternate.stderr
        assert b"dotenv-canary" not in alternate.stderr
    assert digest(binary) == manifest["binary_sha256"]
    print(json.dumps({"schema": "cairn.codex-advice-command-check/1", "binary_sha256": manifest["binary_sha256"],
                      "capability": True, "dotenv_unread": True, "alternate_entry_refused": True, "invalid_input_cases": 4, "forbidden_route": True,
                      "signal_cancellation": True, "stdin_deadline": True, "owned_group_gone": True, "model_calls": 0}))


if __name__ == "__main__":
    main()
