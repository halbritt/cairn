"""Public session composition with real custody/receipts and inert native I/O."""
import contextlib
import inspect
import sys
import os
from types import SimpleNamespace
import stat
import unittest
from unittest.mock import patch

import test_trial_sdk_bootstrap_review as fixtures
from test_trial_sdk_command_input import process_boundaries
from trial_native_bootstrap import prepare_sdk_review_with_resources
from trial_sdk_attempt_resources import AttemptResources, TRAFFIC, ResourceError
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_session_resources import SDKSessionResources
from trial_sdk_session_driver import SDKSessionDriver, SessionDriverError
from trial_sdk_direct_writer import SDKDirectWriter
from trial_sdk_stream_credit import StreamCredit


class SessionResourcesTests(unittest.TestCase):
    setUp = fixtures.SDKReviewTests.setUp

    def setup_binding(self, bank=None):
        bank = AttemptResources() if bank is None else bank
        prepared = prepare_sdk_review_with_resources(self.document, self.output, bank,
            raw=65536, working=2**20)
        self.addCleanup(prepared.close)
        owner = NativeProcessOwner()
        domain = bank.bind(owner)
        allocation = bank.allocate(domain, 'operation', SDKSessionResources.MINIMUM_RAW, 2**20)
        binding = SDKSessionResources(bank, owner, domain, allocation, StreamCredit(bank, domain, 1))
        return prepared, binding

    @contextlib.contextmanager
    def running(self, driver):
        real_fstat, real_close = os.fstat, os.close
        with process_boundaries(), \
                patch('os.fstat', side_effect=lambda fd: SimpleNamespace(st_mode=stat.S_IFSOCK)
                      if 731 <= fd <= 737 else real_fstat(fd)), \
                patch('os.close', side_effect=lambda fd: None if 731 <= fd <= 737 else real_close(fd)), \
                patch('os.waitpid', return_value=(0, 0)):
            driver.native_owner.start(('/inert',), '/inert', {})
            try:
                yield
            finally:
                with patch('os.waitpid', return_value=(123, 0)):
                    driver.native_owner.observe_wait()
                    driver.native_owner.cleanup_after_wait()
                    driver.close_sources()

    def test_same_original_owner_and_bank_bound_before_pump_and_transfer(self):
        prepared, binding = self.setup_binding()
        control = prepared.launch.control
        before = binding.resources.snapshot()
        driver = SDKSessionDriver.from_resources(prepared, binding)
        self.addCleanup(driver.close_sources)
        self.assertIs(driver.native_owner, binding.owner)
        self.assertIs(driver.launch.control, control)
        self.assertIs(driver._custody._allocation.resources, binding.resources)
        self.assertIs(driver._pump._writer._io, binding)
        self.assertEqual(binding.resources.snapshot(), before)
        self.assertTrue(binding.snapshot()['claimed'])
        self.assertEqual(driver.close_sources(), ())
        self.assertEqual(binding.resources.snapshot()['raw_reserved'], before['raw_reserved'])

    def test_bound_preparation_has_no_legacy_or_foreign_bank_fallback(self):
        prepared, binding = self.setup_binding()
        with self.assertRaisesRegex(SessionDriverError, '^sdk_session_resources_required$'):
            SDKSessionDriver(prepared)
        with self.assertRaisesRegex(SessionDriverError, '^sdk_session_resource_binding$'):
            SDKSessionDriver.from_resources(prepared, None)
        other = AttemptResources()
        owner = NativeProcessOwner()
        domain = other.bind(owner)
        allocation = other.allocate(domain, 'operation', 4096, 2**20)
        foreign = SDKSessionResources(other, owner, domain, allocation, StreamCredit(other, domain, 1))
        with self.assertRaisesRegex(SessionDriverError, '^sdk_session_resource_binding$'):
            SDKSessionDriver.from_resources(prepared, foreign)
        self.assertFalse(prepared._transferred)
        self.assertFalse(binding.snapshot()['claimed'])
        self.assertFalse(other.snapshot()['unknown'])

    def test_public_step_uses_only_remaining_credit_across_stderr_and_writer(self):
        prepared, binding = self.setup_binding()
        driver = SDKSessionDriver.from_resources(prepared, binding)
        other = binding.resources.bind(NativeProcessOwner())
        binding.resources.stream_grant(other, TRAFFIC - 4)
        offered = []
        with self.running(driver), patch('time.monotonic_ns', return_value=1):
            def read(fd, views):
                offered.append(('read', len(views[0])))
                views[0][:2] = b'ab'
                return 2
            def write(fd, body):
                offered.append(('write', len(body)))
                return len(body)
            with patch('select.select', return_value=([735], [731], [])), \
                    patch('os.readv', side_effect=read), patch('os.write', side_effect=write):
                self.assertEqual(driver.step(0).kind, 'progress')
            self.assertEqual(offered, [('read', 4), ('write', 2)])
            self.assertEqual(binding.stream.snapshot()['known'], 4)
            self.assertEqual(driver.snapshot()['stream']['writer']['positive_write_bytes'], 2)
            self.assertEqual(driver.snapshot()['stream']['stderr_bytes'], 2)
            self.assertEqual(binding.room(), 0)

    def test_post_read_clock_failure_retains_prefix_and_freezes_without_retry(self):
        prepared, binding = self.setup_binding()
        driver = SDKSessionDriver.from_resources(prepared, binding)
        primary = RuntimeError('synthetic clock failure')
        def clock():
            if binding.stream.snapshot()['known']:
                raise primary
            return 1
        with self.running(driver), patch('time.monotonic_ns', side_effect=clock), \
                patch('select.select', return_value=([735], [], [])), \
                patch('os.readv', return_value=2) as reads:
            with self.assertRaises(RuntimeError) as caught:
                driver.step(0)
            self.assertIs(caught.exception, primary)
            self.assertEqual(binding.stream.snapshot()['known'], 2)
            self.assertTrue(binding.resources.snapshot()['unknown'])
            self.assertEqual(driver.snapshot()['stream']['stderr_bytes'], 2)
            with self.assertRaises(ResourceError):
                driver.step(0)
            self.assertEqual(reads.call_count, 1)

    def test_final_writer_cleanup_interrupt_freezes_already_settled_prefix(self):
        prepared, binding = self.setup_binding()
        driver = SDKSessionDriver.from_resources(prepared, binding)
        method = inspect.unwrap(SDKDirectWriter.advance)
        lines, start = inspect.getsourcelines(method)
        target = start + next(i for i, line in enumerate(lines) if 'self._busy = False' in line)
        primary, hits = KeyboardInterrupt('synthetic final cleanup'), []
        def trace(frame, event, arg):
            if event == 'line' and frame.f_code is method.__code__ and frame.f_lineno == target:
                hits.append(True)
                raise primary
            return trace
        with self.running(driver), patch('time.monotonic_ns', return_value=1), \
                patch('select.select', return_value=([], [731], [])), \
                patch('os.write', return_value=2) as writes:
            previous = sys.gettrace()
            try:
                sys.settrace(trace)
                with self.assertRaises(KeyboardInterrupt) as caught:
                    driver.step(0)
            finally:
                sys.settrace(previous)
            self.assertIs(caught.exception, primary)
            self.assertEqual(hits, [True])
            self.assertEqual(binding.stream.snapshot()['known'], 2)
            self.assertTrue(binding.resources.snapshot()['unknown'])
            with self.assertRaises(ResourceError):
                driver.step(0)
            self.assertEqual(writes.call_count, 1)

    def test_protected_future_credit_parks_payload_but_keeps_wait_interest(self):
        prepared, binding = self.setup_binding()
        driver = SDKSessionDriver.from_resources(prepared, binding)
        trigger = binding.stream.promise(34)
        binding.stream.protect_future_rx(object(), object(), 1, 544, trigger)
        other = binding.resources.bind(NativeProcessOwner())
        binding.resources.stream_grant(other, binding.resources.snapshot()['traffic_available'])
        with self.running(driver), patch('time.monotonic_ns', return_value=1), \
                patch('select.select', return_value=([], [], [])) as select_call, \
                patch('os.readv', side_effect=AssertionError('unfunded read')), \
                patch('os.write', side_effect=AssertionError('unfunded write')):
            self.assertEqual(driver.step(0).kind, 'idle')
            self.assertEqual(select_call.call_args.args[:2], ([737], []))
            self.assertEqual(binding.stream.snapshot()['known'], 0)


if __name__ == '__main__':
    unittest.main()
