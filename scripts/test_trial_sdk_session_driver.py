"""Real source-file custody with no child launch or runtime qualification."""
import os
import unittest
from unittest.mock import patch

import test_trial_sdk_bootstrap_review as fixtures
import trial_bootstrap_snapshot as custody
from trial_native_bootstrap import PreparationRefusal
from trial_sdk_session_driver import SDKSessionDriver, SessionDriverError


class SessionDriverTests(unittest.TestCase):
    setUp = fixtures.SDKReviewTests.setUp
    prepare = fixtures.SDKReviewTests.prepare

    def test_consumes_exact_review_once_and_unlaunched_close_releases_sources(self):
        baseline = set(os.listdir('/proc/self/fd'))
        review = self.prepare()
        launch, control = review.launch, review.launch.control
        driver = SDKSessionDriver(review)
        self.addCleanup(driver.close_sources)
        self.assertIs(driver.launch, launch)
        self.assertIs(driver.launch.control, control)
        self.assertIsNone(driver.native_owner.snapshot()['pid'])
        self.assertFalse(driver.snapshot()['input_close_attempted'])
        review.close()
        self.assertFalse(control.ledger()['closed'])
        with self.assertRaisesRegex(PreparationRefusal, '^sdk_review_transferred$'):
            SDKSessionDriver(review)
        with self.assertRaisesRegex(PreparationRefusal, '^hook_interface_unavailable$'):
            driver.require_complete()
        driver.source_bindings()
        self.assertEqual(driver.close_sources(), ())
        self.assertEqual(driver.close_sources(), ())
        self.assertTrue(control.ledger()['closed'])
        self.assertTrue(driver.snapshot()['sources_close_attempted'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
        with self.assertRaisesRegex(SessionDriverError, '^sdk_session_unavailable$'):
            driver.step()

    def test_raw_controller_or_metadata_cannot_construct_session(self):
        review = self.prepare()
        for value in (review.launch.control, review.review_bindings(), object()):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaisesRegex(SessionDriverError, '^sdk_session_review_required$'):
                    SDKSessionDriver(value)
        self.assertFalse(review.launch.control.ledger()['closed'])

    def test_recheck_failure_keeps_source_handles_until_explicit_owner_cleanup(self):
        baseline = set(os.listdir('/proc/self/fd'))
        review = self.prepare()
        control = review.launch.control
        driver = SDKSessionDriver(review)
        self.addCleanup(driver.close_sources)
        held = set(os.listdir('/proc/self/fd'))
        prompt = self.source / 'prompt.txt'
        prompt.chmod(0o600)
        prompt.write_bytes(b'Deliberate held-source drift')
        with self.assertRaisesRegex(PreparationRefusal, '^session_custody_unknown$'):
            driver.source_bindings()
        self.assertEqual(set(os.listdir('/proc/self/fd')), held)
        self.assertFalse(control.ledger()['closed'])
        self.assertIsNotNone(driver.snapshot()['failure'])
        self.assertEqual(driver.close_sources(), ())
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_failed_custody_assignment_preserves_inherited_actual_close_uncertainty(self):
        baseline = set(os.listdir('/proc/self/fd'))
        review = self.prepare()
        target = review._snapshot.files['prompt.txt'][0].fd
        actual_read, actual_close, attempts = os.read, os.close, []
        def refuse_read(fd, count):
            if fd == target:
                raise OSError('constructed source failure')
            return actual_read(fd, count)
        def uncertain_close(fd):
            attempts.append(fd)
            actual_close(fd)
            if fd == target:
                raise OSError('constructed post-close uncertainty')
        with patch.object(custody.os, 'read', side_effect=refuse_read), \
                patch.object(custody.os, 'close', side_effect=uncertain_close):
            with self.assertRaisesRegex(PreparationRefusal, '^review_custody_unknown$') as caught:
                SDKSessionDriver(review)
        self.assertEqual(caught.exception.cleanup_faults, ('descriptor_cleanup_unknown',))
        self.assertEqual(attempts.count(target), 1)
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_live_source_interrupt_preserves_primary_and_held_handles(self):
        baseline = set(os.listdir('/proc/self/fd'))
        review = self.prepare()
        driver = SDKSessionDriver(review)
        self.addCleanup(driver.close_sources)
        held = set(os.listdir('/proc/self/fd'))
        target = driver._custody._snapshot.files['prompt.txt'][0].fd
        primary = KeyboardInterrupt('constructed source interruption')
        actual_read = os.read
        def refuse(fd, count):
            if fd == target:
                raise primary
            return actual_read(fd, count)
        with patch.object(custody.os, 'read', side_effect=refuse):
            with self.assertRaises(KeyboardInterrupt) as caught:
                driver.source_bindings()
        self.assertIs(caught.exception, primary)
        self.assertEqual(set(os.listdir('/proc/self/fd')), held)
        self.assertFalse(driver._custody.control.ledger()['closed'])
        with self.assertRaisesRegex(SessionDriverError, '^sdk_session_unavailable$'):
            driver.source_bindings()
        self.assertEqual(driver.close_sources(), ())
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)


if __name__ == '__main__':
    unittest.main()
