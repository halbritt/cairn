"""Synthetic bootstrap, source custody and original handle parity; no launch."""
import dataclasses
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import test_trial_sdk_shared_coordinator as coordinator_fixture
from test_trial_sdk_callback_phases import PhaseHarness, interrupt_line
from test_trial_sdk_engine_memory_rpc_parent import ARGS
from test_trial_sdk_engine_adapter import engine_module
from trial_sdk_engine_adapter import CommandOutcome
from trial_sdk_fixed_config import bindings, FixedConfigError
from trial_sdk_fixed_entry import invoke, read_document, producer as run_producer
from trial_sdk_fixed_bootstrap import FixedServiceLaunch
from trial_sdk_producer_driver import SDKProducerDriver, ProducerDriverError
from trial_sdk_incoming import canonical_chunks, canonical_size_sha256
from trial_sdk_stream_driver import StreamEvent
from trial_sdk_command_service_runtime import RAW_STORAGE
from trial_sdk_memory_broker_socket import BrokerListener
from trial_sdk_command_policy import FixedCommandPolicy
from test_trial_sdk_pinned_sources import stage_pinned_sources


def envelope(callback, config=None):
    return dict(schema='cairn.fixed-producer/1', config={} if config is None else config,
        input=callback._input, input_bytes=callback.input_bytes,
        input_sha256=callback.input_sha256, event=callback.event,
        deadline_ns=5_000_000_001, rpc_path='/synthetic/memory.sock',
        client_raw=34848, client_working=64*1024*1024)


def event_for(h):
    return StreamEvent('callback', callback=h.producer._callback, observation={
        key:h.producer._binding[key] for key in ('request_sha256','first_byte_ns','deadline_ns')})


def finish_bootstrap(test,h):
    for _ in range(200):
        h.producer.step(0)
        if h.producer._input_exhausted: break
    test.assertTrue(h.producer._input_exhausted)
    test.assertEqual(h.producer._written,h.producer._transport['input_bytes'])


class TransportTests(unittest.TestCase):
    def test_bootstrap_to_actual_first_command_preserves_two_hashes_and_ticket(self):
        h=PhaseHarness(self); original=dict(h.producer._binding)
        document=envelope(h.producer._callback)
        allocation=h.allocation
        h.producer._bind_bootstrap(event_for(h),document)
        self.assertNotEqual(original['input_sha256'],h.producer._transport['input_sha256'])
        with h.inert_owner(),h.readiness():
            finish_bootstrap(self,h)
            h.client.call('search',ARGS)
            self.assertEqual(h.gate.snapshot()['dispatches'],1)
            self.assertIs(h.rpc._allocation,allocation)
            self.assertEqual(h.producer._binding,original)
            self.assertEqual(h.producer._written_hash.hexdigest(),h.producer._transport['input_sha256'])
            h.client.close()

    def test_original_identity_tampering_refuses_before_input_or_command(self):
        h=PhaseHarness(self);h.producer._callback._input['extra']='changed'
        with self.assertRaisesRegex(ProducerDriverError,'original_input_drift'):
            h.producer._bind_bootstrap(event_for(h),envelope(h.producer._callback))
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertEqual(h.producer._written,0)
        self.assertEqual(h.gate.snapshot()['dispatches'],0)

    def test_transport_identity_tampering_refuses_before_first_command(self):
        h=PhaseHarness(self)
        h.producer._bind_bootstrap(event_for(h),envelope(h.producer._callback))
        h.producer._transport['input_sha256']='0'*64
        with h.inert_owner():
            with self.assertRaisesRegex(ProducerDriverError,'input_drift'):
                finish_bootstrap(self,h)
        self.assertTrue(h.resources.snapshot()['unknown'])
        self.assertEqual(h.gate.snapshot()['dispatches'],0)

    def test_interrupts_at_every_driver_publication_freeze_without_retry(self):
        for needle in ('self._bootstrap_attempted = True','self._drop_input()',
                       'self._bootstrap = document','self._transport = dict(',
                       'self._input = canonical_chunks(document)'):
            with self.subTest(needle=needle):
                h=PhaseHarness(self);event=event_for(h);doc=envelope(event.callback)
                with self.assertRaises(KeyboardInterrupt):
                    interrupt_line(SDKProducerDriver._install_bootstrap,needle,
                        lambda:h.producer._bind_bootstrap(event,doc))
                self.assertTrue(h.resources.snapshot()['unknown'])
                self.assertIn(h.allocation,h.resources._memory)
                self.assertIsNotNone(h.producer._failure)
                with self.assertRaises(ProducerDriverError):h.producer._bind_bootstrap(event,doc)
                self.assertEqual(h.gate.snapshot()['dispatches'],0)

    def test_envelope_digest_and_original_input_digest_are_both_checked(self):
        h=PhaseHarness(self);doc=envelope(h.producer._callback)
        raw=b''.join(canonical_chunks(doc));digest=hashlib.sha256(raw).hexdigest()
        result=read_document(io.BytesIO(raw),digest,5_000_000_001,clock=lambda:1)
        self.assertEqual(result,doc)
        with self.assertRaisesRegex(FixedConfigError,'fixed_entry_digest'):
            read_document(io.BytesIO(raw), '0'*64,5_000_000_001,clock=lambda:1)
        result['input_sha256']='0'*64
        with self.assertRaisesRegex(FixedConfigError,'fixed_original_input'):
            invoke(None,result,None)


class SourceBindingTests(unittest.TestCase):
    def setUp(self):
        coordinator_fixture.CoordinatorTests.setUp(self)
        stage_pinned_sources(self)

    setup_binding=coordinator_fixture.CoordinatorTests.setup_binding
    setup_coordinator=coordinator_fixture.CoordinatorTests.setup_coordinator

    def prepare(self):
        h,session,_=self.setup_coordinator()
        root=Path(self.output).parent
        document=dict(policy=dict(executable='/fixed/cairn',socket='/fixed/socket',
            token_file='/fixed/token',repo='repo',task='task',run='run',context={}),
            engine=dict(harness='claude',recall_mode='agent_tools',recall_observations=False,
                        state_dir=str(root/'state')),
            cwd='/fixed',env={},path=str(root/'service'/'memory.sock'),
            deadline_ns=5_000_000_001,standing_raw=RAW_STORAGE,standing_working=1024*1024,
            preparation_raw=40000,preparation_working=32*1024*1024)
        with patch('time.monotonic_ns',return_value=1):
            owner=FixedServiceLaunch.prepare(session,h.link,document,str(root/'fixed-sources'))
        self.addCleanup(owner.close)
        return h,session,owner,document

    def test_configuration_is_copied_from_held_source_before_policy_use(self):
        h,session,owner,document=self.prepare()
        document['policy']['repo']='forged';document['engine']['state_dir']='/changed'
        self.assertEqual(owner._runtime.policy.repo,'repo')
        self.assertNotEqual(owner._config['state_dir'],'/changed')
        self.assertEqual(owner._config['socket'],'/unavailable/socket')
        self.assertNotIn('/fixed/token',json.dumps(owner._config))
        with patch('time.monotonic_ns',return_value=1):
            with self.assertRaisesRegex(FixedConfigError,'fixed_service_not_held'):
                owner.policy_for(h.rpc)
        self.assertFalse(h.link.owner.snapshot()['launch_attempted'])

    def test_matching_looking_policy_is_not_a_fixed_service_binding(self):
        h,session,coordinator=self.setup_coordinator()
        with patch('time.monotonic_ns',return_value=1):
            with self.assertRaisesRegex(ValueError,'coordinator_fixed_producer'):
                coordinator.bind_fixed_producer(h.service._policy)
        self.assertEqual(h.gate.snapshot()['dispatches'],0)

    def test_changed_runtime_scope_refuses_against_held_configuration(self):
        h,session,owner,document=self.prepare()
        owner._runtime=dataclasses.replace(owner._runtime,policy=FixedCommandPolicy(
            '/fixed/cairn','/fixed/socket','/fixed/token','different','task','run'))
        with patch('time.monotonic_ns',return_value=1):
            with self.assertRaisesRegex(FixedConfigError,'fixed_config_changed'):
                owner.policy_for(h.rpc)
        self.assertEqual(h.gate.snapshot()['dispatches'],0)


class ScriptedChannel:
    def __init__(self,stdout=b'{"ok":true,"data":{"status":"READY","destination":{"name":"hosted"},"index":[]}}'):
        self.stdout=stdout;self.calls=[]
    def call(self,*args):
        self.calls.append(args)
        return CommandOutcome(0,self.stdout,b'')


class HandleParityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.engine=engine_module()
        self.config=dict(harness='claude',recall_mode='agent_tools',recall_observations=False,
            state_dir=str(self.root/'state'),cairn='/unavailable/cairn',
            socket='/unavailable/socket',token_file='/unavailable/token',
            repo='repo',task_id='task',run_id='run')
        self.session='00000000-0000-4000-8000-000000000001'
        self.channel=ScriptedChannel()

    def document(self,event='UserPromptSubmit',**updates):
        value=dict(hook_event_name=event,session_id=self.session,cwd=str(self.root),prompt='repair code')
        value.update(updates);size,digest=canonical_size_sha256(value)
        return dict(schema='cairn.fixed-producer/1',config=self.config,input=value,input_bytes=size,
            input_sha256=digest,event=event,deadline_ns=time.monotonic_ns()+5_000_000_000,
            rpc_path='/synthetic/memory.sock',client_raw=34848,client_working=64*1024*1024)

    def state(self):
        return json.loads((self.root/'state'/(self.session+'.json')).read_text())

    def test_disabled_and_marker_paths_never_dispatch(self):
        with patch.dict(os.environ,{'CAIRN_LIFECYCLE_DISABLED':'1'}):
            self.assertEqual(invoke(self.engine,self.document(),self.channel),{})
        (self.root/'.cairn-no-memory').touch()
        self.assertEqual(invoke(self.engine,self.document(),self.channel),{})
        self.assertEqual(self.channel.calls,[])
        self.assertFalse((self.root/'state').exists())

    def test_validation_and_lock_precede_state_or_dispatch(self):
        for update in ({'session_id':'invalid'},{'cwd':'/does/not/exist'},
                       {'project_path':'relative'},{'workstream':'bad\nline'}):
            with self.subTest(update=update):
                with self.assertRaises(self.engine.HookError):
                    invoke(self.engine,self.document(**update),self.channel)
        with patch.object(self.engine.fcntl,'flock',side_effect=BlockingIOError()), \
                patch.object(self.engine,'event_state',wraps=self.engine.event_state) as state:
            with self.assertRaises(self.engine.HookError):invoke(self.engine,self.document(),self.channel)
            state.assert_not_called()
        self.assertEqual(self.channel.calls,[])

    def test_post_tool_state_survives_callbacks_with_original_caps(self):
        for n in range(18):
            invoke(self.engine,self.document('PostToolUse',tool_name='Read',
                tool_input={'file_path':str(self.root/('file%d.py'%n))}),self.channel)
        errors=' '.join('Thing%sError'%chr(65+n) for n in range(12))
        invoke(self.engine,self.document('PostToolUseFailure',error=errors),self.channel)
        state=self.state();self.assertEqual(len(state['hints']['files']),16)
        self.assertEqual(len(state['hints']['errors']),8)
        self.assertNotIn('file0.py',state['hints']['files'])
        self.assertEqual(self.channel.calls,[])

    def test_real_recall_failure_restores_prior_hints_and_delivery_with_diagnostics(self):
        path=self.root/'state';path.mkdir()
        prior=dict(seen={'previous':1},hints={'files':{'keep.py':1}},last_recall={'outcome':'old'})
        (path/(self.session+'.json')).write_text(json.dumps(prior))
        self.channel.stdout=b'invalid JSON'
        with self.assertRaises(self.engine.HookError):
            invoke(self.engine,self.document('SessionStart'),self.channel)
        state=self.state();self.assertEqual(state['seen'],prior['seen'])
        self.assertEqual(state['hints'],prior['hints'])
        self.assertEqual(state['last_recall']['outcome'],'failed')
        self.assertEqual(state['last_recall']['records'],[])
        self.assertEqual(len(self.channel.calls),1)

    def test_successful_original_recall_and_persistence_precede_return(self):
        result=invoke(self.engine,self.document(),self.channel)
        self.assertIsInstance(result,dict)
        self.assertIn('last_recall',self.state())
        self.assertEqual(len(self.channel.calls),1)

    def test_final_save_failure_prevents_producer_output(self):
        doc=self.document();target=io.BytesIO()
        channel=self.channel
        channel.connect=lambda path:None
        channel.close=lambda:None
        with patch('trial_sdk_fixed_entry.read_document',return_value=doc), \
             patch('trial_sdk_fixed_entry.load_engine',return_value=self.engine), \
             patch('trial_sdk_fixed_entry.ProducerClient',return_value=channel), \
             patch.object(self.engine,'save_state',side_effect=OSError('synthetic persistence')):
            with self.assertRaises(OSError):
                run_producer(io.BytesIO(),target,'0'*64,doc['deadline_ns'],'/synthetic','0'*64)
        self.assertEqual(target.getvalue(),b'')
        self.assertEqual(len(channel.calls),1)



    def test_selected_hooks_match_original_handle_output_and_state(self):
        from trial_sdk_engine_adapter import EngineMemoryAdapter
        state_dir=self.root/'state';state_dir.mkdir()
        path=state_dir/(self.session+'.json')
        prior=dict(seen={'previous':1},hints={'files':{'keep.py':1}})
        for event in ('SessionStart','UserPromptSubmit','PostToolUse','PostToolUseFailure'):
            with self.subTest(event=event):
                document=self.document(event,tool_name='Read',
                    tool_input={'file_path':str(self.root/'new.py')},error='ValueError ETIMEOUT')
                path.write_text(json.dumps(prior))
                baseline=ScriptedChannel()
                with patch.object(self.engine.time,'time',return_value=100), \
                     patch.object(self.engine.time,'monotonic',return_value=1):
                    with EngineMemoryAdapter(self.engine,self.config,baseline):
                        expected=self.engine.handle(self.config,document['input'])
                    expected_state=self.state()
                    path.write_text(json.dumps(prior))
                    actual_channel=ScriptedChannel()
                    actual=invoke(self.engine,document,actual_channel)
                self.assertEqual(actual,expected)
                self.assertEqual(self.state(),expected_state)
                self.assertEqual(actual_channel.calls,baseline.calls)

    def test_original_disabled_report_runs_after_session_lock_is_released(self):
        import fcntl
        report=self.engine.report_recall;observed=[]
        def inspect_report(**fields):
            lock=self.root/'state'/(self.session+'.lock')
            with lock.open('a') as handle:
                fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                observed.append(fields['status'])
                report(**fields)
        with patch.object(self.engine,'report_recall',side_effect=inspect_report):
            invoke(self.engine,self.document(),self.channel)
        self.assertEqual(observed,['completed'])
        self.assertEqual(len(self.channel.calls),1)  # No report API effect.


# This fixture omits the unaffordable standing SDK/session/source-preparation
# combination. It substitutes ONLY session admission and source revalidation;
# it keeps real callback/service bank tickets, real service launch state over
# inert syscall boundaries, and the complete production binding/publication code.
# It is not evidence of full-bank admission or authenticated native execution.
class ScopedBindingHarness(PhaseHarness):
    def __init__(self,test,state_dir='/synthetic/state'):
        from types import SimpleNamespace
        from trial_sdk_shared_coordinator import SharedCoordinator
        super().__init__(test)
        service=FixedServiceLaunch.__new__(FixedServiceLaunch)
        service.link=self.link
        document=dict(policy=dict(executable='/fixed/cairn',socket='/fixed/socket',
            token_file='/fixed/token',repo='repo',task='task',run='run',context={}),
            engine=dict(harness='claude',recall_mode='agent_tools',recall_observations=False,
                        state_dir=state_dir),cwd='/fixed',env={},
            path='/synthetic/service/memory.sock',deadline_ns=5_000_000_001,
            standing_raw=RAW_STORAGE,standing_working=1024*1024,
            preparation_raw=40000,preparation_working=32*1024*1024)
        service._runtime,service._config=bindings(document)
        self.service._policy=service._runtime.policy
        service._started=service._failed=service._closed=False
        service._producers=[];service._cleanup_faults=set()
        service._directory='/synthetic/sources';service._config_sha='a'*64
        service._allocation=SimpleNamespace(poison=lambda primary:self.resources.freeze())
        service._check=lambda:None  # Explicit omitted source/session admission.
        coordinator=SharedCoordinator.__new__(SharedCoordinator)
        coordinator.session=SimpleNamespace(_live=lambda:None,_poison=lambda:None,_resources=None)
        coordinator.gate,coordinator.link,coordinator.resources=self.gate,self.link,self.resources
        coordinator._clock=lambda:1;coordinator._last_clock=None;coordinator._deadline=5_000_000_001
        coordinator._busy=coordinator._unknown=coordinator._cancelled=False
        coordinator._faults=set();coordinator._allocation=self.allocation
        coordinator.producer,coordinator.rpc=self.producer,self.rpc
        coordinator.operation=None;coordinator._event=event_for(self)
        self.rpc._listener=BrokerListener();self.rpc._listener._path='/synthetic/memory.sock'
        self.rpc._listener._started=True  # Inert listener edge, no actual socket.
        self.held_rpc_connection,self.rpc._connection=self.rpc._connection,None
        self.stdout=b'{"ok":true,"data":{"status":"READY","destination":{"name":"hosted"},"index":[]}}'
        self.stderr=b'';self.code=0
        self.fixed,self.coordinator=service,coordinator

    def held_service(self):
        import contextlib
        from test_trial_sdk_command_input import process_boundaries
        @contextlib.contextmanager
        def held():
            # Only the service listener/launch wrapper's OS-facing boundary is
            # substituted. NativeProcessOwner records the actual synthetic start.
            def prestart(directory,argv,cwd,env):
                self.service_argv=argv
                self.link.owner.start(argv,cwd,env)
                self.link.owner.close_stdin()
            with process_boundaries(),patch('time.monotonic_ns',return_value=1), \
                    patch.object(self.link,'prestart',side_effect=prestart),patch('os.close'):
                self.fixed.start_service('/synthetic/service')
                yield
        return held()

    def handoff(self):
        from test_trial_sdk_engine_memory_rpc_parent import OP_QUOTE
        self.coordinator.prepare_next_command(OP_QUOTE)


class ParentPublicationTests(unittest.TestCase):
    def test_scoped_full_binding_then_first_command_uses_held_service_policy(self):
        h=ScopedBindingHarness(self)
        with h.held_service():
            h.coordinator.bind_fixed_producer(h.fixed)
            self.assertIs(h.rpc._read_custody._policy,h.service._policy)
            self.assertIs(h.rpc._read_custody._service_custody,h.fixed)
            self.assertIn(h.producer,h.fixed._producers)
            self.assertEqual(h.producer._bootstrap['config']['repo'],h.service._policy.repo)
            h.rpc._connection=h.held_rpc_connection
            with h.inert_owner(),h.readiness():
                finish_bootstrap(self,h)
                h.client.call('search',ARGS)
                self.assertEqual(h.gate.snapshot()['dispatches'],1)
                h.client.close()

    def test_publication_interrupts_poison_every_owner_and_cannot_retry(self):
        for needle in ('producer._bind_bootstrap(event, document)',
                       'custody = ReadCustody(rpc, policy)',
                       'custody._service_custody = self','rpc._read_custody = custody'):
            with self.subTest(needle=needle):
                h=ScopedBindingHarness(self)
                with h.held_service():
                    with self.assertRaises(KeyboardInterrupt):
                        interrupt_line(FixedServiceLaunch._bind_producer,needle,
                            lambda:h.coordinator.bind_fixed_producer(h.fixed))
                    self.assertIn(h.producer,h.fixed._producers)
                    self.assertTrue(h.fixed._failed)
                    self.assertTrue(h.rpc._unknown)
                    self.assertIsNotNone(h.producer._failure)
                    self.assertTrue(h.resources.snapshot()['unknown'])
                    self.assertIn(h.allocation,h.resources._memory)
                    with self.assertRaises(ValueError):h.coordinator.bind_fixed_producer(h.fixed)
                    self.assertEqual(h.gate.snapshot()['dispatches'],0)

    def test_cancel_busy_or_failed_after_bind_never_starts_producer(self):
        for condition in ('cancelled','busy','failed'):
            with self.subTest(condition=condition):
                h=ScopedBindingHarness(self)
                with h.held_service():
                    h.coordinator.bind_fixed_producer(h.fixed)
                    if condition=='cancelled':h.producer.cancel()
                    elif condition=='busy':h.producer._busy=True
                    else:h.producer._failure='synthetic failure'
                    with patch.object(h.owner,'start') as start:
                        with self.assertRaises(ProducerDriverError):h.fixed.start_producer(h.producer)
                    start.assert_not_called()
                    self.assertFalse(h.owner.snapshot()['launch_attempted'])

    def test_original_or_transport_drift_after_stdin_close_refuses_first_command(self):
        for binding in ('_binding','_transport'):
            with self.subTest(binding=binding):
                h=ScopedBindingHarness(self)
                with h.held_service():
                    h.coordinator.bind_fixed_producer(h.fixed)
                    h.rpc._connection=h.held_rpc_connection
                    with h.inert_owner(),h.readiness():
                        finish_bootstrap(self,h)
                        getattr(h.producer,binding)['input_sha256']='0'*64
                        with self.assertRaisesRegex(ValueError,'rpc_phase_input_unknown'):
                            h.client.call('search',ARGS)
                        self.assertEqual(h.gate.snapshot()['dispatches'],0)

class EngineSourceTests(unittest.TestCase):
    def test_streaming_observation_preserves_digest_with_bounded_authored_reads(self):
        engine=engine_module()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.py';raw=('é😀 # synthetic\n'*3000).encode()
            path.write_bytes(raw)
            real=os.read;sizes=[]
            def read(fd,size):
                sizes.append(size)
                return real(fd,size)
            with patch('os.read',side_effect=read):
                actual=engine.observe_source_sha256(path)
            self.assertEqual(actual,hashlib.sha256(raw).hexdigest())
            self.assertLessEqual(max(sizes),8192)
            self.assertGreater(len(sizes),2)
            # This bounds authored os.read only, not compile/Unicode caches.

    def test_changed_file_remains_unknown_and_validated_bytes_keep_prior_semantics(self):
        engine=engine_module()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'memory.py';path.write_bytes(b'x'*20000)
            real=os.read;seen=[]
            def read(fd,size):
                data=real(fd,size)
                if not seen:
                    seen.append(True)
                    with path.open('ab') as out:out.write(b'changed')
                return data
            with patch('os.read',side_effect=read):
                self.assertIsNone(engine.observe_source_sha256(path))
            self.assertEqual(engine.observe_source_sha256(path,b'original'),
                             hashlib.sha256(b'original').hexdigest())
            with patch('os.open',side_effect=AssertionError('no fallback')):
                self.assertIsNone(engine.observe_source_sha256(path,'not bytes'))



class ComposedEntryTests(unittest.TestCase):
    def test_original_handle_over_bound_parent_client_and_persistent_state(self):
        from trial_sdk_control import SDKControl
        with tempfile.TemporaryDirectory() as directory:
            original=SDKControl.receive
            def receive(control,frame):
                request=frame.get('request',{})
                if request.get('subtype')=='hook_callback':
                    request['input'].update(cwd=directory,prompt='repair synthetic code')
                return original(control,frame)
            with patch.object(SDKControl,'receive',receive):
                h=ScopedBindingHarness(self,state_dir=str(Path(directory)/'state'))
            engine=engine_module()
            with h.held_service():
                h.coordinator.bind_fixed_producer(h.fixed)
                h.rpc._connection=h.held_rpc_connection
                with h.inert_owner(),h.readiness():
                    finish_bootstrap(self,h)
                    # Exact generated bootstrap, parsed by the paired entry.
                    raw=b''.join(canonical_chunks(h.producer._bootstrap))
                    document=read_document(io.BytesIO(raw),h.producer._transport['input_sha256'],
                                           h.producer._binding['deadline_ns'],clock=lambda:1)
                    result=invoke(engine,document,h.client)
                    self.assertIsInstance(result,dict)
                    self.assertEqual(h.gate.snapshot()['dispatches'],1)
                    state=json.loads((Path(directory)/'state'/
                        (document['input']['session_id']+'.json')).read_text())
                    self.assertIn('last_recall',state)
                    h.client.close()

    def complete_owned_producer(self,h):
        h.rpc._connection=h.held_rpc_connection
        finish_bootstrap(self,h)
        h.client.call('search',ARGS)
        h.client.close()
        h.producer._read('stdout');h.producer._read('stderr')
        with patch('os.waitpid',return_value=(123,0)):
            h.owner.observe_wait()
            self.assertEqual(h.producer.step(0),'ready')
        output=h.producer.take_output()
        self.assertIsNone(output.take())  # Actual zero stdout fixture, not native delivery.

    def test_source_owned_producer_graphs_end_before_existing_ticket_release(self):
        h=ScopedBindingHarness(self)
        with h.held_service():
            h.coordinator.bind_fixed_producer(h.fixed)
            with h.inert_owner(),h.readiness():
                self.complete_owned_producer(h)
                allocation=h.allocation
                h.rpc.retire()
                self.assertNotIn(allocation,h.resources._memory)
                self.assertEqual(h.fixed._producers,[])
                self.assertIsNone(h.producer._bootstrap)
                self.assertIsNone(h.producer._callback)
                self.assertEqual(h.producer._slabs,{})
                self.assertIsNone(h.rpc._phases)
        # Only these actual source-owned references ended. Other callers'
        # output/event/error aliases remain an explicit whole-composition duty.

    def test_interrupted_source_retirement_keeps_ticket_and_refuses_retry(self):
        h=ScopedBindingHarness(self)
        with h.held_service():
            h.coordinator.bind_fixed_producer(h.fixed)
            with h.inert_owner(),h.readiness():
                self.complete_owned_producer(h)
                with self.assertRaises(KeyboardInterrupt):
                    interrupt_line(FixedServiceLaunch.release_producer_sources,
                        'producer._slabs.clear()',h.rpc.retire)
                self.assertIn(h.allocation,h.resources._memory)
                self.assertTrue(h.rpc._unknown)
                with self.assertRaises(ValueError):h.rpc.retire()


if __name__=='__main__':unittest.main()
