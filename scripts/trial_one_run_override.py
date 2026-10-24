"""Validate a declared one-slot CAIRN-107 override without proving capacity.

This module is deliberately separate from the scheduled weekly runner. Its
caller may use the result only for an explicit manual one-run CLI path. The
runner must still apply its normal account, provider preflight, identity,
writer, material, custody, stop, and exclusive slot-claim checks.
"""
import re

import trial_paired_request as paired
import trial_weekly_accounts as weekly_accounts
from check_weekly_paired_eval import WEEKLY_ATTEMPTS, WEEKLY_CASES, WEEKLY_SEEDS

SCHEMA = 'cairn.weekly-one-run-override/2'
INSTRUCTION_SCHEMA = 'cairn.owner-instruction-provenance/1'
UNCERTAINTY_SCHEMA = 'cairn.weekly-capacity-uncertainty/1'
FIELDS = ('schema campaign_id slot_id scheduled_at expires_at current candidate '
          'harness_commit executor plan_sha256 plan_file_sha256 '
          'account_assignment_sha256 account_ids owner_instruction uncertainty')
INSTRUCTION_FIELDS = 'schema source reference statement_sha256 observed_at declared_by'
ARMS = ['CURRENT', 'CAND']


def _unknown_capacity():
    return dict(schema=UNCERTAINTY_SCHEMA, state='capacity_unverified',
                planned_attempts=WEEKLY_ATTEMPTS, account_count=3,
                workload_units='unknown', absolute_call_allowance='unknown',
                absolute_token_allowance='unknown', provider_pool_route='unverified',
                account_interactive_use='unknown', dedicated_account_proof='unavailable',
                reservation='none', accepted=False)


def _hex(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _commit(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{40}', value) is not None


def _text(value):
    return isinstance(value, str) and 0 < len(value.encode()) <= 256 and '\0' not in value


def _reference(value, code):
    paired.shape(value, 'path sha256', code)
    paired.pin(value)


def _original_plan(reference, assignment_reference, account_ids):
    _reference(reference, 'override_plan_pin')
    plan = paired.read_json_pin(reference)
    paired.need(isinstance(plan, dict) and plan.get('sha256') == paired.canonical_sha(
        {key: value for key, value in plan.items() if key != 'sha256'}), 'override_plan_pin')
    paired.need(plan.get('attempts') == WEEKLY_ATTEMPTS and plan.get('arms') == ARMS
                and isinstance(plan.get('cases'), list) and len(plan['cases']) == WEEKLY_CASES
                and len(set(plan['cases'])) == WEEKLY_CASES
                and isinstance(plan.get('seeds'), list) and len(plan['seeds']) == WEEKLY_SEEDS
                and len(set(plan['seeds'])) == WEEKLY_SEEDS, 'override_plan_not_original')
    actual_assignment = plan.get('account_assignment')
    paired.need(isinstance(actual_assignment, dict)
                and actual_assignment == assignment_reference, 'override_assignment_mismatch')
    allocation, normalized_reference, _ = weekly_accounts.validate(actual_assignment, plan)
    ids = [account['account_id'] for account in allocation['accounts']]
    paired.need(len(ids) == 3 and set(ids) == set(account_ids)
                and len(ids) == len(set(ids)), 'override_assignment_mismatch')
    return plan, normalized_reference, sorted(ids)


def validate(reference, *, campaign_id, slot_id, scheduled_at, expires_at, current,
             candidate, harness_commit, executor, plan_reference,
             assignment_reference, account_ids):
    """Check the exact one-slot override and return an explicitly unaccepted result.

    This validates shape, pins and identity only. It neither authenticates the
    instruction nor estimates provider capacity, acquires a reservation,
    performs account preflight, or authorizes any downstream acceptance.
    """
    _reference(reference, 'override_pin')
    document = paired.read_json_pin(reference)
    paired.shape(document, FIELDS, 'override_shape')
    paired.need(document['schema'] == SCHEMA, 'override_schema')

    try:
        paired.identifier(campaign_id)
        paired.identifier(slot_id)
        paired.identifier(document['campaign_id'])
        paired.identifier(document['slot_id'])
    except paired.Refusal:
        raise paired.Refusal('override_binding_mismatch') from None
    paired.need(_commit(current) and _commit(candidate) and current != candidate
                and _commit(harness_commit), 'override_binding_mismatch')
    paired.need(_text(executor), 'override_binding_mismatch')
    try:
        start, end = paired.utc(scheduled_at), paired.utc(expires_at)
        paired.need(start < end, 'override_binding_mismatch')
        paired.utc(document['scheduled_at'])
        paired.utc(document['expires_at'])
    except paired.Refusal:
        raise paired.Refusal('override_binding_mismatch') from None

    expected = dict(campaign_id=campaign_id, slot_id=slot_id,
                    scheduled_at=scheduled_at, expires_at=expires_at,
                    current=current, candidate=candidate,
                    harness_commit=harness_commit, executor=executor)
    paired.need(all(document[key] == value for key, value in expected.items()),
                'override_binding_mismatch')
    paired.need(isinstance(account_ids, (list, tuple)) and account_ids
                and all(paired.capacity_name(item) for item in account_ids)
                and len(account_ids) == len(set(account_ids)), 'override_assignment_mismatch')

    plan, assignment, assigned_accounts = _original_plan(
        plan_reference, assignment_reference, account_ids)
    paired.need(document['plan_sha256'] == plan['sha256']
                and document['plan_file_sha256'] == plan_reference['sha256']
                and document['account_assignment_sha256'] == assignment['sha256']
                and document['account_ids'] == assigned_accounts,
                'override_binding_mismatch')
    paired.need(document['uncertainty'] == _unknown_capacity(),
                'override_uncertainty')

    instruction = document['owner_instruction']
    paired.shape(instruction, INSTRUCTION_FIELDS, 'override_instruction')
    paired.need(instruction['schema'] == INSTRUCTION_SCHEMA
                and _text(instruction['source']) and _text(instruction['reference'])
                and _hex(instruction['statement_sha256'])
                and instruction['declared_by'] == 'owner', 'override_instruction')
    try:
        paired.utc(instruction['observed_at'])
    except paired.Refusal:
        raise paired.Refusal('override_instruction') from None

    return dict(
        mode='manual_one_run_override',
        override_sha256=reference['sha256'],
        campaign_id=campaign_id,
        slot_id=slot_id,
        scheduled_at=scheduled_at,
        expires_at=expires_at,
        plan_sha256=plan['sha256'],
        plan_file_sha256=plan_reference['sha256'],
        account_assignment_sha256=assignment['sha256'],
        account_assignment=assignment,
        accounts=assigned_accounts,
        capacity_status='capacity_unverified',
        capacity_uncertainty=dict(document['uncertainty']),
        capacity_receipt_sha256=None,
        reservation='none',
        accepted=False,
        runtime_admission=False,
        instruction_proof='declared_unauthenticated',
    )
