"""Incremental selected-input accounting and borrowed-input lifetime."""
import hashlib
import json
import unittest
import weakref

from trial_native_observation import LineMeasurement


class BorrowedLine(str):
    pass


class BorrowedTiming(dict):
    pass


class IncrementalObservationTests(unittest.TestCase):
    def observer(self):
        return LineMeasurement(input_bytes=500, expects_hooks=False, model='fixture', prompt='task')

    def send(self, observer, event, seconds=1):
        raw = json.dumps(event, ensure_ascii=False) + '\n'
        observer.consume(raw, dict(sha256=hashlib.sha256(raw.encode()).hexdigest(), received_seconds=seconds))

    def test_late_repeated_error_results_keep_full_charge(self):
        observer = self.observer()
        self.send(observer, dict(type='system', subtype='init', model='fixture'))
        self.send(observer, dict(type='user', message=dict(content='task')))
        self.send(observer, dict(type='assistant', message=dict(content=[dict(
            type='tool_use', id='call', name='mcp__cairn__cairn_pull', input={})])))
        result = dict(type='tool_result', tool_use_id='call', is_error=True, content='é' * 2300)
        for seconds in (29, 31):
            self.send(observer, dict(type='user', message=dict(content=[result])), seconds)
        self.send(observer, dict(type='result', permission_denials=[]), 32)
        observed = observer.finish(())
        charge = 2 * len(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode())
        self.assertEqual(observed['total_bytes'], 500 + charge)
        self.assertEqual(observed['last_native_memory_seconds'], 31)
        self.assertIn('selected_input_exceeded_9500_bytes', observed['failures'])
        self.assertIn('last_memory_exceeded_30_seconds', observed['failures'])
        for action in (lambda: observer.finish(()), lambda: self.send(observer, {})):
            with self.assertRaisesRegex(ValueError, 'measurement_consumed'):
                action()

    def test_consumed_line_and_timing_are_not_retained_between_events(self):
        observer = self.observer()
        raw = BorrowedLine('{"type":"system","subtype":"init","model":"fixture"}\n')
        timing = BorrowedTiming(sha256=hashlib.sha256(raw.encode()).hexdigest(), received_seconds=1)
        refs = weakref.ref(raw), weakref.ref(timing)
        observer.consume(raw, timing)
        del raw, timing
        self.assertEqual([ref() for ref in refs], [None, None])
        observer.close()

    def test_failed_consume_cannot_be_retried_or_finished(self):
        observer = self.observer()
        with self.assertRaises(UnicodeEncodeError):
            observer.consume('\ud800', {})
        for action in (lambda: observer.finish(()), lambda: self.send(observer, {})):
            with self.assertRaisesRegex(ValueError, 'measurement_consumed'):
                action()
        observer.close()  # Cleanup remains idempotent after a consumed failure.
