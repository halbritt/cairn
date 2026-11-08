import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, '/work')
from effects import Action, EffectRunner, ExchangeResult, Intent


class Clock:
    def __init__(self): self.ns = 100_000_000_000
    def monotonic_ns(self): return self.ns
    def receipt(self): return {'mono_ns': self.ns, 'rt_ns': self.ns, 'epoch': 0}


class PositiveTransport:
    def __init__(self, clock): self.clock = clock; self.requests = []
    def exchange(self, request, budget_ns):
        self.requests.append(request)
        self.clock.ns += 100_000_000
        reply = {'schema': 'cairn.remote-effect-outcome/1', 'run_id': request['run_id'],
                 'effect_id': request['effect_id'], 'action': request['action'],
                 'binding_sha256': request['binding_sha256'], 'request_sha256': request['request_sha256'],
                 'outcome': 'APPLIED', 'executor_exit': 0}
        return ExchangeResult(json.dumps(reply).encode(), b'', 0, 'fixture-child', True)


class EffectTests(unittest.TestCase):
    def test_positive_effect_is_causal_once_and_does_not_claim_join(self):
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            clock = Clock(); transport = PositiveTransport(clock)
            intent = Intent(Path(tmp)/'one', 'fixture-run', 'a'*64)
            runner = EffectRunner(intent, transport, clock)
            result = runner.perform(Action.SIGNAL_MAIN, signal_deadline_ns=clock.ns+30_000_000_000, prelaunch=clock.receipt)
            self.assertEqual(result['status'], 'POSITIVE_OUTCOME')
            self.assertEqual(result['after']['rt_ns'], 100_100_000_000)
            self.assertTrue(runner.signalled)
            self.assertNotIn('normal_join', result)
            again = runner.perform(Action.SIGNAL_MAIN, signal_deadline_ns=clock.ns+30_000_000_000, prelaunch=clock.receipt)
            self.assertEqual(again['status'], 'STOP_UNCERTAIN')
            self.assertEqual(len(transport.requests), 1)


if __name__ == '__main__': unittest.main(verbosity=2)
