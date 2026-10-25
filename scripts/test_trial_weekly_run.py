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
import trial_original_execution as original
import trial_one_run_override as one_run_override
import trial_weekly_run as weekly
import trial_weekly_accounts as weekly_accounts
from trial_capacity_adapter import adapt
from test_trial_capacity_adapter import Inputs
from test_trial_paired_capacity import write_pin
from test_trial_provider_mapping import END, NOW

NOW_DT = paired.utc(NOW)
ACCOUNTS = ['account-A', 'account-B']


def embedding_fixture(root):
    """A closed trial_embedding_launch config and its rendered launcher (no model, no worker run)."""
    semantic = root / 'semantic'
    semantic.mkdir()
    for name in ('embedding_worker.py', 'semantic_rank.py'):
        shutil.copyfile(Path(__file__).with_name(name), semantic / name)
    python = semantic / 'python'
    python.write_text('#!/bin/sh\nexit 99\n')
    python.chmod(0o700)
    (semantic / 'model').mkdir()
    config = write_pin(semantic / 'embedding-config.json', dict(
        schema='cairn.weekly-embedding-launch/1', python=str(python),
        worker=dict(path=str(semantic / 'embedding_worker.py'),
                    sha256=hashlib.sha256((semantic / 'embedding_worker.py').read_bytes()).hexdigest()),
        scorer=dict(path=str(semantic / 'semantic_rank.py'),
                    sha256=hashlib.sha256((semantic / 'semantic_rank.py').read_bytes()).hexdigest()),
        model_dir=str(semantic / 'model')))
    worker = semantic / 'worker-embeddings'
    worker.write_text(f'#!/bin/sh\nexec {python} {semantic}/embedding_worker.py --model-dir {semantic}/model\n')
    worker.chmod(0o700)
    return config, worker


def _write(path, content):
    path.write_text(content)
    return path


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
        baseline_commit='0' * 40, cases=[dict(id=f'synthetic-{n}', cwd='project', wordings=dict(task=f'Synthetic task for synthetic-{n}.\n'))
                                         for n in range(count)])))
    (root / 'corpus.json').write_text(json.dumps(dict(notes=[])))
    manifest = te.label_manifest(1, root=root)
    (root / 'FROZEN.json').write_text(json.dumps(manifest))
    return root


FAKE_HARNESS = r'''#!/usr/bin/env python3
# Fake trial-task-eval.sh: records argv and writes synthetic legacy output (agent/1, plan, runs).
import hashlib, json, os, sys
from pathlib import Path
argv = sys.argv[1:]
if '--original-limits' not in argv:
    sys.exit(3)
value = lambda flag: argv[argv.index(flag) + 1]
out = Path(value('--output'))
out.mkdir(mode=0o700)
(out / 'argv.json').write_text(json.dumps(dict(argv=argv, pg_bin=os.environ.get('CAIRN_PG_BIN'),
    config_home=os.environ.get('CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR'),
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
    return (json.dumps(dict(type='assistant', message=dict(content=[dict(type='text', text=answer)]))) + '\n'
            + json.dumps(dict(type='result', result=answer, is_error=False, num_turns=1)) + '\n')

def hook_rows(seconds=0.2):
    return [dict(event=event, exit_code=0, stdout_bytes=50, stderr_bytes=0, process_seconds=seconds,
                 memory_call_attempts={'search': 1, 'pull': 0, 'pull-evidence': 0, 'history': 0, 'other': 0})
            for event in ('SessionStart', 'UserPromptSubmit')]

native_path, native_sha, effort = value('--original-native-path'), value('--original-native-sha256'), value('--reasoning-effort')
native_target = '/home/synthetic/.local/bin/claude'
identity = json.loads(os.environ['FAKE_IDENTITY'])
controls_path, controls_sha = value('--original-arms'), value('--original-arms-sha256')
controls = json.loads(Path(controls_path).read_text())
observed_arms = {label: dict(recall_mode=arm['recall_mode'], semantic_fallback=arm['semantic_fallback'],
                             recall_revision=arm['recall_revision'], hook_sha256=arm['hook']['sha256'],
                             bridge_sha256=arm['bridge']['sha256']) for label, arm in controls['arms'].items()}

def record(c, a, s, answer=None, prefix=''):
    answer = answer or f'Synthetic answer {c} {a} {s}.'
    outcome = 'mistake' if a == 'CAND' and c.endswith(('0', '5')) else 'correct'
    return dict(run_id=f'{c}.{a}.s{s}', case=c, arm=a, seed=s, order=0, harness='claude', model=model,
                original_arm=dict(observed_arms[a], recall_mode='ambient') if mode == 'oe_arm' and (c, a, s) == plan[3]
                else dict(observed_arms.get(a, {})),
                original_native=dict(binary_sha256=native_sha, effort='low' if mode == 'oe_effort' and (c, a, s) == plan[3] else effort,
                                     target=native_target),
                wording='task', prompt_bytes=len(task(c).encode()), outcome=outcome, answer_chars=len(answer),
                stream_sha256=hashlib.sha256((prefix + stream(answer)).encode()).hexdigest(),
                prompt_sha256=hashlib.sha256(task(c).encode()).hexdigest()), prefix + stream(answer)

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
elif mode == 'limit_answer':
    rows[2] = record(*plan[2], answer='x' * 8193)
elif mode in ('limit_unobserved', 'limit_late'):
    use = json.dumps(dict(type='assistant', message=dict(content=[dict(type='tool_use', id='toolu_1', name='mcp__cairn__cairn_search', input={})]))) + '\n'
    result = json.dumps(dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='toolu_1', content='r')]))) + '\n'
    rows[2] = record(*plan[2], prefix=use + result)
elif mode == 'prompt':
    rows[2][0]['prompt_sha256'] = '0' * 64
elif mode == 'row_error':
    rows[5][0]['outcome'] = 'harness_error'
stop = None
if capped:
    stop = dict(run_id=rows[-1][0]['run_id'], provider='claude', reason='capacity_exhausted', api_error_status=429)
    rows[-1][0]['trace'] = dict(admission_failure=dict(provider='claude', reason='capacity_exhausted', api_error_status=429))
# Synthetic pre-actor baselines in trial_attempt_custody's retained format (fixture only).
baselines = out / 'baselines'
baselines.mkdir(mode=0o700, exist_ok=True)

def baseline_path(run_id):
    return baselines / (hashlib.sha256(run_id.encode()).hexdigest() + '.json')

def retain_baseline(row, **identity):
    document = dict(schema='cairn.attempt-baseline/1', base_commit='1' * 40, snapshot={}, repository={},
                    identity=dict(dict(run_id=row['run_id'], case=row['case'], arm=row['arm'], seed=row['seed'],
                                       cwd='project'), **identity))
    raw = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
    path = baseline_path(row['run_id'])
    path.unlink(missing_ok=True)
    path.write_bytes(raw)
    path.chmod(0o600)
    row['baseline_custody'] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())

for row, _ in rows:
    retain_baseline(row)
target, other = rows[7][0], rows[8][0]
if mode == 'baseline_missing':
    target.pop('baseline_custody')
elif mode == 'baseline_absent':
    baseline_path(target['run_id']).unlink()
elif mode == 'baseline_tampered':
    path = baseline_path(target['run_id'])
    path.write_bytes(path.read_bytes().replace(b'1' * 40, b'2' * 40))
elif mode == 'baseline_swapped':
    baseline_path(target['run_id']).write_bytes(baseline_path(other['run_id']).read_bytes())
    target['baseline_custody'] = dict(other['baseline_custody'])
elif mode == 'baseline_identity':
    retain_baseline(target, seed=target['seed'] + 1)
elif mode == 'baseline_policy':
    baseline_path(target['run_id']).chmod(0o644)
elif mode.startswith('baseline_raw:'):
    # A malformed retained document whose pin is updated to match its bytes.
    raw = dict(null=b'null', number=b'5', string=b'"baseline"', bool=b'true', list=b'[{"schema": 1}]',
               keys=b'{"schema": "cairn.attempt-baseline/1"}',
               types=b'{"schema": "cairn.attempt-baseline/1", "identity": [], "snapshot": [], "base_commit": 1, "repository": null}',
               invalid=b'{broken', deep=b'[' * 100000 + b']' * 100000)[mode.split(':', 1)[1]]
    path = baseline_path(target['run_id'])
    path.write_bytes(raw)
    target['baseline_custody'] = dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())

for row, text in rows:
    base = out / 'runs' / row['run_id']
    base.mkdir(parents=True, exist_ok=True)
    (base / 'result.json').write_text(json.dumps(row, indent=2))
    (base / 'stream.jsonl').write_text(text)
    (base / 'hook-state').mkdir(exist_ok=True)
    slow = mode == 'limit_hook' and row['run_id'] == rows[4][0]['run_id']
    (base / 'hook-state/observations.jsonl').write_text(''.join(json.dumps(h) + '\n' for h in hook_rows(6 if slow else 0.2)))
    # trial_native_capture.capture timing rows, one per LF-terminated stream line.
    lines = [part + '\n' for part in text.split('\n')[:-1]]
    late = mode == 'limit_late' and row['run_id'] == rows[2][0]['run_id']
    timing = [dict(line=i + 1, received_seconds=31.0 if late else 0.5 + i, bytes=len(line.encode()),
                   sha256=hashlib.sha256(line.encode()).hexdigest()) for i, line in enumerate(lines)]
    if not (mode == 'limit_unobserved' and row['run_id'] == rows[2][0]['run_id']):
        (base / 'stream-timing.json').write_text(json.dumps(timing))
identities = {arm: dict(identity) for arm in arms}
if mode == 'oe_identity_mismatch':
    identities = {arm: dict(identity, model_sha256='f' * 64) for arm in arms}
elif mode == 'oe_identity_arms':
    identities['CAND'] = dict(identity, algorithm='other-embedding/1')
elif mode == 'oe_identity_missing':
    identities['CURRENT'] = dict(model_sha256=None, algorithm=None)
if mode == 'oe_row_missing':
    rows[5][0].pop('original_native')
native = dict(declared=dict(path=native_path, sha256=native_sha),
              observed=dict(source=str(out / 'native/claude'), sha256='0' * 64 if mode == 'oe_plan_sha' else native_sha,
                            version='fake-claude 9.9.9', observed_at='2026-10-09T19:59:30Z', target=native_target))
plan_document = dict(arms=arms, wording='task', runs=[[c, a, s, 0] for c, a, s in plan], model=model,
                     reasoning_effort=effort, original_native=native,
                     original_arms=dict(controls=dict(path=controls_path, sha256=controls_sha), arms=observed_arms),
                     memory_backends={arm: dict(backend='embedding-command',
                                                readiness=dict(state='full_eligible_coverage', identity=identities[arm]))
                                      for arm in arms})
if mode == 'oe_unobserved':
    plan_document.pop('original_native')
if mode == 'oe_arms_missing':
    plan_document.pop('original_arms')
if mode == 'oe_plan_arms':  # the plan's loaded controls differ while every row still matches
    plan_document['original_arms']['arms']['CAND'] = dict(observed_arms['CAND'], semantic_fallback=False)
if mode == 'oe_backend_string':
    plan_document['memory_backends']['CURRENT'] = 'malformed backend'
(out / 'plan.json').write_text(json.dumps(plan_document, indent=2))
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
        # The account selector the runner requires in the exported harness.
        shutil.copy(Path(weekly.selection.__file__), self.path / 'scripts/trial_account_selection.py')
        shutil.copy(Path(weekly.limits.__file__), self.path / 'scripts/trial_legacy_limits.py')
        (self.path / 'scripts/trial_task_eval.py').write_text(
            '# synthetic\n        selected = credentials_source()\n    a.add_argument("--original-limits")\n')
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
    """The real legacy executor with the original configuration declared, run against the fake harness.

    The fixture's capacity evidence routes CAND to a second account, so this
    double declares both synthetic account ids for the one selected config home.
    """

    name = 'fake'
    capabilities = frozenset(original.FIELDS)  # the double carries every declared field

    def __init__(self, account, harness_mode='complete', accounts=ACCOUNTS):
        super().__init__(account)
        self.limits = dict(paired.ORIGINAL_LIMITS)
        self.account_ids = list(accounts)
        self.harness_mode = harness_mode
        self.discovery = None
        self.checked, self.executed = [], []

    def check(self, preview):
        self.checked.append(copy.deepcopy(preview))
        super().check(preview)

    def execute(self, spec, run_dir):
        self.executed.append(copy.deepcopy(spec))
        declared = spec['original_execution']['identity']
        identity = self.discovery or {k: declared[k] for k in ('model_sha256', 'algorithm')}  # discovery has no dimensions
        with unittest.mock.patch.dict(os.environ, FAKE_HARNESS_MODE=self.harness_mode, FAKE_IDENTITY=json.dumps(identity),
                                      CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR='/ambient/must-not-pass',
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
        writer_cgroup = self.root / 'writer-cgroup'
        writer_cgroup.mkdir(mode=0o700)
        writer_controls = dict(schema='cairn.writer-controls/1',
            controls=dict(memory_max=512*1024*1024, memory_swap_max=0, pids_max=32,
                          cpu_max='100000 100000'),
            reviewed_identity=dict(schema='cairn.writer-reviewed-identity/2',
                kernel=dict(release='synthetic-kernel', version='synthetic-build'),
                bwrap=dict(path='/usr/bin/bwrap', sha256='a'*64),
                source_scope=dict(test='synthetic'), execution='sealed_memfd'))
        writer_controls_pin = write_pin(self.root/'writer-controls.json', writer_controls)
        worker = self.embedding_fixture()
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
            executor='fake', selection=None, account=None, execution=None,
            writer=dict(cgroup=str(writer_cgroup), controls=writer_controls_pin))
        self.profile['execution'] = self.execution_pin()
        config_home = self.root / 'accounts/weekly'
        config_home.mkdir(parents=True)
        (config_home / '.credentials.json').write_text('{"synthetic": "never read"}')
        self.profile['account'] = dict(account_id='account-A', config_home=str(config_home))
        self.executor = FakeExecutor(self.profile['account'])
        self.select()

    def embedding_fixture(self):
        self.embedding_config, worker = embedding_fixture(self.root)
        return worker

    def recall_blob(self, commit, name):
        content = git(self.repo.path, 'cat-file', 'blob', f'{commit}:integrations/lifecycle/{name}') + '\n'
        return dict(path=str(_write(self.root / 'declared' / f'{commit[:8]}-{name}', content)),
                    sha256=hashlib.sha256(content.encode()).hexdigest())

    def execution_document(self):
        return json.loads(Path(self.profile['execution']['path']).read_text())

    def execution_pin(self, **changes):
        """A synthetic cairn.weekly-execution/1 declaration for this run (trial_weekly_input._execution)."""
        declared = self.root / 'declared'
        declared.mkdir(exist_ok=True)

        def pinned(name, content):
            return write_pin(declared / name, content) if not isinstance(content, str) else \
                dict(path=str(_write(declared / name, content)), sha256=hashlib.sha256(content.encode()).hexdigest())
        identity = changes.pop('identity', None) or pinned('model-identity.json', dict(
            schema='cairn.embedding-model-identity/1', model_sha256='a' * 64, algorithm='synthetic-embedding/1',
            dimensions=384))
        controls = changes.pop('controls', dict(recall_mode='agent_tools', semantic_fallback=True))
        arm = lambda name, commit: dict(binary=pinned(name + '-cairn', '#!/bin/sh\n'),
                                        hook=self.recall_blob(commit, 'memory.py'),
                                        bridge=self.recall_blob(commit, 'inbox_recall.py'), api_revision=commit,
                                        recall_revision=commit, **controls,
                                        embedding_worker=self.profile['embedding']['worker'],
                                        embedding_config=self.embedding_config,
                                        embedding_model_identity=identity)
        generator = Path(te.__file__).resolve()
        document = dict(schema='cairn.weekly-execution/1',
                        weekly_source=dict(root=str(self.repo.path / 'trials/task-eval'), **self.profile['weekly_source']),
                        actor_api_base=self.repo.current,
                        native=dict(binary=pinned('claude', '#!/bin/sh\n# synthetic native\n'), model='fixture-model',
                                    effort='xhigh'),
                        arms=['CURRENT', 'CAND'],
                        memory_arms=dict(CURRENT=arm('current', self.repo.current), CAND=arm('cand', self.repo.candidate)),
                        seeds=[1, 2, 3], wording='task', workload=pinned('workload.json', '{}'),
                        distractors=dict(count=1900, seed=20260928,
                                         generator_source=dict(path=str(generator),
                                                               sha256=hashlib.sha256(generator.read_bytes()).hexdigest()),
                                         generated_sha256='0' * 64))
        for key, value in changes.items():
            document[key] = value
        return write_pin(declared / 'execution.json', document)

    def select(self, **changes):
        frozen = self.repo.path / 'trials/task-eval/FROZEN.json'
        document = dict(schema=weekly.SELECTION_SCHEMA, campaign_id=self.profile['campaign_id'],
                        scheduled_at='2026-10-09T19:59:00Z', current=self.repo.current,
                        candidate=self.repo.candidate, harness_commit=self.repo.harness, executor='fake',
                        capacity_receipt_sha256=self.receipt_pin['sha256'],
                        writer_sha256=weekly.paired.canonical_sha(self.profile['writer']),
                        selected_by='9a1f0a52-1d0e-4b8e-9a51-0c107b7a1e07')
        document.update(changes)
        self.profile['selection'] = write_pin(self.root / 'selection.json', document)

    def tearDown(self):
        self.tmp.cleanup()

    def run_weekly(self, executors=None, now=NOW_DT, profile=None):
        executors = {'fake': self.executor} if executors is None else executors
        return weekly.run(profile or self.profile, now=now, executors=executors, compiler=fake_compiler)

    def manual_codex_profile(self):
        """Build a synthetic three-account Codex profile for the explicit one-slot path."""
        profile = copy.deepcopy(self.profile)
        profile['executor'] = weekly.CodexWeeklyExecutor.name
        profile['account'] = None
        profile['capacity']['models'] = dict.fromkeys(('CURRENT', 'CAND', 'primary', 'secondary'), 'gpt-6-luna')
        account_rows = []
        for index in range(3):
            home = self.root / f'codex-account-{index}'
            home.mkdir()
            auth_path = home / 'auth.json'
            auth_path.write_text(json.dumps(dict(auth_mode='chatgpt', tokens=dict(account_id=f'account-{index}'))))
            account_rows.append(dict(account_id=f'account-{index}', auth_file=str(auth_path),
                                     auth_sha256=hashlib.sha256(auth_path.read_bytes()).hexdigest(),
                                     config_home=str(home)))
        cases = [f'synthetic-{index:02d}' for index in range(21)]
        assignment = weekly_accounts.balanced(cases, [1, 2, 3], account_rows)
        assignment_ref = write_pin(self.root / 'manual-assignment.json', assignment)
        plan = dict(attempts=126, arms=['CURRENT', 'CAND'], cases=cases, seeds=[1, 2, 3],
                    account_assignment=assignment_ref)
        plan['sha256'] = paired.canonical_sha(plan)
        plan_ref = write_pin(self.root / 'manual-plan.json', plan)
        profile['capacity'].update(plan=plan_ref, receipt=self.receipt_pin)

        execution = self.execution_document()
        execution['schema'] = 'cairn.weekly-execution/2'
        actor = execution.pop('native')
        execution['provider'] = dict(harness='codex', binary=actor['binary'], model='gpt-6-luna', effort='low')
        execution_ref = write_pin(self.root / 'declared/codex-execution.json', execution)
        profile['execution'] = execution_ref
        start, end = weekly.current_slot(profile['schedule'], NOW_DT)
        slot_id = str(uuid.uuid5(weekly.NAMESPACE, profile['campaign_id'] + '|' + weekly.candidate._iso(start)))
        override_doc = dict(
            schema=one_run_override.SCHEMA, campaign_id=profile['campaign_id'], slot_id=slot_id,
            scheduled_at=weekly.candidate._iso(start), expires_at=weekly.candidate._iso(end),
            current=self.repo.current, candidate=self.repo.candidate, harness_commit=self.repo.harness,
            executor=profile['executor'], plan_sha256=plan['sha256'], plan_file_sha256=plan_ref['sha256'],
            account_assignment_sha256=assignment_ref['sha256'], account_ids=sorted(row['account_id'] for row in account_rows),
            owner_instruction=dict(schema=one_run_override.INSTRUCTION_SCHEMA, source='synthetic-test',
                                   reference='fixture-only', statement_sha256='a'*64,
                                   observed_at=NOW, declared_by='owner'),
            uncertainty=one_run_override._unknown_capacity())
        override_ref = write_pin(self.root / 'one-run-override.json', override_doc)
        provider = execution['provider']
        selection_doc = dict(schema=weekly.ONE_RUN_SELECTION_SCHEMA, campaign_id=profile['campaign_id'],
                             scheduled_at=weekly.candidate._iso(start), current=self.repo.current,
                             slot_id=slot_id, expires_at=weekly.candidate._iso(end),
                             candidate=self.repo.candidate, harness_commit=self.repo.harness,
                             executor=profile['executor'], one_run_override_sha256=override_ref['sha256'],
                             writer_sha256=paired.canonical_sha(profile['writer']),
                             account_assignment_sha256=assignment_ref['sha256'],
                             account_ids=sorted(row['account_id'] for row in account_rows),
                             provider_model=provider['model'],
                             provider_binary_sha256=provider['binary']['sha256'],
                             selected_by='9a1f0a52-1d0e-4b8e-9a51-0c107b7a1e07')
        profile['selection'] = write_pin(self.root / 'one-run-selection.json', selection_doc)
        return profile, override_ref

    def test_codex_prelaunch_exception_is_incomplete_after_assignment_and_row_selection_checks(self):
        cases = [f'synthetic-{index:02d}' for index in range(21)]
        seeds = [1, 2, 3]
        account_ids = ['account-A', 'account-B', 'account-C']
        accounts = [dict(account_id=account_id, auth_sha256=chr(ord('a') + index) * 64)
                    for index, account_id in enumerate(account_ids)]
        planned = []
        for seed_index, seed in enumerate(seeds):
            for case_index, case in enumerate(cases):
                account_id = account_ids[(case_index + seed_index) % 3]
                for arm in ('CURRENT', 'CAND'):
                    planned.append(dict(run_id=f'{case}.{arm}.s{seed}', case=case, arm=arm, seed=seed,
                                        account_id=account_id))
        assignment = dict(accounts=accounts)
        assignment_path = self.root / 'assignment.json'
        assignment_path.write_text(json.dumps(assignment, sort_keys=True))
        assignment_ref = dict(path=str(assignment_path), sha256=hashlib.sha256(assignment_path.read_bytes()).hexdigest())
        first = planned[0]
        records = [dict(run_id=first['run_id'], case=first['case'], arm=first['arm'], seed=first['seed'],
                        outcome='harness_error', harness='codex', model='gpt-6-luna', wording='task',
                        account_id=first['account_id'],
                        account_auth_sha256=next(row['auth_sha256'] for row in accounts
                                                 if row['account_id'] == first['account_id']),
                        observer_integrity_unknown=True)]
        started = {first['run_id']}
        unstarted = [dict(run_id=row['run_id'], case=row['case'], arm=row['arm'], seed=row['seed'])
                     for row in planned[1:]]
        plan = dict(arms=['CURRENT', 'CAND'],
                    runs=[[row['case'], row['arm'], row['seed'], index] for index, row in enumerate(planned)],
                    account_assignment_sha256=assignment_ref['sha256'],
                    run_accounts=[dict(run_id=row['run_id'], account_id=row['account_id']) for row in planned])
        out = self.root / 'agent-output'
        out.mkdir()
        agent_path = out / 'agent.json'
        document = dict(schema='cairn.task-eval.agent/1',
                        frozen=dict(label_version=12, labels_sha256='f' * 64),
                        distractors=1900, arms=['CURRENT', 'CAND'], model='gpt-6-luna', wording='task',
                        records=records,
                        admission=dict(state='stopped_observer_failure', planned=126, admitted=1,
                                       stop=None, observer_stop=dict(run_id=first['run_id'],
                                           reason='codex_attempt_exception'), not_started=unstarted),
                        measurement_failures=[])
        agent_path.write_text(json.dumps(document))
        (out / 'plan.json').write_text(json.dumps(plan))
        spec = dict(actor_model='gpt-6-luna',
                    harness=dict(kind='codex-weekly', label_version=12, labels_sha256='f' * 64),
                    planned=planned, account_assignment=assignment_ref, case_materials=None)
        result = weekly._agent_result(agent_path, spec)
        self.assertEqual((result['status'], result['code']), ('incomplete', 'attempts_unfinished'))
        self.assertEqual(started, {records[0]['run_id']})

    def test_manual_one_run_bypasses_only_capacity_proof_and_records_unknown(self):
        profile, override_ref = self.manual_codex_profile()
        profile['capacity']['receipt'] = None  # No fabricated reservation or capacity evidence.
        def finish(profile, slot, claim, plan, executor, repo, compiler):
            self.assertEqual(claim['capacity']['capacity_status'], 'capacity_unverified')
            self.assertEqual(claim['capacity']['reservation'], 'none')
            plan = paired.read_json_pin(profile['capacity']['plan'])
            self.assertEqual(claim['capacity']['account_assignment_sha256'],
                             plan['account_assignment']['sha256'])
            return weekly._finish(slot, claim, status='incomplete', code='synthetic_not_executed')

        with unittest.mock.patch.object(weekly.CodexWeeklyExecutor, 'subscription_preflight', return_value=[{'synthetic': True}]) as preflight, \
             unittest.mock.patch.object(weekly, '_execute', side_effect=finish):
            result = weekly.run(profile, now=NOW_DT, one_run_override=override_ref)
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['capacity_status'], 'capacity_unverified')
        self.assertFalse(result['accepted'])
        self.assertFalse(result['runtime_admission'])
        self.assertIsNone(result['capacity']['capacity_receipt_sha256'])
        self.assertEqual(preflight.call_count, 1)
        self.assertEqual(len(list(self.runs.iterdir())), 1)

    def test_manual_selection_mismatch_refuses_before_claim_or_preflight(self):
        profile, override_ref = self.manual_codex_profile()
        profile['selection'] = write_pin(self.root / 'bad-one-run-selection.json',
                                         dict(json.loads(Path(profile['selection']['path']).read_text()),
                                              one_run_override_sha256='0'*64))
        with unittest.mock.patch.object(weekly.CodexWeeklyExecutor, 'subscription_preflight') as preflight:
            with self.assertRaisesRegex(paired.Refusal, '^one_run_selection_mismatch$'):
                weekly.run(profile, now=NOW_DT, one_run_override=override_ref)
        preflight.assert_not_called()
        self.assertEqual(os.listdir(self.runs), [])

    def test_without_explicit_override_normal_capacity_gate_still_refuses(self):
        profile, _ = self.manual_codex_profile()
        profile['capacity']['receipt'] = None
        with self.assertRaises(paired.Refusal):
            weekly.run(profile, now=NOW_DT)
        self.assertEqual(os.listdir(self.runs), [])

    def refuses(self, code, **kwargs):
        with self.assertRaisesRegex(paired.Refusal, f'^{code}$') as caught:
            self.run_weekly(**kwargs)
        self.assertEqual(os.listdir(self.runs), [], 'a refusal before effects writes nothing')
        return caught.exception

    def test_default_legacy_executor_refuses_with_concrete_blockers_before_effects(self):
        self.profile['executor'] = 'legacy-harness'
        account = self.profile['account']
        self.profile['account'] = None
        refusal = self.refuses('weekly_executor_blocked', executors=weekly.default_executors(None))
        self.assertEqual(refusal.blockers, ['dedicated_account_unbound'])
        # With a selected account and no execution pin, the contract is unbound.
        self.profile['account'], execution = account, self.profile['execution']
        self.profile['execution'] = None
        refusal = self.refuses('weekly_executor_blocked', executors=weekly.default_executors(account))
        self.assertEqual(refusal.blockers, ['original_execution_unbound'])
        # With both, the full contract is carried; the fixture's two-account capacity then refuses.
        self.profile['execution'] = execution
        self.refuses('capacity_account_mismatch', executors=weekly.default_executors(account))
        # An ambient arm with semantic fallback still names its undeclared selector.
        self.profile['execution'] = self.execution_pin(controls=dict(recall_mode='ambient', semantic_fallback=True))
        refusal = self.refuses('original_execution_unsupported:semantic_fallback_selector',
                               executors=weekly.default_executors(account))
        self.assertEqual(refusal.fields, ['original_execution_unsupported:semantic_fallback_selector'])

    def test_legacy_blockers_are_computed_from_configuration_and_account(self):
        account = self.profile['account']
        self.assertEqual(weekly.legacy_blockers(weekly.REQUIRED_CONFIGURATION, account), [])
        self.assertEqual(weekly.legacy_blockers(dict(weekly.REQUIRED_CONFIGURATION, hook_timeout_seconds=13), account),
                         ['original_limits_unenforced'])
        self.assertEqual(weekly.REQUIRED_CONFIGURATION['hook_timeout_seconds'], paired.ORIGINAL_LIMITS['hook_seconds'])
        self.assertEqual(weekly.REQUIRED_CONFIGURATION['mcp_tool_room'], ['--tokens', '32000', '--max-tokens', '32000'])

    def test_account_selection_refusals_before_effects(self):
        home = Path.home() / '.claude'
        for account, code in ((dict(self.profile['account'], config_home=str(home)), 'ambient_credentials_refused'),
                              (dict(self.profile['account'], config_home=str(self.root)), 'account_credentials_missing'),
                              (dict(self.profile['account'], config_home='relative'), 'account_selection_invalid'),
                              (dict(account_id='account-A'), 'account_selection_invalid')):
            with self.subTest(code=code):
                self.refuses(code, executors={'fake': FakeExecutor(account)})

    def test_selected_account_must_match_the_capacity_account_binding(self):
        executor = FakeExecutor(self.profile['account'], accounts=['account-A'])
        self.refuses('capacity_account_mismatch', executors={'fake': executor})
        real = weekly.LegacyHarnessExecutor(self.profile['account'])
        real.capabilities = frozenset(original.FIELDS)  # past the named refusals, to reach the capacity gate
        self.profile['executor'] = 'legacy-harness'
        self.refuses('capacity_account_mismatch', executors={'legacy-harness': real})

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
                          for name in ('CURRENT', 'CAND')},
                    original_execution=dict(native=dict(path='/declared:with/colon/claude', sha256='b' * 64), effort='max',
                                            embedding=dict(model_identity=dict(path='/declared/identity.json', sha256='c' * 64))),
                    original_arms=dict(path='/slot/original-arms.json', sha256='d' * 64))
        self.assertEqual(weekly.legacy_argv(spec, Path('/out')), [
            '/h/scripts/trial-task-eval.sh', 'agent', '--legacy-fixtures', '--original-limits', '--output', '/out', '--arms',
            '--memory', 'CURRENT:/CURRENT/cairn:/CURRENT/memory.py', '--memory', 'CAND:/CAND/cairn:/CAND/memory.py',
            '--distractors', '1900', '--seeds', '3', '--first-seed', '0', '--embedding-worker', '/w',
            '--harness', 'claude', '--model', 'sonnet', '--max-turns', '40', '--timeout', '900', '--parallel', '3',
            '--original-native-path', '/declared:with/colon/claude', '--original-native-sha256', 'b' * 64,
            '--reasoning-effort', 'max', '--original-arms', '/slot/original-arms.json', '--original-arms-sha256', 'd' * 64,
            '--original-embedding-identity', '/declared/identity.json', '--original-embedding-identity-sha256', 'c' * 64])
        with self.assertRaisesRegex(paired.Refusal, '^legacy_seed_list$'):
            weekly.legacy_argv(dict(spec, seeds=[1, 2, 4]), Path('/out'))
        spec['account'] = dict(account_id='account-A', config_home='/accounts/weekly')
        env = weekly.legacy_environment(spec, dict(PATH='/bin', OPENROUTER_API_KEY='x', ANTHROPIC_API_KEY='y',
                                                    CAIRN_DATABASE_URL='postgres://shared'))
        self.assertEqual(env, dict(PATH='/bin', CAIRN_PG_BIN='/pg', PYTHONDONTWRITEBYTECODE='1',
                                   CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR='/accounts/weekly'))

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
        # The retained launcher runs for both arms; the declared worker and config stay separate.
        self.assertEqual(spec['embedding']['declared_worker'], self.profile['embedding']['worker'])
        self.assertEqual(spec['embedding']['selected_config'], self.embedding_config)
        self.assertEqual(spec['embedding']['worker']['sha256'], self.profile['embedding']['worker']['sha256'])
        self.assertNotEqual(spec['embedding']['worker']['path'], self.profile['embedding']['worker']['path'])
        # One embedding worker for both arms; the harness owns the disposable cluster.
        argv = json.loads((Path(receipt['agent']['path']).parent / 'argv.json').read_text())
        self.assertEqual(argv['argv'].count('--embedding-worker'), 1)
        self.assertEqual(argv['argv'][argv['argv'].index('--embedding-worker') + 1], spec['embedding']['worker']['path'])
        self.assertEqual(argv['pg_bin'], self.profile['postgres']['bin_dir'])
        self.assertEqual(argv['leaked'], [])
        self.assertEqual(argv['config_home'], self.profile['account']['config_home'])
        self.assertEqual(receipt['limit_assessment']['within_original_limits'], 126)
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

    def test_optional_postflight_is_passive_and_replayed_byte_for_byte(self):
        first = self.run_weekly()
        slot = self.runs / first['slot_id']
        claim = paired.read_json(slot / 'claim.json')
        observation = [dict(account_id='codex-A', checked_at='2026-10-25T00:00:00Z',
                            status=dict(primary=dict(used_percent=41, window_duration_mins=10080,
                                                     resets_at='2026-10-30T22:43:17Z')),
                            error=None),
                       dict(account_id='codex-B', checked_at='2026-10-25T00:00:00Z',status=None,
                            error='codex_subscription_status_unavailable')]
        (slot / 'receipt.json').unlink()
        receipt = weekly._finish(slot, claim, status='incomplete', code='synthetic_failure',
                                 account_postflight=observation)
        self.assertEqual((receipt['status'], receipt['code']), ('incomplete', 'synthetic_failure'))
        self.assertEqual(receipt['account_postflight'], observation)
        stored, retry, _ = weekly._stored_slot(self.runs, first['slot_id'],
                                               claim['current']['commit'], claim['candidate']['commit'])
        self.assertFalse(retry)
        self.assertEqual(stored, dict(receipt, replayed=True))

    def test_unexpected_postflight_exception_keeps_terminal_receipt_durable(self):
        original = self.run_weekly()
        slot = self.runs / original['slot_id']
        claim = paired.read_json(slot / 'claim.json')
        executor = object.__new__(weekly.CodexWeeklyExecutor)
        executor.account_ids = ('codex-A', 'codex-B', 'codex-C')
        with unittest.mock.patch.object(weekly.CodexWeeklyExecutor, 'subscription_postflight',
                                        side_effect=RuntimeError('must not escape')):
            rows = weekly._account_postflight(executor, {})
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row['status'] is None for row in rows))
        (slot / 'receipt.json').unlink()
        terminal = weekly._finish(
            slot, claim, status=original['status'], code=original['code'], agent=original['agent'],
            attempts=original['attempts'], harness_exit_code=original['harness_exit_code'],
            pair_binding=original['pair_binding'], limit_assessment=original['limit_assessment'],
            baseline_custody=original['baseline_custody'], original_execution=original['original_execution'],
            account_postflight=rows)
        for field in ('status', 'code', 'accepted', 'runtime_admission', 'pair_binding'):
            self.assertEqual(terminal.get(field), original.get(field))
        self.assertEqual(json.loads((slot / 'receipt.json').read_text()), terminal)

    def test_slot_used_by_another_pair_refuses(self):
        self.run_weekly()
        git(self.repo.path, 'checkout', '-q', 'candidate')
        (self.repo.path / 'integrations/lifecycle/memory.py').write_text('# second candidate\n')
        git(self.repo.path, 'commit', '-qam', 'second candidate')
        self.repo.candidate = git(self.repo.path, 'rev-parse', 'HEAD')
        git(self.repo.path, 'checkout', '-q', 'main')
        self.profile['execution'] = self.execution_pin()
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
        self.profile['execution'] = self.execution_pin()
        failed = self.run_weekly()
        self.assertEqual((failed['status'], failed['code']), ('failed_before_execution', 'harness_frozen_mismatch'))
        self.assertEqual(self.executor.executed, [])
        frozen = self.repo.path / 'trials/task-eval/FROZEN.json'
        self.profile['weekly_source']['frozen_sha256'] = hashlib.sha256(frozen.read_bytes()).hexdigest()
        self.profile['execution'] = self.execution_pin()
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
        self.assertEqual(result['code'], 'capacity_account_mismatch')
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
        self.profile['execution'] = self.execution_pin()
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


    def interleave(self):
        """Patch selection_gate so the outer call runs one inner run_weekly before its own gate."""
        real, inner, entered = weekly.selection_gate, [], []
        def gate(*args, **kwargs):
            if not entered:
                entered.append(True)
                try:
                    inner.append(self.run_weekly()['status'])
                except paired.Refusal as refusal:
                    inner.append(str(refusal))
            return real(*args, **kwargs)
        return unittest.mock.patch.object(weekly, 'selection_gate', side_effect=gate), inner

    def test_interleaved_retry_never_executes_the_slot_twice_or_archives_a_completed_slot(self):
        self.profile['weekly_source']['frozen_sha256'] = '0' * 64
        self.profile['execution'] = self.execution_pin()
        failed = self.run_weekly()
        self.assertEqual(failed['status'], 'failed_before_execution')
        frozen = self.repo.path / 'trials/task-eval/FROZEN.json'
        self.profile['weekly_source']['frozen_sha256'] = hashlib.sha256(frozen.read_bytes()).hexdigest()
        self.profile['execution'] = self.execution_pin()
        patch, inner = self.interleave()
        with patch:
            outer = self.run_weekly()
        self.assertEqual(inner, ['weekly_run_in_progress'])
        self.assertEqual(outer['status'], 'complete')
        self.assertEqual(len(self.executor.executed), 1)
        archived = [json.loads((path / 'receipt.json').read_text())['status']
                    for path in (self.runs / 'failed').iterdir()]
        self.assertEqual(archived, ['failed_before_execution'])

    def test_concurrent_fresh_claims_execute_once(self):
        patch, inner = self.interleave()
        with patch:
            outer = self.run_weekly()
        self.assertEqual(inner, ['weekly_run_in_progress'])
        self.assertEqual(outer['status'], 'complete')
        self.assertEqual(len(self.executor.executed), 1)

    def test_held_slot_lock_refuses_without_writing(self):
        import fcntl
        descriptor = os.open(self.runs, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            self.refuses('weekly_run_in_progress')
        finally:
            os.close(descriptor)
        self.assertEqual(self.executor.executed, [])


    def test_original_limit_violations_are_row_failures(self):
        for mode, code in (('limit_answer', 'original_limit_exceeded'), ('limit_hook', 'original_limit_exceeded'),
                           ('limit_late', 'original_limit_exceeded'), ('limit_unobserved', 'original_limit_unobserved')):
            with self.subTest(mode=mode):
                self.tearDown()
                self.setUp()
                self.executor.harness_mode = mode
                receipt = self.run_weekly()
                self.assertEqual((receipt['status'], receipt['code']), ('incomplete', code))
                self.assertIsNone(receipt['pair_binding'])
                self.assertEqual(receipt['limit_assessment']['within_original_limits'], 125)

    def test_attempt_prompt_must_be_the_frozen_task(self):
        self.executor.harness_mode = 'prompt'
        receipt = self.run_weekly()
        self.assertEqual((receipt['status'], receipt['code']), ('rejected', 'task_prompt_mismatch'))

    def test_harness_without_the_account_selector_is_never_executed(self):
        git(self.repo.path, 'checkout', '-q', '-b', 'old-harness', self.repo.harness)
        (self.repo.path / 'scripts/trial_account_selection.py').unlink()
        git(self.repo.path, 'commit', '-qam', 'harness before account selection')
        self.profile['harness_commit'] = git(self.repo.path, 'rev-parse', 'HEAD')
        git(self.repo.path, 'checkout', '-q', 'main')
        self.select(harness_commit=self.profile['harness_commit'])
        failed = self.run_weekly()
        self.assertEqual((failed['status'], failed['code']),
                         ('failed_before_execution', 'harness_account_selector_unsupported'))
        self.assertEqual(self.executor.executed, [])

    def test_executor_environment_never_carries_an_ambient_or_inherited_account(self):
        spec = dict(postgres=dict(bin_dir='/pg'), account=None)
        with self.assertRaisesRegex(paired.Refusal, '^dedicated_account_unbound$'):
            weekly.legacy_environment(spec, dict(PATH='/bin', CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR='/ambient'))
        env = weekly.legacy_environment(dict(spec, account=self.profile['account']),
                                        dict(PATH='/bin', CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR='/ambient'))
        self.assertEqual(env['CAIRN_TASK_EVAL_CLAUDE_CONFIG_DIR'], self.profile['account']['config_home'])

    def test_codex_environment_strips_api_key_and_openrouter_fallback(self):
        env = weekly.codex_environment(dict(postgres=dict(bin_dir='/synthetic/pg')), dict(
            PATH='/bin', OPENAI_API_KEY='must-not-pass', OPENAI_BASE_URL='must-not-pass',
            OPENROUTER_API_KEY='must-not-pass', OPENROUTER_BASE_URL='must-not-pass',
            CAIRN_CODEX_AUTH_FILE='/ambient/must-not-pass'))
        self.assertFalse(any(name in env for name in (
            'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'OPENROUTER_API_KEY', 'OPENROUTER_BASE_URL',
            'CAIRN_CODEX_AUTH_FILE')))
        self.assertEqual(env['CAIRN_PG_BIN'], '/synthetic/pg')


    def test_harness_without_original_limits_support_is_never_executed(self):
        git(self.repo.path, 'checkout', '-q', '-b', 'no-limits', self.repo.harness)
        (self.repo.path / 'scripts/trial_legacy_limits.py').unlink()
        git(self.repo.path, 'commit', '-qam', 'harness before original limits')
        self.profile['harness_commit'] = git(self.repo.path, 'rev-parse', 'HEAD')
        git(self.repo.path, 'checkout', '-q', 'main')
        self.select(harness_commit=self.profile['harness_commit'])
        failed = self.run_weekly()
        self.assertEqual((failed['status'], failed['code']),
                         ('failed_before_execution', 'harness_original_limits_unsupported'))
        self.assertEqual(self.executor.executed, [])


    def test_complete_run_verifies_every_retained_baseline_before_binding(self):
        receipt = self.run_weekly()
        self.assertEqual(receipt['status'], 'complete')
        self.assertEqual(receipt['baseline_custody'], dict(verified=126, proof='harness_retained_baseline'))
        self.assertIsNotNone(receipt['pair_binding'])

    def test_missing_tampered_swapped_or_wrong_identity_baselines_never_complete_or_bind(self):
        for mode, status, code in (('baseline_missing', 'incomplete', 'attempt_baseline_missing'),
                                   ('baseline_absent', 'rejected', 'attempt_baseline_invalid'),
                                   ('baseline_tampered', 'rejected', 'attempt_baseline_invalid'),
                                   ('baseline_swapped', 'rejected', 'attempt_baseline_invalid'),
                                   ('baseline_identity', 'rejected', 'attempt_baseline_invalid'),
                                   ('baseline_policy', 'rejected', 'attempt_baseline_invalid')):
            with self.subTest(mode=mode):
                self.tearDown()
                self.setUp()
                self.executor.harness_mode = mode
                receipt = self.run_weekly()
                self.assertEqual((receipt['status'], receipt['code']), (status, code))
                self.assertIsNone(receipt['pair_binding'])
                self.assertFalse((self.runs / receipt['slot_id'] / 'pair-binding.json').exists())


    def test_malformed_retained_baseline_documents_end_in_a_terminal_rejected_receipt(self):
        for shape in ('null', 'number', 'string', 'bool', 'list', 'keys', 'types', 'invalid', 'deep'):
            with self.subTest(shape=shape):
                self.tearDown()
                self.setUp()
                self.executor.harness_mode = 'baseline_raw:' + shape
                receipt = self.run_weekly()
                self.assertEqual((receipt['status'], receipt['code']), ('rejected', 'attempt_baseline_invalid'))
                self.assertIsNone(receipt['pair_binding'])
                stored = json.loads((self.runs / receipt['slot_id'] / 'receipt.json').read_text())
                self.assertEqual(stored['code'], 'attempt_baseline_invalid')
                self.assertEqual(self.run_weekly()['replayed'], True)
                self.assertEqual(len(self.executor.executed), 1)


    def oe_rejected(self, mode, status, code):
        self.executor.harness_mode = mode
        receipt = self.run_weekly()
        self.assertEqual((receipt['status'], receipt['code']), (status, code))
        self.assertIsNone(receipt['pair_binding'])

    def test_complete_receipt_records_declared_and_observed_execution_separately(self):
        receipt = self.run_weekly()
        self.assertEqual(receipt['status'], 'complete')
        execution = receipt['original_execution']
        self.assertEqual(execution['proof'], 'harness_observed_copy')
        self.assertEqual(execution['declared']['effort'], 'xhigh')
        self.assertEqual(execution['declared']['arms']['CAND'],
                         dict(recall_mode='agent_tools', semantic_fallback=True, recall_revision=self.repo.candidate,
                              hook=self.recall_blob(self.repo.candidate, 'memory.py'),
                              bridge=self.recall_blob(self.repo.candidate, 'inbox_recall.py')))
        self.assertEqual(execution['observed']['native']['sha256'], execution['declared']['native']['sha256'])
        self.assertEqual(execution['declared']['identity'],
                         dict(model_sha256='a' * 64, algorithm='synthetic-embedding/1', dimensions=384))
        self.assertEqual(execution['observed']['identities']['CAND'],
                         dict(model_sha256='a' * 64, algorithm='synthetic-embedding/1', dimensions='unobserved'))
        self.assertNotEqual(execution['declared']['embedding']['model_identity']['sha256'], 'a' * 64)
        argv = json.loads((Path(receipt['agent']['path']).parent / 'argv.json').read_text())['argv']
        self.assertEqual(argv[argv.index('--original-native-sha256') + 1], execution['declared']['native']['sha256'])
        self.assertEqual(argv[argv.index('--reasoning-effort') + 1], 'xhigh')

    def test_observed_execution_mismatches_never_complete_or_bind(self):
        for mode, status, code in (('oe_plan_sha', 'rejected', 'original_execution_mismatch'),
                                   ('oe_effort', 'rejected', 'original_execution_mismatch'),
                                   ('oe_unobserved', 'incomplete', 'original_execution_unobserved'),
                                   ('oe_row_missing', 'incomplete', 'original_execution_unobserved'),
                                   ('oe_identity_mismatch', 'rejected', 'original_execution_identity_mismatch'),
                                   ('oe_identity_arms', 'rejected', 'original_execution_identity_mismatch'),
                                   ('oe_identity_missing', 'incomplete', 'original_execution_identity_unobserved')):
            with self.subTest(mode=mode):
                self.tearDown()
                self.setUp()
                self.oe_rejected(mode, status, code)

    def test_declaration_must_match_the_run_before_any_effect(self):
        document = self.execution_document()
        self.profile['execution'] = self.execution_pin(native=dict(document['native'], model='other-model'))
        self.refuses('original_execution_model_mismatch')
        arms = document['memory_arms']
        arms['CAND'] = dict(arms['CAND'], api_revision=self.repo.current)
        self.profile['execution'] = self.execution_pin(memory_arms=arms)
        self.refuses('original_execution_arm_mismatch')
        arms['CAND'] = dict(arms['CAND'], api_revision=self.repo.candidate)
        for content in (dict(schema='x'), 'opaque model identity', dict(model_sha256='a' * 64, algorithm='a'),
                        dict(schema='cairn.embedding-model-identity/1', model_sha256='a' * 64, algorithm='a')):
            with self.subTest(content=content):
                identity = write_pin(self.root / 'declared/bad-identity.json', content) if isinstance(content, dict) \
                    else dict(path=str(_write(self.root / 'declared/bad-identity.json', content)),
                              sha256=hashlib.sha256(content.encode()).hexdigest())
                for name in ('CURRENT', 'CAND'):
                    arms[name] = dict(arms[name], embedding_model_identity=identity)
                self.profile['execution'] = self.execution_pin(memory_arms=arms)
                refusal = self.refuses('original_execution_unsupported:model_identity_declaration')
                self.assertEqual(refusal.fields, ['original_execution_unsupported:model_identity_declaration'])

    def test_replay_satisfies_only_the_recorded_execution_contract(self):
        first = self.run_weekly()
        self.assertEqual(self.run_weekly(), dict(first, replayed=True))
        native = dict(self.execution_document()['native'], effort='max')
        self.profile['execution'] = self.execution_pin(native=native)
        with self.assertRaisesRegex(paired.Refusal, '^stored_slot_execution_mismatch$'):
            self.run_weekly()
        self.assertEqual(len(self.executor.executed), 1)

    def test_receipt_from_before_the_contract_never_satisfies_it(self):
        receipt = self.run_weekly()
        slot = self.runs / receipt['slot_id']
        legacy = {k: v for k, v in receipt.items() if k != 'original_execution'}
        (slot / 'receipt.json').unlink()
        paired.write_selected(slot / 'receipt.json', legacy)
        with self.assertRaisesRegex(paired.Refusal, '^stored_slot_execution_mismatch$'):
            self.run_weekly()
        self.assertEqual(len(self.executor.executed), 1)


    def test_declared_identity_compares_typed_values_never_the_declaration_file_hash(self):
        declaration = self.execution_document()['memory_arms']['CURRENT']['embedding_model_identity']
        for discovery, status, code in (
                (dict(model_sha256='a' * 64, algorithm='synthetic-embedding/1'), 'complete', None),
                (dict(model_sha256='b' * 64, algorithm='synthetic-embedding/1'), 'rejected', 'original_execution_identity_mismatch'),
                (dict(model_sha256='a' * 64, algorithm='other/1'), 'rejected', 'original_execution_identity_mismatch'),
                # The declaration file's own SHA-256 is not the backend model hash.
                (dict(model_sha256=declaration['sha256'], algorithm='synthetic-embedding/1'), 'rejected',
                 'original_execution_identity_mismatch')):
            with self.subTest(discovery=discovery):
                self.tearDown()
                self.setUp()
                self.executor.discovery = discovery
                receipt = self.run_weekly()
                self.assertEqual((receipt['status'], receipt['code']), (status, code))


    def test_dropping_the_execution_pin_never_replays_a_contract_completion(self):
        self.run_weekly()
        self.profile['execution'] = None
        with self.assertRaisesRegex(paired.Refusal, '^stored_slot_execution_mismatch$'):
            self.run_weekly()
        self.assertEqual(len(self.executor.executed), 1)

    def test_malformed_backend_observation_is_terminal_and_never_held(self):
        self.executor.harness_mode = 'oe_backend_string'
        receipt = self.run_weekly()
        self.assertEqual((receipt['status'], receipt['code']), ('rejected', 'original_execution_observation_malformed'))
        self.assertIsNone(receipt['pair_binding'])
        self.assertEqual(self.run_weekly()['replayed'], True)
        self.assertEqual(len(self.executor.executed), 1)


    def test_complete_run_carries_literal_controls_raw_recall_blobs_and_the_retained_launcher(self):
        receipt = self.run_weekly()
        self.assertEqual(receipt['status'], 'complete')
        slot = self.runs / receipt['slot_id']
        spec = json.loads((slot / 'spec.json').read_text())
        controls = json.loads((slot / 'original-arms.json').read_text())
        for name, commit in (('CURRENT', self.repo.current), ('CAND', self.repo.candidate)):
            arm = controls['arms'][name]
            self.assertEqual((arm['recall_mode'], arm['semantic_fallback'], arm['recall_revision']),
                             ('agent_tools', True, commit))
            for role, file in (('hook', 'memory.py'), ('bridge', 'inbox_recall.py')):
                self.assertEqual(arm[role]['path'], str(slot / 'arms' / name / 'recall' / file))
                self.assertEqual(arm[role]['sha256'], self.recall_blob(commit, file)['sha256'])
                self.assertEqual(spec['arms'][name][role], arm[role])
        argv = json.loads((Path(receipt['agent']['path']).parent / 'argv.json').read_text())['argv']
        self.assertEqual(argv[argv.index('--original-arms') + 1], str(slot / 'original-arms.json'))
        self.assertIn(f"CAND:{spec['arms']['CAND']['binary']['path']}:{slot / 'arms/CAND/recall/memory.py'}", argv)
        self.assertEqual(argv[argv.index('--embedding-worker') + 1], str(slot / 'embedding/worker-embeddings'))
        observed = receipt['original_execution']['observed']['arms']['arms']
        self.assertEqual(observed['CAND']['bridge_sha256'], self.recall_blob(self.repo.candidate, 'inbox_recall.py')['sha256'])

    def test_recall_blob_must_be_the_declared_hook_and_bridge_at_the_revision(self):
        document = self.execution_document()
        arms = document['memory_arms']
        arms['CAND'] = dict(arms['CAND'], bridge=write_pin(self.root / 'declared/other-bridge.json', {'not': 'the blob'}))
        self.profile['execution'] = self.execution_pin(memory_arms=arms)
        failed = self.run_weekly()
        self.assertEqual((failed['status'], failed['code']), ('failed_before_execution', 'original_execution_recall_mismatch'))
        self.assertEqual(self.executor.executed, [])

    def test_recall_revision_must_resolve_to_that_exact_commit(self):
        arms = self.execution_document()['memory_arms']
        arms['CAND'] = dict(arms['CAND'], recall_revision='candidate')
        self.profile['execution'] = self.execution_pin(memory_arms=arms)
        failed = self.run_weekly()
        self.assertEqual((failed['status'], failed['code']), ('failed_before_execution', 'original_execution_recall_unresolved'))

    def test_replacement_objects_never_substitute_the_recall_blob(self):
        git(self.repo.path, 'replace', self.repo.candidate, self.repo.current)
        receipt = self.run_weekly()
        controls = json.loads((self.runs / receipt['slot_id'] / 'original-arms.json').read_text())
        self.assertEqual(controls['arms']['CAND']['hook']['sha256'],
                         hashlib.sha256(b'# synthetic candidate hook\n').hexdigest())

    def test_embedding_config_is_prepared_by_the_launch_helper_or_refused(self):
        changed = json.loads(Path(self.embedding_config['path']).read_text())
        changed['scorer'] = dict(changed['scorer'], sha256='0' * 64)
        self.embedding_config = write_pin(self.root / 'semantic/bad-config.json', changed)
        self.profile['execution'] = self.execution_pin()
        failed = self.run_weekly()
        self.assertEqual((failed['status'], failed['code']),
                         ('failed_before_execution', 'original_execution_embedding_embedding_source_unsupported'))
        self.assertEqual(self.executor.executed, [])

    def test_observed_arm_controls_must_match_the_declaration(self):
        for mode, status, code in (('oe_arm', 'rejected', 'original_execution_mismatch'),
                                   ('oe_plan_arms', 'rejected', 'original_execution_mismatch'),
                                   ('oe_arms_missing', 'incomplete', 'original_execution_unobserved')):
            with self.subTest(mode=mode):
                self.tearDown()
                self.setUp()
                self.oe_rejected(mode, status, code)


if __name__ == '__main__':
    unittest.main()
