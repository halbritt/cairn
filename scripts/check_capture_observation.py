"""Real capture hook -> CLI -> API -> disposable PostgreSQL observation probe.

The selector is a local stand-in. It never calls a provider or retains its
input. The CLI shim forwards ordinary calls and injects only named failures.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("capture_observation_engine", ROOT / "integrations/lifecycle/memory.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

SELECTOR = '''#!/usr/bin/env python3
import json, os, sys, time
sys.stdin.read()
mode = os.environ["CAIRN_CAPTURE_PROBE_MODE"]
with open(os.environ["CAIRN_CAPTURE_PROBE_CALLS"], "a") as calls:
    calls.write(mode + "\\n")
if mode == "timeout":
    time.sleep(3)
    sys.exit(0)
if mode == "provider_error":
    print(json.dumps(dict(type="result", is_error=True, fixture_output="RAW_SELECTOR_SENTINEL")))
    sys.exit(0)
selected = dict(memories=[], workstream=None, checkpoint=None)
if mode in ("saved", "saved_report_failure", "write_failure"):
    selected["memories"] = [dict(record_id=None, kind="lesson", title=mode,
                                  body="Safe selected " + mode + " note")]
print(json.dumps(dict(type="result", is_error=False, structured_output=selected,
    fixture_output="RAW_SELECTOR_SENTINEL", duration_ms=321, total_cost_usd=0.003,
    usage=dict(input_tokens=135, output_tokens=20, cache_creation_input_tokens=7,
               cache_read_input_tokens=90), modelUsage={"fixture-sonnet-2026-10": {}})))
'''

CLI_SHIM = '''#!/usr/bin/env python3
import json, os, sys
operation = sys.argv[-1]
blocked = ((operation == "create" and os.environ.get("CAIRN_CAPTURE_PROBE_FAIL_CREATE") == "1")
           or (operation == "capture-observation" and os.environ.get("CAIRN_CAPTURE_PROBE_FAIL_REPORT") == "1"))
if blocked:
    print(json.dumps(dict(schema="cairn.response/1", ok=False, status="AUTHORITY_DENIED",
                          message="fixture refusal")))
    sys.exit(1)
binary = os.environ["CAIRN_CAPTURE_PROBE_REAL_CAIRN"]
os.execv(binary, [binary, *sys.argv[1:]])
'''


def check(binary, directory):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    assert os.environ["CAIRN_DATABASE_URL"] == os.environ["CAIRN_TEST_DATABASE_URL"]
    root = Path(directory)
    root.mkdir(mode=0o700)
    repo = "capture-observation-probe:" + str(uuid.uuid4())
    identities = []
    for name, destination in (("hook", "hosted"), ("report", "local")):
        token = secrets.token_urlsafe(32)
        path = root / (name + ".token")
        path.write_text(token)
        path.chmod(0o600)
        identities.append(dict(principal="capture-observation-probe/" + name, role="agent", repo=repo,
                               destination=destination, token_sha256=hashlib.sha256(token.encode()).hexdigest()))
    (root / "identities.json").write_text(json.dumps(identities))
    (root / "identities.json").chmod(0o600)
    selector = root / "selector"
    selector.write_text(SELECTOR)
    selector.chmod(0o700)
    shim = root / "cli-shim"
    shim.write_text(CLI_SHIM)
    shim.chmod(0o700)
    calls = root / "selector-calls"
    work = root / "work"
    (work / ".git").mkdir(parents=True)
    state_dir = root / "state"
    env = dict(os.environ, CAIRN_HOME=str(root))
    api_log = (root / "api.log").open("w")
    api = subprocess.Popen([binary, "serve"], env=env, stdout=subprocess.DEVNULL, stderr=api_log)
    try:
        for _ in range(100):
            if api.poll() is not None:
                raise AssertionError("capture probe API exited before readiness")
            if (root / "api.sock").exists():
                break
            time.sleep(0.1)
        assert (root / "api.sock").exists(), "capture probe API did not start"

        def agent(name, operation, body, expected="OK"):
            command = [binary, "agent", "--socket", str(root / "api.sock"),
                       "--token-file", str(root / (name + ".token")), operation]
            result = subprocess.run(command, input=json.dumps(body), text=True,
                                    capture_output=True, timeout=15, env=env)
            response = json.loads(result.stdout)
            assert response["status"] == expected, (operation, response, result.stderr)
            assert (result.returncode == 0) == (expected == "OK"), response
            return response.get("data")

        config = dict(cairn=str(shim), claude=str(selector), socket=str(root / "api.sock"),
                      token_file=str(root / "hook.token"), repo=repo, state_dir=str(state_dir),
                      harness="claude", model="requested-stub-model", capture_observations=True)
        sessions = {name: str(uuid.uuid4()) for name in (
            "saved", "courtesy", "nothing", "provider_error", "timeout", "write_failure", "report_failure")}
        dialogue = "RAW_DIALOGUE_SENTINEL: correct the unfinished task"

        def event(name, messages=None):
            return dict(hook_event_name="PreCompact", cwd=str(work), session_id=sessions[name],
                        messages=messages or [dict(role="user", text=dialogue),
                                              dict(role="assistant", text="Acknowledged")])

        def state(name):
            path = state_dir / (sessions[name] + ".json")
            return json.loads(path.read_text()) if path.exists() else {}

        def rows(name=None):
            body = dict(repo=repo, limit=100, offset=0)
            if name:
                body["session_sha256"] = hashlib.sha256(sessions[name].encode()).hexdigest()
            result = agent("report", "capture-observations", body)
            assert result["more"] is False
            return result["rows"]

        def invoke(name, mode, messages=None, *, fail_create=False, fail_report=False, short_timeout=False):
            settings = dict(CAIRN_HOME=str(root), CAIRN_CAPTURE_PROBE_MODE=mode,
                            CAIRN_CAPTURE_PROBE_CALLS=str(calls), CAIRN_CAPTURE_PROBE_REAL_CAIRN=binary,
                            CAIRN_CAPTURE_PROBE_FAIL_CREATE="1" if fail_create else "0",
                            CAIRN_CAPTURE_PROBE_FAIL_REPORT="1" if fail_report else "0")
            with patch.dict(os.environ, settings):
                if short_timeout:
                    original = engine.select_json
                    with patch.object(engine, "select_json", side_effect=lambda *args, **kwargs:
                                      original(*args, timeout=0.2, **kwargs)):
                        return engine.handle(config, event(name, messages))
                return engine.handle(config, event(name, messages))

        saved = invoke("saved", "saved")
        saved_state = state("saved")
        assert saved_state["last_capture"]["outcome"] == "saved" and len(saved_state["last_capture"]["records"]) == 1
        selected_id = saved_state["last_capture"]["records"][0]
        assert selected_id in saved["systemMessage"]
        history = agent("hook", "history", dict(record_id=selected_id, version=1))
        assert history["versions"][0]["body"].endswith("Safe selected saved note"), history
        first, = rows("saved")
        assert first["outcome"] == "saved" and first["selector_outcome"] == "completed", first
        assert first["snapshot_sha256"] == saved_state["captured_digest"], first
        assert first["session_sha256"] == hashlib.sha256(sessions["saved"].encode()).hexdigest(), first
        for field, expected in (("requested_model", "requested-stub-model"),
                                ("reported_model", "fixture-sonnet-2026-10"),
                                ("input_tokens", 135), ("output_tokens", 20),
                                ("cache_creation_input_tokens", 7), ("cache_read_input_tokens", 90),
                                ("cost_usd", 0.003)):
            assert first[field] == expected, (field, first)
        assert first["duration_ms"] is not None and first["selector_duration_ms"] is not None

        invoke("saved", "saved")
        unchanged = rows("saved")[-1]
        assert unchanged["outcome"] == "unchanged" and unchanged["selector_outcome"] is None, unchanged
        assert unchanged["input_tokens"] is None and unchanged["cost_usd"] is None
        assert state("saved")["captured_digest"] == saved_state["captured_digest"]

        courtesy = [dict(role="user", text="Thanks"), dict(role="assistant", text="You're welcome")]
        invoke("courtesy", "saved", courtesy)
        courtesy_row, = rows("courtesy")
        assert courtesy_row["outcome"] == "courtesy" and courtesy_row["selector_outcome"] is None
        assert courtesy_row["input_tokens"] is None and courtesy_row["snapshot_sha256"] is not None

        invoke("nothing", "nothing_selected")
        nothing, = rows("nothing")
        assert nothing["outcome"] == "nothing_selected" and nothing["selector_outcome"] == "completed"
        assert nothing["input_tokens"] == 135 and state("nothing")["last_capture"]["records"] == []

        for name, mode, options in (("provider_error", "provider_error", {}),
                                    ("timeout", "timeout", dict(short_timeout=True)),
                                    ("write_failure", "write_failure", dict(fail_create=True))):
            try:
                invoke(name, mode, **options)
                raise AssertionError(name + " should fail capture")
            except engine.HookError:
                pass
            row, = rows(name)
            expected = "write_error" if name == "write_failure" else "error" if name == "provider_error" else "timeout"
            assert row["outcome"] == expected, (name, row)
            assert row["selector_outcome"] == ("completed" if name == "write_failure" else expected), row
            assert "captured_digest" not in state(name), (name, state(name))
            if name != "write_failure":
                assert row["input_tokens"] is None and row["cost_usd"] is None, row

        invoke("report_failure", "saved_report_failure", fail_report=True)
        confirmed = state("report_failure")
        assert confirmed["last_capture"]["outcome"] == "saved" and confirmed["captured_digest"]
        assert rows("report_failure") == [], "a refused observation must remain unknown"
        note_id = confirmed["last_capture"]["records"][0]
        assert agent("hook", "history", dict(record_id=note_id, version=1))["versions"][0]["body"].endswith(
            "Safe selected saved_report_failure note")
        invoke("report_failure", "saved_report_failure")
        retry, = rows("report_failure")
        assert retry["outcome"] == "unchanged" and retry["input_tokens"] is None
        assert state("report_failure")["captured_digest"] == confirmed["captured_digest"]
        assert state("report_failure")["last_capture"]["records"] == [], "unchanged retry rewrote the selected note"

        agent("hook", "capture-observations", dict(repo=repo, limit=1, offset=0), expected="AUTHORITY_DENIED")
        all_rows = rows()
        assert len(all_rows) == 8, [(r["outcome"], r["session_sha256"]) for r in all_rows]
        assert len(calls.read_text().splitlines()) == 6, calls.read_text()
        assert "RAW_DIALOGUE_SENTINEL" not in json.dumps(all_rows)
        assert "RAW_SELECTOR_SENTINEL" not in json.dumps(all_rows)
        for sql in ("SELECT row_to_json(c)::text FROM cairn.capture_observation c WHERE repo='" + repo + "'",
                    "SELECT body FROM cairn.record_version WHERE repo='" + repo + "'"):
            stored = subprocess.run(["psql", os.environ["CAIRN_TEST_DATABASE_URL"], "-X", "-A", "-t", "-c", sql],
                                    text=True, capture_output=True, check=True, timeout=15).stdout
            assert "RAW_DIALOGUE_SENTINEL" not in stored and "RAW_SELECTOR_SENTINEL" not in stored
        for path in state_dir.glob("*.json"):
            assert "RAW_DIALOGUE_SENTINEL" not in path.read_text()
            assert "RAW_SELECTOR_SENTINEL" not in path.read_text()
        print("Capture hook/CLI/API metering: saved, no selection, skips, timeout, provider and write errors, unknown report; source/output excluded")
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait(timeout=10)
        api_log.close()


if __name__ == "__main__":
    check(*sys.argv[1:])
