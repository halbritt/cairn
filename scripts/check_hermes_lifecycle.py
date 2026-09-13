#!/usr/bin/env python3
"""Native Hermes probe with synthetic model traffic and a disposable PostgreSQL store.

Run with the installed Hermes virtualenv Python. Nothing contacts a messaging service.
"""
import argparse
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_claude_tools import response_events


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--claude', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--semantic-worker', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(mode=0o700)
    logging.basicConfig(filename=output / 'native.log',level=logging.INFO,format='%(name)s %(levelname)s %(message)s')
    root = output / 'store'
    root.mkdir(mode=0o700)
    pg = Path(subprocess.check_output(['pg_config', '--bindir'], text=True).strip())
    subprocess.run([pg / 'initdb', '-D', root / 'data', '--auth-local=trust', '--auth-host=reject', '--no-locale', '-E', 'UTF8'], check=True, capture_output=True)
    socket = root / 'socket'
    socket.mkdir()
    subprocess.run([pg / 'pg_ctl', '-D', root / 'data', '-l', root / 'postgres.log', '-o', f"-F -k {socket} -c listen_addresses=''", '-w', 'start'], check=True, capture_output=True)
    api = None
    server = None
    try:
        subprocess.run([pg / 'createdb', '-h', socket, 'hermes_fixture'], check=True)
        env = dict(os.environ, CAIRN_HOME=str(root), CAIRN_DATABASE_URL=f'host={socket} dbname=hermes_fixture sslmode=disable')
        binary = str(args.binary.resolve())
        subprocess.run([binary, 'migrate'], env=env, check=True, capture_output=True)
        token = secrets.token_urlsafe(32)
        (root / 'hosted-agent.token').write_text(token)
        (root / 'hosted-agent.token').chmod(0o600)
        (root / 'identities.json').write_text(json.dumps([dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            principal='agent:hermes-fixture', repo='fixture:hermes', role='agent', destination='hosted')]))
        (root / 'identities.json').chmod(0o600)
        api_log = (root / 'api.log').open('w')
        api = subprocess.Popen([binary, 'serve', *(['--semantic-stream-command', str(args.semantic_worker.resolve())] if args.semantic_worker else [])], env=env, stdout=api_log, stderr=api_log)
        deadline = time.monotonic() + 10
        while not (root / 'api.sock').exists():
            assert api.poll() is None and time.monotonic() < deadline, 'fixture API not ready'
            time.sleep(.02)
        run_fixture(args, output, root, env)
    finally:
        if api is not None:
            api.terminate()
            api.wait(timeout=10)
            api_log.close()
        subprocess.run([pg / 'pg_ctl', '-D', root / 'data', '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


def run_fixture(args, output, store, operator_env):
    home, work = output / 'hermes', output / 'hermesfixture'
    work.mkdir()
    subprocess.run(['git', 'init', '--quiet', work], check=True)
    home.mkdir()
    native = dict(executable=str(args.binary.resolve()), socket=str(store / 'api.sock'),
                  token_file=str(store / 'hosted-agent.token'), repo='fixture:hermes')
    title = 'Handoff: hermesfixture / PostgreSQL validation'
    seed_body = title + '\nGoal: PostgreSQL validation. State: another agent prepared the disposable tests. Next: continue validation.'
    from check_claude_tools import session as claude_session, value as claude_value
    seed_results = claude_session(str(args.claude.resolve()),native['executable'],store,output/'claude-seed',
        'hermes-seed',[('remember','cairn_remember',dict(request_id=str(uuid.uuid4()),body=seed_body,shareable=True))],
        ['cairn_remember'],repo=native['repo'])
    seed = claude_value(seed_results,'remember')
    installer = load('hermes_installer', ROOT / 'scripts/install-hermes-integration.py')
    memories = home / 'memories'
    memories.mkdir()
    memories.joinpath('MEMORY.md').write_text('NATIVE_MEMORY_CANARY: preserve Hermes native memory.\n')
    memories.joinpath('USER.md').write_text('Synthetic fixture user profile.\n')
    native_memory = {p.name:p.read_bytes() for p in memories.iterdir()}
    home.joinpath('config.yaml').write_text(json.dumps(dict(memory=dict(memory_enabled=True,user_profile_enabled=True),
        agent=dict(tool_use_enforcement=False), compression=dict(enabled=False), display=dict(tool_progress='off'),
        plugins=dict(enabled=[],entries={'fixture-unrelated':dict(settings=dict(preserve=True))}),
        mcp_servers={'fixture-existing':dict(command='/bin/false',enabled=False)},
        model=dict(default='probe',provider='custom'), terminal=dict(backend='local',cwd=str(work)),
        toolsets=['mcp-cairn'])))
    original_config = json.loads(home.joinpath('config.yaml').read_text())
    installer.install(home, native, str(args.claude.resolve()), Path.home() / '.codex-harm/skills/cairn/SKILL.md', 'sonnet')
    preserved = json.loads(home.joinpath('config.yaml').read_text())
    preserved['memory'].pop('provider')
    preserved['mcp_servers'].pop('cairn')
    preserved['plugins']['enabled'].remove('cairn-controls')
    assert preserved==original_config, 'installer changed unrelated settings'
    before = home.joinpath('config.yaml').read_bytes()
    installer.install(home, native, str(args.claude.resolve()), Path.home() / '.codex-harm/skills/cairn/SKILL.md', 'sonnet')
    assert home.joinpath('config.yaml').read_bytes() == before
    requests, selections, errors, explicit_records = [], [], [], []
    interrupt_started, release_interrupt = threading.Event(), threading.Event()

    def tool_result(message):
        content = message['content']
        value = json.JSONDecoder().raw_decode(content[content.index('{'):])[0]
        return json.loads(value['result']) if isinstance(value.get('result'),str) else value

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.connection.settimeout(10)
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if 'messages' not in data:
                self.send_error(404)
                return
            try:
                if self.path.startswith('/v1/messages'):
                    excerpt = json.loads(data['messages'][0]['content'])
                    if 'candidate' in excerpt:
                        names = [t['name'] for t in data.get('tools', [])]
                        tool = next(n for n in names if n.lower() == 'structuredoutput')
                        verdict = 'durable backend' in excerpt.get('request', '').lower()
                        body = response_events('relevance', data['model'], dict(type='tool_use', id='relevance',
                                               name=tool, input=dict(relevant=verdict)))
                    else:
                        selections.append(data)
                        excerpt = json.loads(data['messages'][0]['content'])
                        assert 'Cairn lifecycle memory:' not in json.dumps(excerpt['messages'])
                        assert 'NATIVE_MEMORY_CANARY' not in json.dumps(excerpt['messages'])
                        names = [t['name'] for t in data.get('tools', [])]
                        selection = dict(checkpoint='Goal: PostgreSQL validation. State: Hermes continued the shared handoff. Next: verify gateway continuity.',
                                         workstream='PostgreSQL validation', memories=[])
                        latest = next(m['text'] for m in reversed(excerpt['messages']) if m['role']=='user')
                        if 'SAVE_DECISION' in latest:
                            selection['memories'] = [dict(record_id=None,kind='decision',title='PostgreSQL validation isolation',
                                body='Use disposable PostgreSQL clusters. Source: synthetic Hermes owner instruction.')]
                        if 'OWNER_CORRECTION' in latest:
                            old = next(r for r in excerpt['existing_memories'] if 'PostgreSQL validation isolation' in r['body'])
                            assert 'disposable PostgreSQL clusters' in old['body']
                            selection['memories'] = [dict(record_id=old['record_id'],kind='decision',title='PostgreSQL validation isolation',
                                body='hermesfixture: PostgreSQL validation isolation\n\nUse disposable PostgreSQL 17 clusters. This supersedes the unversioned fixture instruction. Source: synthetic Hermes owner correction.')]
                        if 'after gateway restart' in latest:
                            selection['checkpoint']='Goal: PostgreSQL validation. State: gateway restart verified. Next: return to CLI.'
                        if 'GATEWAY_TO_CLI' in latest:
                            selection['checkpoint']=excerpt['previous_checkpoint'].split('\n',1)[1]
                        if any(marker in latest for marker in ('UNRELATED_WEATHER','NO_MEMORY','SERVICE_DOWN')):
                            selection = dict(checkpoint=None,workstream=None,memories=[])
                        if 'COMPLETE_WORKSTREAM' in latest:
                            selection = dict(checkpoint='All PostgreSQL validation, gateway continuity and deployment checks completed successfully.',
                                             workstream='PostgreSQL validation', checkpoint_state='complete', memories=[])
                        tool = next(n for n in names if n.lower() == 'structuredoutput')
                        body = response_events('selected', data['model'], dict(type='tool_use', id='selected', name=tool, input=selection))
                else:
                    requests.append(data)
                    messages = data['messages']
                    names = [t['function']['name'] for t in data.get('tools', [])]
                    if not names:
                        assert 'Cairn lifecycle memory:' not in json.dumps(messages), 'recall leaked into auxiliary model'
                        answer = 'PostgreSQL validation remains unfinished; next verify gateway continuity.'
                        body = json.dumps(dict(id='aux',object='chat.completion',created=0,model='probe',
                            choices=[dict(index=0,message=dict(role='assistant',content=answer),finish_reason='stop')],
                            usage=dict(prompt_tokens=20,completion_tokens=10,total_tokens=30))).encode()
                        if data.get('stream'):
                            chunk = dict(id='aux',object='chat.completion.chunk',created=0,model='probe',
                                choices=[dict(index=0,delta=dict(role='assistant',content=answer),finish_reason='stop')])
                            body = ('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode()
                        self.send_response(200)
                        self.send_header('Content-Type','text/event-stream' if data.get('stream') else 'application/json')
                        self.send_header('Content-Length',str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    assert 'tool_search' in names and 'tool_call' in names, names
                    blocks = [m['content'] for m in messages if isinstance(m.get('content'), str) and m['content'].startswith('Cairn lifecycle memory:')]
                    if 'INTERRUPT_NATIVE' in str(messages):
                        interrupt_started.set()
                        assert release_interrupt.wait(5), 'fixture interrupt was not delivered'
                    if any(marker in str(messages) for marker in ('UNRELATED_WEATHER','NO_MEMORY','SERVICE_DOWN')):
                        assert not blocks, 'optional context present during empty/disabled/unavailable recall'
                        chunk = dict(id='empty',object='chat.completion.chunk',created=0,model='probe',
                            choices=[dict(index=0,delta=dict(role='assistant',content='Lookup completed; nothing to remember.'),finish_reason='stop')])
                        body = ('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode()
                        self.send_response(200)
                        self.send_header('Content-Type','text/event-stream')
                        self.send_header('Content-Length',str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    assert len(blocks) == 1 and len(blocks[0].encode()) <= 12000, blocks
                    if 'GATEWAY_TO_CLI' in str(messages):
                        assert 'gateway restart verified' in blocks[0], 'gateway checkpoint did not reach CLI'
                    assert seed['record_id'] in blocks[0]
                    turn_start = max(i for i,m in enumerate(messages) if m['role']=='user' and m.get('content') not in blocks)
                    results = [m for m in messages[turn_start:] if m['role'] == 'tool']
                    if not results:
                        delta = dict(role='assistant', tool_calls=[dict(index=0,id='discover',type='function',function=dict(name='tool_search',arguments=json.dumps(dict(query='cairn',limit=8))))])
                        finish = 'tool_calls'
                    elif len(results) < 5:
                        assert 'cairn_pull' in str(results[0]), results[0]
                        if len(results)==1:
                            operation, arguments = 'cairn_search', dict(query='"'+title+'"')
                        elif len(results)==2:
                            view = tool_result(results[-1])
                            entry = next(e for e in view['index'] if e['record_id'] == seed['record_id'])
                            operation, arguments = 'cairn_pull', entry['pull_arguments']
                        elif len(results)==3:
                            assert title in tool_result(results[-1])['selection']['record']['body']
                            operation, arguments = 'cairn_remember', dict(request_id=str(uuid.uuid4()),body='Explicit Hermes fixture: selected reusable guidance.',kind='lesson',shareable=True)
                        else:
                            record = tool_result(results[-1])
                            operation, arguments = 'cairn_edit', dict(request_id=str(uuid.uuid4()),record_id=record['record_id'],expected_version=record['version'],append=' Verified correction through the native tool.')
                        fn = next(n for n in available_tools if n.endswith(operation))
                        delta = dict(role='assistant', tool_calls=[dict(index=0,id='call-'+str(len(results)),type='function',function=dict(name='tool_call',arguments=json.dumps(dict(name=fn,arguments=arguments))))])
                        finish = 'tool_calls'
                    else:
                        created, edited = tool_result(results[-2]), tool_result(results[-1])
                        assert created['record_id']==edited['record_id'] and edited['version']==2, edited
                        explicit_records.append(edited['record_id'])
                        delta, finish = dict(role='assistant',content='Hermes continued PostgreSQL validation. Next: verify gateway continuity.'), 'stop'
                    chunk = dict(id='probe',object='chat.completion.chunk',created=0,model='probe',choices=[dict(index=0,delta=delta,finish_reason=finish)])
                    body = ('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode()
                self.send_response(200)
                self.send_header('Content-Type','text/event-stream')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (AssertionError, KeyError, ValueError, StopIteration) as exc:
                errors.append(repr(exc))
                self.send_error(400)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    endpoint = f'http://127.0.0.1:{server.server_port}/v1'
    config = json.loads(home.joinpath('config.yaml').read_text())
    config['model'].update(base_url=endpoint, api_key='fixture-only')
    config['platform_toolsets'] = {'slack': ['mcp-cairn']}
    config['platforms'] = {'slack': {'enabled': True, 'dm_policy': 'open'}}
    config['approvals'] = {'destructive_slash_confirm': False}
    home.joinpath('config.yaml').write_text(json.dumps(config))
    os.environ.update(HERMES_HOME=str(home),TERMINAL_CWD=str(work),ANTHROPIC_BASE_URL=endpoint.removesuffix('/v1'),
                      ANTHROPIC_API_KEY='fixture-only',OPENAI_API_KEY='fixture-only',OPENAI_BASE_URL=endpoint,
                      CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', CAIRN_DATABASE_URL='host=/absent-hermes-client dbname=denied')
    os.environ['SLACK_ALLOWED_USERS'] = 'fixture-user'
    os.environ.pop('CLAUDECODE',None)
    claude_config = output / 'claude'
    claude_config.mkdir()
    os.environ['CLAUDE_CONFIG_DIR'] = str(claude_config)
    sys.path.insert(0,str(args.hermes_root.resolve()))
    thread.start()
    try:
        from run_agent import AIAgent
        from tools.mcp_tool import discover_mcp_tools, shutdown_mcp_servers
        available_tools = discover_mcp_tools()
        assert available_tools, 'native MCP discovery returned no tools'
        for platform in ('cli','slack'):
            agent = AIAgent(api_key='fixture-only',base_url=endpoint,provider='custom',api_mode='chat_completions',model='probe',
                platform=platform,session_id=platform+'-fixture',quiet_mode=True,enabled_toolsets=['mcp-cairn'],
                skip_context_files=True,skip_background_review=True)
            history = [dict(role=role, content=f'Synthetic PostgreSQL validation step {i}: preserve disposable test isolation. ' * 8)
                       for i in range(18) for role in ('user', 'assistant')]
            prompt = 'Continue PostgreSQL validation. SAVE_DECISION: choose disposable PostgreSQL.' if platform=='cli' else 'continue'
            result = agent.run_conversation(prompt, conversation_history=history)
            assert result['completed'] and not result['failed'], result
            assert all('Cairn lifecycle memory:' not in str(m) for m in result['messages']), result['messages']
            count = len(selections)
            assert count, 'completed-turn capture did not run'
            compacted, _ = agent._compress_context(result['messages'], agent._build_system_prompt(''), approx_tokens=100000, force=True)
            assert agent.context_compressor.compression_count > 0, 'native compaction did not run'
            assert not agent.context_compressor._last_summary_error, 'native summary failed'
            assert len(selections) == count, 'unchanged pre-compaction selected twice'
            agent.shutdown_memory_provider(compacted)
            assert len(selections) == count, 'unchanged exit selected twice'
            print(platform, 'native recall, pull, completed-turn capture and unchanged exit pass')
        assert not errors, errors
        from cli import HermesCLI
        cli = HermesCLI(model='probe',provider='custom',api_key='fixture-only',base_url=endpoint,
                        toolsets=['mcp-cairn'],compact=True)
        assert cli.chat('continue'), 'CLI chat failed'
        previous_id = cli.session_id
        cli.new_session(silent=True)
        assert cli.session_id != previous_id, 'CLI /new did not rotate'
        assert cli.chat('Continue PostgreSQL validation after new session.'), 'new CLI turn failed'
        cli._handle_resume_command('/resume ' + previous_id)
        assert cli.session_id == previous_id, 'CLI /resume did not restore'
        assert cli.chat('Continue PostgreSQL validation. OWNER_CORRECTION: use PostgreSQL 17 fixtures.'), 'resumed CLI turn failed'
        count = len(selections)
        cli.process_command('/compact')
        assert cli.agent.context_compressor.compression_count > 0, 'CLI /compact did not run'
        assert len(selections)==count, 'unchanged CLI /compact selected again'
        cli.agent.shutdown_memory_provider(cli.conversation_history)
        print('Native HermesCLI chat, new and resume pass')
        asyncio.run(gateway_fixture(requests, selections, errors))
        assert discover_mcp_tools()
        after_gateway = HermesCLI(model='probe',provider='custom',api_key='fixture-only',base_url=endpoint,toolsets=['mcp-cairn'],compact=True)
        assert after_gateway.chat('Continue PostgreSQL validation GATEWAY_TO_CLI.'), 'gateway-to-CLI continuation failed'
        after_gateway.agent.shutdown_memory_provider(after_gateway.conversation_history)
        from test_claude_lifecycle import hook
        engine_config = json.loads(home.joinpath('cairn/engine.json').read_text())
        other_agent = hook.Memory(dict(engine_config,harness='opencode'),'ses_HermesVerification')
        checkpoint = other_agent.checkpoint(title)
        decision = other_agent.checkpoint('hermesfixture: PostgreSQL validation isolation')
        assert checkpoint['record_id']==seed['record_id'] and checkpoint['version']==3 and 'gateway restart verified' in checkpoint['body'], checkpoint
        assert decision['kind']=='decision' and decision['version']==2 and 'PostgreSQL 17' in decision['body'], decision
        assert decision['record_id'] != checkpoint['record_id']
        from check_claude_tools import session as claude_session, value as claude_value
        def pull_correction(results):
            index = claude_value(results,'search')['index']
            return next(e['pull_arguments'] for e in index if e['record_id']==decision['record_id'])
        cross = claude_session(str(args.claude.resolve()),native['executable'],store,output / 'claude-continuity',
            'hermes-correction', [('search','cairn_search',dict(query='"hermesfixture: PostgreSQL validation isolation"')),
                                  ('pull','cairn_pull',pull_correction)], ['cairn_search','cairn_pull'], repo=native['repo'])
        assert claude_value(cross,'pull')['selection']['record']['body']==decision['body']
        print('Native Claude searched and pulled Hermes owner correction at the current version')
        output.joinpath('verified-records.json').write_text(json.dumps(dict(checkpoint=checkpoint,decision=decision)))
        print('Separate durable decision, same-record owner correction and other-agent scoped retrieval pass')
        engine_path = home / 'cairn/engine.json'
        original_engine = engine_path.read_text()
        for marker in ('UNRELATED_WEATHER','NO_MEMORY_DISABLED','NO_MEMORY_FILE','SERVICE_DOWN'):
            if marker=='NO_MEMORY_DISABLED':
                os.environ['CAIRN_LIFECYCLE_DISABLED']='1'
            if marker=='NO_MEMORY_FILE':
                (work / '.cairn-no-memory').touch()
            if marker=='SERVICE_DOWN':
                engine_path.write_text(json.dumps(dict(engine_config,socket=str(output / 'absent.sock'))))
            try:
                agent = AIAgent(api_key='fixture-only',base_url=endpoint,provider='custom',api_mode='chat_completions',model='probe',
                    platform='slack',session_id=marker,quiet_mode=True,enabled_toolsets=['mcp-cairn'],skip_context_files=True,skip_background_review=True)
                count = len(selections)
                result = agent.run_conversation(marker)
                assert result['completed'] and not result['failed'], result
                agent.shutdown_memory_provider(result['messages'])
                assert len(selections)==count+(1 if marker=='UNRELATED_WEATHER' else 0), marker
            finally:
                os.environ.pop('CAIRN_LIFECYCLE_DISABLED',None)
                (work / '.cairn-no-memory').unlink(missing_ok=True)
                engine_path.write_text(original_engine)
        assert not errors, errors
        print('Unrelated recall, environment/file opt-out and unavailable-service task continuation pass')
        profile_fixture(home,output,native,installer,args,work)
        retry_fixture(home, work)
        semantic_fixture(home, work, bool(args.semantic_worker))
        checkpoint_fixture(home, work)
        from concurrent.futures import ThreadPoolExecutor
        agent = AIAgent(api_key='fixture-only',base_url=endpoint,provider='custom',api_mode='chat_completions',model='probe',
            platform='slack',session_id='interrupted-native',quiet_mode=True,enabled_toolsets=['mcp-cairn'],skip_context_files=True,skip_background_review=True)
        count = len(selections)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(agent.run_conversation,'NO_MEMORY_INTERRUPT_NATIVE')
            assert interrupt_started.wait(10), 'native interrupt request did not start'
            agent.interrupt(hard_cancel=True)
            release_interrupt.set()
            result = future.result(timeout=20)
        assert result.get('interrupted'), result
        assert len(selections)==count, 'interrupted turn ran completed-turn selection'
        agent.shutdown_memory_provider(result['messages'])
        assert len(selections)==count+1, 'interrupted original turn was not checkpointed at shutdown'
        print('Native in-flight interruption and pending-turn checkpoint pass')
        timeout_fixture(AIAgent,endpoint,engine_path,original_engine,output,selections)
        assert not errors, errors
        assert all((memories/name).read_bytes()==body for name,body in native_memory.items()), 'native memory changed'
        assert any('NATIVE_MEMORY_CANARY' in str(r['messages']) for r in requests), 'native memory was not available alongside Cairn'
        shutdown_mcp_servers()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        output.joinpath('requests.json').write_text(json.dumps(requests))
        output.joinpath('selections.json').write_text(json.dumps(selections))
        output.joinpath('errors.json').write_text(json.dumps(errors))
        output.joinpath('explicit-records.json').write_text(json.dumps(explicit_records))


def profile_fixture(home,output,native,installer,args,work):
    from hermes_constants import set_hermes_home_override, reset_hermes_home_override
    from plugins.memory import load_memory_provider
    from hermes_cli.lifecycle import invoke_hook
    from hermes_cli.middleware import apply_llm_request_middleware
    second = output / 'other-profile'
    installer.install(second,native,str(args.claude.resolve()),Path.home()/'.codex-harm/skills/cairn/SKILL.md','sonnet')
    providers=[]
    controls = load('native_profile_controls',ROOT/'integrations/hermes/controls.py')
    unrelated = output/'unrelated-terminal'
    unrelated.mkdir()
    previous_cwd = os.environ['TERMINAL_CWD']
    os.environ['TERMINAL_CWD'] = str(unrelated)
    try:
        for path in (home,second):
            token = set_hermes_home_override(path)
            try:
                controls.set_context(path,'gateway/same-native-label',[str(work),'PostgreSQL validation'])
                provider = load_memory_provider('cairn')
                provider.initialize('same-native-label',hermes_home=str(path),platform='slack')
                providers.append(provider)
                assert Path(provider.cwd()) == unrelated, 'native cwd override was not exercised'
                invoke_hook('pre_llm_call',session_id='same-native-label',turn_id=str(path),user_message='Continue PostgreSQL validation.')
            finally:
                reset_hermes_home_override(token)
        for path in (home,second):
            token = set_hermes_home_override(path)
            try:
                result = apply_llm_request_middleware(dict(messages=[dict(role='user',content='continue')]),session_id='same-native-label')
                assert len(result.payload['messages'])==2, 'profile contexts mixed'
            finally:
                reset_hermes_home_override(token)
        assert providers[0].config['engine_config'] != providers[1].config['engine_config']
    finally:
        os.environ['TERMINAL_CWD'] = previous_cwd
        for provider in providers:
            provider.shutdown()
    print('Native context-local profiles isolate labels; explicit project recall works from an unrelated terminal directory')


def checkpoint_fixture(home, work):
    engine = load('native_checkpoint_engine', ROOT/'integrations/lifecycle/memory.py')
    config = json.loads((home/'cairn/engine.json').read_text())
    memory = engine.Memory(config, 'completion-native')
    title = 'Handoff: hermesfixture / PostgreSQL validation'
    old = memory.checkpoint(title)
    event = dict(hook_event_name='SessionEnd', session_id='completion-native', cwd=str(work),
                 workstream='PostgreSQL validation', messages=[
                     dict(role='user', text='COMPLETE_WORKSTREAM: All PostgreSQL validation, gateway continuity and deployment checks are finished.'),
                     dict(role='assistant', text='The entire workstream is complete.')])
    engine.capture(memory, event, dict(workstream=title))
    current = memory.checkpoint(title)
    assert current['record_id'] == old['record_id'] and current['version'] == old['version']+1
    assert 'Status: complete' in current['body'] and 'Next: return to CLI' not in current['body'], current
    historical = memory.call('history', payload=dict(record_id=old['record_id'], version=old['version']))
    assert historical['versions'][0]['body'] == old['body'], historical
    assert len(current['body'].encode()) <= engine.CHECKPOINT_BYTES + 160
    print('Native completed checkpoint replaces stale progress at the same record ID and retains exact prior history')


def semantic_fixture(home, work, configured):
    engine = load('native_semantic_engine', ROOT/'integrations/lifecycle/memory.py')
    config = json.loads((home/'cairn/engine.json').read_text())
    memory = engine.Memory(config, 'semantic-native')
    event = dict(hook_event_name='UserPromptSubmit', session_id='semantic-native',
                 cwd=str(work), prompt='Which durable backend is used here?')
    state = {}
    result = engine.recall(memory, event, state)
    if configured:
        assert state['last_recall']['discovery'] == 'verified', state
        assert 'PostgreSQL' in result['hookSpecificOutput']['additionalContext'], result
        assert len(result['hookSpecificOutput']['additionalContext'].encode()) <= 12000
        state = {}
        result = engine.recall(memory, dict(event, prompt='Which tropical fruit tastes sweetest?'), state)
        assert state['last_recall']['discovery'] == 'not_relevant' and not result, state
    else:
        assert state['last_recall']['discovery'] == 'unavailable' and not result, state
    print('Native semantic fallback verifies paraphrase/full-body path and unrelated refusal' if configured
          else 'Native unconfigured semantic worker returns labelled lexical fallback')


def retry_fixture(home, work):
    from hermes_state import SessionDB
    from hermes_cli.plugins import get_plugin_command_handler
    from hermes_cli.lifecycle import invoke_hook
    controls = load('native_retry_controls', ROOT/'integrations/hermes/controls.py')
    session = 'cold-retry-snapshot'
    messages = [dict(role='user', text='Thanks.'), dict(role='assistant', text="You're welcome.")]
    db = SessionDB()
    try:
        db.create_session(session, 'slack')
        for message in messages:
            db.append_message(session, message['role'], message['text'])
    finally:
        db.close()
    key = 'gateway/cold-retry-thread'
    with controls.change_control(home, key) as record:
        record.update(pending=True, session_id=session, platform='slack', cwd=str(work),
                      pending_digest=controls.dialogue_digest(messages))
    handler = get_plugin_command_handler('cairn')
    assert handler is not None, 'native command registration missing'
    invoke_hook('pre_command', command='cairn', surface='gateway', session_key='cold-retry-thread')
    status = handler('retry')
    assert 'Pending: no.' in status and 'courtesy' in status, status
    with controls.change_control(home, key) as record:
        record.update(pending=True, pending_digest='different-snapshot')
    invoke_hook('pre_command', command='cairn', surface='gateway', session_key='cold-retry-thread')
    assert 'exact pending turn' in handler('retry'), 'changed history was accepted'
    assert controls.read_control(home, key)['pending']
    # CLI plugin dispatch does not emit pre_command; process identity is its key.
    assert 'Recall:' in handler('status'), 'CLI status command unavailable'
    print('Native CLI status and cold gateway retry verify bounded exact-history recovery and mismatch refusal')


def timeout_fixture(AIAgent,endpoint,engine_path,original_engine,output,selections):
    from concurrent.futures import ThreadPoolExecutor
    slow = output / 'slow-selector'
    slow.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n')
    slow.chmod(0o700)
    engine_path.write_text(json.dumps(dict(json.loads(original_engine),claude=str(slow))))
    count = len(selections)
    def turn(platform):
        agent = AIAgent(api_key='fixture-only',base_url=endpoint,provider='custom',api_mode='chat_completions',model='probe',
            platform=platform,session_id=platform+'-timeout',quiet_mode=True,enabled_toolsets=['mcp-cairn'],skip_context_files=True,skip_background_review=True)
        started=time.monotonic()
        result=agent.run_conversation('NO_MEMORY_CAPTURE_TIMEOUT')
        assert result['completed'] and not result['failed'], result
        assert 35 <= time.monotonic()-started < 50, 'selector timeout was not bounded'
        return agent,result
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(turn,('cli','slack')))
        assert len(selections)==count, 'hanging selector should not reach the model endpoint'
    finally:
        engine_path.write_text(original_engine)
    for agent,result in results:
        agent.shutdown_memory_provider(result['messages'])
    assert len(selections)==count+2, 'failed captures were not retryable after selector recovery'
    print('CLI/gateway selector timeouts preserve task completion and retry after recovery')


async def gateway_fixture(requests, selections, errors):
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    from gateway.run import GatewayRunner
    from gateway.config import GatewayConfig, PlatformConfig, Platform, SessionResetPolicy
    from gateway.session import SessionSource
    from gateway.platforms.base import BasePlatformAdapter, MessageEvent, SendResult
    from plugins.platforms.slack.adapter import SlackAdapter

    class Transport(BasePlatformAdapter):
        async def connect(self, **kwargs):
            return True
        async def disconnect(self):
            pass
        async def send(self, chat_id, content, **kwargs):
            deliveries.append(dict(chat_id=chat_id, content=content))
            return SendResult(success=True, message_id=str(len(deliveries)))
        async def get_chat_info(self, chat_id):
            return dict(name='Fixture conversation', type='dm')

    deliveries = []
    platform_config = PlatformConfig(enabled=True)
    gateway_config = GatewayConfig(platforms={Platform.SLACK: platform_config},
                                   default_reset_policy=SessionResetPolicy(mode='idle',idle_minutes=1))
    runner = GatewayRunner(gateway_config)
    transport = Transport(platform_config, Platform.SLACK)
    runner.adapters[Platform.SLACK] = transport
    sources = []
    async def dispatch(event):
        sources.append(event.source)
        return await runner._handle_message(event)
    transport.set_message_handler(dispatch)
    class SlackClient:
        async def users_info(self, **kwargs):
            return dict(ok=True,user=dict(name='Fixture owner',real_name='Fixture owner',is_bot=False))
        async def conversations_info(self, **kwargs):
            return dict(ok=True,channel=dict(name='Fixture DM',is_im=True,user='fixture-user'))
        async def conversations_replies(self, **kwargs):
            return dict(ok=True,messages=[])
        async def api_call(self, *args, **kwargs):
            return dict(ok=True)

    slack = SlackAdapter(platform_config)
    slack._app = SimpleNamespace(client=SlackClient())
    slack.handle_message = transport.handle_message
    async def drain():
        tasks = list(transport._session_tasks.values())
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks),120)

    threads = {}
    def raw(text, channel='D-FIXTURE'):
        threads.setdefault(channel,str(time.time()))
        return dict(type='message',channel_type='im',channel=channel,team='T-FIXTURE',user='fixture-user',
                    ts=str(time.time()),thread_ts=threads[channel],client_msg_id=str(uuid.uuid4()),text=text)

    from hermes_constants import get_hermes_home
    project = Path(os.environ['TERMINAL_CWD'])
    await slack._handle_slack_message(raw('!cairn context '+str(project)+' PostgreSQL validation'))
    await drain()
    assert 'Cairn project:' in deliveries[-1]['content'], 'cold gateway context command failed'
    event = raw('continue')
    before = len(selections)
    await slack._handle_slack_message(event)
    await drain()
    source = sources[0]
    assert source.scope_id == 'T-FIXTURE' and source.user_id == 'fixture-user'
    assert source.thread_id == threads['D-FIXTURE'], source
    assert not errors, errors
    assert len(selections) > before and deliveries, (len(selections),before,deliveries)
    counts = (len(requests),len(selections),len(deliveries))
    await slack._handle_slack_message(event)
    await drain()
    assert counts == (len(requests),len(selections),len(deliveries)), 'duplicate Slack event ran twice'
    await slack._handle_slack_message(raw('Continue PostgreSQL validation before manual compression.'))
    await drain()
    before_compress = len(selections)
    await slack._handle_slack_message(raw('!compress'))
    await drain()
    assert len(selections)==before_compress, 'unchanged manual gateway compression selected again'
    assert any('compress' in d['content'].lower() or 'compact' in d['content'].lower() for d in deliveries[-2:]), deliveries[-2:]
    from agent.context_compressor import is_compaction_summary_message
    compressed_entry = runner.session_store.get_or_create_session(source)
    assert any(is_compaction_summary_message(m) for m in runner.session_store.load_transcript(compressed_entry.session_id)), 'manual gateway compression did not persist its summary'
    await asyncio.gather(slack._handle_slack_message(raw('Continue PostgreSQL validation ALPHA.', 'D-ALPHA')),
                         slack._handle_slack_message(raw('Continue PostgreSQL validation BRAVO.', 'D-BRAVO')))
    await drain()
    recent = [json.loads(s['messages'][0]['content'])['messages'] for s in selections[before:]]
    assert any('ALPHA' in str(m) for m in recent) and any('BRAVO' in str(m) for m in recent)
    assert not any('ALPHA' in str(m) and 'BRAVO' in str(m) for m in recent), 'conversation capture crossed routing keys'
    entry = runner.session_store.get_or_create_session(source)
    previous_id = entry.session_id
    await slack._handle_slack_message(raw('!new'))
    await drain()
    entry = runner.session_store.get_or_create_session(source)
    assert entry.session_id != previous_id, 'gateway reset did not rotate'
    await slack._handle_slack_message(raw('Continue PostgreSQL validation after reset.'))
    await drain()
    previous_id = entry.session_id
    entry.updated_at = datetime.now() - timedelta(days=3)
    runner.session_store._save()
    await slack._handle_slack_message(raw('Continue PostgreSQL validation after expiry.'))
    await drain()
    assert runner.session_store.get_or_create_session(source).session_id != previous_id, 'idle expiry did not rotate'
    assert not errors, errors
    print('Slack ingress, deduplication, concurrent conversations, reset and idle expiry pass')
    await transport.disconnect()
    await transport.connect(is_reconnect=True)
    counts = (len(requests),len(selections),len(deliveries))
    await slack._handle_slack_message(event)
    await drain()
    assert counts == (len(requests),len(selections),len(deliveries)), 'redelivery after transport reconnect ran twice'
    await asyncio.wait_for(runner.stop(),15)
    from tools.mcp_tool import discover_mcp_tools
    await asyncio.to_thread(discover_mcp_tools)
    runner = GatewayRunner(gateway_config)
    runner.adapters[Platform.SLACK] = transport
    await slack._handle_slack_message(raw('Continue PostgreSQL validation after gateway restart.'))
    await drain()
    assert not errors, errors
    assert 'Hermes continued PostgreSQL validation' in deliveries[-1]['content'], deliveries[-1]
    before_controls = (len(requests),len(selections))
    await slack._handle_slack_message(raw('!cairn status'))
    await drain()
    assert 'Recall:' in deliveries[-1]['content'] and 'Pending:' in deliveries[-1]['content']
    for channel, topic in [('D-ALPHA','Alpha work'),('D-BRAVO','Bravo work')]:
        await slack._handle_slack_message(raw('!cairn context '+str(project)+' '+topic,channel))
        await drain()
        assert 'Workstream: '+topic in deliveries[-1]['content']
    await slack._handle_slack_message(raw('!cairn context clear','D-ALPHA'))
    await drain()
    assert 'automatic project' in deliveries[-1]['content']
    await slack._handle_slack_message(raw('!cairn context','D-BRAVO'))
    await drain()
    assert 'Workstream: Bravo work' in deliveries[-1]['content'], 'thread binding crossed conversation keys'
    assert before_controls == (len(requests),len(selections)), 'context controls invoked a model'
    await asyncio.wait_for(runner.stop(),15)
    records = [json.loads(p.read_text()) for p in (get_hermes_home()/'cairn/conversations').glob('*.json')]
    assert any(r.get('binding',{}).get('workstream')=='PostgreSQL validation' for r in records if r.get('binding')), 'thread binding did not persist through restart'
    print('Gateway transport reconnect, graceful shutdown, restart continuation and persistent thread context pass')


if __name__ == '__main__':
    main()
