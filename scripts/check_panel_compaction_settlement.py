#!/usr/bin/env python3
"""Exercise two exact notices across synthetic compact boundaries in a disposable Store.

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
    dsn = f"host={root / 'socket'} dbname=cairn_panel_compaction_settlement sslmode=disable"
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
            'compaction panel database is not fresh')
    repo = 'fixture:cairn-227-compaction-settlement'
    home = root / 'panel-compaction-home'
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
        principals[role] = f'agent/panel-compaction-{role}-{uuid.uuid4()}'
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

        profile('sender', 'version', {})
        native_id = str(uuid.uuid4())
        turn_id = str(uuid.uuid4())
        binding_path = home / 'inbox-recall.json'
        coord_state = home / 'state' / 'panel-compaction-probe'
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
        coord_config = bindings_dir / 'panel-compaction-probe.json'
        memory_config = home / 'memory.json'
        coord = dict(common, state_dir=str(coord_state), binding='panel-compaction-probe',
                     native_delivery=True, compact_inbox_version=1,
                     model='synthetic/probe', process_names=['python3'])
        mem = dict(common, state_dir=str(memory_state), recall_mode='agent_tools',
                   context_lifecycle='compact/1', context_bytes=7000, claude=str(selector))
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
        spec = importlib.util.spec_from_file_location('panel_compaction_probe_bridge',
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
                    workstream='Fixture compaction settlement', model='synthetic/probe')

        def hook(name, prompt='Synthetic native boundary'):
            return owner_request(dict(base, hook_event_name=name, prompt=prompt))

        def compact(*args):
            return owner_request(dict(_compact=args, session_id=native_id))

        def memory_hook(name, source='startup'):
            event = dict(base, hook_event_name=name, source=source, prompt='')
            if name == 'PreCompact':
                transcript = work / 'empty-transcript.jsonl'
                transcript.write_text('', encoding='utf-8')
                event['transcript_path'] = str(transcript)
            result = subprocess.run([sys.executable, str(lifecycle / 'memory.py'),
                                     '--config', str(memory_config)], input=json.dumps(event),
                                    capture_output=True, text=True, env=env, timeout=20)
            require(result.returncode == 0,
                    f'memory {name}/{source}: {result.stdout} {result.stderr}')
            return json.loads(result.stdout)

        hook('SessionStart')
        agents = profile('worker', 'agent-directory', dict(repo=repo, harness='codex'))['agents']
        matched = [agent for agent in agents if agent['native_session_id'] == native_id]
        require(len(matched) == 1, 'native hook did not register one worker')
        worker = matched[0]
        require(sql(f"SELECT owner FROM cairn.agent_session WHERE agent_id='{worker['agent_id']}'")
                == principals['worker'], 'Store worker owner differs')
        session = dict(agent_id=worker['agent_id'], execution_id=worker['execution_id'])
        scope = dict(repo=repo, task_id='*', run_id='*')
        engine_spec = importlib.util.spec_from_file_location('panel_compaction_probe_coord',
                                                             lifecycle / 'coordination.py')
        engine = importlib.util.module_from_spec(engine_spec)
        engine_spec.loader.exec_module(engine)
        state_path = engine.state_path(coord, native_id)
        evidence = []

        def publish(body):
            source = profile('sender', 'create', dict(request_id=str(uuid.uuid4()),
                draft=dict(kind='note', body=body, scope=scope,
                           claim_type='self', sensitivity='shareable')))
            require(source['body'] == body and source['observed_writer'] == principals['sender'],
                    'Store source body or owner differs')
            ref = dict(record_id=source['record_id'], version=source['version'])
            event = profile('sender', 'event-publish', dict(request_id=str(uuid.uuid4()),
                kind='notice', ref=ref, destination=dict(type='agent', name=worker['inbox'])))
            deliveries = profile('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries']
            require(len(deliveries) == 1 and deliveries[0]['state'] == 'pending'
                    and event['ref'] == ref, 'notice was not pending with exact source')
            return dict(body=body, ref=ref, event=event, delivery=deliveries[0])

        def admit(notice):
            request_id = str(uuid.uuid4())
            attempt = profile('worker', 'session-inbox-claim', dict(request_id=request_id,
                session=session, delivery_id=notice['delivery']['delivery_id'],
                native_turn_id=base['turn_id'], turn_exclusive=True))['attempt']
            require(attempt and attempt['native_turn_id'] == base['turn_id']
                    and attempt['delivery']['delivery_id'] == notice['delivery']['delivery_id'],
                    'synthetic host admission changed delivery or turn')
            saved = json.loads(state_path.read_text())
            saved['inbox_intent'] = dict(request_id=request_id, session=session,
                                         native_turn_id=base['turn_id'])
            saved['inbox_attempt'] = attempt
            engine.write_state(state_path, saved)
            wake = engine.wake_message(dict(session=session,
                                            delivery_id=notice['delivery']['delivery_id']))
            output = hook('UserPromptSubmit', prompt=wake)
            text = output['hookSpecificOutput']['additionalContext']
            contexts = [json.loads(path.read_text()) for path in (coord_state / 'inbox').glob('*.json')]
            contexts = [context for context in contexts if context.get('attempt_id') == attempt['attempt_id']]
            require(len(contexts) == 1 and contexts[0]['source'] == notice['ref']
                    and contexts[0]['event_id'] == notice['event']['event_id'],
                    'bound hook did not retain one exact notice context')
            context = contexts[0]
            require(notice['body'] in text and context['compact_handle'] in text,
                    'bound hook did not expose the pending notice')
            return attempt, context

        def inspect(notice):
            rows = profile('sender', 'event-inspect',
                           dict(event_id=notice['event']['event_id']))['deliveries']
            require(len(rows) == 1 and rows[0]['delivery_id'] == notice['delivery']['delivery_id'],
                    'notice delivery identity changed')
            return rows[0]

        before = publish('Pre-compaction cache check complete.')
        before_attempt, before_context = admit(before)
        require(inspect(before)['state'] == 'leased', 'first notice was not leased before compact')
        memory_hook('PreCompact')
        first_compact = memory_hook('SessionStart', 'compact')
        require(first_compact.get('ok') is True or 'hookSpecificOutput' in first_compact,
                'first memory compact callback did not finish')
        first_replay = hook('SessionStart')
        first_text = first_replay['hookSpecificOutput']['additionalContext']
        require(before['body'] in first_text
                and before_context['compact_handle'] in first_text
                and inspect(before)['state'] == 'leased',
                'pending first notice did not survive synthetic compaction')
        first_read = compact('read', before_context['compact_handle'])
        require(first_read['body'] == before['body'] and first_read['source'] == before['ref'],
                'first scoped read changed the whole source')
        first_settlement = compact('settle', before_context['compact_handle'], 'handled')
        require(first_settlement['status'] == 'handled' and first_settlement['attempts'] == 1
                and compact('settle', before_context['compact_handle'], 'handled') == first_settlement,
                'first settlement or stable retry differed')
        require(inspect(before)['state'] == 'handled', 'first notice not durably handled')
        hook('Stop')

        after = publish('Post-compaction cache check complete.')
        require(inspect(after)['state'] == 'pending' and inspect(after)['attempts'] == 0,
                'second notice was consumed before its native turn')
        base['turn_id'] = str(uuid.uuid4())
        after_attempt, after_context = admit(after)
        require(after_attempt['attempt_id'] != before_attempt['attempt_id']
                and after_context['compact_handle'] != before_context['compact_handle'],
                'two notices shared native attempt or scoped handle')
        memory_hook('PreCompact')
        second_compact = memory_hook('SessionStart', 'compact')
        require(second_compact.get('ok') is True or 'hookSpecificOutput' in second_compact,
                'second memory compact callback did not finish')
        second_replay = hook('SessionStart')
        second_text = second_replay['hookSpecificOutput']['additionalContext']
        require(after['body'] in second_text and before['body'] not in second_text
                and after_context['compact_handle'] in second_text
                and inspect(after)['state'] == 'leased',
                'second compact callback lost pending notice or replayed settled notice')
        second_read = compact('read', after_context['compact_handle'])
        require(second_read['body'] == after['body'] and second_read['source'] == after['ref'],
                'second scoped read changed whole source')
        second_settlement = compact('settle', after_context['compact_handle'], 'handled')
        require(second_settlement['status'] == 'handled' and second_settlement['attempts'] == 1
                and compact('settle', after_context['compact_handle'], 'handled') == second_settlement,
                'second settlement or stable retry differed')
        for notice, context in ((before, before_context), (after, after_context)):
            row = inspect(notice)
            require(row['state'] == 'handled' and row['attempts'] == 1
                    and row.get('result') is None,
                    'durable notice outcome differs')
            journal = json.loads((coord_state / 'intents' / f"{context['attempt_id']}.json").read_text())
            require(journal['completion']['status'] == 'committed'
                    and journal['completion']['request_id'] == context['request_ids']['completion'],
                    'settlement journal identity or state differs')
            evidence.append(dict(event_id=notice['event']['event_id'],
                                 attempt_id=context['attempt_id'], status=row['state']))
        require(sql('SELECT count(*) FROM cairn.agent_session_attempt') == '2'
                and sql('SELECT count(*) FROM cairn.agent_delivery') == '2',
                'compaction callbacks created duplicate attempts or deliveries')
        print(json.dumps(dict(case='compaction_before_after_settlement',
                              callbacks='synthetic_precompact_and_compact_twice',
                              notices=evidence, source_extent='whole',
                              settlement='handled_once_per_notice',
                              model_compaction='unobserved', provider_called=False,
                              task_outcome='unassessed')))
    finally:
        stop(owner)
        stop(server)


if __name__ == '__main__':
    require(len(sys.argv) == 2, 'usage: check_panel_compaction_settlement.py BINARY')
    check(Path(sys.argv[1]))
