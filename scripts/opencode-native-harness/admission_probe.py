#!/usr/bin/env python3
"""Explicit opt-in native probe. Isolation checks run in this SAME namespace first.

This script is not a claim of atomic admission: it records a bounded busy-turn
observation and fails unless the observed provider and response are the mock.
"""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

from isolation_checks import checks
from verify import verify_current

HERE = Path(__file__).resolve().parent
BASE = 'http://127.0.0.1:18200'
MODEL = dict(providerID='mock', modelID='mock-model')


def http(method, path, body=None, timeout=15):
    request = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]


def main(binary, iterations=1):
    verify_current()
    if not Path(binary).is_absolute() or not os.access(binary, os.X_OK) or not 1 <= iterations <= 5:
        raise RuntimeError('executable absolute native binary and 1..5 iterations required')
    checks()  # Fresh tests and mock canary in precisely this namespace.
    root = Path(os.environ['HARNESS_FIXTURE_ROOT'])
    config = dict(model='mock/mock-model', small_model='mock/mock-model', enabled_providers=['mock'],
        plugin=[], permission={'*': 'deny'}, autoupdate=False, share='disabled', provider=dict(mock=dict(
        npm='@ai-sdk/openai-compatible', name='Mock',
        options=dict(baseURL='http://127.0.0.1:18131/v1', apiKey='unused-loopback-only'),
        models={'mock-model': dict(name='Mock Model', limit=dict(context=8192, output=1024))})))
    config_dir = root / 'config/opencode'
    config_dir.mkdir()
    (config_dir / 'opencode.json').write_text(json.dumps(config, indent=2))
    processes = []
    logs = []
    try:
        log = (root / 'mock-probe.stderr').open('w'); logs.append(log)
        processes.append(subprocess.Popen(['/usr/bin/python3', str(HERE/'mock_provider.py'), '18131', '2'],
                                          stdout=subprocess.DEVNULL, stderr=log))
        log = (root / 'serve.log').open('w'); logs.append(log)
        # Copy ONLY the already verified minimal environment; never inherit host profiles.
        env = dict(os.environ, OPENCODE_DISABLE_AUTOUPDATE='1', OPENCODE_DISABLE_SHARE='1')
        processes.append(subprocess.Popen([binary, 'serve', '--port', '18200', '--hostname', '127.0.0.1',
                                           '--log-level', 'WARN', '--pure'], cwd=root/'workspace',
                                          env=env, stdout=log, stderr=subprocess.STDOUT))
        for _ in range(100):
            if any(proc.poll() is not None for proc in processes):
                raise RuntimeError('native or mock fixture exited before readiness')
            try:
                if http('GET', '/session', timeout=1)[0] == 200:
                    break
            except OSError:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError('native fixture never became ready')
        results = []
        for iteration in range(iterations):
            code, session = http('POST', '/session', dict(title=f'admission-{iteration}'))
            if code != 200 or not isinstance(session, dict) or not session.get('id'):
                raise RuntimeError('native session creation failed')
            sid = session['id']
            prompt = lambda text: dict(model=MODEL, parts=[dict(type='text', text=text)])
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as worker:
                first = worker.submit(http, 'POST', f'/session/{sid}/message', prompt(f'first-{iteration}'), 20)
                busy = False
                for _ in range(100):
                    code, status = http('GET', '/session/status')
                    if code == 200 and status.get(sid, {}).get('type') == 'busy':
                        busy = True
                        break
                    if first.done():
                        break
                    time.sleep(.05)
                if not busy:
                    raise RuntimeError('probe never observed a busy native turn')
                wake_code, _ = http('POST', f'/session/{sid}/prompt_async', prompt(f'wake-{iteration}'))
                first_code, _ = first.result(timeout=22)
            if first_code != 200:
                raise RuntimeError('first native prompt failed')
            time.sleep(2.5)
            code, messages = http('GET', f'/session/{sid}/message')
            if code != 200 or not isinstance(messages, list):
                raise RuntimeError('native messages unavailable')
            checked = 0
            for message in messages:
                info = message.get('info', {})
                if info.get('role') != 'assistant':
                    continue
                if info.get('providerID') != 'mock' or info.get('modelID') != 'mock-model':
                    raise RuntimeError('native assistant provider/model differs from the mock')
                if info.get('error') or any(part.get('type') == 'tool' for part in message.get('parts', [])):
                    raise RuntimeError('native assistant errored or invoked a tool')
                for part in message.get('parts', []):
                    if part.get('type') == 'text' and part.get('text'):
                        if not part['text'].startswith('MOCK-CANARY-REPLY'):
                            raise RuntimeError('native assistant response is not the mock canary')
                        checked += 1
            if checked == 0:
                raise RuntimeError('no completed mock response was observed')
            results.append(dict(iteration=iteration, busy_observed=busy, wake_http=wake_code, mock_replies=checked))
        (root/'admission-results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(dict(root=str(root), observations=results)), flush=True)
    finally:
        for proc in reversed(processes):
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait()
        for log in logs:
            log.close()


if __name__ == '__main__':
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 1)
