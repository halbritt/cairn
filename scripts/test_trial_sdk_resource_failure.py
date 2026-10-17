"""Secondary quarantine failure must not replace primary failure or skip cleanup."""
import os
from unittest.mock import patch
import unittest

import trial_native_bootstrap as bootstrap
import trial_sdk_session_driver as sessions
from trial_sdk_attempt_resources import AttemptResources, ResourceError
import test_trial_sdk_session_resources as session_tests


class ResourceFailureTests(unittest.TestCase):
    setUp = session_tests.SessionResourcesTests.setUp
    setup_binding = session_tests.SessionResourcesTests.setup_binding
    running = session_tests.SessionResourcesTests.running

    def test_legacy_custody_retains_freeze_fault_after_source_close(self):
        baseline = set(os.listdir('/proc/self/fd'))
        prepared, binding = self.setup_binding()
        custody = bootstrap.SDKSessionCustody(prepared)
        source = self.source / 'prompt.txt'
        source.chmod(0o600)
        source.write_bytes(b'changed')
        with patch.object(binding.resources, 'freeze', side_effect=KeyboardInterrupt('secondary')):
            with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^session_custody_unknown$') as caught:
                custody.source_bindings()
        self.assertIn('resource_freeze_unknown', caught.exception.cleanup_faults)
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
        self.assertFalse(binding.resources.snapshot()['unknown'])

    def test_legacy_custody_merges_generic_primary_and_close_faults(self):
        baseline = set(os.listdir('/proc/self/fd'))
        prepared, binding = self.setup_binding()
        custody = bootstrap.SDKSessionCustody(prepared)
        primary = RuntimeError('primary')
        primary.cleanup_faults = ('resource_freeze_unknown',)
        close = custody.close
        def close_with_fault():
            close()
            return ('descriptor_cleanup_unknown',)
        with patch.object(custody, 'held_source_bindings', side_effect=primary), \
                patch.object(custody, 'close', side_effect=close_with_fault):
            with self.assertRaises(RuntimeError) as caught:
                custody.source_bindings()
        self.assertIs(caught.exception, primary)
        self.assertEqual(primary.cleanup_faults,
                         ('descriptor_cleanup_unknown', 'resource_freeze_unknown'))
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_drift_freeze_failure_preserves_refusal_and_closes_original_sources(self):
        baseline = set(os.listdir('/proc/self/fd'))
        prepared, binding = self.setup_binding()
        source = self.source / 'prompt.txt'
        source.chmod(0o600)
        source.write_bytes(b'changed')
        with patch.object(binding.resources, 'freeze', side_effect=KeyboardInterrupt('secondary')):
            with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$') as caught:
                prepared.review_bindings()
        self.assertIn('resource_freeze_unknown', caught.exception.cleanup_faults)
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
        self.assertTrue(prepared.launch.control.ledger()['closed'])
        self.assertTrue(prepared._allocation._unknown)
        # Do not invent successful ledger freeze; original reservation is held.
        self.assertFalse(binding.resources.snapshot()['unknown'])
        self.assertGreater(binding.resources.snapshot()['raw_reserved'], 0)

    def test_failed_preparation_preserves_shape_refusal_when_freeze_also_fails(self):
        baseline = set(os.listdir('/proc/self/fd'))
        bank = AttemptResources()
        self.document['unexpected'] = True
        with patch.object(bank, 'freeze', side_effect=KeyboardInterrupt('secondary')):
            with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^bootstrap_shape$') as caught:
                bootstrap.prepare_sdk_review_with_resources(self.document, self.output, bank,
                    raw=65536, working=2**20)
        self.assertIn('resource_freeze_unknown', caught.exception.cleanup_faults)
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
        self.assertEqual(bank.snapshot()['raw_reserved'], 65536)

    def test_pump_failure_after_transfer_closes_source_and_native_owners(self):
        baseline = set(os.listdir('/proc/self/fd'))
        prepared, binding = self.setup_binding()
        primary = RuntimeError('primary pump construction')
        with patch.object(sessions, 'SDKStreamDriver', side_effect=primary), \
                patch.object(binding.resources, 'freeze', side_effect=KeyboardInterrupt('secondary')):
            with self.assertRaises(RuntimeError) as caught:
                sessions.SDKSessionDriver.from_resources(prepared, binding)
        self.assertIs(caught.exception, primary)
        self.assertIn('resource_freeze_unknown', primary.cleanup_faults)
        self.assertTrue(prepared._transferred)
        self.assertTrue(prepared.launch.control.ledger()['closed'])
        self.assertTrue(binding.owner.snapshot()['release']['handles_detached'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
        self.assertTrue(binding.snapshot()['unknown'])
        self.assertFalse(binding.resources.snapshot()['unknown'])

    def test_clock_primary_and_positive_prefix_survive_freeze_failure(self):
        prepared, binding = self.setup_binding()
        driver = sessions.SDKSessionDriver.from_resources(prepared, binding)
        primary = RuntimeError('primary clock failure')
        def clock():
            if binding.stream.snapshot()['known']:
                raise primary
            return 1
        with self.running(driver), patch('time.monotonic_ns', side_effect=clock), \
                patch('select.select', return_value=([735], [], [])), \
                patch('os.readv', return_value=2) as reads, \
                patch.object(binding.resources, 'freeze', side_effect=KeyboardInterrupt('secondary')):
            with self.assertRaises(RuntimeError) as caught:
                driver.step(0)
            self.assertIs(caught.exception, primary)
            self.assertIn('resource_freeze_unknown', primary.cleanup_faults)
            self.assertEqual(binding.stream.snapshot()['known'], 2)
            self.assertTrue(binding.snapshot()['unknown'])
            self.assertIn('resource_freeze_unknown', binding.snapshot()['resource_cleanup_faults'])
            self.assertFalse(binding.resources.snapshot()['unknown'])
            with self.assertRaises(ValueError):
                driver.step(0)
            self.assertEqual(reads.call_count, 1)

    def test_standing_release_after_first_step_blocks_next_io(self):
        prepared, binding = self.setup_binding()
        driver = sessions.SDKSessionDriver.from_resources(prepared, binding)
        with self.running(driver), patch('time.monotonic_ns', return_value=1), \
                patch('select.select', return_value=([], [], [])) as selected, \
                patch('os.write', return_value=2) as writes:
            self.assertEqual(driver.step(0).kind, 'idle')
            binding.resources.release(driver._custody._allocation.ticket)
            selected.return_value = ([], [731], [])
            with self.assertRaisesRegex(ResourceError, '^resource_ticket$'):
                driver.step(0)
            self.assertEqual(writes.call_count, 0)
            self.assertEqual(binding.stream.snapshot()['known'], 0)
            self.assertTrue(binding.snapshot()['unknown'])


if __name__ == '__main__':
    unittest.main()
