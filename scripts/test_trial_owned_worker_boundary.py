"""Real disposable cgroups/bwrap/workers. No provider or native execution."""
import hashlib
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from trial_external_supervision import fixture_service_delegation, sandbox, SOURCE_PINS
from trial_selected_native_mounts import reviewed_package
from trial_owned_workers import AttemptDomain, OwnedWorker as Child, CONTROLS, Unavailable
from trial_worker_transport import WorkerTransport, encoded
from trial_worker_protocol import ProtocolError, validate_end, decode_end

SCRIPTS=Path(__file__).resolve().parent
WORKER=SCRIPTS/'supervision_fixture/owned_worker.py'
PACKAGE=reviewed_package(SCRIPTS/'selected_native',SOURCE_PINS)
SOURCES=[(WORKER,'worker.py'),(SCRIPTS/'trial_worker_protocol.py','trial_worker_protocol.py')] + [
    source for source in PACKAGE.sources if not source[1].endswith('/retention.py')]


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.output=Path(self.tmp.name)
        self.run=str(uuid.uuid4())
        self.manifest={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in
            [WORKER,SCRIPTS/'trial_owned_workers.py',SCRIPTS/'trial_worker_transport.py',SCRIPTS/'trial_worker_protocol.py',
             SCRIPTS/'trial_external_supervision.py',SCRIPTS/'supervision_fixture/launcher.py',
             SCRIPTS/'trial_task_eval.py']}
        self.manifest.update(PACKAGE.manifest)
        self.manifest['trial_selected_native_mounts.py']=hashlib.sha256((SCRIPTS/'trial_selected_native_mounts.py').read_bytes()).hexdigest()
        self.source=hashlib.sha256(encoded(self.manifest)).hexdigest()
        self.intent=hashlib.sha256(encoded(dict(run_id=self.run,source_manifest_sha256=self.source))).hexdigest()
        self.domain=AttemptDomain(fixture_service_delegation())
        self.transport=WorkerTransport(self.domain,self.run,self.source,self.intent)
        self.deadline=time.monotonic()+4

    def tearDown(self):
        result=self.domain.close(time.monotonic()+1)
        self.assertTrue(result['released'],result)
        self.assertFalse(result['errors'])
        self.transport.forget_transient()
        self.assertFalse(self.transport.input or self.transport.queue)
        self.assertTrue(all(not buf for child in self.domain.children.values() for buf in child.buffers.values()))
        self.tmp.cleanup()

    def fixed(self,role,mode='normal',*,channels='pipes/1'):
        group=self.domain.leaf(role)
        child=Child(group,sandbox(SOURCES,[(self.output,'/output')],
            [role,mode,self.run,self.source,self.intent]),channels=channels)
        self.domain.bind(role,child)
        return child

    def pump(self,predicate):
        while not predicate() and time.monotonic()<self.deadline:
            self.transport.step()
        self.assertTrue(predicate(),'bounded worker condition missing')

    def drained(self,child):
        return child.wait is not None and {'stdout','stderr'}<=child.eof

    def start(self,mode='normal',sink='normal',data=b'frozen input'):
        composition=self.fixed('composition',sink)
        self.pump(lambda:self.transport.ready)
        self.assertTrue((self.output/'INTENT.json').is_file())
        capture=self.fixed('capture',mode)
        self.pump(lambda:self.transport.gate_capture(data) if not self.transport.gated else True)
        return composition,capture

    def end(self,capture,missing=False):
        self.pump(lambda:self.drained(capture))
        closed=self.domain.close_role('capture',self.deadline)
        self.assertTrue(closed['released'])
        if missing:
            with self.assertRaisesRegex(Unavailable,'metadata_unknown'):
                self.transport.capture_report()
            value=self.transport.end_capture(None,input_bytes=None,input_bytes_written=None,
                observations=['capture_metadata_missing'])
        else:
            metadata=self.transport.capture_report()
            value=self.transport.end_capture(metadata['actor_wait'],input_bytes=metadata['input_bytes'],
                input_bytes_written=metadata['input_bytes_written'],observations=metadata['observations'])
        return value

    def finish(self,sink):
        self.pump(lambda:self.drained(sink))
        self.domain.close_role('composition',self.deadline)
        return self.transport.closed_sink()

    def test_nonzero_actor_reaches_independently_closed_failure_sink(self):
        sink,capture=self.start('nonzero',data=b'a'*8500)
        end=self.end(capture);closed=self.finish(sink)
        self.assertEqual(end['actor_wait']['raw_status'],17<<8)
        self.assertEqual(end['actor_wait']['returncode'],17)
        self.assertTrue(end['actor_wait']['pid']>0)
        self.assertTrue(end['actor_wait']['start_ticks']>0)
        self.assertEqual(end['input_bytes_written'],8500)
        self.assertEqual(closed['wait']['returncode'],0)
        self.assertEqual(closed['receipt']['outcome'],'failure_evidence')
        self.assertEqual(json.loads((self.output/'FAILURE.json').read_bytes())['end'],end)
        self.assertFalse((self.output/'PREPARED.json').exists())

    def test_known_actor_signal_is_observation_not_uncertainty(self):
        sink,capture=self.start('signal');end=self.end(capture);closed=self.finish(sink)
        self.assertTrue(os.WIFSIGNALED(end['actor_wait']['raw_status']))
        self.assertEqual(end['actor_wait']['returncode'],-15)
        self.assertNotIn('actor_wait_unknown',end['observations'])
        self.assertEqual(closed['receipt']['outcome'],'failure_evidence')

    def test_unknown_sink_boot_refuses_closed_preserving_actual_return(self):
        original=Path.read_text
        def read(path,*args,**kwargs):
            if str(path)=='/proc/sys/kernel/random/boot_id':
                raise OSError('disposable boot read unavailable')
            return original(path,*args,**kwargs)
        with patch('trial_owned_workers.Path.read_text',read):
            sink=self.fixed('composition')
        self.pump(lambda:self.transport.ready)
        capture=self.fixed('capture','nonzero')
        self.pump(lambda:self.transport.gate_capture(b'frozen input') if not self.transport.gated else True)
        self.end(capture)
        self.assertIsNone(self.finish(sink))
        self.assertIsNone(self.domain.closed['composition']['wait']['boot_id'])
        self.assertEqual(self.domain.closed['composition']['wait']['returncode'],0)
        self.assertTrue((self.output/'FAILURE.json').is_file())

    def test_unreadable_controller_boot_preserves_wait_as_unknown_identity(self):
        self.fixed('composition');self.pump(lambda:self.transport.ready)
        original=Path.read_text
        def read(path,*args,**kwargs):
            if str(path)=='/proc/sys/kernel/random/boot_id':
                raise OSError('disposable boot read unavailable')
            return original(path,*args,**kwargs)
        with patch('trial_owned_workers.Path.read_text',read):
            capture=self.fixed('capture','nonzero')
        self.pump(lambda:self.transport.gate_capture(b'frozen input') if not self.transport.gated else True)
        end=self.end(capture);self.assertIsNotNone(self.finish(self.domain.children['composition']))
        self.assertIsNone(end['capture_wait']['boot_id'])
        self.assertEqual(end['capture_wait']['returncode'],0)
        self.assertEqual(end['actor_wait']['returncode'],17)
        self.assertIn('capture_wait_unknown',end['observations'])

    def test_stream_hash_and_bytes_no_raw_retained(self):
        sink,capture=self.start('stream');end=self.end(capture);self.finish(sink)
        self.assertEqual(end['stream_bytes'],256*1024)
        self.assertEqual(end['stream_sha256'],hashlib.sha256(b'x'*(256*1024)).hexdigest())
        self.assertLess((self.output/'FAILURE.json').stat().st_size,8192)
        self.assertEqual(set(p.name for p in self.output.iterdir()),{'INTENT.json','FAILURE.json'})

    def test_known_output_cap_closes_failure_evidence_with_forwarded_digest(self):
        self.transport=WorkerTransport(self.domain,self.run,self.source,self.intent,stream_limit=1024)
        sink,capture=self.start('stream')
        with self.assertRaisesRegex(Unavailable,'stream_budget_exhausted'):
            while time.monotonic()<self.deadline:
                self.transport.step()
        with self.assertRaisesRegex(Unavailable,'stream_stop_required'):
            self.transport.step()
        self.domain.stop_role('capture',self.deadline)
        self.pump(lambda:self.drained(capture))
        self.domain.close_role('capture',self.deadline)
        end=self.transport.end_capture(None,input_bytes=12,input_bytes_written=None,
            observations=['capture_metadata_missing'])
        closed=self.finish(sink)
        self.assertIsNotNone(closed)
        self.assertEqual(end['stream_bytes'],1024)
        self.assertEqual(end['stream_sha256'],hashlib.sha256(b'x'*1024).hexdigest())
        self.assertGreater(end['observed_stream_bytes'],end['stream_bytes'])
        self.assertEqual(end['observed_stream_sha256'],hashlib.sha256(b'x'*end['observed_stream_bytes']).hexdigest())
        self.assertIn('stream_budget_exhausted',end['observations'])
        self.assertNotIn('stream_transport_unknown',end['observations'])
        self.assertIn('actor_wait_unknown',end['observations'])

    def test_bounded_backpressure_no_dropped_bytes_or_false_budget_stop(self):
        sink,capture=self.start('stream','hang')
        self.pump(lambda:len(self.transport.queue)>=128*1024-5)
        until=time.monotonic()+.03
        while time.monotonic()<until:
            self.transport.step()
        self.assertLessEqual(len(self.transport.queue),128*1024)
        self.assertFalse(self.transport.transport_loss)
        self.assertFalse(self.transport.budget_exhausted)
        self.assertEqual(self.transport.observed_bytes,self.transport.stream_bytes)
        self.domain.close_role('capture',self.deadline)
        fact=self.domain.close_role('composition',self.deadline)
        self.assertTrue(fact['released'])
        self.assertEqual(fact['wait']['returncode'],-9)
        self.assertIsNone(self.transport.closed_sink())
        self.assertFalse((self.output/'FAILURE.json').exists())

    def test_socket_backpressure_pauses_capture_and_keeps_original_queue_limit(self):
        sink=self.fixed('composition','hang',channels='unix-stream/1')
        self.pump(lambda:self.transport.ready)
        capture=self.fixed('capture','backpressure',channels='unix-stream/1')
        self.pump(lambda:self.transport.gate_capture(b'frozen input') if not self.transport.gated else True)
        view=socket.socket(fileno=sink.process.stdin.fileno())
        try:
            print('SELECTED_SOCKET_BUFFERS',json.dumps(dict(
                sndbuf=view.getsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF),
                rcvbuf=view.getsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF))),flush=True)
        finally:
            view.detach()
        # The old 256-KiB pipe fixture can fit in the socket's kernel buffer.
        # This fixed 1-MiB producer stays below the original 4-MiB stream cap.
        self.pump(lambda:len(self.transport.queue)>=128*1024-5)
        observed=self.transport.observed_bytes
        until=time.monotonic()+.03
        while time.monotonic()<until:
            self.transport.step()
        self.assertEqual(self.transport.observed_bytes,observed)
        self.assertLessEqual(len(self.transport.queue),128*1024)
        self.assertFalse(self.transport.transport_loss)
        self.assertFalse(self.transport.budget_exhausted)
        self.assertEqual(self.transport.observed_bytes,self.transport.stream_bytes)
        self.assertLess(self.transport.observed_bytes,1024*1024)
        self.domain.close_role('capture',self.deadline)
        fact=self.domain.close_role('composition',self.deadline)
        self.assertTrue(fact['released'])
        self.assertEqual(fact['wait']['returncode'],-9)
        self.assertIsNone(self.transport.closed_sink())
        self.assertFalse((self.output/'FAILURE.json').exists())

    def test_real_channel_read_fault_remains_transport_unknown(self):
        sink,capture=self.start('stream')
        original=os.read
        def fault(fd,count):
            if fd==capture.process.stdout.fileno():
                raise OSError('disposable read fault')
            return original(fd,count)
        with patch('trial_worker_transport.os.read',fault):
            with self.assertRaisesRegex(Unavailable,'channel_or_namespace_unknown'):
                while time.monotonic()<self.deadline:
                    self.transport.step()
        self.assertTrue(self.transport.transport_loss)
        self.domain.stop_role('capture',self.deadline)
        self.pump(lambda:self.drained(capture))
        self.domain.close_role('capture',self.deadline)
        end=self.transport.end_capture(None,input_bytes=12,input_bytes_written=None,
            observations=['capture_metadata_missing'])
        self.assertIn('stream_transport_unknown',end['observations'])
        self.assertNotIn('stream_budget_exhausted',end['observations'])
        self.assertIsNotNone(self.finish(sink))

    def test_lost_actor_metadata_remains_unknown_despite_closed_failure_sink(self):
        sink,capture=self.start('missing');end=self.end(capture,True);self.assertIsNotNone(self.finish(sink))
        self.assertIsNone(end['actor_wait'])
        self.assertEqual(end['capture_wait']['returncode'],36)
        self.assertIn('actor_wait_unknown',end['observations'])
        self.assertIn('input_delivery_incomplete',end['observations'])

    def test_sink_nonzero_declaration_does_not_close(self):
        sink,capture=self.start('nonzero','nonzero');self.end(capture)
        self.assertIsNone(self.finish(sink))
        self.assertEqual(self.domain.closed['composition']['wait']['returncode'],29)

    def test_sink_lost_closed_does_not_close(self):
        sink,capture=self.start('nonzero','lost');self.end(capture)
        with self.assertRaisesRegex(Unavailable,'sink_return_unknown'):
            self.finish(sink)
        self.assertEqual(self.domain.closed['composition']['wait']['returncode'],0)

    def test_sink_hang_cleanup_real_wait_and_release(self):
        sink,capture=self.start('nonzero','hang');self.end(capture)
        until=time.monotonic()+.05
        while time.monotonic()<until:
            self.transport.step()
        fact=self.domain.close_role('composition',self.deadline)
        self.assertTrue(fact['released'])
        self.assertEqual(fact['wait']['returncode'],-9)
        self.assertIsNone(self.transport.closed_sink())
        self.assertFalse((self.output/'FAILURE.json').exists())

    def test_aggregate_limits_and_descendant_release(self):
        sink,capture=self.start('descendant')
        for field,value in CONTROLS.items():
            self.assertEqual(self.domain.parent.read(field.replace('_','.')).strip(),str(value))
        self.assertEqual(self.domain.groups['capture'].path.rsplit('/',1)[0],self.domain.parent.path)
        # Descendant retains pipes; leaf cleanup terminates it before EOF.
        self.pump(lambda:capture.wait is not None)
        # Drain available bytes after kill while retaining controller-owned FDs.
        self.domain.stop_role('capture',self.deadline)
        self.assertFalse(capture.process.stdout.closed)
        self.end(capture);self.assertIsNotNone(self.finish(sink))
        self.assertFalse(self.domain.parent.populated())

    def test_gate_requires_ready_and_independent_namespace(self):
        with self.assertRaisesRegex(Unavailable,'gate_order'):
            self.transport.gate_capture(b'input')
        self.fixed('composition');self.pump(lambda:self.transport.ready)
        child=self.fixed('capture')
        with patch.object(self.domain,'verified',return_value=False):
            self.assertFalse(self.transport.gate_capture(b'input'))
        self.assertFalse(self.transport.gated)
        self.assertFalse(self.transport.input)
        self.assertIsNone(child.wait)

    def test_auxiliary_namespace_before_control_actual_wait_release(self):
        for role in ('diagnostic','export'):
            child=self.fixed(role)
            with patch.object(self.domain,'verified',return_value=False):
                self.assertFalse(self.transport.gate_auxiliary(role,b'{}'))
            self.assertNotIn(role,self.transport.auxiliary)
            self.pump(lambda:self.transport.gate_auxiliary(role,b'{}') if role not in self.transport.auxiliary else True)
            self.pump(lambda:self.drained(child))
            self.domain.close_role(role,self.deadline)
            closed=self.transport.closed_auxiliary(role)
            self.assertEqual(closed['wait']['returncode'],0)
            self.assertTrue(closed['released'])
            self.assertIsNotNone(closed['namespace_identity'])
            self.assertEqual((self.output/(role+'.json')).read_bytes(),b'{}')

    def test_auxiliary_hang_release_never_returned_closed(self):
        child=self.fixed('export','hang')
        self.pump(lambda:self.transport.gate_auxiliary('export',b'{}') if 'export' not in self.transport.auxiliary else True)
        self.pump(lambda:child.process.stdin.closed)
        fact=self.domain.close_role('export',self.deadline)
        self.assertTrue(fact['released']);self.assertEqual(fact['wait']['returncode'],-9)
        self.assertIsNone(self.transport.closed_auxiliary('export'))

    def test_inner_cleanup_fault_preserves_actual_wait_and_all_roles_cleanup(self):
        sink,capture=self.start('nonzero');self.pump(lambda:self.drained(capture))
        actual=capture.close
        def close_fault():
            actual();raise OSError('disposable injected close')
        with patch.object(capture,'close',side_effect=close_fault):
            end=self.end(capture)
        self.assertEqual(end['capture_wait']['returncode'],0)
        self.assertTrue(end['capture_released'])
        self.assertIn('worker_close_unknown',end['capture_cleanup_observations'])
        self.assertIn('capture_release_unknown',end['observations'])
        self.assertIsNotNone(self.finish(sink))

    def test_exclusive_intent_collision_prevents_ready_actor_gate(self):
        (self.output/'INTENT.json').write_bytes(b'existing selected intent')
        sink=self.fixed('composition')
        self.pump(lambda:self.drained(sink))
        self.assertNotEqual(sink.wait.returncode,0)
        self.assertFalse(self.transport.ready);self.assertFalse(self.transport.gated)
        self.assertNotIn('capture',self.domain.children)
        self.assertEqual((self.output/'INTENT.json').read_bytes(),b'existing selected intent')

    def test_parent_pid_ceiling_applies_across_two_owned_leaves(self):
        children={role:self.fixed(role,'pids') for role in ('diagnostic','export')}
        for role in children:
            self.pump(lambda: self.transport.gate_auxiliary(role,b'{}') if role not in self.transport.auxiliary else True)
        self.pump(lambda:all(self.drained(child) for child in children.values()))
        parent_events=self.domain.parent.events()
        leaf_events={role:self.domain.groups[role].events() for role in children}
        outputs={role:json.loads((self.output/(role+'.json')).read_bytes()) for role in children}
        # v6.8 on this host accounts failed ancestor charges at originating
        # leaves. Retain both observations, without inventing a parent event.
        print('SELECTED_PID_EVENTS',json.dumps(dict(aggregate=parent_events,leaves=leaf_events,workers=outputs),sort_keys=True),flush=True)
        self.assertGreater(sum(int(events['pids.events']['max']) for events in leaf_events.values()),0)
        total=0
        for role in children:
            self.assertLess(outputs[role]['forked']+3,32)
            self.assertEqual(self.domain.groups[role].read('pids.max').strip(),'32')
            self.domain.close_role(role,self.deadline)
            self.assertIsNotNone(self.transport.closed_auxiliary(role))
            total+=json.loads((self.output/(role+'.json')).read_bytes())['forked']
        self.assertLess(total,40)

    def test_changed_identity_refuses_domain_binding(self):
        group=self.domain.leaf('capture')
        child=Child(group,sandbox(SOURCES,[(self.output,'/output')],
            ['capture','normal',self.run,self.source,self.intent]))
        actual=child.group_identity
        child.group_identity=(actual[0],actual[1]+1)
        with self.assertRaisesRegex(Unavailable,'worker_binding'):
            self.domain.bind('capture',child)
        child.group_identity=actual
        self.domain.bind('capture',child)  # Ensure owned cleanup after refusal.

    def test_missing_pidfd_refuses_gate_and_retains_owned_cleanup_unknown(self):
        group=self.domain.leaf('capture')
        with patch('trial_owned_workers.os.pidfd_open',side_effect=OSError('disposable unavailable')):
            with self.assertRaisesRegex(Unavailable,'launcher_handle_unknown'):
                Child(group,sandbox(SOURCES,[(self.output,'/output')],
                    ['capture','normal',self.run,self.source,self.intent]))
        self.assertNotIn('capture',self.domain.children)
        fact=self.domain.close_role('capture',self.deadline)
        self.assertTrue(fact['released'])
        self.assertIn('owned_launcher_handle_unknown',fact['errors'])
        self.assertIsNotNone(fact['wait'])
        self.assertNotEqual(fact['wait']['returncode'],0)

    def setup_refusal(self,fail_call):
        before=len(os.listdir('/proc/self/fd'))
        group=self.domain.leaf('capture');original=os.set_blocking;calls=0
        def refuse(fd,blocking):
            nonlocal calls
            calls+=1
            if calls==fail_call:
                raise OSError('disposable post-Popen setup refusal')
            return original(fd,blocking)
        with patch('trial_owned_workers.os.set_blocking',refuse):
            with self.assertRaisesRegex(Unavailable,'worker_setup_unknown'):
                Child(group,sandbox(SOURCES,[(self.output,'/output')],
                    ['capture','normal',self.run,self.source,self.intent]))
        child=group.bootstrap_child
        self.assertIsNotNone(child.process)
        self.assertIsNotNone(child.pidfd)
        with self.assertRaisesRegex(Unavailable,'worker_binding'):
            self.domain.bind('capture',child)
        self.assertNotIn('capture',self.domain.children)
        self.assertFalse(self.transport.gated)
        pid=child.process.pid
        fact=self.domain.close_role('capture',self.deadline)
        self.assertEqual(fact['wait']['pid'],pid)
        self.assertNotEqual(fact['wait']['returncode'],0)
        self.assertEqual(os.waitstatus_to_exitcode(fact['wait']['raw_status']),fact['wait']['returncode'])
        self.assertTrue(fact['released'])
        self.assertIn('owned_worker_setup_unknown',fact['errors'])
        self.assertNotIn('owned_launcher_handle_unknown',fact['errors'])
        self.assertTrue(all(stream.closed for stream in (child.process.stdin,child.process.stdout,child.process.stderr)))
        self.assertIsNone(child.status_fd)
        self.assertIsNone(child.pidfd)
        self.assertIsNone(child.pending_status_write)
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_post_popen_refusal_stops_actual_launcher_before_leaf_join(self):
        before=len(os.listdir('/proc/self/fd'));group=self.domain.leaf('capture')
        import subprocess
        original=subprocess.Popen
        def stopped_launcher(command,**kwargs):
            # Fixed disposable process, deliberately stopped before any join.
            # This is a test-only builder, never a public/native actor route.
            process=original(['/usr/bin/python3','-c',
                'import os,signal;os.kill(os.getpid(),signal.SIGSTOP)'],**kwargs)
            until=time.monotonic()+.5
            while time.monotonic()<until:
                if Path(f'/proc/{process.pid}/stat').read_text().rsplit(') ',1)[1].split()[0]=='T':
                    return process
                time.sleep(.001)
            raise AssertionError('disposable launcher failed to stop')
        with patch('trial_owned_workers.subprocess.Popen',stopped_launcher), \
             patch('trial_owned_workers.os.set_blocking',side_effect=OSError('disposable setup refusal')):
            with self.assertRaisesRegex(Unavailable,'worker_setup_unknown'):
                Child(group,sandbox(SOURCES,[(self.output,'/output')],
                    ['capture','normal',self.run,self.source,self.intent]))
        child=group.bootstrap_child
        self.assertNotEqual(Path(f'/proc/{child.process.pid}/cgroup').read_text().strip(),'0::'+group.relative)
        self.assertFalse(group.populated())
        self.assertIsNotNone(child.pidfd)
        self.assertNotIn('capture',self.domain.children)
        fact=self.domain.close_role('capture',self.deadline)
        self.assertTrue(fact['released'])
        self.assertEqual(fact['wait']['raw_status'],9)
        self.assertEqual(fact['wait']['returncode'],-9)
        self.assertIsNone(fact['namespace_identity'])
        self.assertIn('owned_worker_setup_unknown',fact['errors'])
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_post_popen_first_pipe_setup_refusal_retains_owned_wait(self):
        self.setup_refusal(1)

    def test_post_popen_last_pipe_setup_refusal_retains_owned_wait(self):
        self.setup_refusal(3)

    def writer_close_refusal(self,reused):
        before=len(os.listdir('/proc/self/fd'))
        group=self.domain.leaf('capture');original=os.close;state=dict(failed=False,sentinel=None)
        def refuse(fd):
            child=getattr(group,'bootstrap_child',None)
            if child and fd==child.pending_status_write and not state['failed']:
                state['failed']=True
                if reused:
                    original(fd)
                    sentinel=os.open(self.output/'unrelated.txt',os.O_RDWR|os.O_CREAT|os.O_EXCL,0o600)
                    if sentinel!=fd:
                        os.dup2(sentinel,fd);original(sentinel)
                    state['sentinel']=fd
                raise OSError('disposable status writer close refusal')
            return original(fd)
        with patch('trial_owned_workers.os.close',refuse):
            with self.assertRaisesRegex(Unavailable,'worker_setup_unknown'):
                Child(group,sandbox(SOURCES,[(self.output,'/output')],
                    ['capture','normal',self.run,self.source,self.intent]))
        self.assertTrue(state['failed'])
        child=group.bootstrap_child
        fact=self.domain.close_role('capture',self.deadline)
        self.assertTrue(fact['released'])
        self.assertEqual(fact['wait']['pid'],child.process.pid)
        self.assertNotEqual(fact['wait']['returncode'],0)
        self.assertIn('owned_worker_setup_unknown',fact['errors'])
        self.assertIn('worker_descriptor_close_unknown',fact['errors'])
        if reused:
            # Recovery must not close this deliberately reused unrelated FD.
            self.assertEqual(os.fstat(state['sentinel']).st_ino,(self.output/'unrelated.txt').stat().st_ino)
            original(state['sentinel'])
        self.assertEqual(len(os.listdir('/proc/self/fd')),before)

    def test_post_popen_status_writer_close_refusal_cleans_retained_pipe(self):
        self.writer_close_refusal(False)

    def test_post_popen_status_writer_close_refusal_does_not_close_reused_fd(self):
        self.writer_close_refusal(True)

    def test_end_rejects_inconsistent_wait_and_input_binding(self):
        sink,capture=self.start('nonzero');self.pump(lambda:self.drained(capture))
        self.domain.close_role('capture',self.deadline)
        metadata=self.transport.capture_report()
        bad={**metadata['actor_wait'],'returncode':0}
        with self.assertRaisesRegex(Unavailable,'wait_inconsistent'):
            self.transport.end_capture(bad,input_bytes=12,input_bytes_written=12)
        with self.assertRaisesRegex(Unavailable,'input_binding'):
            self.transport.end_capture(metadata['actor_wait'],input_bytes=11,input_bytes_written=11)
        self.transport.end_capture(metadata['actor_wait'],input_bytes=12,input_bytes_written=12)
        self.assertIsNotNone(self.finish(sink))

    def test_sink_control_overflow_stops_and_cleans(self):
        self.fixed('composition','overflow')
        with self.assertRaisesRegex(Unavailable,'control_bounds'):
            while time.monotonic()<self.deadline:
                self.transport.step()


class ProtocolTests(unittest.TestCase):
    """Constructed packet validation only; these declarations attest no runtime."""
    def setUp(self):
        self.run='00000000-0000-4000-8000-000000000001';self.source='a'*64
        wait=dict(pid=1,start_ticks=1,boot_id=self.run,raw_status=0,returncode=0)
        self.value=dict(schema='cairn.worker-end-observation/1',run_id=self.run,
            source_manifest_sha256=self.source,stream_sha256='b'*64,stream_bytes=1,
            observed_stream_sha256='b'*64,observed_stream_bytes=1,actor_wait=wait.copy(),
            capture_wait=wait.copy(),capture_released=True,input_bytes=1,input_bytes_written=1,
            observations=[],capture_cleanup_observations=[])

    def check(self,value):
        return validate_end(value,run_id=self.run,source_manifest_sha256=self.source)

    def test_strict_bindings_no_extra_payload_and_copy(self):
        copy=self.check(self.value);copy['actor_wait']['pid']=2
        self.assertEqual(self.value['actor_wait']['pid'],1)
        for changes in ({'run_id':str(uuid.uuid4())},{'source_manifest_sha256':'c'*64},
                        {'raw_output':'private arbitrary payload'}):
            with self.subTest(keys=list(changes)),self.assertRaises(ProtocolError):
                self.check({**self.value,**changes})

    def test_unknown_facts_require_fixed_concurrent_observations(self):
        value={**self.value,'actor_wait':None,'capture_wait':None,'capture_released':None,
            'input_bytes_written':None}
        with self.assertRaisesRegex(ProtocolError,'unknown_observation_missing'):
            self.check(value)
        value['observations']=sorted(['actor_wait_unknown','capture_wait_unknown',
            'capture_release_unknown','input_delivery_incomplete'])
        self.assertIsNone(self.check(value)['capture_wait'])
        with self.assertRaisesRegex(ProtocolError,'observation_bounds'):
            self.check({**value,'capture_cleanup_observations':['arbitrary failure text']})

    def test_wait_and_forwarded_vs_observed_consistency(self):
        for change in ({'returncode':17},{'raw_status':65535},{'raw_status':True},
                       {'raw_status':None,'returncode':{}},{'pid':True},
                       {'boot_id':'invalid'}):
            with self.subTest(change=change),self.assertRaises(ProtocolError):
                self.check({**self.value,'actor_wait':{**self.value['actor_wait'],**change}})
        value={**self.value,'observed_stream_bytes':2,'observed_stream_sha256':'c'*64}
        with self.assertRaisesRegex(ProtocolError,'unknown_observation_missing'):
            self.check(value)
        value['observations']=['stream_budget_exhausted']
        self.assertEqual(self.check(value)['stream_bytes'],1)
        with self.assertRaisesRegex(ProtocolError,'boot_identity_mismatch'):
            self.check({**self.value,'actor_wait':{**self.value['actor_wait'],'boot_id':str(uuid.uuid4())}})

    def test_bounded_deep_duplicate_and_nonfinite_json(self):
        for data in (b'['*2000+b'0'+b']'*2000,b'{"schema":0,"schema":1}',b'{"schema":NaN}',b'x'*65537):
            with self.subTest(length=len(data)),self.assertRaises(ProtocolError):
                decode_end(data,run_id=self.run,source_manifest_sha256=self.source)
        self.assertEqual(decode_end(encoded(self.value),run_id=self.run,source_manifest_sha256=self.source),self.value)


if __name__=='__main__':
    unittest.main()
