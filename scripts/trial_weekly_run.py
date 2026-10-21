"""Weekly paired task-evaluation runner (CAIRN-107), behind a fail-closed executor seam.

One invocation handles one weekly slot of one campaign. Before any effect it
checks the closed profile, that `now` is inside the slot window, the slot's
existing state, both commits, the existing capacity-adapter evidence and the
selected executor. Only then does it claim the slot, build both pinned arms
(the `trial_candidate_request` build), export the pinned harness commit, verify
the frozen label version, write the run spec and call the executor. The
executor's `cairn.task-eval.agent/1` document is checked with the same reader
`trial_run_report` uses and pinned in a `cairn.weekly-run-receipt/1`.

What this is not:

- It never asserts runtime admission or authorization. The receipt always
  says `runtime_admission: false`; capacity account proof stays declared.
- It never manufactures capacity. The only accepted evidence is a pinned
  `trial_capacity_adapter` receipt whose sources still verify, bound to the
  original 21-case, 126-attempt plan, covering the whole slot window and the
  executor's exact accounts. Percentages never reach this gate.
- The shipped `legacy-harness` executor refuses with named blockers; see
  docs/weekly-runner-reconciliation.md. No executor is selected by default.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import trial_capacity_adapter as adapter
import trial_candidate_request as candidate
import trial_paired_request as paired
import trial_run_report as report
import trial_task_eval as te
from check_weekly_paired_eval import WEEKLY_ARMS, WEEKLY_ATTEMPTS, WEEKLY_CASES, WEEKLY_SEEDS

PROFILE_SCHEMA = 'cairn.weekly-run-profile/1'
SPEC_SCHEMA = 'cairn.weekly-run-spec/1'
RECEIPT_SCHEMA = 'cairn.weekly-run-receipt/1'
CLAIM_SCHEMA = 'cairn.weekly-run-claim/1'
SELECTION_SCHEMA = 'cairn.weekly-run-selection/1'
PROFILE_FIELDS = ('schema repo current_ref candidate_ref harness_commit runs_root campaign_id schedule '
                  'weekly_source embedding postgres capacity executor selection')
SELECTION_FIELDS = 'schema campaign_id scheduled_at current candidate harness_commit executor capacity_receipt_sha256 selected_by'
DEFAULT_PROFILE = Path.home() / '.config/cairn/weekly-run.json'
ARMS = ('CURRENT', 'CAND')
DISTRACTORS = 1900
NAMESPACE = uuid.UUID('0b5c1f3e-7a42-5d8e-9c10-7e107a0c1d07')
TRIAL_PATH = 'trials/task-eval'
AGENT_LIMIT = 256 * 1024 * 1024
need = paired.need


class ExecutorBlocked(paired.Refusal):
    """Executor refusal that names its exact blockers."""

    def __init__(self, blockers):
        super().__init__('weekly_executor_blocked')
        self.blockers = list(blockers)


# ---------------------------------------------------------------- executors

# Limits the legacy run_agent actually applies (trial_task_eval.py): hook
# context only (CLAUDE_CONTEXT_BYTES), a 13 s hook timeout (settings hook
# timeout), and no native search, pull, final-answer or tool-room bound.
LEGACY_LIMITS = dict(selected_input_bytes=None, final_answer_bytes=None, tool_room_bytes=None,
                     native_searches=None, pulls=None, hook_seconds=13, last_native_memory_seconds=None)
# trial_task_eval.sandbox_command binds the host ~/.claude/.credentials.json
# read-write; the harness has no way to select a dedicated account.
LEGACY_ACCOUNT_SELECTOR = None
# Provider routes the harness process must never inherit (subscription only).
STRIPPED_ENVIRONMENT = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'OPENAI_API_KEY',
                        'OPENAI_BASE_URL', 'OPENROUTER_API_KEY', 'OPENROUTER_BASE_URL')


def legacy_blockers(limits=LEGACY_LIMITS, account_selector=LEGACY_ACCOUNT_SELECTOR):
    """Concrete source blockers of the legacy route, computed from its declared behavior."""
    blockers = []
    if limits != paired.ORIGINAL_LIMITS:
        blockers.append('original_limits_unenforced')
    if account_selector is None:
        blockers.append('dedicated_account_unbound')
    return blockers


def legacy_argv(spec, output):
    """The original harness command for one weekly spec; runs nothing."""
    seeds = spec['seeds']
    need(seeds == list(range(seeds[0], seeds[0] + len(seeds))), 'legacy_seed_list')
    argv = [str(Path(spec['harness']['root']) / 'scripts/trial-task-eval.sh'), 'agent', '--legacy-fixtures',
            '--output', str(output), '--arms']
    for name in ARMS:
        arm = spec['arms'][name]
        argv += ['--memory', f"{name}:{arm['binary']['path']}:{arm['hook']['path']}"]
    return argv + ['--distractors', str(spec['distractors']), '--seeds', str(len(seeds)),
                   '--first-seed', str(seeds[0]), '--embedding-worker', spec['embedding']['worker']['path'],
                   '--harness', 'claude', '--model', spec['actor_model'],
                   '--max-turns', '40', '--timeout', '900', '--parallel', '3']


def legacy_environment(spec, base=None):
    """Harness environment: the profile's PostgreSQL binaries, no API-key or OpenRouter route."""
    base = os.environ if base is None else base
    env = {k: v for k, v in base.items() if k not in STRIPPED_ENVIRONMENT and not k.startswith('CAIRN_')}
    return dict(env, CAIRN_PG_BIN=spec['postgres']['bin_dir'], PYTHONDONTWRITEBYTECODE='1')


class LegacyHarnessExecutor:
    """Runs `trial-task-eval.sh agent --legacy-fixtures` from the exported harness commit.

    `check` refuses while any concrete blocker from `legacy_blockers` applies;
    `execute` is the real invocation and is exercised only with a fake harness.
    """

    name = 'legacy-harness'
    limits = LEGACY_LIMITS
    account_ids = ()

    def check(self, preview):
        blockers = legacy_blockers(self.limits, LEGACY_ACCOUNT_SELECTOR)
        if blockers:
            raise ExecutorBlocked(blockers)

    def execute(self, spec, run_dir):
        output = Path(run_dir) / 'agent'
        argv = legacy_argv(spec, output)
        with open(Path(run_dir) / 'harness.log', 'xb') as log:
            os.chmod(log.name, 0o600)
            done = subprocess.run(argv, cwd=spec['harness']['root'], env=legacy_environment(spec),
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        # 0 is complete and 2 a provider or observer stop; the runner judges any other status.
        return dict(agent=output / 'agent.json', exit_code=done.returncode)


def default_executors():
    return {LegacyHarnessExecutor.name: LegacyHarnessExecutor()}


# ---------------------------------------------------------------- profile and slot

def check_profile(profile):
    paired.shape(profile, PROFILE_FIELDS, 'profile_shape')
    need(profile['schema'] == PROFILE_SCHEMA, 'profile_schema')
    repo = paired.plain_path(profile['repo'])
    need((repo / '.git').exists(), 'profile_repo')
    for field in ('current_ref', 'candidate_ref'):
        need(type(profile[field]) is str and candidate._safe_ref(profile[field]), 'profile_ref')
    need(type(profile['harness_commit']) is str and len(profile['harness_commit']) == 40, 'profile_harness')
    runs = paired.plain_path(profile['runs_root'])
    need(runs.is_dir() and runs.stat().st_mode & 0o077 == 0, 'private_runs_root_required')
    paired.identifier(profile['campaign_id'])
    schedule = profile['schedule']
    paired.shape(schedule, 'weekday time window_minutes', 'profile_schedule')
    need(candidate._int(schedule['weekday'], 0, 6) and type(schedule['time']) is str
         and len(schedule['time']) == 5 and candidate._int(schedule['window_minutes'], 1, 7 * 24 * 60),
         'profile_schedule')
    paired.shape(profile['weekly_source'], 'label_version frozen_sha256', 'profile_weekly_source')
    need(candidate._int(profile['weekly_source']['label_version'], 1), 'profile_weekly_source')
    paired.shape(profile['embedding'], 'worker', 'profile_embedding')
    paired.pin(profile['embedding']['worker'])
    paired.shape(profile['postgres'], 'bin_dir', 'profile_postgres_shape')
    paired.plain_path(profile['postgres']['bin_dir'])
    paired.shape(profile['capacity'], 'receipt plan models', 'profile_capacity')
    need(type(profile['executor']) is str, 'profile_executor')
    need(profile['selection'] is None or isinstance(profile['selection'], dict), 'profile_selection')
    return repo, runs


def current_slot(schedule, now):
    """The latest weekly slot start <= now, refusing outside its window."""
    start = candidate.next_occurrence(schedule, now)[0] - dt.timedelta(days=7)
    end = start + dt.timedelta(minutes=schedule['window_minutes'])
    need(start <= now < end, 'outside_weekly_window')
    return start, end


# ---------------------------------------------------------------- capacity gate

def capacity_gate(capacity, *, now, slot_end, account_ids):
    """Accept only existing adapter evidence for the original plan, the whole slot and these accounts."""
    adapter.verify_sources(capacity['receipt'])
    receipt = paired.read_json_pin(capacity['receipt'])
    plan = paired.read_json_pin(capacity['plan'])
    need(isinstance(plan, dict) and plan.get('sha256') == paired.canonical_sha(
        {k: v for k, v in plan.items() if k != 'sha256'}) and receipt['plan_sha256'] == plan['sha256'],
        'capacity_plan_mismatch')
    need(plan.get('attempts') == WEEKLY_ATTEMPTS and plan.get('arms') == list(ARMS)
         and isinstance(plan.get('cases'), list) and len(set(plan['cases'])) == WEEKLY_CASES == len(plan['cases'])
         and isinstance(plan.get('seeds'), list) and len(set(plan['seeds'])) == WEEKLY_SEEDS == len(plan['seeds']),
         'capacity_plan_not_original')
    models = capacity['models']
    paired.capacity_check(receipt['capacity'], plan, now, models)
    evidence = paired.read_json_pin(receipt['capacity'])
    need(paired.utc(evidence['valid_until']) >= slot_end, 'capacity_window_short')
    accounts = paired.capacity_accounts(evidence['account_binding'])
    need(accounts == set(account_ids), 'capacity_account_mismatch')
    need(models['CURRENT'] == models['CAND'], 'capacity_actor_models_differ')
    return dict(receipt_sha256=capacity['receipt']['sha256'], plan_sha256=plan['sha256'],
                input_sha256=receipt['sources']['input_sha256'], accounts=sorted(accounts),
                valid_until=evidence['valid_until'], account_proof=receipt['account_proof']), plan


# ---------------------------------------------------------------- live selection

def selection_gate(profile, preview, executor_name, capacity):
    """Bind an explicit live-invocation selection to this exact slot, pair, harness and evidence.

    The selection is a declared, pinned decision by whoever holds campaign
    authority; this code checks only that it names this invocation. It does
    not authenticate the selector or infer authority from any other input.
    """
    reference = profile['selection']
    need(reference is not None, 'live_invocation_not_selected')
    document = paired.read_json_pin(reference)
    paired.shape(document, SELECTION_FIELDS, 'live_selection_shape')
    need(document['schema'] == SELECTION_SCHEMA, 'live_selection_shape')
    paired.identifier(document['selected_by'])
    expected = dict(campaign_id=profile['campaign_id'], scheduled_at=preview['scheduled_at'],
                    current=preview['current'], candidate=preview['candidate'],
                    harness_commit=preview['harness'], executor=executor_name,
                    capacity_receipt_sha256=capacity['receipt_sha256'])
    need(all(document[key] == value for key, value in expected.items()), 'live_selection_mismatch')
    return dict(sha256=reference['sha256'], selected_by=document['selected_by'], proof='declared_unauthenticated')


# ---------------------------------------------------------------- run

def run(profile, *, now, executors=None, compiler=None):
    """Run or refuse one weekly slot; return its receipt.

    Every refusal before the slot claim writes nothing. A repeated call for a
    recorded slot returns the stored receipt without building or executing.
    """
    executors = default_executors() if executors is None else executors
    repo, runs = check_profile(profile)
    start, end = current_slot(profile['schedule'], now)
    slot_id = str(uuid.uuid5(NAMESPACE, profile['campaign_id'] + '|' + candidate._iso(start)))
    current = candidate.resolve(repo, profile['current_ref'], 'current_unresolved')
    cand = candidate.resolve(repo, profile['candidate_ref'], 'candidate_unresolved')
    need(cand != current, 'candidate_equals_current')
    harness = candidate.resolve(repo, profile['harness_commit'], 'harness_unresolved')
    need(harness == profile['harness_commit'], 'harness_unresolved')
    stored = _stored_slot(runs, slot_id, current, cand)
    if stored is not None:
        return stored
    executor = executors.get(profile['executor'])
    need(executor is not None, 'weekly_executor_unknown')
    preview = dict(slot_id=slot_id, scheduled_at=candidate._iso(start), expires_at=candidate._iso(end),
                   current=current, candidate=cand, harness=harness, limits=dict(paired.ORIGINAL_LIMITS),
                   cases=WEEKLY_CASES, attempts=WEEKLY_ATTEMPTS, distractors=DISTRACTORS)
    executor.check(preview)
    need(executor.limits == paired.ORIGINAL_LIMITS, 'executor_limits_unenforced')
    capacity, plan = capacity_gate(profile['capacity'], now=now, slot_end=end, account_ids=executor.account_ids)
    selection = selection_gate(profile, preview, executor.name, capacity)
    slot = runs / slot_id
    claim = dict(schema=CLAIM_SCHEMA, slot_id=slot_id, campaign_id=profile['campaign_id'],
                 scheduled_at=preview['scheduled_at'], expires_at=preview['expires_at'],
                 current=dict(ref=profile['current_ref'], commit=current),
                 candidate=dict(ref=profile['candidate_ref'], commit=cand), harness_commit=harness,
                 executor=executor.name, capacity=capacity, selection=selection)
    try:
        slot.mkdir(mode=0o700)
    except FileExistsError:
        raise paired.Refusal('weekly_run_in_progress') from None
    paired.write_selected(slot / 'claim.json', claim)
    try:
        spec = _prepare(profile, slot, claim, plan, repo, compiler or candidate.go_build)
    except paired.Refusal as error:
        return _finish(slot, claim, status='failed_before_execution', code=str(error))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return _finish(slot, claim, status='failed_before_execution', code='weekly_preparation_failed')
    paired.write_selected(slot / 'execution-started.json', dict(slot_id=slot_id, executor=executor.name))
    try:
        executed = executor.execute(spec, slot)
    except (paired.Refusal, OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        return _finish(slot, claim, status='incomplete', code='executor_failed')
    result = _agent_result(Path(executed['agent']), spec)
    if executed['exit_code'] not in (0, 2) and result['status'] == 'complete':
        result.update(status='incomplete', code='harness_exit_unexpected')
    return _finish(slot, claim, harness_exit_code=executed['exit_code'], **result)


def _stored_slot(runs, slot_id, current, cand):
    """Return a recorded slot's receipt, retire a retryable failure, or refuse; read-only otherwise."""
    slot = runs / slot_id
    if not slot.exists():
        return None
    try:
        claim = paired.read_json(slot / 'claim.json')
        receipt = paired.read_json(slot / 'receipt.json')
    except (paired.Refusal, OSError):
        raise paired.Refusal('weekly_run_in_progress') from None
    need((claim['current']['commit'], claim['candidate']['commit']) == (current, cand), 'weekly_slot_other_pair')
    if receipt['status'] != 'failed_before_execution' or (slot / 'execution-started.json').exists():
        return dict(receipt, replayed=True)
    failed = runs / 'failed'
    failed.mkdir(mode=0o700, exist_ok=True)
    attempt = 1 + sum(1 for path in failed.iterdir() if path.name.startswith(slot_id + '.'))
    slot.rename(failed / f'{slot_id}.{attempt}')
    return None


def _prepare(profile, slot, claim, plan, repo, compiler):
    """Build both arms and export the pinned harness; verify the frozen label version."""
    arms = {name: candidate.build_arm(repo, claim[key]['commit'], slot / 'arms' / name, compiler)
            for name, key in (('CURRENT', 'current'), ('CAND', 'candidate'))}
    harness = slot / 'harness'
    try:
        candidate.export_commit(repo, claim['harness_commit'], harness)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        raise paired.Refusal('harness_export_failed') from None
    trial = harness / TRIAL_PATH
    source = profile['weekly_source']
    version = source['label_version']
    # The legacy loaders select the latest revision of their own checkout: pin that it is this one.
    need(te.latest_version(root=trial) == version, 'harness_label_version')
    frozen_file = te.frozen_path(version, root=trial)
    need(frozen_file.is_file() and hashlib.sha256(frozen_file.read_bytes()).hexdigest() == source['frozen_sha256'],
         'harness_frozen_mismatch')
    try:
        frozen = te.verify_frozen(version, root=trial)
    except SystemExit:
        raise paired.Refusal('harness_frozen_mismatch') from None
    case_ids = [case['id'] for case in te.load_cases(version, root=trial)]
    need(len(case_ids) == WEEKLY_CASES and sorted(case_ids) == sorted(plan['cases']), 'harness_cases_not_planned')
    spec = dict(schema=SPEC_SCHEMA, slot_id=claim['slot_id'], arms=arms,
                harness=dict(commit=claim['harness_commit'], root=str(harness), label_version=version,
                             frozen_sha256=source['frozen_sha256'], labels_sha256=frozen['labels_sha256']),
                embedding=profile['embedding'], postgres=profile['postgres'],
                actor_model=profile['capacity']['models']['CURRENT'],
                cases=WEEKLY_CASES, arm_count=WEEKLY_ARMS, seeds=list(plan['seeds']),
                attempts=WEEKLY_ATTEMPTS, distractors=DISTRACTORS, limits=dict(paired.ORIGINAL_LIMITS),
                capacity=claim['capacity'])
    paired.write_selected(slot / 'spec.json', spec)
    return spec


def _agent_result(path, spec):
    """Check the executor output with the run report's own reader; counts only."""
    try:
        with open(paired.plain_path(str(path)), 'rb') as source:
            content = source.read(AGENT_LIMIT + 1)
        need(len(content) <= AGENT_LIMIT, 'agent_document_limit')
        document = paired.decode_json(content)
        summary = report._agent(document)
    except (paired.Refusal, OSError):
        return dict(status='incomplete', code='agent_document_unavailable')
    pin = dict(path=str(path), sha256=hashlib.sha256(content).hexdigest())
    attempts = dict(planned=summary['planned'], admitted=summary['admitted'],
                    not_started=summary['not_started'], records=len(summary['records']))
    result = dict(agent=pin, attempts=attempts)
    frozen = document.get('frozen') or {}
    if (frozen.get('label_version', 1) != spec['harness']['label_version']
            or frozen.get('labels_sha256') != spec['harness']['labels_sha256']
            or document.get('distractors') != DISTRACTORS or summary['planned'] != WEEKLY_ATTEMPTS
            or not set(ARMS) <= set(document.get('arms') or ())):
        return dict(result, status='incomplete', code='agent_document_not_original')
    if summary['stopped_reason'] == 'capacity_exhausted':
        return dict(result, status='incomplete', code='provider_cap_stop')
    if summary['state'] != 'complete' or summary['admitted'] != WEEKLY_ATTEMPTS or summary['not_started'] \
            or len(summary['records']) != WEEKLY_ATTEMPTS:
        return dict(result, status='incomplete', code='attempts_unfinished')
    return dict(result, status='complete', code=None)


def _finish(slot, claim, *, status, code, agent=None, attempts=None, harness_exit_code=None):
    receipt = dict(claim, schema=RECEIPT_SCHEMA, status=status, code=code, agent=agent, attempts=attempts,
                   harness_exit_code=harness_exit_code,
                   report_input=None if agent is None else dict(schema='cairn.task-eval.agent/1', agent=agent),
                   runtime_admission=False)
    paired.write_selected(slot / 'receipt.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', default=str(DEFAULT_PROFILE))
    parser.add_argument('--now', help='UTC YYYY-MM-DDTHH:MM:SSZ (default: current time)')
    args = parser.parse_args(argv)
    try:
        profile = paired.read_json(paired.plain_path(args.profile))
        now = paired.utc(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
        result = run(profile, now=now)
    except ExecutorBlocked as error:
        result = dict(schema=RECEIPT_SCHEMA, status='refused', code=str(error), blockers=error.blockers,
                      runtime_admission=False)
    except paired.Refusal as error:
        result = dict(schema=RECEIPT_SCHEMA, status='refused', code=str(error), runtime_admission=False)
    except (OSError, ValueError, TypeError, KeyError):
        result = dict(schema=RECEIPT_SCHEMA, status='refused', code='invalid_or_unavailable_input',
                      runtime_admission=False)
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'complete' else 2


if __name__ == '__main__':
    sys.exit(main())
