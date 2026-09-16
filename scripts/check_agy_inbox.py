"""Opt-in Agy Stop/inbox smoke test using its existing account and real model."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
import uuid


def check(binary, model, root, config, api_call):
    spec = importlib.util.spec_from_file_location('agy_inbox_installer', Path(__file__).with_name('install-agent-coordination.py'))
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    root.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '--quiet', str(root)], check=True, timeout=5)
    config = dict(config, harness='agy', binding='agy-inbox', process_names=['agy'], native_delivery=True)
    settings = root / '.agents/hooks.json'
    installed = installer.install(root / 'engine', settings, config)
    marker = root / 'first-invocation'
    hooks = json.loads(settings.read_text())
    wrapper = root / 'fixture_hook.py'
    wrapper.write_text('import os, pathlib, subprocess, sys\n'
        'result=subprocess.run(sys.argv[2:], input=sys.stdin.buffer.read(), capture_output=True, timeout=15, '
        'env=dict(os.environ, CAIRN_COORDINATION_DISABLED="0"))\n'
        'if result.returncode == 0 and sys.argv[1] == "PreInvocation": pathlib.Path('+repr(str(marker))+').touch()\n'
        'sys.stdout.buffer.write(result.stdout); sys.stderr.buffer.write(result.stderr); sys.exit(result.returncode)\n')
    for event, handlers in hooks['cairn-coordination'].items():
        for handler in handlers:
            handler['command'] = shlex.join([sys.executable, str(wrapper), event, *shlex.split(handler['command'])])
    settings.write_text(json.dumps(hooks))
    source = api_call('alice', 'create', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()), draft=dict(
        kind='note', body='Native inbox verification: compute 17 times 19 and complete this request with the result. '
        'Use only the supplied fixture context commands and token file. Do not inspect or change projects or other memory profiles.',
        sensitivity='shareable', claim_type='self', scope=dict(repo=config['repo'], task_id='*', run_id='*')))))
    env = dict(os.environ)
    # Only the fixture wrapper enables coordination against the disposable API.
    # The authenticated CLI also discovers owner-wide hooks.
    env['CAIRN_COORDINATION_DISABLED'] = '1'
    for key in ('CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD', 'CAIRN_WAKE_CONTEXT'):
        env.pop(key, None)
    process = None
    try:
        with (root / 'stdout.json').open('w') as out, (root / 'stderr.log').open('w') as err:
            process = subprocess.Popen([binary, '--new-project', '--model', model, '--print-timeout', '60s',
                '--output-format', 'json', '--print', 'Reply INITIAL_DONE.'], cwd=root, env=env,
                stdout=out, stderr=err, start_new_session=True)
            deadline = time.monotonic() + 15
            while not marker.exists():
                assert process.poll() is None and time.monotonic() < deadline, 'Agy did not reach native invocation boundary'
                time.sleep(.05)
            target = next(a for a in api_call('bob', 'agents', 'list', '--harness', 'agy')['agents']
                          if a['metadata']['workspace'] == str(root))
            assert target['metadata']['state'] == 'busy'
            message = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', target['inbox'],
                              '--kind', 'request', '--version', '1', source['record_id'])
            process.wait(timeout=70)
        installer.engine.watch_once(installer.engine.load_config(installed))
        status = api_call('alice', 'event-status', message['event_id'])['deliveries'][0]
        assert process.returncode == 0 and status['state'] == 'handled', (process.returncode, status)
        selected = api_call('alice', 'history', raw=True, body=json.dumps(status['result']))
        assert '323' in json.dumps(selected), selected
        assert json.loads((root / 'stdout.json').read_text())['conversation_id'] == target['native_session_id']
        print('Installed Agy Stop continuation handled a busy-session request and recorded the computed result')
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        installer.engine.watch_once(installer.engine.load_config(installed))
