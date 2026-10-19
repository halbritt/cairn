"""Scoped real control/writer/RPC lifecycle with inert process edges.

Full source/preparation/session standing allocations are NOT admitted by this
fixture. Existing callback/service quotes remain unchanged. No native launch.
"""
import contextlib
import hashlib
import inspect
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_trial_sdk_fixed_bootstrap as bootstrap
from test_trial_sdk_callback_phases import interrupt_line
from test_trial_sdk_command_input import process_boundaries
from test_trial_sdk_engine_memory_rpc_parent import OP_QUOTE
from trial_sdk_control import SDKControl
from trial_sdk_stream_driver import SDKStreamDriver
from trial_sdk_session_driver import SDKSessionDriver
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_fixed_outer import FixedCallbackDriver
from trial_sdk_shared_coordinator import SharedCoordinator
from trial_sdk_incoming import canonical_chunks


@contextlib.contextmanager
def session_for(h):
    """Real writer counts; synthetic native owner; omitted preparation admission."""
    control = SDKControl(h.coordinator._event.callback._input['session_id'],
        b'{"type":"user","message":{"role":"user","content":"Frozen task."}}\n',31)
    owner = NativeProcessOwner()
    pump = SDKStreamDriver(control, owner)
    with process_boundaries(), patch('os.waitpid',return_value=(0,0)), \
            patch('os.write',side_effect=lambda fd,data:min(17,len(data))):
        owner.start(('/synthetic-sdk',),'/synthetic',{})
        while control.write_pending:pump._writer.advance()
        control.receive(dict(type='control_response',response=dict(subtype='success',
            request_id=control.bindings['initialize_request_id'],response={})))
        while control.write_pending:pump._writer.advance()
        callback=h.coordinator._event.callback
        control.receive(dict(type='control_request',request_id=callback.request_id,request=dict(
            subtype='hook_callback',callback_id=control.bindings['callback_ids'][callback.event],
            input=callback._input)))
        pump._active=dict(h.coordinator._event.observation,request_id=callback.request_id,row=0)
        session=SDKSessionDriver.__new__(SDKSessionDriver)
        session._resources=None
        session._custody=SimpleNamespace(control=control)
        session._native=owner;session._pump=pump
        session._first_step_checked=True
        session._terminal_observed=None
        session._busy=session._sources_close_attempted=False
        session._failure=None;session._cleanup=()
        h.coordinator.session=session
        h.coordinator._cursor=0
        h.coordinator._output=h.coordinator._quote=None
        h.coordinator._reply_queued=h.coordinator._reply_written=False
        yield session


class OuterTests(unittest.TestCase):
    @contextlib.contextmanager
    def completed(self,graph=None):
        h=bootstrap.ScopedBindingHarness(self)
        with h.held_service():
            h.coordinator.bind_fixed_producer(h.fixed)
            with h.inert_owner(),h.readiness():
                h.rpc._connection=h.held_rpc_connection
                bootstrap.finish_bootstrap(self,h)
                h.client.close()
                # Authored producer stdout fixture through real read/decoder/finish.
                payload=b''.join(canonical_chunks({} if graph is None else graph))
                def read(fd,views):
                    views[0][:len(payload)]=payload
                    return len(payload)
                with patch('os.readv',side_effect=read):h.producer._read('stdout')
                h.producer._read('stdout');h.producer._read('stderr')
                with patch('os.waitpid',return_value=(123,0)):
                    h.owner.observe_wait()
                    self.assertEqual(h.producer.step(0),'ready')
                with session_for(h) as session:
                    driver=FixedCallbackDriver(h.coordinator,h.fixed,OP_QUOTE)
                    yield h,session,driver

    def drain(self,h,driver):
        for _ in range(200):
            state=driver.step()
            if state=='detached':return
        self.fail('did not observe actual reply write and detach')

    def test_real_projection_partial_writes_detach_then_retire(self):
        graph={'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':'café 😀'}}
        with self.completed(graph) as (h,session,driver):
            self.assertEqual(driver.step(),'reply_queued')
            self.assertIsNone(h.coordinator._output._graph)
            self.assertFalse(h.coordinator._reply_written)
            self.assertIn(h.allocation,h.resources._memory)
            self.drain(h,driver)
            self.assertIsNone(h.coordinator._event)
            self.assertIsNone(h.coordinator._output)
            self.assertIn(h.allocation,h.resources._memory)
            row=session._custody.control.ledger()['rows'][0]
            self.assertTrue(row['reply_written'])
            self.assertEqual(row['context_sha256'],hashlib.sha256('café 😀'.encode()).hexdigest())
            self.assertEqual(row['reply_written_bytes'],row['reply_bytes'])
            release=h.resources.release
            def check_release(ticket):
                names=[];frame=inspect.currentframe()
                while frame is not None:
                    names.append(frame.f_code.co_name);frame=frame.f_back
                self.assertNotIn('_queue_reply',names)
                self.assertNotIn('_detach',names)
                self.assertIsNone(h.coordinator.producer)
                self.assertIsNone(h.producer._callback)
                self.assertIsNone(h.producer._bootstrap)
                self.assertFalse(session._custody.control.write_pending)
                return release(ticket)
            with patch.object(h.resources,'release',side_effect=check_release):
                self.assertEqual(driver.step(),'retired')
            self.assertNotIn(h.allocation,h.resources._memory)
            with self.assertRaisesRegex(ValueError,'outer_unavailable'):driver.step()

    def test_original_handle_result_through_wire_projection_and_retirement(self):
        # Full selected engine.handle is called through the paired invoke entry;
        # file state and framed client/service are real authored synthetic paths.
        with tempfile.TemporaryDirectory() as directory:
            original=SDKControl.receive
            def receive(control,frame):
                request=frame.get('request',{})
                if request.get('subtype')=='hook_callback':
                    request['input'].update(cwd=directory,prompt='synthetic task')
                return original(control,frame)
            with patch.object(SDKControl,'receive',receive):
                h=bootstrap.ScopedBindingHarness(self,state_dir=str(Path(directory)/'state'))
            engine=bootstrap.engine_module()
            with h.held_service():
                h.coordinator.bind_fixed_producer(h.fixed)
                h.rpc._connection=h.held_rpc_connection
                with h.inert_owner(),h.readiness():
                    bootstrap.finish_bootstrap(self,h)
                    def invoke_and_encode():
                        result=bootstrap.invoke(engine,h.producer._bootstrap,h.client)
                        return b''.join(canonical_chunks(result))
                    payload=invoke_and_encode()
                    self.assertTrue(list((Path(directory)/'state').glob('*.json')))
                    h.client.close()
                    offset=0
                    def read(fd,views):
                        nonlocal offset
                        count=min(len(views[0]),len(payload)-offset)
                        views[0][:count]=payload[offset:offset+count]
                        offset+=count
                        return count
                    with patch('os.readv',side_effect=read):
                        while offset<len(payload):h.producer._read('stdout')
                    payload=None
                    h.producer._read('stdout');h.producer._read('stderr')
                    with patch('os.waitpid',return_value=(123,0)):
                        h.owner.observe_wait();h.producer.step(0)
                    with session_for(h) as session:
                        driver=FixedCallbackDriver(h.coordinator,h.fixed,OP_QUOTE)
                        driver.step();self.drain(h,driver)
                        self.assertEqual(driver.step(),'retired')
                        self.assertTrue(session._custody.control.ledger()['rows'][0]['reply_written'])
                        self.assertNotIn(h.allocation,h.resources._memory)

    def test_projection_refusal_has_no_reply_or_refund_and_cannot_retry(self):
        with self.completed({'unexpected':'payload'}) as (h,session,driver):
            with self.assertRaisesRegex(ValueError,'engine_context_shape'):driver.step()
            self.assertTrue(driver._failed)
            self.assertIn(h.allocation,h.resources._memory)
            self.assertIsNone(session._custody.control.ledger()['rows'][0]['reply_sha256'])
            with self.assertRaisesRegex(ValueError,'outer_unavailable'):driver.step()

    def test_partial_write_error_retains_ticket_and_actual_prefix(self):
        with self.completed() as (h,session,driver):
            driver.step();driver.step()
            row=session._custody.control.ledger()['rows'][0]
            self.assertGreater(row['reply_written_bytes'],0)
            self.assertFalse(row['reply_written'])
            with patch('os.write',side_effect=BrokenPipeError()):
                with self.assertRaises(BrokenPipeError):driver.step()
            self.assertIn(h.allocation,h.resources._memory)
            self.assertTrue(driver._failed)
            self.assertIsNotNone(h.coordinator._event)

    def test_interrupt_projection_detach_and_retirement_publications(self):
        targets=[(FixedCallbackDriver._queue_reply,x) for x in (
            'graph = output.take()', 'context = engine_context(', 'self._coordinator.reply(')]
        targets += [(FixedCallbackDriver._advance,x) for x in (
            "self._state = 'writing'", "self._state = 'detached'", 'self._rpc.retire()')]
        for function,needle in targets:
            with self.subTest(needle=needle),self.completed() as (h,session,driver):
                if needle in ("self._state = 'detached'",'self._rpc.retire()'):
                    driver.step()
                    if needle=='self._rpc.retire()':self.drain(h,driver)
                    else:
                        while not h.coordinator._reply_written:driver.step()
                with self.assertRaises(KeyboardInterrupt):
                    interrupt_line(function,needle,driver.step)
                self.assertTrue(driver._failed)
                self.assertTrue(h.resources.snapshot()['unknown'])
                self.assertIn(h.allocation,h.resources._memory)
                with self.assertRaisesRegex(ValueError,'outer_unavailable'):driver.step()

    def test_handoff_before_input_eof_pumps_until_original_phase_is_ready(self):
        h=bootstrap.ScopedBindingHarness(self)
        with h.held_service():
            h.coordinator.bind_fixed_producer(h.fixed)
            h.rpc._connection=h.held_rpc_connection
            with h.inert_owner(),h.readiness(),session_for(h):
                driver=FixedCallbackDriver(h.coordinator,h.fixed,OP_QUOTE)
                delayed=[]
                def handoff():
                    before=h.producer._written
                    driver.step()
                    if not h.producer._input_exhausted:
                        delayed.append((before,h.producer._written))
                h.handoff=handoff
                answer=h.client.call('search',bootstrap.ARGS)
                self.assertEqual(answer.returncode,0)
                self.assertTrue(delayed)
                self.assertTrue(any(after>before for before,after in delayed))
                self.assertTrue(h.producer._input_exhausted)
                self.assertEqual(h.gate.snapshot()['dispatches'],1)
                h.client.close()

    def test_original_deadline_survives_detach(self):
        with self.completed() as (h,session,driver):
            driver.step();self.drain(h,driver)
            h.rpc._clock=lambda:5_000_000_001
            with self.assertRaisesRegex(ValueError,'rpc_deadline'):driver.step()
            self.assertIn(h.allocation,h.resources._memory)

    def test_retained_error_traceback_is_quarantined_not_released(self):
        with self.completed({'invalid':'retained'}) as (h,session,driver):
            retained=None
            try:driver.step()
            except ValueError as error:retained=error
            frames=[];trace=retained.__traceback__
            while trace:
                if trace.tb_frame.f_code.co_name=='_queue_reply':
                    frames.append(trace.tb_frame.f_locals['graph'])
                trace=trace.tb_next
            self.assertEqual(frames,[{'invalid':'retained'}])
            self.assertIn(h.allocation,h.resources._memory)
            self.assertTrue(h.resources.snapshot()['unknown'])


if __name__=='__main__':unittest.main()
