"""In-process source custody only; real files and controller, no native launch."""
import dataclasses
import os
import unittest
from unittest.mock import patch

import test_trial_sdk_bootstrap_review as fixtures
import trial_bootstrap_snapshot as custody
import trial_native_bootstrap as bootstrap
from trial_coding_launch import compile_sdk_launch


class SessionCustodyTests(unittest.TestCase):
    setUp = fixtures.SDKReviewTests.setUp
    prepare = fixtures.SDKReviewTests.prepare

    def test_move_keeps_original_writer_and_sources_alive_after_old_close(self):
        baseline = set(os.listdir('/proc/self/fd'))
        review = self.prepare()
        launch, control = review.launch, review.launch.control
        initial = review.review_bindings()
        session = review.consume_for_session()
        self.addCleanup(session.close)
        self.assertIs(session.launch, launch)
        self.assertIs(session.control, control)
        self.assertEqual(review.close(), ())
        self.assertFalse(control.ledger()['closed'])
        self.assertEqual(session.source_bindings()['snapshots'], initial['snapshots'])
        for operation in (review.review_bindings, review.consume_for_session):
            with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^sdk_review_transferred$'):
                operation()
        # Pure caller-reported progress: no native delivery claim is made.
        control.written(1)
        self.assertEqual(session.source_bindings()['snapshots'], initial['snapshots'])
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^hook_interface_unavailable$'):
            session.require_complete()
        self.assertFalse(control.ledger()['closed'])
        self.assertEqual(session.close(), ())
        self.assertEqual(session.close(), ())
        self.assertTrue(control.ledger()['closed'])
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^session_custody_unknown$'):
            session.source_bindings()
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_session_construction_consumes_review_instead_of_accepting_raw_handles(self):
        review = self.prepare()
        session = bootstrap.SDKSessionCustody(review)
        self.addCleanup(session.close)
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^sdk_review_transferred$'):
            review.consume_for_session()
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^sdk_review_transferred$'):
            bootstrap.SDKSessionCustody(review)
        review.close()
        self.assertFalse(session.control.ledger()['closed'])
        session.source_bindings()

    def test_progress_or_close_before_move_refuses_and_releases_original_owner(self):
        for action in ('progress', 'close'):
            with self.subTest(action=action):
                baseline = set(os.listdir('/proc/self/fd'))
                review = self.prepare(str(self.root / action))
                control = review.launch.control
                if action == 'progress':
                    control.written(1)
                else:
                    review.close()
                with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
                    review.consume_for_session()
                self.assertTrue(control.ledger()['closed'])
                self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_replaced_old_launch_cannot_redirect_transferred_control_cleanup(self):
        review = self.prepare()
        session = review.consume_for_session()
        self.addCleanup(session.close)
        original = session.control
        foreign = compile_sdk_launch(session_id=self.document['binding']['session_id'],
            model='fixture-model', effort='high', prompt=self.prompt,
            memory_instruction=self.instruction).control
        self.addCleanup(foreign.close)
        review._launch = dataclasses.replace(review.launch, control=foreign)
        review._control = foreign
        review.close()
        session.source_bindings()
        self.assertFalse(original.ledger()['closed'])
        session.close()
        self.assertTrue(original.ledger()['closed'])
        self.assertFalse(foreign.ledger()['closed'])

    def test_source_drift_after_progress_closes_session_but_metadata_copy_cannot_drift_it(self):
        baseline = set(os.listdir('/proc/self/fd'))
        session = self.prepare().consume_for_session()
        self.addCleanup(session.close)
        session.control.written(1)
        first = session.source_bindings()
        first['initial_control']['bindings']['session_id'] = 'changed projection'
        self.assertEqual(session.source_bindings()['initial_control']['bindings']['session_id'],
                         self.document['binding']['session_id'])
        source = self.source / 'prompt.txt'
        source.chmod(0o600)
        source.write_bytes(b'Changed after the ownership move')
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^session_custody_unknown$'):
            session.source_bindings()
        self.assertTrue(session.control.ledger()['closed'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_constructor_read_failure_or_interrupt_keeps_primary_and_cleanup_uncertainty(self):
        for index, primary in enumerate((OSError('private source read failure'),
                                         KeyboardInterrupt('private source interruption'))):
            with self.subTest(primary=type(primary).__name__):
                baseline = set(os.listdir('/proc/self/fd'))
                review = self.prepare(str(self.root / ('fault-' + str(index))))
                original = review.launch.control
                target = review._snapshot.files['prompt.txt'][0].fd
                actual_read, actual_close, attempts = os.read, os.close, []
                def refuse_read(fd, count):
                    if fd == target:
                        raise primary
                    return actual_read(fd, count)
                def uncertain_close(fd):
                    attempts.append(fd)
                    actual_close(fd)
                    if fd == target:
                        raise OSError('private close uncertainty')
                expected = bootstrap.PreparationRefusal if isinstance(primary, OSError) else KeyboardInterrupt
                with patch.object(custody.os, 'read', side_effect=refuse_read), \
                        patch.object(custody.os, 'close', side_effect=uncertain_close):
                    with self.assertRaises(expected) as caught:
                        review.consume_for_session()
                    self.assertEqual(review.close(), ('descriptor_cleanup_unknown',))
                if isinstance(primary, OSError):
                    self.assertEqual(str(caught.exception), 'review_custody_unknown')
                else:
                    self.assertIs(caught.exception, primary)
                self.assertEqual(caught.exception.cleanup_faults, ('descriptor_cleanup_unknown',))
                self.assertEqual(attempts.count(target), 1)
                self.assertTrue(original.ledger()['closed'])
                self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_session_exit_preserves_immutable_primary_and_never_retries_unknown_close(self):
        @dataclasses.dataclass(frozen=True)
        class Primary(Exception):
            reason: str
        baseline = set(os.listdir('/proc/self/fd'))
        review = self.prepare()
        session = review.consume_for_session()
        self.addCleanup(session.close)
        target = session._snapshot.files['prompt.txt'][0].fd
        actual_close, attempts = os.close, []
        def uncertain_close(fd):
            attempts.append(fd)
            actual_close(fd)
            if fd == target:
                raise OSError('private close uncertainty')
        primary = Primary('immutable body failure')
        with patch.object(custody.os, 'close', side_effect=uncertain_close):
            with self.assertRaises(Primary) as caught:
                with session:
                    raise primary
            self.assertEqual(session.close(), ('descriptor_cleanup_unknown',))
            self.assertEqual(review.close(), ())
        self.assertIs(caught.exception, primary)
        self.assertEqual(attempts.count(target), 1)
        self.assertTrue(session.control.ledger()['closed'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_cleanup_fault_without_body_exception_is_not_successful_context_exit(self):
        session = self.prepare().consume_for_session()
        self.addCleanup(session.close)
        target = session._snapshot.files['prompt.txt'][0].fd
        actual_close = os.close
        def uncertain_close(fd):
            actual_close(fd)
            if fd == target:
                raise OSError('private close uncertainty')
        with patch.object(custody.os, 'close', side_effect=uncertain_close):
            with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^sdk_session_cleanup_unknown$') as caught:
                with session:
                    pass
        self.assertEqual(caught.exception.cleanup_faults, ('descriptor_cleanup_unknown',))
        self.assertTrue(session.control.ledger()['closed'])


if __name__ == '__main__':
    unittest.main()
