"""Synthetic public request boundaries; no native process or provider traffic."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

import trial_paired_request as paired
from test_trial_paired_request import request_fixture, pinned, NOW
from test_trial_provider_mapping import Fixture as MappingFixture
from test_trial_paired_capacity import write_pin
from trial_task_input import freeze_input


class WeeklyFixture:
    def __init__(self, root):
        self.root = root
        self.request = request_fixture(root)
        provider = root/'provider'
        provider.mkdir()
        self.provider = MappingFixture(provider)
        inputs = root/'input'
        document = json.loads((inputs/'input.json').read_text())
        case = document['cases'][0]
        document['cases'] = [dict(case, id=ident) for ident in self.provider.plan['cases']]
        (inputs/'input.json').write_text(json.dumps(document))
        (inputs/'FROZEN.json').unlink()
        freeze_input(inputs)
        self.request['frozen_input']['manifest_sha256'] = pinned(inputs/'FROZEN.json')['sha256']
        self.request['dispositions']['task']['subject_sha256'] = pinned(inputs/'input.json')['sha256']
        self.request['dispositions']['integration']['subject_sha256'] = pinned(inputs/'FROZEN.json')['sha256']
        self.review = dict(schema='cairn.paired-companion-review/1', subject_sha256='',
            artifact=write_pin(root/'source-review.json', {'synthetic_review': 'évidence only'}),
            reviewer_id=str(uuid.uuid4()), state='accepted',
            observed_at=self.request['scheduled_at'], valid_until=self.request['expires_at'])
        self.companion = dict(schema='cairn.paired-companion/1', request_sha256='',
            spend_request={}, mapping={}, observed_at=self.request['scheduled_at'],
            valid_until=self.request['expires_at'])
        self.save()

    def save(self):
        plan, _, capacity, spend, mapping = self.provider.save()
        reservation = json.loads(Path(self.provider.capacity['reservation']['path']).read_bytes())
        reservation['valid_until'] = self.provider.capacity['valid_until']
        self.provider.capacity['reservation'] = write_pin(self.provider.root/'reservation.json', reservation)
        capacity = write_pin(self.provider.root/'capacity.json', self.provider.capacity)
        self.provider.mapping['capacity_sha256'] = capacity['sha256']
        mapping = write_pin(self.provider.root/'mapping.json', self.provider.mapping)
        self.request.update(plan=plan, capacity=capacity)
        self.request['dispositions']['comparator']['subject_sha256'] = paired.canonical_sha(
            dict(plan=plan, interfaces=self.request['interfaces']))
        self.companion.update(request_sha256=paired.canonical_sha(self.request),
                              spend_request=spend, mapping=mapping)
        # The reviewer selects this subject independently; no review or
        # companion file digest appears inside its own subject.
        self.review['subject_sha256'] = paired.canonical_sha(self.companion)
        return self.save_selection()

    def save_selection(self):
        self.references = dict(companion=write_pin(self.root/'companion.json', self.companion),
            review=write_pin(self.root/'selected-review.json', self.review))
        return self.references

    def cli(self, command, *, references=True, imported=False):
        request = self.root/'request.json'
        request.write_text(json.dumps(self.request))
        args = [command, str(request)]
        if command == 'prepare':
            args += ['--registry', str(self.root/'registry')]
        if references:
            for name, pin in self.references.items():
                args += ['--'+name, pin['path'], '--'+name+'-sha256', pin['sha256']]
        # Only the process clock is replaced. runpy exercises the real script
        # entry including its canonical module/Refusal identity, real parsing,
        # validators, files, arithmetic and preparation writes.
        prefix = "import datetime as d,sys,runpy; from unittest.mock import patch; "
        clock = "Clock=type('Clock',(d.datetime,),{'now':classmethod(lambda cls,tz=None:d.datetime(2026,10,9,20,tzinfo=d.timezone.utc))}); "
        if imported:
            call = "import trial_paired_request as p; sys.exit(p.main())"
        else:
            call = "runpy.run_path(sys.argv[0],run_name='__main__')"
        script = str(Path(paired.__file__).resolve())
        code = prefix+clock+"sys.path.insert(0,"+repr(str(Path(script).parent))+"); sys.argv="+repr([script, *args])+"; "
        code += "\nwith patch.object(d,'datetime',Clock):\n "+call
        return subprocess.run([sys.executable, '-B', '-c', code], capture_output=True, text=True)


class CompanionTests(unittest.TestCase):
    def test_complete_weekly_checks_and_prepares_the_exact_reviewed_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WeeklyFixture(Path(directory))
            references = fixture.references
            before = copy.deepcopy(fixture.request)
            checked = paired.check(fixture.request, now=NOW, **references)
            self.assertFalse(checked['runtime_admitted'])
            self.assertEqual(list((fixture.root/'registry').iterdir()), [])
            prepared = paired.prepare(fixture.request, now=NOW,
                registry=fixture.root/'registry', **references)
            self.assertEqual(fixture.request, before)
            self.assertEqual(prepared['provider_evidence'], checked['provider_evidence'])
            output = fixture.root/'output'
            projection = json.loads((output/'provider-projection.json').read_bytes())
            self.assertEqual(projection['spend']['total_units'], 656450)
            self.assertEqual(projection['account_totals']['account-B']['spend_units'], 293580)
            evidence = prepared['provider_evidence']
            self.assertEqual(evidence['projection_sha256'], paired.canonical_sha(projection))
            projection_pin = evidence['projection_file']
            self.assertEqual(paired.read_json_pin(dict(path=str(output/projection_pin['file']),
                sha256=projection_pin['sha256'])), projection)
            self.assertNotEqual(projection_pin['sha256'], evidence['projection_sha256'])
            self.assertEqual(evidence['subject_sha256'], paired.canonical_sha(fixture.companion))
            for selected in evidence['inputs'].values():
                retained = output/selected['file']
                self.assertEqual(retained.read_bytes(), Path(selected['source']['path']).read_bytes())
                self.assertEqual(hashlib.sha256(retained.read_bytes()).hexdigest(), selected['source']['sha256'])
                self.assertEqual(retained.stat().st_mode & 0o777, 0o600)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            self.assertEqual(len(list((fixture.root/'registry').iterdir())), 2)

    def test_weekly_without_companion_refuses_before_registration(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WeeklyFixture(Path(directory))
            paired.validate(fixture.request, now=NOW)
            with self.assertRaisesRegex(paired.Refusal, '^weekly_companion_required$'):
                paired.prepare(fixture.request, registry=fixture.root/'registry', now=NOW)
            self.assertEqual(list((fixture.root/'registry').iterdir()), [])
            self.assertFalse((fixture.root/'output').exists())

    def assert_unwritten_refusal(self, fixture, code, references=None):
        references = fixture.references if references is None else references
        for operation in ('check', 'prepare'):
            with self.subTest(operation=operation):
                arguments = dict(now=NOW, **references)
                if operation == 'prepare':
                    arguments['registry'] = fixture.root/'registry'
                with self.assertRaisesRegex(paired.Refusal, '^'+code+'$'):
                    getattr(paired, operation)(fixture.request, **arguments)
                self.assertEqual(list((fixture.root/'registry').iterdir()), [])
                self.assertFalse((fixture.root/'output').exists())

    def test_closed_companion_and_independently_selected_review_refuse_invalid_declarations(self):
        changes = [
            ('companion', 'schema', 'unknown', 'companion_schema'),
            ('companion', 'review', {}, 'companion_shape'),
            ('companion', 'request_sha256', '0'*64, 'companion_request_mismatch'),
            ('review', 'schema', 'unknown', 'companion_review_schema'),
            ('review', 'authenticated', True, 'companion_review_shape'),
            ('review', 'state', 'unknown', 'companion_review_unavailable'),
            ('review', 'subject_sha256', '0'*64, 'companion_review_subject'),
            ('review', 'reviewer_id', 'PRIVATE invalid identity', 'canonical_uuid'),
            ('review', 'valid_until', '2026-10-09T20:04:59Z', 'companion_interval'),
            ('review', 'observed_at', '2026-10-09T19:59:01Z', 'companion_interval'),
            ('review', 'valid_until', '2026-10-09T19:58:00Z', 'companion_interval')]
        for owner, field, value, code in changes:
            with self.subTest(owner=owner, field=field, value=value), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                getattr(fixture, owner)[field] = value
                fixture.save_selection()
                self.assert_unwritten_refusal(fixture, code)
        for omitted in ('companion', 'review'):
            with self.subTest(omitted=omitted), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                refs = dict(fixture.references)
                del refs[omitted]
                self.assert_unwritten_refusal(fixture, 'weekly_companion_required', refs)

    def test_companion_metadata_changes_invalidate_whole_subject_before_writes(self):
        for field, value in [('observed_at','2026-10-09T19:58:00Z'),
                             ('valid_until','2026-10-09T20:06:00Z')]:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                fixture.companion[field] = value
                fixture.save_selection()
                self.assert_unwritten_refusal(fixture, 'companion_review_subject')
                fixture.review['subject_sha256'] = paired.canonical_sha(fixture.companion)
                fixture.save_selection()
                self.assertFalse(paired.check(fixture.request, now=NOW, **fixture.references)['runtime_admitted'])

    def test_selected_source_reviewer_can_be_excluded_from_grading_and_own_disputes(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WeeklyFixture(Path(directory))
            reviewer = fixture.review['reviewer_id']
            fixture.request['grading']['excluded_agents'].append(reviewer)
            fixture.request['grading']['dispute_owner'] = reviewer
            fixture.save()
            paired.check(fixture.request, now=NOW, **fixture.references)
            for role in ('primary', 'secondary'):
                fixture.review['reviewer_id'] = fixture.request['grading'][role]['agent_id']
                fixture.save_selection()
                self.assert_unwritten_refusal(fixture, 'companion_reviewer_is_grader')

    def test_request_hash_binds_nonplan_fields_and_valid_model_identity_changes(self):
        for mutation in ('reply', 'grader_identity', 'grader_model', 'native_model'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                old = copy.deepcopy((fixture.companion, fixture.review))
                if mutation == 'reply':
                    fixture.request['reply']['event_id'] = str(uuid.uuid4())
                elif mutation == 'grader_identity':
                    fixture.request['grading']['primary']['agent_id'] = str(uuid.uuid4())
                else:
                    roles = ('primary',) if mutation == 'grader_model' else ('CURRENT','CAND')
                    model = 'new-explicit-model'
                    if mutation == 'native_model':
                        inputs = fixture.root/'input'
                        document = json.loads((inputs/'input.json').read_bytes())
                        document['native']['model'] = model
                        (inputs/'input.json').write_text(json.dumps(document))
                        (inputs/'FROZEN.json').unlink()
                        freeze_input(inputs)
                        manifest = pinned(inputs/'FROZEN.json')['sha256']
                        fixture.request['frozen_input']['manifest_sha256'] = manifest
                        fixture.request['dispositions']['integration']['subject_sha256'] = manifest
                        fixture.request['dispositions']['task']['subject_sha256'] = pinned(inputs/'input.json')['sha256']
                    else:
                        fixture.request['grading']['primary']['model'] = model
                    for role in roles:
                        fixture.provider.workload['roles'][role]['jobs'][0]['model'] = model
                        fixture.provider.routes['routes'][role]['model'] = model
                fixture.save()
                fixture.companion, fixture.review = old
                fixture.save_selection()
                # Real base validation succeeds, including exact native/grader
                # demand models; a stale whole-request companion must not.
                paired.validate(fixture.request, now=NOW)
                self.assert_unwritten_refusal(fixture, 'companion_request_mismatch')

    def test_mapping_spend_and_allowance_refusals_precede_all_mutation(self):
        changes = [
            (lambda f: f.provider.mapping['quotes']['qA']['accounts']['account-A'].update(model='other'),
             'mapping_quote_route_mismatch'),
            (lambda f: f.provider.mapping['quotes']['qA']['accounts'].update(
                {'account-B': f.provider.mapping['quotes']['qA']['accounts'].pop('account-A')}),
             'mapping_account_quote_mismatch'),
            (lambda f: f.provider.capacity['resources'][0].update(remaining=1226), 'capacity_insufficient'),
            (lambda f: f.provider.spend_request.update(budget_units=656449), 'provider_spend_refused'),
            (lambda f: f.provider.prices['quotes']['qA']['rates'].pop('cache_creation_input_tokens'),
             'provider_spend_refused')]
        for change, code in changes:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                change(fixture)
                fixture.save()
                self.assert_unwritten_refusal(fixture, code)

    def test_every_validity_covers_full_request_interval(self):
        variants = ('companion', 'review', 'mapping', 'capacity', 'binding', 'price',
                    'spend_short', 'disposition')
        for variant in variants:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                code = 'companion_interval'
                if variant in ('companion','review'):
                    getattr(fixture, variant)['valid_until'] = '2026-10-09T20:04:59Z'
                elif variant in ('mapping','capacity','binding'):
                    getattr(fixture.provider, variant)['observed_at'] = '2026-10-09T19:59:01Z'
                elif variant == 'price':
                    fixture.provider.prices['quotes']['qA']['observed_at'] = '2026-10-09T19:59:01Z'
                elif variant.startswith('spend_'):
                    fixture.provider.spend_request['valid_until'] = ('2026-10-09T20:04:59Z'
                        if variant == 'spend_short' else '2026-10-09T20:05:01Z')
                    code = 'companion_spend_interval'
                else:
                    fixture.request['dispositions']['task']['valid_until'] = '2026-10-09T20:04:59Z'
                    code = 'companion_disposition_interval'
                fixture.save()
                self.assert_unwritten_refusal(fixture, code)

    def test_longer_projection_requires_review_through_its_full_end(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WeeklyFixture(Path(directory))
            end = '2026-10-09T20:06:00Z'
            fixture.provider.spend_request['valid_until'] = end
            for document in (fixture.companion, fixture.review, fixture.provider.capacity,
                             fixture.provider.mapping, fixture.provider.binding,
                             *fixture.provider.prices['quotes'].values(),
                             *fixture.request['dispositions'].values()):
                document['valid_until'] = end
            fixture.save()
            self.assertFalse(paired.check(fixture.request, now=NOW, **fixture.references)['runtime_admitted'])
            fixture.review['valid_until'] = fixture.request['expires_at']
            fixture.save_selection()
            self.assert_unwritten_refusal(fixture, 'companion_interval')

    def test_retention_bound_includes_projection_before_any_write(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WeeklyFixture(Path(directory))
            checked = paired.check(fixture.request, now=NOW, **fixture.references)
            total = sum(Path(p['source']['path']).stat().st_size for p in checked['provider_evidence']['inputs'].values())
            artifact = Path(fixture.review['artifact']['path'])
            artifact.write_bytes(b'x'*(paired.FILE_LIMIT-total+artifact.stat().st_size))
            fixture.review['artifact'] = pinned(artifact)
            fixture.save_selection()
            # Input snapshots alone exactly fit; the real serialized projection
            # must also fit. No limit/validator/arithmetic mocks.
            self.assertEqual(sum(Path(p['source']['path']).stat().st_size
                                 for p in checked['provider_evidence']['inputs'].values()), paired.FILE_LIMIT)
            self.assert_unwritten_refusal(fixture, 'companion_retention_bound')

    def test_raw_pins_duplicate_keys_and_nonascii_bytes_remain_distinct_from_canonical_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WeeklyFixture(Path(directory))
            # Noncanonical layout and literal UTF-8 must survive retention.
            artifact = fixture.root/'évidence.json'
            artifact.write_text('Synthetic source review: évidence, not authority.\n', encoding='utf-8')
            fixture.review['artifact'] = pinned(artifact)
            fixture.save_selection()
            path = Path(fixture.references['review']['path'])
            raw = json.dumps(fixture.review, ensure_ascii=False, indent=3).encode()+b'\n\n'
            path.write_bytes(raw)
            fixture.references['review'] = pinned(path)
            self.assertNotEqual(pinned(path)['sha256'], paired.canonical_sha(fixture.review))
            paired.prepare(fixture.request, now=NOW, registry=fixture.root/'registry', **fixture.references)
            self.assertEqual((fixture.root/'output/provider-review.json').read_bytes(), raw)
            self.assertEqual((fixture.root/'output/provider-review-artifact.bin').read_bytes(),
                             Path(fixture.review['artifact']['path']).read_bytes())
        for target in ('companion','review','artifact','duplicate'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                ref = fixture.review['artifact'] if target == 'artifact' else fixture.references[
                    'review' if target == 'duplicate' else target]
                path = Path(ref['path'])
                if target == 'duplicate':
                    path.write_text('{"schema":"PRIVATE","schema":"other"}')
                    fixture.references['review'] = pinned(path)
                else:
                    path.write_bytes(path.read_bytes()+b' ')
                self.assert_unwritten_refusal(fixture, 'duplicate_json_key' if target == 'duplicate' else 'pin_mismatch')

    def test_exact_frozen_actor_and_distinct_grader_models_reach_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = WeeklyFixture(Path(directory))
            inputs = fixture.root/'input'
            document = json.loads((inputs/'input.json').read_bytes())
            document['native']['model'] = 'exact-actor-model'
            (inputs/'input.json').write_text(json.dumps(document))
            (inputs/'FROZEN.json').unlink()
            freeze_input(inputs)
            manifest = pinned(inputs/'FROZEN.json')['sha256']
            fixture.request['frozen_input']['manifest_sha256'] = manifest
            fixture.request['dispositions']['integration']['subject_sha256'] = manifest
            fixture.request['dispositions']['task']['subject_sha256'] = pinned(inputs/'input.json')['sha256']
            expected = dict(CURRENT='exact-actor-model', CAND='exact-actor-model',
                            primary='exact-primary-model', secondary='exact-secondary-model')
            provider = fixture.provider
            for role, model in expected.items():
                if role in ('primary','secondary'):
                    fixture.request['grading'][role]['model'] = model
                provider.workload['roles'][role]['jobs'][0]['model'] = model
                provider.routes['routes'][role]['model'] = model
                old_quote = 'qB' if role == 'CAND' else 'qA'
                quote = 'q'+role
                provider.prices['quotes'][quote] = dict(copy.deepcopy(provider.prices['quotes'][old_quote]), model=model)
                entry = copy.deepcopy(provider.mapping['quotes'][old_quote])
                for account in entry['accounts'].values():
                    account['model'] = model
                provider.mapping['quotes'][quote] = entry
                provider.binding['routes'][role] = dict(model=model, quotes=[quote])
            del provider.prices['quotes']['qB']
            del provider.mapping['quotes']['qB']
            fixture.save()
            paired.prepare(fixture.request, now=NOW, registry=fixture.root/'registry', **fixture.references)
            projection = json.loads((fixture.root/'output/provider-projection.json').read_bytes())
            self.assertEqual({role: projection['job_bindings'][role]['model'] for role in expected}, expected)
            self.assertEqual(projection['spend']['total_units'], 656450)

    def test_cli_check_prepare_and_module_refusals_are_consistent_and_sanitized(self):
        for imported in (False, True):
            with self.subTest(imported=imported), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                for command in ('check', 'prepare'):
                    result = fixture.cli(command, imported=imported)
                    self.assertEqual((result.returncode, result.stderr), (0, ''), result.stdout+result.stderr)
                    self.assertFalse(json.loads(result.stdout)['runtime_admitted'])
        for variant, code in [('missing','weekly_companion_required'), ('mapping','mapping_quote_route_mismatch'),
                              ('spend','provider_spend_refused'), ('unreadable','invalid_or_unavailable_input')]:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory(prefix='PRIVATE-') as directory:
                fixture = WeeklyFixture(Path(directory))
                if variant == 'mapping':
                    fixture.provider.mapping['quotes']['qA']['accounts']['account-A']['model'] = 'PRIVATE payload'
                if variant == 'spend':
                    fixture.provider.spend_request['budget_units'] = 0
                fixture.save()
                if variant == 'unreadable':
                    Path(fixture.references['review']['path']).unlink()
                for command in ('check','prepare'):
                    result = fixture.cli(command, references=variant != 'missing')
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stderr, '')
                    self.assertEqual(json.loads(result.stdout), dict(status='refused', code=code))
                    self.assertNotIn('PRIVATE', result.stdout)
                self.assertEqual(list((fixture.root/'registry').iterdir()), [])

    def test_supplemental_remains_explicit_and_launch_never_reads_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = request_fixture(root)
            self.assertEqual(request['plan']['instrument'], 'supplemental-fresh-tasks')
            self.assertNotIn('provider_evidence', paired.check(request, now=NOW))
            self.assertNotIn('provider_evidence', paired.prepare(request, now=NOW, registry=root/'registry'))
            with self.assertRaisesRegex(paired.Refusal, '^supplemental_companion_unsupported$'):
                paired.check(request, now=NOW, companion={'unreadable':'PRIVATE'})
            result = subprocess.run([sys.executable, '-B', paired.__file__, 'launch',
                str(root/'PRIVATE-nonexistent')], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stderr), (2, ''))
            self.assertEqual(json.loads(result.stdout), dict(status='refused', code='provider_execution_unavailable'))

    def test_cli_argument_and_input_pin_errors_do_not_echo_private_arguments(self):
        for args, code in [
            (['check','/PRIVATE-request','--private-payload'], 'invalid_arguments'),
            (['check','/PRIVATE-request','--companion','/PRIVATE-companion'], 'explicit_pin_required')]:
            with self.subTest(code=code):
                result = subprocess.run([sys.executable, '-B', paired.__file__, *args],
                    capture_output=True, text=True)
                self.assertEqual((result.returncode, result.stderr), (2, ''))
                self.assertEqual(json.loads(result.stdout), dict(status='refused', code=code))

    def test_genuine_write_failure_keeps_partial_preparation_held(self):
        for failure in ('marker', 'retained'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                fixture = WeeklyFixture(Path(directory))
                original, count = os.fsync, 0
                def failing_fsync(fd):
                    nonlocal count
                    count += 1
                    if count == (1 if failure == 'marker' else 5):
                        raise OSError('PRIVATE injected fsync failure')
                    return original(fd)
                with patch.object(os, 'fsync', failing_fsync):
                    with self.assertRaises(OSError):
                        paired.prepare(fixture.request, now=NOW, registry=fixture.root/'registry', **fixture.references)
                markers = list((fixture.root/'registry').iterdir())
                self.assertEqual(len(markers), 1 if failure == 'marker' else 2)
                self.assertTrue(all(json.loads(p.read_bytes())['state'] == 'held_preparation' for p in markers))
                with self.assertRaisesRegex(paired.Refusal, '^occurrence_registered$' if failure == 'marker' else '^target_exists$'):
                    paired.prepare(fixture.request, now=NOW, registry=fixture.root/'registry', **fixture.references)


if __name__ == '__main__':
    unittest.main()
