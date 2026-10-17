"""Actual authored engine decoder; inert text outcomes are not command waits."""
import time
import unittest

from test_trial_sdk_engine_adapter import engine_module
from trial_sdk_engine_adapter import EngineMemoryAdapter, CommandTextOutcome
from trial_sdk_memory_gate import MemoryDispatchGate, MemoryGateError


class CommandTextTests(unittest.TestCase):
    def run_engine(self,outcome,operation='search'):
        engine=engine_module()
        gate=MemoryDispatchGate(lambda *a:None,lambda *a:outcome,
                                time.monotonic_ns()+30_000_000_000)
        config=dict(cairn='/unavailable/cairn',socket='/unavailable/api.sock',
                    token_file='/unavailable/token',repo='/work',harness='claude',
                    recall_mode='agent_tools',recall_observations=False)
        adapter=EngineMemoryAdapter(engine,config,gate.hook(time.monotonic_ns()+5_000_000_000))
        return engine,gate,config,adapter

    def test_full_per_stream_ceiling_keeps_real_json_decoder(self):
        prefix='{"ok":true,"data":{"text":"'
        suffix='"}}'
        text=prefix+'a'*(1024*1024-len(prefix)-len(suffix))+suffix
        value=CommandTextOutcome(0,text,'\ufffd'*(1024*1024),1024*1024,1024*1024)
        engine,gate,config,adapter=self.run_engine(value)
        with adapter:
            data=engine.Memory(config,'session').call('search')
        self.assertEqual(len(data['text']),1024*1024-len(prefix)-len(suffix))
        self.assertEqual(gate.snapshot()['returned'],1)

    def test_multibyte_stdout_and_replaced_stderr_keep_distinct_raw_counts(self):
        text='{"ok":true,"data":{"text":"\U0001f4a1"}}'
        value=CommandTextOutcome(0,text,'\ufffd\ufffd',len(text.encode('utf-8')),2)
        engine,gate,config,adapter=self.run_engine(value)
        with adapter:
            self.assertEqual(engine.Memory(config,'session').call('search')['text'],'\U0001f4a1')

    def test_nonzero_budget_refusal_uses_original_error(self):
        text='{"status":"BUDGET_REFUSED"}'
        value=CommandTextOutcome(17,text,'\ufffd',len(text),1)
        engine,gate,config,adapter=self.run_engine(value,'pull')
        with self.assertRaisesRegex(MemoryGateError,'^engine_result_failed$'):
            with adapter:
                with self.assertRaises(engine.BudgetRefused):
                    engine.Memory(config,'session').call('pull')
        self.assertEqual(gate.snapshot()['expansions_debited'],1)

    def test_forged_text_metadata_and_surrogates_refuse(self):
        cases=[CommandTextOutcome(0,'\u00e9','',1,0),
               CommandTextOutcome(0,'','x',0,0),
               CommandTextOutcome(0,'','',True,0),
               CommandTextOutcome(0,'\ud800','',3,0),
               CommandTextOutcome(0,'','\udfff',0,1),
               CommandTextOutcome(0,'','',0,1024*1024+1)]
        for value in cases:
            with self.subTest(value=value):
                engine,gate,config,adapter=self.run_engine(value)
                with self.assertRaisesRegex(MemoryGateError,'^engine_command_outcome$'):
                    with adapter:
                        with self.assertRaises(engine.HookError):
                            engine.Memory(config,'session').call('search')
                self.assertEqual(gate.snapshot()['returned'],1)


if __name__=='__main__':unittest.main()
