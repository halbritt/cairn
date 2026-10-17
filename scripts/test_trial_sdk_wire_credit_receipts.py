"""Actual-count regression at fixed-credit accounting handoffs; no subject I/O."""
import sys
import unittest

from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_command_service_broker import WireCredit, ServiceLinkError
import trial_sdk_command_service_wire as wire
import test_trial_sdk_command_service_broker as fixtures


def guarded_body(wrapper):
    return next(cell.cell_contents for cell in wrapper.__closure__
                if callable(cell.cell_contents))


class WireReceiptTests(unittest.TestCase):
    def test_observed_prefix_survives_accounting_interruption_without_double_count(self):
        # 2026-10-17: a line fault after the mutation flag but before receipt
        # staging lost an actual32-byte transfer while freezing all64 bytes.
        for name in ('known', 'unknown_prefix', 'burst_known', 'burst_unknown_prefix'):
            with self.subTest(method=name):
                bank = AttemptResources()
                burst = name.startswith('burst_')
                maximum = 128 if burst else 64
                credit = WireCredit(bank, bank.local, maximum)
                promise = credit.burst((32, 32)) if burst else credit.promise(32)
                target = WireCredit._burst_prefix if burst else guarded_body(getattr(WireCredit, name))
                hits = []
                fault = KeyboardInterrupt()
                def trace(frame, event, argument):
                    if (event == 'line' and frame.f_code is target.__code__
                            and frame.f_locals.get('self') is credit
                            and credit._mutating and not hits):
                        hits.append(frame.f_lineno)
                        raise fault
                    return trace
                try:
                    sys.settrace(trace)
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        getattr(credit, name)(promise, 32)
                finally:
                    sys.settrace(None)
                self.assertIs(caught.exception, fault)
                self.assertEqual(len(hits), 1, 'required accounting fault missed')
                for _ in range(2):
                    state = credit.snapshot()
                    self.assertEqual(state['known'], 32)
                    self.assertTrue(state['unknown'])
                    self.assertEqual(bank.snapshot()['traffic_spent'], 32)
                    self.assertEqual(bank.snapshot()['traffic_reserved'], maximum - 32)
                    self.assertEqual(state['receipt_in_progress'], (32, 32))
                    credit._poison()  # Existing terminal observer may repeat.
                with self.assertRaisesRegex(ServiceLinkError, '^wire_credit_unknown$'):
                    credit.promise(32)

    def test_socket_receipt_covers_both_sides_of_credit_staging(self):
        for direction in ('send', 'receive', 'burst'):
          for phase in ('before_staging', 'staged', 'applied'):
            for fault in (KeyboardInterrupt(), BlockingIOError()):
              with self.subTest(direction=direction, phase=phase, fault=type(fault).__name__):
                fixture = fixtures.LinkTests()
                link = fixture.link()
                fixture.bound(link, fixture.endpoint(fixture.records((wire.READY,))))
                credit = link.credit(64)
                if direction == 'send':
                    fixture.ready(link)
                    link.queue(wire.SHUTDOWN, 0, credit)
                    step = link.send_step
                else:
                    if direction == 'burst':
                        token = credit.burst((32, 32))
                        link.adopt_receive_burst(credit, token)
                    else:
                        link.arm_receive(credit, 32)
                    step = lambda: link.receive_step(lambda frame: None)
                target = WireCredit._burst_prefix if direction == 'burst' else guarded_body(WireCredit.known)
                hits = []
                def trace(frame, event, argument):
                    if (event != 'line' or frame.f_code is not target.__code__
                            or frame.f_locals.get('self') is not credit or hits):
                        return trace
                    staged = credit._receipt is not None
                    selected = ((not staged and not credit._mutating) if phase == 'before_staging'
                                else staged and credit.snapshot()['known'] == (32 if phase == 'applied' else 0))
                    if selected:
                        hits.append(frame.f_lineno)
                        raise fault
                    return trace
                try:
                    sys.settrace(trace)
                    with self.assertRaises(type(fault)) as caught:
                        step()
                finally:
                    sys.settrace(None)
                self.assertIs(caught.exception, fault)
                self.assertEqual(len(hits), 1, 'required socket handoff fault missed')
                self.assertEqual(credit.snapshot()['known'], 32)
                self.assertEqual(link.resources.snapshot()['traffic_spent'], 32)
                self.assertEqual(link.resources.snapshot()['traffic_reserved'], 32)
                self.assertTrue(link.snapshot()['unknown'])
                self.assertTrue(credit.snapshot()['unknown'])


if __name__ == '__main__':
    unittest.main()
