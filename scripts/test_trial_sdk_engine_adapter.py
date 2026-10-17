"""Exercise the adapter against the actual authored engine, without dispatch effects."""
import os
from pathlib import Path
import stat
import sys
import time
import types
import unittest

from trial_sdk_memory_gate import MemoryDispatchGate, MemoryGateError
from trial_sdk_engine_adapter import EngineMemoryAdapter, CommandOutcome, engine_context
from check_trial_sdk_native_process_owner import EXCLUDED


def engine_module():
    # Only the known tracked public Cairn engine; not the installed SDK or a
    # caller-selected path. Larger than the generic small-publication guard.
    path = Path(__file__).absolute().parents[1]/'integrations/lifecycle/memory.py'
    for part in (path,*path.parents):
        value=part.lstat()
        if stat.S_ISLNK(value.st_mode) or (value.st_dev,value.st_ino) in EXCLUDED:
            raise ValueError('test_engine_path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid()
                or not 0<before.st_size<=256*1024 or (before.st_dev,before.st_ino) in EXCLUDED):
            raise ValueError('test_engine_source')
        chunks=[]; count=0
        while count<before.st_size:
            part=os.read(fd,min(32768,before.st_size-count))
            if not part: raise ValueError('test_engine_changed')
            chunks.append(part); count+=len(part)
        after=os.fstat(fd)
        identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if os.read(fd,1) or identity(before)!=identity(after):
            raise ValueError('test_engine_changed')
    finally:
        os.close(fd)
    engine=types.ModuleType('cairn_gate_test_engine')
    engine.__file__=str(path)
    missing=object(); previous=sys.modules.get(engine.__name__,missing)
    sys.modules[engine.__name__]=engine
    try:
        exec(compile(b''.join(chunks),str(path),'exec'),engine.__dict__)
    finally:
        if previous is missing: del sys.modules[engine.__name__]
        else: sys.modules[engine.__name__]=previous
    return engine


class EngineAdapterTests(unittest.TestCase):
    def test_actual_memory_call_uses_gate_and_original_decoder(self):
        engine=engine_module()
        calls=[]
        def forward(request,deadline):
            calls.append(request)
            return CommandOutcome(0,br'{"ok":true,"data":{"marker":"caf\u00e9"}}',b'')
        gate=MemoryDispatchGate(forward,forward,time.monotonic_ns()+30_000_000_000)
        channel=gate.hook(time.monotonic_ns()+5_000_000_000)
        config=dict(cairn='/unavailable/cairn',socket='/unavailable/api.sock',
                    token_file='/unavailable/token',repo='/work',harness='claude',
                    recall_mode='agent_tools',recall_observations=False)
        original=(engine.run_json,engine.bounded_command)
        with EngineMemoryAdapter(engine,config,channel):
            memory=engine.Memory(config,'00000000-0000-4000-8000-000000000003')
            self.assertEqual(memory.call('pull',payload={'receipt_id':'new'})['marker'],'café')
        self.assertEqual((engine.run_json,engine.bounded_command),original)
        self.assertEqual(gate.snapshot()['expansions_debited'],1)
        self.assertEqual(calls[0].operation,'pull')
        self.assertEqual(calls[0].body,'{"receipt_id":"new"}')

    def test_caught_real_decoder_failure_cannot_qualify_callback(self):
        engine=engine_module()
        gate=MemoryDispatchGate(lambda *a:None,lambda *a:CommandOutcome(0,b'not JSON',b''),
                                time.monotonic_ns()+30_000_000_000)
        config=dict(cairn='/unavailable/cairn',socket='/unavailable/api.sock',
                    token_file='/unavailable/token',repo='/work',harness='claude',
                    recall_mode='agent_tools',recall_observations=False)
        adapter=EngineMemoryAdapter(engine,config,gate.hook(time.monotonic_ns()+5_000_000_000))
        with self.assertRaisesRegex(MemoryGateError,'^engine_result_failed$'):
            with adapter:
                try:
                    engine.Memory(config,'session').call('search')
                except engine.HookError:
                    pass
        self.assertEqual(gate.snapshot()['dispatches'],1)

    def test_caught_unconfirmed_api_envelope_retains_real_memory_error(self):
        engine=engine_module()
        gate=MemoryDispatchGate(lambda *a:None,
            lambda *a:CommandOutcome(0,b'{"ok":false,"status":"UNAVAILABLE"}',b''),
            time.monotonic_ns()+30_000_000_000)
        config=dict(cairn='/unavailable/cairn',socket='/unavailable/api.sock',
                    token_file='/unavailable/token',repo='/work',harness='claude',
                    recall_mode='agent_tools',recall_observations=False)
        with self.assertRaisesRegex(MemoryGateError,'^engine_operation_unconfirmed$'):
            with EngineMemoryAdapter(engine,config,gate.hook(time.monotonic_ns()+5_000_000_000)):
                with self.assertRaises(engine.HookError) as observed:
                    engine.Memory(config,'session').call('search')
                self.assertEqual(observed.exception.reason,'NOT_CONFIRMED')
        self.assertEqual(gate.snapshot()['returned'],1)

    def test_unsupported_command_and_write_refusals_are_sticky_before_forward(self):
        for operation in ('selector','create','revise','recall-observation'):
            engine=engine_module();calls=[]
            gate=MemoryDispatchGate(lambda *a:calls.append(a),lambda *a:calls.append(a),
                                    time.monotonic_ns()+30_000_000_000)
            config=dict(cairn='/unavailable/cairn',socket='/unavailable/api.sock',
                        token_file='/unavailable/token',repo='/work',harness='claude',
                        recall_mode='agent_tools',recall_observations=False)
            original=(engine.run_json,engine.bounded_command)
            adapter=EngineMemoryAdapter(engine,config,gate.hook(time.monotonic_ns()+5_000_000_000))
            with self.assertRaisesRegex(MemoryGateError,'^engine_dispatch_(route|operation)$'):
                with adapter:
                    try:
                        if operation=='selector':
                            engine.bounded_command(['/unavailable/claude'],body='{}',timeout=1,
                                                   env={},cwd=None)
                        else: engine.Memory(config,'session').call(operation)
                    except engine.HookError: pass
            self.assertEqual(calls,[])
            self.assertEqual((engine.run_json,engine.bounded_command),original)
            self.assertEqual(gate.snapshot()['dispatches'],0)

    def test_actual_decoder_budget_refusal_and_utf8_error_mapping(self):
        for outcome,reason in [(CommandOutcome(1,b'{"status":"BUDGET_REFUSED"}',b''),None),
                               (CommandOutcome(0,bytes([255]),b''),'INVALID_JSON')]:
            engine=engine_module()
            gate=MemoryDispatchGate(lambda *a:None,lambda *a:outcome,
                                    time.monotonic_ns()+30_000_000_000)
            config=dict(cairn='/unavailable/cairn',socket='/unavailable/api.sock',
                        token_file='/unavailable/token',repo='/work',harness='claude',
                        recall_mode='agent_tools',recall_observations=False)
            with self.assertRaisesRegex(MemoryGateError,'^engine_result_failed$'):
                with EngineMemoryAdapter(engine,config,gate.hook(time.monotonic_ns()+5_000_000_000)):
                    with self.assertRaises(engine.HookError) as observed:
                        engine.Memory(config,'session').call('pull')
                    if reason is None: self.assertIs(type(observed.exception),engine.BudgetRefused)
                    else: self.assertEqual(observed.exception.reason,reason)
            self.assertEqual(gate.snapshot()['expansions_debited'],1)

    def test_engine_context_is_event_specific_and_empty_is_not_delivery(self):
        self.assertEqual(engine_context({},'PostToolUse'),'')
        self.assertEqual(engine_context({'hookSpecificOutput':{'hookEventName':'SessionStart',
                         'additionalContext':'café 😀'}},'SessionStart'),'café 😀')
        for graph,event in [(None,'SessionStart'),({'continue':False},'SessionStart'),
                ({'hookSpecificOutput':{'hookEventName':'UserPromptSubmit','additionalContext':'x'}},'SessionStart'),
                ({'hookSpecificOutput':{'hookEventName':'PostToolUse','additionalContext':'x'}},'PostToolUse'),
                ({'hookSpecificOutput':{'hookEventName':'SessionStart','additionalContext':None}},'SessionStart')]:
            with self.assertRaisesRegex(MemoryGateError,'^engine_context_shape$'):
                engine_context(graph,event)

    def test_interrupted_restore_preserves_body_primary_and_marks_unknown(self):
        engine=engine_module()
        gate=MemoryDispatchGate(lambda *a:None,lambda *a:None,
                                time.monotonic_ns()+30_000_000_000)
        config=dict(cairn='/unavailable/cairn',socket='/unavailable/api.sock',
                    token_file='/unavailable/token',repo='/work',harness='claude',
                    recall_mode='agent_tools',recall_observations=False)
        adapter=EngineMemoryAdapter(engine,config,gate.hook(time.monotonic_ns()+5_000_000_000))
        original=(engine.run_json,engine.bounded_command)
        injected=[]
        def trace(frame,event,arg):
            if (frame.f_code is EngineMemoryAdapter.close.__code__ and event=='line'
                    and engine.run_json is original[0] and engine.bounded_command is not original[1]):
                sys.settrace(None);injected.append(True)
                raise KeyboardInterrupt('restore boundary interruption')
            return trace
        previous=sys.gettrace()
        try:
            with self.assertRaisesRegex(ValueError,'^body primary$'):
                with adapter:
                    sys.settrace(trace)
                    raise ValueError('body primary')
        finally:
            sys.settrace(previous)
        self.assertEqual(injected,[True])
        self.assertEqual(adapter.snapshot()['failure'],'engine_restore_unknown')
        self.assertNotEqual(adapter.snapshot()['restoration']['bounded_command'],'restored')
        with self.assertRaisesRegex(MemoryGateError,'^engine_restore_unknown$'): adapter.check()

    def test_direct_transport_utf8_failure_is_sticky_even_if_caught(self):
        engine=engine_module()
        gate=MemoryDispatchGate(lambda *a:None,lambda *a:CommandOutcome(0,bytes([255]),b''),
                                time.monotonic_ns()+30_000_000_000)
        config=dict(cairn='/unavailable/cairn',socket='/unavailable/api.sock',
                    token_file='/unavailable/token',repo='/work',harness='claude',
                    recall_mode='agent_tools',recall_observations=False)
        with self.assertRaisesRegex(MemoryGateError,'^engine_result_failed$'):
            with EngineMemoryAdapter(engine,config,gate.hook(time.monotonic_ns()+5_000_000_000)):
                memory=engine.Memory(config,'session')
                with self.assertRaises(UnicodeError):
                    engine.bounded_command(memory.command+['search'],body=None,timeout=1,
                                           env={},cwd=None)


if __name__ == '__main__': unittest.main()
