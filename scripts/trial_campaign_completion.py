"""Two-week completion verdict for CAIRN-107 over existing verified outputs.

Consumes, per week, the pinned runner profile, the runner's
`cairn.weekly-run-receipt/1`, the occurrence's persisted
`cairn.legacy-occurrence-state-snapshot/1` and an optional
`cairn.candidate-run-bridge-receipt/1`. Every input is bound by pin; a changed
input, a broken identity join, or a gap, duplicate or reversal between the two
weekly slots refuses. Honest but incomplete evidence yields a typed
`not_complete` verdict with precise unmet reasons.

What this never does: grade or regrade (figures come
only from the existing report projection and join), resolve disputes, promote an
incomplete report, treat a declaration as an observation, or claim actual
campaign success. Custody stays `unknown`. "No manual setup" stays `unproved`:
no existing receipt records automation of ordinary branch requests. See
docs/campaign-completion.md.
"""
import argparse
import datetime as dt
import hashlib
import json
import sys
import uuid

import trial_candidate_run_bridge as bridge
import trial_legacy_occurrence as occurrence
import trial_paired_request as paired
import trial_run_report as report
import trial_weekly_run as weekly
from check_weekly_paired_eval import WEEKLY_ATTEMPTS

INPUT_SCHEMA = 'cairn.campaign-completion-input/1'
VERDICT_SCHEMA = 'cairn.campaign-completion-verdict/1'
WEEK_FIELDS = 'runner_profile receipt occurrence_snapshot bridge_receipt'
# The runner's _finish writes exactly these receipt fields.
RECEIPT_FIELDS = set(('schema slot_id campaign_id scheduled_at expires_at current candidate harness_commit executor '
                      'capacity selection original_execution status code agent attempts harness_exit_code '
                      'pair_binding limit_assessment baseline_custody pair_identity report_input '
                      'runtime_admission').split())
SNAPSHOT_FIELDS = 'schema input state report join collection routing'
# trial_run_report incomplete reasons, by the criterion they bear on.
ATTEMPT_REASONS = frozenset(('admission_not_complete', 'planned_not_original', 'attempts_not_started',
                             'attempts_unfinished', 'attempt_failures', 'attempt_outcome_unknown',
                             'provider_limit_losses', 'provider_auth_failures', 'measurement_failures'))
SELECTION_REASONS = frozenset(('evidence_mismatch', 'grading_attempts_unavailable', 'selection_undetermined'))
GRADING_REASONS = frozenset(('changed_pairs_not_dual_graded', 'grade_conflicts'))
OWNER_REASON = 'disagreements_pending_owner'
WEEK = dt.timedelta(days=7)
REMAINING_LIVE_EVIDENCE = (
    'two_live_consecutive_runner_receipts_from_an_actual_campaign',
    'dedicated_account_capacity_evidence_for_each_slot',
    'root_issued_live_selection_for_each_slot',
    'private_conversion_materials_and_grader_release_authority',
    'actual_primary_and_secondary_grades_from_fresh_sessions',
    'recorded_automation_of_branch_requests_from_standing_configuration',
    'grading_custody_attestation',
)
need = paired.need


def _read(reference, code='campaign_pin_mismatch'):
    """The exact pinned bytes; any change refuses."""
    need(isinstance(reference, dict) and set(reference) == {'path', 'bytes', 'sha256'}, 'campaign_pin_shape')
    try:
        content = paired.read_bytes(reference['path'])
    except OSError:
        raise paired.Refusal(code) from None
    need(len(content) == reference['bytes'] and hashlib.sha256(content).hexdigest() == reference['sha256'], code)
    return content


def _json(reference):
    return paired.decode_json(_read(reference))


def _short_json(reference):
    """A runner-style {path, sha256} pin."""
    need(isinstance(reference, dict) and set(reference) == {'path', 'sha256'}, 'campaign_pin_shape')
    try:
        content = paired.read_bytes(reference['path'])
    except OSError:
        raise paired.Refusal('campaign_pin_mismatch') from None
    need(hashlib.sha256(content).hexdigest() == reference['sha256'], 'campaign_pin_mismatch')
    return paired.decode_json(content)


# ---------------------------------------------------------------- slots

def _schedule(profile, campaign_id):
    need(isinstance(profile, dict) and profile.get('schema') == weekly.PROFILE_SCHEMA, 'campaign_profile_shape')
    need(profile.get('campaign_id') == campaign_id, 'campaign_id_mismatch')
    schedule = profile.get('schedule')
    paired.shape(schedule, 'weekday time window_minutes', 'campaign_profile_shape')
    need(type(schedule['weekday']) is int and 0 <= schedule['weekday'] <= 6 and type(schedule['time']) is str
         and len(schedule['time']) == 5 and type(schedule['window_minutes']) is int
         and schedule['window_minutes'] > 0, 'campaign_profile_shape')
    return schedule


def _slot(receipt, schedule, campaign_id):
    """The runner's own slot identity, checked against the actual schedule."""
    need(receipt['campaign_id'] == campaign_id, 'campaign_id_mismatch')
    start, end = paired.utc(receipt['scheduled_at']), paired.utc(receipt['expires_at'])
    need(start.weekday() == schedule['weekday'] and start.strftime('%H:%M') == schedule['time']
         and start.second == 0 and end - start == dt.timedelta(minutes=schedule['window_minutes']),
         'campaign_slot_off_schedule')
    need(receipt['slot_id'] == str(uuid.uuid5(weekly.NAMESPACE, campaign_id + '|' + receipt['scheduled_at'])),
         'campaign_slot_identity')
    return start


# ---------------------------------------------------------------- one week

class _Week:
    def __init__(self, index, row, campaign_id):
        paired.shape(row, WEEK_FIELDS, 'campaign_week_shape')
        self.name = f'week-{index}'
        self.schedule = _schedule(_json(row['runner_profile']), campaign_id)
        self.receipt_pin = row['receipt']
        self.receipt = receipt = _json(row['receipt'])
        need(isinstance(receipt, dict) and receipt.get('schema') == weekly.RECEIPT_SCHEMA
             and set(receipt) == RECEIPT_FIELDS, 'campaign_receipt_shape')
        report._token(receipt['status'])
        if receipt['code'] is not None:
            report._token(receipt['code'])
        self.start = _slot(receipt, self.schedule, campaign_id)
        self.slot_id = receipt['slot_id']
        self.occurrence_id = str(uuid.uuid5(weekly.NAMESPACE, 'occurrence|' + self.slot_id))
        self.agent = self.summary = self.binding = None
        if receipt['agent'] is not None:
            self.agent = _short_json(receipt['agent'])
            self.summary = report._agent(self.agent)
            attempts = receipt['attempts']
            need(isinstance(attempts, dict) and attempts == dict(
                planned=self.summary['planned'], admitted=self.summary['admitted'],
                not_started=self.summary['not_started'], records=len(self.summary['records'])),
                'campaign_receipt_agent_mismatch')
        if receipt['pair_binding'] is not None:
            need(self.agent is not None, 'campaign_binding_mismatch')
            self.binding = _json(receipt['pair_binding'])
            need(isinstance(self.binding, dict) and self.binding.get('request_id') == self.slot_id
                 and self.binding.get('campaign_id') == campaign_id
                 and self.binding.get('occurrence_id') == self.occurrence_id
                 and self.binding.get('agent_sha256') == receipt['agent']['sha256'], 'campaign_binding_mismatch')
        self._occurrence(row['occurrence_snapshot'], campaign_id)
        self.bridge = None if row['bridge_receipt'] is None else self._bridge(row['bridge_receipt'])

    def _occurrence(self, reference, campaign_id):
        self.state = self.report = self.join = self.routing = None
        if reference is None:
            return
        need(self.binding is not None, 'campaign_occurrence_without_binding')
        snapshot = _json(reference)
        paired.shape(snapshot, SNAPSHOT_FIELDS, 'campaign_snapshot_shape')
        need(snapshot['schema'] == occurrence.SNAPSHOT_SCHEMA, 'campaign_snapshot_shape')
        state = snapshot['state']
        need(isinstance(state, dict) and state.get('schema') == occurrence.STATE_SCHEMA, 'campaign_snapshot_shape')
        if state.get('pending') is not None:
            report._token(state['pending'])
        collection = None if snapshot['collection'] is None else _json(snapshot['collection'])
        routing = None if snapshot['routing'] is None else _json(snapshot['routing'])
        self.state, self.routing = state, routing
        if snapshot['report'] is None:
            need(snapshot['join'] is None and state['snapshot'] is None, 'campaign_snapshot_shape')
            return
        built, join = _json(snapshot['report']), _json(snapshot['join'])
        join_sha = paired.canonical_sha(join)
        campaign = built.get('campaign') or {}
        inputs = built.get('inputs') or {}
        # Identity joins between this report and this week's receipt; any break refuses.
        need(built.get('schema') == report.SCHEMA and built.get('report_sha256') == report.report_digest(built)
             and campaign.get('request_id') == self.slot_id and campaign.get('campaign_id') == campaign_id
             and campaign.get('occurrence_id') == self.occurrence_id
             and inputs.get('agent_sha256') == paired.canonical_sha(self.agent)
             and inputs.get('join_sha256') == join_sha and state['snapshot'] == join_sha[:20]
             and state['report_status'] == built['status'], 'campaign_identity_mismatch')
        grading = state['grading']
        need(isinstance(grading, dict), 'campaign_identity_mismatch')
        recorded = grading.get('routing')
        need((routing is None) == (recorded is None)
             and (routing is None or recorded.get('disputes') == routing.get('disputes')), 'campaign_routing_mismatch')
        # Validate through the existing report projection; never implement a second grading rule.
        need(built == report.build_report(self.agent, join, built['run_date']),
             'campaign_report_projection_mismatch')
        if collection is not None:
            need(isinstance(collection, dict)
                 and collection.get('schema') == 'cairn.grader-delivery-collection/1'
                 and collection.get('join') == join
                 and collection.get('agreement') == join['aggregate']['declared_raw_agreement'],
                 'campaign_collection_mismatch')
        need(grading == occurrence._grading(join, collection, routing), 'campaign_grading_mismatch')
        self.report, self.join = built, join

    def _bridge(self, reference):
        """Observed origin only: a bridge receipt for exactly this runner receipt."""
        receipt = _json(reference)
        need(isinstance(receipt, dict) and receipt.get('schema') == bridge.RECEIPT
             and receipt.get('runtime_admitted') is False, 'campaign_bridge_mismatch')
        _short_json(receipt['selection'])
        binding = receipt['binding']
        need(receipt['runner_receipt'] == dict(path=self.receipt_pin['path'], sha256=self.receipt_pin['sha256'])
             and receipt['runner'] == self.receipt and receipt['status'] == self.receipt['status']
             and binding['runner']['slot_id'] == self.slot_id, 'campaign_bridge_mismatch')
        return dict(origin='candidate_run_bridge', candidate_request_id=binding['candidate']['request_id'],
                    bridge_receipt_sha256=reference['sha256'])

    # ---- criteria

    def reasons(self):
        return [] if self.report is None else list(self.report['incomplete_reasons'])

    def attempts(self):
        r, unmet = self.receipt, []
        if r['status'] != 'complete':
            unmet.append(f"receipt_{r['status']}:{r['code']}")
        if r['attempts'] != dict(planned=WEEKLY_ATTEMPTS, admitted=WEEKLY_ATTEMPTS, not_started=0,
                                 records=WEEKLY_ATTEMPTS):
            unmet.append('attempts_not_all_finished')
        if self.summary is not None and (self.summary['state'] != 'complete' or self.summary['stopped_reason']):
            unmet.append('agent_admission_not_complete')
        limits = r['limit_assessment']
        if not (isinstance(limits, dict) and limits.get('limits') == paired.ORIGINAL_LIMITS
                and (limits.get('within_original_limits'), limits.get('failed'), limits.get('unknown'))
                == (WEEKLY_ATTEMPTS, 0, 0)):
            unmet.append('original_limits_not_observed_within')
        if (r['baseline_custody'] or {}).get('verified') != WEEKLY_ATTEMPTS:
            unmet.append('baseline_custody_unverified')
        # A declared execution contract is never an observation of it.
        if (r['original_execution'] or {}).get('observed') is None:
            unmet.append('original_execution_unobserved')
        attempts = None
        if self.report is None:
            unmet.append('occurrence_report_missing')
        else:
            attempts = self.report['attempts']
            if attempts['provider_limit_losses'] or attempts['provider_auth_failures']:
                unmet.append('provider_limit_or_auth_losses')
            unmet += ['report:' + k for k in self.reasons() if k in ATTEMPT_REASONS]
        evidence = dict(slot_id=self.slot_id, receipt_status=r['status'], receipt_code=r['code'],
                        attempts=r['attempts'],
                        limit_assessment=None if not isinstance(limits, dict) else
                        {k: limits.get(k) for k in ('within_original_limits', 'failed', 'unknown')},
                        report_attempts=attempts)
        return evidence, unmet

    def grading(self):
        if self.report is None:
            return dict(slot_id=self.slot_id, occurrence_pending=self.state and self.state['pending']), \
                ['occurrence_report_missing']
        g, r, unmet = self.state['grading'], self.report['grading'], []
        changed = r['changed_pairs']
        need(g['changed_pairs'] == changed and g['agreement'] == r['agreement'], 'campaign_identity_mismatch')
        reasons = self.reasons()
        selection_complete = (self.join['aggregate'].get('selection_completeness') == 'declared'
                              and not SELECTION_REASONS & set(reasons))
        if not selection_complete:
            unmet.append('changed_pair_selection_incomplete')
        if r['second_grader_reviewed'] < changed:
            unmet.append('second_grader_missing')
        if not g['all_graded'] or r['dual_determinate'] < changed:
            unmet.append('grades_not_determinate')
        unmet += ['report:' + k for k in reasons
                  if k not in ATTEMPT_REASONS | SELECTION_REASONS | {OWNER_REASON}]
        agreement = dict(r['agreement'])
        if changed == 0:
            # No changed pair: nothing to grade and no rate to report; never 100%.
            need(agreement == dict(numerator=0, denominator=0, rate=None), 'campaign_identity_mismatch')
            bundle_status = next((p.get('status') for p in self.state['phases'] if p['phase'] == 'bundle'), None)
            if bundle_status != 'no_grading_needed':
                unmet.append('zero_changed_without_no_grading_needed')
            agreement['state'] = 'not_applicable'
        else:
            agreement['state'] = 'reported' if agreement['rate'] is not None else 'undefined'
            if agreement['rate'] is None:
                unmet.append('agreement_undefined')
        evidence = dict(slot_id=self.slot_id, changed_pairs=changed, second_grader_reviewed=r['second_grader_reviewed'],
                        dual_determinate=r['dual_determinate'], all_graded=g['all_graded'],
                        selection_complete=selection_complete, agreement=agreement,
                        occurrence_pending=self.state['pending'])
        return evidence, unmet

    def disputes(self):
        if self.report is None:
            return dict(slot_id=self.slot_id), ['occurrence_report_missing']
        g, unmet = self.state['grading'], []
        pending = [dict(case=d['case'], seed=d['seed']) for d in self.report['disagreements_pending_owner']]
        count = g['disagreements']
        need(len(pending) == count, 'campaign_identity_mismatch')
        expected_pairs = {p['packet']['pair_id'] for p in self.join['pairs']
                          if p['declared_comparison'] == 'disagreement'}
        routed_pairs = [] if self.routing is None else [d['pair_id'] for d in self.routing['disputes']]
        routed = (count == 0 or (g['disagreements_routed'] is True and self.routing is not None
                                 and g['routing']['destination'] == 'owner_only'
                                 and len(routed_pairs) == len(set(routed_pairs)) == count
                                 and set(routed_pairs) == expected_pairs))
        if not routed:
            unmet.append('disagreements_unrouted')
        evidence = dict(slot_id=self.slot_id, disagreements=count, routed=routed,
                        destination=None if g['routing'] is None else g['routing']['destination'],
                        resolution=g['resolution'], pending_owner=pending)
        return evidence, unmet


# ---------------------------------------------------------------- verdict

def _criterion(rows):
    unmet = [f'{week}:{reason}' for week, (_, reasons) in rows for reason in reasons]
    return dict(state='unmet' if unmet else 'met', unmet=unmet, weeks=[evidence for _, (evidence, _) in rows])


def _evaluate(input_pin):
    document = _json(input_pin)
    paired.shape(document, 'schema campaign_id weeks', 'campaign_input_shape')
    need(document['schema'] == INPUT_SCHEMA and isinstance(document['weeks'], list)
         and len(document['weeks']) == 2, 'campaign_input_shape')
    campaign_id = document['campaign_id']
    paired.identifier(campaign_id)  # the runner's campaign identity is a canonical UUID
    weeks = [_Week(index, row, campaign_id) for index, row in enumerate(document['weeks'], 1)]
    first, second = weeks
    need(first.schedule == second.schedule, 'campaign_schedule_changed')
    need(first.slot_id != second.slot_id, 'campaign_weeks_duplicate')
    need(second.start - first.start == WEEK, 'campaign_weeks_not_consecutive')
    criteria = dict(
        consecutive_slots=dict(state='met', unmet=[], slots=[
            dict(slot_id=w.slot_id, scheduled_at=w.receipt['scheduled_at'], expires_at=w.receipt['expires_at'],
                 receipt_sha256=w.receipt_pin['sha256']) for w in weeks]),
        complete_attempts=_criterion([(w.name, w.attempts()) for w in weeks]),
        second_grader_coverage=_criterion([(w.name, w.grading()) for w in weeks]),
        disputes_routed=_criterion([(w.name, w.disputes()) for w in weeks]),
        # Declarations and booleans cannot establish this; no receipt records the automation.
        branch_request_without_manual_setup=dict(state='unproved', unmet=['recorded_automation_absent'],
                                                 observed=[w.bridge for w in weeks]))
    unmet = sorted(f'{name}:{reason}' for name, criterion in criteria.items() for reason in criterion['unmet'])
    return dict(schema=VERDICT_SCHEMA, campaign_id=campaign_id, input_sha256=input_pin['sha256'],
                status='not_complete' if unmet else 'complete', unmet=unmet, criteria=criteria,
                week_reports=[dict(slot_id=w.slot_id, report_status=w.report and w.report['status'],
                                   incomplete_reasons=w.reasons()) for w in weeks],
                remaining_live_evidence=list(REMAINING_LIVE_EVIDENCE),
                custody='unknown', actual_campaign_success=False, runtime_admission=False)


def evaluate(input_pin):
    """Return the typed two-week verdict for one pinned campaign input; refuse on any changed input."""
    try:
        return _evaluate(input_pin)
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
        raise paired.Refusal('campaign_invalid_or_unavailable') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', required=True, help='campaign completion input JSON path')
    parser.add_argument('--input-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        content = paired.read_bytes(args.input)
        verdict = evaluate(dict(path=args.input, bytes=len(content), sha256=args.input_sha256))
    except paired.Refusal as refusal:
        print(json.dumps(dict(status='refused', code=str(refusal))))
        return 2
    except OSError:
        print(json.dumps(dict(status='refused', code='campaign_invalid_or_unavailable')))
        return 2
    print(json.dumps(verdict, sort_keys=True))
    return 0 if verdict['status'] == 'complete' else 3


if __name__ == '__main__':
    sys.exit(main())
