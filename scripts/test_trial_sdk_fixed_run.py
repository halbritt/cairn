"""Owned source paths with inert children; original reservations unchanged."""
import unittest
from unittest.mock import patch
import test_trial_sdk_session_resources as sf
import test_trial_sdk_bootstrap_review as bf
from test_trial_sdk_callback_phases import interrupt_line
import test_trial_sdk_shared_coordinator as cf
from test_trial_sdk_shared_coordinator import AGGREGATE
from test_trial_sdk_engine_memory_rpc_parent import OP_QUOTE
from trial_native_bootstrap import SDKSessionCustody, PreparationRefusal
from trial_sdk_session_driver import SDKSessionDriver
from trial_sdk_fixed_run import FixedRunOwner
from trial_sdk_command_service_runtime import RAW_STORAGE

class MoveTests(unittest.TestCase):
    setUp=bf.SDKReviewTests.setUp
    setup_binding=sf.SessionResourcesTests.setup_binding

    def test_move_ends_old_aliases_without_release(self):
        review,binding=self.setup_binding();allocation=review._allocation;control=review.launch.control
        session=SDKSessionDriver.from_resources(review,binding);self.addCleanup(session.close_sources)
        for name in ('_snapshot','_launch','_control','_allocation','_move_recovery'):
            self.assertIsNone(getattr(review,name))
        self.assertIs(session._custody._allocation,allocation)
        self.assertIs(session._custody.control,control)
        self.assertIn(allocation.ticket,binding.resources._memory)
        with self.assertRaisesRegex(PreparationRefusal,'sdk_review_transferred'):review.launch

    def test_move_interrupts_keep_original_cleanup_and_ticket(self):
        targets=('preparation._move_recovery = recovery  # Publish initial recovery.','preparation._move_attempted = True',
            'self._initial = preparation.review_bindings()', 'self._snapshot = recovery._snapshot',
            'self._allocation = recovery._allocation','self._launch = preparation._launch',
            'self._control = recovery._control','self._source_failed = False',
            'preparation._transferred = True','preparation._snapshot = None',
            'preparation._launch = None','preparation._control = None',
            'preparation._allocation = None','preparation._move_recovery = None')
        for n,needle in enumerate(targets):
            with self.subTest(needle=needle):
                self.output=str(self.root/('move'+str(n)))
                review,binding=self.setup_binding();allocation=review._allocation;control=review.launch.control
                with patch.object(control,'close',wraps=control.close) as close:
                    with self.assertRaises(KeyboardInterrupt):
                        interrupt_line(SDKSessionCustody.__init__,needle,
                            lambda:SDKSessionDriver.from_resources(review,binding))
                    review.close();self.assertEqual(close.call_count,1)
                self.assertTrue(control.ledger()['closed'])
                self.assertIn(allocation.ticket,binding.resources._memory)
                self.assertTrue(binding.resources.snapshot()['unknown'])
                with self.assertRaisesRegex(PreparationRefusal,'sdk_review_transferred'):review.consume_for_session()

    def test_receiver_published_before_successful_move_returns(self):
        review,binding=self.setup_binding();allocation=review._allocation;control=review.launch.control
        original=SDKSessionCustody.__init__
        def interrupt(owner,preparation):
            original(owner,preparation)
            raise KeyboardInterrupt('after move before receiver continuation')
        with patch.object(SDKSessionCustody,'__init__',interrupt), \
             patch.object(control,'close',wraps=control.close) as close, \
             patch.object(review,'consume_for_session',side_effect=AssertionError('old return gap')):
            with self.assertRaises(KeyboardInterrupt):SDKSessionDriver.from_resources(review,binding)
            self.assertEqual(close.call_count,1)
        self.assertIn(allocation.ticket,binding.resources._memory)
        self.assertTrue(binding.resources.snapshot()['unknown'])

class RunTests(unittest.TestCase):
    setUp=bf.SDKReviewTests.setUp
    setup_binding=sf.SessionResourcesTests.setup_binding
    running=sf.SessionResourcesTests.running

    def config(self):
        return dict(policy=dict(executable='/fixed/cairn',socket='/fixed/socket',token_file='/fixed/token',
            repo='repo',task='task',run='run',context={}),engine=dict(harness='claude',recall_mode='agent_tools',
            recall_observations=False,state_dir=str(self.root/'state')),cwd='/fixed',env={},
            path=str(self.root/'service'/'memory.sock'),deadline_ns=5_000_000_001,
            standing_raw=RAW_STORAGE,standing_working=2**20,preparation_raw=40000,preparation_working=32*2**20)

    def prepared(self,suffix=''):
        self.output=str(self.root/('prepared'+suffix))
        review,binding=self.setup_binding();owner=FixedRunOwner(binding.resources,clock=lambda:1)
        self.addCleanup(owner.cleanup_step)
        with patch('time.monotonic_ns',return_value=1):
            owner.prepare(review,binding,self.config(),str(self.root/('fixed'+suffix)),str(self.root/'callbacks'),AGGREGATE,OP_QUOTE)
        return owner,review,binding

    def test_real_prepare_and_unlaunched_cleanup_preserve_all_charges(self):
        owner,review,binding=self.prepared();held=dict(binding.resources._memory)
        self.assertIsNone(review._allocation);self.assertIsNone(owner._preparation)
        with self.assertRaisesRegex(PreparationRefusal,'hook_interface_unavailable'):owner.require_complete()
        self.assertEqual(owner.cleanup_step(),'closed')
        self.assertTrue(owner._service._snapshot.closed)
        self.assertTrue(owner._session._custody._snapshot.closed)
        self.assertEqual(binding.resources._memory,held)
        with self.assertRaisesRegex(ValueError,'run_consumed'):
            owner.prepare(review,binding,self.config(),'ignored','/ignored',AGGREGATE,OP_QUOTE)

    def test_service_ready_credit_precedes_start_and_actual_ready_consumption(self):
        from test_trial_sdk_engine_memory_rpc_parent import Harness
        from test_trial_sdk_command_operation import Endpoint
        owner,review,binding=self.prepared()
        peer=Harness(self)
        # Independent synthetic service endpoint, no process/socket creation.
        from trial_sdk_command_service import CommandService,ChildSublease
        service=CommandService(owner._service._runtime.policy,'/fixed',{},ChildSublease(),
            OP_QUOTE.preparation_raw,OP_QUOTE.preparation_working,memoryview(bytearray(512)),clock=lambda:1)
        endpoint=Endpoint(service)
        def start(directory):
            self.assertIsNotNone(owner._link._rx)
            self.assertIsNotNone(owner._ready_credit)
            owner._link._connection=peer.transport(owner._link,endpoint)
        with patch.object(owner._service,'start_service',side_effect=start) as called:
            owner.start_service()
            self.assertEqual(owner.step(),'session')
            self.assertIsNone(owner._ready_credit)
            with self.assertRaisesRegex(ValueError,'run_service_consumed'):owner.start_service()
            self.assertEqual(called.call_count,1)
        # This test substitutes process/accept only; actual credit/READY codec runs.

    def test_full_reservations_refuse_callback_before_endpoint_or_launch(self):
        owner,review,binding=self.prepared()
        with self.running(owner._session),patch('time.monotonic_ns',return_value=1):
            cf.CoordinatorTests.callback(self,owner._session,owner._coordinator)
            owner._state='binding'
            with patch('trial_sdk_engine_memory_rpc.ProducerRPC.prestart') as endpoint:
                with self.assertRaisesRegex(ValueError,'coordinator_aggregate_denied'):owner.step()
                endpoint.assert_not_called()
            self.assertFalse(owner._callback_owner.snapshot()['launch_attempted'])
            self.assertTrue(binding.resources.snapshot()['unknown'])

    def test_cleanup_publication_interrupts_cannot_skip_unattempted_sources(self):
        for n,needle in enumerate(('actions=[]','for name,action in actions:',
                                   'self._sources_closed = True',"self._state='closed_unknown'")):
            with self.subTest(needle=needle):
                owner,review,binding=self.prepared(str(n))
                with self.assertRaises(KeyboardInterrupt):
                    interrupt_line(FixedRunOwner._cleanup,needle,owner.cleanup_step)
                self.assertTrue(owner._failed)
                self.assertIn('run_cleanup_interrupted',owner._faults)
                self.assertEqual(owner.cleanup_step(),'closed_unknown')
                self.assertTrue(owner._service._snapshot.closed)
                self.assertTrue(owner._session._custody._snapshot.closed)
                self.assertIn(binding.allocation,binding.resources._memory)

    def test_interrupt_between_source_owners_resumes_only_unattempted_actions(self):
        import sys
        owner,review,binding=self.prepared()
        def trace(frame,event,arg):
            if (frame.f_code is FixedRunOwner._cleanup.__code__ and event=='line'
                    and 'fixed' in owner._source_attempts and 'session' not in owner._source_attempts
                    and owner._service._snapshot.closed):
                raise KeyboardInterrupt('between source owners')
            return trace
        previous=sys.gettrace();sys.settrace(trace)
        try:
            with self.assertRaises(KeyboardInterrupt):owner.cleanup_step()
        finally:sys.settrace(previous)
        with patch.object(owner._service,'close',side_effect=AssertionError('no retry')):
            self.assertEqual(owner.cleanup_step(),'closed_unknown')
        self.assertTrue(owner._session._custody._snapshot.closed)
        self.assertIn(binding.allocation,binding.resources._memory)

    def test_interrupted_run_construction_keeps_receiving_owner_and_all_tickets(self):
        targets=('self._construct_session()', 'self._preparation = None',
                 'self._owners.append(self._link.owner)',
                 'self._service.prepare_owned(', 'self._coordinator = SharedCoordinator(',
                 "self._state = 'session'")
        for n,needle in enumerate(targets):
            with self.subTest(needle=needle):
                self.output=str(self.root/('construction'+str(n)))
                review,binding=self.setup_binding()
                owner=FixedRunOwner(binding.resources,clock=lambda:1)
                with patch('time.monotonic_ns',return_value=1):
                    with self.assertRaises(KeyboardInterrupt):
                        interrupt_line(FixedRunOwner.prepare,needle,lambda:owner.prepare(
                            review,binding,self.config(),str(self.root/('source'+str(n))),
                            str(self.root/'callbacks'),AGGREGATE,OP_QUOTE))
                self.assertTrue(owner._failed)
                self.assertIn(binding.allocation,binding.resources._memory)
                self.assertEqual(owner.cleanup_step(),'closed_unknown')
                if owner._session is not None:
                    self.assertTrue(owner._session._custody._snapshot.closed)
                else:self.assertTrue(review._snapshot.closed)

    def test_source_close_failure_does_not_skip_session_or_retry(self):
        owner,review,binding=self.prepared();original=owner._service.close
        def close():
            original();raise KeyboardInterrupt('after close')
        with patch.object(owner._service,'close',side_effect=close) as called:
            with self.assertRaises(KeyboardInterrupt):owner.cleanup_step()
            self.assertTrue(owner._session._custody._snapshot.closed)
            self.assertEqual(owner.cleanup_step(),'closed_unknown')
            self.assertEqual(called.call_count,1)
        self.assertIn(binding.allocation,binding.resources._memory)


class CleanupTests(unittest.TestCase):
    setUp=RunTests.setUp
    setup_binding=RunTests.setup_binding
    running=RunTests.running
    config=RunTests.config
    prepared=RunTests.prepared
    def test_actual_wait_gate_and_once_only_stop_requests(self):
        owner,review,binding=self.prepared()
        now=[1];owner._clock=lambda:now[0]
        with self.running(owner._session),patch('signal.pidfd_send_signal') as signal:
            owner.cancel()
            self.assertEqual(owner.cleanup_step(),'cleanup_pending')
            self.assertFalse(owner._sources_closed)
            self.assertEqual(signal.call_count,1)
            self.assertEqual(owner.cleanup_step(),'cleanup_pending')
            self.assertEqual(signal.call_count,1)
            now[0]=owner._deadline
            self.assertEqual(owner.cleanup_step(),'cleanup_pending')
            self.assertEqual(signal.call_count,2)
            with patch('os.waitpid',return_value=(123,0)):
                self.assertEqual(owner.cleanup_step(),'closed_unknown')
            self.assertTrue(owner._session.native_owner.snapshot()['release']['handles_detached'])
            self.assertTrue(owner._sources_closed)
            self.assertIn(binding.allocation,binding.resources._memory)

class ScopedSequenceTests(unittest.TestCase):
    def test_callback_driver_then_actual_terminal_consume_retains_work_product(self):
        import json
        from types import SimpleNamespace
        from test_trial_sdk_fixed_outer import OuterTests
        from trial_sdk_stream_driver import StreamEvent
        from trial_sdk_incoming import LineAssembler,validate_line
        # Reuse the scoped original callback/service fixture. It explicitly omits
        # preparation/session standing admission; no whole-bank fit is claimed.
        fixture=OuterTests();self.addCleanup(fixture.doCleanups)
        with fixture.completed() as (h,session,callback):
            owner=FixedRunOwner(h.resources,clock=lambda:1)
            owner._coordinator=h.coordinator;owner._session=session;owner._service=h.fixed
            owner._link=h.link;owner._callback=callback;owner._state='callback'
            owner._deadline=5_000_000_001
            for _ in range(200):
                if owner.step()=='retired':break
            self.assertEqual(owner._state,'session')
            self.assertIsNone(h.coordinator._event)
            # Existing selected control requires both hooks before input seal.
            control=session._custody.control
            c=control.receive(dict(type='control_request',request_id='next',request=dict(
                subtype='hook_callback',callback_id=control.bindings['callback_ids']['UserPromptSubmit'],
                input=dict(hook_event_name='UserPromptSubmit',session_id=control.bindings['session_id']))))
            control.reply(c.request_id,'')
            while control.write_pending:session._pump._writer.advance()
            c=None
            session._pump._last_write_ns=1
            session._custody.held_source_bindings=lambda:None  # Same explicit omitted source admission.
            raw=json.dumps(dict(type='result',session_id=control.bindings['session_id'],
                subtype='success',is_error=False,result='ungraded terminal work product')).encode()+b'\n'
            assembler=LineAssembler(131072)
            document=validate_line(next(assembler.feed(raw,1)))
            with patch.object(session._pump,'step',return_value=StreamEvent('document',document=document)):
                self.assertEqual(owner.step(),'terminal')
            self.assertTrue(document.taken)
            self.assertTrue(session.native_owner.snapshot()['stdin_closed'])
            self.assertEqual(owner._terminal_graph['result'],'ungraded terminal work product')
            self.assertNotIn('result',owner.snapshot())
            with patch('os.waitpid',return_value=(123,0)),                     patch('select.select',side_effect=lambda reads,writes,errors,timeout:(reads,[],[])):
                for _ in range(8):
                    if owner.step()=='terminal_waited':break
            self.assertEqual(owner._state,'terminal_waited')
            self.assertTrue(session.native_owner.snapshot()['release']['released'])
            self.assertEqual(owner._terminal_graph['result'],'ungraded terminal work product')
            self.assertEqual(owner.snapshot()['qualification'],'source_run_custody_only_no_native_or_task_acceptance')

    def test_callback_connection_parking_pumps_input_before_rpc_progress(self):
        import test_trial_sdk_fixed_bootstrap as bs
        from test_trial_sdk_fixed_outer import session_for
        from trial_sdk_fixed_outer import FixedCallbackDriver
        h=bs.ScopedBindingHarness(self)
        with h.held_service():
            h.coordinator.bind_fixed_producer(h.fixed)
            with h.inert_owner(),h.readiness(),session_for(h) as session:
                owner=FixedRunOwner(h.resources,clock=lambda:1)
                owner._coordinator=h.coordinator;owner._session=session;owner._service=h.fixed
                owner._deadline=5_000_000_001;owner._state='callback_connecting'
                with patch.object(h.rpc._listener,'accept',side_effect=[None,h.held_rpc_connection]):
                    self.assertEqual(owner.step(),'callback_connecting')
                    self.assertEqual(h.producer._written,0)
                    self.assertEqual(owner.step(),'callback_connecting')
                    self.assertGreater(h.producer._written,0)
                    owner.step()
                    self.assertEqual(owner.step(),'callback')
                self.assertIs(h.rpc._connection,h.held_rpc_connection)

    def test_terminal_publication_interrupt_keeps_graph_and_quarantines_bank(self):
        from trial_sdk_session_driver import TerminalDocument
        from trial_sdk_stream_driver import StreamEvent
        from trial_sdk_shared_coordinator import Turn
        from test_trial_sdk_terminal_document import owned,SESSION
        for needle in ('self._terminal_metadata = event.document.metadata()',
                       'self._terminal_graph = event.document.take()', "self._state = 'terminal'"):
            with self.subTest(needle=needle):
                from trial_sdk_attempt_resources import AttemptResources
                from types import SimpleNamespace
                bank=AttemptResources();ticket=bank.allocate(bank.local,'standing',65536,2**20)
                owner=FixedRunOwner(bank,clock=lambda:1);owner._state='session';owner._deadline=2
                document,_=owned(dict(type='result',session_id=SESSION,subtype='success',
                    is_error=False,result='retained work product'))
                terminal=TerminalDocument.consume(document,SESSION)
                # Only the already classified event boundary is substituted here;
                # the successful sequence test exercises actual SDKSessionDriver.
                owner._coordinator=SimpleNamespace(step=lambda:Turn('session',StreamEvent('terminal',document=terminal)),
                                                    _fail=lambda error:None)
                with self.assertRaises(KeyboardInterrupt):
                    interrupt_line(FixedRunOwner._session_turn,needle,owner.step)
                self.assertIn(ticket,bank._memory);self.assertTrue(bank.snapshot()['unknown'])
                self.assertTrue(owner._failed)
                with self.assertRaisesRegex(ValueError,'run_unavailable'):owner.step()

if __name__=='__main__':unittest.main()
