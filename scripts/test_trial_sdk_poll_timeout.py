"""A poll cap changes waiting only; no child or deadline assertion is supplied."""
import unittest

from trial_sdk_control import SDKControl
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_stream_driver import SDKStreamDriver, StreamDriverError


class PollTimeoutTests(unittest.TestCase):
    def pump(self):
        control = SDKControl('00000000-0000-4000-8000-000000000003',
            b'{"type":"user","message":{"role":"user","content":"Frozen task."}}\n',31)
        owner = NativeProcessOwner()
        pump = SDKStreamDriver(control,owner)
        self.addCleanup(control.close)
        self.addCleanup(owner.cleanup_unlaunched)
        self.addCleanup(pump.cancel)
        return pump

    def test_zero_poll_is_valid_and_does_not_authorize_unlaunched_owner(self):
        pump = self.pump()
        with self.assertRaisesRegex(StreamDriverError,'^sdk_stream_native_unavailable$'):
            pump.step(0)

    def test_invalid_poll_caps_refuse_before_waiting(self):
        for timeout in (-1,.050001,float('nan'),float('inf'),True,None):
            with self.subTest(timeout=timeout):
                pump = self.pump()
                with self.assertRaisesRegex(StreamDriverError,'^sdk_stream_poll_bounds$'):
                    pump.step(timeout)
                self.assertIsNotNone(pump.snapshot()['failure'])


if __name__ == '__main__':
    unittest.main()
