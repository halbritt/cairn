"""Pure producer binding/state checks; no child or model launch."""
import hashlib
import json
import os
import time
import unittest

from trial_sdk_control import SDKControl
from trial_sdk_stream_driver import StreamEvent
from trial_sdk_producer_driver import SDKProducerDriver, ProducerDriverError


def callback_event(extra='Full callback content: café 😀'):
    session = '00000000-0000-4000-8000-000000000003'
    control = SDKControl(session,
        b'{"type":"user","message":{"role":"user","content":"Frozen task."}}\n', 31)
    wire = control.pending_write()
    control.written(len(wire))
    control.receive(dict(type='control_response', response=dict(subtype='success',
        request_id=control.bindings['initialize_request_id'], response={})))
    wire = control.pending_write()
    control.written(len(wire))
    callback = control.receive(dict(type='control_request', request_id='producer-0',
        request=dict(subtype='hook_callback',
            callback_id=control.bindings['callback_ids']['SessionStart'],
            input=dict(hook_event_name='SessionStart', session_id=session, extra=extra))))
    first = time.monotonic_ns()
    observation = dict(request_sha256=hashlib.sha256(callback.request_id.encode()).hexdigest(),
        first_byte_ns=first, deadline_ns=first + 5_000_000_000)
    return control, StreamEvent('callback', callback=callback, observation=observation)


class ProducerDriverTests(unittest.TestCase):
    def test_original_callback_binding_fresh_owner_and_unlaunched_cancel(self):
        before = set(os.listdir('/proc/self/fd'))
        control, event = callback_event()
        self.addCleanup(control.close)
        driver = SDKProducerDriver(event)
        self.addCleanup(driver.native_owner.cleanup_unlaunched)
        self.assertIsNone(driver.native_owner.snapshot()['pid'])
        state = driver.snapshot()
        self.assertEqual(state['input_bytes'], event.callback.input_bytes)
        self.assertEqual(state['input_sha256'], event.callback.input_sha256)
        self.assertEqual(state['input_generated_bytes'], 0)
        self.assertEqual(state['input_written_bytes'], 0)
        self.assertIsNone(state['memory_call_attempts'])
        self.assertFalse(state['memory_call_measurement_attested'])
        self.assertIsNone(state['wait'])
        with self.assertRaisesRegex(ProducerDriverError, '^sdk_producer_output_pending$'):
            driver.take_output()
        driver.cancel()
        driver.cancel()
        with self.assertRaisesRegex(ProducerDriverError, '^sdk_producer_unavailable$'):
            driver.step(0)
        driver.native_owner.cleanup_unlaunched()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_only_callback_event_and_original_deadline_shape_are_accepted(self):
        control, event = callback_event()
        self.addCleanup(control.close)
        bad = [None, event.callback, StreamEvent('document', callback=event.callback,
            observation=event.observation)]
        for changes in ({'deadline_ns':event.observation['deadline_ns']+1},
                        {'request_sha256':'0'*64}, {'first_byte_ns':True}):
            bad.append(StreamEvent('callback', callback=event.callback,
                                   observation=event.observation | changes))
        for value in bad:
            with self.subTest(kind=type(value).__name__):
                with self.assertRaisesRegex(ProducerDriverError, '^sdk_producer_binding$'):
                    SDKProducerDriver(value)

    def test_expired_first_read_deadline_cannot_be_restarted_by_construction(self):
        control, event = callback_event()
        self.addCleanup(control.close)
        first = time.monotonic_ns() - 5_000_000_001
        driver = SDKProducerDriver(StreamEvent('callback', callback=event.callback,
            observation=event.observation | dict(first_byte_ns=first, deadline_ns=first+5_000_000_000)))
        self.addCleanup(driver.native_owner.cleanup_unlaunched)
        with self.assertRaisesRegex(ProducerDriverError, '^sdk_producer_deadline$'):
            driver.step(0)
        self.assertEqual(driver.snapshot()['failure'], 'sdk_producer_deadline')
        self.assertIsNone(driver.snapshot()['wait'])
        self.assertEqual(driver.snapshot()['input_written_bytes'], 0)
        with self.assertRaisesRegex(ProducerDriverError, '^sdk_producer_unavailable$'):
            driver.step(0)


if __name__ == '__main__':
    unittest.main()
