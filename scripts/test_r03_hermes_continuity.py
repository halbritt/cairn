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
import hashlib
import shlex
from types import SimpleNamespace
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
    def test_lifecycle_diagnostics_keep_fixed_lines_without_adjacent_private_text(self):
        expected = 'Cairn lifecycle diagnostic: phase=handle error_class=KeyError'
        raw = ('private credential bearer\n' + expected + '\n'
               'Cairn lifecycle diagnostic: phase=private error_class=KeyError\n'
               + expected + ' token=private\nTraceback private argv\n').encode()
        self.assertEqual(continuity.lifecycle_diagnostics(raw), [expected])
        self.assertEqual(continuity.lifecycle_diagnostics(b'private\xff'), [])

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
                     'clone3({flags=CLONE_VM}, 88','restart_syscall(0x0'):
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

    def test_strace_unavailable_exit_requires_termination_in_owned_trace(self):
        for marker in ('???( <unfinished ...>','???() = ?','<... ??? resumed>) = ?'):
            for threaded,terminated in ((True,True),(False,True),(True,False)):
                reducer=continuity.TraceReducer()
                reducer.feed('100 clone3({flags='+('CLONE_THREAD' if threaded else 'CLONE_VM')+'}, 88) = 101')
                reducer.feed('101 '+marker)
                if terminated: reducer.feed('101 +++ exited with 0 +++')
                result=continuity.audit_syscalls(set(),set(),reducer=reducer)
                self.assertEqual(bool(result['violations']),not terminated)
        reducer=continuity.TraceReducer();reducer.feed('101 ???() = 0');reducer.feed('101 +++ exited with 0 +++')
        self.assertIn('trace_parse_error',continuity.audit_syscalls(set(),set(),reducer=reducer)['violations'])


class FaultCorrection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='r03-fault-', dir=Path.home()/'.cache')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def selection(self):
        release = self.root/'release'
        generation = release/'generations/selection-owned'; generation.mkdir(parents=True, mode=0o700)
        for name in ('receipt.json','manifest.json','memory.json'):
            (generation/name).write_text('{}')
        image_sha = hashlib.sha256(b'reviewed control bytes').hexdigest()
        binary = release/'bin'/image_sha/'cairn'; binary.parent.mkdir(parents=True,mode=0o700)
        binary.write_bytes(b'reviewed control bytes'); binary.chmod(0o500)
        selection = dict(binary=dict(path=str(binary),sha256=image_sha), engine_config=str(generation/'memory.json'),
            receipt=dict(path=str(generation/'receipt.json'),sha256=continuity.digest(generation/'receipt.json')),
            lifecycle_selection=dict(path=str(generation/'manifest.json'),sha256=continuity.digest(generation/'manifest.json')),
            commands={})
        for event in ('SessionStart','UserPromptSubmit','PostToolUse','PostToolUseFailure','PreCompact','SessionEnd'):
            capture = event in ('PreCompact','SessionEnd')
            vector = [str(binary),'memory-capture' if capture else 'memory',
                '--receipt',selection['receipt']['path'],'--receipt-sha256',selection['receipt']['sha256'],
                '--role','memory','--entrypoint','manifest.json','--config',selection['engine_config'],
                '--hook-timeout','150' if capture else '13','--launch-root',str(release/'launch'),'--shell','/bin/sh']
            raw = ('#!/bin/sh\nexec '+shlex.join(vector)+' -- "$@"\n').encode()
            launcher = release/'launch'/hashlib.sha256(raw).hexdigest()/'inbox'
            launcher.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if not launcher.exists():
                launcher.write_bytes(raw); launcher.chmod(0o500)
            else:
                self.assertEqual(launcher.read_bytes(),raw)
            selection['commands'][event] = [str(launcher)]
        return selection,image_sha

    def test_selected_control_and_two_exact_launchers_are_admitted(self):
        selection,sha = self.selection()
        pins = continuity.selected_command_pins(selection,sha)
        self.assertEqual(len(pins),3)
        self.assertEqual(pins[selection['binary']['path']],sha)

    def test_changed_control_foreign_target_and_changed_role_flags_refuse(self):
        for mode in ('bytes','foreign','role','timeout'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(dir=self.root) as name:
                prior = self.root; self.root = Path(name)
                try:
                    selected,sha = self.selection()
                    if mode == 'bytes':
                        p=Path(selected['binary']['path']);p.chmod(0o700);p.write_bytes(b'changed');p.chmod(0o500)
                    elif mode == 'foreign':
                        p=self.root/'foreign';p.write_bytes(b'reviewed control bytes');p.chmod(0o500)
                        selected['binary']['path']=str(p)
                    else:
                        p=Path(selected['commands']['SessionEnd'][0]);p.chmod(0o700)
                        p.write_text(p.read_text().replace('--role memory','--role coordination') if mode=='role'
                                     else p.read_text().replace('--hook-timeout 150','--hook-timeout 13'))
                        p.chmod(0o500)
                    with self.assertRaises(continuity.PrerequisiteError): continuity.selected_command_pins(selected,sha)
                finally: self.root=prior

    def test_callback_failure_keeps_exit_and_source_diagnostic_without_streams(self):
        canary=secrets.token_hex(24);command=['/selected/hook']
        observer=continuity.NativeAudit(self.root,dict(exact_argv=[command]))
        frame=SimpleNamespace(f_code=SimpleNamespace(co_name='run_engine',co_filename='/owned/plugins/cairn/__init__.py'),
            f_locals=dict(command=command,payload=dict(hook_event_name='SessionEnd'),timeout=150))
        observer.profile(frame,'call',None)
        line='Cairn lifecycle diagnostic: phase=handle error_class=HookError'
        observer.profile(frame,'return',SimpleNamespace(returncode=1,stdout=canary,stderr=line+'\n'+canary))
        observer.flush();row=json.loads((self.root/'calls.json').read_bytes())[0]
        self.assertEqual(row['exit'],1);self.assertEqual(row['diagnostics'],[line]);self.assertEqual(row['timeout'],150)
        self.assertNotIn(canary,(self.root/'calls.json').read_text())
        self.assertNotIn('stderr',row);self.assertEqual(row['stderr_bytes'],len((line+'\n'+canary).encode()))

    def test_fixed_exception_requires_the_complete_source_stream(self):
        raw=('Cairn lifecycle diagnostic: phase=handle error_class=HookError\n'
             'Cairn lifecycle: memory selection did not return the required schema; use native tools or an explicit handoff.\n')
        self.assertEqual(continuity.selected_failure(raw)['error_class'],'HookError')
        self.assertIsNone(continuity.selected_failure(raw+'private adjacent material'))

    def test_origin_requires_all_source_pins_and_preserves_cairn_priority(self):
        client=self.root/'client.py';client.write_text('pinned client')
        cairn=self.root/'cairn.py';cairn.write_text('pinned Cairn')
        hp={str(client):continuity.digest(client)};cp={str(cairn):continuity.digest(cairn)}
        def origin(paths):return continuity.classify_origin(paths,'/pinned/python','interpreter-sha',hp,cp)
        self.assertEqual(origin([str(client)])['component'],'hermes')
        self.assertEqual(origin([str(client),str(cairn)])['component'],'cairn')
        self.assertEqual(origin(['/unknown/source.py',str(client)])['component'],'unknown')
        cairn.write_text('changed');self.assertEqual(origin([str(client),str(cairn)])['component'],'unknown')

    def test_only_proven_hermes_refused_network_is_non_failing(self):
        class Observer(continuity.NativeAudit):
            def origin(self):return dict(component=self.spec['component'],source_sha256='pinned-source',executable_sha256='pinned-python')
        for component in ('hermes','cairn','unknown'):
            with self.subTest(component=component):
                observer=Observer(self.root,dict(component=component,provider_url=continuity.PROVIDER_URL,api_socket='/owned/socket'))
                canary=secrets.token_hex(24)
                with self.assertRaises(continuity.PrerequisiteError):observer.audit('socket.getaddrinfo',(canary,443))
                self.assertEqual(bool(observer.violations),component!='hermes')
                self.assertEqual(len(observer.refused_attempts),int(component=='hermes'))
                self.assertNotIn(canary,(self.root/'audit.json').read_text())
        observer=Observer(self.root,dict(component='hermes',provider_url=continuity.PROVIDER_URL,api_socket='/owned/socket'))
        with self.assertRaises(continuity.PrerequisiteError):observer.audit('socket.connect',(None,'/unknown/socket'))
        self.assertTrue(observer.violations)

    def test_successful_external_socket_is_always_a_violation(self):
        reducer=continuity.TraceReducer()
        reducer.feed('100 connect(3, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("203.0.113.1")}, 16) = 0')
        result=continuity.audit_syscalls(set(),set(),reducer=reducer)
        self.assertEqual(result['violations'],['unapproved socket target'])

    def test_watchdog_exact_vector_is_distinct_from_python_fallback(self):
        python=str(Path(sys.executable).resolve());script=self.root/'mcp_stdio_watchdog.py';script.write_text('pinned watchdog')
        command=continuity.watchdog_command(python,str(script),100,['/selected/api','mcp','--repo','fixture'])
        for changed in (False,True):
            reducer=continuity.TraceReducer(exec_pins={python:continuity.digest(python)},
                selected_python_scripts={str(script):continuity.digest(script)})
            actual=command if not changed else [*command[:-1],'foreign']
            reducer.feed('100 clone(child_stack=NULL) = 101')
            reducer.feed('101 execve('+json.dumps(python)+', '+json.dumps(actual)+', 0x0) = 0')
            result=continuity.audit_syscalls({python},set(),{100:'A'},python_path=python,reducer=reducer,
                exec_pins={python:continuity.digest(python)},selected_python_commands={100:[continuity.argv_digest(command)]})
            self.assertEqual(bool(result['violations']),changed)

    def test_unparsed_metadata_stays_a_violation_without_retaining_arguments(self):
        canary=secrets.token_hex(24);reducer=continuity.TraceReducer()
        # prctl is now supported explicitly; a genuinely unknown operation
        # continues to fail while exposing its safe kernel name only.
        reducer.feed('100 fixture_unknown_call('+json.dumps(canary)+') = 0')
        result=continuity.audit_syscalls(set(),set(),reducer=reducer)
        self.assertIn('unparsed_syscall:fixture_unknown_call',result['violations'])
        self.assertEqual(result['unparsed_syscalls'][0]['name'],'fixture_unknown_call')
        self.assertEqual(result['unparsed_syscalls'][0]['syscall_name_sha256'],hashlib.sha256(b'fixture_unknown_call').hexdigest())
        self.assertNotIn(canary,json.dumps(result))

    def test_pidfd_signal_zero_probe_requires_pinned_origin_and_nonzero_refuses(self):
        image=self.root/'control';image.write_bytes(b'pinned control');image.chmod(0o500)
        pins={str(image):continuity.digest(image)}
        for signal,known in (('0',True),('SIGTERM',True),('0',False)):
            with self.subTest(signal=signal,known=known):
                reducer=continuity.TraceReducer(exec_pins=pins)
                reducer.feed('100 execve('+json.dumps(str(image))+', ["control"], 0x0) = 0')
                reducer.feed('100 clone3({flags=CLONE_THREAD}, 88) = 101')
                reducer.feed('101 pidfd_send_signal(9, '+signal+', NULL, 0) = 0')
                result=continuity.audit_syscalls(set(pins) if known else set(),set(),reducer=reducer,exec_pins=pins)
                self.assertEqual(bool(result['violations']),signal!='0' or not known)
                if signal=='0' and known:
                    probe=result['declared_negative_or_anchor_probes'][0]
                    self.assertTrue(probe['signal_zero_probe']);self.assertEqual(probe['origin']['sha256'],pins[str(image)])

    def test_metadata_probe_stays_bound_to_fixture_model_and_credential(self):
        with continuity.ScriptedChat(self.root/'requests.json') as transport:
            port=transport.server.server_address[1]
            for name,key,expected in ((continuity.MODEL,'r03-fixture-only',404),
                                      ('foreign','r03-fixture-only',400),(continuity.MODEL,'foreign',400)):
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
                conn.request('POST','/api/show',json.dumps(dict(name=name)),
                             {'Authorization':'Bearer '+key,'Content-Type':'application/json'})
                response=conn.getresponse();self.assertEqual(response.status,expected);response.read();conn.close()
            self.assertEqual(len(transport.violations),2)
            self.assertEqual(transport.requests[0]['purpose'],'metadata_probe')

    def test_later_pinned_exec_cannot_qualify_an_earlier_unknown_pidfd_probe(self):
        image=self.root/'control';image.write_bytes(b'pinned control');image.chmod(0o500)
        pins={str(image):continuity.digest(image)}
        reducer=continuity.TraceReducer(exec_pins=pins)
        reducer.feed('100 pidfd_send_signal(9, 0, NULL, 0) = 0')
        reducer.feed('100 execve('+json.dumps(str(image))+', ["control"], 0x0) = 0')
        result=continuity.audit_syscalls(set(pins),set(),reducer=reducer,exec_pins=pins)
        self.assertIn('unapproved pidfd signal',result['violations'])




class F78Completion(unittest.TestCase):
    selection = FaultCorrection.selection

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='r03-f78-',dir=Path.home()/'.cache')
        self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)

    def test_canonical_selector_abi_is_shared_and_dialogue_topics_are_deterministic(self):
        script=self.root/'selector';script.write_text(continuity.fixture_selector_source(sys.executable))
        (self.root/'selection.json').write_text(json.dumps(continuity.fixture_selection('proof')))
        results=[]
        for text in ('first dialogue','second dialogue','first dialogue'):
            done=subprocess.run([sys.executable,'-B',str(script)],input=json.dumps(dict(messages=[dict(role='user',text=text)])),
                text=True,capture_output=True,check=True)
            row=json.loads(done.stdout);self.assertFalse(row['is_error']);self.assertEqual(row['type'],'result')
            self.assertNotIn('result',row);results.append(row['structured_output']['memories'][0]['title'])
        self.assertEqual(results[0],results[2]);self.assertNotEqual(results[0],results[1])

    def test_a_and_staged_b_binary_are_in_all_three_pin_sets(self):
        allowed=set();actor={};trace={};snapshots=[]
        for phase in ('A','B'):
            prior=self.root;self.root=prior/phase;self.root.mkdir(mode=0o700)
            try:
                selection,sha=self.selection()
                snapshot=continuity.register_selected(selection,sha,allowed,actor,trace);snapshots.append(snapshot)
                for old in snapshots:
                    path=old['binary']['path'];self.assertIn(path,allowed)
                    self.assertEqual(actor[path],sha);self.assertEqual(trace[path],sha)
            finally:self.root=prior

    def test_netlink_kernel_probe_requires_pinned_native_origin_and_zero_groups(self):
        python=str(Path(sys.executable).resolve());pins={python:continuity.digest(python)}
        for owner,pid,groups,external in ((True,0,0,False),(False,0,0,False),(True,12,0,False),(True,0,1,False),(True,0,0,True)):
            with self.subTest(owner=owner,pid=pid,groups=groups,external=external):
                reducer=continuity.TraceReducer(exec_pins=pins)
                reducer.feed('100 execve('+json.dumps(python)+', ["python"], 0x0) = 0')
                reducer.feed('100 bind(3, {sa_family=AF_NETLINK, nl_pid='+str(pid)+', nl_groups='+format(groups,'08x')+'}, 12) = 0')
                if owner and pid==0 and groups==0:
                    reducer.feed('100 getsockname(3, {sa_family=AF_NETLINK, nl_pid=100, nl_groups=00000000}, [12]) = 0')
                if external:reducer.feed('100 connect(4, {sa_family=AF_INET, sin_port=htons(443), sin_addr=inet_addr("203.0.113.1")}, 16) = 0')
                result=continuity.audit_syscalls(set(pins),set(),{100:'A'} if owner else {},reducer=reducer,exec_pins=pins,python_path=python)
                self.assertEqual(bool(result['violations']),not owner or pid!=0 or groups!=0 or external)

    def test_named_process_calls_keep_no_arguments_and_require_pinned_origin(self):
        image=self.root/'control';image.write_bytes(b'pinned process');image.chmod(0o500);pins={str(image):continuity.digest(image)}
        canary=secrets.token_hex(24)
        for known in (True,False):
            reducer=continuity.TraceReducer(exec_pins=pins)
            reducer.feed('100 execve('+json.dumps(str(image))+', ["control"], 0x0) = 0')
            reducer.feed('100 arch_prctl(ARCH_SET_FS, 0x123456) = 0')
            reducer.feed('100 prctl(PR_SET_NAME, '+json.dumps(canary)+', 0, 0, 0) = 0')
            reducer.feed('100 waitpid(-1, NULL, WNOHANG) = 0')
            result=continuity.audit_syscalls(set(pins) if known else set(),set(),reducer=reducer,exec_pins=pins)
            self.assertEqual(bool(result['violations']),not known);self.assertNotIn(canary,json.dumps(result))
            self.assertEqual({v['kind'] for v in result['processes']},{'arch_prctl','prctl','waitpid'})
        reducer=continuity.TraceReducer();reducer.feed('100 prctl(PR_SET_PTRACER, 42) = 0')
        self.assertIn('trace_parse_error',continuity.audit_syscalls(set(),set(),reducer=reducer)['violations'])

    def test_uname_only_admits_the_pinned_minus_p_vector(self):
        executable=str(Path(shutil.which('uname')).resolve())
        observer=continuity.NativeAudit(self.root,dict(exec_pins={executable:continuity.digest(executable)},
            exact_argv=[['uname','-p']],git_executable='/unselected/git'))
        observer.audit('subprocess.Popen',('uname',['uname','-p'],None,None))
        with self.assertRaises(continuity.PrerequisiteError):
            observer.audit('subprocess.Popen',('uname',['uname','-a'],None,None))

    def test_named_model_list_probe_is_404_but_foreign_route_refuses(self):
        with continuity.ScriptedChat(self.root/'requests.json') as transport:
            for path,expected in (('/v1/models',404),('/unselected',400)):
                conn=http.client.HTTPConnection('127.0.0.1',transport.server.server_address[1],timeout=5)
                conn.request('GET',path,headers={'Authorization':'Bearer r03-fixture-only'})
                response=conn.getresponse();self.assertEqual(response.status,expected);response.read();conn.close()
            self.assertEqual(len(transport.violations),1)


if __name__ == '__main__':
    unittest.main()
