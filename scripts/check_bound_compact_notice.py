#!/usr/bin/env python3
"""Join real bound memory recall to a compact notice in a disposable Store.

All host callbacks and the native owner are synthetic. No model is launched.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import disposable_host



SELECTOR = r'''#!/usr/bin/env python3
import json, sys
sys.stdin.read()
print(json.dumps(dict(type='result', is_error=False,
    structured_output=dict(checkpoint=None, workstream=None, memories=[]))))
'''


def require(value, message):
    if not value:
        raise AssertionError(message)



def check(binary):
    cluster = disposable_host.ProvidedCluster.from_environment()
    root = cluster.root
    repo = 'fixture:cairn-216-bound-compact'
    host = disposable_host.DisposableHost.start(
        cluster, binary=binary, repo=repo, database='cairn_bound_compact_notice',
        home=root / 'bound-compact-home', roles=('sender', 'worker'),
        principals=lambda role: f'agent/bound-compact-{role}-{uuid.uuid4()}', inbox_root=True)
    home, env, socket, sql = host.home, host.env, host.socket, host.sql
    principals = host.principals
    work = home / 'work'
    (work / '.git').mkdir(parents=True)
    owner = None
    try:
        host.require_fresh('agent_event', 'agent_session', 'memory_record',
                           message='bound compact database is not fresh')

        def profile(role, command, body, session=None):
            return host.agent(role, command, body, session=session)

        def operator(command, body):
            return host.operator(command, body)

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
        source_lifecycle = Path(__file__).resolve().parents[1] / "integrations/lifecycle"
        lifecycle = home / "lifecycle"
        spec = importlib.util.spec_from_file_location("fixture_lifecycle_bundle",
                                                    source_lifecycle / "lifecycle_bundle.py")
        bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bundle)
        bundle.stage_runtime(source_lifecycle, lifecycle)
        descriptor = bundle.bundle_descriptor(lifecycle)

        def pinned(path):
            return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())

        manifest = dict(schema="cairn.inbox-recall-binding/2", enabled=True,
            bridge=dict(pinned(lifecycle / "inbox_recall.py"), bundle=descriptor),
            engine=dict(pinned(lifecycle / "memory.py"), bundle=descriptor),
            memory_config=pinned(memory_config), coordination_configs=[pinned(coord_config)])
        binding_path.write_text(json.dumps(manifest), encoding="utf-8")
        binding_path.chmod(0o600)
        bridge = bundle.Bundle(lifecycle).load_pinned(manifest["bridge"], "inbox_recall")
        bridge.validate_binding(mem, require_enabled=True)

        owner = disposable_host.HookOwner.start(lifecycle / 'coordination.py', coord_config, env,
                                                hook_timeout=20, reply_timeout=30)

        def owner_request(request, expected_code=0):
            result = owner.send(request)
            require(result['code'] == expected_code,
                    f"synthetic owner: {result['stdout']} {result['stderr']}")
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

        # Exercise the Store's real forgotten-payload response after a committed
        # settlement. History must refuse with PAYLOAD_UNAVAILABLE, and the
        # helper must not disclose the body or replay another acknowledgement.
        preview_result = subprocess.run([str(binary), 'preview-delete', source['record_id']],
            env=env, capture_output=True, text=True, timeout=15)
        preview_envelope = json.loads(preview_result.stdout)
        require(preview_result.returncode == 0 and preview_envelope['status'] == 'OK',
                f"preview-delete: {preview_envelope.get('status')}")
        forget = dict(request_id=str(uuid.uuid4()), record_id=source['record_id'],
                      expected_version=source['version'], grant_id=grant['grant_id'],
                      preview_id=preview_envelope['data']['preview_id'])
        forget_result = subprocess.run([str(binary), 'forget'], input=json.dumps(forget),
            env=env, capture_output=True, text=True, timeout=15)
        forget_envelope = json.loads(forget_result.stdout)
        require(forget_result.returncode == 0 and forget_envelope['status'] == 'OK',
                f"forget: {forget_envelope.get('status')}")
        history_result = subprocess.run([str(binary), 'agent', '--socket', str(socket),
            '--token-file', str(home / 'worker.token'), '--agent-id', session['agent_id'],
            '--execution-id', session['execution_id'], 'history'], input=json.dumps(ref),
            env=env, capture_output=True, text=True, timeout=15)
        history_envelope = json.loads(history_result.stdout)
        require(history_result.returncode != 0
                and history_envelope['status'] == 'PAYLOAD_UNAVAILABLE',
                f"forgotten history: {history_envelope.get('status')}")
        retry = owner_request(dict(_compact=['settle', handle, 'handled'], session_id=native_id),
                              expected_code=7)
        require(retry['ok'] is False and retry['status'] == 'PAYLOAD_UNAVAILABLE'
                and 'body' not in retry,
                'committed settlement retry exposed or accepted a forgotten source')
        after_forgetting = profile('sender', 'event-inspect',
                                   dict(event_id=event['event_id']))['deliveries'][0]
        require(after_forgetting['state'] == 'handled' and after_forgetting['attempts'] == 1,
                'forgotten-source retry changed the already committed delivery')
        print(json.dumps(dict(case='bound_compact_notice', store_api='disposable',
                              callbacks='synthetic_store_claim_and_bound_wake_prompt',
                              required_context='whole', source='scoped_whole_read',
                              settlement='explicit_handled_once_then_forgotten_retry_refused', provider_called=False,
                              actor_task='unrun', task_outcome='unassessed')))
    finally:
        if owner is not None:
            owner.stop()
        host.close()


if __name__ == '__main__':
    require(len(sys.argv) == 2, 'usage: check_bound_compact_notice.py BINARY')
    check(Path(sys.argv[1]))
