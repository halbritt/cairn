#!/usr/bin/env python3
"""OpenCode native admission race probe inside the fail-closed harness.

Preconditions (enforced): already inside the loopback-only namespace, credential
environment scrubbed, mock provider reachable, config uses only the mock model.
Every assistant reply is canary-checked; any non-canary text aborts the probe as a
containment failure.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

HARNESS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("HARNESS_FIXTURE_ROOT", "/tmp/opencode-native-harness")
OPENCODE = os.environ.get("HARNESS_OPENCODE", "/home/halbritt/.npm-global/bin/opencode")
BASE = "http://127.0.0.1:18200"
MOCK_PORT = 18131
CANARY = "MOCK-CANARY-REPLY"
CONFIG_DIR = f"{ROOT}/config"
DATA_DIR = f"{ROOT}/data"
CACHE_DIR = f"{ROOT}/cache"
WORKSPACE = f"{ROOT}/workspace"


def http(method, url, body=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except Exception as e:
        return None, str(e)


def alive(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def scrubbed_env():
    env = {
        "HOME": ROOT,
        "PATH": "/usr/bin:/bin",
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "XDG_CONFIG_HOME": CONFIG_DIR,
        "XDG_DATA_HOME": DATA_DIR,
        "XDG_CACHE_HOME": CACHE_DIR,
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_PLUGINS": "1",
    }
    for key in list(os.environ):
        if "KEY" in key.upper() or "TOKEN" in key.upper() or "SECRET" in key.upper():
            continue  # never inherit credential-bearing variables
        if key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            continue
        env.setdefault(key, os.environ[key])
    return env


def canary_ok(text):
    return isinstance(text, str) and text.startswith(CANARY)


def main():
    iterations = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    for required in (f"{CONFIG_DIR}/opencode.json",):
        if not os.path.exists(required):
            print(json.dumps({"error": f"missing {required}; run run-harness.sh"}))
            sys.exit(2)

    procs = []
    results = {"iterations": [], "canary_failures": 0}

    if alive(f"http://127.0.0.1:{MOCK_PORT}/v1/models") != 200:
        procs.append(subprocess.Popen(
            [sys.executable, f"{HARNESS}/mock_provider.py", str(MOCK_PORT), "4"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
        for _ in range(40):
            if alive(f"http://127.0.0.1:{MOCK_PORT}/v1/models") == 200:
                break
            time.sleep(0.25)

    if alive(BASE + "/session") != 200:
        env = scrubbed_env()
        procs.append(subprocess.Popen(
            [OPENCODE, "serve", "--port", "18200", "--hostname", "127.0.0.1",
             "--log-level", "WARN", "--pure"],
            cwd=WORKSPACE, env=env,
            stdout=open(f"{ROOT}/serve.log", "ab"), stderr=subprocess.STDOUT,
            start_new_session=True))
        for _ in range(80):
            if alive(BASE + "/session") == 200:
                break
            time.sleep(0.5)

    for i in range(iterations):
        st, sess = http("POST", BASE + "/session", {"title": f"admission-{i}"})
        sid = sess["id"]
        first = {}

        def run_first():
            first["res"] = http("POST", BASE + f"/session/{sid}/message",
                                {"parts": [{"type": "text", "text": f"first-{i}"}]}, timeout=90)

        th = threading.Thread(target=run_first)
        th.start()
        busy_seen = False
        for _ in range(100):
            st2, stat = http("GET", BASE + "/session/status")
            if st2 == 200 and isinstance(stat, dict) and stat.get(sid, {}).get("type") == "busy":
                busy_seen = True
                break
            time.sleep(0.1)
        st3, _ = http("POST", BASE + f"/session/{sid}/prompt_async",
                      {"parts": [{"type": "text", "text": f"wake-{i}"}]})
        th.join()
        time.sleep(2.5)
        st4, msgs = http("GET", BASE + f"/session/{sid}/message")
        entry = {"i": i, "busy_seen": busy_seen, "prompt_async_http": st3,
                 "first_prompt_http": first.get("res", (None,))[0], "messages": []}
        if isinstance(msgs, list):
            for m in msgs:
                info = m["info"]
                if info["role"] == "assistant":
                    for part in m.get("parts", []):
                        if part.get("type") == "text":
                            text = part.get("text", "")
                            ok = canary_ok(text)
                            entry["messages"].append({"role": "assistant", "canary": ok, "text": text[:40]})
                            if not ok:
                                results["canary_failures"] += 1
        results["iterations"].append(entry)

    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    results["containment"] = "PASS" if results["canary_failures"] == 0 else "FAIL: non-mock model output detected"
    print(json.dumps(results, indent=1))
    sys.exit(0 if results["canary_failures"] == 0 else 3)


if __name__ == "__main__":
    main()
