"""Synthetic two-week campaigns (126 attempts and 63 pairs per week) for the
CAIRN-107 completion verdict. Occurrences run through the real orchestration
with the fake event fabric and fake note store, and their persisted state
snapshots are the verifier's input. Runner and bridge receipts are synthetic
documents in the runner's exact receipt shape. No cases, oracles, providers,
models or stores are used."""
import datetime as dt
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
import uuid

import trial_campaign_completion as completion
import trial_candidate_run_bridge as bridge
import trial_legacy_occurrence as occurrence
import trial_paired_request as paired
import trial_run_report as report
import trial_weekly_run as weekly
from test_trial_grader_delivery import config, NOW
from test_trial_legacy_grading_bundle import CHANGED
from test_trial_legacy_occurrence import Occurrence, UNCHANGED

CAMPAIGN = str(uuid.uuid5(uuid.NAMESPACE_URL, 'synthetic-campaign'))
WEEK_1 = '2026-10-15T12:00:00Z'  # a Thursday
WEEK_2 = '2026-10-22T12:00:00Z'
SCHEDULE = dict(weekday=3, time='12:00', window_minutes=360)
AGREE = lambda pair, slot: ['pass', 'fail']  # noqa: E731
CHANGED_STATES = lambda i, s: CHANGED.get((i, s), UNCHANGED)  # noqa: E731


def C(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def pin(path, content):
    Path(path).write_bytes(content)
    return dict(path=str(path), bytes=len(content), sha256=hashlib.sha256(content).hexdigest())


def short(reference):
    return dict(path=reference['path'], sha256=reference['sha256'])


def plus_minutes(iso, minutes):
    return (paired.utc(iso) + dt.timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')


class Week:
    """One weekly slot: runner profile and receipt, the occurrence's state snapshot, optional bridge receipt."""

    def __init__(self, root, scheduled_at, state_of=lambda i, s: UNCHANGED, campaign=CAMPAIGN):
        self.root = root
        root.mkdir(parents=True)
        self.campaign = campaign
        self.scheduled_at = scheduled_at
        self.slot_id = str(uuid.uuid5(weekly.NAMESPACE, campaign + '|' + scheduled_at))
        self.o = Occurrence(root, state_of)
        self.o.f.binding.update(request_id=self.slot_id, campaign_id=campaign,
                                occurrence_id=str(uuid.uuid5(weekly.NAMESPACE, 'occurrence|' + self.slot_id)))
        self.o.input['conversion_request'] = self.o.f.save()
        self.state = self.snapshot = None

    def _run(self, *, fabric):
        self.state, self.snapshot = occurrence.run_occurrence_snapshot(
            self.o.pin_input(), self.o.out, fabric=self.o.fabric if fabric else None,
            config=config() if fabric else None, publisher=self.o.notes.publisher, now=NOW)
        return self

    def unchanged(self):
        return self._run(fabric=False)

    def graded(self, choose=AGREE, *, fail_routing=False):
        self.o.prepared()
        self.o.answer(choose)
        if fail_routing:
            self.o.fabric.fail_publish = 1
        return self._run(fabric=True)

    def receipt(self, **change):
        agent = self.o.f.request['agent']
        doc = dict(
            schema=weekly.RECEIPT_SCHEMA, slot_id=self.slot_id, campaign_id=self.campaign,
            scheduled_at=self.scheduled_at, expires_at=plus_minutes(self.scheduled_at, SCHEDULE['window_minutes']),
            current=dict(ref='main', commit='a' * 40), candidate=dict(ref='feature/x', commit='b' * 40),
            harness_commit='c' * 40, executor='legacy-harness', capacity=dict(receipt_sha256='d' * 64),
            selection=dict(sha256='e' * 64, selected_by=CAMPAIGN, proof='declared_unauthenticated'),
            original_execution=dict(declared=dict(model='synthetic-model'), observed=dict(effort='high'),
                                    proof='harness_observed_copy'),
            status='complete', code=None, agent=short(agent),
            attempts=dict(planned=126, admitted=126, not_started=0, records=126), harness_exit_code=0,
            pair_binding=self.o.f.request['binding'],
            limit_assessment=dict(within_original_limits=126, failed=0, unknown=0, reasons={},
                                  limits=dict(paired.ORIGINAL_LIMITS)),
            baseline_custody=dict(verified=126, proof='harness_retained_baseline'),
            pair_identity='declared_binding',
            report_input=dict(schema='cairn.task-eval.agent/1', agent=short(agent)), runtime_admission=False)
        doc.update(change)
        return doc

    def bridge_receipt(self, receipt_doc, receipt_pin):
        selection = pin(self.root/'bridge-selection.json', C(dict(schema=bridge.SCHEMA)))
        return dict(schema=bridge.RECEIPT, selection=short(selection),
                    binding=dict(candidate=dict(request_id='candidate-request', occurrence_id='candidate-occurrence'),
                                 runner=dict(request_id=self.slot_id, slot_id=self.slot_id), campaign_id=self.campaign),
                    status=receipt_doc['status'], runner_spec=None, runner_receipt=short(receipt_pin),
                    runner=receipt_doc, runtime_admitted=False)

    def row(self, receipt_doc=None, *, bridged=True, schedule=SCHEDULE):
        receipt_doc = receipt_doc or self.receipt()
        receipt_pin = pin(self.root/'receipt.json', C(receipt_doc))
        profile = pin(self.root/'runner-profile.json', C(dict(schema=weekly.PROFILE_SCHEMA, campaign_id=self.campaign,
                                                             schedule=schedule)))
        bridge_pin = pin(self.root/'bridge-receipt.json', C(self.bridge_receipt(receipt_doc, receipt_pin))) \
            if bridged else None
        return dict(runner_profile=profile, receipt=receipt_pin, occurrence_snapshot=self.snapshot,
                    bridge_receipt=bridge_pin)


class Case(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.serial = 0

    def week(self, name, scheduled_at, state_of=lambda i, s: UNCHANGED):
        return Week(self.root/name, scheduled_at, state_of)

    def pin_input(self, rows, campaign_id=CAMPAIGN):
        self.serial += 1
        document = dict(schema=completion.INPUT_SCHEMA, campaign_id=campaign_id, weeks=rows)
        return pin(self.root/f'campaign-{self.serial}.json', C(document))

    def evaluate(self, rows):
        return completion.evaluate(self.pin_input(rows))

    def refused(self, code, rows):
        with self.assertRaisesRegex(paired.Refusal, '^' + code + '$'):
            self.evaluate(rows)

    def assert_never_success(self, verdict):
        self.assertEqual(verdict['status'], 'not_complete')
        self.assertEqual((verdict['custody'], verdict['actual_campaign_success'], verdict['runtime_admission']),
                         ('unknown', False, False))
        self.assertEqual(verdict['remaining_live_evidence'], list(completion.REMAINING_LIVE_EVIDENCE))
        self.assertEqual(verdict['criteria']['branch_request_without_manual_setup']['state'], 'unproved')

    def complete_weeks(self):
        return self.week('w1', WEEK_1).unchanged(), self.week('w2', WEEK_2, CHANGED_STATES).graded()


class CompleteCampaignTests(Case):
    def test_complete_weeks_meet_every_run_criterion_and_manual_setup_stays_unproved(self):
        one, two = self.complete_weeks()
        verdict = self.evaluate([one.row(), two.row()])
        self.assert_never_success(verdict)
        criteria = verdict['criteria']
        for name in ('consecutive_slots', 'complete_attempts', 'second_grader_coverage', 'disputes_routed'):
            self.assertEqual((name, criteria[name]['state']), (name, 'met'))
        self.assertEqual([s['slot_id'] for s in criteria['consecutive_slots']['slots']], [one.slot_id, two.slot_id])
        self.assertEqual(verdict['unmet'], ['branch_request_without_manual_setup:recorded_automation_absent'])
        # The bridge receipts are recorded as observed origin, which does not prove the absence of manual setup.
        observed = criteria['branch_request_without_manual_setup']['observed']
        self.assertEqual([o['origin'] for o in observed], ['candidate_run_bridge'] * 2)
        self.assertEqual([w['report_status'] for w in verdict['week_reports']], ['declared_complete'] * 2)

    def test_zero_changed_pairs_report_no_rate_and_proof_of_complete_selection(self):
        one, two = self.complete_weeks()
        weeks = self.evaluate([one.row(), two.row()])['criteria']['second_grader_coverage']['weeks']
        self.assertEqual(weeks[0]['agreement'], dict(numerator=0, denominator=0, rate=None, state='not_applicable'))
        self.assertEqual((weeks[0]['changed_pairs'], weeks[0]['selection_complete']), (0, True))
        # Agreement is the existing join's figure for the changed pairs only; never pooled or recomputed.
        self.assertEqual(weeks[1]['agreement'], dict(numerator=2, denominator=2, rate=1.0, state='reported'))
        self.assertEqual(weeks[1]['changed_pairs'], 2)

    def test_without_bridge_receipts_no_origin_is_observed(self):
        one, two = self.complete_weeks()
        verdict = self.evaluate([one.row(bridged=False), two.row(bridged=False)])
        self.assertEqual(verdict['criteria']['branch_request_without_manual_setup']['observed'], [None, None])
        self.assert_never_success(verdict)

    def test_cli_prints_the_verdict_and_exits_3_while_not_complete(self):
        one, two = self.complete_weeks()
        reference = self.pin_input([one.row(), two.row()])
        with redirect_stdout(io.StringIO()) as printed:
            code = completion.main(['--input', reference['path'], '--input-sha256', reference['sha256']])
        self.assertEqual((code, json.loads(printed.getvalue())['status']), (3, 'not_complete'))
        with redirect_stdout(io.StringIO()) as printed:
            code = completion.main(['--input', reference['path'], '--input-sha256', '0' * 64])
        self.assertEqual((code, json.loads(printed.getvalue())['code']), (2, 'campaign_pin_mismatch'))


class AttemptTests(Case):
    def test_a_provider_limit_loss_week_is_unmet_with_its_receipt_code(self):
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2)
        two.o.f.admission = dict(state='stopped_provider_failure', stop=dict(reason='capacity_exhausted'),
                                 planned=126, admitted=111,
                                 not_started=[dict(run_id=r['run_id']) for r in two.o.f.records[111:]])
        two.o.f.records = two.o.f.records[:111]
        two.o.input['conversion_request'] = two.o.f.save()
        loss = two.receipt(status='incomplete', code='provider_cap_stop', pair_binding=None, limit_assessment=None,
                           baseline_custody=None, attempts=dict(planned=126, admitted=111, not_started=15, records=111),
                           original_execution=dict(declared=dict(model='synthetic-model'), observed=None, proof=None))
        verdict = self.evaluate([one.row(), two.row(loss)])
        self.assert_never_success(verdict)
        attempts = verdict['criteria']['complete_attempts']
        self.assertEqual(attempts['state'], 'unmet')
        for reason in ('receipt_incomplete:provider_cap_stop', 'attempts_not_all_finished',
                       'agent_admission_not_complete', 'original_limits_not_observed_within',
                       'original_execution_unobserved', 'occurrence_report_missing'):
            self.assertIn('week-2:' + reason, attempts['unmet'])
        self.assertFalse([u for u in attempts['unmet'] if u.startswith('week-1:')])
        self.assertEqual(verdict['criteria']['second_grader_coverage']['unmet'], ['week-2:occurrence_report_missing'])

    def test_a_declared_execution_without_an_observation_never_counts(self):
        one, two = self.complete_weeks()
        declared_only = two.receipt(original_execution=dict(declared=dict(model='synthetic-model'), observed=None,
                                                            proof=None))
        verdict = self.evaluate([one.row(), two.row(declared_only)])
        self.assertEqual(verdict['criteria']['complete_attempts']['unmet'], ['week-2:original_execution_unobserved'])

    def test_receipt_counts_must_match_its_agent_document(self):
        one, two = self.complete_weeks()
        self.refused('campaign_receipt_agent_mismatch',
                     [one.row(), two.row(two.receipt(attempts=dict(planned=126, admitted=126, not_started=0,
                                                                   records=125)))])


class SlotTests(Case):
    def test_a_gap_a_duplicate_and_a_reversal_refuse(self):
        one = self.week('w1', WEEK_1).unchanged()
        later = self.week('w3', '2026-10-29T12:00:00Z').unchanged()
        self.refused('campaign_weeks_not_consecutive', [one.row(), later.row()])
        self.refused('campaign_weeks_duplicate', [one.row(), one.row()])
        two = self.week('w2', WEEK_2).unchanged()
        self.refused('campaign_weeks_not_consecutive', [two.row(), one.row()])

    def test_a_slot_off_the_actual_schedule_or_with_another_identity_refuses(self):
        one, two = self.complete_weeks()
        self.refused('campaign_slot_off_schedule', [one.row(schedule=dict(SCHEDULE, time='06:00')), two.row()])
        self.refused('campaign_slot_identity', [one.row(), two.row(two.receipt(slot_id=one.slot_id))])

    def test_weeks_from_different_schedules_or_campaigns_refuse(self):
        one, two = self.complete_weeks()
        shorter = two.receipt(expires_at=plus_minutes(WEEK_2, 60))
        self.refused('campaign_schedule_changed', [one.row(), two.row(shorter, schedule=dict(SCHEDULE, window_minutes=60))])
        with self.assertRaisesRegex(paired.Refusal, '^campaign_id_mismatch$'):
            completion.evaluate(self.pin_input([one.row(), two.row()], campaign_id=str(uuid.uuid4())))


class GradingTests(Case):
    def test_a_missing_second_grade_is_unmet_and_distinct_from_pending_owner(self):
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2, CHANGED_STATES).graded(lambda pair, slot: ['pass', 'fail']
                                                             if slot == 'primary' else None)
        self.assertEqual(two.state['pending'], 'awaiting_grades')
        verdict = self.evaluate([one.row(), two.row()])
        coverage = verdict['criteria']['second_grader_coverage']
        # With no determinate pair the existing join defines no rate; that is reported, not invented.
        self.assertCountEqual(coverage['unmet'], ['week-2:second_grader_missing', 'week-2:grades_not_determinate',
                                                  'week-2:report:changed_pairs_not_dual_graded',
                                                  'week-2:agreement_undefined'])
        self.assertEqual(coverage['weeks'][1]['agreement'], dict(numerator=0, denominator=0, rate=None,
                                                                 state='undefined'))
        self.assertEqual(coverage['weeks'][1]['occurrence_pending'], 'awaiting_grades')
        self.assertEqual(verdict['criteria']['disputes_routed']['state'], 'met')
        self.assertEqual(verdict['criteria']['disputes_routed']['weeks'][1]['pending_owner'], [])

    def test_a_routed_dispute_pending_owner_meets_routing_and_coverage_but_the_report_stays_incomplete(self):
        first = None

        def split(pair, slot):
            nonlocal first
            first = first or pair
            return ['pass', 'fail'] if pair == first or slot == 'primary' else ['fail', 'pass']
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2, CHANGED_STATES).graded(split)
        verdict = self.evaluate([one.row(), two.row()])
        criteria = verdict['criteria']
        self.assertEqual((criteria['second_grader_coverage']['state'], criteria['disputes_routed']['state']),
                         ('met', 'met'))
        week = criteria['disputes_routed']['weeks'][1]
        self.assertEqual((week['disagreements'], week['routed'], week['destination'], week['resolution']),
                         (1, True, 'owner_only', 'owner_pending'))
        self.assertEqual(len(week['pending_owner']), 1)
        self.assertEqual(criteria['second_grader_coverage']['weeks'][1]['agreement'],
                         dict(numerator=1, denominator=2, rate=0.5, state='reported'))
        self.assertEqual(verdict['week_reports'][1],
                         dict(slot_id=two.slot_id, report_status='incomplete',
                              incomplete_reasons=['disagreements_pending_owner']))
        self.assert_never_success(verdict)

    def test_an_unrouted_disagreement_is_unmet(self):
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2, CHANGED_STATES).graded(
            lambda pair, slot: ['pass', 'fail'] if slot == 'primary' else ['fail', 'pass'], fail_routing=True)
        self.assertEqual(two.state['pending'], 'dispute_routing_uncertain_retry')
        verdict = self.evaluate([one.row(), two.row()])
        self.assertEqual(verdict['criteria']['disputes_routed']['unmet'], ['week-2:disagreements_unrouted'])
        self.assertEqual(verdict['criteria']['disputes_routed']['weeks'][1]['routed'], False)

    def test_an_undetermined_selection_cannot_pass_as_zero_changed(self):
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2, lambda i, s: ('undetermined', 'undetermined')).unchanged()
        coverage = self.evaluate([one.row(), two.row()])['criteria']['second_grader_coverage']
        self.assertIn('week-2:changed_pair_selection_incomplete', coverage['unmet'])
        self.assertIs(coverage['weeks'][1]['selection_complete'], False)


class PinTests(Case):
    def rewrite(self, path, change):
        path = Path(path)
        path.chmod(0o600)
        path.write_bytes(change(path.read_bytes()))

    def test_every_changed_input_refuses(self):
        def edit_json(update):
            return lambda content: C(update(json.loads(content)))
        targets = {
            'input': lambda one, two, ref: ref['path'],
            'receipt': lambda one, two, ref: two.root/'receipt.json',
            'profile': lambda one, two, ref: two.root/'runner-profile.json',
            'snapshot': lambda one, two, ref: two.snapshot['path'],
            'report': lambda one, two, ref: two.o.out/'report'/two.state['snapshot']/'report.json',
            'join': lambda one, two, ref: two.o.out/'report'/two.state['snapshot']/'join.json',
            'agent': lambda one, two, ref: two.o.f.request['agent']['path'],
            'pair_binding': lambda one, two, ref: two.o.f.request['binding']['path'],
            'bridge': lambda one, two, ref: two.root/'bridge-receipt.json',
            'bridge_selection': lambda one, two, ref: two.root/'bridge-selection.json',
        }
        for name, target in targets.items():
            with self.subTest(name=name):
                root = self.root/name
                one = Week(root/'w1', WEEK_1).unchanged()
                two = Week(root/'w2', WEEK_2, CHANGED_STATES).graded()
                reference = self.pin_input([one.row(), two.row()])
                completion.evaluate(reference)
                self.rewrite(target(one, two, reference), lambda content: content.replace(b'}', b' }', 1))
                with self.assertRaisesRegex(paired.Refusal, '^(campaign_pin_mismatch|pin_mismatch)$'):
                    completion.evaluate(reference)

    def test_collection_and_routing_documents_are_pinned(self):
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2, CHANGED_STATES).graded(
            lambda pair, slot: ['pass', 'fail'] if slot == 'primary' else ['fail', 'pass'])
        reference = self.pin_input([one.row(), two.row()])
        snapshot = json.loads(Path(two.snapshot['path']).read_bytes())
        for name in ('collection', 'routing'):
            self.rewrite(snapshot[name]['path'], lambda content: content + b' ')
            with self.subTest(name=name), self.assertRaisesRegex(paired.Refusal, '^campaign_pin_mismatch$'):
                completion.evaluate(reference)
            self.rewrite(snapshot[name]['path'], lambda content: content[:-1])

    def test_a_consistently_repinned_snapshot_of_another_week_breaks_the_identity_join(self):
        one, two = self.complete_weeks()
        swapped = dict(two.row(), occurrence_snapshot=one.snapshot)
        self.refused('campaign_identity_mismatch', [one.row(), swapped])

    def test_a_repinned_state_whose_routing_disagrees_with_the_routing_document_refuses(self):
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2, CHANGED_STATES).graded(
            lambda pair, slot: ['pass', 'fail'] if slot == 'primary' else ['fail', 'pass'])
        snapshot = json.loads(Path(two.snapshot['path']).read_bytes())
        snapshot['state']['grading']['routing']['disputes'] = []
        forged = pin(two.root/'forged-snapshot.json', C(snapshot))
        self.refused('campaign_routing_mismatch', [one.row(), dict(two.row(), occurrence_snapshot=forged)])

    def forged(self, week, *, report_change=None, routing_disputes=None):
        """A consistently re-pinned snapshot with one altered field, as a forger would write it."""
        snapshot = json.loads(Path(week.snapshot['path']).read_bytes())
        if report_change is not None:
            built = json.loads(Path(snapshot['report']['path']).read_bytes())
            report_change(built)
            built['report_sha256'] = report.report_digest(built)
            snapshot['report'] = pin(week.root/'forged-report.json', C(built))
        if routing_disputes is not None:
            routing = json.loads(Path(snapshot['routing']['path']).read_bytes())
            routing['disputes'] = routing_disputes
            snapshot['routing'] = pin(week.root/'forged-routing.json', C(routing))
            snapshot['state']['grading']['routing']['disputes'] = routing_disputes
            # Keep the summary internally consistent; test the routing identities separately.
            snapshot['state']['grading']['disagreements_routed'] = (
                len(routing_disputes) == snapshot['state']['grading']['disagreements'])
        return dict(week.row(), occurrence_snapshot=pin(week.root/'forged-snapshot.json', C(snapshot)))

    def test_a_report_for_another_slot_or_agent_breaks_the_identity_join(self):
        one, two = self.complete_weeks()
        changes = {
            'request_id': lambda built: built['campaign'].update(request_id=one.slot_id),
            'occurrence_id': lambda built: built['campaign'].update(occurrence_id=str(uuid.uuid4())),
            'agent_sha256': lambda built: built['inputs'].update(agent_sha256='0' * 64),
        }
        for name, change in changes.items():
            with self.subTest(name=name):
                self.refused('campaign_identity_mismatch', [one.row(), self.forged(two, report_change=change)])

    def test_repinned_report_cannot_override_its_source_join(self):
        one, two = self.complete_weeks()
        self.refused('campaign_report_projection_mismatch', [
            one.row(), self.forged(two, report_change=lambda built: built['attempts'].update(finished=0))])

    def test_same_count_routing_for_other_pairs_is_unrouted(self):
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2, CHANGED_STATES).graded(
            lambda pair, slot: ['pass', 'fail'] if slot == 'primary' else ['fail', 'pass'])
        snapshot = json.loads(Path(two.snapshot['path']).read_bytes())
        routing = json.loads(Path(snapshot['routing']['path']).read_bytes())
        for index, dispute in enumerate(routing['disputes']):
            dispute['pair_id'] = 'wrong-pair-' + str(index)
        verdict = self.evaluate([one.row(), self.forged(two, routing_disputes=routing['disputes'])])
        self.assertEqual(verdict['criteria']['disputes_routed']['state'], 'unmet')

    def test_routing_with_fewer_disputes_than_disagreements_is_unrouted(self):
        one = self.week('w1', WEEK_1).unchanged()
        two = self.week('w2', WEEK_2, CHANGED_STATES).graded(
            lambda pair, slot: ['pass', 'fail'] if slot == 'primary' else ['fail', 'pass'])
        verdict = self.evaluate([one.row(), self.forged(two, routing_disputes=[])])
        self.assertEqual(verdict['criteria']['disputes_routed']['unmet'], ['week-2:disagreements_unrouted'])

    def test_a_bridge_receipt_naming_this_receipt_but_carrying_another_run_refuses(self):
        one, two = self.complete_weeks()
        row = two.row()
        forged = json.loads(Path(row['bridge_receipt']['path']).read_bytes())
        forged['runner'] = two.receipt(code='other')
        self.refused('campaign_bridge_mismatch',
                     [one.row(), dict(row, bridge_receipt=pin(two.root/'forged-bridge.json', C(forged)))])
        forged = json.loads(Path(row['bridge_receipt']['path']).read_bytes())
        forged['binding']['runner']['slot_id'] = one.slot_id
        self.refused('campaign_bridge_mismatch',
                     [one.row(), dict(row, bridge_receipt=pin(two.root/'forged-bridge.json', C(forged)))])

    def test_a_bridge_receipt_for_another_runner_receipt_refuses(self):
        one, two = self.complete_weeks()
        row = two.row()
        other = one.row()
        self.refused('campaign_bridge_mismatch', [one.row(), dict(row, bridge_receipt=other['bridge_receipt'])])


if __name__ == '__main__':
    unittest.main()
