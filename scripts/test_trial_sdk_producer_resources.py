"""Original ledger and real native receipts; syscall boundaries are inert."""
import unittest
from unittest.mock import patch

from trial_sdk_attempt_resources import AttemptResources, TRAFFIC
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_producer_driver import SDKProducerDriver
from trial_sdk_producer_resources import ProducerResources, ProducerResourceError
from trial_sdk_stream_credit import StreamCredit
from test_trial_sdk_command_input import process_boundaries
from test_trial_sdk_producer_driver import callback_event
from test_trial_sdk_io_receipts import after_released_loan
from test_trial_sdk_engine_memory_rpc_parent import Harness


class ResourcePorts(unittest.TestCase):
    def binding(self):
        bank = AttemptResources()
        owner = NativeProcessOwner()
        domain = bank.bind(owner)
        allocation = bank.allocate(domain, 'operation', 65536, 128 * 1024 * 1024)
        stream = StreamCredit(bank, domain, 1)
        return ProducerResources(bank, owner, domain, allocation, stream)

    def driver(self, binding):
        with patch('time.monotonic_ns', return_value=1):
            _, event = callback_event()
        return SDKProducerDriver.from_resources(event, binding)

    def test_constructs_with_exact_preheld_owner_and_allocation(self):
        binding = self.binding()
        before = binding.resources.snapshot()
        driver = self.driver(binding)
        self.assertIs(driver.native_owner, binding.owner)
        self.assertEqual(binding.resources.snapshot(), before)
        self.assertTrue(driver.snapshot()['resource_binding']['claimed'])
        with self.assertRaises(ProducerResourceError):
            self.driver(binding)
        self.assertTrue(binding.resources.snapshot()['unknown'])

    def test_foreign_owner_allocation_and_stream_cannot_bind(self):
        for changed in ('owner', 'allocation', 'stream'):
            with self.subTest(changed=changed):
                b = self.binding()
                other = NativeProcessOwner()
                domain = b.resources.bind(other)
                allocation = b.resources.allocate(domain, 'operation', 30000, 1024)
                stream = StreamCredit(b.resources, domain, 1)
                args = [b.resources, b.owner, b.domain, b.allocation, b.stream]
                args[{'owner': 1, 'allocation': 3, 'stream': 4}[changed]] = {
                    'owner': other, 'allocation': allocation, 'stream': stream}[changed]
                with self.assertRaises(ProducerResourceError):
                    ProducerResources(*args)
                self.assertFalse(b.resources.snapshot()['unknown'])
                self.assertFalse(b.owner.snapshot()['launch_attempted'])

    def test_small_remaining_original_credit_caps_actual_read_and_write(self):
        with process_boundaries():
            b = self.binding()
            b.claim()
            b.owner.start(('/inert',), '/inert', {})
            other = b.resources.bind(NativeProcessOwner())
            b.resources.stream_grant(other, TRAFFIC - 4)
            widths = []
            def read(fd, views):
                widths.append(len(views[0]))
                return 2
            with patch('os.readv', side_effect=read), memoryview(bytearray(512)) as slab:
                self.assertEqual(b.read_into('stdout', slab), 2)
            def write(fd, body):
                widths.append(len(body))
                return len(body)
            with patch('os.write', side_effect=write), memoryview(b'abcdef') as body:
                self.assertEqual(b.write(body), 2)
            self.assertEqual(widths, [4, 2])
            self.assertEqual(b.stream.snapshot()['known'], 4)
            self.assertEqual(b.room(), 0)

    def test_future_rpc_credit_is_not_available_to_stdio(self):
        with process_boundaries():
            b = self.binding()
            b.claim()
            b.owner.start(('/inert',), '/inert', {})
            trigger = b.stream.promise(34)
            transaction, peer = object(), object()
            b.stream.protect_future_rx(transaction, peer, 1, 544, trigger)
            other = b.resources.bind(NativeProcessOwner())
            b.resources.stream_grant(other, b.resources.snapshot()['traffic_available'])
            self.assertEqual(b.room(), 0)
            with patch('os.readv', side_effect=AssertionError('unfunded read')), \
                    patch('os.write', side_effect=AssertionError('unfunded write')):
                with memoryview(bytearray(8)) as slab:
                    self.assertEqual(b.read_into('stdout', slab), -1)
                    with self.assertRaises(BlockingIOError):
                        b.write(slab)
            self.assertEqual(b.stream.snapshot()['future_reserved'], 544)
            self.assertEqual(b.stream.snapshot()['pending'], 1)

    def test_cleanup_failure_retains_fresh_positive_prefix_without_retry(self):
        with process_boundaries(), patch('os.readv', return_value=3) as read:
            b = self.binding()
            b.claim()
            b.owner.start(('/inert',), '/inert', {})
            def action():
                with memoryview(bytearray(8)) as slab:
                    with self.assertRaises(KeyboardInterrupt):
                        b.read_into('stdout', slab)
            self.assertTrue(after_released_loan(NativeProcessOwner.read_into, action,
                                               KeyboardInterrupt('cleanup')))
            self.assertEqual(b.stream.snapshot()['known'], 3)
            self.assertTrue(b.resources.snapshot()['unknown'])
            self.assertTrue(b.snapshot()['io_pending'])
            with memoryview(bytearray(8)) as slab:
                with self.assertRaises(ProducerResourceError):
                    b.read_into('stdout', slab)
            self.assertEqual(read.call_count, 1)

    def test_driver_clock_failure_after_read_keeps_charge(self):
        with process_boundaries(), patch('os.readv', return_value=3), \
                patch('os.waitpid', return_value=(0, 0)):
            b = self.binding()
            driver = self.driver(b)
            b.owner.start(('/inert',), '/inert', {})
            with patch('time.monotonic_ns', side_effect=[1, 1, 5_000_000_001]), \
                    patch('select.select', return_value=([735], [], [])):
                with self.assertRaisesRegex(ValueError, 'sdk_producer_deadline'):
                    driver.step(0)
            self.assertEqual(b.stream.snapshot()['known'], 3)
            self.assertTrue(b.resources.snapshot()['unknown'])

    def test_eagain_receipt_spends_zero_and_leaves_original_stream_usable(self):
        with process_boundaries():
            b = self.binding()
            b.claim()
            b.owner.start(('/inert',), '/inert', {})
            with patch('os.readv', side_effect=BlockingIOError), memoryview(bytearray(8)) as slab:
                self.assertEqual(b.read_into('stdout', slab), -1)
            with patch('os.write', side_effect=BlockingIOError), memoryview(b'ab') as body:
                with self.assertRaises(BlockingIOError):
                    b.write(body)
            self.assertEqual(b.stream.snapshot()['known'], 0)
            self.assertEqual(b.stream.snapshot()['pending'], 0)
            self.assertFalse(b.resources.snapshot()['unknown'])

    def test_rpc_and_driver_keep_the_same_data_ticket_after_ack(self):
        with process_boundaries():
            h = Harness(self)
            stream = h.rpc.stream_credit()
            binding = ProducerResources(h.resources, h.owner, h.domain, h.allocation, stream)
            driver = self.driver(binding)
            self.assertIs(driver.native_owner, h.owner)
            h.owner.start(('/inert',), '/inert', {})
            with h.readiness():
                h.client.close()
            self.assertIs(h.rpc.stream_credit(), binding.stream)
            before = stream.snapshot()['known']
            with patch('os.readv', return_value=3), memoryview(bytearray(8)) as slab:
                self.assertEqual(binding.read_into('stderr', slab), 3)
            self.assertEqual(stream.snapshot()['known'], before + 3)
            self.assertFalse(stream.snapshot()['closed'])
            with self.assertRaisesRegex(ValueError, 'resource_slot_busy'):
                h.resources.grant(h.domain, 'data', 1)

    def test_parked_driver_polls_wait_without_payload_interests(self):
        with process_boundaries(), patch('os.waitpid', return_value=(0, 0)):
            b = self.binding()
            driver = self.driver(b)
            b.owner.start(('/inert',), '/inert', {})
            trigger = b.stream.promise(34)
            b.stream.protect_future_rx(object(), object(), 1, 544, trigger)
            other = b.resources.bind(NativeProcessOwner())
            b.resources.stream_grant(other, b.resources.snapshot()['traffic_available'])
            polled = []
            def poll(reads, writes, exceptional, delay):
                polled.append((reads, writes, delay))
                return [], [], []
            with patch('time.monotonic_ns', return_value=1), \
                    patch('select.select', side_effect=poll), \
                    patch('os.readv', side_effect=AssertionError('unfunded read')), \
                    patch('os.write', side_effect=AssertionError('unfunded write')):
                self.assertEqual(driver.step(0), 'idle')
            self.assertEqual(polled, [([737], [], 0)])
            self.assertFalse(b.resources.snapshot()['unknown'])


if __name__ == '__main__':
    unittest.main()
