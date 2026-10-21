"""Synthetic weekly runner contract: fake executor, fake compiler, synthetic cases.

No real case, oracle, account, provider, model, store or campaign is used.
"""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
import tempfile
import unittest
import unittest.mock

import trial_paired_request as paired
import trial_task_eval as te
import trial_weekly_run as weekly
from trial_capacity_adapter import adapt
from test_trial_capacity_adapter import Inputs
from test_trial_paired_capacity import write_pin
from test_trial_provider_mapping import END, NOW

NOW_DT = paired.utc(NOW)
ACCOUNTS = ['account-A', 'account-B']


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME='fixture', GIT_AUTHOR_EMAIL='fixture@example.invalid',
               GIT_COMMITTER_NAME='fixture', GIT_COMMITTER_EMAIL='fixture@example.invalid')
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, env=env).stdout.decode().strip()


def fake_compiler(source, output):
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.write_bytes(b'#!/bin/sh\n# synthetic cairn built from ' + str(source).encode() + b'\n')
    output.chmod(0o700)


def write_synthetic_trial(repo, count=21):
    """Synthetic frozen fixtures: identifiers only, no oracle content."""
    root = repo / 'trials/task-eval'
    root.mkdir(parents=True)
    (root / 'cases.json').write_text(json.dumps(dict(
        baseline_commit='0' * 40, cases=[dict(id=f'synthetic-{n}') for n in range(count)])))
    (root / 'corpus.json').write_text(json.dumps(dict(notes=[])))
    manifest = te.label_manifest(1, root=root)
    (root / 'FROZEN.json').write_text(json.dumps(manifest))
    return root


FAKE_HARNESS = r'''#!/usr/bin/env python3
# Fake trial-task-eval.sh: records argv and writes synthetic legacy output (agent/1, plan, runs).
import hashlib, json, os, sys
from pathlib import Path
argv = sys.argv[1:]
value = lambda flag: argv[argv.index(flag) + 1]
out = Path(value('--output'))
out.mkdir(mode=0o700)
(out / 'argv.json').write_text(json.dumps(dict(argv=argv, pg_bin=os.environ.get('CAIRN_PG_BIN'),
    leaked=sorted(k for k in os.environ if 'OPENROUTER' in k or k == 'ANTHROPIC_API_KEY'))))
cases = [c['id'] for c in json.loads(Path('trials/task-eval/cases.json').read_text())['cases']]
frozen = json.loads(Path('trials/task-eval/FROZEN.json').read_text())
arms = [spec.split(':', 1)[0] for i, spec in enumerate(argv) if i and argv[i - 1] == '--memory']
first, count = int(value('--first-seed')), int(value('--seeds'))
model = value('--model')
mode = os.environ.get('FAKE_HARNESS_MODE', 'complete')
plan = [(c, a, s) for s in range(first, first + count) for c in cases for a in arms]
capped = mode in ('cap', 'bad_unstarted_cap')
admitted = len(plan) if not capped else 40

def task(case):
    return f'Synthetic task for {case}.\n'

def stream(answer):
    return json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text=answer)]))) + '\n'

def record(c, a, s):
    answer = f'Synthetic answer {c} {a} {s}.'
    outcome = 'mistake' if a == 'CAND' and c.endswith(('0', '5')) else 'correct'
    return dict(run_id=f'{c}.{a}.s{s}', case=c, arm=a, seed=s, order=0, harness='claude', model=model,
                wording='task', prompt_bytes=len(task(c).encode()), outcome=outcome, answer_chars=len(answer),
                stream_sha256=hashlib.sha256(stream(answer).encode()).hexdigest(),
                prompt_sha256=hashlib.sha256(task(c).encode()).hexdigest()), stream(answer)

rows = [record(*key) for key in plan[:admitted]]
if mode == 'duplicate':      # root's reproduction: 126 copies of CURRENT/case0/seed0
    rows = [record(cases[0], 'CURRENT', first)] * len(plan)
elif mode == 'replace':      # one planned row missing, replaced by an unplanned arm
    rows = rows[:-1] + [record(cases[0], 'none', first)]
elif mode == 'dup_one':      # every planned row once, plus one exact duplicate
    rows = rows + [rows[0]]
elif mode == 'missing':      # one planned row silently absent, counters still claim complete
    rows = rows[:-1]
elif mode == 'extra':
    rows = rows + [record('synthetic-extra', 'CURRENT', first)]
elif mode == 'wrong_seed':
    rows = [record(c, a, s + 1) for c, a, s in plan]
elif mode == 'nohash':
    rows[0][0].pop('stream_sha256')
elif mode == 'row_provider':
    rows[3][0]['trace'] = dict(admission_failure=dict(provider='claude', reason='authentication_failed', api_error_status=401))
elif mode == 'row_error':
    rows[5][0]['outcome'] = 'harness_error'
stop = None
if capped:
    stop = dict(run_id=rows[-1][0]['run_id'], provider='claude', reason='capacity_exhausted', api_error_status=429)
    rows[-1][0]['trace'] = dict(admission_failure=dict(provider='claude', reason='capacity_exhausted', api_error_status=429))
for row, text in rows:
    base = out / 'runs' / row['run_id']
    base.mkdir(parents=True, exist_ok=True)
    (base / 'result.json').write_text(json.dumps(row, indent=2))
    (base / 'stream.jsonl').write_text(text)
(out / 'plan.json').write_text(json.dumps(dict(arms=arms, wording='task', runs=[[c, a, s, 0] for c, a, s in plan]), indent=2))
document = dict(schema='cairn.task-eval.agent/1', frozen=frozen, arms=arms, model='other-model' if mode == 'model' else model,
                wording='task', harness='claude', distractors=1900,
                admission=dict(state='stopped_provider_failure' if stop else 'complete', stop=stop,
                               planned=len(plan), admitted=admitted,
                               not_started=[dict(run_id=f'{c}.{a}.s{s}', case=c, arm=a, seed=s) for c, a, s in plan[admitted:]]),
                records=sorted((row for row, _ in rows), key=lambda r: r['run_id']))
if mode == 'bad_unstarted_cap':
    document['admission']['not_started'][0]['seed'] = 9
if mode == 'measurement':
    document['measurement_failures'] = [rows[0][0]['run_id']]
(out / 'agent.json').write_text(json.dumps(document, indent=2))
sys.exit(2 if stop else 0)
'''


class Repo:
    """An owned temporary repository with harness, current and candidate commits."""

    def __init__(self, root, cases=21):
        self.path = root / 'repo'
        self.path.mkdir()
        git(self.path, 'init', '-q', '-b', 'main')
        for name in ('integrations/lifecycle/memory.py', 'integrations/lifecycle/inbox_recall.py'):
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text('# synthetic hook\n')
        (self.path / 'cmd/cairn').mkdir(parents=True)
        (self.path / 'cmd/cairn/main.go').write_text('package main\n')
        harness = self.path / 'scripts/trial-task-eval.sh'
        harness.parent.mkdir()
        harness.write_text(FAKE_HARNESS)
        harness.chmod(0o755)
        write_synthetic_trial(self.path, cases)
        git(self.path, 'add', '-A')
        git(self.path, 'commit', '-q', '-m', 'harness')
        self.harness = git(self.path, 'rev-parse', 'HEAD')
        git(self.path, 'checkout', '-q', '-b', 'candidate')
        (self.path / 'integrations/lifecycle/memory.py').write_text('# synthetic candidate hook\n')
        git(self.path, 'commit', '-qam', 'candidate')
        self.candidate = git(self.path, 'rev-parse', 'HEAD')
        git(self.path, 'checkout', '-q', 'main')
        self.current = self.harness


class FakeExecutor(weekly.LegacyHarnessExecutor):
    """The real legacy executor with its concrete blockers lifted, run against the fake harness."""

    name = 'fake'

    def __init__(self, harness_mode='complete', limits=None, accounts=ACCOUNTS):
        self.limits = dict(paired.ORIGINAL_LIMITS) if limits is None else limits
        self.account_ids = list(accounts)
        self.harness_mode = harness_mode
        self.checked, self.executed = [], []

    def check(self, preview):
        self.checked.append(copy.deepcopy(preview))

    def execute(self, spec, run_dir):
        self.executed.append(copy.deepcopy(spec))
        with unittest.mock.patch.dict(os.environ, FAKE_HARNESS_MODE=self.harness_mode,
                                      OPENROUTER_API_KEY='must-not-pass', ANTHROPIC_API_KEY='must-not-pass'):
            return super().execute(spec, run_dir)


class WeeklyRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = Repo(self.root)
        evidence = self.root / 'evidence'
        evidence.mkdir()
        self.inputs = Inputs(evidence)
        receipt = adapt(self.inputs.document(), evidence / 'adapted', now=NOW)
        self.receipt_pin = write_pin(evidence / 'adapted' / 'receipt.json',
                                     json.loads((evidence / 'adapted' / 'receipt.json').read_text()))
        self.receipt_pin = dict(path=str(evidence / 'adapted' / 'receipt.json'),
                                sha256=hashlib.sha256((evidence / 'adapted' / 'receipt.json').read_bytes()).hexdigest())
        plan = self.inputs.fixture.plan
        self.plan_pin = write_pin(evidence / 'plan.json', plan)
        self.assertEqual(receipt['plan_sha256'], plan['sha256'])
        self.runs = self.root / 'runs'
        self.runs.mkdir(mode=0o700)
        worker = self.root / 'embedding-worker'
        worker.write_text('#!/bin/sh\n# synthetic worker\n')
        frozen = self.repo.path / 'trials/task-eval/FROZEN.json'
        self.profile = dict(
            schema=weekly.PROFILE_SCHEMA, repo=str(self.repo.path), current_ref='main', candidate_ref='candidate',
            harness_commit=self.repo.harness, runs_root=str(self.runs),
            campaign_id='3f1f0a52-1d0e-4b8e-9a51-0c107b7a1e07',
            schedule=dict(weekday=NOW_DT.weekday(), time='19:59', window_minutes=6),
            weekly_source=dict(label_version=1, frozen_sha256=hashlib.sha256(frozen.read_bytes()).hexdigest()),
            embedding=dict(worker=dict(path=str(worker), sha256=hashlib.sha256(worker.read_bytes()).hexdigest())),
            postgres=dict(bin_dir=str(self.root / 'pgbin')),
            capacity=dict(receipt=self.receipt_pin, plan=self.plan_pin,
                          models=dict.fromkeys(('CURRENT', 'CAND', 'primary', 'secondary'), 'fixture-model')),
            executor='fake', selection=None)
        self.executor = FakeExecutor()
        self.select()

    def select(self, **changes):
        frozen = self.repo.path / 'trials/task-eval/FROZEN.json'
        document = dict(schema=weekly.SELECTION_SCHEMA, campaign_id=self.profile['campaign_id'],
                        scheduled_at='2026-10-09T19:59:00Z', current=self.repo.current,
                        candidate=self.repo.candidate, harness_commit=self.repo.harness, executor='fake',
                        capacity_receipt_sha256=self.receipt_pin['sha256'],
                        selected_by='9a1f0a52-1d0e-4b8e-9a51-0c107b7a1e07')
        document.update(changes)
        self.profile['selection'] = write_pin(self.root / 'selection.json', document)

    def tearDown(self):
        self.tmp.cleanup()

    def run_weekly(self, executors=None, now=NOW_DT, profile=None):
        executors = {'fake': self.executor} if executors is None else executors
        return weekly.run(profile or self.profile, now=now, executors=executors, compiler=fake_compiler)

    def refuses(self, code, **kwargs):
        with self.assertRaisesRegex(paired.Refusal, f'^{code}$') as caught:
            self.run_weekly(**kwargs)
        self.assertEqual(os.listdir(self.runs), [], 'a refusal before effects writes nothing')
        return caught.exception

    def test_default_legacy_executor_refuses_with_concrete_blockers_before_effects(self):
        self.profile['executor'] = 'legacy-harness'
        refusal = self.refuses('weekly_executor_blocked', executors=weekly.default_executors())
        self.assertEqual(refusal.blockers, ['original_limits_unenforced', 'dedicated_account_unbound'])

    def test_legacy_blockers_are_computed_from_declared_limits_and_account_selector(self):
        self.assertEqual(weekly.legacy_blockers(dict(paired.ORIGINAL_LIMITS), 'selector'), [])
        self.assertEqual(weekly.legacy_blockers(dict(paired.ORIGINAL_LIMITS, hook_seconds=13), 'selector'),
                         ['original_limits_unenforced'])

    def test_no_selected_live_invocation_refuses_before_effects(self):
        self.profile['selection'] = None
        self.refuses('live_invocation_not_selected')
        self.assertEqual(self.executor.executed, [])

    def test_selection_must_name_this_exact_invocation(self):
        for field, value in (('scheduled_at', '2026-10-02T19:59:00Z'), ('candidate', self.repo.current),
                             ('harness_commit', 'f' * 40), ('executor', 'legacy-harness'),
                             ('capacity_receipt_sha256', '0' * 64), ('campaign_id', str(uuid.uuid4()))):
            with self.subTest(field=field):
                self.select(**{field: value})
                self.refuses('live_selection_mismatch')

    def test_executor_without_original_limits_refuses(self):
        self.executor.limits = dict(paired.ORIGINAL_LIMITS, pulls=5)
        self.refuses('executor_limits_unenforced')

    def test_capacity_gate_uses_only_verified_adapter_evidence(self):
        Path(self.inputs.allowances[0]['basis']['path']).write_text('{"changed": true}')
        self.refuses('pin_mismatch')

    def test_capacity_must_cover_the_executor_accounts_and_whole_slot(self):
        self.executor.account_ids = ['account-A']
        self.refuses('capacity_account_mismatch')
        self.executor.account_ids = ACCOUNTS
        self.profile['schedule']['window_minutes'] = 7
        self.refuses('capacity_window_short')

    def test_capacity_plan_must_be_the_original_plan(self):
        plan = copy.deepcopy(self.inputs.fixture.plan)
        plan['cases'] = plan['cases'][:20]
        plan['sha256'] = paired.canonical_sha({k: v for k, v in plan.items() if k != 'sha256'})
        self.profile['capacity']['plan'] = write_pin(self.root / 'other-plan.json', plan)
        self.refuses('capacity_plan_mismatch')

    def test_outside_the_weekly_window_refuses(self):
        self.refuses('outside_weekly_window', now=NOW_DT + dt.timedelta(minutes=10))
        self.refuses('outside_weekly_window', now=NOW_DT - dt.timedelta(minutes=2))

    def test_legacy_argv_and_environment_keep_the_original_route(self):
        spec = dict(harness=dict(root='/h'), seeds=[0, 1, 2], distractors=1900, actor_model='sonnet',
                    embedding=dict(worker=dict(path='/w')), postgres=dict(bin_dir='/pg'),
                    arms={name: dict(binary=dict(path=f'/{name}/cairn'), hook=dict(path=f'/{name}/memory.py'))
                          for name in ('CURRENT', 'CAND')})
        self.assertEqual(weekly.legacy_argv(spec, Path('/out')), [
            '/h/scripts/trial-task-eval.sh', 'agent', '--legacy-fixtures', '--output', '/out', '--arms',
            '--memory', 'CURRENT:/CURRENT/cairn:/CURRENT/memory.py', '--memory', 'CAND:/CAND/cairn:/CAND/memory.py',
            '--distractors', '1900', '--seeds', '3', '--first-seed', '0', '--embedding-worker', '/w',
            '--harness', 'claude', '--model', 'sonnet', '--max-turns', '40', '--timeout', '900', '--parallel', '3'])
        with self.assertRaisesRegex(paired.Refusal, '^legacy_seed_list$'):
            weekly.legacy_argv(dict(spec, seeds=[1, 2, 4]), Path('/out'))
        env = weekly.legacy_environment(spec, dict(PATH='/bin', OPENROUTER_API_KEY='x', ANTHROPIC_API_KEY='y',
                                                    CAIRN_DATABASE_URL='postgres://shared'))
        self.assertEqual(env, dict(PATH='/bin', CAIRN_PG_BIN='/pg', PYTHONDONTWRITEBYTECODE='1'))

    def test_complete_run_pins_both_arms_original_counts_and_report_readable_agent_document(self):
        receipt = self.run_weekly()
        self.assertEqual(receipt['status'], 'complete')
        self.assertFalse(receipt['runtime_admission'])
        self.assertEqual(receipt['capacity']['account_proof'], 'declared_unauthenticated')
        self.assertEqual((receipt['current']['commit'], receipt['candidate']['commit']),
                         (self.repo.current, self.repo.candidate))
        [spec] = self.executor.executed
        self.assertEqual((spec['cases'], spec['attempts'], spec['distractors']), (21, 126, 1900))
        self.assertEqual(spec['limits'], paired.ORIGINAL_LIMITS)
        self.assertEqual(spec['seeds'], self.inputs.fixture.plan['seeds'])
        self.assertEqual(sorted(spec['arms']), ['CAND', 'CURRENT'])
        self.assertEqual(spec['arms']['CURRENT']['commit'], self.repo.current)
        self.assertEqual(spec['arms']['CAND']['commit'], self.repo.candidate)
        self.assertNotEqual(spec['arms']['CURRENT']['hook']['sha256'], spec['arms']['CAND']['hook']['sha256'])
        self.assertEqual(spec['embedding'], self.profile['embedding'])
        # One embedding worker for both arms; the harness owns the disposable cluster.
        argv = json.loads((Path(receipt['agent']['path']).parent / 'argv.json').read_text())
        self.assertEqual(argv['argv'].count('--embedding-worker'), 1)
        self.assertEqual(argv['pg_bin'], self.profile['postgres']['bin_dir'])
        self.assertEqual(argv['leaked'], [])
        self.assertEqual(receipt['harness_exit_code'], 0)
        self.assertEqual(receipt['selection']['proof'], 'declared_unauthenticated')
        agent = paired.read_json_pin(receipt['agent'])
        self.assertEqual(agent['schema'], 'cairn.task-eval.agent/1')
        self.assertEqual(receipt['attempts'], dict(planned=126, admitted=126, not_started=0, records=126))
        stored = json.loads((self.runs / receipt['slot_id'] / 'receipt.json').read_text())
        self.assertEqual(stored, receipt)


    def test_repeated_slot_returns_stored_receipt_without_rebuilding_or_executing(self):
        first = self.run_weekly()
        later = NOW_DT + dt.timedelta(minutes=3)
        self.assertEqual(self.run_weekly(now=later), dict(first, replayed=True))
        self.assertEqual(len(self.executor.executed), 1)

    def test_slot_used_by_another_pair_refuses(self):
        self.run_weekly()
        git(self.repo.path, 'checkout', '-q', 'candidate')
        (self.repo.path / 'integrations/lifecycle/memory.py').write_text('# second candidate\n')
        git(self.repo.path, 'commit', '-qam', 'second candidate')
        git(self.repo.path, 'checkout', '-q', 'main')
        with self.assertRaisesRegex(paired.Refusal, '^weekly_slot_other_pair$'):
            self.run_weekly()
        self.assertEqual(len(self.executor.executed), 1)

    def test_unfinished_claim_refuses_as_in_progress(self):
        receipt = self.run_weekly()
        (self.runs / receipt['slot_id'] / 'receipt.json').unlink()
        with self.assertRaisesRegex(paired.Refusal, '^weekly_run_in_progress$'):
            self.run_weekly()

    def test_preparation_failure_is_recorded_and_retried_in_the_same_slot(self):
        self.profile['weekly_source']['frozen_sha256'] = '0' * 64
        failed = self.run_weekly()
        self.assertEqual((failed['status'], failed['code']), ('failed_before_execution', 'harness_frozen_mismatch'))
        self.assertEqual(self.executor.executed, [])
        frozen = self.repo.path / 'trials/task-eval/FROZEN.json'
        self.profile['weekly_source']['frozen_sha256'] = hashlib.sha256(frozen.read_bytes()).hexdigest()
        self.assertEqual(self.run_weekly()['status'], 'complete')
        self.assertEqual(os.listdir(self.runs / 'failed'), [failed['slot_id'] + '.1'])

    def test_started_execution_is_never_repeated_in_the_slot(self):
        self.executor.harness_mode = 'cap'
        stopped = self.run_weekly()
        self.assertEqual((stopped['status'], stopped['code']), ('incomplete', 'provider_cap_stop'))
        self.assertEqual(stopped['attempts']['not_started'], 126 - 40)
        self.assertEqual(self.run_weekly(), dict(stopped, replayed=True))
        self.assertEqual(len(self.executor.executed), 1)


    def test_harness_whose_latest_revision_is_not_the_pinned_version_fails_before_execution(self):
        git(self.repo.path, 'checkout', '-q', '-b', 'harness-v2', self.repo.harness)
        (self.repo.path / 'trials/task-eval/revisions').mkdir()
        (self.repo.path / 'trials/task-eval/revisions/v2.json').write_text('{"cases": {}}')
        git(self.repo.path, 'add', '-A')
        git(self.repo.path, 'commit', '-qm', 'later label revision')
        self.profile['harness_commit'] = git(self.repo.path, 'rev-parse', 'HEAD')
        git(self.repo.path, 'checkout', '-q', 'main')
        self.select(harness_commit=self.profile['harness_commit'])
        failed = self.run_weekly()
        self.assertEqual((failed['status'], failed['code']), ('failed_before_execution', 'harness_label_version'))
        self.assertEqual(self.executor.executed, [])

    def test_recorded_execution_start_is_never_retried_even_if_marked_failed(self):
        receipt = self.run_weekly()
        slot = self.runs / receipt['slot_id']
        (slot / 'receipt.json').unlink()
        paired.write_selected(slot / 'receipt.json', dict(receipt, status='failed_before_execution'))
        self.assertEqual(self.run_weekly()['replayed'], True)
        self.assertEqual(len(self.executor.executed), 1)


    def test_cli_reports_the_fail_closed_refusal_without_paths(self):
        self.profile['executor'] = 'legacy-harness'
        profile = self.root / 'profile.json'
        profile.write_text(json.dumps(self.profile))
        import contextlib, io
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = weekly.main(['--profile', str(profile), '--now', NOW])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual((result['code'], result['blockers']),
                         ('weekly_executor_blocked', ['original_limits_unenforced', 'dedicated_account_unbound']))
        self.assertFalse(result['runtime_admission'])
        self.assertNotIn(str(self.root), output.getvalue())
        self.assertEqual(os.listdir(self.runs), [])


    def rejected(self, mode, code):
        self.executor.harness_mode = mode
        receipt = self.run_weekly()
        self.assertEqual((receipt['status'], receipt['code']), ('rejected', code))
        self.assertIsNone(receipt['pair_binding'])
        return receipt

    def test_root_reproduction_126_duplicates_of_one_row_is_never_complete(self):
        self.rejected('duplicate', 'agent_rows_not_planned')

    def test_missing_row_replaced_by_unplanned_row_is_rejected(self):
        self.rejected('replace', 'agent_rows_not_planned')

    def test_extra_row_is_rejected(self):
        self.rejected('extra', 'agent_rows_not_planned')

    def test_wrong_seeds_are_rejected(self):
        self.rejected('wrong_seed', 'agent_rows_not_planned')

    def test_actor_model_mismatch_is_rejected(self):
        self.rejected('model', 'agent_document_not_original')

    def test_row_level_failures_are_never_complete(self):
        for mode in ('row_provider', 'row_error', 'measurement'):
            with self.subTest(mode=mode):
                self.tearDown()
                self.setUp()
                self.executor.harness_mode = mode
                receipt = self.run_weekly()
                self.assertEqual((receipt['status'], receipt['code']), ('incomplete', 'attempt_failures'))

    def test_missing_harness_content_hashes_are_never_complete(self):
        self.executor.harness_mode = 'nohash'
        receipt = self.run_weekly()
        self.assertEqual((receipt['status'], receipt['code']), ('incomplete', 'original_attempt_provenance_unavailable'))


    def test_refused_retry_of_a_failed_slot_leaves_it_unchanged_and_retryable(self):
        self.profile['weekly_source']['frozen_sha256'] = '0' * 64
        failed = self.run_weekly()
        self.assertEqual(failed['status'], 'failed_before_execution')
        slot = self.runs / failed['slot_id']
        before = {p.relative_to(slot): p.read_bytes() for p in slot.rglob('*') if p.is_file()}
        self.profile['selection'] = None
        with self.assertRaisesRegex(paired.Refusal, '^live_invocation_not_selected$'):
            self.run_weekly()
        self.assertTrue(slot.is_dir())
        self.assertEqual({p.relative_to(slot): p.read_bytes() for p in slot.rglob('*') if p.is_file()}, before)
        self.assertFalse((self.runs / 'failed').exists())
        self.assertEqual(self.executor.executed, [])
        self.assertEqual(json.loads((slot / 'receipt.json').read_text())['code'], 'harness_frozen_mismatch')


    def test_complete_receipt_issues_the_pair_binding_that_the_converter_accepts(self):
        import trial_blind_packet_draft as packet
        import trial_legacy_blind_selection as legacy
        from test_trial_legacy_blind_selection import C, pin
        receipt = self.run_weekly()
        self.assertEqual(receipt['pair_identity'], 'declared_binding')
        binding = paired.read_json_pin({k: receipt['pair_binding'][k] for k in ('path', 'sha256')})
        self.assertEqual(binding['request_id'], receipt['slot_id'])
        self.assertEqual(binding['agent_sha256'], receipt['agent']['sha256'])
        self.assertEqual(binding['arm_roles'], dict(CURRENT='CURRENT', CAND='CAND'))
        spec = json.loads((self.runs / receipt['slot_id'] / 'spec.json').read_text())
        self.assertEqual(binding['schedule_sha256'], paired.canonical_sha(spec['planned']))
        self.assertEqual(len(spec['planned']), 126)
        agent_path = Path(receipt['agent']['path'])
        materials = self.root / 'materials'
        materials.mkdir()
        cases = []
        for n in range(21):
            case = f'synthetic-{n}'
            contract = dict(schema='cairn.blind-case-contract/2', case_key=case, case_class='synthetic',
                            required_channels=['task_statement', 'grading_instructions', 'final_answer'],
                            artifact_inventory=[])
            cases.append(dict(case=case, contract=pin(materials / f'contract-{case}.json', C(contract)),
                              task_statement=pin(materials / f'task-{case}.txt', f'Synthetic task for {case}.\n'.encode()),
                              grading_instructions=pin(materials / f'grading-{case}.txt', b'Synthetic rubric.\n')))
        policy = dict(schema='cairn.blind-policy/2', **{
            name: dict(version='synthetic-v1', pin=pin(materials / (name + '.bin'), ('PRIVATE ' + name).encode()))
            for name in packet.POLICIES})
        review = dict(schema='cairn.blind-review-measurement/1', unit='bytes', semantics='comparison_plus_control',
                      graders=[dict(slot=i, limit=100000, control=pin(materials / f'control-{i}', b'SYNTHETIC CONTROL'))
                               for i in (1, 2)])
        agent_bytes = agent_path.read_bytes()
        plan_bytes = (agent_path.parent / 'plan.json').read_bytes()
        request = dict(schema='cairn.legacy-blind-conversion/1',
                       agent=dict(path=str(agent_path), bytes=len(agent_bytes), sha256=hashlib.sha256(agent_bytes).hexdigest()),
                       plan=dict(path=str(agent_path.parent / 'plan.json'), bytes=len(plan_bytes),
                                 sha256=hashlib.sha256(plan_bytes).hexdigest()),
                       binding=receipt['pair_binding'], namespace='namespace-A', policy=policy,
                       policy_ref=dict(schema='cairn.blind-policy-ref/1', id='public-policy-A'),
                       review_measurement=review, cases=cases)
        converted = legacy.convert(pin(self.root / 'request.json', C(request)), self.root / 'converted')
        self.assertEqual(converted['pairs'], dict(total=63, changed=15, unchanged=48, indeterminate=0))
        self.assertEqual(converted['sources']['binding'], receipt['pair_binding'])
        self.assertIs(converted['runtime_admitted'], False)


    def test_one_extra_duplicate_of_a_planned_row_is_rejected(self):
        self.rejected('dup_one', 'agent_rows_not_planned')

    def test_missing_planned_row_with_complete_counters_is_rejected(self):
        self.rejected('missing', 'agent_rows_not_planned')

    def test_unstarted_row_with_unplanned_identity_is_rejected(self):
        self.executor.harness_mode = 'bad_unstarted_cap'
        receipt = self.run_weekly()
        self.assertEqual((receipt['status'], receipt['code']), ('rejected', 'agent_rows_not_planned'))

    def test_every_attempt_row_must_carry_the_selected_model_harness_and_wording(self):
        receipt = self.run_weekly()
        self.assertEqual(receipt['status'], 'complete')
        path = Path(receipt['agent']['path'])
        spec = json.loads((self.runs / receipt['slot_id'] / 'spec.json').read_text())
        original = json.loads(path.read_text())
        for field, value in (('model', 'unselected-synthetic-model'), ('model', None), ('harness', 'codex'),
                             ('harness', None), ('wording', 'paraphrase'), ('wording', None)):
            with self.subTest(field=field, value=value):
                document = copy.deepcopy(original)
                if value is None:
                    document['records'][0].pop(field)
                else:
                    document['records'][0][field] = value
                path.write_text(json.dumps(document))
                result = weekly._agent_result(path, spec)
                self.assertEqual((result['status'], result['code']), ('rejected', 'agent_row_not_selected'))


if __name__ == '__main__':
    unittest.main()
