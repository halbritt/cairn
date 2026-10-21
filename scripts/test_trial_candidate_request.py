"""Candidate-triggered weekly paired requests with fakes and disposable stores only.

No provider, model, native actor or shared database is used. The compiler is a
fake that writes an inert file; git runs against an owned temporary repository.
The optional PostgreSQL smoke test creates and removes its own disposable cluster.
"""
import contextlib
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import uuid

import trial_candidate_request as candidate
import trial_paired_request as paired
import trial_weekly_request as dispatch
from test_trial_weekly_request import END, Fixture, NOW
from test_trial_paired_capacity import write_pin

PREPARE_NOW = NOW + dt.timedelta(minutes=2)  # Inside the 20:01-20:04 occurrence.
PG_BIN = Path('/usr/lib/postgresql/16/bin')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True,
                          text=True, env=dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@t',
                                              GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@t')).stdout.strip()


def make_repo(path):
    path.mkdir()
    git(path, 'init', '-q', '-b', 'main')
    for name in ('cmd/cairn/main.go', 'integrations/lifecycle/memory.py',
                 'integrations/lifecycle/inbox_recall.py'):
        (path/name).parent.mkdir(parents=True, exist_ok=True)
        (path/name).write_text('# base ' + name + '\n')
    git(path, 'add', '-A'); git(path, 'commit', '-q', '-m', 'base')
    current = git(path, 'rev-parse', 'HEAD')
    git(path, 'checkout', '-q', '-b', 'feature/recall')
    (path/'integrations/lifecycle/memory.py').write_text('# candidate recall change\n')
    git(path, 'commit', '-q', '-am', 'candidate')
    cand = git(path, 'rev-parse', 'HEAD')
    git(path, 'checkout', '-q', 'main')
    return current, cand


class FakeCompiler:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, source, output):
        self.calls.append(Path(source))
        if self.fail:
            raise subprocess.CalledProcessError(1, ['go', 'build'])
        output.parent.mkdir(parents=True, exist_ok=True)
        memory = (Path(source)/'integrations/lifecycle/memory.py').read_bytes()
        output.write_bytes(b'inert fake binary for ' + memory)


class CandidateRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = Fixture(self.root)
        self.current, self.cand = make_repo(self.root/'repo')
        self.queue = self.root/'queue'
        self.queue.mkdir(mode=0o700)
        self.pg = self.root/'pgbin'
        self.pg.mkdir()
        for name in ('initdb', 'pg_ctl', 'postgres', 'psql'):
            (self.pg/name).write_text('#!/bin/sh\nexit 1\n')
            (self.pg/name).chmod(0o755)
        self.observation = dict(schema='cairn.weekly-usage-observation/1', source_run='synthetic-last-run',
            roles={role: dict(attempts=60, tokens=600_000, requests=120) for role in candidate.ROLES})
        self.snapshot = dict(schema='cairn.provider-quota-snapshot/1', observed_at='2026-10-09T19:58:00Z',
            roles={role: dict(remaining_tokens=10_000_000, remaining_requests=10_000, http_429_active=False)
                   for role in candidate.ROLES})
        self.write_usage()
        request = self.fixture.request
        execution = self.fixture.execution
        self.profile = dict(
            schema=candidate.PROFILE_SCHEMA, repo=str(self.root/'repo'), current_ref='main',
            queue=str(self.queue), campaign_id=request['campaign_id'],
            weekly_source={k: self.fixture.reference[k] for k in ('root', 'label_version', 'frozen_sha256')},
            native=execution['native'], seeds=[1, 2, 3], workload=execution['workload'],
            embedding=dict(worker=self.fixture.component, config=self.fixture.component,
                           model_identity=self.fixture.component),
            recall=dict(recall_mode='agent_tools', semantic_fallback=True),
            postgres=dict(bin_dir=str(self.pg)),
            schedule=dict(weekday=4, time='20:01', window_minutes=3),
            proposal=dict(capacity=request['capacity'], grading=request['grading'],
                          supervisor_source=request['supervisor_source'],
                          permission_profile=request['permission_profile'],
                          reply_recipient_id=request['reply']['recipient_id']),
            usage=dict(observation=self.observation_pin, quota_snapshot=str(self.root/'quota.json'),
                       max_age_seconds=600, margin_percent=125))

    def write_usage(self):
        self.observation_pin = write_pin(self.root/'usage-observation.json', self.observation)
        (self.root/'quota.json').write_text(json.dumps(self.snapshot))

    def run_request(self, ref='feature/recall', compiler=None, **kwargs):
        return candidate.request(self.profile, ref, now=NOW, compiler=compiler or FakeCompiler(), **kwargs)

    def queue_entries(self):
        return sorted(p.name for p in self.queue.iterdir())

    def test_candidate_branch_is_queued_as_a_reviewable_weekly_draft(self):
        compiler = FakeCompiler()
        receipt = self.run_request(compiler=compiler)
        self.assertEqual(receipt['status'], 'queued_for_review')
        self.assertEqual(receipt['arms']['CURRENT']['commit'], self.current)
        self.assertEqual(receipt['arms']['CAND']['commit'], self.cand)
        self.assertEqual(receipt['scheduled_at'], '2026-10-09T20:01:00Z')
        self.assertEqual(receipt['expires_at'], '2026-10-09T20:04:00Z')
        self.assertEqual(receipt['launch'], dict(status='refused', code='provider_execution_unavailable'))
        self.assertFalse(receipt['runtime_admitted'])
        self.assertTrue(receipt['precheck']['admitted'])
        self.assertEqual(len(compiler.calls), 2)
        entry = self.queue/receipt['request_id']
        self.assertEqual(entry.stat().st_mode & 0o777, 0o700)
        # Each arm binary is built from its own exact commit.
        execution = json.loads((entry/'execution.json').read_text())
        arms = execution['memory_arms']
        self.assertNotEqual(arms['CURRENT']['binary']['sha256'], arms['CAND']['binary']['sha256'])
        self.assertIn(b'candidate recall change', Path(arms['CAND']['binary']['path']).read_bytes())
        self.assertEqual(arms['CURRENT']['api_revision'], self.current)
        self.assertEqual(arms['CAND']['api_revision'], self.cand)
        self.assertEqual(Path(arms['CAND']['hook']['path']).read_text(), '# candidate recall change\n')
        for field in ('embedding_worker', 'embedding_config', 'embedding_model_identity'):
            self.assertEqual(arms['CURRENT'][field], arms['CAND'][field])
        # The existing weekly draft producer bound the full 126-row schedule.
        core = json.loads((entry/'draft/request-core.json').read_text())
        schedule = json.loads((entry/'draft/schedule.json').read_text())
        subjects = json.loads((entry/'draft/review-subjects.json').read_text())
        self.assertEqual(core['schema'], dispatch.SCHEMA)
        self.assertEqual(len(schedule['runs']), 126)
        self.assertEqual(core['plan']['attempts'], 126)
        self.assertEqual(core['limits'], paired.ORIGINAL_LIMITS)
        self.assertEqual(set(subjects['subjects']), set(paired.REVIEWS))
        self.assertEqual(core['weekly_input']['execution']['path'], str(entry/'execution.json'))
        self.assertFalse((entry/'preparation').exists())
        # Disposable PostgreSQL is declared under the entry and never an existing DSN.
        environment = json.loads((entry/'environment.json').read_text())
        self.assertEqual(environment['postgres']['cluster_root'], str(entry/'cluster'))
        self.assertEqual(environment['postgres']['listen_addresses'], '')
        self.assertFalse((entry/'cluster').exists())
        self.assertEqual(environment['embedding']['worker'], self.fixture.component)
        state = json.loads((entry/'entry.json').read_text())
        self.assertEqual(state['state'], 'queued_for_review')
        listed = candidate.list_queue(self.profile)
        self.assertEqual([e['request_id'] for e in listed], [receipt['request_id']])

    def test_usage_precheck_refuses_before_any_build_or_queue_write(self):
        self.snapshot['roles']['primary_grader']['remaining_tokens'] = 10
        self.write_usage(); self.profile['usage']['observation'] = self.observation_pin
        compiler = FakeCompiler()
        with self.assertRaises(candidate.PrecheckRefusal) as caught:
            self.run_request(compiler=compiler)
        self.assertEqual(str(caught.exception), 'usage_precheck_refused')
        self.assertEqual(caught.exception.detail['code'], 'PRECHECK_INSUFFICIENT_HEADROOM')
        self.assertEqual(compiler.calls, [])
        self.assertEqual(self.queue_entries(), [])

    def test_required_usage_scales_last_run_to_whole_plan_with_margin(self):
        demand = candidate.plan_demand(self.observation, margin_percent=125)
        # 600000 tokens / 60 attempts * 63 attempts * 1.25 = 787500.
        self.assertEqual(demand['roles']['primary_actor'], dict(required_tokens=787500, required_requests=158))
        self.assertEqual(demand['attempts'], 126)

    def test_stale_missing_or_percentage_quota_snapshot_refuses(self):
        cases = [
            lambda s: s.update(observed_at='2026-10-09T19:00:00Z'),
            lambda s: s.update(observed_at='2026-10-09T20:30:00Z'),
            lambda s: s['roles'].pop('secondary_actor'),
            lambda s: s['roles']['primary_actor'].update(remaining_percent=50),
            lambda s: s['roles']['primary_actor'].update(http_429_active='no'),
        ]
        for mutate in cases:
            with self.subTest(mutate=mutate):
                self.snapshot = dict(schema='cairn.provider-quota-snapshot/1', observed_at='2026-10-09T19:58:00Z',
                    roles={role: dict(remaining_tokens=10_000_000, remaining_requests=10_000, http_429_active=False)
                           for role in candidate.ROLES})
                mutate(self.snapshot); self.write_usage()
                with self.assertRaisesRegex(paired.Refusal, 'usage_snapshot_unavailable'):
                    self.run_request()
        os.unlink(self.root/'quota.json')
        with self.assertRaisesRegex(paired.Refusal, 'usage_snapshot_unavailable'):
            self.run_request()
        self.assertEqual(self.queue_entries(), [])

    def test_unresolvable_or_identical_candidate_refuses(self):
        with self.assertRaisesRegex(paired.Refusal, 'candidate_unresolved'):
            self.run_request('no-such-branch')
        with self.assertRaisesRegex(paired.Refusal, 'candidate_unresolved'):
            self.run_request('--upload-pack=evil')
        with self.assertRaisesRegex(paired.Refusal, 'candidate_equals_current'):
            self.run_request('main')
        self.assertEqual(self.queue_entries(), [])

    def test_occurrence_ids_are_deterministic_and_repeat_requests_are_idempotent(self):
        compiler = FakeCompiler()
        first = self.run_request(compiler=compiler)
        expected = candidate.occurrence_ids(self.profile['campaign_id'], first['scheduled_at'], self.cand, self.current)
        self.assertEqual((first['request_id'], first['occurrence_id']), (expected['request'], expected['occurrence']))
        proposal = json.loads((self.queue/first['request_id']/'proposal.json').read_text())
        self.assertEqual(proposal['reply']['event_id'], expected['event'])
        self.assertEqual(proposal['reply']['correlation_id'], expected['correlation'])
        before = sorted(str(p) for p in self.queue.rglob('*'))
        again = self.run_request(compiler=compiler)
        self.assertEqual(again['status'], 'already_queued')
        self.assertEqual(again['request_id'], first['request_id'])
        self.assertEqual(again['draft'], first['draft'])
        self.assertEqual(len(compiler.calls), 2)  # No rebuild on the repeat.
        self.assertEqual(sorted(str(p) for p in self.queue.rglob('*')), before)
        # A different candidate commit in the same occurrence gets distinct IDs.
        other = candidate.occurrence_ids(self.profile['campaign_id'], first['scheduled_at'], self.current, self.cand)
        self.assertNotEqual(other['occurrence'], expected['occurrence'])

    def test_build_failure_marks_entry_failed_and_allows_retry(self):
        with self.assertRaisesRegex(paired.Refusal, 'arm_build_failed'):
            self.run_request(compiler=FakeCompiler(fail=True))
        self.assertEqual([e['state'] for e in candidate.list_queue(self.profile)], ['failed'])
        self.assertEqual(candidate.list_queue(self.profile)[0]['code'], 'arm_build_failed')
        receipt = self.run_request()
        self.assertEqual(receipt['status'], 'queued_for_review')
        archived = list((self.queue/'failed').iterdir())
        self.assertEqual([p.name for p in archived], [receipt['request_id'] + '.1'])
        self.assertEqual(sorted(e['state'] for e in candidate.list_queue(self.profile)), ['failed', 'queued_for_review'])

    def test_missing_arm_component_refuses(self):
        git(self.root/'repo', 'checkout', '-q', 'feature/recall')
        git(self.root/'repo', 'rm', '-q', 'integrations/lifecycle/inbox_recall.py')
        git(self.root/'repo', 'commit', '-q', '-m', 'drop bridge')
        git(self.root/'repo', 'checkout', '-q', 'main')
        with self.assertRaisesRegex(paired.Refusal, 'arm_component_missing'):
            self.run_request()

    def test_profile_is_closed_and_postgres_must_be_disposable(self):
        bad = copy.deepcopy(self.profile); bad['postgres']['dsn'] = 'postgres://shared/cairn'
        with self.assertRaisesRegex(paired.Refusal, 'profile_postgres_shape'):
            candidate.request(bad, 'feature/recall', now=NOW, compiler=FakeCompiler())
        bad = copy.deepcopy(self.profile); bad['extra'] = True
        with self.assertRaisesRegex(paired.Refusal, 'profile_shape'):
            candidate.request(bad, 'feature/recall', now=NOW, compiler=FakeCompiler())
        os.unlink(self.pg/'initdb')
        with self.assertRaisesRegex(paired.Refusal, 'postgres_binaries_unavailable'):
            self.run_request()
        self.queue.chmod(0o755)
        with self.assertRaisesRegex(paired.Refusal, 'private_queue_required'):
            self.run_request()

    def test_capacity_not_covering_the_weekly_occurrence_refuses(self):
        self.profile['schedule'] = dict(weekday=4, time='20:03', window_minutes=10)
        with self.assertRaisesRegex(paired.Refusal, 'draft_capacity_interval'):
            self.run_request()
        self.assertEqual(candidate.list_queue(self.profile)[0]['state'], 'failed')

    # ------------------------------------------------------------ prepare

    def reviewed(self, receipt, *, valid_until=END):
        entry = self.queue/receipt['request_id']
        core = json.loads((entry/'draft/request-core.json').read_text())
        subjects = json.loads((entry/'draft/review-subjects.json').read_text())['subjects']
        artifact = self.fixture.request['supervisor_source']
        dispositions = {name: dict(artifact=artifact, subject_sha256=subjects[name], reviewer_id=str(uuid.uuid4()),
                                   state='accepted', valid_until=valid_until) for name in paired.REVIEWS}
        full = dict(core, dispositions=dispositions)
        companion = dict(self.fixture.companion, request_sha256=paired.canonical_sha(full))
        review = dict(self.fixture.review, subject_sha256=paired.canonical_sha(companion))
        return full, dict(dispositions=write_pin(self.root/'dispositions.json', dispositions),
                          companion=write_pin(self.root/'companion.json', companion),
                          review=write_pin(self.root/'companion-review.json', review)), companion, review

    def assert_untouched(self, receipt):
        self.assertFalse((self.queue/'registry').exists())
        self.assertFalse((self.queue/receipt['request_id']/'preparation').exists())
        self.assertFalse((self.queue/receipt['request_id']/'prepared.json').exists())

    def test_prepare_binds_queued_draft_reviews_companion_and_output_exactly(self):
        receipt = self.run_request()
        full, pins, _, _ = self.reviewed(receipt)
        result = candidate.prepare(self.profile, receipt['request_id'], now=PREPARE_NOW, **pins)
        entry = self.queue/receipt['request_id']
        self.assertEqual(result['status'], 'prepared_for_review')
        self.assertFalse(result['runtime_admitted'])
        self.assertEqual(result['request_sha256'], paired.canonical_sha(full))
        self.assertEqual(result['schedule_file'], full['schedule'])
        self.assertEqual(result['schedule_file']['path'], str(entry/'draft/schedule.json'))
        self.assertEqual(result['execution_file'], full['weekly_input']['execution'])
        self.assertEqual(result['execution_file']['path'], str(entry/'execution.json'))
        self.assertEqual(result['plan_sha256'], full['plan']['sha256'])
        self.assertEqual(json.loads((entry/'preparation/request.json').read_text()), full)
        self.assertEqual(json.loads((entry/'preparation/preparation.json').read_text()), result)
        markers = sorted(p.name for p in (self.queue/'registry').iterdir())
        self.assertEqual(markers, sorted(['occurrence-%s.json' % receipt['occurrence_id'],
                                          'request-%s.json' % receipt['request_id']]))
        prepared = json.loads((entry/'prepared.json').read_text())
        self.assertEqual((prepared['dispositions'], prepared['companion'], prepared['review']),
                         (pins['dispositions'], pins['companion'], pins['review']))
        self.assertEqual(candidate.list_queue(self.profile)[0]['state'], 'prepared_for_review')
        with self.assertRaisesRegex(paired.Refusal, 'candidate_already_prepared'):
            candidate.prepare(self.profile, receipt['request_id'], now=PREPARE_NOW, **pins)

    def test_missing_or_stale_companion_or_review_refuses_before_registry_or_output(self):
        receipt = self.run_request()
        _, pins, companion, review = self.reviewed(receipt)
        for name in ('companion', 'review'):
            with self.subTest(missing=name):
                with self.assertRaisesRegex(paired.Refusal, 'weekly_companion_required'):
                    candidate.prepare(self.profile, receipt['request_id'], now=PREPARE_NOW,
                                      **dict(pins, **{name: None}))
                self.assert_untouched(receipt)
        stale_companion = dict(companion, valid_until='2026-10-09T20:02:30Z')
        stale_review = dict(review, subject_sha256=paired.canonical_sha(stale_companion))
        late_review = dict(review, observed_at='2026-10-09T20:01:30Z')
        for label, chosen in (('companion', dict(companion=write_pin(self.root/'stale-c.json', stale_companion),
                                                 review=write_pin(self.root/'stale-r.json', stale_review))),
                              ('review', dict(review=write_pin(self.root/'late-r.json', late_review)))):
            with self.subTest(stale=label):
                with self.assertRaisesRegex(paired.Refusal, 'companion_interval'):
                    candidate.prepare(self.profile, receipt['request_id'], now=PREPARE_NOW, **dict(pins, **chosen))
                self.assert_untouched(receipt)
        with self.assertRaisesRegex(paired.Refusal, 'request_expired'):
            candidate.prepare(self.profile, receipt['request_id'], now=NOW, **pins)
        self.assert_untouched(receipt)

    def test_prepare_refuses_changed_draft_or_input_pins(self):
        receipt = self.run_request()
        _, pins, _, _ = self.reviewed(receipt)
        bad = dict(pins, dispositions=dict(pins['dispositions'], sha256='0'*64))
        with self.assertRaisesRegex(paired.Refusal, 'pin_mismatch'):
            candidate.prepare(self.profile, receipt['request_id'], now=PREPARE_NOW, **bad)
        core = self.queue/receipt['request_id']/'draft/request-core.json'
        original = core.read_bytes()
        core.chmod(0o600); core.write_bytes(original.replace(b'"schema"', b'"schema" ', 1))
        with self.assertRaisesRegex(paired.Refusal, 'candidate_draft_changed'):
            candidate.prepare(self.profile, receipt['request_id'], now=PREPARE_NOW, **pins)
        core.write_bytes(original)
        execution = self.queue/receipt['request_id']/'execution.json'
        execution.chmod(0o600); execution.write_bytes(execution.read_bytes() + b' ')
        with self.assertRaisesRegex(paired.Refusal, 'pin_mismatch'):
            candidate.prepare(self.profile, receipt['request_id'], now=PREPARE_NOW, **pins)
        self.assert_untouched(receipt)
        with self.assertRaisesRegex(paired.Refusal, 'candidate_not_queued'):
            candidate.prepare(self.profile, str(uuid.uuid4()), now=PREPARE_NOW, **pins)

    def test_next_occurrence_is_the_first_weekly_slot_after_now(self):
        schedule = dict(weekday=0, time='03:00', window_minutes=60)
        start, end = candidate.next_occurrence(schedule, NOW)  # NOW is Friday 2026-10-09 20:00Z.
        self.assertEqual(start, dt.datetime(2026, 10, 12, 3, tzinfo=dt.timezone.utc))
        self.assertEqual(end - start, dt.timedelta(minutes=60))
        start, _ = candidate.next_occurrence(dict(weekday=4, time='20:00', window_minutes=5), NOW)
        self.assertEqual(start, dt.datetime(2026, 10, 16, 20, tzinfo=dt.timezone.utc))

    def test_cli_request_list_and_fail_closed_launch(self):
        profile_path = self.root/'profile.json'
        profile_path.write_text(json.dumps(self.profile))

        class Clock(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW

        def cli(argv):
            out = io.StringIO()
            with patch.object(candidate.dt, 'datetime', Clock), \
                 patch.object(candidate, 'go_build', FakeCompiler()), contextlib.redirect_stdout(out):
                status = candidate.main(argv)
            return status, json.loads(out.getvalue())

        status, receipt = cli(['request', '--profile', str(profile_path), '--candidate', 'feature/recall'])
        self.assertEqual((status, receipt['status']), (0, 'queued_for_review'))
        status, listed = cli(['list', '--profile', str(profile_path)])
        self.assertEqual((status, [e['request_id'] for e in listed['entries']]), (0, [receipt['request_id']]))
        status, refused = cli(['prepare', receipt['request_id'], '--profile', str(profile_path)])
        self.assertEqual((status, refused), (2, dict(status='refused', code='dispositions_required')))
        self.assertFalse((self.queue/'registry').exists())
        status, refused = cli(['launch', receipt['request_id']])
        self.assertEqual((status, refused), (2, dict(status='refused', code='provider_execution_unavailable')))
        status, refused = cli(['request', '--profile', str(self.root/'absent.json'), '--candidate', 'x'])
        self.assertEqual((status, refused['status']), (2, 'refused'))
        self.assertNotIn(str(self.root), json.dumps(refused))

    def test_uncertain_smoke_cleanup_preserves_entry_and_refuses_retry(self):
        compiler = FakeCompiler()
        with patch.object(candidate, 'smoke_postgres',
                          side_effect=paired.Refusal('postgres_smoke_cleanup_unknown')) as smoke:
            with self.assertRaisesRegex(paired.Refusal, '^postgres_smoke_cleanup_unknown$'):
                self.run_request(compiler=compiler, smoke_postgres=True)
            entries = candidate.list_queue(self.profile)
            self.assertEqual([e['state'] for e in entries], ['cleanup_unknown'])
            entry = self.queue/entries[0]['request_id']
            before = (entry/'entry.json').read_bytes()
            with self.assertRaisesRegex(paired.Refusal, '^candidate_cleanup_unknown$'):
                self.run_request(compiler=compiler, smoke_postgres=True)
            self.assertEqual((entry/'entry.json').read_bytes(), before)
            self.assertFalse((self.queue/'failed').exists())
            self.assertEqual(len(compiler.calls), 2)
            self.assertEqual(smoke.call_count, 1)

    @unittest.skipUnless((PG_BIN/'initdb').exists(), 'PostgreSQL binaries unavailable')
    def test_smoke_postgres_uses_and_removes_its_own_disposable_cluster(self):
        self.profile['postgres']['bin_dir'] = str(PG_BIN)
        receipt = self.run_request(smoke_postgres=True)
        entry = self.queue/receipt['request_id']
        environment = json.loads((entry/'environment.json').read_text())
        self.assertEqual(environment['smoke'], dict(status='passed', query='select 1'))
        self.assertFalse((entry/'pg-smoke').exists())
        self.assertFalse((entry/'cluster').exists())


if __name__ == '__main__':
    unittest.main()
