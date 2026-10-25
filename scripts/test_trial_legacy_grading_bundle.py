"""Synthetic full-shape legacy runs only: 126 attempts, 63 pairs. No cases,
oracles, providers, live delivery or grading; grades come from the fake fabric."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import trial_blind_packet_draft as packet
import trial_grader_delivery as grader
import trial_grader_delivery_plan as delivery_plan
import trial_grading_custody as custody
import trial_legacy_blind_selection as legacy
import trial_legacy_grading_bundle as adapter
import trial_paired_request as paired
import trial_run_report as report
from test_trial_grader_delivery import FakeFabric, config, OWNER, PRIMARY, SECONDARY, MODELS, PUBLISHER, NOW, ROUTE
from test_trial_grading_custody import fixture as sdk_fixture
from test_trial_legacy_blind_selection import Fixture, C, pin
from test_trial_legacy_blind_selection import SHAPE_TASKS

POLICY = dict(comparison='exact-sample-verdicts/1', verdict_labels=['pass', 'fail'],
              graders=dict(primary=dict(agent_id=PRIMARY, session_id=PRIMARY),
                           secondary=dict(agent_id=SECONDARY, session_id=SECONDARY)),
              excluded_agents=['engineer'], excluded_sessions=['engineer-session'],
              coordinator=dict(agent_id='coordinator', session_id='coordinator-session', delegation_sha256='f'*64))
CHANGED = {(3, 0): ('correct', 'mistake'), (9, 2): ('incomplete', 'correct')}


def existing(path):
    content = Path(path).read_bytes()
    return dict(path=str(path), bytes=len(content), sha256=hashlib.sha256(content).hexdigest())


def full_run(root, state_of, *, arms=('CURRENT', 'CAND'), schedule=True):
    outcomes = {(case, seed): state_of(index, seed) for index, case in enumerate(sorted(SHAPE_TASKS))
                for seed in range(3)}
    f = Fixture(root, outcomes=outcomes, tasks=SHAPE_TASKS, arms=arms)
    planned = sorted((dict(run_id=r['run_id'], case=r['case'], arm=r['arm'], seed=r['seed']) for r in f.records),
                     key=lambda r: r['run_id'])
    if schedule:  # what trial_weekly_run.spec's schedule_sha256 hashes
        f.binding['schedule_sha256'] = paired.canonical_sha(planned)
    legacy.convert(f.save(), root/'conversion')
    return f, existing(root/'conversion/receipt.json')


def draft(f, receipt_pin):
    receipt = json.loads(Path(receipt_pin['path']).read_bytes())
    subject = packet.approval_subject(receipt['selection'])
    approval = dict(schema='cairn.blind-declared-approval/2', state='approved',
                    subject_sha256=hashlib.sha256(C(subject)).hexdigest(),
                    observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
                    issuer_role=pin(f.root/'issuer.bin', b'PRIVATE declared role'))
    approval_pin = pin(f.root/'approval.json', C(approval))
    packet.draft(receipt['selection'], approval_pin, f.root/'draft', now=NOW)
    return existing(f.root/'draft/private/receipt.json'), approval_pin


class Case(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def report(self, f, join):
        agent = json.loads((f.run/'agent.json').read_bytes())
        return report.build_report(agent, join, '2026-10-15')


class ZeroChangedTests(Case):
    def test_all_unchanged_run_needs_no_grades_and_the_report_join_accepts_it(self):
        f, receipt = full_run(self.root, lambda i, s: ('correct', 'correct'))
        bundle, result = adapter.build(receipt, POLICY)
        self.assertEqual(result['status'], 'no_grading_needed')
        self.assertEqual(bundle['packets'], [])
        self.assertEqual(result['delivery_pairs'], [])
        join = adapter.report_join(bundle, result)
        self.assertEqual(join['aggregate']['selection'], dict(changed=0, unchanged=63, undetermined=0))
        self.assertEqual(join['aggregate']['changed_pair_coverage']['denominator'], 0)
        self.assertEqual(join['submissions'], [])
        rendered = self.report(f, join)
        self.assertEqual(rendered['status'], 'declared_complete', rendered['incomplete_reasons'])
        self.assertEqual(rendered['grading']['changed_pairs'], 0)
        self.assertEqual(rendered['grading']['agreement'], dict(numerator=0, denominator=0, rate=None))
        self.assertEqual(rendered['campaign']['request_id'], 'request-A')
        self.assertIs(result['grades_created'], False)

    def test_pinned_runner_plan_accepts_cold_readiness_diagnostic_floats(self):
        outcomes = {(case, seed): ('correct', 'correct') for case in SHAPE_TASKS for seed in range(3)}
        f = Fixture(self.root/'float-plan-run', outcomes=outcomes, tasks=SHAPE_TASKS,
                    arms=('CURRENT', 'CAND'))
        planned = sorted((dict(run_id=r['run_id'], case=r['case'], arm=r['arm'], seed=r['seed'])
                          for r in f.records), key=lambda r: r['run_id'])
        f.binding['schedule_sha256'] = paired.canonical_sha(planned)
        f.save()

        plan_path = f.run/'plan.json'
        plan = json.loads(plan_path.read_bytes())
        plan['memory_backends'] = {
            'CURRENT': {'readiness': {'cold_seconds': 587.068}},
            'CAND': {'readiness': {'cold_seconds': 616.543}},
        }
        plan_pin = pin(plan_path, json.dumps(plan, indent=2).encode())
        f.request['plan'] = plan_pin

        binding_pin = f.request['binding']
        binding_path = Path(binding_pin['path'])
        binding = json.loads(binding_path.read_bytes())
        binding['plan_sha256'] = plan_pin['sha256']
        f.request['binding'] = pin(binding_path, C(binding))

        request_pin = f.resave_request()
        output = self.root/'float-plan-conversion'
        legacy.convert(request_pin, output)
        receipt_pin = existing(output/'receipt.json')
        bundle, result = adapter.build(receipt_pin, POLICY)

        self.assertEqual(result['status'], 'no_grading_needed')
        self.assertEqual(bundle['plan']['observed']['plan_file_sha256'], plan_pin['sha256'])

    def test_zero_changed_refuses_an_unexpected_draft(self):
        f, receipt = full_run(self.root, lambda i, s: ('correct', 'correct'))
        stray = pin(self.root/'stray.json', b'{}')
        with self.assertRaisesRegex(paired.Refusal, '^legacy_bundle_draft_unexpected$'):
            adapter.build(receipt, POLICY, draft_receipt=stray)


class IndeterminateTests(Case):
    def check(self, state_of, state):
        f, receipt = full_run(self.root, state_of)
        bundle, result = adapter.build(receipt, POLICY)
        self.assertEqual(result['status'], 'incomplete_' + state)
        with self.assertRaisesRegex(paired.Refusal, '^legacy_grades_awaited$'):
            adapter.report_join(bundle, result)
        rendered = self.report(f, custody.join_results(bundle, [], []))
        self.assertEqual(rendered['status'], 'incomplete')
        self.assertIn('selection_undetermined', rendered['incomplete_reasons'])

    def test_all_indeterminate_stays_incomplete(self):
        self.check(lambda i, s: ('undetermined', 'undetermined'), 'incomplete_all_pairs_indeterminate')

    def test_mixed_unchanged_and_indeterminate_stays_incomplete(self):
        self.check(lambda i, s: ('correct', 'needs_review') if (i, s) == (4, 1) else ('correct', 'correct'),
                   'blocked_indeterminate_pairs')


class ChangedPairTests(Case):
    def deliver(self):
        """Changed pairs through the unchanged delivery plan, dispatch and collect."""
        f, receipt = full_run(self.root, lambda i, s: CHANGED.get((i, s), ('correct', 'correct')))
        draft_pin, approval = draft(f, receipt)
        bundle, result = adapter.build(receipt, POLICY, draft_receipt=draft_pin)
        self.assertEqual(result['status'], 'awaiting_grades')
        request = json.loads(Path(f.request and f.root/'request.json').read_bytes())
        sessions = dict(schema='cairn.grader-session-selection/1', binding_sha256=result['binding_sha256'],
            observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
            exclusions=[dict(role='actor_engineer', agent_id='engineer', session_id='engineer-session', execution_id='engineer-exec'),
                        dict(role='coordinator', agent_id='coordinator', session_id='coordinator-session', execution_id='coord-exec')],
            slots=[dict(slot=slot, **POLICY['graders'][slot], execution_id='execution-'+slot, freshness='unknown',
                        observation=None, control=request['review_measurement']['graders'][index]['control'],
                        route=pin(self.root/(slot+'-route'), b'route declaration'),
                        workload=pin(self.root/(slot+'-workload'), b'workload declaration'),
                        envelope=dict(renderer=None, unit='tokens', semantics='unknown', limit=10000, transport=None,
                                      framing=None, tool_schema=None, declared_input_bytes=None,
                                      declared_input_tokens=None))
                   for index, slot in enumerate(('primary', 'secondary'))])
        selection = dict(schema='cairn.grader-delivery-selection/1',
            crosswalk=existing(self.root/'draft/private/crosswalk.json'), draft_receipt=draft_pin, approval=approval,
            policy=request['policy'], grading_bundle=pin(self.root/'bundle.json', C(bundle)),
            sessions=pin(self.root/'sessions.json', C(sessions)), pairs=result['delivery_pairs'])
        delivery_plan.build_plan(pin(self.root/'delivery-selection.json', C(selection)), self.root/'plan', now=NOW)
        plan = json.loads((self.root/'plan/plan.json').read_bytes())
        receipt_bytes = (self.root/'plan/receipt.json').read_bytes()
        authority = dict(schema='cairn.grader-release-authority/2',
            plan_receipt_sha256=hashlib.sha256(receipt_bytes).hexdigest(), binding_sha256=plan['binding_sha256'],
            instruction_sha256='a'*64,
            issuer=dict(role='owner', agent_id='owner-agent', session_id='owner-session'),
            observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
            recipients=[dict(slot=s['slot'], agent_id=s['agent_id'], session_id=s['session_id'],
                             execution_id=s['execution_id'], model=MODELS[s['slot']], route=dict(ROUTE),
                             declared_route_sha256=hashlib.sha256(Path(s['route']['path']).read_bytes()).hexdigest(),
                             metadata_source='reported', model_custody='owner_selected_and_reported')
                        for s in plan['sessions']['slots']],
            packets=[dict(pair_id=p['pair_id'], slot=d['slot'], packet_sha256=d['packet_sha256'])
                     for p in plan['pairs'] for d in p['deliveries']], max_request_bytes=65536)
        fabric = FakeFabric()
        dispatched = grader.dispatch(self.root/'plan', self.root/'delivery-run', fabric=fabric, config=config(),
                                     authority=pin(self.root/'authority.json', C(authority)), now=NOW)
        return f, bundle, result, fabric, dispatched

    def answer(self, fabric, dispatched, choose):
        for row in dispatched['deliveries']:
            request = fabric.request_of(row['event_id'])
            samples = request['response']['samples']
            verdicts = choose(request['pair_id'], row['slot'], samples)
            if verdicts is None:
                continue
            fabric.complete(row['event_id'], json.dumps(dict(schema='cairn.grader-response/1',
                request_id=request['request_id'], pair_id=request['pair_id'], slot=request['slot'],
                packet_sha256=request['packet_sha256'], binding_sha256=request['binding_sha256'],
                state='determinate', verdicts=dict(zip(samples, verdicts)), reason='synthetic reason')))

    def collect(self):
        return grader.collect(self.root/'plan', self.root/'delivery-run', fabric=self.fabric, config=config(), now=NOW)

    def test_returned_grades_join_through_the_existing_join_and_disagreement_goes_to_owner_only(self):
        f, bundle, result, self.fabric, dispatched = self.deliver()
        self.assertEqual(len(dispatched['deliveries']), 4)
        pairs = sorted(p['pair_id'] for p in result['delivery_pairs'])
        # One pair agrees; the other disagrees.
        self.answer(self.fabric, dispatched, lambda pair, slot, samples:
                    ['pass', 'fail'] if pair == pairs[0] or slot == 'primary' else ['fail', 'pass'])
        collection = self.collect()
        join = adapter.report_join(bundle, result, collection)
        self.assertEqual(join['aggregate']['declared_raw_agreement'], dict(numerator=1, denominator=2, rate=0.5))
        self.assertEqual(join['aggregate']['changed_pair_coverage']['determinate_dual'], 2)
        rendered = self.report(f, join)
        self.assertEqual(rendered['grading']['agreement'], dict(numerator=1, denominator=2, rate=0.5))
        self.assertEqual(rendered['status'], 'incomplete')
        self.assertEqual(rendered['incomplete_reasons'], ['disagreements_pending_owner'])
        self.assertEqual([p['resolution'] for p in rendered['disagreements_pending_owner']], ['pending_owner'])
        routed = grader.route_disagreements(self.root/'plan', self.root/'delivery-run', fabric=self.fabric, config=config(), now=NOW)
        self.assertEqual(len(routed['disputes']), 1)
        destinations = {e['event']['destination']['name'] for e in self.fabric.events.values()
                        if json.loads(self.fabric.records[(e['event']['ref']['record_id'], 1)]['body'])['schema']
                        == 'cairn.grader-dispute/1'}
        self.assertEqual(destinations, {OWNER})
        self.assertNotIn('agent/'+PUBLISHER['agent_id'], destinations)

    def test_missing_grades_for_changed_pairs_are_not_complete(self):
        f, bundle, result, self.fabric, dispatched = self.deliver()
        with self.assertRaisesRegex(paired.Refusal, '^legacy_grades_awaited$'):
            adapter.report_join(bundle, result)
        self.answer(self.fabric, dispatched, lambda pair, slot, samples: ['pass', 'pass'] if slot == 'primary' else None)
        rendered = self.report(f, adapter.report_join(bundle, result, self.collect()))
        self.assertEqual(rendered['status'], 'incomplete')
        self.assertIn('changed_pairs_not_dual_graded', rendered['incomplete_reasons'])
        self.assertEqual(rendered['grading']['dual_determinate'], 0)

    def test_a_mutated_collection_join_refuses(self):
        f, bundle, result, self.fabric, dispatched = self.deliver()
        self.answer(self.fabric, dispatched, lambda pair, slot, samples: ['pass', 'fail'])
        collection = self.collect()
        adapter.report_join(bundle, result, collection)
        mutations = [lambda j: j['aggregate']['declared_raw_agreement'].update(numerator=0),
                     lambda j: j['pairs'][0].update(selection='changed'),
                     lambda j: j['submissions'].pop(),
                     lambda j: j.update(schema='other'),
                     lambda j: j['submissions'][0]['submission']['verdicts'].update(
                         dict.fromkeys(j['submissions'][0]['submission']['verdicts'], 'fail'))]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                mutated = copy.deepcopy(collection); mutate(mutated['join'])
                with self.assertRaisesRegex(paired.Refusal, '^legacy_collection_mismatch$'):
                    adapter.report_join(bundle, result, mutated)

    def test_changed_pairs_require_the_matching_draft(self):
        f, receipt = full_run(self.root, lambda i, s: CHANGED.get((i, s), ('correct', 'correct')))
        with self.assertRaisesRegex(paired.Refusal, '^legacy_bundle_draft_required$'):
            adapter.build(receipt, POLICY)


class IdentityTests(Case):
    def test_schedule_hash_not_of_the_planned_identities_refuses(self):
        f, receipt = full_run(self.root, lambda i, s: ('correct', 'correct'), schedule=False)
        with self.assertRaisesRegex(paired.Refusal, '^legacy_bundle_schedule_mismatch$'):
            adapter.build(receipt, POLICY)

    def test_arm_labels_that_are_not_the_roles_refuse(self):
        f, receipt = full_run(self.root, lambda i, s: ('correct', 'correct'), arms=('baseline-mem', 'candidate-mem'))
        with self.assertRaisesRegex(paired.Refusal, '^legacy_bundle_arm_roles$'):
            adapter.build(receipt, POLICY)

    def test_changed_pair_binding_plan_or_attempt_files_refuse(self):
        f, receipt = full_run(self.root, lambda i, s: ('correct', 'correct'))
        for name in ('materials/binding.json', 'run/plan.json', 'run/agent.json', 'run/runs/task-00.CAND.s0/result.json'):
            with self.subTest(name=name):
                path = self.root/name; original = path.read_bytes()
                path.write_bytes(original.replace(b'"correct"', b'"mistake"', 1) if name.endswith('result.json')
                                 else original + b' ')
                with self.assertRaises(paired.Refusal):
                    adapter.build(receipt, POLICY)
                path.write_bytes(original)
        adapter.build(receipt, POLICY)

    def test_grade_for_an_unknown_or_wrong_pair_refuses(self):
        f, receipt = full_run(self.root, lambda i, s: CHANGED.get((i, s), ('correct', 'correct')))
        draft_pin, _ = draft(f, receipt)
        bundle, result = adapter.build(receipt, POLICY, draft_receipt=draft_pin)
        first, second = bundle['packets']
        grade = dict(submission_id='grade-1', binding_sha256=result['binding_sha256'], pair_id=first['pair_id'],
                     packet_sha256=first['packet_sha256'], rubric_sha256=bundle['binding']['rubric_sha256'],
                     slot='primary', **POLICY['graders']['primary'], state='determinate',
                     verdicts=dict.fromkeys(first['samples'], 'pass'), reason_sha256='5'*64)
        custody.join_results(bundle, [grade], [])
        for change, code in ((dict(pair_id='pair-unknown'), 'grading_submission_binding'),
                             (dict(packet_sha256=second['packet_sha256']), 'grading_submission_binding'),
                             (dict(verdicts=dict.fromkeys(second['samples'], 'pass')), 'grading_verdict_samples'),
                             (dict(binding_sha256='0'*64), 'grading_submission_binding')):
            with self.subTest(change=change):
                with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
                    custody.join_results(bundle, [dict(grade, **change)], [])


def reseal(bundle):
    plan = bundle['plan']
    if plan['declared']['pair_binding']['schedule_sha256'] == bundle['binding']['schedule_sha256']:
        # Follow a schedule edit everywhere it is declared, so the coverage check itself is reached.
        new = paired.canonical_sha(bundle['schedule'])
        plan['declared']['pair_binding']['schedule_sha256'] = bundle['binding']['schedule_sha256'] = new
    plan['sha256'] = paired.canonical_sha({k: v for k, v in plan.items() if k != 'sha256'})
    for field in ('plan', 'schedule', 'inventory', 'selection', 'packets', 'policy'):
        bundle['binding'][field + '_sha256'] = paired.canonical_sha(bundle[field])


class ReportJoinTests(Case):
    def test_empty_bundle_with_an_arbitrary_cached_join_refuses(self):
        # agent-341's reproduction.
        result = dict(bundle_sha256=paired.canonical_sha({}), report_join=dict(unrelated='unchecked'))
        with self.assertRaises(paired.Refusal):
            adapter.report_join({}, result)

    def test_a_cached_no_grade_join_is_never_trusted(self):
        f, receipt = full_run(self.root, lambda i, s: ('correct', 'correct'))
        bundle, result = adapter.build(receipt, POLICY)
        self.assertNotIn('report_join', result)
        stale = dict(result, report_join=dict(schema='cairn.grading-join-report/1', aggregate={}, pairs=[]))
        join = adapter.report_join(bundle, stale)
        self.assertEqual(join, custody.join_results(bundle, [], []))

    def test_a_changed_bundle_with_an_old_cached_join_refuses(self):
        f, receipt = full_run(self.root, lambda i, s: ('correct', 'correct'))
        bundle, result = adapter.build(receipt, POLICY)
        changed = copy.deepcopy(bundle)
        changed['selection'][0]['state'] = 'changed'; reseal(changed)
        with self.assertRaisesRegex(paired.Refusal, '^legacy_bundle_mismatch$'):
            adapter.report_join(changed, result)
        # Even with a matching result hash, a non-unchanged selection never completes without grades.
        with self.assertRaises(paired.Refusal):
            adapter.report_join(changed, dict(result, bundle_sha256=paired.canonical_sha(changed)))


class JoinBranchTests(Case):
    def setUp(self):
        super().setUp()
        f, receipt = full_run(self.root, lambda i, s: ('correct', 'correct'))
        self.bundle, self.result = adapter.build(receipt, POLICY)

    def test_any_field_change_breaks_the_binding(self):
        mutations = [lambda b: b['schedule'][5].update(seed=7), lambda b: b['plan']['observed'].update(distractors=1899),
                     lambda b: b['plan']['declared']['pair_binding'].update(request_id='request-B'),
                     lambda b: b['plan']['observed']['positions'][0].update(position=1-b['plan']['observed']['positions'][0]['position']),
                     lambda b: b['inventory'][0].update(outcome='mistake'), lambda b: b['selection'][0].update(state='changed'),
                     lambda b: b['policy'].update(verdict_labels=['pass'])]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                bundle = copy.deepcopy(self.bundle); mutate(bundle)
                with self.assertRaisesRegex(paired.Refusal, '^grading_binding_mismatch$'):
                    custody.join_results(bundle, [], [])
                with self.assertRaisesRegex(paired.Refusal, '^legacy_bundle_mismatch$'):
                    adapter.report_join(bundle, self.result)

    def test_resealed_identity_mismatches_still_refuse(self):
        cases = [
            ('legacy_declared_observed_mismatch', lambda b: b['plan']['observed'].update(agent_sha256='0'*64)),
            ('legacy_declared_observed_mismatch', lambda b: b['plan']['observed'].update(distractors=1899)),
            ('legacy_pair_binding_mismatch', lambda b: b['binding'].update(request_id='request-B')),
            ('legacy_pair_binding_mismatch', lambda b: b['plan']['declared']['pair_binding'].update(schedule_sha256='0'*64)),
            ('grading_schedule_coverage', lambda b: b['schedule'][0].update(run_id='other.CURRENT.s0')),
            ('grading_schedule_coverage', lambda b: b['schedule'].pop()),
            ('legacy_attempt_identity', lambda b: b['inventory'][0].update(run_id=b['inventory'][1]['run_id'])),
            ('grading_position', lambda b: b['plan']['observed']['positions'][0].update(position=2)),
            ('grading_schedule_coverage', lambda b: b['plan']['observed']['positions'][1].update(
                position=b['plan']['observed']['positions'][0]['position'])),
            ('legacy_declared', lambda b: b['plan']['declared']['pair_binding'].update(arm_roles={'CURRENT': 'CAND', 'CAND': 'CURRENT'})),
            ('legacy_plan_shape', lambda b: b['plan'].update(pairs=62)),
        ]
        for code, mutate in cases:
            with self.subTest(code=code):
                bundle = copy.deepcopy(self.bundle); mutate(bundle); reseal(bundle)
                with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
                    custody.join_results(bundle, [], [])

    def test_sdk_path_refuses_legacy_inputs_and_legacy_path_refuses_sdk_inputs(self):
        sdk_shaped = dict(copy.deepcopy(self.bundle), schema='cairn.grading-join-input/1')
        with self.assertRaises(paired.Refusal):
            custody.join_results(sdk_shaped, [], [])
        legacy_shaped = dict(sdk_fixture(), schema=custody.LEGACY_SCHEMA)
        with self.assertRaisesRegex(paired.Refusal, '^legacy_plan$'):
            custody.join_results(legacy_shaped, [], [])
        custody.join_results(sdk_fixture(), [], [])  # the SDK path itself is unchanged


if __name__ == '__main__':
    unittest.main()
