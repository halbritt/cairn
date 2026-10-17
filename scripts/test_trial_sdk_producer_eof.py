"""Exhausted-credit EOF uses real owner logic with inert syscall boundaries."""
import select
import struct
import unittest
from unittest.mock import patch

from trial_sdk_attempt_resources import TRAFFIC
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_producer_resources import ProducerResourceError
from test_trial_sdk_command_input import process_boundaries
import test_trial_sdk_producer_resources as resource_tests


class Poll:
    def __init__(self, flags):
        self.flags = flags

    def register(self, fd, events):
        self.fd = fd

    def poll(self, timeout):
        return [(self.fd, self.flags)] if self.flags else []


class ExhaustedEOF(unittest.TestCase):
    def exhausted(self):
        b = resource_tests.ResourcePorts().binding()
        b.claim()
        b.owner.start(('/inert',), '/inert', {})
        with patch('os.readv', return_value=1), memoryview(bytearray(1)) as slab:
            self.assertEqual(b.read_into('stderr', slab), 1)
        # Seed the other domain's known traffic at its exact original boundary;
        # no claim of having transferred four MiB through a real process.
        other = b.resources.bind(NativeProcessOwner())
        ticket = b.resources.stream_grant(other, TRAFFIC - 1)
        b.resources.settle_stream(ticket, TRAFFIC - 1)
        self.assertTrue(b.can_probe_eof())
        return b

    @staticmethod
    def queued(count):
        def ioctl(fd, operation, buffer, mutate):
            struct.pack_into('i', buffer, 0, count)
            return 0
        return ioctl

    def test_live_empty_is_pending_without_read_or_new_credit(self):
        with process_boundaries():
            b = self.exhausted()
            before = b.resources.snapshot()
            receipt = b.owner.io_receipt()
            with patch('select.poll', return_value=Poll(0)), \
                    patch('fcntl.ioctl', side_effect=self.queued(0)), \
                    patch('os.readv', side_effect=AssertionError('live-empty read')):
                with memoryview(bytearray(8)) as slab:
                    self.assertEqual(b.read_into('stdout', slab), -1)
            self.assertEqual(b.resources.snapshot(), before)
            self.assertEqual(b.owner.io_receipt(), receipt)
            self.assertFalse(b.owner.snapshot()['stdout_eof'])

    def test_drained_hangup_observes_zero_once_without_budget_change(self):
        with process_boundaries():
            b = self.exhausted()
            before = b.resources.snapshot()
            with patch('select.poll', return_value=Poll(select.POLLHUP)), \
                    patch('fcntl.ioctl', side_effect=self.queued(0)), \
                    patch('os.readv', return_value=0) as read:
                with memoryview(bytearray(8)) as slab:
                    self.assertEqual(b.read_into('stdout', slab), 0)
                    self.assertEqual(b.read_into('stdout', slab), 0)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(b.resources.snapshot(), before)
            self.assertEqual(b.snapshot()['last_probe_receipt'],
                             (2, 'stdout', 1, True, 0, False))
            self.assertEqual(b.snapshot()['unfunded_probe_bytes'], 0)

    def test_queued_bytes_refuse_without_consumption_or_capacity_growth(self):
        with process_boundaries():
            b = self.exhausted()
            with patch('select.poll', return_value=Poll(select.POLLHUP)), \
                    patch('fcntl.ioctl', side_effect=self.queued(1)), \
                    patch('os.readv', side_effect=AssertionError('over-budget read')):
                with memoryview(bytearray(8)) as slab:
                    with self.assertRaisesRegex(ProducerResourceError, 'producer_traffic_exhausted'):
                        b.read_into('stdout', slab)
            self.assertTrue(b.resources.snapshot()['unknown'])
            self.assertEqual(b.resources.snapshot()['traffic_spent'], TRAFFIC)
            self.assertEqual(b.snapshot()['unfunded_probe_bytes'], 0)
            self.assertFalse(b.owner.snapshot()['stdout_eof'])

    def test_impossible_positive_probe_is_retained_unfunded_and_never_retried(self):
        with process_boundaries():
            b = self.exhausted()
            with patch('select.poll', return_value=Poll(select.POLLHUP)), \
                    patch('fcntl.ioctl', side_effect=self.queued(0)), \
                    patch('os.readv', return_value=1) as read:
                with memoryview(bytearray(8)) as slab:
                    with self.assertRaisesRegex(ValueError, 'owner_output_probe_positive'):
                        b.read_into('stdout', slab)
                    with self.assertRaises(ProducerResourceError):
                        b.read_into('stdout', slab)
            self.assertEqual(read.call_count, 1)
            self.assertTrue(b.resources.snapshot()['unknown'])
            self.assertEqual(b.resources.snapshot()['traffic_spent'], TRAFFIC)
            self.assertEqual(b.snapshot()['unfunded_probe_bytes'], 1)
            self.assertEqual(b.snapshot()['last_probe_receipt'][1:5], ('stdout', 1, True, 1))

    def test_actual_zero_with_close_failure_retains_unknown_and_does_not_retry_close(self):
        with process_boundaries():
            b = self.exhausted()
            with patch('select.poll', return_value=Poll(select.POLLHUP)), \
                    patch('fcntl.ioctl', side_effect=self.queued(0)), \
                    patch('os.readv', return_value=0), \
                    patch('os.close', side_effect=OSError('close')) as close:
                with memoryview(bytearray(8)) as slab:
                    with self.assertRaises(OSError):
                        b.read_into('stdout', slab)
                    with self.assertRaises(ProducerResourceError):
                        b.read_into('stdout', slab)
            self.assertEqual(close.call_count, 1)
            self.assertTrue(b.resources.snapshot()['unknown'])
            self.assertEqual(b.snapshot()['last_probe_receipt'],
                             (2, 'stdout', 1, True, 0, True))

    def test_public_driver_finishes_at_exact_cap_with_real_zero_eof_receipts(self):
        with process_boundaries():
            fixture = resource_tests.ResourcePorts()
            b = fixture.binding()
            driver = fixture.driver(b)
            required = driver.snapshot()['input_bytes']
            other = b.resources.bind(NativeProcessOwner())
            ticket = b.resources.stream_grant(other, TRAFFIC - required)
            b.resources.settle_stream(ticket, TRAFFIC - required)
            b.owner.start(('/inert',), '/inert', {})
            widths = []
            def write(fd, body):
                widths.append(len(body))
                return len(body)
            def wait(pid, flags):
                return (123, 0) if b.owner.snapshot()['stdin_closed'] else (0, 0)
            def ready(reads, writes, exceptional, timeout):
                if writes:
                    return [], writes, []
                return [fd for fd in reads if fd != 737], [], []
            with patch('os.write', side_effect=write), patch('os.waitpid', side_effect=wait), \
                    patch('os.readv', return_value=0) as read, \
                    patch('time.monotonic_ns', return_value=1), \
                    patch('select.select', side_effect=ready), \
                    patch('select.poll', side_effect=lambda: Poll(select.POLLHUP)), \
                    patch('fcntl.ioctl', side_effect=self.queued(0)):
                for _ in range(1024):
                    if driver.step(0) == 'ready':
                        break
                else:
                    self.fail('bounded driver never completed')
                self.assertIsNone(driver.take_output().take())
            state = driver.snapshot()
            self.assertEqual(sum(widths), required)
            self.assertTrue(all(0 < width <= 512 for width in widths))
            self.assertEqual(read.call_count, 2)
            self.assertTrue(state['stdout_eof'] and state['stderr_eof'])
            self.assertEqual(state['wait']['returncode'], 0)
            self.assertTrue(state['release']['released'])
            self.assertEqual(b.resources.snapshot()['traffic_available'], 0)
            self.assertEqual(b.stream.snapshot()['known'], required)
            self.assertFalse(b.resources.snapshot()['unknown'])
            self.assertFalse(b.stream.snapshot()['closed'])


if __name__ == '__main__':
    unittest.main()
