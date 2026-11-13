"""Safety boundaries for the separately run, held-runtime continuity prep."""
import contextlib
import io
import http.client
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import check_r03_hermes_continuity as continuity


class ContinuityBoundary(unittest.TestCase):
    def setUp(self):
        cache = Path.home() / '.cache'
        self.temp = tempfile.TemporaryDirectory(prefix='r03-continuity-test-', dir=cache)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_native_phase_refuses_before_invalid_paths_or_any_effect(self):
        output = self.root / 'untouched'
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            status = continuity.main(['--phase', 'native', '--output', str(output),
                '--native-root', '/nonexistent/hermes', '--manifest', '/nonexistent/pins'])
        report = json.loads(stream.getvalue())
        self.assertEqual(status, 75)
        self.assertEqual(report['status'], 'NOTRUN')
        self.assertFalse(report['ac4_closed'])
        self.assertFalse(output.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_public_evidence_ancestor_refused_before_output_creation(self):
        parent = self.root / 'public'
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
        with self.assertRaisesRegex(continuity.PrerequisiteError, '0700'):
            continuity.private_output(self.root, parent / 'case')
        self.assertFalse((parent / 'case').exists())

    def test_symlink_cannot_redirect_application_state(self):
        target = self.root / 'target'
        target.mkdir(mode=0o700)
        link = self.root / 'link'
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'symlink'):
            continuity.private_output(self.root, link / 'case')
        self.assertEqual(list(target.iterdir()), [])

    def test_existing_evidence_is_never_reused_or_erased(self):
        prior = self.root / 'prior'
        prior.mkdir(mode=0o700)
        sentinel = prior / 'failure.json'
        sentinel.write_text('retained')
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'fresh output'):
            continuity.private_output(self.root, prior)
        self.assertEqual(sentinel.read_text(), 'retained')

    def test_wrong_inventory_stops_before_runtime_access(self):
        manifest = self.root / 'pins.json'
        manifest.write_text('{}')
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'held inventory changed'):
            continuity.verify_inputs(self.root / 'absent-runtime', manifest)

    def test_session_api_requires_disposable_home_before_import(self):
        home = self.root / 'home'
        home.mkdir(mode=0o700)
        # The normal host interpreter cannot enter the held API phase merely
        # because a caller supplies a valid-looking application home.
        previous = os.environ.pop('HERMES_HOME', None)
        try:
            with self.assertRaisesRegex(continuity.PrerequisiteError, 'disposable HERMES_HOME'):
                continuity.session_api(home, self.root / 'absent-runtime', self.root / 'report.json')
        finally:
            if previous is not None:
                os.environ['HERMES_HOME'] = previous
        self.assertFalse((home / 'state.db').exists())


class MechanismGuards(unittest.TestCase):
    def admission_case(self):
        # Data-only validator controls. These never reach a native dispatcher
        # or manufacture a real Cairn admission/registration.
        context = dict(event_id='synthetic-event', source=dict(record_id='synthetic-record', version=1),
                       agent_id=continuity.AGENT_ID, execution_id=continuity.EXECUTION_ID,
                       delivery_id='synthetic-delivery')
        event = dict(event_id=context['event_id'], ref=context['source'], kind='request',
                     **{'from': continuity.RELEASE_ROOT},
                     destination=dict(type='agent', name='agent/' + continuity.AGENT_ID),
                     resolution=dict(execution_id=continuity.EXECUTION_ID))
        status = dict(event=event, deliveries=[dict(delivery_id=context['delivery_id'], state='leased',
                                                   consumer='agent/' + continuity.AGENT_ID)])
        bindings = dict(tree='synthetic-tree', provider_url=continuity.PROVIDER_URL)
        admission = dict(schema='cairn.r03-hermes-native-admission/1',
            operation='one-isolated-hermes-continuity', attempt_limit=1, bindings=bindings,
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat())
        return status, admission, context, bindings

    def test_admission_requires_all_live_source_and_binding_controls(self):
        for field in ('publisher', 'lease', 'tree', 'execution', 'expiry'):
            with self.subTest(field=field):
                status, admission, context, bindings = self.admission_case()
                if field == 'publisher': status['event']['from'] = 'agent/foreign'
                if field == 'lease': status['deliveries'][0]['state'] = 'handled'
                if field == 'tree': admission['bindings'] = dict(bindings, tree='wrong-tree')
                if field == 'execution': context['execution_id'] = 'foreign-execution'
                if field == 'expiry': admission['expires_at'] = '2020-01-01T00:00:00Z'
                with self.assertRaises(continuity.PrerequisiteError):
                    continuity.validate_admission(status, admission, context, bindings)

    def test_refresh_rejects_an_open_owner_or_replacement_session(self):
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'idle finalization'):
            continuity.require_idle_boundary(False, 'same', 'same')
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'replacement conversation'):
            continuity.require_idle_boundary(True, 'same', 'different')
        continuity.require_idle_boundary(True, 'same', 'same')

    def test_local_transport_streams_and_rejects_wrong_credentials(self):
        with tempfile.TemporaryDirectory(prefix='r03-transport-', dir=Path.home() / '.cache') as name:
            with continuity.ScriptedChat(Path(name) / 'requests.json', port=0) as transport:
                port = transport.server.server_address[1]
                for key, expected in [('r03-fixture-only', 200), ('unexpected', 400)]:
                    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
                    body = json.dumps(dict(model=continuity.MODEL, stream=True,
                                           messages=[dict(role='user', content='Synthetic transport unit.')]))
                    connection.request('POST', '/v1/chat/completions', body,
                                       {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
                    response = connection.getresponse()
                    self.assertEqual(response.status, expected)
                    raw = response.read()
                    if expected == 200:
                        self.assertIn(b'data: [DONE]', raw)
                        self.assertIn(b'Synthetic continuity response A.', raw)
                    connection.close()
                self.assertEqual(len(transport.requests), 2)
                self.assertEqual(len(transport.violations), 1)

    def test_host_namespace_refused_before_native_images_or_client_import(self):
        with self.assertRaisesRegex(continuity.PrerequisiteError, 'host network namespace'):
            continuity.inside_isolation(dict(host_net_namespace=os.readlink('/proc/self/ns/net')), native=True)

    def test_syscall_audit_distinguishes_absent_nss_from_dns_and_live_nss(self):
        with tempfile.TemporaryDirectory(prefix='r03-audit-', dir=Path.home() / '.cache') as name:
            trace = Path(name) / 'syscalls'
            trace.write_text('10 connect(3, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = -1 ENOENT\n'
                '10 connect(3, {sa_family=AF_UNIX, sun_path="/var/run/nscd/socket"}, 110) = 0\n'
                '10 connect(3, {sa_family=AF_INET, sin_port=htons(53), sin_addr=inet_addr("127.0.0.1")}, 16) = 0\n')
            result = continuity.audit_syscalls(set(), set(), trace_path=trace)
            self.assertEqual(len(result['declared_negative_or_anchor_probes']), 1)
            self.assertEqual(len(result['violations']), 2)

    def test_go_phase_rejects_descendant_python_source_fallback(self):
        with tempfile.TemporaryDirectory(prefix='r03-fallback-', dir=Path.home() / '.cache') as name:
            trace = Path(name) / 'syscalls'
            python = '/opt/assets/held/python/bin/python3.11'
            trace.write_text('100 clone(child_stack=NULL) = 101\n'
                '101 execve("' + python + '", ["python", "memory.py"], 0x0) = 0\n')
            result = continuity.audit_syscalls({python}, set(), {100: 'A'}, trace_path=trace)
            self.assertEqual(len(result['violations']), 1)
            self.assertIn('Source Python fallback', result['violations'][0])


if __name__ == '__main__':
    unittest.main()
