import dataclasses
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
sys.path[:0] = ['/work', '/work/tests']
from effects import Action, EffectRunner, ExchangeResult, ExchangeUnknown, Intent, SECOND, canonical
from transport import ChildTransport
from test_effects import Clock, PositiveTransport


class FaultTransport(PositiveTransport):
    def __init__(self, clock, fault): super().__init__(clock); self.fault = fault
    def exchange(self, request, budget_ns):
        result = super().exchange(request, budget_ns)
        if self.fault in ('lost-after-actuation', 'remote-suspend', 'missing'):
            raise ExchangeUnknown(self.fault, 'fixture-child-retained')
        if self.fault in ('late-before-actuation', 'late-after-actuation'):
            self.clock.ns += 2*SECOND
        if self.fault == 'nonzero': return dataclasses.replace(result, returncode=1)
        if self.fault == 'eof-before-reap': return dataclasses.replace(result, returncode=None)
        if self.fault == 'partial': return dataclasses.replace(result, stdout=result.stdout[:20])
        if self.fault == 'duplicate': return dataclasses.replace(result, stdout=result.stdout+result.stdout)
        if self.fault == 'mismatch': return dataclasses.replace(result, stdout=result.stdout.replace(b'fixture-run', b'foreign-run'))
        if self.fault == 'duplicate-key': return dataclasses.replace(result, stdout=b'{"outcome":"APPLIED",'+result.stdout[1:])
        if self.fault == 'stderr': return dataclasses.replace(result, stderr=b'opaque private output')
        if self.fault == 'oversize': return dataclasses.replace(result, stdout=b'x'*8193)
        return result


class FixedDummyLauncher:
    def __init__(self, mode): self.mode = mode; self.count = 0
    def launch(self, request):
        self.count += 1
        return subprocess.Popen(['/usr/bin/python3', '-I', '-B', '/work/tests/dummy.py', self.mode],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


class TransportTests(unittest.TestCase):
    def test_all_uncertain_outcomes_stop_without_second_session(self):
        for fault in ('lost-after-actuation','remote-suspend','missing','late-before-actuation','late-after-actuation',
                      'nonzero','eof-before-reap','partial','duplicate','mismatch','duplicate-key','stderr','oversize'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as base:
                clock = Clock(); transport = FaultTransport(clock, fault)
                engine = EffectRunner(Intent(Path(base)/'intent', 'fixture-run', 'a'*64), transport, clock)
                result = engine.perform(Action.SIGNAL_MAIN, signal_deadline_ns=clock.ns+30*SECOND, prelaunch=clock.receipt)
                self.assertEqual(result['status'], 'STOP_UNCERTAIN')
                self.assertTrue(result['signalled']); self.assertTrue(result['start_blocking'])
                engine.perform(Action.START_SELECTED)
                self.assertEqual(len(transport.requests), 1)
                self.assertNotIn('opaque private', json.dumps(engine.outcomes))

    def test_dummy_positive_is_reaped_and_causally_valid(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as base:
            launcher = FixedDummyLauncher('positive')
            transport = ChildTransport(time.monotonic_ns, launcher)
            engine = EffectRunner(Intent(Path(base)/'intent', 'fixture-run', 'a'*64), transport, clock)
            result = engine.perform(Action.SIGNAL_MAIN, signal_deadline_ns=clock.ns+30*SECOND, prelaunch=clock.receipt)
            self.assertEqual(result['status'], 'POSITIVE_OUTCOME')
            self.assertEqual(launcher.count, 1)
            self.assertTrue(all(c.returncode == 0 for c in transport.children.values()))
            self.assertFalse(result['remote_descendant_terminal_guarantee'])

    def test_owned_dummy_can_outlive_wait_even_after_reply_or_EOF(self):
        for mode in ('delay-before-reply', 'delay-after-reply', 'eof-before-reap'):
            with self.subTest(mode=mode):
                launcher = FixedDummyLauncher(mode); transport = ChildTransport(time.monotonic_ns, launcher)
                request = {'run_id':'fixture-run','effect_id':'one','action':Action.SIGNAL_MAIN.value,
                           'binding_sha256':'a'*64,'request_sha256':'b'*64}
                with self.assertRaises(ExchangeUnknown): transport.exchange(request, 30_000_000)
                self.assertEqual(launcher.count, 1)
                child = next(iter(transport.children.values()))
                self.assertIsNone(child.returncode)
                with self.assertRaises(ExchangeUnknown): transport.exchange(request, SECOND)
                self.assertEqual(launcher.count, 1)
                # Fixture-owned child finishes naturally; no kill or signal, no effect reconciliation.
                child.communicate(timeout=2)
                self.assertEqual(child.returncode, 0)

    def test_default_launcher_refuses(self):
        transport = ChildTransport(lambda: 1)
        with self.assertRaises(ExchangeUnknown):
            transport.exchange({'run_id':'fixture','effect_id':'one','action':Action.START_SELECTED.value}, SECOND)
        self.assertEqual(transport.children, {})


if __name__ == '__main__': unittest.main(verbosity=2)
