"""Read a saved task through observed body/index runs without shell conversion."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid


def check(binary, root, environment):
    env = dict(environment, CAIRN_DATABASE_URL='host=/absent-file-run-client dbname=denied')
    observer = [binary, 'agent', '--socket', str(root / 'api.sock'),
                '--token-file', str(root / 'hosted.token')]
    task = 'Saved task $(literal), `literal`, café and "quotes".\r\n' + 'Retain these task details.\n' * 200 + 'Ending.\r\n\n\n'
    path = root / 'saved task.md'
    path.write_bytes(task.encode())
    linked = root / 'linked task.md'
    linked.symlink_to(path.name)
    child_dir = root / 'task-file-child'
    child_dir.mkdir()
    scope = dict(repo='fixture:socket', task_id='task-file-run', run_id=str(uuid.uuid4()))
    pins = dict(task_class='review', binding_id='task-file-fixture', capability_id='shell')
    inspect = "import json,sys; print(json.dumps((sys.stdin.buffer.read().decode('utf-8') if sys.argv[1]=='stdin' else sys.argv[-1])))"

    def call(operation, payload):
        response = subprocess.run([*observer, operation], input=json.dumps(payload), env=env,
                                  capture_output=True, text=True, timeout=15, check=True)
        return json.loads(response.stdout)['data']

    for index in (False, True):
        for carrier in ('stdin', 'argv'):
            retained = carrier == 'argv'
            query = 'compiler' if index else ''
            req = dict(request_id=str(uuid.uuid4()), scope=scope, context=pins, purpose='context',
                       query=query, available_tokens=32000)
            args = [*observer, 'run', '--destination', 'hosted', '--repo', scope['repo'],
                    '--task', scope['task_id'], '--run', scope['run_id'], '--task-class', 'review',
                    '--binding', pins['binding_id'], '--capability', 'shell', '--dir', str(child_dir),
                    '--prompt-file', path.name if carrier == 'stdin' else linked.name, '--carrier', carrier]
            if index:
                req['expansion_reader'] = 'agent:hosted-capture'
                args += ['--index', '--expansion-reader', req['expansion_reader'], '--query', query,
                         '--pull-tool', 'cairn_pull', '--search-tool', 'cairn_search']
            original = None
            if retained:
                prepared = call('index' if index else 'compile', req)
                original = prepared['package'] if index else prepared
                args += ['--receipt-id', original['receipt_id'], '--seal', original['seal']]
            actual = subprocess.run([*args, '--', sys.executable, '-c', inspect, carrier], cwd=root,
                                    env=env, capture_output=True, text=True, timeout=20)
            assert actual.returncode == 0, (actual.stdout, actual.stderr)
            result = json.loads(actual.stderr)['data']
            delivered = json.loads(actual.stdout)
            context_text, actual_task = delivered.split('\nTASK\n', 1)
            assert actual_task == task and path.read_bytes() == task.encode()
            memory = json.loads(context_text.split('\n', 2)[2])
            assert memory['query'] == 'sha256:' + hashlib.sha256(query.encode()).hexdigest()
            assert result['process_state'] == 'exited' and result['exit_code'] == 0
            if retained:
                assert result['receipt_id'] == original['receipt_id'] and result['seal'] == original['seal']
            stored = Path(result['artifacts'], 'context.txt').read_bytes()
            assert task.encode() not in stored and str(path).encode() not in stored
    maximum = root / 'maximum task.md'
    maximum.write_bytes(b'x' * 131072)
    package = call('compile', dict(request_id=str(uuid.uuid4()), scope=scope, context=pins,
                                  purpose='context', query='', available_tokens=32000))
    args = [*observer, 'run', '--destination', 'hosted', '--repo', scope['repo'],
            '--task', scope['task_id'], '--run', scope['run_id'], '--task-class', 'review',
            '--binding', pins['binding_id'], '--capability', 'shell', '--prompt-file', str(maximum),
            '--receipt-id', package['receipt_id'], '--seal', package['seal']]
    refused = subprocess.run([*args, '--carrier', 'argv', '--', '/bin/echo', 'UNEXPECTED-LAUNCH'],
                             env=env, capture_output=True, text=True, timeout=20)
    assert refused.returncode != 0 and 'BUDGET_REFUSED' in refused.stdout + refused.stderr, (refused.stdout, refused.stderr)
    status = call('run-status', dict(receipt_id=package['receipt_id']))
    assert not status['binding_observed'] and not status['launch_claimed']
    piped = subprocess.run([*args, '--carrier', 'stdin', '--', sys.executable, '-c', inspect, 'stdin'],
                           env=env, capture_output=True, text=True, timeout=20)
    assert piped.returncode == 0, (piped.stdout, piped.stderr)
    assert json.loads(piped.stdout).split('\nTASK\n', 1)[1].encode() == maximum.read_bytes()
    direct = subprocess.run([binary, 'run', '--repo', scope['repo'], '--task', scope['task_id'],
                             '--run', str(uuid.uuid4()), '--prompt-file', str(path), '--carrier', 'stdin',
                             '--', sys.executable, '-c', inspect, 'stdin'], env=environment,
                            capture_output=True, text=True, timeout=20)
    assert direct.returncode == 0, (direct.stdout, direct.stderr)
    assert json.loads(direct.stdout).split('\nTASK\n', 1)[1] == task
    print('Observed fresh/retained body/index task files preserve CRLF, quotes and trailing newlines through both carriers; file contents do not become retrieval queries')
