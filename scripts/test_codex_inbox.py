"""Basic inbox caller acceptance and its isolated response-stream import."""
import hashlib
import io
from contextlib import redirect_stdout
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import check_codex_inbox as fixture


def result(data):
    response = dict(schema='cairn.response/1', ok=True, status='OK', data=data)
    return {'output': json.dumps(dict(exit_code=0, output=json.dumps(response)))}


class BasicInboxAcceptanceTests(unittest.TestCase):
    def run_fixture(self, store_state):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / 'fixture'
        context_path = root / 'engine/state/codex-inbox/inbox/attempt.json'
        context = dict(source=dict(record_id='source-1', version=1), event_id='event-1',
                       delivery_id='delivery-1', attempt_id='attempt-1', agent_id='agent-1', execution_id='execution-1',
                       read=['cairn', 'history'], read_input=dict(record_id='source-1', version=1),
                       completion=['cairn', 'complete'])
        body = 'Native Codex selected request source'
        source_result = result(dict(historical=True, record_id='source-1', versions=[dict(
            version=1, payload_available=True, body=body, body_bytes=len(body.encode()),
            body_sha256=hashlib.sha256(body.encode()).hexdigest())]))
        completion = result(dict(delivery_id='delivery-1', event=dict(event_id='event-1'), state='handled', attempts=1))
        observed = []

        class LocalActor:
            def __init__(self, handler, **kwargs):
                self.handler, self.after = handler, kwargs['after_response']
                self.requests, self.failures = [], []
                self.__class__.active = self
            def write_config_toml(self, *args, **kwargs): pass
            def start(self): pass
            def close(self, **kwargs): pass
            def respond(self, request, results):
                self.requests.append(request)
                item = self.handler(request, results)
                self.after(item)
                return item

        def install(engine, _settings, _config):
            context_path.parent.mkdir(parents=True)
            context_path.write_text(json.dumps(context))
            config = engine / 'config.json'
            config.write_text('{}')
            return config

        def rpc(method, _params):
            if method == 'thread/start':
                return dict(thread=dict(id='native-1'))
            self.assertEqual(method, 'turn/start')
            actor = LocalActor.active
            request = dict(input=[dict(type='message', text=str(context_path)),
                dict(type='additional_tools', tools=[dict(name='functions', tools=[dict(name='exec', description='tools.exec_command')])])])
            actor.respond(request, {})  # Initial task publishes the request.
            self.assertEqual(actor.respond(request, {})['call_id'], 'source')
            self.assertEqual(actor.respond(request, dict(source=source_result))['call_id'], 'completion')
            self.assertEqual(actor.respond(request, dict(source=source_result, completion=completion))['type'], 'message')
            return {}

        def api_call(_role, command, *args, **kwargs):
            observed.append(command)
            if command == 'create': return dict(record_id='source-1', version=1)
            if command == 'agents': return dict(agents=[dict(native_session_id='native-1', inbox='agent/bob', metadata=dict(state='busy'))])
            if command == 'publish': return dict(event_id='event-1')
            if command == 'event-status': return dict(deliveries=[store_state])
            if command == 'session-inbox-claim': return dict(attempt=dict(finished_at='synthetic-finished'))
            raise AssertionError('unexpected API command')

        installer = SimpleNamespace(install=install, trust_codex_hooks=lambda *args, verify, **kwargs: verify(rpc),
            engine=SimpleNamespace(load_config=lambda path: {}, watch_once=lambda config: None))
        with patch.object(fixture, 'load_coordination_installer', return_value=installer), \
                patch.object(fixture, 'LoopbackResponsesServer', LocalActor), redirect_stdout(io.StringIO()):
            fixture.check('/unused/native', root, dict(repo='fixture'), api_call)
        return observed

    def test_successful_tool_result_still_requires_independent_store_confirmation(self):
        for state in (dict(delivery_id='delivery-1', state='pending', attempts=0),
                      dict(delivery_id='other', state='handled', attempts=1),
                      dict(delivery_id='delivery-1', state='handled', attempts=2)):
            with self.subTest(state=state), self.assertRaises(AssertionError):
                self.run_fixture(state)
        observed = self.run_fixture(dict(delivery_id='delivery-1', state='handled', attempts=1))
        self.assertIn('event-status', observed)
        self.assertEqual(observed[-1], 'session-inbox-claim')

    def test_response_stream_import_survives_isolated_probe_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'check_codex_inbox.py'
            shutil.copyfile(fixture.__file__, script)
            command = [sys.executable, '-I', '-c',
                "import runpy,sys; m=runpy.run_path(sys.argv[1]); "
                "item={'type':'message','id':'msg','role':'assistant','content':[{'type':'output_text','text':'fixture'}]}; "
                "assert m['_shared_response_stream'] is None; "
                "assert b'response.completed' in m['response_stream']('resp',item)", str(script)]
            completed = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=10)
            self.assertEqual(completed.returncode, 0, completed.stderr)
