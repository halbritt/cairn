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
- The opt-in `--one-run-override PATH SHA256` is a separate manual path for one
  selected slot. It bypasses only remaining-allowance and held-reservation
  comparison, records `capacity_unverified`, and always records
  `accepted: false` and `runtime_admission: false`. It does not change the
  scheduled path or establish that the provider can finish the workload.
- The shipped `legacy-harness` and `codex-weekly` executors enforce their
  respective original-limit evidence paths. Neither is selected by default;
  see docs/weekly-runner-reconciliation.md.
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

import trial_account_selection as selection
import trial_codex_account_selection as codex_account
import trial_codex_weekly_limits as codex_limits
import trial_attempt_custody as custody
import trial_capacity_adapter as adapter
import trial_candidate_request as candidate
import trial_embedding_launch as embedding_launch
import trial_legacy_limits as limits
import trial_legacy_case_materials as case_materials
import trial_original_execution as original
import trial_paired_request as paired
import trial_run_report as report
import trial_task_eval as te
import trial_writer_release as writer_release
import trial_one_run_override as one_run_override_contract
import trial_weekly_accounts as weekly_accounts
from check_weekly_paired_eval import WEEKLY_ARMS, WEEKLY_ATTEMPTS, WEEKLY_CASES, WEEKLY_SEEDS

PROFILE_SCHEMA = 'cairn.weekly-run-profile/1'
SPEC_SCHEMA = 'cairn.weekly-run-spec/1'
RECEIPT_SCHEMA = 'cairn.weekly-run-receipt/1'
CLAIM_SCHEMA = 'cairn.weekly-run-claim/1'
SELECTION_SCHEMA = 'cairn.weekly-run-selection/2'
ONE_RUN_SELECTION_SCHEMA = 'cairn.weekly-run-selection/3'
BINDING_SCHEMA = 'cairn.legacy-pair-binding/1'
PROFILE_FIELDS = ('schema repo current_ref candidate_ref harness_commit runs_root campaign_id schedule '
                  'weekly_source embedding postgres capacity executor selection account execution writer')
SELECTION_FIELDS = ('schema campaign_id scheduled_at current candidate harness_commit executor '
                    'capacity_receipt_sha256 writer_sha256 selected_by')
CODEX_SELECTION_FIELDS = (SELECTION_FIELDS+' codex_account_binding_sha256 codex_account_assignment_sha256 '
                          'provider_model provider_binary_sha256')
ONE_RUN_SELECTION_FIELDS = ('schema campaign_id scheduled_at current candidate harness_commit executor '
                            'slot_id expires_at one_run_override_sha256 writer_sha256 '
                            'account_assignment_sha256 account_ids provider_model provider_binary_sha256 selected_by')
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

# What `trial_task_eval.py agent --legacy-fixtures --original-limits` applies
# (tested in test_trial_original_limits_boundary): the original 5 s hook
# timeout, the MCP tool-room arguments, hook context net of the charged task
# wire, and per-line stream timing from the existing capture. The runner
# refuses a harness export without that flag and still re-checks every
# attempt against ORIGINAL_LIMITS (trial_legacy_limits).
REQUIRED_CONFIGURATION = dict(hook_timeout_seconds=limits.hook_timeout_seconds(),
                              mcp_tool_room=limits.mcp_tool_room_arguments(),
                              task_wire_charged=True, stream_timing=True)
LEGACY_CONFIGURATION = dict(REQUIRED_CONFIGURATION)  # only with --original-limits in legacy_argv
# Provider routes the harness process must never inherit (subscription only).
STRIPPED_ENVIRONMENT = ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'OPENAI_API_KEY',
                        'OPENAI_BASE_URL', 'OPENROUTER_API_KEY', 'OPENROUTER_BASE_URL')
ACCOUNT_FIELDS = 'account_id config_home'


def legacy_blockers(configuration=LEGACY_CONFIGURATION, account=None):
    """Concrete source blockers of the legacy route, computed from its configuration and selection."""
    blockers = []
    if configuration != REQUIRED_CONFIGURATION:
        blockers.append('original_limits_unenforced')
    if account is None:
        blockers.append('dedicated_account_unbound')
    return blockers


def check_account(account):
    """A selected account: declared id plus a config home checked by metadata only."""
    if account is None:
        return None
    paired.shape(account, ACCOUNT_FIELDS, 'account_selection_invalid')
    need(paired.capacity_name(account['account_id']), 'account_selection_invalid')
    selection.selected_credentials(account['config_home'])
    return account


def legacy_argv(spec, output):
    """The original harness command for one weekly spec; runs nothing."""
    seeds = spec['seeds']
    need(seeds == list(range(seeds[0], seeds[0] + len(seeds))), 'legacy_seed_list')
    argv = [str(Path(spec['harness']['root']) / 'scripts/trial-task-eval.sh'), 'agent', '--legacy-fixtures',
            '--original-limits', '--output', str(output), '--arms']
    for name in ARMS:
        arm = spec['arms'][name]
        argv += ['--memory', f"{name}:{arm['binary']['path']}:{arm['hook']['path']}"]
    return argv + ['--distractors', str(spec['distractors']), '--seeds', str(len(seeds)),
                   '--first-seed', str(seeds[0]), '--embedding-worker', spec['embedding']['worker']['path'],
                   '--harness', 'claude', '--model', spec['actor_model'],
                   '--max-turns', '40', '--timeout', '900', '--parallel', '3',
                   *original.harness_flags(spec['original_execution'], spec['original_arms'])]


def legacy_environment(spec, base=None):
    """Harness environment: PostgreSQL binaries and the selected account; no API-key or OpenRouter route.

    Any inherited account selection is dropped; only the spec's selection is passed.
    """
    base = os.environ if base is None else base
    env = {k: v for k, v in base.items() if k not in STRIPPED_ENVIRONMENT and not k.startswith('CAIRN_')}
    need(spec.get('account') is not None, 'dedicated_account_unbound')
    return dict(env, CAIRN_PG_BIN=spec['postgres']['bin_dir'], PYTHONDONTWRITEBYTECODE='1',
                **{selection.ENVIRONMENT: spec['account']['config_home']})


def codex_environment(spec, base=None):
    base=os.environ if base is None else base
    env={k:v for k,v in base.items() if k not in STRIPPED_ENVIRONMENT and not k.startswith('CAIRN_')}
    return dict(env,CAIRN_PG_BIN=spec['postgres']['bin_dir'],PYTHONDONTWRITEBYTECODE='1')


class LegacyHarnessExecutor:
    """Runs `trial-task-eval.sh agent --legacy-fixtures` from the exported harness commit.

    `check` validates the selected account by reference and refuses while any
    concrete blocker from `legacy_blockers` applies. `execute` is the real
    invocation; tests exercise it only with a fake harness.
    """

    name = 'legacy-harness'
    limits = paired.ORIGINAL_LIMITS  # checked per attempt by trial_legacy_limits
    configuration = LEGACY_CONFIGURATION

    def __init__(self, account=None):
        self.account = account
        self.account_ids = () if account is None else (account['account_id'],)

    capabilities = original.LEGACY_CAPABILITIES

    def check(self, preview):
        check_account(self.account)
        blockers = legacy_blockers(self.configuration, self.account)
        if preview.get('original_execution') is None:
            blockers.append('original_execution_unbound')
        if blockers:
            raise ExecutorBlocked(blockers)
        # Every originally selected control the harness cannot carry refuses by name.
        original.check_supported(preview['original_execution'], self.capabilities)

    def execute(self, spec, run_dir):
        output = Path(run_dir) / 'agent'
        argv = legacy_argv(spec, output)
        if spec.get('case_materials') is not None:
            argv += ['--case-materials', spec['case_materials']['path'],
                     '--case-materials-sha256', spec['case_materials']['sha256']]
        argv += ['--original-writer-cgroup', spec['writer']['cgroup'],
                 '--original-writer-controls', spec['writer']['controls']['path'],
                 '--original-writer-controls-sha256', spec['writer']['controls']['sha256']]
        env = legacy_environment(spec)
        with open(Path(run_dir) / 'harness.log', 'xb') as log:
            os.chmod(log.name, 0o600)
            done = subprocess.run(argv, cwd=spec['harness']['root'], env=env,
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        # 0 is complete and 2 a provider or observer stop; the runner judges any other status.
        return dict(agent=output / 'agent.json', exit_code=done.returncode)


def default_executors(account=None, account_assignment=None):
    return {LegacyHarnessExecutor.name: LegacyHarnessExecutor(account),
            CodexWeeklyExecutor.name: CodexWeeklyExecutor(account, account_assignment)}


class CodexWeeklyExecutor:
    """Codex executor with a distinct Codex provider pin, account path and JSONL limit reader."""
    name='codex-weekly'
    limits=paired.ORIGINAL_LIMITS
    configuration=dict(codex_jsonl=True,hook_timeout_seconds=paired.ORIGINAL_LIMITS['hook_seconds'],
                       mcp_tool_room=limits['tool_room_bytes'],task_wire_charged=True,stream_timing=True)
    capabilities=original.LEGACY_CAPABILITIES

    def __init__(self,account=None,account_assignment=None):
        self.account=account
        self.account_assignment=account_assignment
        self.account_ids=(tuple(item['account_id'] for item in account_assignment['document']['accounts'])
                          if account_assignment is not None else
                          (() if account is None else (account.get('account_id'),)))

    def check(self,preview):
        blockers=[]
        if self.account_assignment is not None:
            for account in self.account_assignment['document']['accounts']:
                codex_account.verify_auth_file(account['auth_file'], account['auth_sha256'],
                                                expected_account_id=account['account_id'])
        elif self.account is None:
            blockers.append('codex_account_unbound')
        else:
            codex_account.selected_auth(self.account)
        fragment=preview.get('original_execution')
        if fragment is None: blockers.append('original_execution_unbound')
        elif fragment.get('harness')!='codex': blockers.append('codex_provider_contract_mismatch')
        if blockers: raise ExecutorBlocked(blockers)
        original.check_supported(fragment,self.capabilities)
        need(fragment['model']=='gpt-6-luna','codex_model_mismatch')

    def subscription_preflight(self, preview, now):
        if self.account_assignment is None:
            return []
        fragment = preview['original_execution']
        provider = fragment['provider']
        observed_at = candidate._iso(now)
        return [dict(weekly_accounts_status=codex_account.subscription_preflight(
                        account, provider, fragment['model']), observed_at=observed_at)
                for account in self.account_assignment['document']['accounts']]

    def subscription_postflight(self, spec):
        """Capture one read-only usage snapshot per selected account after actor execution."""
        if self.account_assignment is None:
            return []
        rows = []
        for account in self.account_assignment['document']['accounts']:
            try:
                provider = spec['original_execution']['provider']
                rows.append(codex_account.subscription_postflight(account, provider))
            except Exception:
                rows.append(_unavailable_account_postflight(account.get('account_id')))
        return rows

    def execute(self,spec,run_dir):
        output=Path(run_dir)/'agent'
        fragment=spec['original_execution']
        need(fragment.get('harness')=='codex','codex_provider_contract_mismatch')
        argv=[str(Path(spec['harness']['root'])/'scripts/trial-task-eval.sh'),'agent','--legacy-fixtures',
              '--codex-weekly-limits','--output',str(output),'--arms']
        if spec.get('case_materials') is not None:
            argv += ['--case-materials', spec['case_materials']['path'],
                     '--case-materials-sha256', spec['case_materials']['sha256']]
        argv += ['--original-writer-cgroup', spec['writer']['cgroup'],
                 '--original-writer-controls', spec['writer']['controls']['path'],
                 '--original-writer-controls-sha256', spec['writer']['controls']['sha256']]
        for name in ARMS:
            arm=spec['arms'][name]
            argv += ['--memory',f"{name}:{arm['binary']['path']}:{arm['hook']['path']}"]
        auth_account = (self.account_assignment['document']['accounts'][0]
                        if self.account_assignment is not None else self.account)
        need(auth_account is not None, 'codex_account_unbound')
        argv += ['--distractors',str(spec['distractors']),'--seeds',str(len(spec['seeds'])),'--first-seed',str(spec['seeds'][0]),
                 '--embedding-worker',spec['embedding']['worker']['path'],'--harness','codex','--model',spec['actor_model'],
                 '--codex-auth-file',
                 auth_account['auth_file'],'--codex-auth-sha256',auth_account['auth_sha256'],
                 '--timeout','900','--parallel','1',
                 *original.harness_flags(fragment,spec['original_arms'])]
        if spec.get('account_assignment') is not None:
            argv += ['--codex-account-assignment', spec['account_assignment']['path'],
                     '--codex-account-assignment-sha256', spec['account_assignment']['sha256']]
        env=codex_environment(spec)
        with open(Path(run_dir)/'harness.log','xb') as log:
            os.chmod(log.name,0o600)
            done=subprocess.run(argv,cwd=spec['harness']['root'],env=env,stdin=subprocess.DEVNULL,
                                stdout=log,stderr=subprocess.STDOUT)
        return dict(agent=output/'agent.json',exit_code=done.returncode)


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
    need(profile['account'] is None or isinstance(profile['account'], dict), 'account_selection_invalid')
    need(profile['execution'] is None or isinstance(profile['execution'], dict), 'original_execution_unbound')
    check_writer(profile['writer'])
    return repo, runs


def check_writer(writer):
    """Require an explicit reviewed writer boundary; never infer host defaults."""
    paired.shape(writer, 'cgroup controls', 'weekly_writer_unselected')
    cgroup = paired.plain_path(writer['cgroup'])
    need(cgroup.is_dir() and cgroup.resolve() == cgroup, 'weekly_writer_unavailable')
    paired.shape(writer['controls'], 'path sha256', 'weekly_writer_controls_unselected')
    document = paired.read_json_pin(writer['controls'])
    paired.shape(document, 'schema controls reviewed_identity', 'weekly_writer_controls_invalid')
    need(document['schema'] == 'cairn.writer-controls/1' and isinstance(document['controls'], dict) and
         document['reviewed_identity'] is not None, 'weekly_writer_controls_invalid')
    try:
        writer_release.check_controls(document['controls'])
        reviewed = writer_release.check_reviewed(document['reviewed_identity'])
    except (writer_release.WriterUnavailable, TypeError, ValueError):
        raise paired.Refusal('weekly_writer_controls_invalid') from None
    need(reviewed is not None, 'weekly_writer_review_unselected')
    return dict(cgroup=str(cgroup), controls=dict(writer['controls']))


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
    assignment_reference = plan.get('account_assignment')
    if assignment_reference is not None:
        assignment, assignment_reference, _ = weekly_accounts.validate(assignment_reference, plan)
        need({account['account_id'] for account in assignment['accounts']} == set(account_ids),
             'capacity_account_mismatch')
    models = capacity['models']
    paired.capacity_check(receipt['capacity'], plan, now, models)
    evidence = paired.read_json_pin(receipt['capacity'])
    need(paired.utc(evidence['valid_until']) >= slot_end, 'capacity_window_short')
    accounts = paired.capacity_accounts(evidence['account_binding'])
    need(accounts == set(account_ids), 'capacity_account_mismatch')
    need(models['CURRENT'] == models['CAND'], 'capacity_actor_models_differ')
    return dict(receipt_sha256=capacity['receipt']['sha256'], plan_sha256=plan['sha256'],
                input_sha256=receipt['sources']['input_sha256'], accounts=sorted(accounts),
                valid_until=evidence['valid_until'], account_proof=receipt['account_proof'],
                account_assignment=assignment_reference), plan


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
    codex=executor_name==CodexWeeklyExecutor.name
    paired.shape(document, CODEX_SELECTION_FIELDS if codex else SELECTION_FIELDS, 'live_selection_shape')
    need(document['schema'] == SELECTION_SCHEMA, 'live_selection_shape')
    paired.identifier(document['selected_by'])
    expected = dict(campaign_id=profile['campaign_id'], scheduled_at=preview['scheduled_at'],
                    current=preview['current'], candidate=preview['candidate'],
                    harness_commit=preview['harness'], executor=executor_name,
                    capacity_receipt_sha256=capacity['receipt_sha256'],
                    writer_sha256=paired.canonical_sha(profile['writer']))
    if codex:
        fragment=preview['original_execution']
        assignment = capacity.get('account_assignment')
        account_binding = (paired.read_json_pin(assignment) if assignment is not None else profile['account'])
        expected.update(codex_account_binding_sha256=paired.canonical_sha(account_binding),
                        codex_account_assignment_sha256=(assignment['sha256'] if assignment is not None else
                                                         paired.canonical_sha(profile['account'])),
                        provider_model=fragment['model'],provider_binary_sha256=fragment['provider']['sha256'])
    need(all(document[key] == value for key, value in expected.items()), 'live_selection_mismatch')
    return dict(sha256=reference['sha256'], selected_by=document['selected_by'], proof='declared_unauthenticated')


def unreserved_one_run_gate(profile, preview, executor, reference):
    """Validate non-quota identity inputs for a manual slot; claim no capacity.

    The only bypass is quota/workload/reservation pre-admission. This still
    requires the exact original account assignment; the Codex executor then
    checks every selected auth identity and performs the usual status/model
    preflight before the slot claim.
    """
    capacity, plan = profile['capacity'], paired.read_json_pin(profile['capacity']['plan'])
    need(executor.name == CodexWeeklyExecutor.name and isinstance(executor, CodexWeeklyExecutor),
         'one_run_override_codex_required')
    result = one_run_override_contract.validate(
        reference, campaign_id=profile['campaign_id'], slot_id=preview['slot_id'],
        scheduled_at=preview['scheduled_at'], expires_at=preview['expires_at'],
        current=preview['current'], candidate=preview['candidate'], harness_commit=preview['harness'],
        executor=executor.name, plan_reference=capacity['plan'],
        assignment_reference=plan.get('account_assignment'), account_ids=executor.account_ids)
    return result, plan


def one_run_selection_gate(profile, preview, executor_name, capacity):
    """Bind a distinct manual selection to this exact override and reviewed writer pin."""
    reference = profile['selection']
    need(reference is not None, 'live_invocation_not_selected')
    document = paired.read_json_pin(reference)
    codex = executor_name == CodexWeeklyExecutor.name
    need(codex, 'one_run_codex_selection_required')
    paired.shape(document, ONE_RUN_SELECTION_FIELDS, 'one_run_selection_shape')
    need(document['schema'] == ONE_RUN_SELECTION_SCHEMA, 'one_run_selection_shape')
    paired.identifier(document['selected_by'])
    expected = dict(campaign_id=profile['campaign_id'], scheduled_at=preview['scheduled_at'],
                    slot_id=preview['slot_id'], expires_at=preview['expires_at'],
                    current=preview['current'], candidate=preview['candidate'],
                    harness_commit=preview['harness'], executor=executor_name,
                    one_run_override_sha256=capacity['override_sha256'],
                    writer_sha256=paired.canonical_sha(profile['writer']),
                    account_assignment_sha256=capacity['account_assignment_sha256'],
                    account_ids=capacity['accounts'])
    fragment = preview['original_execution']
    expected.update(provider_model=fragment['model'],
                    provider_binary_sha256=fragment['provider']['sha256'])
    need(all(document[key] == value for key, value in expected.items()), 'one_run_selection_mismatch')
    return dict(sha256=reference['sha256'], selected_by=document['selected_by'],
                proof='declared_unauthenticated', mode='manual_one_run_override')


# ---------------------------------------------------------------- run

def run(profile, *, now, executors=None, compiler=None, one_run_override=None):
    """Run or refuse one weekly slot; return its receipt.

    Every refusal before the slot claim writes nothing. A repeated call for a
    recorded slot returns the stored receipt without building or executing.
    """
    repo, runs = check_profile(profile)
    assignment = None
    if profile['executor'] == CodexWeeklyExecutor.name:
        capacity_plan = paired.read_json_pin(profile['capacity']['plan'])
        if capacity_plan.get('account_assignment') is not None:
            allocation, reference, counts = weekly_accounts.validate(capacity_plan['account_assignment'], capacity_plan)
            assignment = dict(document=allocation, reference=reference, pair_counts=counts)
    executors = default_executors(profile.get('account'), assignment) if executors is None else executors
    start, end = current_slot(profile['schedule'], now)
    slot_id = str(uuid.uuid5(NAMESPACE, profile['campaign_id'] + '|' + candidate._iso(start)))
    current = candidate.resolve(repo, profile['current_ref'], 'current_unresolved')
    cand = candidate.resolve(repo, profile['candidate_ref'], 'candidate_unresolved')
    need(cand != current, 'candidate_equals_current')
    harness = candidate.resolve(repo, profile['harness_commit'], 'harness_unresolved')
    need(harness == profile['harness_commit'], 'harness_unresolved')
    # One owner per runs_root from the retry decision through the recorded claim.
    with _runs_lock(runs):
        admitted = _admit(profile, now, executors, runs, slot_id, start, end, current, cand, harness,
                          one_run_override=one_run_override)
    if 'claim' not in admitted:
        return admitted
    slot, claim, plan, executor = admitted['slot'], admitted['claim'], admitted['plan'], admitted['executor']
    return _execute(profile, slot, claim, plan, executor, repo, compiler)


def _runs_lock(runs):
    """Exclusive non-blocking flock on the runs_root directory itself; creates no file."""
    import contextlib
    import fcntl

    @contextlib.contextmanager
    def held():
        descriptor = os.open(runs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise paired.Refusal('weekly_run_in_progress') from None
            yield
        finally:
            os.close(descriptor)  # releases the lock
    return held()


def _admit(profile, now, executors, runs, slot_id, start, end, current, cand, harness,
           *, one_run_override=None):
    """Under the runs lock: stored receipt, or every check, retirement and the recorded claim."""
    fragment = None if profile['execution'] is None else original.declared(
        profile, current=current, candidate=cand, models=profile['capacity']['models'])
    requested_plan = paired.read_json_pin(profile['capacity']['plan'])
    requested_assignment = requested_plan.get('account_assignment')
    if requested_assignment is not None:
        weekly_accounts.validate(requested_assignment, requested_plan)
    stored, retry, previous_claim = _stored_slot(runs, slot_id, current, cand)
    if stored is not None:
        # A replay satisfies only the exact execution contract it recorded. A receipt from
        # before the contract existed, or under another binary, effort or model, never does.
        # A selection that drops a recorded contract does not name the completion either.
        recorded = stored.get('original_execution')
        need((recorded.get('declared') if isinstance(recorded, dict) else None) == fragment,
             'stored_slot_execution_mismatch')
        need(stored.get('writer') == check_writer(profile['writer']), 'stored_slot_writer_mismatch')
        stored_override = (stored.get('capacity') or {}).get('override_sha256')
        if stored_override is not None:
            need(one_run_override is not None, 'stored_slot_override_required')
            paired.pin(one_run_override)
            need(stored_override == one_run_override['sha256'], 'stored_slot_override_mismatch')
        else:
            need(one_run_override is None, 'stored_slot_override_mismatch')
        if profile['executor'] == CodexWeeklyExecutor.name:
            assert_account_slot_binding(stored, requested_plan, requested_assignment,
                                        executors[profile['executor']].account_ids,
                                        'stored_slot_account_assignment_mismatch')
        return stored
    if retry and profile['executor'] == CodexWeeklyExecutor.name:
        assert_account_slot_binding(previous_claim, requested_plan, requested_assignment,
                                    executors[profile['executor']].account_ids,
                                    'retry_account_assignment_mismatch')
    if retry:
        previous_override = ((previous_claim or {}).get('capacity') or {}).get('override_sha256')
        if previous_override is not None:
            need(one_run_override is not None, 'retry_override_required')
            paired.pin(one_run_override)
            need(previous_override == one_run_override['sha256'], 'retry_override_mismatch')
    executor = executors.get(profile['executor'])
    need(executor is not None, 'weekly_executor_unknown')
    preview = dict(slot_id=slot_id, scheduled_at=candidate._iso(start), expires_at=candidate._iso(end),
                   current=current, candidate=cand, harness=harness, limits=dict(paired.ORIGINAL_LIMITS),
                   cases=WEEKLY_CASES, attempts=WEEKLY_ATTEMPTS, distractors=DISTRACTORS,
                   original_execution=fragment)
    executor.check(preview)
    need(executor.limits == paired.ORIGINAL_LIMITS, 'executor_limits_unenforced')
    if one_run_override is None:
        capacity, plan = capacity_gate(profile['capacity'], now=now, slot_end=end, account_ids=executor.account_ids)
        selection = selection_gate(profile, preview, executor.name, capacity)
    else:
        capacity, plan = unreserved_one_run_gate(profile, preview, executor, one_run_override)
        selection = one_run_selection_gate(profile, preview, executor.name, capacity)
    account_preflight = []
    if isinstance(executor, CodexWeeklyExecutor):
        account_preflight = executor.subscription_preflight(preview, now)
    slot = runs / slot_id
    if retry:
        _retire(runs, slot_id)  # only after every check above has passed
    claim = dict(schema=CLAIM_SCHEMA, slot_id=slot_id, campaign_id=profile['campaign_id'],
                 scheduled_at=preview['scheduled_at'], expires_at=preview['expires_at'],
                 current=dict(ref=profile['current_ref'], commit=current),
                 candidate=dict(ref=profile['candidate_ref'], commit=cand), harness_commit=harness,
                 executor=executor.name, capacity=capacity, account_preflight=account_preflight,
                 selection=selection, writer=check_writer(profile['writer']), original_execution=fragment)
    try:
        slot.mkdir(mode=0o700)
    except FileExistsError:
        raise paired.Refusal('weekly_run_in_progress') from None
    paired.write_selected(slot / 'claim.json', claim)
    return dict(slot=slot, claim=claim, plan=plan, executor=executor)


def assert_account_slot_binding(claim, plan, assignment_reference, account_ids, code):
    capacity = (claim or {}).get('capacity') or {}
    need(capacity.get('account_assignment') == assignment_reference
         and capacity.get('plan_sha256') == plan.get('sha256')
         and capacity.get('accounts') == sorted(account_ids), code)


def _execute(profile, slot, claim, plan, executor, repo, compiler):
    slot_id = claim['slot_id']
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
        postflight = _account_postflight(executor, spec)
        return _finish(slot, claim, status='incomplete', code='executor_failed', account_postflight=postflight)
    postflight = _account_postflight(executor, spec)
    result = _agent_result(Path(executed['agent']), spec)
    if executed['exit_code'] not in (0, 2) and result['status'] == 'complete':
        result.update(status='incomplete', code='harness_exit_unexpected')
    plan = result.pop('plan', None)
    if result['status'] == 'complete':
        result['pair_binding'] = _pair_binding(slot, spec, profile['campaign_id'], result['agent'], plan)
    return _finish(slot, claim, harness_exit_code=executed['exit_code'], account_postflight=postflight, **result)


def _unavailable_account_postflight(account_id):
    checked_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    return dict(account_id=account_id, checked_at=checked_at, status=None,
                error='codex_subscription_status_unavailable')


def _account_postflight(executor, spec):
    if not isinstance(executor, CodexWeeklyExecutor):
        return None
    try:
        rows = executor.subscription_postflight(spec)
        if isinstance(rows, list) and len(rows) == len(executor.account_ids):
            return rows
    except Exception:
        pass
    # Postflight is best-effort and cannot prevent the actor's terminal receipt.
    return [_unavailable_account_postflight(account_id) for account_id in executor.account_ids]


def _stored_slot(runs, slot_id, current, cand):
    """Read-only: stored receipt or retry state and its claim; refuse anything ambiguous."""
    slot = runs / slot_id
    if not slot.exists():
        return None, False, None
    try:
        claim = paired.read_json(slot / 'claim.json')
        receipt = paired.read_json(slot / 'receipt.json')
    except (paired.Refusal, OSError):
        raise paired.Refusal('weekly_run_in_progress') from None
    need((claim['current']['commit'], claim['candidate']['commit']) == (current, cand), 'weekly_slot_other_pair')
    if receipt['status'] != 'failed_before_execution' or (slot / 'execution-started.json').exists():
        return dict(receipt, replayed=True), False, claim
    return None, True, claim


def _retire(runs, slot_id):
    """Move a failed-before-execution slot aside for a retry that passed every check."""
    failed = runs / 'failed'
    failed.mkdir(mode=0o700, exist_ok=True)
    attempt = 1 + sum(1 for path in failed.iterdir() if path.name.startswith(slot_id + '.'))
    try:
        (runs / slot_id).rename(failed / f'{slot_id}.{attempt}')
    except OSError:
        raise paired.Refusal('weekly_run_in_progress') from None


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
    # The selected account reaches the actor only through a harness that binds it.
    harness_eval = harness / 'scripts/trial_task_eval.py'
    harness_source = harness_eval.read_text() if harness_eval.is_file() else ''
    need((harness / 'scripts/trial_account_selection.py').is_file()
         and 'selected = credentials_source()' in harness_source, 'harness_account_selector_unsupported')
    if profile['executor']=='codex-weekly':
        need((harness/'scripts/trial_codex_weekly_limits.py').is_file() and '"--codex-weekly-limits"' in harness_source,
             'harness_codex_weekly_limits_unsupported')
        if claim['capacity'].get('account_assignment') is not None:
            need((harness/'scripts/trial_weekly_accounts.py').is_file()
                 and '"--codex-account-assignment"' in harness_source
                 and 'weekly_accounts.account_for' in harness_source,
                 'harness_codex_account_assignment_unsupported')
    else:
        need((harness / 'scripts/trial_legacy_limits.py').is_file() and '"--original-limits"' in harness_source,
             'harness_original_limits_unsupported')
    loaded = te.load_cases(version, root=trial)
    case_ids = [case['id'] for case in loaded]
    need(len(case_ids) == WEEKLY_CASES and sorted(case_ids) == sorted(plan['cases']), 'harness_cases_not_planned')
    # The exact planned attempt identities (legacy run_id is case.arm.sSEED).
    need(all(type(c) is str and c and '.' not in c and '/' not in c for c in case_ids), 'harness_case_identity')
    planned = sorted((dict(run_id=f'{c}.{a}.s{s}', case=c, arm=a, seed=s)
                      for s in plan['seeds'] for c in case_ids for a in ARMS), key=lambda r: r['run_id'])
    assignment_reference = claim['capacity'].get('account_assignment')
    if assignment_reference is not None:
        assignment = paired.read_json_pin(assignment_reference)
        assigned = {row['run_id']: row['account_id'] for row in assignment['assignments']}
        for item in planned:
            item['account_id'] = assigned[item['run_id']]
    need(len(planned) == WEEKLY_ATTEMPTS, 'harness_cases_not_planned')
    material_manifest = None
    if version == case_materials.VERSION:
        material_pin = case_materials.issue(slot / 'case-materials', root=trial)
        material_manifest = dict(schema=material_pin['schema'], path=material_pin['path'],
                                 bytes=material_pin['bytes'], sha256=material_pin['sha256'])
        case_materials.read_manifest(material_manifest, root=trial)
    selected_writer = check_writer(profile['writer'])
    # Per-case task identity and selected-input charge; no task text is retained.
    tasks = {}
    for case in loaded:
        prompt = (case.get('wordings') or {}).get('task')
        need(type(prompt) is str and type(case.get('cwd')) is str and case['cwd'], 'harness_case_identity')
        # cwd is the reviewed case's attempt identity field for its retained baseline.
        tasks[case['id']] = dict(prompt_sha256=hashlib.sha256(prompt.encode('utf-8')).hexdigest(),
                                 input_bytes=limits.input_charge(prompt, te.MEMORY_INSTRUCTION), cwd=case['cwd'])
    fragment = claim['original_execution']
    recall = {name: _recall_blobs(repo, fragment['arms'][name], slot / 'arms' / name / 'recall') for name in ARMS}
    for name in ARMS:
        # The harness mounts the raw recall blobs, not the arm export's working-tree copy.
        arms[name] = dict(arms[name], hook=recall[name]['hook'], bridge=recall[name]['bridge'])
    controls = original.arm_controls(fragment, recall)
    paired.write_selected(slot / 'original-arms.json', controls)
    controls_pin = _pin_file(slot / 'original-arms.json')
    embedding = dict(fragment['embedding'])
    try:
        launch = embedding_launch.prepare(embedding['config'], dict(CURRENT=embedding['worker'], CAND=embedding['worker']),
                                          slot / 'embedding')
    except paired.Refusal as error:
        raise paired.Refusal('original_execution_embedding_' + str(error)) from None
    spec = dict(schema=SPEC_SCHEMA, slot_id=claim['slot_id'], arms=arms,
                harness=dict(commit=claim['harness_commit'], root=str(harness), kind=claim['executor'], label_version=version,
                             frozen_sha256=source['frozen_sha256'], labels_sha256=frozen['labels_sha256']),
                # The retained launcher runs; the declared worker and config stay separate fields.
                embedding=dict(worker=dict(launch['launcher']), declared_worker=dict(launch['declared_worker']),
                               config=dict(launch['config']), selected_config=dict(launch['selected_config']),
                               model_identity=dict(embedding['model_identity']), preparation=launch),
                original_arms=controls_pin, postgres=profile['postgres'],
                actor_model=claim['original_execution']['model'], original_execution=claim['original_execution'],
                cases=WEEKLY_CASES, arm_count=WEEKLY_ARMS, seeds=list(plan['seeds']),
                attempts=WEEKLY_ATTEMPTS, distractors=DISTRACTORS, limits=dict(paired.ORIGINAL_LIMITS),
                capacity=claim['capacity'], planned=planned, schedule_sha256=paired.canonical_sha(planned),
                case_materials=material_manifest,
                writer=selected_writer, tasks=tasks, account=profile['account'])
    if assignment_reference is not None:
        spec['account_assignment'] = assignment_reference
    paired.write_selected(slot / 'spec.json', spec)
    return spec


def _pin_file(path):
    return dict(path=str(path), sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest())


RECALL_SOURCES = (('hook', 'memory.py'), ('bridge', 'inbox_recall.py'))


def _recall_blobs(repo, arm, directory):
    """Retain the raw memory.py and inbox_recall.py blobs at the declared recall revision.

    Git replacement objects are disabled. Each blob must equal the declared hook or
    bridge pin; it is never taken from an export, worktree or a self-reported label.
    """
    env = dict(os.environ, GIT_NO_REPLACE_OBJECTS='1')
    try:
        commit = subprocess.run(['git', '--no-replace-objects', '-C', str(repo), 'rev-parse', '--verify', '--quiet',
                                 '--end-of-options', arm['recall_revision'] + '^{commit}'], env=env, check=True,
                                capture_output=True, timeout=candidate.GIT_TIMEOUT).stdout.decode().strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeDecodeError):
        raise paired.Refusal('original_execution_recall_unresolved') from None
    need(commit == arm['recall_revision'], 'original_execution_recall_unresolved')
    directory.mkdir(mode=0o700, parents=True)
    pins = {}
    for role, name in RECALL_SOURCES:
        try:
            blob = subprocess.run(['git', '--no-replace-objects', '-C', str(repo), 'cat-file', 'blob',
                                   f'{commit}:integrations/lifecycle/{name}'], env=env, check=True,
                                  capture_output=True, timeout=candidate.GIT_TIMEOUT).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise paired.Refusal('original_execution_recall_unresolved') from None
        need(hashlib.sha256(blob).hexdigest() == arm[role]['sha256'], 'original_execution_recall_mismatch')
        paired.write_selected_bytes(directory / name, blob)
        pins[role] = _pin_file(directory / name)
    return pins


def _read_limited(path):
    with open(paired.plain_path(str(path)), 'rb') as source:
        content = source.read(AGENT_LIMIT + 1)
    need(len(content) <= AGENT_LIMIT, 'agent_document_limit')
    return content


def _identity(row):
    return (row.get('run_id'), row.get('case'), row.get('arm'), row.get('seed')) if isinstance(row, dict) else None


def _agent_result(path, spec):
    """Check the executor output against the exact planned attempt set; never trust its counters."""
    try:
        content = _read_limited(path)
        document = paired.decode_json(content)
        summary = report._agent(document)
        plan_content = _read_limited(Path(path).parent / 'plan.json')
        plan = paired.decode_json(plan_content)
    except (paired.Refusal, OSError):
        return dict(status='incomplete', code='agent_document_unavailable')
    pin = dict(path=str(path), sha256=hashlib.sha256(content).hexdigest())
    records = document.get('records') or []
    attempts = dict(planned=summary['planned'], admitted=summary['admitted'],
                    not_started=summary['not_started'], records=len(records))
    result = dict(agent=pin, attempts=attempts)
    frozen = document.get('frozen') or {}
    if (frozen.get('label_version', 1) != spec['harness']['label_version']
            or frozen.get('labels_sha256') != spec['harness']['labels_sha256']
            or document.get('distractors') != DISTRACTORS or summary['planned'] != WEEKLY_ATTEMPTS
            or document.get('arms') != list(ARMS) or document.get('model') != spec['actor_model']
            or document.get('wording') != 'task'):
        return dict(result, status='rejected', code='agent_document_not_original')
    planned = {(r['run_id'], r['case'], r['arm'], r['seed']) for r in spec['planned']}
    observed = [_identity(r) for r in records]
    unstarted = [_identity(r) for r in document['admission']['not_started']]
    rows = observed + [row for row in unstarted if row is not None and row[1:] != (None, None, None)]
    unstarted_ids = {row[0] for row in unstarted if row is not None}
    # Records and unstarted rows together must be exactly the plan, each once.
    if (len(set(observed)) != len(observed) or not set(observed) <= planned
            or len(unstarted_ids) != len(unstarted)
            or {row[0] for row in observed} & unstarted_ids
            or {row[0] for row in observed} | unstarted_ids != {row[0] for row in planned}
            or not set(rows) <= planned
            or not isinstance(plan, dict) or plan.get('arms') != list(ARMS)
            or sorted(tuple(row[:3]) for row in plan.get('runs') or [] if isinstance(row, list))
            != sorted((r['case'], r['arm'], r['seed']) for r in spec['planned'])):
        return dict(result, status='rejected', code='agent_rows_not_planned')
    assignment_reference = spec.get('account_assignment')
    if assignment_reference is not None:
        try:
            assignment = paired.read_json_pin(assignment_reference)
        except paired.Refusal:
            return dict(result, status='rejected', code='agent_account_assignment_mismatch')
        auth_by_id = {row['account_id']: row['auth_sha256'] for row in assignment['accounts']}
        account_by_run = {row['run_id']: row['account_id'] for row in spec['planned']}
        expected_rows = sorted((dict(run_id=run_id, account_id=account_id)
                                for run_id, account_id in account_by_run.items()), key=lambda row: row['run_id'])
        actual_rows = plan.get('run_accounts')
        actual_rows = (sorted(actual_rows, key=lambda row: row.get('run_id', '') if isinstance(row, dict) else '')
                       if isinstance(actual_rows, list) else None)
        if (plan.get('account_assignment_sha256') != assignment_reference['sha256']
                or actual_rows != expected_rows
                or any(row.get('account_id') != account_by_run.get(row.get('run_id'))
                       or row.get('account_auth_sha256') != auth_by_id.get(row.get('account_id'))
                       for row in records)):
            return dict(result, status='rejected', code='agent_account_assignment_mismatch')
    # Read from the raw rows: report._agent drops these fields. A runner-exception
    # row without wording is rejected too; it could never be complete.
    selected = dict(model=spec['actor_model'], harness='codex' if spec['harness'].get('kind')=='codex-weekly' else 'claude', wording='task')
    if not all(record.get(key) == value for record in records for key, value in selected.items()):
        return dict(result, status='rejected', code='agent_row_not_selected')
    if spec.get('case_materials') is not None:
        selected_materials = dict(schema=case_materials.SCHEMA, sha256=spec['case_materials']['sha256'])
        try:
            case_materials.read_manifest(spec['case_materials'],
                                         root=Path(spec['harness']['root']) / TRIAL_PATH,
                                         evaluator_sha256=plan.get('evaluator_sha256'))
        except paired.Refusal:
            return dict(result, status='rejected', code='agent_case_materials_unavailable')
        if plan.get('case_materials') != selected_materials or document.get('case_materials') != selected_materials:
            return dict(result, status='rejected', code='agent_case_materials_mismatch')
    result['plan'] = dict(path=str(Path(path).parent / 'plan.json'), sha256=hashlib.sha256(plan_content).hexdigest())
    if summary['stopped_reason'] == 'capacity_exhausted':
        return dict(result, status='incomplete', code='provider_cap_stop')
    if summary['state'] != 'complete' or summary['admitted'] != WEEKLY_ATTEMPTS or unstarted \
            or len(observed) != WEEKLY_ATTEMPTS:
        return dict(result, status='incomplete', code='attempts_unfinished')
    if summary['measurement_failures'] or any(row['provider'] is not None or row['outcome'] in report.FAILED_OUTCOMES
                                              or row['outcome'] == 'other' for row in summary['records']):
        return dict(result, status='incomplete', code='attempt_failures')
    if not all(type(r.get(k)) is str and len(r[k]) == 64 for r in records for k in ('stream_sha256', 'prompt_sha256')):
        return dict(result, status='incomplete', code='original_attempt_provenance_unavailable')
    mismatch = original.check_observed(plan, records, spec['original_execution'])
    if mismatch is not None:
        return dict(result, status=mismatch[0], code=mismatch[1])
    result['original_execution'] = dict(declared=spec['original_execution'], observed=original.observed(plan),
                                        proof='harness_observed_copy')
    return _limit_result(result, Path(path).parent, records, spec)


def _limit_result(result, run_dir, records, spec):
    """Check every attempt against ORIGINAL_LIMITS; an exceeding or unmeasured row is never complete."""
    counts, reasons = dict(within_original_limits=0, failed=0, unknown=0), {}
    for record in records:
        task = spec['tasks'][record['case']]
        if record['prompt_sha256'] != task['prompt_sha256']:
            return dict(result, status='rejected', code='task_prompt_mismatch')
        base = run_dir / 'runs' / record['run_id']
        # The retained pre-actor baseline must exist, match its pin and file policy, and
        # name exactly this attempt (trial_attempt_custody.AttemptBaseline.load). Never rebuilt.
        if 'baseline_custody' not in record:
            return dict(result, status='incomplete', code='attempt_baseline_missing')
        try:
            custody.AttemptBaseline.load(base, dict(id=record['case'], cwd=task['cwd']), record['arm'],
                                         record['seed'], record['baseline_custody'])
        except (custody.BaselineError, TypeError, ValueError, KeyError, RecursionError):
            # Malformed retained data only (wrong JSON shape, scalar, null, invalid or
            # too-deep JSON); OS and process failures are not caught here.
            return dict(result, status='rejected', code='attempt_baseline_invalid')
        try:
            stream = _read_limited(base / 'stream.jsonl')
            hooks = base / 'hook-state/observations.jsonl'
            rows = [json.loads(line) for line in _read_limited(hooks).decode().splitlines()] if hooks.exists() else []
            timing = base / 'stream-timing.json'
            timings = json.loads(_read_limited(timing)) if timing.exists() else None
        except (paired.Refusal, OSError, ValueError):
            return dict(result, status='incomplete', code='original_limit_unobserved')
        if hashlib.sha256(stream).hexdigest() != record['stream_sha256']:
            return dict(result, status='rejected', code='attempt_content_mismatch')
        checker=codex_limits if spec['harness'].get('kind')=='codex-weekly' else limits
        assessment = checker.assess(stream.decode('utf-8', errors='replace'), rows, input_bytes=task['input_bytes'],
                                    timings=timings)
        category='within_original_limits' if assessment['status'].startswith('within_') else assessment['status']
        counts[category] += 1
        for reason in assessment['failures'] + assessment['unknown']:
            reasons[reason] = reasons.get(reason, 0) + 1
    result = dict(result, limit_assessment=dict(counts, reasons=dict(sorted(reasons.items())),
                                                limits=dict(paired.ORIGINAL_LIMITS)),
                  baseline_custody=dict(verified=len(records), proof='harness_retained_baseline'))
    if counts['failed']:
        return dict(result, status='incomplete', code='original_limit_exceeded')
    if counts['unknown']:
        return dict(result, status='incomplete', code='original_limit_unobserved')
    return dict(result, status='complete', code=None)


def _pair_binding(slot, spec, campaign_id, agent, plan):
    """Issue the declared cairn.legacy-pair-binding/1 for a complete run of exactly the planned attempts."""
    spec_bytes = (slot / 'spec.json').read_bytes()
    binding = dict(schema=BINDING_SCHEMA, agent_sha256=agent['sha256'], plan_sha256=plan['sha256'],
                   request_id=spec['slot_id'], request_sha256=hashlib.sha256(spec_bytes).hexdigest(),
                   campaign_id=campaign_id,
                   occurrence_id=str(uuid.uuid5(NAMESPACE, 'occurrence|' + spec['slot_id'])),
                   schedule_sha256=spec['schedule_sha256'], arm_roles={name: name for name in ARMS})
    content = paired.selected_bytes(binding)
    paired.write_selected_bytes(slot / 'pair-binding.json', content)
    return dict(path=str(slot / 'pair-binding.json'), bytes=len(content), sha256=hashlib.sha256(content).hexdigest())


def _finish(slot, claim, *, status, code, agent=None, attempts=None, harness_exit_code=None, pair_binding=None,
            limit_assessment=None, baseline_custody=None, original_execution=None, account_postflight=None):
    receipt = dict(claim, schema=RECEIPT_SCHEMA, status=status, code=code, agent=agent, attempts=attempts,
                   harness_exit_code=harness_exit_code, pair_binding=pair_binding, limit_assessment=limit_assessment,
                   baseline_custody=baseline_custody,
                   original_execution=original_execution or dict(declared=claim['original_execution'], observed=None,
                                                                 proof=None),
                   pair_identity=None if pair_binding is None else 'declared_binding',
                   report_input=None if agent is None else dict(schema='cairn.task-eval.agent/1', agent=agent),
                   runtime_admission=False)
    if account_postflight is not None:
        receipt['account_postflight'] = account_postflight
    if claim.get('capacity', {}).get('mode') == 'manual_one_run_override':
        receipt.update(capacity_status='capacity_unverified', accepted=False)
    paired.write_selected(slot / 'receipt.json', receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', default=str(DEFAULT_PROFILE))
    parser.add_argument('--now', help='UTC YYYY-MM-DDTHH:MM:SSZ (default: current time)')
    parser.add_argument('--one-run-override', nargs=2, metavar=('PATH', 'SHA256'),
                        help='explicit manual one-slot override pin; never used by the scheduled unit')
    args = parser.parse_args(argv)
    try:
        profile = paired.read_json(paired.plain_path(args.profile))
        now = paired.utc(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
        override_reference = None
        if args.one_run_override is not None:
            override_reference = dict(path=str(Path(args.one_run_override[0]).absolute()),
                                      sha256=args.one_run_override[1])
        result = run(profile, now=now, one_run_override=override_reference)
    except original.Unsupported as error:
        result = dict(schema=RECEIPT_SCHEMA, status='refused', code=str(error), unsupported=error.fields,
                      runtime_admission=False)
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
