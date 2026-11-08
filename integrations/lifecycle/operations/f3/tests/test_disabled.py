from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path[:0] = ['/work', '/work/tests']
import runner
from effects import Action, EffectRunner, Intent, SECOND
from test_effects import Clock, PositiveTransport


class DisabledTests(unittest.TestCase):
    def test_entry_refuses_without_importing_live_ports_or_creating_intent(self):
        output=io.StringIO()
        with redirect_stdout(output): self.assertEqual(runner.main(),2)
        value=json.loads(output.getvalue())
        self.assertEqual(value['status'],'REFUSED_UNBOUND')
        self.assertEqual(value['effects_launched'],0)
        self.assertFalse(value['readiness_or_adoption'])

    def test_exclusive_intent_refuses_existing_directory_without_replay(self):
        with tempfile.TemporaryDirectory() as base:
            path=Path(base)/'intent'; Intent(path,'fixture','a'*64)
            before={p.name:p.read_bytes() for p in path.iterdir()}
            with self.assertRaises(FileExistsError): Intent(path,'fixture','a'*64)
            self.assertEqual(before,{p.name:p.read_bytes() for p in path.iterdir()})

    def test_durable_pre_send_time_is_inside_the_same_wait_budget(self):
        with tempfile.TemporaryDirectory() as base:
            clock=Clock(); transport=PositiveTransport(clock); intent=Intent(Path(base)/'intent','fixture','a'*64)
            write=intent.write
            def slow(kind,value):
                write(kind,value)
                if kind=='effect-requested':clock.ns+=2*SECOND
            intent.write=slow
            engine=EffectRunner(intent,transport,clock)
            result=engine.perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND, prelaunch=clock.receipt)
            self.assertEqual(result['status'],'STOP_UNCERTAIN')
            self.assertFalse(result['signalled'])
            self.assertEqual(transport.requests,[])

    def test_receipt_failure_retains_stop_and_never_allows_another_effect(self):
        for failed_kind, launched in (('effect-requested',0),('positive-outcome',1)):
            with self.subTest(failed_kind=failed_kind), tempfile.TemporaryDirectory() as base:
                clock=Clock(); transport=PositiveTransport(clock); intent=Intent(Path(base)/'intent','fixture','a'*64)
                write=intent.write
                def broken(kind,value):
                    if kind==failed_kind: raise OSError('fixture storage unavailable')
                    write(kind,value)
                intent.write=broken
                engine=EffectRunner(intent,transport,clock)
                result=engine.perform(Action.SIGNAL_MAIN,signal_deadline_ns=clock.ns+30*SECOND, prelaunch=clock.receipt)
                self.assertEqual(result['status'],'STOP_UNCERTAIN')
                engine.perform(Action.START_SELECTED)
                self.assertTrue(engine.stopped)
                self.assertEqual(len(transport.requests),launched)


if __name__=='__main__':unittest.main(verbosity=2)
