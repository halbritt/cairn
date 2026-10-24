"""Synthetic contract tests for a capacity-unverified one-slot override."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import trial_one_run_override as override
import trial_paired_request as paired
import trial_weekly_accounts as accounts


class OneRunOverrideTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.case_ids = [f'synthetic-{index:02d}' for index in range(21)]
        self.seeds = [0, 1, 2]
        assigned_accounts = [
            dict(account_id=f'account-{letter}', auth_file=f'/accounts/{letter}/auth.json',
                 auth_sha256=letter.lower() * 64, config_home=f'/accounts/{letter}')
            for letter in ('A', 'B', 'C')
        ]
        assignment = accounts.balanced(self.case_ids, self.seeds, assigned_accounts)
        self.assignment_ref = self._pin('assignment.json', assignment)
        plan = dict(attempts=126, arms=['CURRENT', 'CAND'], cases=self.case_ids,
                    seeds=self.seeds, account_assignment=self.assignment_ref)
        plan['sha256'] = paired.canonical_sha(plan)
        self.plan_ref = self._pin('plan.json', plan)
        self.plan = plan
        self.account_ids = [item['account_id'] for item in assigned_accounts]
        self.context = dict(
            campaign_id='d5f71c84-2374-4ff2-83c2-54ee5cd5572c',
            slot_id='4d55a9a5-723b-5620-9c0e-fb43cc8ae13b',
            scheduled_at='2026-10-24T12:00:00Z', expires_at='2026-10-24T12:45:00Z',
            current='1' * 40, candidate='2' * 40, harness_commit='3' * 40,
            executor='codex-weekly', plan_reference=self.plan_ref,
            assignment_reference=self.assignment_ref, account_ids=self.account_ids,
        )
        self.document = self._document()

    def tearDown(self):
        self.temp.cleanup()

    def _pin(self, name, value):
        path = self.root / name
        raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()
        path.write_bytes(raw)
        return dict(path=str(path), sha256=hashlib.sha256(raw).hexdigest())

    def _document(self):
        return dict(
            schema=override.SCHEMA,
            campaign_id=self.context['campaign_id'], slot_id=self.context['slot_id'],
            scheduled_at=self.context['scheduled_at'], expires_at=self.context['expires_at'],
            current=self.context['current'], candidate=self.context['candidate'],
            harness_commit=self.context['harness_commit'], executor=self.context['executor'],
            plan_sha256=self.plan['sha256'], plan_file_sha256=self.plan_ref['sha256'],
            account_assignment_sha256=self.assignment_ref['sha256'],
            account_ids=sorted(self.account_ids),
            owner_instruction=dict(
                schema=override.INSTRUCTION_SCHEMA, source='owner-conversation',
                reference='conversation-message-reference', statement_sha256='a' * 64,
                observed_at='2026-10-24T11:55:00Z', declared_by='owner'),
            uncertainty=dict(
                schema=override.UNCERTAINTY_SCHEMA, state='capacity_unverified',
                planned_attempts=126, account_count=3, workload_units='unknown',
                absolute_call_allowance='unknown', absolute_token_allowance='unknown',
                provider_pool_route='unverified', account_interactive_use='unknown',
                dedicated_account_proof='unavailable', reservation='none', accepted=False),
        )

    def _override_ref(self):
        return self._pin('override.json', self.document)

    def test_valid_override_binds_one_exact_slot_and_never_claims_capacity(self):
        checked = override.validate(self._override_ref(), **self.context)

        self.assertEqual(checked['slot_id'], self.context['slot_id'])
        self.assertEqual(checked['capacity_status'], 'capacity_unverified')
        self.assertIsNone(checked['capacity_receipt_sha256'])
        self.assertEqual(checked['reservation'], 'none')
        self.assertFalse(checked['accepted'])
        self.assertFalse(checked['runtime_admission'])
        self.assertEqual(checked['instruction_proof'], 'declared_unauthenticated')

    def test_different_slot_is_refused(self):
        self.document['slot_id'] = 'acccf8a1-f1d3-5aef-994b-6961b2d85761'

        with self.assertRaisesRegex(paired.Refusal, 'override_binding_mismatch'):
            override.validate(self._override_ref(), **self.context)

    def test_unknowns_and_non_acceptance_are_mandatory(self):
        self.document['uncertainty']['absolute_token_allowance'] = 4096

        with self.assertRaisesRegex(paired.Refusal, 'override_uncertainty'):
            override.validate(self._override_ref(), **self.context)

    def test_wrong_plan_or_account_assignment_is_refused(self):
        self.document['account_assignment_sha256'] = '0' * 64

        with self.assertRaisesRegex(paired.Refusal, 'override_binding_mismatch'):
            override.validate(self._override_ref(), **self.context)

    def test_missing_route_dedication_and_capacity_are_reported_as_unknown(self):
        checked = override.validate(self._override_ref(), **self.context)

        self.assertEqual(checked['capacity_uncertainty']['provider_pool_route'], 'unverified')
        self.assertEqual(checked['capacity_uncertainty']['account_interactive_use'], 'unknown')
        self.assertEqual(checked['capacity_uncertainty']['dedicated_account_proof'], 'unavailable')
        self.assertEqual(checked['capacity_uncertainty']['workload_units'], 'unknown')

    def test_missing_instruction_provenance_is_refused(self):
        self.document['owner_instruction'].pop('reference')

        with self.assertRaisesRegex(paired.Refusal, 'override_instruction'):
            override.validate(self._override_ref(), **self.context)

    def test_plan_must_be_original_and_assignment_must_cover_every_account(self):
        self.plan['attempts'] = 125
        self.plan['sha256'] = paired.canonical_sha({k: v for k, v in self.plan.items() if k != 'sha256'})
        self.plan_ref = self._pin('plan.json', self.plan)
        self.context['plan_reference'] = self.plan_ref
        self.context['assignment_reference'] = self.assignment_ref
        self.document['plan_sha256'] = self.plan['sha256']
        self.document['plan_file_sha256'] = self.plan_ref['sha256']

        with self.assertRaisesRegex(paired.Refusal, 'override_plan_not_original'):
            override.validate(self._override_ref(), **self.context)


if __name__ == '__main__':
    unittest.main()
