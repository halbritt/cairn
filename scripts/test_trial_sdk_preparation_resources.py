"""Original accounting held before real synthetic preparation file effects."""
import copy
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_trial_sdk_bootstrap_review as fixtures
import trial_native_bootstrap as bootstrap
from trial_sdk_attempt_resources import AttemptResources, ResourceError, RAW


class PreparationResourcesTests(unittest.TestCase):
    setUp = fixtures.SDKReviewTests.setUp

    def prepare(self, bank, **kwargs):
        return bootstrap.prepare_sdk_review_with_resources(self.document, self.output,
            bank, raw=65536, working=2**20, **kwargs)

    def test_original_allocation_precedes_snapshot_and_survives_transfer_and_close(self):
        bank = AttemptResources()
        original = bootstrap.Snapshot.__init__
        observations = []
        def construct(owner, output):
            observations.append(bank.snapshot())
            original(owner, output)
        with patch.object(bootstrap.Snapshot, '__init__', new=construct):
            prepared = self.prepare(bank)
        self.addCleanup(prepared.close)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0]['raw_reserved'], 65536)
        self.assertEqual(observations[0]['working_reserved'], 2**20)
        allocation = prepared._allocation
        self.assertIs(allocation.resources, bank)
        session = prepared.consume_for_session()
        self.addCleanup(session.close)
        self.assertIs(session._allocation, allocation)
        self.assertEqual(prepared.close(), ())
        session.held_source_bindings()
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^hook_interface_unavailable$'):
            session.require_complete()
        self.assertEqual(session.close(), ())
        # A borrowed launch survives FD/control close, so no automatic refund.
        self.assertIsNotNone(session.launch)
        self.assertEqual(bank.snapshot()['raw_reserved'], 65536)
        self.assertFalse(bank.snapshot()['unknown'])

    def test_exhausted_or_occupied_original_bank_refuses_before_output_effect(self):
        for occupied in (False, True):
            bank = AttemptResources()
            bank.allocate(bank.local, 'standing' if occupied else 'operation', RAW, 0)
            expected = ResourceError if occupied else bootstrap.PreparationRefusal
            with patch.object(bootstrap, 'Snapshot', side_effect=AssertionError('unfunded output')):
                with self.assertRaises(expected):
                    self.prepare(bank)
            self.assertFalse(Path(self.output).exists())
            self.assertFalse(bank.snapshot()['unknown'])
            self.assertEqual(bank.snapshot()['raw_reserved'], RAW)

    def test_failure_retains_reservation_freezes_original_bank_and_preserves_cleanup(self):
        bank = AttemptResources()
        self.document = copy.deepcopy(self.document)
        self.document['unexpected'] = True
        before = set(os.listdir('/proc/self/fd'))
        with self.assertRaises(bootstrap.PreparationRefusal):
            self.prepare(bank)
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertTrue(Path(self.output).exists())
        self.assertTrue(bank.snapshot()['unknown'])
        self.assertEqual(bank.snapshot()['raw_reserved'], 65536)
        with self.assertRaisesRegex(ResourceError, '^resource_unknown$'):
            self.prepare(bank)

    def test_released_ticket_cannot_continue_custody_or_mint_replacement(self):
        bank = AttemptResources()
        prepared = self.prepare(bank)
        self.addCleanup(prepared.close)
        session = prepared.consume_for_session()
        self.addCleanup(session.close)
        bank.release(session._allocation.ticket)  # Deliberate trusted-owner misuse.
        with self.assertRaisesRegex(ResourceError, '^resource_ticket$'):
            session.held_source_bindings()
        self.assertTrue(bank.snapshot()['unknown'])
        self.assertEqual(bank.snapshot()['raw_reserved'], 0)
        self.assertEqual(session.close(), ())

    def test_same_allocation_cannot_create_a_second_preparation(self):
        bank = AttemptResources()
        prepared = self.prepare(bank)
        self.addCleanup(prepared.close)
        second = str(self.root / 'second')
        with patch.object(bootstrap, 'Snapshot', side_effect=AssertionError('duplicate output')):
            with self.assertRaisesRegex(bootstrap.Refusal, '^sdk_preparation_allocation_consumed$'):
                bootstrap.prepare_sdk_review(self.document, second, _allocation=prepared._allocation)
        self.assertFalse(Path(second).exists())
        self.assertFalse(bank.snapshot()['unknown'])
        prepared.review_bindings()

    def test_source_drift_freezes_original_allocation_and_closes_original_files(self):
        bank = AttemptResources()
        before = set(os.listdir('/proc/self/fd'))
        prepared = self.prepare(bank)
        source = self.source / 'prompt.txt'
        source.chmod(0o600)
        source.write_bytes(b'changed source')
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            prepared.review_bindings()
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        self.assertTrue(bank.snapshot()['unknown'])
        self.assertEqual(bank.snapshot()['raw_reserved'], 65536)


if __name__ == '__main__':
    unittest.main()
