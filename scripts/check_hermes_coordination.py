"""Installed Hermes lifecycle and middleware against local fixture provider/API."""
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading
import uuid

from check_native_inbox import inbox_tool_step, chat_stream

root, hermes_root, config_path = map(Path, sys.argv[1:])
root.mkdir(mode=0o700)
home = root / 'home'
home.mkdir()
work = root / 'work'
work.mkdir()
source = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('coordination_installer', source / 'scripts/install-agent-coordination.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
config = dict(json.loads(config_path.read_text()), harness='hermes', binding='hermes-native', process_names=[])
installer.install(root / 'engine', home, config)
requests = []
failures = []
active_event = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path != '/v1/chat/completions':
            # Hermes also probes Ollama model capabilities at /api/show.
            self.send_response(404)
            self.end_headers()
            return
        requests.append(request)
        if 'Cairn session agent-' not in json.dumps(request.get('messages', [])):
            failures.append('native request lacks session identity context')
        try:
            delta, finish = dict(role='assistant', content='COORDINATION_OK'), 'stop'
            if active_event:
                path = next(p for p in (root / 'engine/state/hermes-native/inbox').glob('*.json')
                            if json.loads(p.read_text())['event_id'] == active_event['event_id'])
                assert str(path) in json.dumps(request['messages'])
                delta, finish = inbox_tool_step(request, json.loads(path.read_text()), 'Native Hermes selected request', 'terminal')
            body = chat_stream(delta, finish)
        except (AssertionError, ValueError, KeyError, StopIteration) as exc:
            failures.append(repr(exc))
            self.send_error(500)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
endpoint = f'http://127.0.0.1:{server.server_port}/v1'
os.environ.update(HERMES_HOME=str(home), TERMINAL_CWD=str(work), OPENAI_API_KEY='fixture-only', OPENAI_BASE_URL=endpoint,
                  CAIRN_DATABASE_URL='host=/absent-hermes-coordination-db dbname=denied')
for key in ('CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD', 'CAIRN_WAKE_CONTEXT'):
    os.environ.pop(key, None)
sys.path.insert(0, str(hermes_root))
thread.start()
try:
    from run_agent import AIAgent
    from hermes_cli.plugins import get_plugin_manager
    for platform in ('cli', 'slack'):
        ident = platform + '-coordination-fixture'
        agent = AIAgent(api_key='fixture-only', base_url=endpoint, provider='custom', api_mode='chat_completions', model='probe',
                       platform=platform, session_id=ident, quiet_mode=True, enabled_toolsets=['terminal'],
                       skip_context_files=True, skip_background_review=True)
        result = agent.run_conversation('Reply COORDINATION_OK')
        assert result['completed'] and not result['failed']
        assert 'Cairn session agent-' not in json.dumps(result['messages']), 'identity context persisted in conversation history'
        page = installer.engine.call(config, 'agent-directory', dict(repo=config['repo'], harness='hermes', include_offline=True))
        entry = next(a for a in page['agents'] if a['native_session_id'] == ident)
        assert entry['metadata']['observed_model'] == 'probe'
        assert entry['online'] == (platform == 'cli'), 'gateway turn falsely remained reachable after its agent finished'
        selected = installer.engine.call(config, 'create', dict(request_id=str(uuid.uuid4()), draft=dict(
            kind='note', body='Native Hermes selected request', sensitivity='shareable', claim_type='self',
            scope=dict(repo=config['repo'], task_id='*', run_id='*'))))
        active_event = installer.engine.call(config, 'event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
            ref=dict(record_id=selected['record_id'], version=1), destination=dict(type='agent', name=entry['inbox'])))
        handled = agent.run_conversation('Handle the queued Cairn inbox request.', conversation_history=result['messages'])
        assert handled['completed'] and not handled['failed']
        status = installer.engine.call(config, 'event-inspect', dict(event_id=active_event['event_id']))
        assert status['deliveries'][0]['state'] == 'handled'
        assert 'Cairn session agent-' not in json.dumps(handled['messages'])
        active_event = None
    assert len(requests) >= 8 and not failures, failures
    get_plugin_manager().unload('cairn-coordination')
    assert not installer.engine.call(config, 'agent-directory', dict(repo=config['repo'], harness='hermes'))['agents']
    print('Installed Hermes CLI/gateway native turns receive inbox context, read source and complete through native terminal')
finally:
    server.shutdown()
    server.server_close()
