"""Synthetic full-shape occurrences (126 attempts, 63 pairs) with the fake event
fabric only. No cases, oracles, providers, native runs or publication."""
import hashlib
import io
import json
import subprocess
import uuid
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_legacy_occurrence as occurrence
import trial_paired_request as paired
import trial_run_report as report
from test_trial_grader_delivery import FakeFabric, config, OWNER, MODELS, NOW
from test_trial_legacy_blind_selection import Fixture, C, pin, SHAPE_TASKS
from test_trial_legacy_grading_bundle import POLICY, CHANGED

UNCHANGED = ('correct', 'correct')


class FakeNotes:
    """Stands in for `cairn agent remember` behind trial_run_report.publish_note:
    request-ID idempotency, optionally losing the first response after storing."""

    def __init__(self):
        self.store, self.calls, self.lose_response = {}, [], 0

    def runner(self, argv, *, input, capture_output, text, timeout):
        request_id = argv[argv.index('--request-id') + 1]
        self.calls.append(request_id)
        if request_id in self.store and self.store[request_id] != input:
            return SimpleNamespace(stdout=json.dumps(dict(status='IDEMPOTENCY_CONFLICT')))
        self.store[request_id] = input
        if self.lose_response:
            self.lose_response -= 1
            raise subprocess.TimeoutExpired(argv, timeout)
        return SimpleNamespace(stdout=json.dumps(dict(status='OK', data=dict(
            record_id=str(uuid.uuid5(uuid.NAMESPACE_URL, request_id)), version=1, body=input))))

    def publisher(self, built):
        return report.publish_note(built, binary='cairn', token_file='/synthetic/token', repo='/synthetic/repo',
                                   runner=self.runner)


class Occurrence:
    def __init__(self, root, state_of):
        self.root = root
        outcomes = {(case, seed): state_of(index, seed) for index, case in enumerate(sorted(SHAPE_TASKS))
                    for seed in range(3)}
        self.f = Fixture(root/'harness', outcomes=outcomes, tasks=SHAPE_TASKS, arms=('CURRENT', 'CAND'))
        planned = sorted((dict(run_id=r['run_id'], case=r['case'], arm=r['arm'], seed=r['seed']) for r in self.f.records),
                         key=lambda r: r['run_id'])
        self.f.binding['schedule_sha256'] = paired.canonical_sha(planned)
        self.out = root/'occurrence'
        self.input = dict(schema=occurrence.INPUT_SCHEMA, conversion_request=self.f.save(),
                          grading_policy=pin(root/'policy.json', C(POLICY)), draft_approval=None,
                          grader_sessions=None, release_authority=None, run_date='2026-10-15')
        self.fabric = FakeFabric()
        self.notes = FakeNotes()
        self.serial = 0

    def pin_input(self):
        self.serial += 1
        return pin(self.root/f'input-{self.serial}.json', C(self.input))

    def run(self, *, fabric=True, publish=True):
        return occurrence.run_occurrence(self.pin_input(), self.out, fabric=self.fabric if fabric else None,
                                         config=config() if fabric else None,
                                         publisher=self.notes.publisher if publish else None, now=NOW)

    def approve(self):
        subject = (self.out/'draft-subject.json').read_bytes()
        approval = dict(schema='cairn.blind-declared-approval/2', state='approved',
                        subject_sha256=hashlib.sha256(subject).hexdigest(),
                        observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
                        issuer_role=pin(self.root/'issuer.bin', b'PRIVATE declared role'))
        self.input['draft_approval'] = pin(self.root/'approval.json', C(approval))

    def sessions(self):
        result = json.loads((self.out/'bundle/result.json').read_bytes())
        request = json.loads(Path(self.input['conversion_request']['path']).read_bytes())
        doc = dict(schema='cairn.grader-session-selection/1', binding_sha256=result['binding_sha256'],
            observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
            exclusions=[dict(role='actor_engineer', agent_id='engineer', session_id='engineer-session', execution_id='engineer-exec'),
                        dict(role='coordinator', agent_id='coordinator', session_id='coordinator-session', execution_id='coord-exec')],
            slots=[dict(slot=slot, **POLICY['graders'][slot], execution_id='execution-'+slot, freshness='unknown',
                        observation=None, control=request['review_measurement']['graders'][index]['control'],
                        route=pin(self.root/(slot+'-route'), b'route declaration'),
                        workload=pin(self.root/(slot+'-workload'), b'workload declaration'),
                        envelope=dict(renderer=None, unit='tokens', semantics='unknown', limit=10000, transport=None,
                                      framing=None, tool_schema=None, declared_input_bytes=None, declared_input_tokens=None))
                   for index, slot in enumerate(('primary', 'secondary'))])
        self.input['grader_sessions'] = pin(self.root/'sessions.json', C(doc))

    def authorize(self):
        plan = json.loads((self.out/'delivery-plan/plan.json').read_bytes())
        receipt = (self.out/'delivery-plan/receipt.json').read_bytes()
        doc = dict(schema='cairn.grader-release-authority/1', plan_receipt_sha256=hashlib.sha256(receipt).hexdigest(),
            binding_sha256=plan['binding_sha256'], issuer=dict(role='owner', agent_id='owner-agent', session_id='owner-session'),
            observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
            recipients=[dict(slot=s['slot'], agent_id=s['agent_id'], session_id=s['session_id'],
                             execution_id=s['execution_id'], model=MODELS[s['slot']]) for s in plan['sessions']['slots']],
            packets=[dict(pair_id=p['pair_id'], slot=d['slot'], packet_sha256=d['packet_sha256'])
                     for p in plan['pairs'] for d in p['deliveries']], max_request_bytes=65536)
        self.input['release_authority'] = pin(self.root/'authority.json', C(doc))

    def prepared(self):
        """Walk every prerequisite in order up to dispatched and awaiting grades."""
        steps = [('draft_approval_missing', self.approve), ('grader_sessions_missing', self.sessions),
                 ('release_authority_missing', self.authorize)]
        for code, provide in steps:
            state = self.run()
            assert state['pending'] == code, (state['pending'], code)
            provide()
        state = self.run()
        assert state['pending'] == 'awaiting_grades', state
        return state

    def answer(self, choose):
        dispatched = json.loads((self.out/'dispatch.json').read_bytes())
        for row in dispatched['deliveries']:
            request = self.fabric.request_of(row['event_id'])
            samples = request['response']['samples']
            verdicts = choose(request['pair_id'], row['slot'])
            if verdicts is None:
                continue
            self.fabric.complete(row['event_id'], json.dumps(dict(schema='cairn.grader-response/1',
                request_id=request['request_id'], pair_id=request['pair_id'], slot=request['slot'],
                packet_sha256=request['packet_sha256'], binding_sha256=request['binding_sha256'],
                state='determinate', verdicts=dict(zip(samples, verdicts)), reason='synthetic reason')))

    def published(self):
        return len(self.fabric.events), len(self.fabric.published), len(self.fabric.remembered)

    def report(self, snapshot):
        return json.loads((self.out/'report'/snapshot/'report.json').read_bytes())


class Case(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def states(self, state):
        return {row['phase']: row['state'] for row in state['phases']}


class ZeroChangedTests(Case):
    def test_zero_changed_needs_no_delivery_and_writes_a_complete_report(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        state = o.run(fabric=False, publish=False)
        self.assertEqual((state['status'], state['pending']), ('pending', 'note_publication_unconfigured'))
        self.assertIs(state['complete'], False)
        state = o.run(fabric=False)
        self.assertEqual((state['status'], state['pending']), ('finished', None))
        self.assertEqual(state['report_status'], 'declared_complete')
        self.assertIs(state['complete'], True)
        self.assertEqual(self.states(state), dict(conversion='complete', draft='not_required', bundle='complete',
                                                  plan='not_required', dispatch='not_required',
                                                  collect='not_required', report='snapshot_written',
                                                  publication='published'))
        self.assertEqual(o.report(state['snapshot'])['grading']['changed_pairs'], 0)
        self.assertEqual(state['grading']['changed_pairs'], 0)
        self.assertEqual(len(o.notes.store), 1)
        self.assertFalse((o.out/'draft').exists() or (o.out/'delivery-plan').exists())
        self.assertIs(state['note_published'], True)
        files = {p: p.read_bytes() for p in o.out.rglob('*') if p.is_file()}
        self.assertEqual(o.run(fabric=False), state)
        self.assertEqual(len(o.notes.calls), 1)
        self.assertEqual(files, {p: p.read_bytes() for p in o.out.rglob('*') if p.is_file()})

    def test_missing_grading_policy_is_pending(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        o.input['grading_policy'] = None
        state = o.run(fabric=False)
        self.assertEqual((state['status'], state['pending']), ('pending', 'grading_policy_missing'))


class ChangedPairTests(Case):
    def test_prerequisites_are_pending_in_order_then_agreeing_grades_complete_the_report(self):
        o = Occurrence(self.root, lambda i, s: CHANGED.get((i, s), UNCHANGED))
        state = o.run()
        self.assertEqual((state['status'], state['pending']), ('pending', 'draft_approval_missing'))
        self.assertFalse((o.out/'draft').exists())
        o.approve()
        self.assertEqual(o.run()['pending'], 'grader_sessions_missing')
        o.sessions()
        self.assertEqual(o.run()['pending'], 'release_authority_missing')
        o.authorize()
        self.assertEqual(o.run(fabric=False)['pending'], 'grader_fabric_unavailable')
        self.assertEqual(o.published(), (0, 0, 0))
        state = o.run()
        self.assertEqual(state['pending'], 'awaiting_grades')
        self.assertEqual(o.published(), (4, 4, 4))
        self.assertEqual(o.notes.calls, [])
        o.answer(lambda pair, slot: ['pass', 'fail'] if slot == 'primary' else None)
        partial = o.run()
        self.assertEqual(partial['pending'], 'awaiting_grades')
        self.assertIs(partial['grading']['all_graded'], False)
        self.assertEqual(partial['report_status'], 'incomplete')
        self.assertIn('changed_pairs_not_dual_graded', o.report(partial['snapshot'])['incomplete_reasons'])
        self.assertEqual(o.notes.calls, [])  # an advancing occurrence is not published
        o.answer(lambda pair, slot: ['pass', 'fail'] if slot == 'secondary' else None)
        state = o.run()
        self.assertEqual(state['status'], 'finished')
        self.assertEqual(state['report_status'], 'declared_complete')
        self.assertIs(state['complete'], True)
        self.assertEqual(o.report(state['snapshot'])['grading']['agreement'], dict(numerator=2, denominator=2, rate=1.0))
        self.assertEqual(state['grading']['agreement'], dict(numerator=2, denominator=2, rate=1.0))
        self.assertEqual(o.published(), (4, 4, 4))
        # Earlier snapshots are kept; later grades advanced the report without overwriting them.
        self.assertNotEqual(partial['snapshot'], state['snapshot'])
        self.assertIn(partial['snapshot'], state['snapshots'])
        self.assertEqual(o.report(partial['snapshot'])['status'], 'incomplete')
        self.assertEqual(len(o.notes.store), 1)
        self.assertEqual(o.run(), state)
        self.assertEqual(len(o.notes.calls), 1)

    def test_disagreement_goes_to_the_owner_only_and_the_report_stays_incomplete(self):
        o = Occurrence(self.root, lambda i, s: CHANGED.get((i, s), UNCHANGED))
        o.prepared()
        first = None

        def choose(pair, slot):
            nonlocal first
            first = first or pair
            return ['pass', 'fail'] if pair == first or slot == 'primary' else ['fail', 'pass']
        o.answer(choose)
        state = o.run()
        self.assertEqual(state['report_status'], 'incomplete')
        self.assertEqual(o.report(state['snapshot'])['incomplete_reasons'], ['disagreements_pending_owner'])
        grading = state['grading']
        self.assertIs(grading['all_graded'], True)
        self.assertIs(grading['disagreements_routed'], True)
        self.assertEqual(grading['agreement'], dict(numerator=1, denominator=2, rate=0.5))
        self.assertEqual(grading['resolution'], 'owner_pending')
        self.assertEqual(len(grading['routing']['disputes']), 1)
        self.assertEqual(grading['routing']['disputes'][0]['destination'], OWNER)
        self.assertEqual((state['status'], state['complete']), ('finished', False))
        collect = next(p for p in state['phases'] if p['phase'] == 'collect')
        self.assertEqual(collect['disputes'], dict(count=1, destination='owner_only', resolution='unresolved'))
        disputes = [e for e in o.fabric.events.values()
                    if json.loads(o.fabric.records[(e['event']['ref']['record_id'], 1)]['body'])['schema']
                    == 'cairn.grader-dispute/1']
        self.assertEqual([e['event']['destination']['name'] for e in disputes], [OWNER])
        before = o.published()
        self.assertEqual(o.run(), state)
        self.assertEqual(o.published(), before)


class IncompleteEvidenceTests(Case):
    def test_indeterminate_runs_keep_an_incomplete_report_never_labelled_no_grading_needed(self):
        for name, state_of in (('all', lambda i, s: ('undetermined', 'undetermined')),
                               ('mixed', lambda i, s: ('correct', 'needs_review') if (i, s) == (2, 1) else UNCHANGED)):
            with self.subTest(name=name):
                o = Occurrence(self.root/name, state_of)
                state = o.run(fabric=False)
                self.assertEqual(state['report_status'], 'incomplete')
                self.assertIn('selection_undetermined', o.report(state['snapshot'])['incomplete_reasons'])
                report_phase = next(p for p in state['phases'] if p['phase'] == 'report')
                self.assertEqual(report_phase['basis'], 'no_grades_incomplete')
                self.assertIs(state['complete'], False)

    def test_missing_grades_and_pending_owner_are_distinguishable(self):
        o = Occurrence(self.root, lambda i, s: CHANGED.get((i, s), UNCHANGED))
        o.prepared()
        missing = o.run()
        self.assertEqual((missing['pending'], missing['grading']['all_graded']), ('awaiting_grades', False))
        self.assertEqual(missing['grading']['disagreements'], 0)

    def test_interrupted_publication_replays_the_same_request_once(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        o.notes.lose_response = 1
        state = o.run(fabric=False)
        self.assertEqual(state['pending'], 'note_publication_uncertain_retry')
        state = o.run(fabric=False)
        self.assertEqual((state['status'], state['complete']), ('finished', True))
        self.assertEqual(len(set(o.notes.calls)), 1)
        self.assertEqual(len(o.notes.calls), 2)
        self.assertEqual(len(o.notes.store), 1)
        o.run(fabric=False)
        self.assertEqual(len(o.notes.calls), 2)


class PublicationReceiptTests(Case):
    """agent-341's reproduction: a publisher returning only the request ID."""

    def run_with(self, o, publisher):
        return occurrence.run_occurrence(o.pin_input(), o.out, publisher=publisher, now=NOW)

    def test_a_new_publication_without_a_usable_store_reference_refuses(self):
        def returns(make):
            return lambda built: make(report.note_draft(built)['request_id'])
        cases = {
            'no_ref': returns(lambda rid: dict(request_id=rid)),
            'null_ref': returns(lambda rid: dict(request_id=rid, record_id=None, version=1)),
            'malformed_ref': returns(lambda rid: dict(request_id=rid, record_id='record-1', version=1)),
            'nil_uuid': returns(lambda rid: dict(request_id=rid, record_id=str(uuid.UUID(int=0)), version=1)),
            'zero_version': returns(lambda rid: dict(request_id=rid, record_id=str(uuid.uuid4()), version=0)),
            'bool_version': returns(lambda rid: dict(request_id=rid, record_id=str(uuid.uuid4()), version=True)),
            'string_version': returns(lambda rid: dict(request_id=rid, record_id=str(uuid.uuid4()), version='1')),
        }
        for name, publisher in cases.items():
            with self.subTest(name=name):
                o = Occurrence(self.root/name, lambda i, s: UNCHANGED)
                with self.assertRaisesRegex(paired.Refusal, '^occurrence_publication_invalid$'):
                    self.run_with(o, publisher)
                self.assertEqual(list((o.out/'phases').glob('publication-*')), [])
                state = o.run(fabric=False, publish=False)
                self.assertEqual((state['pending'], state['note_published'], state['complete']),
                                 ('note_publication_unconfigured', False, False))

    def test_a_mismatched_request_id_refuses(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        with self.assertRaisesRegex(paired.Refusal, '^occurrence_publication_mismatch$'):
            self.run_with(o, lambda built: dict(request_id=str(uuid.uuid4()), record_id=str(uuid.uuid4()), version=1))
        self.assertEqual(list((o.out/'phases').glob('publication-*')), [])

    def test_a_valid_reference_publishes_exactly_once(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        state = o.run(fabric=False)
        self.assertEqual((state['note_published'], state['complete']), (True, True))
        publication = next(p for p in state['phases'] if p['phase'] == 'publication')
        self.assertEqual(str(uuid.UUID(publication['record_id'])), publication['record_id'])
        self.assertEqual(publication['version'], 1)
        o.run(fabric=False)
        self.assertEqual(len(o.notes.calls), 1)

    def test_a_replayed_publication_record_with_empty_or_tampered_details_refuses(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        o.run(fabric=False)
        path = next((o.out/'phases').glob('publication-*'))
        original = json.loads(path.read_bytes())
        tamper = {
            'empty_publication': lambda r: r.update(publication={}),
            'no_publication': lambda r: r.pop('publication'),
            'request_id': lambda r: r['publication'].update(request_id=str(uuid.uuid4())),
            'record_id': lambda r: r['publication'].update(record_id='not-a-uuid'),
            'version': lambda r: r['publication'].update(version=0),
            'report': lambda r: r['publication'].update(report_sha256='0'*64),
            'schema': lambda r: r.update(schema='other'),
            'phase': lambda r: r.update(phase='publication-other'),
            'phase_id': lambda r: r.update(phase_id='0'*64),
            'outputs': lambda r: r.update(outputs=dict(extra=None)),
            'extra_field': lambda r: r.update(extra=1),
        }
        for name, mutate in tamper.items():
            with self.subTest(name=name):
                record = json.loads(json.dumps(original)); mutate(record)
                path.chmod(0o600); path.write_bytes(C(record))
                with self.assertRaisesRegex(paired.Refusal, '^occurrence_publication-[0-9a-f]{20}_record_invalid$|'
                                                            '^occurrence_publication_record_invalid$'):
                    o.run(fabric=False)
        path.write_bytes(C(original))
        self.assertIs(o.run(fabric=False)['complete'], True)
        self.assertEqual(len(o.notes.calls), 1)


class ResumeTests(Case):
    def test_uncertain_dispatch_resumes_without_duplicate_publication(self):
        o = Occurrence(self.root, lambda i, s: CHANGED.get((i, s), UNCHANGED))
        o.run(); o.approve(); o.run(); o.sessions(); o.run(); o.authorize()
        o.fabric.fail_publish = 1
        state = o.run()
        self.assertEqual(state['pending'], 'dispatch_uncertain_retry')
        self.assertFalse((o.out/'phases/dispatch.json').exists())
        state = o.run()
        self.assertEqual(state['pending'], 'awaiting_grades')
        self.assertEqual(o.published(), (4, 4, 4))
        publications = [call for call in o.fabric.calls if call[0] == 'publish']
        self.assertEqual(len({request_id for _, request_id in publications}), 4)
        self.assertEqual(o.run()['pending'], 'awaiting_grades')
        self.assertEqual(o.published(), (4, 4, 4))

    def test_phase_interrupted_after_its_output_is_adopted_on_repeat(self):
        o = Occurrence(self.root, lambda i, s: CHANGED.get((i, s), UNCHANGED))
        o.run(); o.approve(); o.run(); o.sessions()
        original = occurrence._Phases.complete

        def crash(self, phase, *args, **kwargs):
            if phase == 'plan':
                raise OSError('interrupted')
            return original(self, phase, *args, **kwargs)
        with patch.object(occurrence._Phases, 'complete', crash):
            with self.assertRaisesRegex(paired.Refusal, '^occurrence_invalid_or_unavailable$'):
                o.run()
        self.assertTrue((o.out/'delivery-plan/receipt.json').exists())
        self.assertFalse((o.out/'phases/plan.json').exists())
        self.assertEqual(o.run()['pending'], 'release_authority_missing')
        self.assertTrue((o.out/'phases/plan.json').exists())

    def test_draft_written_but_unrecorded_is_adopted_only_if_identical(self):
        o = Occurrence(self.root, lambda i, s: CHANGED.get((i, s), UNCHANGED))
        o.run(); o.approve()
        original = occurrence._Phases.complete

        def crash(self, phase, *args, **kwargs):
            if phase == 'draft':
                raise OSError('interrupted')
            return original(self, phase, *args, **kwargs)
        with patch.object(occurrence._Phases, 'complete', crash):
            with self.assertRaises(paired.Refusal):
                o.run()
        self.assertEqual(o.run()['pending'], 'grader_sessions_missing')


class ChangedInputTests(Case):
    def test_changed_conversion_request_refuses_instead_of_reusing(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        o.run(fabric=False)
        request = json.loads(Path(o.input['conversion_request']['path']).read_bytes())
        request['namespace'] = 'namespace-B'
        o.input['conversion_request'] = pin(self.root/'request-b.json', C(request))
        with self.assertRaisesRegex(paired.Refusal, '^occurrence_conversion_input_changed$'):
            o.run(fabric=False)

    def test_changed_grading_policy_refuses_downstream_reuse(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        o.run(fabric=False)
        o.input['grading_policy'] = pin(self.root/'policy-b.json', C(dict(POLICY, verdict_labels=['pass'])))
        with self.assertRaisesRegex(paired.Refusal, '^occurrence_bundle_input_changed$'):
            o.run(fabric=False)

    def test_changed_approval_after_the_draft_refuses(self):
        o = Occurrence(self.root, lambda i, s: CHANGED.get((i, s), UNCHANGED))
        o.run(); o.approve(); o.run()
        approval = json.loads(Path(o.input['draft_approval']['path']).read_bytes())
        approval['valid_until'] = '2026-10-15T12:30:00Z'
        o.input['draft_approval'] = pin(self.root/'approval-b.json', C(approval))
        with self.assertRaisesRegex(paired.Refusal, '^occurrence_draft_input_changed$'):
            o.run()

    def test_changed_outputs_refuse(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        o.run(fabric=False)
        path = o.out/'bundle/bundle.json'
        path.chmod(0o600); path.write_bytes(path.read_bytes().replace(b'"pass"', b'"PASS"'))
        with self.assertRaisesRegex(paired.Refusal, '^occurrence_bundle_output_changed$'):
            o.run(fabric=False)

    def test_input_document_must_match_its_pin(self):
        o = Occurrence(self.root, lambda i, s: UNCHANGED)
        reference = o.pin_input()
        Path(reference['path']).write_bytes(b'{}')
        with self.assertRaisesRegex(paired.Refusal, '^occurrence_input_pin_mismatch$'):
            occurrence.run_occurrence(reference, o.out, now=NOW)

    def test_missing_case_material_for_a_changed_pair_is_pending(self):
        o = Occurrence(self.root, lambda i, s: CHANGED.get((i, s), UNCHANGED))
        request = json.loads(Path(o.input['conversion_request']['path']).read_bytes())
        request['cases'] = [c for c in request['cases'] if c['case'] != 'task-03']
        o.input['conversion_request'] = pin(self.root/'request-missing.json', C(request))
        state = o.run()
        self.assertEqual((state['status'], state['pending']), ('pending', 'case_material_missing'))
        self.assertFalse((o.out/'conversion').exists())


class CliTests(Case):
    def test_cli_without_delivery_config_runs_offline_phases_only(self):
        for name, state_of, code, status in (('zero', lambda i, s: UNCHANGED, 3, 'pending'),
                                             ('changed', lambda i, s: CHANGED.get((i, s), UNCHANGED), 3, 'pending')):
            with self.subTest(name=name):
                o = Occurrence(self.root/name, state_of)
                reference = o.pin_input()
                with redirect_stdout(io.StringIO()) as printed:
                    self.assertEqual(occurrence.main(['--input', reference['path'], '--input-sha256', reference['sha256'],
                                                      '--out', str(o.out), '--now', '2026-10-15T12:00:00Z']), code)
                self.assertEqual(json.loads(printed.getvalue())['status'], status)


if __name__ == '__main__':
    unittest.main()
