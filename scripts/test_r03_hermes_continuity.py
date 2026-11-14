"""Safety boundaries for the separately run, held-runtime continuity prep."""
import contextlib
import io
import http.client
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
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
            operation='one-direct-host-hermes-continuity', attempt_limit=1, bindings=bindings,
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

    def test_foreign_namespace_refused_before_native_images_or_client_import(self):
        plan = dict(normal_home=os.environ['HOME'],normal_path=os.environ['PATH'],
                    host_net_namespace='foreign',host_pid_namespace=os.readlink('/proc/self/ns/pid'))
        with self.assertRaisesRegex(continuity.PrerequisiteError,'foreign namespace'):
            continuity.require_host_view(plan)


    def test_private_native_workers_are_not_cli_phase_shortcuts(self):
        for phase in ('native-worker', 'isolated-native', 'isolated-setup'):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                continuity.main(['--phase', phase, '--plan', '/nonexistent/forged-grant'])
            self.assertEqual(error.exception.code, 2)

    def test_host_identity_mismatch_refused_before_any_native_path_access(self):
        with self.assertRaisesRegex(continuity.PrerequisiteError,'normal HOME/PATH'):
            continuity.require_host_view(dict(normal_home='/wrong',normal_path=os.environ['PATH']))

    def test_shell_environment_chain_is_not_a_native_execution_capability(self):
        with tempfile.TemporaryDirectory(prefix='r03-exec-',dir=Path.home()/'.cache') as name:
            executable = str(Path('/usr/bin/env').resolve())
            spec = dict(exec_pins={executable:continuity.digest(executable)},exact_argv=[],git_executable='/unselected/git')
            observer = continuity.NativeAudit(Path(name),spec)
            with self.assertRaisesRegex(continuity.PrerequisiteError,'arguments outside'):
                observer.audit('subprocess.Popen',(executable,[executable,'claude','unadmitted'],None,None))
            self.assertNotIn('unadmitted',(Path(name)/'audit.json').read_text())

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
            python = '/fixture/held/python'
            trace.write_text('100 clone(child_stack=NULL) = 101\n'
                '101 execve("' + python + '", ["python", "memory.py"], 0x0) = 0\n')
            result = continuity.audit_syscalls({python}, set(), {100: 'A'}, trace_path=trace, python_path=python,
                scratch_root='/fixture')
            self.assertEqual(len(result['violations']), 1)
            self.assertIn('Source Python fallback', result['violations'][0])


class EvidencePrivacy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='r03-privacy-',dir=Path.home()/'.cache')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def assert_private(self, canary, value):
        self.assertTrue(canary not in value, 'unvalidated payload entered retained evidence')

    def test_only_selected_receipts_survive_token_identity_and_unknown_files(self):
        source, target = self.root/'scratch', self.root/'retained'
        (source/'api').mkdir(parents=True,mode=0o700)
        (source/'A').mkdir(mode=0o700)
        canary = secrets.token_hex(24)
        for relative in ('api/worker.token','api/identities.json','unknown-future-file','A/unknown-secret'):
            (source/relative).write_text(canary)
        bodies = b'Synthetic baseline retention policy.\n'
        stdin = b'{"argv":["/selected/hook"],"payload":{"hook_event_name":"SessionEnd","stdin":"Selected synthetic body.\\n"}}\n'
        (source/'stored-bodies.txt').write_bytes(bodies)
        (source/'A/calls.json').write_bytes(stdin)
        continuity.retain_evidence(source,target)
        self.assertEqual((target/'stored-bodies.txt').read_bytes(),bodies)
        self.assertEqual((target/'A/calls.json').read_bytes(),stdin)
        metadata = json.loads((target/'filesystem-before-cleanup.json').read_bytes())
        for relative in ('api/worker.token','api/identities.json','unknown-future-file','A/unknown-secret'):
            self.assertFalse((target/relative).exists())
            self.assertEqual(metadata[relative]['sha256'],continuity.digest(source/relative))
            self.assertFalse(metadata[relative]['content_selected'])
        for path in target.rglob('*'):
            if path.is_file(): self.assert_private(canary,path.read_text())

    def test_credential_in_selected_schema_fails_without_copying_or_echoing_it(self):
        source, target = self.root/'scratch', self.root/'retained'
        source.mkdir(mode=0o700); canary = secrets.token_hex(24)
        (source/'result.json').write_text(json.dumps(dict(api_key=canary)))
        with self.assertRaises(continuity.PrerequisiteError) as error:
            continuity.retain_evidence(source,target)
        self.assert_private(canary,str(error.exception))
        self.assertFalse((target/'result.json').exists())

    def test_pipe_reduces_argv_without_losing_exec_socket_fork_and_exit_facts(self):
        canary = secrets.token_hex(24)
        reducer = continuity.TraceReducer()
        reader, writer = os.pipe(); stop = threading.Event()
        thread = threading.Thread(target=reducer.drain,args=(reader,stop)); thread.start()
        lines = ('100 clone(child_stack=NULL) = 101\n'
            '101 execve("/selected/hook", ["/selected/hook", "literal"], 0x0) = 0\n'
            '101 connect(3, {sa_family=AF_UNIX, sun_path="/owned/api.sock"}, 110) = 0\n'
            '101 execve("/unknown/hook", ["/unknown/hook", "'+canary+'"], 0x0) = 0\n'
            '101 exit_group(0) = ?\n101 +++ exited with 0 +++\n')
        os.write(writer,lines.encode()); os.close(writer); thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assert_private(canary,json.dumps(reducer.rows))
        result = continuity.audit_syscalls({'/selected/hook'},{'/owned/api.sock'},reducer=reducer)
        self.assertEqual(result['violations'],['unapproved or changed executable'])
        self.assertTrue(result['execve'][0]['executable_validated'])
        self.assertNotIn('path',result['execve'][1])
        self.assertEqual(result['connect'][0]['sockaddr']['path'],'/owned/api.sock')
        self.assertTrue(any(row.get('child') == 101 for row in result['processes']))
        self.assertTrue(any(row['kind'] == 'exit' and row['result'] == 0 for row in result['processes']))
        self.assert_private(canary,json.dumps(result)); self.assertEqual(reducer.rows,[])
        self.assertEqual(list(self.root.iterdir()),[])

    def test_unfinished_malformed_and_unknown_exec_errors_never_echo_raw_argv(self):
        for mode in ('unfinished','malformed','unknown-path','limit'):
            with self.subTest(mode=mode):
                canary = secrets.token_hex(24); reducer = continuity.TraceReducer()
                line = '100 execve("/unknown/'+canary+'", ["'+canary+'"], 0x0) = 0'
                if mode == 'unfinished': line = line.split(' = ')[0]+' <unfinished ...>'
                if mode == 'malformed': line = '100 execve("'+canary+'", [truncated...], 0x0) = 0'
                if mode == 'limit': reducer.max_line = 8
                reducer.feed(line)
                result = continuity.audit_syscalls(set(),set(),reducer=reducer)
                self.assertTrue(result['violations'])
                self.assert_private(canary,json.dumps(result))
                self.assertEqual(reducer.pending,{})

    def test_unfinished_resume_and_hex_escaped_vectors_retain_safe_facts(self):
        reducer = continuity.TraceReducer()
        reducer.feed('100 execve("\\x2f\\x61", ["\\x2f\\x61", "\\xc3\\xa9"], 0x0 <unfinished ...>')
        reducer.feed('100 <... execve resumed>) = 0')
        result = continuity.audit_syscalls({'/a'},set(),reducer=reducer)
        self.assertEqual(result['violations'],[])
        self.assertTrue(result['execve'][0]['executable_validated'])

    def test_successful_unknown_exec_cannot_use_negative_lookup_or_anchor_exceptions(self):
        for path, normal in (('/missing/git','/missing'),('/fixture/project/release/.prepare-123/probe','')):
            reducer = continuity.TraceReducer()
            reducer.feed('100 execve('+json.dumps(path)+', ["git", "--version", "extra"], 0x0) = 0')
            result = continuity.audit_syscalls({'/usr/bin/git'},set(),reducer=reducer,
                scratch_root='/fixture',normal_path=normal)
            self.assertEqual(result['violations'],['unapproved or changed executable'])

    def test_real_strace_pipe_discards_descendant_canary_on_refusal(self):
        canary = secrets.token_hex(24)
        executable = str(Path(sys.executable).resolve())
        unknown = self.root/'unselected-child'
        unknown.write_text('#!'+executable+'\nimport sys;print(sys.argv[1]);print(sys.argv[1],file=sys.stderr)\n')
        unknown.chmod(0o500)
        code = 'import subprocess;subprocess.run(['+repr(str(unknown))+','+repr(canary)+'])'
        approved = [executable,'-I','-B','-c',code]
        descriptor, writer = os.pipe(); stop = threading.Event(); reducer = continuity.TraceReducer()
        thread = threading.Thread(target=reducer.drain,args=(descriptor,stop)); thread.start()
        try:
            process = subprocess.Popen([shutil.which('strace'),'-f','-s','131072','-xx',
                '-e','trace=process,connect','-o','/dev/fd/'+str(writer),*approved],
                pass_fds=(writer,),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            os.close(writer); writer = None
            stdout, stderr = process.communicate(timeout=15)
        finally:
            if writer is not None: os.close(writer)
            thread.join(timeout=5)
            if thread.is_alive(): stop.set(); thread.join(timeout=1)
        self.assertEqual(process.returncode,0)
        self.assertTrue(canary.encode() in stdout and canary.encode() in stderr)
        result = continuity.audit_syscalls({executable},set(),reducer=reducer)
        self.assertEqual(result['violations'],['unapproved or changed executable'])
        self.assertEqual(len(result['execve']),2)
        self.assertTrue(any(row['kind'] == 'exit' for row in result['processes']))
        stdout = stderr = b''
        self.assert_private(canary,json.dumps(result))
        unknown.unlink()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_pinned_descendant_without_parent_audit_is_accepted_and_changed_bytes_refuse(self):
        executable = self.root/'stager'; executable.write_bytes(b'pinned fixture executable'); executable.chmod(0o500)
        pins = {str(executable):continuity.digest(executable)}
        for changed in (False,True):
            if changed:
                executable.chmod(0o700); executable.write_bytes(b'changed'); executable.chmod(0o500)
            reducer = continuity.TraceReducer(exec_pins=pins)
            reducer.feed('101 execve('+json.dumps(str(executable))+', ["stager", "literal"], 0x0) = 0')
            result = continuity.audit_syscalls(set(pins),set(),reducer=reducer,exec_pins=pins)
            self.assertEqual(bool(result['violations']),changed)

    def test_real_producer_probe_requires_exact_bytes_private_parent_and_no_alias(self):
        import stage_lifecycle_compatibility as producer
        python = str(Path(sys.executable).resolve())
        scratch = self.root/'project/release/.prepare-test'; scratch.mkdir(parents=True,mode=0o700)
        producer.qualify_launch_anchors(dict(python=python,shell='/bin/sh'),scratch)
        probe = scratch/'probe'
        for changed in (False,True):
            if changed:
                probe.chmod(0o700); probe.write_text('#!/bin/sh\nexit 0\n'); probe.chmod(0o500)
            reducer = continuity.TraceReducer(scratch=self.root)
            reducer.feed('101 execve('+json.dumps(str(probe))+', ["probe"], 0x0) = 0')
            result = continuity.audit_syscalls(set(),set(),reducer=reducer,scratch_root=self.root,python_path=python,exec_pins={})
            self.assertEqual(bool(result['violations']),changed)

    def test_credential_inode_alias_is_not_selected_content(self):
        source,target = self.root/'scratch',self.root/'retained'; source.mkdir(mode=0o700)
        canary = secrets.token_hex(24); (source/'unknown-credential').write_text(canary)
        os.link(source/'unknown-credential',source/'stored-bodies.txt')
        with self.assertRaises(continuity.PrerequisiteError) as error:
            continuity.retain_evidence(source,target)
        self.assert_private(canary,str(error.exception))
        self.assertFalse((target/'stored-bodies.txt').exists())

    def test_native_exact_argv_still_rejects_canary_without_echo(self):
        executable = str(Path(sys.executable).resolve()); canary = secrets.token_hex(24)
        observer = continuity.NativeAudit(self.root,dict(exec_pins={executable:continuity.digest(executable)},
            exact_argv=[[executable,'literal']],git_executable='/unselected/git'))
        with self.assertRaises(continuity.PrerequisiteError) as error:
            observer.audit('subprocess.Popen',(executable,[executable,canary],None,None))
        self.assert_private(canary,str(error.exception))
        for path in self.root.iterdir(): self.assert_private(canary,path.read_text())

    def test_daemon_shutdown_waits_and_detach_keep_metadata(self):
        for pending in ('accept4(5, NULL, NULL, SOCK_CLOEXEC','wait4(-1, NULL, 0, NULL','exit_group(0',
                        'socket(AF_INET, SOCK_STREAM, 0','clone3({flags=CLONE_VM|CLONE_THREAD}, 88'):
            reducer = continuity.TraceReducer()
            reducer.feed('100 '+pending+' <unfinished ...>')
            reducer.feed('100 <detached ...>')
            reducer.feed('100 +++ exited with 0 +++')
            result = continuity.audit_syscalls(set(),set(),reducer=reducer)
            self.assertEqual(result['violations'],[])
            self.assertEqual(len(result['expected_shutdown']),1)
        reducer = continuity.TraceReducer()
        reducer.feed('100 accept4(5, NULL, NULL, SOCK_CLOEXEC <detached ...>')
        reducer.feed('100 +++ exited with 0 +++')
        self.assertEqual(continuity.audit_syscalls(set(),set(),reducer=reducer)['violations'],[])

    def test_consequential_unfinished_and_unowned_detach_still_refuse(self):
        for call in ('execve("/selected", ["private"], 0x0',
                     'connect(3, {sa_family=AF_UNIX, sun_path="/unknown"}, 110',
                     'bind(3, {sa_family=AF_UNIX, sun_path="/unknown"}, 110',
                     'clone3({flags=CLONE_VM}, 88'):
            reducer = continuity.TraceReducer()
            reducer.feed('100 '+call+' <unfinished ...>'); reducer.feed('100 +++ exited with 0 +++')
            self.assertIn('unfinished audited operation',continuity.audit_syscalls(set(),set(),reducer=reducer)['violations'])
        reducer = continuity.TraceReducer(); reducer.feed('100 <detached ...>')
        self.assertIn('unexpected detached process',continuity.audit_syscalls(set(),set(),reducer=reducer)['violations'])
        result = continuity.audit_syscalls(set(),{'/owned/socket'},reducer=continuity.TraceReducer(),owned_daemons={100:'/owned/api'})
        self.assertIn('missing owned daemon execution',result['violations'])
        self.assertIn('missing owned socket connection',result['violations'])
        reducer=continuity.TraceReducer(); reducer.feed('100 <detached ...> malformed')
        self.assertIn('trace_parse_error',continuity.audit_syscalls(set(),set(),reducer=reducer)['violations'])

    def test_unknown_symlink_to_pinned_executable_is_refused(self):
        target=self.root/'pinned'; target.write_bytes(b'pinned'); target.chmod(0o500)
        alias=self.root/'unselected'; alias.symlink_to(target)
        pins={str(target):continuity.digest(target)}
        reducer=continuity.TraceReducer(scratch=self.root,exec_pins=pins)
        reducer.feed('100 execve('+json.dumps(str(alias))+', ["alias"], 0x0) = 0')
        result=continuity.audit_syscalls(set(pins),set(),reducer=reducer,exec_pins=pins)
        self.assertEqual(result['violations'],['unapproved or changed executable'])


if __name__ == '__main__':
    unittest.main()
