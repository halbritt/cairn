"""Exercise native hook/process association through the real disposable Unix API."""
import json
import importlib.util
import shlex
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time
import uuid


OWNER = r'''
import json, subprocess, sys
script, config = sys.argv[1:]
for line in sys.stdin:
    result = subprocess.run([sys.executable, script, 'hook', '--config', config],
        input=line, text=True, capture_output=True, timeout=15)
    print(json.dumps(dict(code=result.returncode, stdout=result.stdout, stderr=result.stderr)), flush=True)
'''


def check(binary, root, repo, api_call):
    assert os.environ.get("CAIRN_TEST_DATABASE_URL")
    script = Path(__file__).resolve().parents[1] / "integrations/lifecycle/coordination.py"
    configs = root / "native-bindings"
    configs.mkdir(mode=0o700)
    config = configs / "codex-probe.json"
    state = root / "native-state"
    config.write_text(json.dumps(dict(cairn=str(Path(binary).resolve()), socket=str(root / "api.sock"),
        token_file=str(root / "bob.token"), repo=repo, harness="codex", binding="codex-probe",
        model="configured", process_names=["python3"], state_dir=str(state), native_delivery=True)))
    config.chmod(0o600)
    processes = []

    def owner(env=None):
        p = subprocess.Popen([sys.executable, "-u", "-c", OWNER, str(script), str(config)],
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
        second = owner()
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
        print("Native coordination: process ownership, death, resume, fork, model/task context and transcript exclusion passed")
        if os.environ.get("CAIRN_CODEX_BINARY"):
            print("Installed Codex first-turn hooks registered the actual native thread with a fixture-only provider")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)
        watch()
