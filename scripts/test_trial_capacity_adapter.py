"""Synthetic offline capacity evidence; no account, credential, provider or network access."""
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import trial_paired_request as paired
from trial_capacity_adapter import adapt, main
from test_trial_paired_capacity import write_pin
from test_trial_provider_mapping import END, NOW, START, Fixture

LATER = '2026-10-09T20:10:00Z'


class Inputs:
    """The mapping fixture's declarations rewritten as raw adapter observations."""

    def __init__(self, root):
        self.root = root
        self.fixture = Fixture(root)
        self.fixture.save()
        self.basis = write_pin(root / 'allowance-basis.json', {'synthetic_allowance_only': True})
        self.allowances = [dict(accounts=['account-A', 'account-B'], pool_id=r['pool_id'],
                                window_id=r['window_id'], metric=r['metric'], unit='absolute',
                                remaining=r['remaining'], observed_at=START, valid_until=END, basis=self.basis)
                           for r in self.fixture.capacity['resources']]
        self.reservation = dict(state='held', valid_until=END,
                                basis=write_pin(root / 'reservation-basis.json', {'synthetic_hold': True}))

    def document(self):
        f = self.fixture
        f.save()
        return dict(schema='cairn.capacity-evidence-input/1', plan=f.plan,
                    models=dict.fromkeys(('CURRENT', 'CAND', 'primary', 'secondary'), 'fixture-model'),
                    accounts=f.accounts, routes=f.routes, allowances=self.allowances,
                    reservation=self.reservation, spend_request=self.spend_request_pin(),
                    spend_binding=f.mapping['spend_binding'], prices=f.mapping['prices'], quotes=f.mapping['quotes'])

    def spend_request_pin(self):
        return dict(path=str((self.root / 'spend-request.json').resolve()),
                    sha256=hashlib.sha256((self.root / 'spend-request.json').read_bytes()).hexdigest())


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.inputs = Inputs(self.root)
        self.output = self.root / 'adapted'

    def tearDown(self):
        self.tmp.cleanup()

    def refuses(self, code, document=None, now=NOW):
        with self.assertRaisesRegex(paired.Refusal, f'^{code}$'):
            adapt(document or self.inputs.document(), self.output, now=now)
        self.assertFalse(self.output.exists(), 'refusal must not leave partial evidence')

    def test_complete_observations_become_plan_bound_capacity_and_quote_mapping(self):
        receipt = adapt(self.inputs.document(), self.output, now=NOW)
        capacity = paired.read_json_pin(receipt['capacity'])
        self.assertEqual(capacity['schema'], 'cairn.paired-capacity-evidence/2')
        self.assertEqual([(r['metric'], r['required']) for r in capacity['resources']],
                         [('provider_requests', 1227), ('total_tokens', 40450)])
        self.assertEqual(receipt['projection']['spend']['total_units'], 656450)
        self.assertEqual(sorted(receipt['projection']['job_bindings']), sorted(paired.CAPACITY_ROLES))
        self.assertFalse(receipt['runtime_admission'])
        self.assertEqual(receipt['account_proof'], 'declared_unauthenticated')
        mapping = paired.read_json_pin(receipt['mapping'])
        self.assertEqual(mapping['capacity_sha256'], receipt['capacity']['sha256'])
        self.assertEqual(sorted(os.listdir(self.output)), sorted(
            ['accounts.json', 'routes.json', 'reservation.json', 'capacity.json', 'mapping.json', 'receipt.json']))

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        with self.assertRaisesRegex(paired.Refusal, '^output_exists$'):
            adapt(self.inputs.document(), self.output, now=NOW)

    # Stale, missing or unknown counters.
    def test_expired_or_future_allowance_is_stale(self):
        self.inputs.allowances[0]['valid_until'] = NOW
        self.refuses('allowance_stale')
        self.inputs.allowances[0]['valid_until'] = END
        self.inputs.allowances[1]['observed_at'] = LATER
        self.refuses('allowance_stale')

    def test_missing_allowance_for_charged_window_refuses(self):
        self.inputs.allowances.pop()
        self.refuses('allowance_missing')

    def test_unknown_counter_or_value_refuses(self):
        self.inputs.allowances[0]['metric'] = 'credits'
        self.refuses('allowance_unknown_counter')
        self.inputs.allowances[0]['metric'] = 'provider_requests'
        self.inputs.allowances[0]['remaining'] = None
        self.refuses('allowance_unknown_counter')

    def test_usage_percentages_are_not_absolute_capacity(self):
        self.inputs.allowances[1].update(unit='percent', remaining=60)
        self.refuses('allowance_not_absolute')

    def test_workload_job_missing_a_counter_refuses(self):
        del self.inputs.fixture.workload['roles']['auxiliary']['jobs'][0]['per_job']['cache_read_input_tokens']
        self.refuses('workload_counters')

    def test_allowance_outside_charged_windows_refuses(self):
        extra = dict(self.inputs.allowances[0], pool_id='unrelated-pool')
        self.inputs.allowances.append(extra)
        self.refuses('allowance_unmapped')

    # Mismatched quotes or accounts.
    def test_allowance_must_name_exactly_the_accounts_drawing_on_the_pool(self):
        self.inputs.allowances[0]['accounts'] = ['account-A']
        self.refuses('allowance_account_mismatch')

    def test_route_to_undeclared_or_interactive_account_refuses(self):
        self.inputs.fixture.routes['routes']['retry']['account_id'] = 'account-C'
        self.refuses('account_route_unknown')
        self.inputs.fixture.routes['routes']['retry']['account_id'] = 'account-A'
        self.inputs.fixture.accounts['accounts'][1]['interactive_use'] = True
        self.refuses('dedicated_account_unknown')

    def test_quote_bound_to_other_model_or_account_refuses(self):
        quotes = self.inputs.fixture.mapping['quotes']
        quotes['qA']['accounts']['account-A']['model'] = 'other-model'
        self.refuses('mapping_quote_route_mismatch')
        quotes['qA']['accounts']['account-A']['model'] = 'fixture-model'
        quotes['qB']['accounts'] = {'account-A': quotes['qB']['accounts']['account-B']}
        self.refuses('mapping_account_quote_mismatch')

    # Shared-pool double counting.
    def test_duplicate_observation_of_one_shared_pool_cannot_be_summed(self):
        self.inputs.allowances.append(copy.deepcopy(self.inputs.allowances[0]))
        self.refuses('allowance_double_counted')

    def test_shared_pool_must_cover_both_accounts_demand_together(self):
        # 1227 requests total across both accounts; one account's share alone must not pass.
        self.inputs.allowances[0]['remaining'] = 1227 - 63 * 8
        self.refuses('capacity_insufficient')

    # Insufficient allowance.
    def test_allowance_one_token_short_refuses(self):
        self.inputs.allowances[1]['remaining'] = 40450 - 1
        self.refuses('capacity_insufficient')

    def test_reservation_must_be_held_through_capacity_validity(self):
        self.inputs.reservation['state'] = 'requested'
        self.refuses('reservation_unknown')

    def test_command_line_writes_receipt_and_exits_nonzero_on_refusal(self):
        document = write_pin(self.root / 'input.json', self.inputs.document())
        argv = ['--input', document['path'], document['sha256'], '--output', str(self.output), '--now', NOW]
        self.assertEqual(main(argv), 0)
        self.assertTrue((self.output / 'receipt.json').is_file())
        self.inputs.allowances[1]['remaining'] = 1
        document = write_pin(self.root / 'input.json', self.inputs.document())
        other = self.root / 'second'
        self.assertEqual(main(['--input', document['path'], document['sha256'], '--output', str(other),
                               '--now', NOW]), 1)
        self.assertFalse(other.exists())


if __name__ == '__main__':
    unittest.main()
