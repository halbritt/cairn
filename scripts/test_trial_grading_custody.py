"""Synthetic public-interface checks; no weekly fixtures or runtime operations."""
import copy
from contextlib import ExitStack
import importlib
import json
import unittest
from unittest.mock import patch

from trial_paired_request import canonical_sha
import trial_grading_custody as custody


def fixture():
    plan = dict(instrument='weekly-frozen-cases', cases=[f'case-{i:02}' for i in range(21)],
                arms=['CURRENT', 'CAND'], seeds=[11, 22, 33], attempts=126,
                workload=dict(path='/synthetic/workload', sha256='a' * 64))
    plan['sha256'] = canonical_sha(plan)
    runs = [dict(case=case, seed=seed, arm=arm, position=position)
            for case in plan['cases'] for seed in plan['seeds']
            for position, arm in enumerate(plan['arms'])]
    schedule = dict(schema='cairn.weekly-schedule/1', plan_sha256=plan['sha256'],
                    source=dict(manifest_sha256='b' * 64), execution=dict(sha256='c' * 64),
                    actor_api_base='d' * 40, wording='task',
                    distractors=dict(count=1900, seed=20260928, sha256='e' * 64),
                    runs=runs)
    schedule['sha256'] = canonical_sha(schedule)
    pairs = [dict(case=case, seed=seed) for case in plan['cases'] for seed in plan['seeds']]
    selection = [dict(**pair, state='changed' if i < 2 else 'undetermined' if i == 2 else 'unchanged')
                 for i, pair in enumerate(pairs)]
    policy = dict(comparison='exact-sample-verdicts/1', verdict_labels=['pass', 'fail'],
                  graders=dict(primary=dict(agent_id='grader-a', session_id='session-a'),
                               secondary=dict(agent_id='grader-b', session_id='session-b')),
                  excluded_agents=['engineer'], excluded_sessions=['engineer-session'],
                  coordinator=dict(agent_id='coordinator', session_id='coordinator-session',
                                   delegation_sha256='f' * 64))
    bundle = dict(schema='cairn.grading-join-input/1', plan=plan, schedule=schedule,
                  inventory=[], selection=selection, packets=[], policy=policy,
                  binding=dict(request_id='request-1', campaign_id='campaign-1',
                               occurrence_id='week-1', request_sha256='1' * 64,
                               rubric_sha256='2' * 64, changed_pair_definition_sha256='3' * 64,
                               blinding_policy_sha256='4' * 64))
    seal(bundle)
    return bundle


def seal(bundle):
    for field in ('plan', 'schedule', 'inventory', 'selection', 'packets', 'policy'):
        bundle['binding'][field + '_sha256'] = canonical_sha(bundle[field])


def completed_inventory(bundle):
    bundle['inventory'] = [dict(case=row['case'], seed=row['seed'], arm=row['arm'],
                               run_id=f'run-{i}', state='completed', outcome='correct',
                               evidence_sha256=canonical_sha(dict(synthetic=i)))
                           for i, row in enumerate(bundle['schedule']['runs'])]
    seal(bundle)


def grading_fixture():
    bundle = fixture()
    completed_inventory(bundle)
    bundle['packets'] = [dict(case=row['case'], seed=row['seed'], pair_id=f'pair-{i}',
                              packet_sha256=canonical_sha(dict(packet=i)),
                              samples=[f'sample-{i}-x', f'sample-{i}-y'])
                         for i, row in enumerate(bundle['selection'][:2])]
    seal(bundle)
    grades = []
    for packet in bundle['packets']:
        for slot, reviewer in bundle['policy']['graders'].items():
            grades.append(dict(submission_id=f"{packet['pair_id']}-{slot}",
                               binding_sha256=canonical_sha(bundle['binding']),
                               pair_id=packet['pair_id'], packet_sha256=packet['packet_sha256'],
                               rubric_sha256=bundle['binding']['rubric_sha256'],
                               slot=slot, **reviewer, state='determinate',
                               verdicts=dict.fromkeys(packet['samples'], 'pass'), reason_sha256='5' * 64))
    return bundle, grades


def disposition(bundle, grades):
    return dict(disposition_id='decision-1', binding_sha256=canonical_sha(bundle['binding']),
                pair_id=grades[0]['pair_id'],
                grade_sha256={grade['slot']: canonical_sha(grade) for grade in grades},
                **bundle['policy']['coordinator'], state='determinate',
                verdicts=copy.deepcopy(grades[0]['verdicts']), reason_sha256='6' * 64)


class JoinTests(unittest.TestCase):
    def test_json_boundary_preserves_unicode_metadata_and_refuses_unrepresentable_inputs(self):
        bundle = fixture()
        bundle['schedule']['source']['synthetic_label'] = 'café-東京'
        bundle['schedule']['sha256'] = canonical_sha({k: v for k, v in bundle['schedule'].items() if k != 'sha256'})
        seal(bundle)
        report = custody.join_results(bundle, [], [])
        self.assertEqual(report['input_sha256'], canonical_sha(bundle))
        for invalid in (float('nan'), b'bytes', '\ud800'):
            with self.subTest(invalid_type=type(invalid).__name__):
                bad = fixture()
                bad['schedule']['source']['invalid'] = invalid
                with self.assertRaisesRegex(custody.Refusal, 'grading_json_input'):
                    custody.join_results(bad, [], [])
        bundle, grades = grading_fixture()
        with self.assertRaisesRegex(custody.Refusal, 'grading_submissions_bound'):
            custody.join_results(bundle, [grades[0]] * 1025, [])

    def test_join_and_reload_use_no_file_process_or_network_effects(self):
        bundle, grades = grading_fixture()
        with ExitStack() as guards:
            for target in ('builtins.open', 'pathlib.Path.open', 'os.open',
                           'subprocess.Popen', 'socket.socket'):
                guards.enter_context(patch(target, side_effect=AssertionError('unexpected external effect')))
            importlib.reload(custody)
            report = custody.join_results(bundle, grades, [])
        self.assertEqual(report['aggregate']['declared_raw_agreement']['denominator'], 2)

    def test_no_changed_pairs_is_undefined_not_perfect_agreement(self):
        bundle = fixture()
        for row in bundle['selection']:
            row['state'] = 'unchanged'
        seal(bundle)
        report = custody.join_results(bundle, [], [])
        self.assertEqual(report['aggregate']['selection_completeness'], 'declared')
        self.assertEqual(report['aggregate']['changed_pair_coverage']['denominator'], 0)
        self.assertIsNone(report['aggregate']['declared_raw_agreement']['rate'])
        self.assertFalse(report['aggregate']['actual_campaign_success'])

    def test_packet_mapping_is_unique_and_cannot_include_unselected_pairs(self):
        for variant in ('pair_id', 'sample', 'duplicate_pair', 'unchanged'):
            with self.subTest(variant=variant):
                bundle, _ = grading_fixture()
                if variant == 'pair_id':
                    bundle['packets'][1]['pair_id'] = bundle['packets'][0]['pair_id']
                elif variant == 'sample':
                    bundle['packets'][1]['samples'][0] = bundle['packets'][0]['samples'][0]
                elif variant == 'duplicate_pair':
                    bundle['packets'][1]['seed'] = bundle['packets'][0]['seed']
                else:
                    bundle['selection'][0]['state'] = 'unchanged'
                seal(bundle)
                with self.assertRaises(custody.Refusal):
                    custody.join_results(bundle, [], [])

    def test_disposition_identifier_cannot_be_reused_for_another_pair(self):
        bundle, grades = grading_fixture()
        grades[1]['verdicts']['sample-0-y'] = 'fail'
        grades[3]['verdicts']['sample-1-y'] = 'fail'
        first = disposition(bundle, grades[:2])
        second = disposition(bundle, grades[2:])
        with self.assertRaisesRegex(custody.Refusal, 'grading_disposition_id_reused'):
            custody.join_results(bundle, grades, [first, second])

    def test_cross_bound_grades_and_payload_fields_are_refused(self):
        for field in ('binding_sha256', 'packet_sha256', 'rubric_sha256', 'pair_id', 'session_id',
                      'verdicts', 'verified', 'body'):
            with self.subTest(field=field):
                bundle, grades = grading_fixture()
                if field == 'verdicts':
                    grades[0][field] = {'unrelated-sample': 'pass'}
                elif field == 'verified':
                    grades[0][field] = True
                else:
                    grades[0][field] = '0' * 64 if field.endswith('sha256') else 'unrelated'
                with self.assertRaises(custody.Refusal):
                    custody.join_results(bundle, grades, [])
        for field in ('request_id', 'occurrence_id', 'changed_pair_definition_sha256', 'blinding_policy_sha256'):
            with self.subTest(binding=field):
                bundle, grades = grading_fixture()
                bundle['binding'][field] = '0' * 64 if field.endswith('sha256') else 'another'
                with self.assertRaisesRegex(custody.Refusal, 'grading_submission_binding'):
                    custody.join_results(bundle, grades, [])

    def test_two_undetermined_grades_do_not_agree_and_zero_denominator_is_undefined(self):
        bundle, grades = grading_fixture()
        for grade in grades:
            grade.update(state='undetermined', verdicts=None)
        report = custody.join_results(bundle, grades, [])
        self.assertEqual(report['aggregate']['declared_raw_agreement'],
                         dict(numerator=0, denominator=0, rate=None))
        self.assertEqual(report['aggregate']['changed_pair_coverage']['unconflicted_dual'], 2)
        self.assertEqual(report['aggregate']['changed_pair_issues']['primary_undetermined'], 2)
        self.assertEqual(report['aggregate']['changed_pair_issues']['secondary_undetermined'], 2)

    def test_disposition_conflicts_are_retained_but_wrong_authority_or_grade_binding_refuses(self):
        bundle, grades = grading_fixture()
        grades[1]['verdicts']['sample-0-y'] = 'fail'
        decision = disposition(bundle, grades[:2])
        conflict = copy.deepcopy(decision)
        conflict['verdicts']['sample-0-x'] = 'fail'
        report = custody.join_results(bundle, grades, [decision, conflict, decision])
        self.assertEqual(len(report['dispositions']), 2)
        self.assertEqual(report['pairs'][0]['disposition_state'], 'conflict')
        self.assertEqual(report, custody.join_results(bundle, grades, [conflict, decision]))
        self.assertEqual(report['aggregate']['declared_raw_agreement']['numerator'], 1)
        self.assertEqual(report['aggregate']['declared_raw_agreement']['denominator'], 2)
        for field in ('binding_sha256', 'delegation_sha256', 'agent_id', 'session_id', 'grade_sha256'):
            with self.subTest(field=field):
                bad = copy.deepcopy(decision)
                bad[field] = ('0' * 64 if field.endswith('sha256') else 'unrelated')
                if field == 'grade_sha256':
                    bad[field] = dict(primary=decision[field]['secondary'], secondary=decision[field]['primary'])
                with self.assertRaises(custody.Refusal):
                    custody.join_results(bundle, grades, [bad])

    def test_public_aggregate_excludes_identifiers_and_returned_graph_is_owned(self):
        bundle, grades = grading_fixture()
        bundle['schedule']['source']['synthetic_private_body'] = 'PRIVATE-CANARY'
        bundle['schedule']['sha256'] = canonical_sha({k: v for k, v in bundle['schedule'].items() if k != 'sha256'})
        seal(bundle)
        for grade in grades:
            grade['binding_sha256'] = canonical_sha(bundle['binding'])
        report = custody.join_results(bundle, grades, [])
        self.assertNotIn('PRIVATE-CANARY', json.dumps(report))
        public = json.dumps(report['aggregate'])
        for private in ('case-00', 'request-1', 'grader-a', 'sample-0-x', 'pass'):
            self.assertNotIn(private, public)
        report['submissions'][0]['submission']['verdicts'].clear()
        report['binding']['occurrence_id'] = 'changed'
        fresh = custody.join_results(bundle, grades, [])
        self.assertEqual(fresh['binding']['occurrence_id'], 'week-1')
        self.assertEqual(fresh['aggregate']['declared_raw_agreement']['numerator'], 2)

    def test_missing_undetermined_and_failed_evidence_have_distinct_coverage(self):
        for variant, issue in (('missing', 'secondary_missing'), ('undetermined', 'secondary_undetermined'),
                               ('failed', 'attempt_unavailable'), ('packet', 'missing_packet')):
            with self.subTest(variant=variant):
                bundle, grades = grading_fixture()
                if variant == 'missing':
                    grades.pop(1)
                elif variant == 'undetermined':
                    grades[1].update(state='undetermined', verdicts=None)
                elif variant == 'failed':
                    bundle['inventory'][0]['state'] = 'failed'
                else:
                    bundle['packets'].pop(0)
                    grades = grades[2:]
                seal(bundle)
                for grade in grades:
                    grade['binding_sha256'] = canonical_sha(bundle['binding'])
                report = custody.join_results(bundle, grades, [])
                self.assertIn(issue, report['pairs'][0]['issues'])
                self.assertEqual(report['aggregate']['declared_raw_agreement'],
                                 dict(numerator=1, denominator=1, rate=1.0))
                coverage = report['aggregate']['changed_pair_coverage']
                self.assertEqual(coverage['denominator'], 2)
                self.assertEqual(coverage['determinate_dual'], 1)
                self.assertEqual(report['aggregate']['selection_completeness'], 'undetermined')

    def test_conflicting_submissions_survive_and_exact_replays_do_not_double_count(self):
        bundle, grades = grading_fixture()
        baseline = custody.join_results(bundle, grades, [])
        self.assertEqual(baseline, custody.join_results(bundle, grades + [copy.deepcopy(grades[0])], []))
        conflict = copy.deepcopy(grades[0])
        conflict['verdicts']['sample-0-x'] = 'fail'
        report = custody.join_results(bundle, grades + [conflict], [])
        self.assertEqual(len(report['submissions']), 5)
        self.assertEqual(report['pairs'][0]['grade_states']['primary'], 'conflict')
        self.assertEqual(report['aggregate']['declared_raw_agreement']['denominator'], 1)
        self.assertEqual(report, custody.join_results(bundle, list(reversed(grades + [conflict])), []))
        grades[2]['submission_id'] = grades[0]['submission_id']
        with self.assertRaisesRegex(custody.Refusal, 'grading_submission_id_reused'):
            custody.join_results(bundle, grades, [])

    def test_coordinator_disposition_retains_disagreement_without_authenticating_resolution(self):
        bundle, grades = grading_fixture()
        grades[1]['verdicts']['sample-0-y'] = 'fail'
        decision = disposition(bundle, grades[:2])
        before = custody.join_results(bundle, grades, [])
        report = custody.join_results(bundle, grades, [decision])
        self.assertEqual(report['aggregate']['declared_raw_agreement'],
                         before['aggregate']['declared_raw_agreement'])
        self.assertEqual(report['pairs'][0]['declared_comparison'], 'disagreement')
        self.assertEqual(report['pairs'][0]['disposition_state'], 'declared')
        self.assertEqual(report['pairs'][0]['resolution'], 'unresolved')
        self.assertEqual(report['dispositions'][0]['disposition'], decision)
        self.assertEqual(report['pairs'][0]['attempts'], before['pairs'][0]['attempts'])
        self.assertEqual(report['submissions'], before['submissions'])
        self.assertFalse(report['aggregate']['actual_campaign_success'])

    def test_declared_identity_separation_cannot_be_bypassed_by_aliases(self):
        for variant in ('same_agent', 'same_session', 'excluded_agent', 'excluded_session', 'coordinator',
                        'invented_comparison', 'caller_verified'):
            with self.subTest(variant=variant):
                bundle = fixture()
                policy = bundle['policy']
                if variant == 'same_agent':
                    policy['graders']['secondary']['agent_id'] = 'grader-a'
                elif variant == 'same_session':
                    policy['graders']['secondary']['session_id'] = 'session-a'
                elif variant == 'excluded_agent':
                    policy['excluded_agents'].append('grader-a')
                elif variant == 'excluded_session':
                    policy['excluded_sessions'].append('session-a')
                elif variant == 'coordinator':
                    policy['coordinator']['session_id'] = 'session-a'
                elif variant == 'invented_comparison':
                    policy['comparison'] = 'majority-vote'
                else:
                    policy['verified'] = True
                seal(bundle)
                with self.assertRaises(custody.Refusal):
                    custody.join_results(bundle, [], [])

    def test_dual_grades_preserve_disagreement_and_only_measure_declared_agreement(self):
        bundle, grades = grading_fixture()
        grades[-1]['verdicts']['sample-1-x'] = 'fail'
        before = copy.deepcopy((bundle, grades))
        report = custody.join_results(bundle, grades, [])
        self.assertEqual((bundle, grades), before)
        self.assertEqual(report['aggregate']['declared_raw_agreement'],
                         dict(numerator=1, denominator=2, rate=0.5))
        self.assertEqual(report['aggregate']['verified_raw_agreement'],
                         dict(numerator=0, denominator=0, rate=None))
        self.assertEqual(report['pairs'][0]['declared_comparison'], 'agreement')
        self.assertEqual(report['pairs'][1]['declared_comparison'], 'disagreement')
        self.assertEqual(len(report['submissions']), 4)
        self.assertEqual(report, custody.join_results(bundle, list(reversed(grades)), []))
        self.assertEqual(report['aggregate']['custody'], 'unknown')

    def test_inventory_retains_failed_and_missing_attempts_without_outcome_credit(self):
        bundle = fixture()
        completed_inventory(bundle)
        bundle['inventory'].pop(0)
        bundle['inventory'][0].update(state='failed', outcome='provider_error', evidence_sha256=None)
        seal(bundle)
        report = custody.join_results(bundle, [], [])
        self.assertEqual(report['aggregate']['attempts'],
                         dict(expected=126, observed=125, missing=1, completed=124, failed=1))
        self.assertEqual(report['pairs'][0]['attempts']['CURRENT']['state'], 'missing')
        self.assertEqual(report['pairs'][0]['attempts']['CAND']['outcome'], 'provider_error')
        for variant in ('duplicate_key', 'duplicate_run', 'extra', 'missing_digest'):
            with self.subTest(variant=variant):
                bad = fixture()
                completed_inventory(bad)
                if variant == 'duplicate_key':
                    bad['inventory'][-1] = copy.deepcopy(bad['inventory'][0])
                elif variant == 'duplicate_run':
                    bad['inventory'][-1]['run_id'] = bad['inventory'][0]['run_id']
                elif variant == 'extra':
                    bad['inventory'][-1]['seed'] = 44
                else:
                    bad['inventory'][0]['evidence_sha256'] = None
                seal(bad)
                with self.assertRaises(custody.Refusal):
                    custody.join_results(bad, [], [])

    def test_selection_must_cover_each_pair_once_without_defaulting_to_unchanged(self):
        for variant in ('missing', 'duplicate', 'unknown_state', 'wrong_seed'):
            with self.subTest(variant=variant):
                bundle = fixture()
                if variant == 'missing':
                    bundle['selection'].pop(0)
                elif variant == 'duplicate':
                    bundle['selection'][0] = copy.deepcopy(bundle['selection'][1])
                elif variant == 'unknown_state':
                    bundle['selection'][0]['state'] = 'unreviewed'
                else:
                    bundle['selection'][0]['seed'] = 99
                seal(bundle)
                with self.assertRaises(custody.Refusal):
                    custody.join_results(bundle, [], [])

    def test_equal_counts_cannot_hide_schedule_substitution(self):
        for variant in ('duplicate', 'extra', 'position', 'plan', 'binding', 'distractors'):
            with self.subTest(variant=variant):
                bundle = fixture()
                schedule = bundle['schedule']
                if variant == 'duplicate':
                    schedule['runs'][-1] = copy.deepcopy(schedule['runs'][0])
                elif variant == 'extra':
                    schedule['runs'][-1]['seed'] = 44
                elif variant == 'position':
                    schedule['runs'][1]['position'] = 0
                elif variant == 'plan':
                    schedule['plan_sha256'] = '0' * 64
                elif variant == 'distractors':
                    schedule['distractors']['count'] = 0
                if variant != 'binding':
                    schedule['sha256'] = canonical_sha({k: v for k, v in schedule.items() if k != 'sha256'})
                    seal(bundle)
                else:
                    bundle['binding']['schedule_sha256'] = '0' * 64
                with self.assertRaises(custody.Refusal):
                    custody.join_results(bundle, [], [])

    def test_missing_attempts_and_grades_stay_visible_with_undefined_agreement(self):
        bundle = fixture()
        before = copy.deepcopy(bundle)
        report = custody.join_results(bundle, [], [])
        self.assertEqual(bundle, before)
        self.assertEqual(len(report['pairs']), 63)
        self.assertEqual(report['aggregate']['attempts'], dict(expected=126, observed=0,
                                                            missing=126, completed=0, failed=0))
        self.assertEqual(report['aggregate']['selection'],
                         dict(changed=2, unchanged=60, undetermined=1))
        self.assertEqual(report['aggregate']['declared_raw_agreement'],
                         dict(numerator=0, denominator=0, rate=None))
        self.assertEqual(report['aggregate']['custody'], 'unknown')
        self.assertFalse(report['aggregate']['admission'])


if __name__ == '__main__':
    unittest.main()
