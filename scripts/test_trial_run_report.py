"""Synthetic checks for the dated paired-run report and its short Cairn note.

Only generated evidence and a fake remember runner are used; no store, provider,
model or network operation is performed.
"""
import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import trial_grading_custody as custody
import trial_run_report as run_report
from test_trial_grading_custody import grading_fixture as join_fixture, seal
from trial_paired_request import canonical_sha

SECRET = 'sk-ant-SECRET-DO-NOT-LEAK'
ORACLE = 'ORACLE-ANSWER-the-port-is-8443'
TRANSCRIPT = 'TRANSCRIPT-user-said-hello'
COUNCIL = 'PRIVATE-COUNCIL-deliberation'


def grading_fixture():
    """Join fixture with every pair selection declared (no undetermined row)."""
    bundle, grades = join_fixture()
    for row in bundle['selection']:
        if row['state'] == 'undetermined':
            row['state'] = 'unchanged'
    seal(bundle)
    rebind(bundle, grades)
    return bundle, grades


def rebind(bundle, grades):
    for grade in grades:
        grade['binding_sha256'] = canonical_sha(bundle['binding'])


def agent_fixture(bundle):
    """A complete cairn.task-eval.agent/1 document matching the join inventory."""
    records = []
    for row in bundle['inventory']:
        records.append(dict(
            run_id=row['run_id'], case=row['case'], arm=row['arm'], seed=row['seed'],
            outcome=row['outcome'], seconds=12.5, harness='claude', model='synthetic-model',
            answer=ORACLE, error=SECRET, trace=dict(turns=3, commands=[TRANSCRIPT]),
            memory=dict(delivered=[COUNCIL]), reviewed=dict(outcome='correct', note=ORACLE)))
    return dict(schema='cairn.task-eval.agent/1', arms=['CURRENT', 'CAND'], model='synthetic-model',
                wording='task', distractors=1900, frozen=dict(oracle=ORACLE),
                admission=dict(state='complete', stop=None, planned=126, admitted=126, not_started=[]),
                records=records, summary=dict(note=TRANSCRIPT))


def complete_inputs(disagree=False):
    bundle, grades = grading_fixture()
    if disagree:
        sample = bundle['packets'][1]['samples'][0]
        for grade in grades:
            if grade['pair_id'] == bundle['packets'][1]['pair_id'] and grade['slot'] == 'secondary':
                grade['verdicts'][sample] = 'fail'
    join = custody.join_results(bundle, grades, [])
    return agent_fixture(bundle), join


def provider_limited_inputs():
    """Mirror the 2026-09-29 shape: a 429 weekly cap stops admission mid-run."""
    bundle, grades = grading_fixture()
    kept = bundle['inventory'][:110]
    capped = dict(bundle['inventory'][110], state='failed', outcome='harness_error', evidence_sha256=None)
    bundle['inventory'] = kept + [capped]
    seal(bundle)
    rebind(bundle, grades)
    join = custody.join_results(bundle, grades, [])
    agent = agent_fixture(bundle)
    agent['records'][-1]['trace'] = dict(admission_failure=dict(
        provider='claude', reason='capacity_exhausted', api_error_status=429))
    planned = [(r['case'], r['arm'], r['seed']) for r in bundle['schedule']['runs']]
    agent['admission'] = dict(
        state='stopped_provider_failure', planned=126, admitted=111,
        stop=dict(run_id=capped['run_id'], provider='claude', reason='capacity_exhausted', api_error_status=429),
        not_started=[dict(run_id=f'{c}.{a}.s{s}', case=c, arm=a, seed=s, order=i)
                     for i, (c, a, s) in enumerate(planned[111:], start=111)])
    return agent, join


class ReportTests(unittest.TestCase):
    def test_complete_declared_run_reports_counts_but_never_claims_campaign_success(self):
        agent, join = complete_inputs()
        report = run_report.build_report(agent, join, '2026-10-21')
        self.assertEqual(report['schema'], 'cairn.task-eval-run-report/1')
        self.assertEqual(report['run_date'], '2026-10-21')
        self.assertEqual(report['status'], 'declared_complete')
        self.assertEqual(report['incomplete_reasons'], [])
        self.assertEqual(report['attempts'], dict(planned=126, admitted=126, finished=126, not_started=0,
                                                  failed=0, provider_limit_losses=0, provider_auth_failures=0))
        self.assertEqual(len(report['pairs']), 63)
        self.assertEqual(report['grading']['agreement'], dict(numerator=2, denominator=2, rate=1.0))
        self.assertEqual(report['grading']['changed_pairs'], 2)
        self.assertEqual(report['grading']['second_grader_reviewed'], 2)
        self.assertEqual(report['disagreements_pending_owner'], [])
        self.assertEqual(report['custody'], 'unknown')
        self.assertIs(report['actual_campaign_success'], False)
        self.assertEqual(report['report_sha256'], run_report.report_digest(report))

    def test_report_is_deterministic_and_independent_of_record_order(self):
        agent, join = complete_inputs()
        first = run_report.build_report(agent, join, '2026-10-21')
        shuffled = copy.deepcopy(agent)
        shuffled['records'].reverse()
        second = run_report.build_report(shuffled, copy.deepcopy(join), '2026-10-21')
        self.assertNotEqual(first['inputs'], second['inputs'])  # exact input bytes stay traceable
        for field in ('status', 'incomplete_reasons', 'attempts', 'pairs', 'grading',
                      'disagreements_pending_owner'):
            self.assertEqual(first[field], second[field])
        again = run_report.build_report(copy.deepcopy(agent), copy.deepcopy(join), '2026-10-21')
        self.assertEqual(run_report.render_markdown(first), run_report.render_markdown(again))
        self.assertEqual(run_report.note_draft(first), run_report.note_draft(again))

    def test_provider_limit_stop_is_reported_incomplete_with_losses(self):
        agent, join = provider_limited_inputs()
        report = run_report.build_report(agent, join, '2026-09-29')
        self.assertEqual(report['status'], 'incomplete')
        for reason in ('admission_not_complete', 'attempts_not_started', 'provider_limit_losses',
                       'attempt_failures', 'grading_attempts_unavailable'):
            self.assertIn(reason, report['incomplete_reasons'])
        self.assertEqual(report['attempts']['planned'], 126)
        self.assertEqual(report['attempts']['finished'], 110)
        self.assertEqual(report['attempts']['not_started'], 15)
        self.assertEqual(report['attempts']['provider_limit_losses'], 16)
        markdown = run_report.render_markdown(report)
        self.assertIn('INCOMPLETE', markdown)
        self.assertNotIn('declared_complete', markdown)
        self.assertIn('Status: incomplete', run_report.note_draft(report)['body'])

    def test_disagreements_are_listed_pending_owner_and_never_resolved(self):
        agent, join = complete_inputs(disagree=True)
        report = run_report.build_report(agent, join, '2026-10-21')
        self.assertEqual(report['grading']['agreement'], dict(numerator=1, denominator=2, rate=0.5))
        pending = report['disagreements_pending_owner']
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]['resolution'], 'pending_owner')
        self.assertIn('disagreements_pending_owner', report['incomplete_reasons'])
        self.assertEqual(report['status'], 'incomplete')
        self.assertIn('Disagreements pending owner: 1', run_report.note_draft(report)['body'])

    def test_missing_second_grades_and_undetermined_selection_fail_closed(self):
        bundle, grades = join_fixture()
        primary_only = [g for g in grades if g['slot'] == 'primary']
        join = custody.join_results(bundle, primary_only, [])
        report = run_report.build_report(agent_fixture(bundle), join, '2026-10-21')
        self.assertIn('changed_pairs_not_dual_graded', report['incomplete_reasons'])
        self.assertIn('selection_undetermined', report['incomplete_reasons'])
        self.assertIsNone(report['grading']['agreement']['rate'])
        self.assertIn('n/a', run_report.render_markdown(report))

    def test_agent_and_join_evidence_must_correspond(self):
        for variant in ('missing_record', 'foreign_record', 'swapped_run'):
            with self.subTest(variant=variant):
                agent, join = complete_inputs()
                if variant == 'missing_record':
                    agent['records'].pop()
                elif variant == 'foreign_record':
                    agent['records'].append(dict(agent['records'][0], run_id='foreign', case='case-99'))
                else:
                    agent['records'][0]['run_id'], agent['records'][1]['run_id'] = (
                        agent['records'][1]['run_id'], agent['records'][0]['run_id'])
                report = run_report.build_report(agent, join, '2026-10-21')
                self.assertEqual(report['status'], 'incomplete')
                self.assertIn('evidence_mismatch', report['incomplete_reasons'])

    def test_malformed_inputs_and_dates_refuse_with_fixed_codes(self):
        agent, join = complete_inputs()
        cases = [
            (dict(agent, schema='other'), join, '2026-10-21', 'report_agent_schema'),
            (agent, dict(join, schema='other'), '2026-10-21', 'report_join_schema'),
            (agent, join, '2026-13-01', 'report_date'),
            (agent, join, '21/10/2026', 'report_date'),
            (dict(agent, admission=None), join, '2026-10-21', 'report_agent_admission'),
            (dict(agent, records='x'), join, '2026-10-21', 'report_agent_records'),
            (agent, dict(join, pairs=join['pairs'][:3]), '2026-10-21', 'report_join_pairs'),
        ]
        for agent_doc, join_doc, date, code in cases:
            with self.subTest(code=code, date=date):
                with self.assertRaisesRegex(run_report.Refusal, '^' + code + '$'):
                    run_report.build_report(agent_doc, join_doc, date)
        bad_case = copy.deepcopy(agent)
        bad_case['records'][0]['case'] = '../../etc/passwd'
        with self.assertRaisesRegex(run_report.Refusal, '^report_identifier$'):
            run_report.build_report(bad_case, join, '2026-10-21')

    def test_success_flags_from_inputs_cannot_upgrade_the_report(self):
        agent, join = complete_inputs()
        join['aggregate'] = dict(join['aggregate'], custody='verified', actual_campaign_success=True, admission=True)
        report = run_report.build_report(agent, join, '2026-10-21')
        self.assertEqual(report['custody'], 'unknown')
        self.assertIs(report['actual_campaign_success'], False)

    def test_outputs_exclude_transcripts_secrets_council_and_oracle_material(self):
        agent, join = complete_inputs(disagree=True)
        report = run_report.build_report(agent, join, '2026-10-21')
        note = run_report.note_draft(report)
        outputs = [json.dumps(report), run_report.render_markdown(report), json.dumps(note)]
        forbidden = [SECRET, ORACLE, TRANSCRIPT, COUNCIL, 'synthetic-model', 'grader-a', 'session-a',
                     'coordinator', 'sample-1-x', '5' * 64]
        for text in outputs:
            for token in forbidden:
                self.assertNotIn(token, text)
        # The shareable note is counts-only: no case identifiers, seeds or run IDs.
        for pair in report['pairs']:
            self.assertNotIn(pair['case'], note['body'])
        self.assertNotIn('run-0', note['body'])
        self.assertLessEqual(len(note['body'].encode()), run_report.NOTE_LIMIT)

    def test_unknown_outcome_tokens_are_collapsed_not_echoed(self):
        agent, join = complete_inputs()
        agent['records'][0]['outcome'] = ORACLE.replace('-', '_')
        report = run_report.build_report(agent, join, '2026-10-21')
        self.assertNotIn(ORACLE.replace('-', '_'), json.dumps(report))
        self.assertIn('other', json.dumps(report['pairs']))


class FakeRunner:
    """Stands in for the cairn CLI; models request-id idempotency of the store."""

    def __init__(self):
        self.calls, self.saved = [], {}

    def __call__(self, argv, input=None, capture_output=False, text=False, timeout=None, env=None):
        self.calls.append(argv)
        request_id = argv[argv.index('--request-id') + 1]
        if request_id in self.saved and self.saved[request_id]['body'] != input:
            response = dict(ok=False, status='IDEMPOTENCY_CONFLICT')
        else:
            record = self.saved.setdefault(request_id, dict(record_id='rec-1', version=1, body=input,
                                                            sensitivity='shareable'))
            response = dict(ok=True, status='OK', data=record)
        return subprocess.CompletedProcess(argv, 0 if response['ok'] else 1, json.dumps(response), '')


class PublishTests(unittest.TestCase):
    def test_note_request_id_is_stable_per_report_and_changes_with_evidence(self):
        agent, join = complete_inputs()
        first = run_report.note_draft(run_report.build_report(agent, join, '2026-10-21'))
        again = run_report.note_draft(run_report.build_report(copy.deepcopy(agent), join, '2026-10-21'))
        later = run_report.note_draft(run_report.build_report(agent, join, '2026-10-28'))
        self.assertEqual(first['request_id'], again['request_id'])
        self.assertNotEqual(first['request_id'], later['request_id'])
        self.assertTrue(first['shareable'])

    def test_publish_uses_remember_api_and_is_idempotent_by_request_id(self):
        agent, join = complete_inputs()
        report = run_report.build_report(agent, join, '2026-10-21')
        runner = FakeRunner()
        kwargs = dict(binary='cairn-fixture', token_file='/disposable/agent.token', repo='fixture:report', runner=runner)
        saved = run_report.publish_note(report, **kwargs)
        replay = run_report.publish_note(copy.deepcopy(report), **kwargs)
        self.assertEqual(saved, replay)
        self.assertEqual(len(runner.saved), 1)
        argv = runner.calls[0]
        self.assertEqual(argv[:4], ['cairn-fixture', 'agent', '--token-file', '/disposable/agent.token'])
        for flag in ('remember', '--stdin', '--shareable', '--request-id'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index('--repo') + 1], 'fixture:report')

    def test_publish_refuses_edited_reports_and_conflicting_replays(self):
        agent, join = complete_inputs()
        report = run_report.build_report(agent, join, '2026-10-21')
        runner = FakeRunner()
        kwargs = dict(binary='cairn-fixture', token_file='/disposable/agent.token', repo='fixture:report', runner=runner)
        edited = dict(report, status='declared_complete', incomplete_reasons=[], custody='verified')
        edited['attempts'] = dict(report['attempts'], finished=999)
        with self.assertRaisesRegex(run_report.Refusal, '^report_digest$'):
            run_report.publish_note(edited, **kwargs)
        self.assertEqual(runner.calls, [])
        run_report.publish_note(report, **kwargs)
        draft = run_report.note_draft(report)
        runner.saved[draft['request_id']]['body'] = 'different body'
        with self.assertRaisesRegex(run_report.Refusal, '^note_request_conflict$'):
            run_report.publish_note(report, **kwargs)

    def test_publish_requires_explicit_token_file_and_repo(self):
        agent, join = complete_inputs()
        report = run_report.build_report(agent, join, '2026-10-21')
        for missing in ('token_file', 'repo'):
            kwargs = dict(binary='cairn-fixture', token_file='/disposable/agent.token', repo='fixture:report',
                          runner=FakeRunner())
            kwargs[missing] = ''
            with self.subTest(missing=missing), self.assertRaisesRegex(run_report.Refusal, '^note_destination$'):
                run_report.publish_note(report, **kwargs)


class CliTests(unittest.TestCase):
    def test_render_writes_dated_files_and_refuses_to_overwrite_different_content(self):
        agent, join = complete_inputs()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'agent.json').write_text(json.dumps(agent))
            (root / 'join.json').write_text(json.dumps(join))
            out = root / 'out'
            argv = ['render', '--agent', str(root / 'agent.json'), '--join', str(root / 'join.json'),
                    '--date', '2026-10-21', '--out', str(out)]
            self.assertEqual(run_report.main(argv), 0)
            names = sorted(p.name for p in out.iterdir())
            self.assertEqual(names, ['task-eval-run-2026-10-21.json', 'task-eval-run-2026-10-21.md',
                                     'task-eval-run-2026-10-21.note.json'])
            self.assertEqual(run_report.main(argv), 0)  # identical rerun is idempotent
            agent['records'][0]['outcome'] = 'mistake'
            (root / 'agent.json').write_text(json.dumps(agent))
            self.assertEqual(run_report.main(argv), 2)
            report = json.loads((out / 'task-eval-run-2026-10-21.json').read_text())
            self.assertEqual(report['report_sha256'], run_report.report_digest(report))


if __name__ == '__main__':
    unittest.main()
