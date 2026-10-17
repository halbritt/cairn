"""Real private files and original bank; native syscalls are inert fixtures."""
import hashlib
import contextlib
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import test_trial_sdk_fixed_run as rf
from test_trial_sdk_callback_phases import interrupt_line
from trial_sdk_stream_retention import SessionStreamRetention, RetentionError
from trial_sdk_fixed_run import FixedRunOwner
from trial_sdk_stream_driver import SDKStreamDriver


class RetentionTests(unittest.TestCase):
    setUp=rf.RunTests.setUp
    setup_binding=rf.RunTests.setup_binding
    running=rf.RunTests.running
    config=rf.RunTests.config
    prepared=rf.RunTests.prepared

    def read_once(self, owner, body, *, name='stderr', fail_after=False):
        real_readv=os.readv
        def read(fd,views):
            if fd not in (733,735):return real_readv(fd,views)
            views[0][:len(body)]=body
            return len(body)
        def clock():
            if fail_after and owner._session_binding.stream.snapshot()['known']:
                raise RuntimeError('after read clock')
            return 1
        with patch('os.readv',side_effect=read),patch('time.monotonic_ns',side_effect=clock), \
             patch('select.select',return_value=([733 if name=='stdout' else 735],[],[])):
            return owner.step()

    def test_positive_read_is_fsynced_before_clock_failure_and_original_charge_held(self):
        owner,_,binding=self.prepared();before=dict(binding.resources._memory)
        with self.running(owner._session):
            with self.assertRaisesRegex(RuntimeError,'after read clock'):
                self.read_once(owner,b'private raw error',fail_after=True)
            self.assertEqual((self.root/'streams/stderr.bin').read_bytes(),b'private raw error')
            self.assertEqual(owner._evidence.synced['stderr'],17)
            self.assertEqual(binding.stream.snapshot()['known'],17)
            self.assertEqual(binding.resources._memory,before)
            self.assertTrue(binding.resources.snapshot()['unknown'])

    def test_ordinary_document_custody_uses_real_read_and_no_result_only_projection(self):
        owner,_,binding=self.prepared()
        raw=b'{"type":"assistant","message":{"content":[{"type":"text","text":"ungraded"}]}}\n'
        with self.running(owner._session):
            self.assertEqual(self.read_once(owner,raw,name='stdout'),'session')
            self.assertEqual((self.root/'streams/stdout.bin').read_bytes(),raw)
            self.assertEqual(owner._evidence.synced['stdout'],len(raw))
            self.assertFalse(owner._failed)
            self.assertIsNone(owner._terminal_graph)

    def test_invalid_parser_bytes_are_persisted_before_refusal(self):
        owner,_,binding=self.prepared()
        with self.running(owner._session):
            with self.assertRaises(ValueError):self.read_once(owner,b'not-json\n',name='stdout')
            self.assertEqual((self.root/'streams/stdout.bin').read_bytes(),b'not-json\n')
            self.assertTrue(binding.resources.snapshot()['unknown'])

    def test_partial_write_failure_retains_slab_consumes_writer_and_preserves_primary(self):
        owner,_,binding=self.prepared();real_write=os.write;calls=[]
        primary=OSError('synthetic disk failure')
        def write(fd,view):
            if fd!=owner._evidence.fds['stderr']:return real_write(fd,view)
            calls.append(True)
            if len(calls)==1:return real_write(fd,view[:2])
            raise primary
        with self.running(owner._session),patch('os.write',side_effect=write):
            with self.assertRaises(OSError) as caught:self.read_once(owner,b'abcdef')
            self.assertIs(caught.exception,primary)
            self.assertEqual((self.root/'streams/stderr.bin').read_bytes(),b'ab')
            self.assertEqual(bytes(owner._evidence.pending),b'abcdef')
            self.assertEqual(owner._evidence.state,'unknown')
            self.assertTrue(binding.resources.snapshot()['unknown'])
            with self.assertRaisesRegex(RetentionError,'retention_consumed'):
                owner._evidence.record('stderr',bytearray(b'abcdef'),6,2)
            self.assertEqual(len(calls),2)

    def test_known_read_prefix_survives_resource_settlement_interrupt(self):
        owner,_,binding=self.prepared()
        original=binding.stream.known
        def fail(promise,count):
            original(promise,count)
            raise KeyboardInterrupt('after traffic settlement')
        with self.running(owner._session),patch.object(binding.stream,'known',side_effect=fail):
            with self.assertRaises(KeyboardInterrupt):self.read_once(owner,b'prefix')
            self.assertEqual((self.root/'streams/stderr.bin').read_bytes(),b'prefix')
            self.assertEqual(binding.stream.snapshot()['known'],6)
            self.assertTrue(binding.resources.snapshot()['unknown'])

    def test_read_return_publication_interrupt_recovers_original_prefix_once(self):
        owner,_,binding=self.prepared()
        with self.running(owner._session):
            with self.assertRaises(KeyboardInterrupt):
                interrupt_line(SDKStreamDriver._read_body,'if count > 0 and self._retention',
                               lambda:self.read_once(owner,b'prefix'))
            self.assertEqual((self.root/'streams/stderr.bin').read_bytes(),b'prefix')
            self.assertEqual(owner._evidence.written['stderr'],6)

    def test_partial_constructor_shell_is_cleanup_safe_and_other_sources_close(self):
        for n,needle in enumerate(('self.observed = dict(', 'self.hashes = {',
                                  'self.faults = set()', 'self._open(\'directory\'',
                                  'self.fds[name] = fd')):
            with self.subTest(needle=needle):
                self.output=str(self.root/('prepared'+str(n)))
                review,binding=self.setup_binding();owner=FixedRunOwner(binding.resources,clock=lambda:1)
                method=SessionStreamRetention._open if 'fds[name]' in needle else SessionStreamRetention.__init__
                with self.assertRaises(KeyboardInterrupt):
                    interrupt_line(method,needle,lambda:owner.prepare(review,binding,self.config(),
                        str(self.root/('fixed'+str(n))),str(self.root/'callbacks'),rf.AGGREGATE,rf.OP_QUOTE,
                        stream_output=str(self.root/('streams'+str(n)))))
                owner.snapshot()
                self.assertEqual(owner.cleanup_step(),'closed_unknown')
                self.assertTrue(owner._session._custody._snapshot.closed)
                self.assertIn(binding.allocation,binding.resources._memory)
                self.assertTrue(binding.resources.snapshot()['unknown'])
                self.assertFalse(any(fd is not None for fd in owner._evidence.fds.values()))

    def test_incomplete_run_refuses_seal_and_close_failure_is_consumed(self):
        owner,_,binding=self.prepared();retention=owner._evidence
        # Actual bytes via the resource port; terminal owner flags below are
        # explicitly synthetic cleanup observations, not a native launch claim.
        with self.running(owner._session):self.read_once(owner,b'bytes')
        pump=owner._session._pump
        # Its context cleanup poisons/cancels the pump; finish must not convert
        # this incomplete run into a closed persistence receipt.
        with self.assertRaises(RetentionError):retention.finish(pump)
        self.assertEqual(retention.state,'unknown')
        self.assertIn(binding.allocation,binding.resources._memory)
        real_close=os.close;closed=[]
        def close(fd):
            closed.append(fd);real_close(fd)
            if len(closed)==1:raise KeyboardInterrupt('after close')
        with patch('os.close',side_effect=close):
            with self.assertRaises(KeyboardInterrupt):retention.close()
            retention.close()
        self.assertEqual(len(closed),len(set(closed)))
        self.assertTrue(retention.faults)

    @contextlib.contextmanager
    def ended_prefix(self, suffix=''):
        owner,_,binding=self.prepared(suffix)
        real_readv=os.readv
        with self.running(owner._session):
            self.read_once(owner,b'private-prefix')
            self.read_once(owner,b'',name='stdout')
            self.read_once(owner,b'',name='stderr')
            native=owner._session.native_owner
            native.close_stdin()
            with patch('os.waitpid',return_value=(123,0)):native.observe_wait()
            native.release()
            with patch('os.readv',side_effect=real_readv):yield owner,binding

    def test_successful_finish_readback_syncs_leaves_directory_parent_and_closes(self):
        with self.ended_prefix() as (owner,binding):
            retention=owner._evidence;fds=dict(retention.fds);seen=[];actual=os.fsync
            def sync(fd):seen.append(fd);return actual(fd)
            before=dict(binding.resources._memory)
            with patch('os.fsync',side_effect=sync):retention.finish(owner._session._pump)
            self.assertEqual(retention.state,'closed')
            self.assertEqual(set(seen),set(fds.values()))
            self.assertEqual(seen[-2:],[fds['directory'],fds['parent']])
            self.assertTrue(all(fd is None for fd in retention.fds.values()))
            self.assertEqual(retention.hashes['stderr'].hexdigest(),hashlib.sha256(b'private-prefix').hexdigest())
            self.assertEqual(binding.resources._memory,before)
            self.assertFalse(retention.faults)
            with self.assertRaisesRegex(RetentionError,'retention_consumed'):
                retention.finish(owner._session._pump)

    def test_leaf_directory_parent_sync_failures_never_publish_closed(self):
        for name in ('stdout','stderr','directory','parent'):
            with self.subTest(name=name), self.ended_prefix(name) as (owner,binding):
                retention=owner._evidence;target=retention.fds[name];actual=os.fsync
                primary=OSError('sync failed')
                def sync(fd):
                    if fd==target:raise primary
                    return actual(fd)
                with patch('os.fsync',side_effect=sync):
                    with self.assertRaises(OSError) as caught:retention.finish(owner._session._pump)
                self.assertIs(caught.exception,primary)
                self.assertEqual(retention.state,'unknown')
                self.assertTrue(binding.resources.snapshot()['unknown'])
                self.assertIn(binding.allocation,binding.resources._memory)
                with self.assertRaisesRegex(RetentionError,'retention_consumed'):
                    retention.finish(owner._session._pump)

    def test_readback_detects_same_size_content_and_path_replacement(self):
        for n,rename in enumerate((False,True)):
            with self.subTest(rename=rename),self.ended_prefix(str(n)) as (owner,binding):
                path=self.root/('streams'+str(n))/'stderr.bin'
                if rename:
                    path.rename(path.with_suffix('.old'))
                    path.write_bytes(b'private-prefix');path.chmod(0o600)
                else:
                    path.write_bytes(b'changed-prefix')
                with self.assertRaisesRegex(RetentionError,'retention_readback_changed'):
                    owner._evidence.finish(owner._session._pump)
                self.assertEqual(owner._evidence.state,'unknown')
                self.assertIn(binding.allocation,binding.resources._memory)

    def test_close_reentrancy_cannot_close_active_read_writer(self):
        owner,_,binding=self.prepared();actual=os.write;attempts=[]
        def write(fd,body):
            if fd==owner._evidence.fds['stderr']:
                with self.assertRaisesRegex(RetentionError,'retention_busy'):owner._evidence.close()
                attempts.append(True)
            return actual(fd,body)
        with self.running(owner._session),patch('os.write',side_effect=write):
            self.read_once(owner,b'private-prefix')
            self.assertEqual(attempts,[True])
            self.assertEqual(owner._evidence.state,'open')
            self.assertFalse(owner._evidence.close_attempted)

    def test_terminal_move_interrupts_leave_graph_owned_and_no_charge_release(self):
        for n,needle in enumerate(('self._evidence._terminal_graph = self._terminal_graph',
                                  'self._terminal_graph = None', "self._state='terminal_waited'")):
            with self.subTest(needle=needle),self.ended_prefix(str(n)) as (owner,binding):
                # Actual private file sealing with inert EOF/wait observations.
                # The terminal graph is synthetic, never an asserted native grade.
                graph={'result':'ungraded'};owner._terminal_graph=graph
                owner._state='terminal';before=dict(binding.resources._memory)
                from trial_sdk_stream_driver import StreamEvent
                with patch.object(owner._session,'step',return_value=StreamEvent('idle')):
                    with self.assertRaises(KeyboardInterrupt):
                        interrupt_line(FixedRunOwner._drain_terminal,needle,owner.step)
                self.assertTrue(owner._terminal_graph is graph or owner._evidence._terminal_graph is graph)
                self.assertEqual(binding.resources._memory,before)
                self.assertTrue(owner._failed)
                with self.assertRaisesRegex(ValueError,'run_unavailable'):owner.step()

if __name__=='__main__':unittest.main()
