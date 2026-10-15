"""Generated weekly fixtures only; no native, provider, subprocess or store."""
import copy
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_paired_request as paired
import trial_weekly_request as dispatch
import trial_weekly_draft as drafting
from test_trial_weekly_request import Fixture, NOW


def proposal(fixture):
    value = copy.deepcopy(fixture.request)
    for key in ('plan', 'schedule', 'dispositions', 'limits'):
        del value[key]
    value['schema'] = 'cairn.weekly-eval.proposal/1'
    return value


class WeeklyDraftTests(unittest.TestCase):
    def cli(self, argv):
        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        stdout = io.StringIO()
        with patch.object(drafting.dt, 'datetime', Clock), contextlib.redirect_stdout(stdout):
            status = drafting.main(argv)
        return status, json.loads(stdout.getvalue())

    def test_public_draft_derives_exact_original_schedule_core_and_review_subjects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root); submitted = proposal(fixture)
            receipt = drafting.draft(submitted, root/'draft', now=NOW)
            schedule = json.loads((root/'draft/schedule.json').read_bytes())
            core = json.loads((root/'draft/request-core.json').read_bytes())
            reviews = json.loads((root/'draft/review-subjects.json').read_bytes())
            self.assertEqual(schedule, fixture.bundle['schedule'])
            self.assertEqual(len(schedule['runs']), 126)
            self.assertEqual({(r['case'], r['seed'], r['arm']) for r in schedule['runs']},
                             {(c, s, a) for c in fixture.bundle['plan']['cases']
                              for s in [1, 2, 3] for a in ['CURRENT', 'CAND']})
            self.assertEqual(schedule['distractors'], fixture.bundle['generated'])
            self.assertEqual(schedule['distractors']['count'], 1900)
            self.assertEqual(core['plan'], fixture.bundle['plan'])
            self.assertEqual(len(core['plan']), 7)
            self.assertEqual(core['limits'], paired.ORIGINAL_LIMITS)
            self.assertNotIn('dispositions', core)
            self.assertEqual(core['schedule'], {'path': str(root/'draft/schedule.json'),
                'sha256': hashlib.sha256((root/'draft/schedule.json').read_bytes()).hexdigest()})
            self.assertEqual(reviews['request_core_sha256'], paired.canonical_sha(core))
            self.assertEqual(reviews['subjects'], dispatch.review_subjects(core, fixture.bundle))
            self.assertEqual(receipt['status'], 'drafted_for_review')
            self.assertFalse(receipt['admission'])
            self.assertEqual(set(p.name for p in (root/'draft').iterdir()),
                             {'schedule.json', 'request-core.json', 'review-subjects.json'})
            self.assertEqual(list((root/'registry').iterdir()), [])
            self.assertFalse((root/'output').exists())
            self.assertEqual(submitted, proposal(fixture))

    def test_applicable_control_failures_refuse_before_any_output(self):
        for failure in ['uuid', 'interval', 'expired', 'reply', 'grader', 'supervisor_pin',
                        'profile_pin', 'capacity_pin', 'capacity_model', 'capacity_interval', 'workload_basis']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); fixture = Fixture(root); submitted = proposal(fixture)
                if failure == 'uuid': submitted['request_id'] = 'invalid'
                if failure == 'interval': submitted['scheduled_at'] = submitted['expires_at']
                if failure == 'expired': submitted['expires_at'] = '2026-10-08T20:00:00Z'
                if failure == 'reply': submitted['reply']['event_id'] = 'invalid'
                if failure == 'grader': submitted['grading']['secondary'] = submitted['grading']['primary']
                if failure == 'supervisor_pin': submitted['supervisor_source']['sha256'] = 'f'*64
                if failure == 'profile_pin': submitted['permission_profile']['settings']['sha256'] = 'f'*64
                if failure == 'capacity_pin': submitted['capacity']['sha256'] = 'f'*64
                if failure == 'capacity_model': submitted['grading']['primary']['model'] = 'another-model'
                if failure == 'capacity_interval': submitted['expires_at'] = '2026-10-10T20:05:00Z'
                if failure == 'workload_basis':
                    basis = fixture.provider.workload['roles']['CURRENT']['basis']
                    Path(basis['path']).write_text('changed selected basis')
                with self.assertRaises(paired.Refusal):
                    drafting.draft(submitted, root/'draft', now=NOW)
                self.assertFalse((root/'draft').exists())
                self.assertFalse((root/'output').exists())
                self.assertEqual(list((root/'registry').iterdir()), [])

    def test_destinations_cannot_overlap_source_controls_or_each_other(self):
        for placement in ['source', 'nested_output', 'nested_draft', 'supervisor', 'binary',
                          'workload_basis', 'reservation', 'account', 'route']:
            with self.subTest(placement=placement), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); fixture = Fixture(root); submitted = proposal(fixture)
                destination = root/'draft'
                if placement == 'source': destination = fixture.source/'new-draft'
                if placement == 'nested_output': destination = root/'output/draft'
                if placement == 'nested_draft': submitted['output'] = str(destination/'future-output')
                if placement == 'supervisor': submitted['output'] = submitted['supervisor_source']['path']+'/child'
                if placement == 'binary': submitted['output'] = fixture.execution['native']['binary']['path']+'/child'
                if placement == 'workload_basis':
                    submitted['output'] = fixture.provider.workload['roles']['CURRENT']['basis']['path']+'/child'
                if placement in ('reservation', 'account', 'route'):
                    field = {'reservation':'reservation','account':'account_binding','route':'route_binding'}[placement]
                    submitted['output'] = fixture.provider.capacity[field]['path']+'/child'
                with self.assertRaisesRegex(paired.Refusal, 'control_path_overlap'):
                    drafting.draft(submitted, destination, now=NOW)
                self.assertFalse(destination.exists())

    def test_cli_emits_only_receipt_and_protects_proposal_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root); submitted = proposal(fixture)
            source = root/'proposal.json'; source.write_bytes(paired.selected_bytes(submitted))
            status, receipt = self.cli(['draft', str(source), '--draft-dir', str(root/'draft')])
            self.assertEqual(status, 0)
            self.assertEqual(receipt['status'], 'drafted_for_review')
            self.assertNotIn('grading', receipt)
            submitted['output'] = str(source/'descendant')
            source.write_bytes(paired.selected_bytes(submitted))
            before = source.read_bytes()
            status, refusal = self.cli(['draft', str(source), '--draft-dir', str(root/'second')])
            self.assertEqual((status, refusal['code']), (2, 'control_path_overlap'))
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((root/'second').exists())

    def test_roundtrip_requires_separately_supplied_reviews_and_companion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root)
            drafting.draft(proposal(fixture), root/'draft', now=NOW)
            core = json.loads((root/'draft/request-core.json').read_bytes())
            with self.assertRaisesRegex(paired.Refusal, 'weekly_request_shape'):
                dispatch.check(core, now=NOW, **fixture.references)
            request = dict(core, dispositions=copy.deepcopy(fixture.request['dispositions']))
            with self.assertRaisesRegex(paired.Refusal, 'review_subject_mismatch'):
                dispatch.check(request, now=NOW, **fixture.references)
            subjects = json.loads((root/'draft/review-subjects.json').read_bytes())['subjects']
            for name, disposition in request['dispositions'].items():
                disposition['subject_sha256'] = subjects[name]
            with self.assertRaisesRegex(paired.Refusal, 'companion_request_mismatch'):
                dispatch.check(request, now=NOW, **fixture.references)
            # Only this synthetic reviewer/companion fixture supplies accepted
            # declarations; the draft producer never manufactures them.
            fixture.request = request
            fixture.seal_companion()
            checked = dispatch.check(request, now=NOW, **fixture.references)
            self.assertEqual(checked['status'], 'validated_for_review')
            prepared = dispatch.prepare(request, registry=root/'registry', now=NOW, **fixture.references)
            self.assertEqual(prepared['status'], 'prepared_for_review')
            self.assertFalse(prepared['admission'])
            self.assertEqual(json.loads((root/'output/request.json').read_bytes()), request)

    def test_schedule_file_hash_and_destination_are_distinct_review_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root); subjects = []
            for name in ('first', 'other'):
                receipt = drafting.draft(proposal(fixture), root/name, now=NOW)
                raw = (root/name/'schedule.json').read_bytes()
                schedule = json.loads(raw)
                core = json.loads((root/name/'request-core.json').read_bytes())
                self.assertEqual(core['schedule']['sha256'], hashlib.sha256(raw).hexdigest())
                self.assertNotEqual(core['schedule']['sha256'], schedule['sha256'])
                self.assertEqual(schedule['sha256'], paired.canonical_sha({k:v for k,v in schedule.items() if k!='sha256'}))
                for filename, pin in receipt['files'].items():
                    content = (root/name/filename).read_bytes()
                    self.assertEqual((pin['sha256'],pin['bytes']), (hashlib.sha256(content).hexdigest(),len(content)))
                subjects.append(json.loads((root/name/'review-subjects.json').read_bytes())['subjects'])
            self.assertEqual((root/'first/schedule.json').read_bytes(), (root/'other/schedule.json').read_bytes())
            self.assertEqual({name for name in subjects[0] if subjects[0][name]!=subjects[1][name]}, {'integration'})

    def test_changed_candidate_source_invalidates_bound_review_subjects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root)
            drafting.draft(proposal(fixture), root/'first', now=NOW)
            before = json.loads((root/'first/review-subjects.json').read_bytes())['subjects']
            fixture.execution['memory_arms']['CAND']['api_revision'] = '9'*40
            fixture.reseal_bundle()
            drafting.draft(proposal(fixture), root/'other', now=NOW)
            after = json.loads((root/'other/review-subjects.json').read_bytes())['subjects']
            self.assertNotEqual(before['comparator'], after['comparator'])
            self.assertNotEqual(before['integration'], after['integration'])
            self.assertEqual(before['supervisor'], after['supervisor'])

    def test_draft_never_copies_private_fixture_bodies_or_review_dispositions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root)
            receipt = drafting.draft(proposal(fixture), root/'draft', now=NOW)
            emitted = b'\n'.join(p.read_bytes() for p in (root/'draft').iterdir()) + paired.selected_bytes(receipt)
            for forbidden in [b'PRIVATE WEEKLY NOTE', b'PRIVATE LOCAL NOTE', b'private paraphrase',
                              b'private direct', b'synthetic private grader asset', b'synthetic_review',
                              b'"correct"', b'"mistake"', b'"dispositions"', b'"accepted"']:
                self.assertNotIn(forbidden, emitted)
            self.assertEqual((root/'draft').stat().st_mode & 0o777, 0o700)
            for path in (root/'draft').iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_aggregate_bound_includes_all_files_and_returned_receipt(self):
        self.assertEqual(paired.FILE_LIMIT, 8*1024*1024)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root)
            receipt = drafting.draft(proposal(fixture), root/'first', now=NOW)
            total = sum(p.stat().st_size for p in (root/'first').iterdir()) + len(paired.selected_bytes(receipt))
            with patch.object(paired, 'FILE_LIMIT', total-1):
                with self.assertRaisesRegex(paired.Refusal, 'weekly_retention_bound'):
                    drafting.draft(proposal(fixture), root/'other', now=NOW)
            self.assertFalse((root/'other').exists())
            with patch.object(paired, 'FILE_LIMIT', total):
                drafting.draft(proposal(fixture), root/'other', now=NOW)

    def test_partial_write_stays_consumed_and_cannot_be_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root)
            original_fsync = os.fsync
            calls = 0
            def fsync(fd):
                nonlocal calls
                calls += 1
                if calls == 2: raise OSError('PRIVATE_IO_DETAIL')
                return original_fsync(fd)
            with patch.object(os, 'fsync', side_effect=fsync):
                with self.assertRaisesRegex(paired.Refusal, '^weekly_draft_invalid_or_unavailable$'):
                    drafting.draft(proposal(fixture), root/'draft', now=NOW)
            self.assertTrue((root/'draft/schedule.json').is_file())
            self.assertFalse((root/'draft/review-subjects.json').exists())
            before = {p.name:p.read_bytes() for p in (root/'draft').iterdir()}
            with self.assertRaisesRegex(paired.Refusal, 'target_exists'):
                drafting.draft(proposal(fixture), root/'draft', now=NOW)
            self.assertEqual(before, {p.name:p.read_bytes() for p in (root/'draft').iterdir()})
            self.assertEqual(list((root/'registry').iterdir()), [])

    def test_closed_proposal_rejects_derived_fields_missing_controls_and_bad_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root)
            mutations = []
            for field in ('plan', 'schedule', 'limits', 'dispositions', 'companion'):
                changed = proposal(fixture); changed[field] = {}
                mutations.append(changed)
            changed = proposal(fixture); del changed['grading']; mutations.append(changed)
            changed = proposal(fixture); changed['schema'] = dispatch.SCHEMA; mutations.append(changed)
            for value in mutations + [None, []]:
                with self.subTest(value_type=type(value).__name__), self.assertRaises(paired.Refusal):
                    drafting.draft(value, root/'draft', now=NOW)
                self.assertFalse((root/'draft').exists())
            source = root/'bad.json'
            for text in ('{"schema":1,"schema":2}', '{"schema":NaN}', 'not json'):
                source.write_text(text)
                status, result = self.cli(['draft', str(source), '--draft-dir', str(root/'draft')])
                self.assertEqual((status, result['status']), (2, 'refused'))
                self.assertFalse((root/'draft').exists())
            status, result = self.cli(['launch', str(source)])
            self.assertEqual((status, result['code']), (2, 'invalid_arguments'))

    def test_existing_symlink_and_stale_transitive_inputs_refuse_before_output(self):
        for fault in ['existing', 'destination_link', 'parent_link', 'basis_link',
                      'execution', 'native', 'source_freeze', 'reservation', 'read_error']:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); fixture = Fixture(root); submitted = proposal(fixture)
                destination = root/'draft'
                if fault == 'existing':
                    destination.mkdir(); (destination/'keep').write_text('untouched')
                if fault == 'destination_link': destination.symlink_to(root/'not-created')
                if fault == 'parent_link':
                    (root/'alias').symlink_to(root, target_is_directory=True); destination = root/'alias/draft'
                if fault == 'basis_link':
                    path = Path(fixture.provider.workload['roles']['CURRENT']['basis']['path'])
                    other = root/'basis-copy'; other.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(other)
                if fault in ('execution', 'native', 'source_freeze', 'reservation'):
                    path = {'execution':Path(fixture.reference['execution']['path']),
                            'native':Path(fixture.execution['native']['binary']['path']),
                            'source_freeze':fixture.source/'FROZEN.json',
                            'reservation':Path(fixture.provider.capacity['reservation']['path'])}[fault]
                    path.write_bytes(path.read_bytes()+b' ')
                real_open = os.open
                def open_file(path, *args, **kwargs):
                    if fault == 'read_error' and Path(path) == Path(submitted['supervisor_source']['path']):
                        raise OSError('PRIVATE_OPEN_DETAIL')
                    return real_open(path, *args, **kwargs)
                with patch.object(os, 'open', side_effect=open_file), self.assertRaises(paired.Refusal):
                    drafting.draft(submitted, destination, now=NOW)
                if fault == 'existing':
                    self.assertEqual(list(destination.iterdir()), [destination/'keep'])
                    self.assertEqual((destination/'keep').read_text(), 'untouched')
                else:
                    self.assertFalse(destination.exists())
                self.assertFalse((root/'output').exists())

    def test_future_occurrence_is_draftable_only_with_present_covering_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root); submitted = proposal(fixture)
            submitted['scheduled_at'] = '2026-10-09T20:01:00Z'
            drafting.draft(submitted, root/'draft', now=NOW)
            core = json.loads((root/'draft/request-core.json').read_bytes())
            self.assertEqual(core['scheduled_at'], submitted['scheduled_at'])
            self.assertEqual(core['occurrence_id'], submitted['occurrence_id'])
            self.assertEqual(list((root/'registry').iterdir()), [])

    def test_opaque_grading_artifact_is_not_interpreted_as_a_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); fixture = Fixture(root); submitted = proposal(fixture)
            forbidden = root/'not-selected'
            content = paired.selected_bytes(dict(path=str(forbidden),sha256='f'*64))
            artifact = root/'opaque.json'; artifact.write_bytes(content)
            submitted['grading']['rubric'] = dict(path=str(artifact),sha256=hashlib.sha256(content).hexdigest())
            real_open = os.open
            def open_file(path, *args, **kwargs):
                if Path(path) == forbidden: raise AssertionError('opaque body was followed')
                return real_open(path, *args, **kwargs)
            with patch.object(os, 'open', side_effect=open_file):
                drafting.draft(submitted, root/'draft', now=NOW)


if __name__ == '__main__':
    unittest.main()
