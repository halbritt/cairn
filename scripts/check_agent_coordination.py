"""Exercise native hook/process association through the real disposable Unix API."""
import json
import importlib.util
import shlex
import os
import hashlib
import shutil
import stat
from pathlib import Path
import select
import signal
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
            env=env, text=True, capture_output=True, timeout=15)
    else:
        result = subprocess.run([sys.executable, script, 'hook', '--config', config],
            input=line, text=True, capture_output=True, timeout=15)
    print(json.dumps(dict(code=result.returncode, stdout=result.stdout, stderr=result.stderr)), flush=True)
'''


def check(binary, root, repo, api_call):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    script = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    compact_root = root / "compact-native"
    compact_root.mkdir(mode=0o700)
    configs = compact_root / "bindings"
    configs.mkdir(mode=0o700)
    config = configs / "codex-probe.json"
    state = compact_root / "state" / "codex-probe"
    state.mkdir(parents=True, mode=0o700)
    config.write_text(json.dumps(dict(cairn=str(Path(binary).resolve()), socket=str(root / "api.sock"),
        token_file=str(root / "bob.token"), repo=repo, harness="codex", binding="codex-probe",
        model="configured", process_names=["python3"], state_dir=str(state), native_delivery=True,
        compact_inbox_version=1)))
    config.chmod(0o600)
    processes = []

    def owner(env=None, native_queue=False):
        args = ['app-server', '--listen', 'unix://'+str(root/'native.sock')] if native_queue else []
        env = dict(env or os.environ, CAIRN_INBOX_ROOT=str(compact_root))
        p = subprocess.Popen([sys.executable, "-u", "-c", OWNER, str(script), str(config), *args],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        processes.append(p)
        return p

    def hook(p, event, code=0):
        p.stdin.write(json.dumps(event) + "\n")
        p.stdin.flush()
        ready, _, _ = select.select([p.stdout], [], [], 20)
        assert ready, "native hook owner did not respond"
        result = json.loads(p.stdout.readline())
        assert result["code"] == code, result
        return json.loads(result["stdout"]) if code == 0 else result

    def compact(p, native_session, *args, status='OK'):
        p.stdin.write(json.dumps(dict(_compact=args, session_id=native_session)) + '\n')
        p.stdin.flush()
        ready, _, _ = select.select([p.stdout], [], [], 20)
        assert ready, 'compact command owner did not respond'
        result = json.loads(p.stdout.readline())
        output = json.loads(result['stdout'])
        if status == 'OK':
            assert result['code'] == 0 and output['ok'], result
        else:
            assert result['code'] != 0 and output['status'] == status, result
        return output

    def watch():
        subprocess.run([sys.executable, str(script), "watch", "--config-dir", str(configs), "--once"],
                       capture_output=True, text=True, timeout=15, check=True)

    def entry(ident, offline=False):
        args = ["list", "--agent-id", ident]
        if offline:
            args.append("--include-offline")
        return api_call("bob", "agents", *args)["agents"]

    event = dict(session_id="native-probe", cwd=str(root), hook_event_name="UserPromptSubmit",
                 model="observed-one", prompt="PRIVATE PROMPT MUST NOT BE RETAINED")
    try:
        first = owner()
        response = hook(first, event)
        agents = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
        agent = next(a for a in agents if a["native_session_id"] == "native-probe")
        assert agent["agent_id"] in response["hookSpecificOutput"]["additionalContext"]
        assert agent["metadata"]["observed_model"] == "observed-one"
        metadata = dict(agent["metadata"], task_summary="Selected chart editor task")
        api_call("bob", "agent-context", raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()),
            session={k: agent[k] for k in ("agent_id", "execution_id")},
            expected_revision=agent["context_revision"], metadata=metadata)))
        # A concurrent second process must not take the same native conversation.
        second = owner(env=dict(os.environ, HERDR_ENV='1', HERDR_SOCKET_PATH=str(root/'fixture-host.sock')))
        refused = hook(second, event, code=1)
        assert "SESSION_BUSY" in refused["stderr"]
        first.terminate()
        first.communicate(timeout=5)
        watch()
        assert not entry(agent["agent_id"])
        assert entry(agent["agent_id"], True)[0]["stopped"]
        # Resume retains the UUID and task metadata even though its old host died.
        event["model"] = "observed-two"
        hook(second, event)
        resumed = entry(agent["agent_id"])[0]
        assert resumed["execution_id"] != agent["execution_id"]
        assert resumed["metadata"]["task_summary"] == "Selected chart editor task"
        assert resumed["metadata"]["observed_model"] == "observed-two"
        revision = resumed["context_revision"]
        watch()
        assert entry(agent["agent_id"])[0]["context_revision"] == revision
        # A separate conversation in the same process gets its own UUID.
        hook(second, dict(event, session_id="native-fork"))
        entries = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
        fork = next(a for a in entries if a["native_session_id"] == "native-fork")
        assert fork["agent_id"] != resumed["agent_id"]
        replacement = api_call("bob", "agent-register", raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()),
            repo=repo, binding="codex-probe", native_session_id="native-fork", metadata=fork['metadata'])))
        watch()
        assert entry(fork['agent_id'])[0]['execution_id'] == replacement['execution_id'], 'watcher replaced a newer execution'
        refused = hook(second, dict(event, session_id="native-fork"), code=1)
        assert 'STALE_SESSION' in refused['stderr']
        hook(second, dict(event, session_id="native-fork", hook_event_name="SessionEnd"))
        assert entry(fork['agent_id'])[0]['execution_id'] == replacement['execution_id'], 'old process stopped a newer execution'
        api_call('bob', 'agent-leave', raw=True, body=json.dumps({k: replacement[k] for k in ('agent_id', 'execution_id')}))
        for path in state.glob("*.json"):
            assert "PRIVATE PROMPT" not in path.read_text()
            assert path.stat().st_mode & 0o777 == 0o600
        hook(second, dict(event, hook_event_name="SessionEnd"))
        assert not entry(agent["agent_id"])
        hook(second, dict(event, session_id='native-outage-end'))
        ended = next(a for a in api_call('bob', 'agents', 'list', '--harness', 'codex')['agents'] if a['native_session_id'] == 'native-outage-end')
        configured = config.read_text()
        config.write_text(json.dumps(dict(json.loads(configured), socket=str(root / 'unavailable.sock'))))
        try:
            hook(second, dict(event, session_id='native-outage-end', hook_event_name='SessionEnd'), code=1)
        finally:
            config.write_text(configured)
        watch()
        assert second.poll() is None and not entry(ended['agent_id']), 'ending native turn was revived after API recovery'
        hook(second, dict(event, session_id='native-delivery'))
        recipient = next(a for a in api_call('bob', 'agents', 'list', '--harness', 'codex')['agents'] if a['native_session_id'] == 'native-delivery')
        source = api_call('alice', 'create', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()), draft=dict(
            kind='note', body='Selected native inbox request', sensitivity='shareable', claim_type='self',
            scope=dict(repo=repo, task_id='*', run_id='*')))))
        message = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', recipient['inbox'],
            '--kind', 'request', '--version', str(source['version']), source['record_id'])
        boundary = hook(second, dict(event, session_id='native-delivery', hook_event_name='Stop'))
        assert boundary.get('decision') == 'block', 'busy native session did not receive its queued request at Stop'
        contexts = list((state / 'inbox').glob('*.json'))
        context = next(json.loads(p.read_text()) for p in contexts if json.loads(p.read_text())['event_id'] == message['event_id'])
        assert context['agent_id'] == recipient['agent_id'] and context['schema'] == 'cairn.session-inbox/1'
        assert context['source'] == message['ref']
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in contexts)
        completed = subprocess.run(context['completion'], input='Selected native handling result', text=True,
            capture_output=True, check=True, timeout=10)
        assert json.loads(completed.stdout)['data']['state'] == 'handled'
        result_ref = json.loads(completed.stdout)['data']['result']
        reply = subprocess.run([*context['response'], '--version', str(result_ref['version']), result_ref['record_id']],
            capture_output=True, text=True, check=True, timeout=10)
        replied = json.loads(reply.stdout)['data']
        assert replied['from'] == recipient['inbox']
        assert replied['causation_id'] == message['event_id']
        assert hook(second, dict(event, session_id='native-delivery', hook_event_name='Stop')) == {}
        assert api_call('alice', 'event-status', message['event_id'])['deliveries'][0]['state'] == 'handled'
        hook(second, dict(event, session_id='native-delivery', hook_event_name='SessionEnd'))
        # A partition across completion that outlasts the lease: the journaled
        # result is replayed after the link returns and accepted late under the
        # same still-open hold. Nothing reconciles while its outcome is unknown.
        hook(second, dict(event, session_id='native-partition'))
        parted = next(a for a in api_call('bob', 'agents', 'list', '--harness', 'codex')['agents'] if a['native_session_id'] == 'native-partition')
        parted_message = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', parted['inbox'],
            '--kind', 'request', '--version', str(source['version']), source['record_id'])
        assert hook(second, dict(event, session_id='native-partition', hook_event_name='Stop')).get('decision') == 'block'
        parted_context = next(json.loads(p.read_text()) for p in (state / 'inbox').glob('*.json')
                              if json.loads(p.read_text())['event_id'] == parted_message['event_id'])
        socket, cut = root / 'api.sock', root / 'api.sock.partitioned'
        os.rename(socket, cut)
        try:
            lost = subprocess.run(parted_context['completion'], input='Selected result across a partition',
                                  text=True, capture_output=True, timeout=30)
            assert lost.returncode == 7 and json.loads(lost.stdout)['status'] == 'API_CONNECTION_FAILED', lost.stdout
            hook(second, dict(event, session_id='native-partition', hook_event_name='Stop'), code=1)
            watch()
            psql = Path(subprocess.run(['pg_config', '--bindir'], capture_output=True, text=True, check=True).stdout.strip()) / 'psql'
            subprocess.run([str(psql), os.environ['CAIRN_TEST_DATABASE_URL'], '-qtAc',
                "UPDATE cairn.agent_delivery d SET lease_until=clock_timestamp()-interval '1 second' FROM cairn.agent_event e "
                "WHERE e.event_id=d.event_id AND e.event_id='" + parted_message['event_id'] + "'"], check=True, capture_output=True, timeout=15)
        finally:
            os.rename(cut, socket)
        held = api_call('alice', 'event-status', parted_message['event_id'])['deliveries'][0]
        assert held['state'] == 'leased' and held['attempts'] == 1 and not held.get('late'), held
        watch()
        settled = api_call('alice', 'event-status', parted_message['event_id'])['deliveries'][0]
        assert settled['state'] == 'handled' and settled['late'] and settled['attempts'] == 1, settled
        intents = json.loads((state / 'intents' / (parted_context['attempt_id'] + '.json')).read_text())
        assert intents['completion']['status'] == 'committed' and intents['completion']['result'] == settled['result']
        assert (state / 'intents' / (parted_context['attempt_id'] + '.json')).stat().st_mode & 0o777 == 0o600
        replayed = subprocess.run(parted_context['completion'], input='Selected result across a partition',
                                  text=True, capture_output=True, check=True, timeout=30)
        assert json.loads(replayed.stdout)['data']['result'] == settled['result']
        # The watcher already reconciled this attempt. A reply the agent journals
        # afterwards, while it still cannot reach the API, is replayed too.
        parted_state = next(json.loads(p.read_text()) for p in state.glob('*.json')
                            if json.loads(p.read_text()).get('agent', {}).get('agent_id') == parted['agent_id'])
        assert not parted_state.get('inbox_intent') and parted_context['attempt_id'] in parted_state['inbox_journals']
        os.rename(socket, cut)
        try:
            lost_reply = subprocess.run([*parted_context['response'], '--version', str(settled['result']['version']),
                settled['result']['record_id']], capture_output=True, text=True, timeout=30)
            assert json.loads(lost_reply.stdout)['status'] == 'API_CONNECTION_FAILED', lost_reply.stdout
        finally:
            os.rename(cut, socket)
        watch()
        intents = json.loads((state / 'intents' / (parted_context['attempt_id'] + '.json')).read_text())
        assert intents['response']['status'] == 'committed', intents['response']
        replies, after = [], 0
        while True:
            page = api_call('alice', 'events', '--after', str(after), '--limit', '100')
            replies += [e for e in page['events']
                        if e.get('causation_id') == parted_message['event_id'] and e['kind'] == 'response']
            if not page['more']:
                break
            after = page['next_after']
        assert len(replies) == 1 and replies[0]['from'] == parted['inbox'], replies
        assert hook(second, dict(event, session_id='native-partition', hook_event_name='Stop')) == {}
        hook(second, dict(event, session_id='native-partition', hook_event_name='SessionEnd'))
        print('Partition across completion: journaled result replayed after the lease lapsed and accepted late under its hold')
        # The compact notice/response path must cross the same native process
        # boundary and real history/renew/ack API as the full request path.
        def compact_delivery(native, kind, body, sender='alice', cause=None):
            hook(second, dict(event, session_id=native))
            recipient = next(a for a in api_call('bob', 'agents', 'list', '--harness', 'codex')['agents']
                             if a['native_session_id'] == native)
            source = api_call(sender, 'create', raw=True, body=json.dumps(dict(request_id=str(uuid.uuid4()),
                draft=dict(kind='note', body=body, sensitivity='shareable', claim_type='self',
                           scope=dict(repo=repo, task_id='*', run_id='*')))))
            args = ['--request-id', str(uuid.uuid4()), '--to', recipient['inbox'],
                    '--kind', kind, '--version', str(source['version'])]
            if cause:
                args += ['--causation-id', cause]
            notice = api_call(sender, 'publish', *args, source['record_id'])
            boundary = hook(second, dict(event, session_id=native, hook_event_name='Stop'))
            assert boundary.get('decision') == 'block', boundary
            context = next(json.loads(p.read_text()) for p in (state / 'inbox').glob('*.json')
                           if json.loads(p.read_text())['event_id'] == notice['event_id'])
            assert context['source'] == notice['ref'] == dict(record_id=source['record_id'], version=source['version'])
            assert context['kind'] == kind and context['sender'] == notice['from']
            assert len(context['compact_handle']) == 16
            return notice, context, boundary

        small = 'Selected notice; keep this exact source and acknowledge explicitly.'
        notice, compact_context, boundary = compact_delivery('native-compact-notice', 'notice', small)
        assert small in json.dumps(boundary) and compact_context['compact_handle'] in json.dumps(boundary), boundary
        handle = compact_context['compact_handle']
        read = compact(second, 'native-compact-notice', 'read', handle)
        assert read['body'] == small and read['source'] == notice['ref']
        assert api_call('alice', 'event-status', notice['event_id'])['deliveries'][0]['state'] == 'leased'
        compact(second, 'wrong-native-session', 'settle', handle, 'handled', status='NOT_THIS_SESSION')
        outsider = subprocess.run([sys.executable, str(script), 'inbox', 'compact', 'settle', handle, 'handled'],
                                 env=dict(os.environ, CAIRN_INBOX_ROOT=str(compact_root), CODEX_THREAD_ID='native-compact-notice'),
                                 capture_output=True, text=True, timeout=15)
        assert outsider.returncode and json.loads(outsider.stdout)['status'] == 'NOT_THIS_SESSION', outsider.stdout
        settled = compact(second, 'native-compact-notice', 'settle', handle, 'handled')
        assert settled['status'] == 'handled'
        assert compact(second, 'native-compact-notice', 'settle', handle, 'handled') == settled
        compact(second, 'native-compact-notice', 'settle', handle, 'ignored', '--code', 'unsupported_kind',
                status='IDEMPOTENCY_CONFLICT')
        assert api_call('alice', 'event-status', notice['event_id'])['deliveries'][0]['state'] == 'handled'
        assert hook(second, dict(event, session_id='native-compact-notice', hook_event_name='Stop')) == {}
        hook(second, dict(event, session_id='native-compact-notice', hook_event_name='SessionEnd'))

        oversized = 'Exact long source; ' * 300
        long_notice, long_context, long_boundary = compact_delivery('native-compact-large', 'notice', oversized)
        assert len(oversized.encode()) > 4096 and oversized not in json.dumps(long_boundary)
        assert 'read its exact text' in json.dumps(long_boundary)
        long_handle = long_context['compact_handle']
        assert compact(second, 'native-compact-large', 'read', long_handle)['body'] == oversized
        index = state / 'compact' / (long_handle + '.json')
        issued = json.loads(index.read_text())
        index.write_text(json.dumps(dict(issued, issued_at=0)))
        compact(second, 'native-compact-large', 'settle', long_handle, 'failed', '--code', 'processing_failed',
                status='HANDLE_EXPIRED')
        index.write_text(json.dumps(issued))
        failed = compact(second, 'native-compact-large', 'settle', long_handle, 'failed', '--code', 'processing_failed')
        assert failed['status'] == 'failed'
        assert api_call('alice', 'event-status', long_notice['event_id'])['deliveries'][0]['state'] == 'failed'
        hook(second, dict(event, session_id='native-compact-large', hook_event_name='SessionEnd'))

        response_body = 'A selected response to the earlier request.'
        causal_request = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()),
            '--to', 'agent/bob', '--kind', 'request', '--version', str(source['version']), source['record_id'])
        response, response_context, response_boundary = compact_delivery(
            'native-compact-response', 'response', response_body, sender='bob', cause=causal_request['event_id'])
        assert response_body in json.dumps(response_boundary)
        assert response_context['source'] == response['ref']
        ignored = compact(second, 'native-compact-response', 'settle', response_context['compact_handle'],
                          'ignored', '--code', 'unsupported_kind')
        assert ignored['status'] == 'ignored'
        assert api_call('bob', 'event-status', response['event_id'])['deliveries'][0]['state'] == 'ignored'
        old_response = next(a for a in api_call('bob', 'agents', 'list', '--harness', 'codex')['agents']
                            if a['native_session_id'] == 'native-compact-response')
        hook(second, dict(event, session_id='native-compact-response', hook_event_name='SessionEnd'))
        replacement = api_call('bob', 'agent-register', raw=True, body=json.dumps(dict(
            request_id=str(uuid.uuid4()), repo=repo, binding='codex-probe',
            native_session_id='native-compact-response', metadata=old_response['metadata'])))
        assert replacement['agent_id'] == old_response['agent_id']
        assert replacement['execution_id'] != old_response['execution_id']
        compact(second, 'native-compact-response', 'settle', response_context['compact_handle'],
                'ignored', '--code', 'unsupported_kind', status='STALE_SESSION')
        compact(second, 'native-compact-response', 'read', response_context['compact_handle'], status='STALE_SESSION')
        api_call('bob', 'agent-leave', raw=True, body=json.dumps({k: replacement[k] for k in ('agent_id', 'execution_id')}))
        causal_delivery = api_call('bob', 'inbox')['delivery']
        assert causal_delivery['event']['event_id'] == causal_request['event_id']
        api_call('bob', 'ack', '--request-id', str(uuid.uuid4()), '--lease', causal_delivery['lease_id'],
                 causal_delivery['delivery_id'])
        # The real-store response fixture above exercises ignored settlement.
        # Also prove an actionable response can be read and explicitly handled,
        # with its causal source identity intact across an idempotent retry.
        handled_body = 'Confirmed the requested result and retained its source.'
        handled_response, handled_context, handled_boundary = compact_delivery(
            'native-compact-response-handled', 'response', handled_body,
            sender='bob', cause=causal_request['event_id'])
        assert handled_body in json.dumps(handled_boundary)
        assert handled_response['causation_id'] == causal_request['event_id']
        assert handled_context['source'] == handled_response['ref']
        assert handled_context['kind'] == 'response'
        handled_read = compact(second, 'native-compact-response-handled', 'read',
                               handled_context['compact_handle'])
        assert handled_read['body'] == handled_body
        assert handled_read['source'] == handled_response['ref']
        assert api_call('bob', 'event-status', handled_response['event_id'])['deliveries'][0]['state'] == 'leased'
        acknowledged = compact(second, 'native-compact-response-handled', 'settle',
                               handled_context['compact_handle'], 'handled')
        assert acknowledged['status'] == 'handled'
        assert compact(second, 'native-compact-response-handled', 'settle',
                       handled_context['compact_handle'], 'handled') == acknowledged
        handled_delivery = api_call('bob', 'event-status', handled_response['event_id'])['deliveries'][0]
        assert handled_delivery['state'] == 'handled' and handled_delivery['attempts'] == 1
        handled_journal = json.loads((state / 'intents' / f"{handled_context['attempt_id']}.json").read_text())
        assert handled_journal['completion']['status'] == 'committed'
        assert handled_journal['completion']['kind'] == 'ack'
        assert handled_journal['completion']['request_id'] == handled_context['request_ids']['completion']
        hook(second, dict(event, session_id='native-compact-response-handled', hook_event_name='SessionEnd'))
        print('Compact handled response: exact source/version and causation preserved; explicit handled settlement committed once across identical retry')
        print('Compact native inbox: real notice/response and oversized history, explicit settlements, process/session/expiry refusals')
        from test_idle_wakeup import HOST
        idle_root = root/'idle-host'
        idle_root.mkdir()
        herdr = idle_root/'herdr'
        herdr.write_text(HOST)
        herdr.chmod(0o700)
        installed_config = json.loads(config.read_text())
        installed_config['idle_wakeup'] = str(herdr)
        config.write_text(json.dumps(installed_config))
        idle_native = dict(event, session_id='native-idle-host')
        hook(second, idle_native)
        hook(second, dict(idle_native, hook_event_name='Stop'))
        idle_agent = next(a for a in api_call('bob','agents','list')['agents'] if a['native_session_id']=='native-idle-host')
        (idle_root/'fixture.json').write_text(json.dumps(dict(host=dict(agent='codex',agent_status='idle',
            pane_id='fixture:p1',terminal_id='fixture-terminal',revision=1,state_change_seq=1,focused=False,
            agent_session=dict(agent='codex',kind='id',value='native-idle-host')),
            process_info=dict(foreground_processes=[dict(pid=second.pid)],foreground_process_group_id=os.getpgid(second.pid)))))
        idle_event = api_call('alice','publish','--request-id',str(uuid.uuid4()),'--to',idle_agent['inbox'],
            '--kind','request','--version',str(source['version']),source['record_id'])
        assert idle_agent['metadata']['state']=='idle'
        for _ in range(2):
            watch()
            assert not (idle_root/'prompts.jsonl').exists(), 'native Codex without a queue fell back to a terminal prompt'
            idle_status = api_call('alice','event-status',idle_event['event_id'])['deliveries'][0]
            assert idle_status['state']=='pending' and idle_status['attempts']==0, 'watcher claimed native work'
            idle_state = next(json.loads(p.read_text()) for p in state.glob('*.json')
                              if json.loads(p.read_text()).get('agent',{}).get('agent_id')==idle_agent['agent_id'])
            assert not idle_state.get('idle_wake'), 'refused fallback retained a wake marker'
            assert not idle_state.get('inbox_intent'), 'watcher created an inbox claim intent'
            assert not any(json.loads(p.read_text())['event_id']==idle_event['event_id']
                           for p in (state/'inbox').glob('*.json')), 'watcher delivered native context'
        injected = hook(second, idle_native)
        assert 'structured inbox context' in injected['hookSpecificOutput']['additionalContext']
        idle_context = next(json.loads(p.read_text()) for p in (state/'inbox').glob('*.json')
                            if json.loads(p.read_text())['event_id']==idle_event['event_id'])
        assert idle_context['agent_id']==idle_agent['agent_id'] and idle_context['execution_id']==idle_agent['execution_id']
        assert idle_context['delivery_id']==idle_status['delivery_id'] and idle_context['source']==idle_event['ref']
        idle_status = api_call('alice','event-status',idle_event['event_id'])['deliveries'][0]
        assert idle_status['state']=='leased' and idle_status['attempts']==1
        subprocess.run(idle_context['completion'], input='Selected ordinary boundary delivery result',
                       text=True,capture_output=True,check=True,timeout=10)
        assert hook(second, dict(idle_native,hook_event_name='Stop'))=={}
        watch()
        assert not (idle_root/'prompts.jsonl').exists(), 'handled work prompted through the terminal'
        idle_status = api_call('alice','event-status',idle_event['event_id'])['deliveries'][0]
        assert idle_status['state']=='handled' and idle_status['attempts']==1
        print('Idle wakeup: refused native terminal fallback across watcher restarts, zero automatic claims and one ordinary boundary completion passed')
        hook(second,dict(idle_native,hook_event_name='SessionEnd'))
        # Native queued wakes bind the exact delivery and native turn. An owner
        # prompt must not steal its pending work; a different Stop cannot end it.
        queued_owner = owner(native_queue=True)
        queued_event = dict(event, session_id='native-queue-owner', turn_id='owner-turn')
        hook(queued_owner, queued_event)
        queued_agent = next(a for a in api_call('bob','agents','list')['agents'] if a['native_session_id']=='native-queue-owner')
        queued_message = api_call('alice','publish','--request-id',str(uuid.uuid4()),'--to',queued_agent['inbox'],
            '--kind','request','--version',str(source['version']),source['record_id'])
        assert 'structured inbox context' not in hook(queued_owner, queued_event)['hookSpecificOutput']['additionalContext']
        queued_delivery = api_call('alice','event-status',queued_message['event_id'])['deliveries'][0]
        assert queued_delivery['state']=='pending' and queued_delivery['attempts']==0
        spec = importlib.util.spec_from_file_location('native_binding_engine',script)
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        queued_path = engine.state_path(installed_config,queued_event['session_id'])
        queued_state = json.loads(queued_path.read_text())
        marker = dict(transport='codex-queue',session=engine.session_ref(queued_agent),delivery_id=queued_delivery['delivery_id'])
        queued_state['idle_wake'] = marker
        engine.write_state(queued_path,queued_state)
        wake_event = dict(queued_event, prompt=engine.wake_message(marker), turn_id='queue-turn')
        hook(queued_owner,wake_event)
        queued_context = next(json.loads(p.read_text()) for p in (state/'inbox').glob('*.json')
                              if json.loads(p.read_text())['event_id']==queued_message['event_id'])
        assert queued_context['native_turn_id']=='queue-turn'
        wrong_stop = hook(queued_owner,dict(queued_event,hook_event_name='Stop'),code=1)
        assert 'NATIVE_TURN_MISMATCH' in wrong_stop['stderr']
        assert api_call('alice','event-status',queued_message['event_id'])['deliveries'][0]['state']=='leased'
        subprocess.run(queued_context['completion'],input='Native request bound to its exact queued turn',
                       text=True,capture_output=True,check=True,timeout=10)
        assert hook(queued_owner,dict(wake_event,hook_event_name='Stop'))=={}
        hook(queued_owner,dict(wake_event,hook_event_name='SessionEnd'))
        # A natively interrupted Codex turn restores actual idle presence but
        # must never claim or release inbox work: pending stays pending, a held
        # delivery stays leased, and only its owning turn can finish it.
        interrupt_owner = owner(native_queue=True)
        interrupt_event = dict(event, session_id='native-interrupt', turn_id='interrupt-owner-turn')
        hook(interrupt_owner, interrupt_event)
        interrupt_agent = next(a for a in api_call('bob','agents','list')['agents'] if a['native_session_id']=='native-interrupt')
        assert interrupt_agent['metadata']['state']=='busy'
        interrupt_message = api_call('alice','publish','--request-id',str(uuid.uuid4()),'--to',interrupt_agent['inbox'],
            '--kind','request','--version',str(source['version']),source['record_id'])
        hook(interrupt_owner,dict(interrupt_event,hook_event_name='Interrupt'))
        interrupted_entry = next(a for a in api_call('bob','agents','list')['agents'] if a['native_session_id']=='native-interrupt')
        assert interrupted_entry['metadata']['state']=='idle', 'Interrupt did not restore idle presence'
        pending = api_call('alice','event-status',interrupt_message['event_id'])['deliveries'][0]
        assert pending['state']=='pending' and pending['attempts']==0, 'Interrupt claimed inbox work'
        interrupt_path = engine.state_path(installed_config,'native-interrupt')
        interrupt_state = json.loads(interrupt_path.read_text())
        interrupt_marker = dict(transport='codex-queue',session=engine.session_ref(interrupt_agent),
                                delivery_id=pending['delivery_id'])
        interrupt_state['idle_wake'] = interrupt_marker
        engine.write_state(interrupt_path,interrupt_state)
        interrupt_wake = dict(interrupt_event, prompt=engine.wake_message(interrupt_marker), turn_id='interrupt-wake-turn')
        hook(interrupt_owner,interrupt_wake)
        interrupt_context = next(json.loads(p.read_text()) for p in (state/'inbox').glob('*.json')
                              if json.loads(p.read_text())['event_id']==interrupt_message['event_id'])
        assert interrupt_context['native_turn_id']=='interrupt-wake-turn'
        hook(interrupt_owner,dict(interrupt_event,hook_event_name='Interrupt'))
        held = api_call('alice','event-status',interrupt_message['event_id'])['deliveries'][0]
        assert held['state']=='leased' and held['attempts']==1, 'Interrupt disturbed the held native delivery'
        subprocess.run(interrupt_context['completion'],input='Native request completed after interruption',
            text=True,capture_output=True,check=True,timeout=10)
        assert hook(interrupt_owner,dict(interrupt_wake,hook_event_name='Stop'))=={}
        hook(interrupt_owner,dict(interrupt_wake,hook_event_name='SessionEnd'))
        print('Codex Interrupt: presence idle restored while pending stayed pending and the held delivery stayed leased')
        installed_config.pop('idle_wakeup')
        config.write_text(json.dumps(installed_config))
        crashed = owner()
        hook(crashed, dict(event, session_id='native-crashed-delivery'))
        crash_agent = next(a for a in api_call('bob', 'agents', 'list', '--harness', 'codex')['agents'] if a['native_session_id'] == 'native-crashed-delivery')
        uncertain = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', crash_agent['inbox'],
            '--kind', 'request', '--version', str(source['version']), source['record_id'])
        injected = hook(crashed, dict(event, session_id='native-crashed-delivery'))
        assert 'structured inbox context' in injected['hookSpecificOutput']['additionalContext']
        watch()  # Renews both presence and the delivery while its owner lives.
        assert api_call('alice', 'event-status', uncertain['event_id'])['deliveries'][0]['state'] == 'leased'
        crashed.terminate()
        crashed.communicate(timeout=5)
        watch()
        assert api_call('alice', 'event-status', uncertain['event_id'])['deliveries'][0]['state'] == 'failed'
        resumed_context = hook(second, dict(event, session_id='native-crashed-delivery'))
        assert 'structured inbox context' not in resumed_context['hookSpecificOutput']['additionalContext'], 'uncertain native work replayed on resume'
        current_crash_agent = entry(crash_agent['agent_id'])[0]
        assert current_crash_agent['execution_id'] != crash_agent['execution_id']
        notice = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', crash_agent['inbox'],
            '--kind', 'response', '--version', str(source['version']), source['record_id'])
        hook(second, dict(event, session_id='native-crashed-delivery'))
        response_context = next(json.loads(p.read_text()) for p in (state / 'inbox').glob('*.json') if json.loads(p.read_text())['event_id'] == notice['event_id'])
        acknowledged = subprocess.run(response_context['acknowledgement'], capture_output=True, text=True, check=True, timeout=10)
        assert json.loads(acknowledged.stdout)['data']['state'] == 'handled'
        assert hook(second, dict(event, session_id='native-crashed-delivery', hook_event_name='Stop')) == {}
        hook(second, dict(event, session_id='native-restore-uncertain'))
        restoring = next(a for a in api_call('bob', 'agents', 'list', '--harness', 'codex')['agents'] if a['native_session_id'] == 'native-restore-uncertain')
        restore_event = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', restoring['inbox'],
            '--kind', 'request', '--version', str(source['version']), source['record_id'])
        hook(second, dict(event, session_id='native-restore-uncertain'))
        restore_state = next(p for p in state.glob('*.json') if json.loads(p.read_text()).get('registration', {}).get('native_session_id') == 'native-restore-uncertain')
        uncertain_state = json.loads(restore_state.read_text())
        # Crash point: the database committed the claim, but only its original
        # intent survived locally. A restore then fences the execution.
        for key in ('inbox_attempt', 'inbox_completion', 'delivered_since_idle'):
            uncertain_state.pop(key, None)
        restore_state.write_text(json.dumps(uncertain_state))
        subprocess.run([binary, 'fence-restore'], input=json.dumps(dict(request_id=str(uuid.uuid4()),
            reason='Fence disposable native inbox recovery fixture')), capture_output=True, text=True, check=True, timeout=10)
        assert hook(second, dict(event, session_id='native-restore-uncertain', hook_event_name='Stop')) == {}
        restored = entry(restoring['agent_id'])[0]
        assert restored['execution_id'] != restoring['execution_id']
        assert api_call('alice', 'event-status', restore_event['event_id'])['deliveries'][0]['state'] == 'failed'
        hook(second, dict(event, session_id='native-restore-uncertain', hook_event_name='SessionEnd'))
        # A launched conversation retains its native identity under the normal
        # profile while the original slot continues to own the request delivery.
        def raw_slot(operation, request):
            return api_call('carol', operation, raw=True, body=json.dumps(request))
        mail = api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', 'agent/carol',
            '--kind', 'request', '--version', str(source['version']), source['record_id'])
        attempt = raw_slot('wake-claim', dict(request_id=str(uuid.uuid4())))['attempt']
        assert attempt['delivery']['event']['event_id'] == mail['event_id']
        for operation in ('start', 'enter'):
            raw_slot('wake-change', dict(request_id=str(uuid.uuid4()), attempt_id=attempt['attempt_id'], operation=operation))
        wake_path = root / 'native-wake.context.json'
        wake_path.write_text(json.dumps(dict(schema='cairn.wake-context/1', native_registration=True,
            attempt_id=attempt['attempt_id'], execution_id=attempt['attempt_id'], collection=repo,
            socket=str(root / 'api.sock'), token_file=str(root / 'carol.token'))))
        wake_path.chmod(0o600)
        worker = owner(dict(os.environ, CAIRN_WAKE_CONTEXT=str(wake_path), CAIRN_LIFECYCLE_CHILD='1'))
        native_event = dict(event, session_id='actual-fresh-worker')
        supplied = hook(worker, native_event)
        linked = raw_slot('wake-attempts', dict(attempt_id=attempt['attempt_id']))['attempts'][0]
        native = entry(linked['session']['agent_id'])[0]
        assert native['native_session_id'] == native_event['session_id']
        assert native['metadata']['delivery_mode'] == 'fresh-worker'
        assert native['agent_id'] in supplied['hookSpecificOutput']['additionalContext']
        assert json.loads(wake_path.read_text())['session'] == linked['session']
        assert linked['delivery']['consumer'] == 'agent/carol'
        assert hook(worker, dict(native_event, session_id='child-must-not-inherit')) == {}
        assert hook(worker, dict(native_event, hook_event_name='Stop')) == {}
        # The slot completion contract is unchanged by native registration.
        done = api_call('carol', 'complete', '--request-id', str(uuid.uuid4()), '--lease',
            linked['delivery']['lease_id'], '--shareable', '--stdin', linked['delivery']['delivery_id'], body='Selected fresh worker result')
        assert done['state'] == 'handled'
        worker.terminate()
        worker.communicate(timeout=5)
        raw_slot('wake-change', dict(request_id=str(uuid.uuid4()), attempt_id=attempt['attempt_id'], operation='finish', reason='fixture_process_stopped'))
        watch()
        assert entry(native['agent_id'], True)[0]['stopped']
        hook(second, native_event)
        resumed_worker = entry(native['agent_id'])[0]
        assert resumed_worker['execution_id'] != native['execution_id']
        assert resumed_worker['metadata']['delivery_mode'] == 'existing-session'
        hook(second, dict(native_event, hook_event_name='SessionEnd'))
        def prepare_native_wake(label):
            api_call('alice', 'publish', '--request-id', str(uuid.uuid4()), '--to', 'agent/carol',
                '--kind', 'request', '--version', str(source['version']), source['record_id'])
            attempt = raw_slot('wake-claim', dict(request_id=str(uuid.uuid4())))['attempt']
            for operation in ('start', 'enter'):
                raw_slot('wake-change', dict(request_id=str(uuid.uuid4()), attempt_id=attempt['attempt_id'], operation=operation))
            path = root / (label + '.context.json')
            path.write_text(json.dumps(dict(schema='cairn.wake-context/1', native_registration=True,
                attempt_id=attempt['attempt_id'], execution_id=attempt['attempt_id'], collection=repo,
                socket=str(root / 'api.sock'), token_file=str(root / 'carol.token'))))
            path.chmod(0o600)
            return path, attempt['attempt_id']

        def finish_native_wake(ident, native_id):
            attempt = raw_slot('wake-attempts', dict(attempt_id=ident))['attempts'][0]
            assert attempt.get('session'), 'Installed native runtime did not associate its wake'
            agent = entry(attempt['session']['agent_id'], True)[0]
            assert agent['native_session_id'] == native_id and agent['metadata']['delivery_mode'] == 'fresh-worker'
            raw_slot('wake-change', dict(request_id=str(uuid.uuid4()), attempt_id=ident, operation='finish', reason='fixture_process_stopped'))
            assert entry(agent['agent_id'], True)[0]['stopped']
        if os.environ.get("CAIRN_HERMES_PYTHON"):
            subprocess.run([os.environ["CAIRN_HERMES_PYTHON"], str(Path(__file__).with_name("check_hermes_coordination.py")),
                str(root / "native-hermes"), os.environ["CAIRN_HERMES_ROOT"], str(config)], check=True, timeout=90)
        if os.environ.get("CAIRN_OPENCODE_BINARY"):
            from check_opencode_coordination import check as check_opencode
            check_opencode(os.environ['CAIRN_OPENCODE_BINARY'], root / 'native-opencode', json.loads(config.read_text()), api_call)
        if os.environ.get('CAIRN_AGY_BINARY'):
            from check_agy_inbox import check as check_agy_inbox
            check_agy_inbox(os.environ['CAIRN_AGY_BINARY'], os.environ['CAIRN_AGY_NATIVE_MODEL'],
                           root / 'agy-native-inbox', json.loads(config.read_text()), api_call)
            # Explicit opt-in: Agy has no configured local fixture provider, so
            # this bounded smoke test uses its existing account and one model turn.
            installer_spec = importlib.util.spec_from_file_location('agy_coordination_installer', Path(__file__).with_name('install-agent-coordination.py'))
            installer = importlib.util.module_from_spec(installer_spec)
            installer_spec.loader.exec_module(installer)
            work = root / 'native-agy'
            work.mkdir()
            subprocess.run(['git', 'init', '--quiet', str(work)], check=True, timeout=5)
            native_config = dict(json.loads(config.read_text()), harness='agy', binding='agy-native', process_names=['agy'])
            installed = installer.install(root / 'native-agy-engine', work / '.agents/hooks.json', native_config)
            agy_settings = work / '.agents/hooks.json'
            agy_hooks = json.loads(agy_settings.read_text())
            for handlers in agy_hooks['cairn-coordination'].values():
                for handler in handlers:
                    handler['command'] = '/usr/bin/env CAIRN_COORDINATION_DISABLED=0 ' + handler['command']
            agy_settings.write_text(json.dumps(agy_hooks))
            model = os.environ['CAIRN_AGY_NATIVE_MODEL']
            env = dict(os.environ)
            env['CAIRN_COORDINATION_DISABLED'] = '1'
            for key in ('CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD', 'CAIRN_WAKE_CONTEXT'):
                env.pop(key, None)
            wake_file, wake_id = prepare_native_wake('agy-native-wake')
            env.update(CAIRN_WAKE_CONTEXT=str(wake_file), CAIRN_LIFECYCLE_CHILD='1')
            result = subprocess.run([os.environ['CAIRN_AGY_BINARY'], '--new-project', '--model', model,
                '--print-timeout', '30s', '--output-format', 'json', '--print',
                'Native session identity smoke test. Reply only with your Cairn agent UUID from the injected session context. Do not use tools or access files.'],
                cwd=work, env=env, capture_output=True, text=True, timeout=40)
            (work / 'stdout.json').write_text(result.stdout)
            (work / 'stderr.log').write_text(result.stderr)
            assert result.returncode == 0, result.stderr[-1000:]
            output = json.loads(result.stdout)
            records = api_call('bob', 'agents', 'list', '--harness', 'agy', '--include-offline')['agents']
            native = next(a for a in records if a['native_session_id'] == output['conversation_id'])
            assert native['agent_id'] in output['response'], 'Agy did not observe its registered identity'
            assert native['metadata']['observed_model'] == model and native['metadata']['state'] == 'idle'
            finish_native_wake(wake_id, output['conversation_id'])
            installer.engine.watch_once(installer.engine.load_config(installed))
            assert not entry(native['agent_id']), 'Exited Agy remained live'
            print('Installed Agy PreInvocation/Stop supplied native identity/model and ended presence after exit')
        if os.environ.get("CAIRN_CLAUDE_BINARY"):
            from check_claude_inbox import check as check_claude_inbox
            check_claude_inbox(os.environ['CAIRN_CLAUDE_BINARY'], root / 'claude-native-inbox', json.loads(config.read_text()), api_call)
            from check_claude_compact_native import check as check_claude_compact_native
            check_claude_compact_native(os.environ['CAIRN_CLAUDE_BINARY'], root / 'claude-native-compact-notice',
                                        json.loads(config.read_text()), api_call, scenario='notice')
            check_claude_compact_native(os.environ['CAIRN_CLAUDE_BINARY'], root / 'claude-native-compact-response',
                                        json.loads(config.read_text()), api_call, scenario='response')
            installer_spec = importlib.util.spec_from_file_location("claude_coordination_installer", Path(__file__).with_name("install-agent-coordination.py"))
            installer = importlib.util.module_from_spec(installer_spec)
            installer_spec.loader.exec_module(installer)
            native_home = root / "native-claude-home"
            native_home.mkdir()
            settings = native_home / "settings.json"
            native_config = dict(json.loads(config.read_text()), harness="claude", binding="claude-native", process_names=["claude"], config_home=str(native_home))
            installer.install(root / "native-claude-engine", settings, native_config)
            ident = str(uuid.uuid4())
            env = dict(os.environ, HOME=str(native_home), CLAUDE_CONFIG_DIR=str(native_home), ANTHROPIC_API_KEY="fixture-only",
                       ANTHROPIC_BASE_URL="http://127.0.0.1:1", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1")
            for key in ("CLAUDECODE", "CAIRN_LIFECYCLE_DISABLED", "CAIRN_LIFECYCLE_CHILD", "CAIRN_WAKE_CONTEXT"):
                env.pop(key, None)
            wake_file, wake_id = prepare_native_wake('claude-native-wake')
            env.update(CAIRN_WAKE_CONTEXT=str(wake_file), CAIRN_LIFECYCLE_CHILD='1')
            with (root / "native-claude.stdout").open("w") as out, (root / "native-claude.stderr").open("w") as err:
                native = subprocess.Popen([os.environ["CAIRN_CLAUDE_BINARY"], "--print", "--verbose", "--output-format", "stream-json",
                    "--input-format", "stream-json", "--setting-sources", "", "--settings", str(settings), "--strict-mcp-config",
                    "--mcp-config", '{"mcpServers":{}}', "--tools", "", "--permission-mode", "dontAsk", "--session-id", ident, "--model", "sonnet"],
                    cwd=root, env=env, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True, start_new_session=True)
                try:
                    native.stdin.write(json.dumps(dict(type="user", message=dict(role="user", content="Reply OK"))) + "\n")
                    native.stdin.close()
                    deadline = time.monotonic() + 15
                    while True:
                        records = api_call("bob", "agents", "list", "--harness", "claude", "--include-offline")["agents"]
                        if any(a["native_session_id"] == ident for a in records) and raw_slot('wake-attempts', dict(attempt_id=wake_id))['attempts'][0].get('session'):
                            break
                        assert time.monotonic() < deadline and native.poll() is None, "Actual Claude hooks did not register the native conversation"
                        time.sleep(.1)
                finally:
                    try:
                        os.killpg(native.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        native.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(native.pid, signal.SIGKILL)
                        native.wait(timeout=5)
            finish_native_wake(wake_id, ident)
            subprocess.run([sys.executable, str(root / "native-claude-engine/coordination.py"), "watch", "--config-dir", str(root / "native-claude-engine/bindings"), "--once"], check=True, capture_output=True, timeout=15)
            assert not api_call("bob", "agents", "list", "--harness", "claude")["agents"]
            print("Installed Claude hooks associated the actual native conversation with its fresh wake")
        if os.environ.get("CAIRN_NODE_BINARY"):
            opencode_config = configs / "opencode-probe.json"
            opencode_config.write_text(json.dumps(dict(json.loads(config.read_text()), harness="opencode", binding="opencode-probe", state_dir=str(root / "opencode-state"))))
            subprocess.run([os.environ["CAIRN_NODE_BINARY"], str(Path(__file__).with_name("check_opencode_coordination.mjs")), str(root / "opencode-plugin"), sys.executable, str(script), str(opencode_config)], check=True, timeout=30)
            records = api_call("bob", "agents", "list", "--harness", "opencode", "--include-offline")["agents"]
            records = [a for a in records if a['native_session_id'] == 'ses_coordination']
            assert len(records) == 1 and records[0]["stopped"] and records[0]["metadata"]["observed_model"] == "fixture/probe"
        if os.environ.get("CAIRN_CODEX_BINARY"):
            from check_codex_inbox import check as check_codex_inbox
            check_codex_inbox(os.environ['CAIRN_CODEX_BINARY'], root / 'codex-native-inbox', json.loads(config.read_text()), api_call)
            installer_spec = importlib.util.spec_from_file_location("coordination_installer", Path(__file__).with_name("install-agent-coordination.py"))
            installer = importlib.util.module_from_spec(installer_spec)
            installer_spec.loader.exec_module(installer)
            native_home = root / "native-codex-home"
            native_home.mkdir()
            (native_home / "config.toml").write_text('model_provider = "fixture"\n[model_providers.fixture]\nname = "Fixture"\nbase_url = "http://127.0.0.1:1/v1"\nwire_api = "responses"\nrequires_openai_auth = false\nrequest_max_retries = 0\nstream_max_retries = 0\n')
            native_config = dict(json.loads(config.read_text()), binding="codex-native", process_names=["codex"], config_home=str(native_home))
            installed = installer.install(root / "native-codex-engine", native_home / "hooks.json", native_config)
            native_command = shlex.join([sys.executable, str(root / "native-codex-engine/coordination.py"), "hook", "--config", str(installed)])
            native_ids = []
            wake_file, wake_id = prepare_native_wake('codex-native-wake')
            def native_start(rpc):
                started = rpc("thread/start", dict(cwd=str(root), model="gpt-6-astra", approvalPolicy="never", sandbox="read-only", ephemeral=True))
                ident = started["thread"]["id"]
                native_ids.append(ident)
                rpc("turn/start", dict(threadId=ident, input=[dict(type="text", text="Reply OK", text_elements=[])]))
                deadline = time.monotonic() + 8
                while True:
                    entries = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
                    if any(a["native_session_id"] == ident for a in entries) and raw_slot('wake-attempts', dict(attempt_id=wake_id))['attempts'][0].get('session'):
                        break
                    assert time.monotonic() < deadline, "Actual Codex first-turn hooks did not register its native thread"
                    time.sleep(.1)
            previous_wake = os.environ.get('CAIRN_WAKE_CONTEXT')
            os.environ['CAIRN_WAKE_CONTEXT'] = str(wake_file)
            try:
                installer.trust_codex_hooks(native_home, native_home / "hooks.json", native_command, verify=native_start, codex_binary=os.environ["CAIRN_CODEX_BINARY"])
            finally:
                if previous_wake is None:
                    os.environ.pop('CAIRN_WAKE_CONTEXT', None)
                else:
                    os.environ['CAIRN_WAKE_CONTEXT'] = previous_wake
            finish_native_wake(wake_id, native_ids[0])
            subprocess.run([sys.executable, str(root / "native-codex-engine/coordination.py"), "watch", "--config-dir", str(root / "native-codex-engine/bindings"), "--once"], check=True, capture_output=True, timeout=15)
            remaining = api_call("bob", "agents", "list", "--harness", "codex")["agents"]
            assert not any(a["native_session_id"] in native_ids for a in remaining), "Exited native Codex stayed live"

            candidate_path = os.environ.get("CAIRN_COMPACT_STAGE")
            if candidate_path:
                candidate_root = Path(candidate_path).resolve()
                source_home = candidate_root / "codex"
                staged_home = root / "native-compact-codex-home"
                shutil.copytree(source_home, staged_home)
                (staged_home / "config.toml").write_text(
                    'model_provider = "fixture"\n'
                    '[model_providers.fixture]\n'
                    'name = "Fixture"\n'
                    'base_url = "http://127.0.0.1:1/v1"\n'
                    'wire_api = "responses"\n'
                    'requires_openai_auth = false\n'
                    'request_max_retries = 0\n'
                    'stream_max_retries = 0\n', encoding="utf-8")
                (staged_home / "config.toml").chmod(0o600)

                def replace_stage_paths(value):
                    if isinstance(value, str):
                        return value.replace(str(source_home), str(staged_home))
                    if isinstance(value, list):
                        return [replace_stage_paths(item) for item in value]
                    if isinstance(value, dict):
                        return {key: replace_stage_paths(item) for key, item in value.items()}
                    return value

                settings_path = staged_home / "hooks.json"
                settings = replace_stage_paths(json.loads(settings_path.read_text(encoding="utf-8")))
                settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
                settings_path.chmod(0o600)
                memory_path = staged_home / "ordinary/config.json"
                memory = json.loads(memory_path.read_text(encoding="utf-8"))
                memory.update(cairn=str(candidate_root / "bin/cairn"),
                              socket=str(root / "api.sock"),
                              token_file=str(root / "carol.token"), repo=repo,
                              state_dir=str(staged_home / "ordinary/state"),
                              inbox_recall_binding=str(staged_home / "binding.json"))
                memory_path.write_text(json.dumps(memory, indent=2) + "\n", encoding="utf-8")
                memory_path.chmod(0o600)
                coordination_path = staged_home / "coordination/bindings/codex-one.json"
                coordination = json.loads(coordination_path.read_text(encoding="utf-8"))
                coordination.update(cairn=str(candidate_root / "bin/cairn"),
                                    socket=str(root / "api.sock"),
                                    token_file=str(root / "carol.token"), repo=repo,
                                    config_home=str(staged_home),
                                    state_dir=str(staged_home / "coordination/state/codex-one"),
                                    inbox_recall_binding=str(staged_home / "binding.json"))
                coordination_path.write_text(json.dumps(coordination, indent=2) + "\n", encoding="utf-8")
                coordination_path.chmod(0o600)
                bridge_path = staged_home / "inbox_recall.py"
                memory_engine = staged_home / "ordinary/memory.py"
                manifest = json.loads((staged_home / "binding.json").read_text(encoding="utf-8"))
                if manifest.get("schema") != "cairn.inbox-recall-binding/2":
                    raise RuntimeError("restage the candidate with a complete lifecycle bundle before native verification")
                manifest.update(enabled=True,
                    bridge=dict(manifest["bridge"], path=str(bridge_path)),
                    engine=dict(manifest["engine"], path=str(memory_engine)),
                    memory_config=dict(path=str(memory_path), sha256=hashlib.sha256(memory_path.read_bytes()).hexdigest()),
                    coordination_configs=[dict(path=str(coordination_path), sha256=hashlib.sha256(coordination_path.read_bytes()).hexdigest())])
                manifest_path = staged_home / "binding.json"
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
                manifest_path.chmod(0o600)
                from codex_loopback_fixture import lifecycle_bundle
                bridge = lifecycle_bundle().Bundle(staged_home).load_pinned(manifest["bridge"], "inbox_recall")
                bridge.validate_binding(memory, require_enabled=True)
                bridge.validate_binding(coordination, require_enabled=True)

                command = next(hook["command"] for groups in settings["hooks"].values()
                               for group in groups for hook in group["hooks"]
                               if "coordination.py" in hook.get("command", "")
                               and "--config" in hook.get("command", ""))
                native_ids = []
                wake_file, wake_id = prepare_native_wake("codex-staged-compact")

                def staged_native_start(rpc):
                    started = rpc("thread/start", dict(cwd=str(root), model="gpt-6-astra",
                        approvalPolicy="never", sandbox="read-only", ephemeral=True))
                    ident = started["thread"]["id"]
                    native_ids.append(ident)
                    rpc("turn/start", dict(threadId=ident,
                        input=[dict(type="text", text="Reply OK", text_elements=[])]))
                    deadline = time.monotonic() + 8
                    while True:
                        entries = api_call("carol", "agents", "list", "--harness", "codex")["agents"]
                        linked = raw_slot("wake-attempts", dict(attempt_id=wake_id))["attempts"][0].get("session")
                        if (any(agent["native_session_id"] == ident for agent in entries)
                                and linked):
                            return
                        assert time.monotonic() < deadline, "Staged compact binding did not register the native Codex first turn"
                        time.sleep(.1)

                previous_wake = os.environ.get("CAIRN_WAKE_CONTEXT")
                os.environ["CAIRN_WAKE_CONTEXT"] = str(wake_file)
                try:
                    installer.trust_codex_hooks(staged_home, settings_path, command,
                        verify=staged_native_start, codex_binary=os.environ["CAIRN_CODEX_BINARY"])
                finally:
                    if previous_wake is None:
                        os.environ.pop("CAIRN_WAKE_CONTEXT", None)
                    else:
                        os.environ["CAIRN_WAKE_CONTEXT"] = previous_wake
                attempt = raw_slot("wake-attempts", dict(attempt_id=wake_id))["attempts"][0]
                agent = next(agent for agent in api_call("carol", "agents", "list", "--harness", "codex", "--include-offline")["agents"]
                             if agent["native_session_id"] == native_ids[0])
                assert attempt.get("session", {}).get("agent_id") == agent["agent_id"]
                assert agent["metadata"]["observed_model"] == "gpt-6-astra"
                raw_slot("wake-change", dict(request_id=str(uuid.uuid4()), attempt_id=wake_id,
                                              operation="finish", reason="fixture_process_stopped"))
                subprocess.run([sys.executable, str(staged_home / "coordination/coordination.py"),
                    "watch", "--config-dir", str(staged_home / "coordination/bindings"), "--once"],
                    check=True, capture_output=True, timeout=15)
                stopped = api_call("carol", "agents", "list", "--agent-id", agent["agent_id"],
                                   "--include-offline")["agents"]
                assert stopped and stopped[0]["stopped"]
                print("Staged compact Codex binding loaded and registered an actual native first turn against the disposable API; fixture provider only")
                from check_codex_compact_native import check as check_codex_compact_native
                for scenario in ("notice", "response", "batch-admission"):
                    check_codex_compact_native(os.environ["CAIRN_CODEX_BINARY"],
                        root / ("native-compact-codex-" + scenario), json.loads(config.read_text()),
                        api_call, candidate_root, scenario=scenario)
                codex_entry = os.environ.get("CAIRN_CODEX_ENTRY")
                codex_root = os.environ.get("CAIRN_CODEX_ROOT")
                if codex_entry and codex_root:
                    from check_codex_full_inbox_native import check as check_codex_full_inbox_native
                    from preflight_token_efficiency_panel import manifest as panel_manifest, read_fixture
                    from token_efficiency_single_workflow import (
                        fixture_manifest as single_workflow_manifest,
                        provider_free_fixture_refusal,
                    )
                    route_config = dict(json.loads(config.read_text()),
                        cairn=str(candidate_root / "bin/cairn"),
                        socket=str(root / "api.sock"), token_file=str(root / "carol.token"))
                    fixture_data, fixture_hash = read_fixture()
                    controller_path = root / "native-single-notice-controller.json"
                    with controller_path.open("x", encoding="utf-8") as stream:
                        json.dump(panel_manifest(fixture_data, fixture_hash), stream,
                                  ensure_ascii=False)
                        stream.write("\n")
                    controller_path.chmod(0o600)
                    workflow_manifest = single_workflow_manifest(
                        Path(__file__).resolve().parents[1] / "trials/token-efficiency/FIXTURE.json",
                        controller_path, case="single_notice", arm="baseline")
                    complete_fixture = check_codex_full_inbox_native(
                        binary=os.environ["CAIRN_CODEX_BINARY"],
                        root=root / "native-full-inbox-codex",
                        config=route_config, api_call=api_call,
                        candidate_root=candidate_root, sandbox_auth_file=None,
                        turn_timeout=120, codex_entry=codex_entry, codex_root=codex_root)
                    complete_refusal = provider_free_fixture_refusal(
                        workflow_manifest, complete_fixture)
                    assert complete_refusal["state"] == "refused"
                    assert complete_refusal["join_eligible"] is False
                    print("Single-notice native fixture bound to workflow gate and refused for live usage:",
                          json.dumps(complete_refusal, sort_keys=True))
                    stall_fixture = check_codex_full_inbox_native(
                        binary=os.environ["CAIRN_CODEX_BINARY"],
                        root=root / "native-full-inbox-codex-stall",
                        config=route_config, api_call=api_call,
                        candidate_root=candidate_root, sandbox_auth_file=None,
                        turn_timeout=2, codex_entry=codex_entry, codex_root=codex_root,
                        mode="stall")
                    stall_refusal = provider_free_fixture_refusal(workflow_manifest, stall_fixture)
                    assert stall_refusal["state"] == "refused"
                    assert stall_refusal["join_eligible"] is False
                    print("Single-notice stalled fixture bound to workflow gate and left unscored:",
                          json.dumps(stall_refusal, sort_keys=True))
            if (not os.environ.get("CAIRN_COMPACT_STAGE") and
                    os.environ.get("CAIRN_CODEX_ENTRY") and os.environ.get("CAIRN_CODEX_ROOT")):
                from check_codex_full_inbox_native import check as check_codex_full_inbox_native
                batch_candidate = root / "native-batch-candidate"
                batch_bin = batch_candidate / "bin"
                batch_bin.mkdir(parents=True, mode=0o700)
                shutil.copyfile(Path(binary).resolve(strict=True), batch_bin / "cairn")
                (batch_bin / "cairn").chmod(0o700)
                route_config = dict(json.loads(config.read_text()),
                    cairn=str(batch_bin / "cairn"), socket=str(root / "api.sock"),
                    token_file=str(root / "carol.token"))
                trace_dir = Path.home() / ".cache/cairn-221-native-batch-actor"
                trace_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                trace_info = trace_dir.lstat()
                assert stat.S_ISDIR(trace_info.st_mode) and trace_info.st_uid == os.getuid()
                assert stat.S_IMODE(trace_info.st_mode) == 0o700, \
                    "native batch actor trace directory must be private"
                trace_path = trace_dir / ("actor-" + str(uuid.uuid4()) + ".jsonl")
                batch_fixture = check_codex_full_inbox_native(
                    binary=os.environ["CAIRN_CODEX_BINARY"],
                    root=root / "native-batch-codex", config=route_config,
                    api_call=api_call, candidate_root=batch_candidate,
                    sandbox_auth_file=None, turn_timeout=120,
                    codex_entry=os.environ["CAIRN_CODEX_ENTRY"],
                    codex_root=os.environ["CAIRN_CODEX_ROOT"],
                    scenario="batch", diagnostic_output=trace_path, api_role="carol")
                assert batch_fixture["result"] == "PASS"
                print("Three-notice native Codex fixture retained sanitized response/tool-result classes at:",
                      str(trace_path))
        print("Native coordination: process ownership, death, resume, fork, model/task context and transcript exclusion passed")
        if os.environ.get("CAIRN_CODEX_BINARY"):
            print("Installed Codex first-turn hooks registered the actual native thread with a fixture-only provider")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        watch()
