#!/usr/bin/env python3
"""Join real bound memory recall to a compact notice in a disposable Store.

All host callbacks and the native owner are synthetic. No model is launched.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import select
import stat
import subprocess
import sys
import time
import uuid


OWNER = r'''
import json, os, subprocess, sys
script, config = sys.argv[1:3]
for line in sys.stdin:
    request = json.loads(line)
    if '_compact' in request:
        env = dict(os.environ, CODEX_THREAD_ID=request['session_id'])
        result = subprocess.run([sys.executable, script, 'inbox', 'compact', *request['_compact']],
            env=env, text=True, capture_output=True, timeout=20)
    else:
        result = subprocess.run([sys.executable, script, 'hook', '--config', config],
            input=line, text=True, capture_output=True, timeout=20)
    print(json.dumps(dict(code=result.returncode, stdout=result.stdout,
                          stderr=result.stderr)), flush=True)
'''

SELECTOR = r'''#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps(dict(type='result', is_error=False,
    structured_output=dict(checkpoint=None, workstream=None, memories=[]))))
'''


def require(value, message):
    if not value:
        raise AssertionError(message)


def stop(process):
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


def check(binary):
    root = Path(os.environ['CAIRN_DISPOSABLE_TEST_ROOT'])
    require(root.parent == Path('/tmp') and root.name.startswith('cairn-postgres.')
            and root.stat().st_uid == os.getuid()
            and stat.S_IMODE(root.stat().st_mode) == 0o700
            and (root / 'data' / 'PG_VERSION').is_file(),
            'fresh private PostgreSQL cluster required')
    require(binary == root / 'cairn' and binary.is_file() and not binary.is_symlink(),
            'disposable Cairn binary required')
    dsn = f"host={root / 'socket'} dbname=cairn_bound_compact_notice sslmode=disable"
    require(os.environ.get('CAIRN_TEST_DATABASE_URL') == dsn
            and os.environ.get('CAIRN_DATABASE_URL') == dsn,
            'dedicated disposable database required')

    def sql(statement):
        result = subprocess.run(['psql', '-X', '-A', '-t', '-d', dsn, '-c', statement],
                                capture_output=True, text=True, timeout=10)
        require(result.returncode == 0, 'disposable SQL read failed')
        return result.stdout.strip()

    require(sql('SELECT count(*) FROM cairn.agent_event') == '0'
            and sql('SELECT count(*) FROM cairn.agent_session') == '0'
            and sql('SELECT count(*) FROM cairn.memory_record') == '0',
            'bound compact database is not fresh')
    repo = 'fixture:cairn-216-bound-compact'
    home = root / 'bound-compact-home'
    home.mkdir(mode=0o700)
    work = home / 'work'
    (work / '.git').mkdir(parents=True)
    principals = {}
    identities = []
    for role in ('sender', 'worker'):
        token = secrets.token_urlsafe(32)
        path = home / f'{role}.token'
        path.write_text(token, encoding='utf-8')
        path.chmod(0o600)
        principals[role] = f'agent/bound-compact-{role}-{uuid.uuid4()}'
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                               principal=principals[role], repo=repo,
                               role='agent', destination='hosted'))
    identities_path = home / 'identities.json'
    identities_path.write_text(json.dumps(identities), encoding='utf-8')
    identities_path.chmod(0o600)
    env = dict(os.environ, CAIRN_HOME=str(home), CAIRN_INBOX_ROOT=str(home))
    server = subprocess.Popen([str(binary), 'serve'], env=env, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL)
    owner = None
    try:
        socket = home / 'api.sock'
        deadline = time.monotonic() + 10
        while not socket.exists():
            require(server.poll() is None and time.monotonic() < deadline,
                    'disposable API did not start')
            time.sleep(0.02)
        require(stat.S_ISSOCK(socket.stat().st_mode), 'disposable API socket missing')

        def profile(role, command, body, session=None):
            options = ['--socket', str(socket), '--token-file', str(home / f'{role}.token')]
            if session:
                options += ['--agent-id', session['agent_id'],
                            '--execution-id', session['execution_id']]
            result = subprocess.run([str(binary), 'agent', *options, command],
                                    input=json.dumps(body), capture_output=True, text=True,
                                    env=env, timeout=15)
            response = json.loads(result.stdout)
            require(result.returncode == 0 and response['status'] == 'OK',
                    f"{command}: {response['status']}")
            return response['data']

        def operator(command, body):
            result = subprocess.run([str(binary), command], input=json.dumps(body),
                                    capture_output=True, text=True, env=env, timeout=15)
            response = json.loads(result.stdout)
            require(result.returncode == 0 and response['status'] == 'OK',
                    f"operator {command}: {response['status']}")
            return response['data']

        profile('sender', 'version', {})
        native_id = str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        binding_path = home / 'inbox-recall.json'
        coord_state = home / 'state' / 'bound-compact-probe'
        memory_state = home / 'memory'
        coord_state.mkdir(parents=True, mode=0o700)
        memory_state.mkdir(mode=0o700)
        selector = home / 'local-selector'
        selector.write_text(SELECTOR, encoding='utf-8')
        selector.chmod(0o700)
        common = dict(cairn=str(binary), socket=str(socket),
                      token_file=str(home / 'worker.token'), repo=repo,
                      harness='codex', inbox_recall_binding=str(binding_path))
        bindings_dir = home / 'bindings'
        bindings_dir.mkdir(mode=0o700)
        coord_config = bindings_dir / 'bound-compact-probe.json'
        memory_config = home / 'memory.json'
        coord = dict(common, state_dir=str(coord_state), binding='bound-compact-probe',
                     native_delivery=True, compact_inbox_version=1,
                     model='synthetic/probe', process_names=['python3'])
        mem = dict(common, state_dir=str(memory_state), recall_mode='agent_tools',
                   context_bytes=3000, claude=str(selector))
        for path, value in ((coord_config, coord), (memory_config, mem)):
            path.write_text(json.dumps(value), encoding='utf-8')
            path.chmod(0o600)
        source_lifecycle = Path(__file__).resolve().parents[1] / 'integrations/lifecycle'
        lifecycle = home / 'lifecycle'
        lifecycle.mkdir(mode=0o700)
        for name in ('coordination.py', 'memory.py', 'inbox_recall.py'):
            target = lifecycle / name
            target.write_bytes((source_lifecycle / name).read_bytes())
            target.chmod(0o600)

        def pinned(path):
            return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

        binding_path.write_text(json.dumps(dict(schema='cairn.inbox-recall-binding/1',
            enabled=True, bridge=pinned(lifecycle / 'inbox_recall.py'),
            engine=pinned(lifecycle / 'memory.py'), memory_config=pinned(memory_config),
            coordination_configs=[pinned(coord_config)])), encoding='utf-8')
        binding_path.chmod(0o600)
        spec = importlib.util.spec_from_file_location('bound_compact_probe_bridge',
                                                  lifecycle / 'inbox_recall.py')
        bridge = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bridge)
        bridge.validate_binding(mem, require_enabled=True)

        owner = subprocess.Popen([sys.executable, '-u', '-c', OWNER,
                                  str(lifecycle / 'coordination.py'), str(coord_config)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, text=True, env=env)

        def owner_request(request):
            owner.stdin.write(json.dumps(request) + '\n')
            owner.stdin.flush()
            require(select.select([owner.stdout], [], [], 30)[0], 'synthetic owner timed out')
            result = json.loads(owner.stdout.readline())
            require(result['code'] == 0, f"synthetic owner: {result['stdout']} {result['stderr']}")
            return json.loads(result['stdout'])

        base = dict(session_id=native_id, turn_id=turn_id, cwd=str(work),
                    workstream='Fixture compact notice', model='synthetic/probe')

        def hook(name, prompt='Synthetic native boundary'):
            return owner_request(dict(base, hook_event_name=name, prompt=prompt))

        def compact(*args):
            return owner_request(dict(_compact=args, session_id=native_id))

        grant = operator('bootstrap', dict(request_id=str(uuid.uuid4()),
                         reason='Authorize disposable mandatory memory'))
        required_body = 'Preserve the exact fixture condition and do not infer task success.'
        scope = dict(repo=repo, task_id='*', run_id='*')
        required = operator('issue', dict(request_id=str(uuid.uuid4()),
            grant_id=grant['grant_id'], mandatory=True, policy_key='bound-compact-required',
            category='workflow', reason='Test source budget without dropping required context',
            draft=dict(kind='instruction', body=required_body, claim_type='self',
                       sensitivity='shareable', scope=scope)))
        hook('SessionStart')
        agents = profile('worker', 'agent-directory', dict(repo=repo, harness='codex'))['agents']
        matched = [agent for agent in agents if agent['native_session_id'] == native_id]
        require(len(matched) == 1, 'native hook did not register one worker')
        worker = matched[0]
        require(sql(f"SELECT owner FROM cairn.agent_session WHERE agent_id='{worker['agent_id']}'")
                == principals['worker'], 'Store worker owner differs')
        source_body = 'Exact bound compact notice source. ' * 105
        require(3000 < len(source_body.encode()) < 4096, 'source does not straddle hook budget')
        source = profile('sender', 'create', dict(request_id=str(uuid.uuid4()),
            draft=dict(kind='note', body=source_body, scope=scope,
                       claim_type='self', sensitivity='shareable')))
        ref = dict(record_id=source['record_id'], version=source['version'])
        event = profile('sender', 'event-publish', dict(request_id=str(uuid.uuid4()),
            kind='notice', ref=ref, destination=dict(type='agent', name=worker['inbox'])))
        delivery = profile('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
        session = dict(agent_id=worker['agent_id'], execution_id=worker['execution_id'])
        claim_id = str(uuid.uuid4())
        admitted = profile('worker', 'session-inbox-claim', dict(request_id=claim_id,
            session=session, delivery_id=delivery['delivery_id'], native_turn_id=turn_id,
            turn_exclusive=True))['attempt']
        require(admitted and admitted['delivery']['delivery_id'] == delivery['delivery_id']
                and admitted['native_turn_id'] == turn_id,
                'synthetic host admission did not pin exact delivery and turn')
        engine_spec = importlib.util.spec_from_file_location('bound_compact_probe_coord',
                                                            lifecycle / 'coordination.py')
        engine = importlib.util.module_from_spec(engine_spec)
        engine_spec.loader.exec_module(engine)
        state_path = engine.state_path(coord, native_id)
        saved = json.loads(state_path.read_text())
        saved['inbox_intent'] = dict(request_id=claim_id, session=session, native_turn_id=turn_id)
        saved['inbox_attempt'] = admitted
        engine.write_state(state_path, saved)
        wake = engine.wake_message(dict(session=session, delivery_id=delivery['delivery_id']))
        output = hook('UserPromptSubmit', prompt=wake)
        text = output['hookSpecificOutput']['additionalContext']
        contexts = [json.loads(path.read_text()) for path in (coord_state / 'inbox').glob('*.json')]
        contexts = [context for context in contexts if context.get('event_id') == event['event_id']]
        require(len(contexts) == 1, 'bound hook did not write exact notice context')
        context = contexts[0]
        require(context['source'] == ref and context['kind'] == 'notice'
                and context['attempt_id'] == admitted['attempt_id']
                and len(context['compact_handle']) == 16, 'compact context identity differs')
        handle = context['compact_handle']
        require(required_body in text and source_body not in text
                and f'cairn-inbox read {handle}' in text
                and f'cairn-inbox settle {handle}' in text,
                'bound hook lost required context or failed scoped source fallback')
        require(len(json.dumps(output, ensure_ascii=False, separators=(',', ':')).encode()) <= 3000,
                'bound hook exceeded configured byte budget')
        read = compact('read', handle)
        require(read['body'] == source_body and read['source'] == ref,
                'scoped helper did not return exact whole source')
        require(profile('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]['state']
                == 'leased', 'scoped read settled notice')
        settled = compact('settle', handle, 'handled')
        require(settled['status'] == 'handled' and settled['attempts'] == 1,
                'explicit helper settlement differs')
        require(compact('settle', handle, 'handled') == settled,
                'identical helper retry changed confirmed settlement')
        delivery = profile('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
        require(delivery['state'] == 'handled' and delivery['attempts'] == 1
                and delivery['delivery_id'] == context['delivery_id']
                and delivery.get('result') is None,
                'durable notice settlement differs')
        journal = json.loads((coord_state / 'intents' / f"{context['attempt_id']}.json").read_text())
        require(journal['completion']['status'] == 'committed'
                and journal['completion']['request_id'] == context['request_ids']['completion'],
                'helper did not retain committed acknowledgement identity')
        require(required['record_id'] in json.dumps(output),
                'bound output did not retain required Store record identity')
        print(json.dumps(dict(case='bound_compact_notice', store_api='disposable',
                              callbacks='synthetic_store_claim_and_bound_wake_prompt',
                              required_context='whole', source='scoped_whole_read',
                              settlement='explicit_handled_once', provider_called=False,
                              actor_task='unrun', task_outcome='unassessed')))
    finally:
        stop(owner)
        stop(server)


if __name__ == '__main__':
    require(len(sys.argv) == 2, 'usage: check_bound_compact_notice.py BINARY')
    check(Path(sys.argv[1]))
