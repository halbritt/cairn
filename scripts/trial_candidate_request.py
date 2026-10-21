"""Candidate-branch trigger for the weekly paired evaluation queue.

Given a candidate ref, this builds a pinned CAND arm and a pinned CURRENT arm
from exact commits, declares a disposable PostgreSQL cluster and the shared
embedding worker, gates on a usage pre-check, and queues a reviewable weekly
draft through the existing `trial_weekly_draft` producer. It never launches a
provider, model, actor or store; `launch` refuses fail-closed because no runtime
launcher exists. `prepare` joins a reviewed entry to the existing weekly
check/prepare contracts; reviews and companion selection stay external.
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid

import trial_paired_request as paired
import trial_weekly_request as dispatch
from check_weekly_paired_eval import (REQUIRED_ROLES as ROLES, WEEKLY_ATTEMPTS, WEEKLY_PAIRS,
                                      check_usage_precheck)

PROFILE_SCHEMA = 'cairn.paired-candidate-profile/1'
ENTRY_SCHEMA = 'cairn.weekly-candidate-entry/1'
DEFAULT_PROFILE = Path.home()/'.config/cairn/paired-candidate.json'
PROFILE_FIELDS = ('schema repo current_ref queue campaign_id weekly_source native seeds workload '
                  'embedding recall postgres schedule proposal usage')
PG_TOOLS = ('initdb', 'pg_ctl', 'postgres', 'psql')
HOOK = 'integrations/lifecycle/memory.py'
BRIDGE = 'integrations/lifecycle/inbox_recall.py'
GIT_TIMEOUT = 120
BUILD_TIMEOUT = 900
ARCHIVE_LIMIT = 512 * 1024 * 1024


class PrecheckRefusal(paired.Refusal):
    """Usage gate refusal carrying the path-free pre-check summary."""
    def __init__(self, detail):
        super().__init__('usage_precheck_refused')
        self.detail = detail


# ---------------------------------------------------------------- profile

def _int(value, low, high=2**31 - 1):
    return type(value) is int and low <= value <= high


def check_profile(profile):
    paired.shape(profile, PROFILE_FIELDS, 'profile_shape')
    paired.need(profile['schema'] == PROFILE_SCHEMA, 'profile_schema')
    repo = paired.plain_path(profile['repo'])
    paired.need((repo/'.git').exists(), 'profile_repo')
    paired.need(type(profile['current_ref']) is str and _safe_ref(profile['current_ref']), 'profile_current_ref')
    queue = paired.plain_path(profile['queue'])
    paired.need(queue.is_dir() and queue.stat().st_mode & 0o077 == 0, 'private_queue_required')
    paired.identifier(profile['campaign_id'])
    paired.shape(profile['weekly_source'], 'root label_version frozen_sha256', 'profile_weekly_source')
    paired.shape(profile['native'], 'binary model effort', 'profile_native')
    paired.pin(profile['native']['binary'])
    seeds = profile['seeds']
    paired.need(type(seeds) is list and len(seeds) == 3 and all(_int(s, 0) for s in seeds)
                and len(set(seeds)) == 3, 'profile_seeds')
    paired.pin(profile['workload'])
    paired.shape(profile['embedding'], 'worker config model_identity', 'profile_embedding')
    for value in profile['embedding'].values():
        paired.pin(value)
    paired.shape(profile['recall'], 'recall_mode semantic_fallback', 'profile_recall')
    paired.need(profile['recall']['recall_mode'] in ('ambient', 'agent_tools')
                and type(profile['recall']['semantic_fallback']) is bool, 'profile_recall')
    # Only a binary directory: a DSN, host or existing data directory is refused by shape.
    paired.shape(profile['postgres'], 'bin_dir', 'profile_postgres_shape')
    bin_dir = paired.plain_path(profile['postgres']['bin_dir'])
    paired.need(all((bin_dir/tool).is_file() and os.access(bin_dir/tool, os.X_OK) for tool in PG_TOOLS),
                'postgres_binaries_unavailable')
    schedule = profile['schedule']
    paired.shape(schedule, 'weekday time window_minutes', 'profile_schedule')
    paired.need(_int(schedule['weekday'], 0, 6) and type(schedule['time']) is str
                and re.fullmatch('([01][0-9]|2[0-3]):[0-5][0-9]', schedule['time'])
                and _int(schedule['window_minutes'], 1, 7 * 24 * 60), 'profile_schedule')
    proposal = profile['proposal']
    paired.shape(proposal, 'capacity grading supervisor_source permission_profile reply_recipient_id',
                 'profile_proposal')
    paired.identifier(proposal['reply_recipient_id'])
    usage = profile['usage']
    paired.shape(usage, 'observation quota_snapshot max_age_seconds margin_percent', 'profile_usage')
    paired.plain_path(usage['quota_snapshot'])
    paired.need(_int(usage['max_age_seconds'], 1, 7 * 24 * 3600) and _int(usage['margin_percent'], 100, 1000),
                'profile_usage')
    return repo, queue, bin_dir


def _safe_ref(ref):
    return 0 < len(ref) <= 256 and not ref.startswith('-') and '\0' not in ref and not re.search(r'\s', ref)


# ---------------------------------------------------------------- git and builds

def _git(repo, *args, **kwargs):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True,
                          timeout=GIT_TIMEOUT, **kwargs).stdout


def resolve(repo, ref, code):
    paired.need(type(ref) is str and _safe_ref(ref), code)
    try:
        commit = _git(repo, 'rev-parse', '--verify', '--quiet', '--end-of-options', ref + '^{commit}').decode().strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, UnicodeDecodeError):
        raise paired.Refusal(code) from None
    paired.need(re.fullmatch('[0-9a-f]{40}', commit) is not None, code)
    return commit


def export_commit(repo, commit, destination):
    """Export exactly one commit's tree; no refs, hooks or worktree state."""
    archive = _git(repo, 'archive', '--format=tar', commit)
    paired.need(len(archive) <= ARCHIVE_LIMIT, 'arm_archive_limit')
    destination.mkdir(mode=0o700, parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(destination, filter='data')


def go_build(source, output):
    """Default compiler: local build of the exported cmd/cairn.

    The export has no .git, so VCS stamping is disabled; the request pins the
    exact commit and binary hash instead.
    """
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = dict(os.environ, GOFLAGS='-mod=readonly', GOWORK='off')
    subprocess.run(['go', 'build', '-trimpath', '-buildvcs=false', '-o', str(output), './cmd/cairn'], cwd=source, env=env,
                   check=True, capture_output=True, timeout=BUILD_TIMEOUT)


def _file_pin(path):
    paired.need(path.is_file() and not path.is_symlink(), 'arm_component_missing')
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def build_arm(repo, commit, destination, compiler):
    source = destination/'src'
    try:
        export_commit(repo, commit, source)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, tarfile.TarError, OSError):
        raise paired.Refusal('arm_export_failed') from None
    hook, bridge = _file_pin(source/HOOK), _file_pin(source/BRIDGE)
    binary = destination/'bin/cairn'
    try:
        compiler(source, binary)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        raise paired.Refusal('arm_build_failed') from None
    paired.need(binary.is_file(), 'arm_build_failed')
    return dict(commit=commit, binary=_file_pin(binary), hook=hook, bridge=bridge)


# ---------------------------------------------------------------- schedule and usage

def next_occurrence(schedule, now):
    """First weekly slot strictly after `now`, in UTC, and its window end."""
    hour, minute = map(int, schedule['time'].split(':'))
    day = now.astimezone(dt.timezone.utc).replace(hour=hour, minute=minute, second=0, microsecond=0)
    day += dt.timedelta(days=(schedule['weekday'] - day.weekday()) % 7)
    if day <= now:
        day += dt.timedelta(days=7)
    return day, day + dt.timedelta(minutes=schedule['window_minutes'])


def _iso(moment):
    return moment.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def check_observation(observation):
    try:
        paired.shape(observation, 'schema source_run roles', 'usage_observation_invalid')
        paired.need(observation['schema'] == 'cairn.weekly-usage-observation/1'
                    and type(observation['source_run']) is str and observation['source_run'], 'usage_observation_invalid')
        paired.shape(observation['roles'], ' '.join(ROLES), 'usage_observation_invalid')
        for role in observation['roles'].values():
            paired.shape(role, 'attempts tokens requests', 'usage_observation_invalid')
            paired.need(_int(role['attempts'], 1, 2**62) and _int(role['tokens'], 0, 2**62)
                        and _int(role['requests'], 0, 2**62), 'usage_observation_invalid')
    except paired.Refusal:
        raise paired.Refusal('usage_observation_invalid') from None


def plan_demand(observation, *, margin_percent):
    """Scale the last run's per-attempt usage to the whole 126-attempt plan.

    Each actor role runs one arm's 63 attempts; each grader role is budgeted for
    all 63 pairs. The margin is a whole percentage applied with ceiling division.
    """
    check_observation(observation)
    def scaled(total, attempts):
        return -(-total * WEEKLY_PAIRS * margin_percent // (attempts * 100))
    roles = {name: dict(required_tokens=scaled(role['tokens'], role['attempts']),
                        required_requests=scaled(role['requests'], role['attempts']))
             for name, role in observation['roles'].items()}
    return dict(cases=21, arms=2, seeds=3, attempts=WEEKLY_ATTEMPTS, pairs=WEEKLY_PAIRS, roles=roles)


def read_quota_snapshot(path, *, now, max_age_seconds):
    """Absolute remaining counts only; a percentage or stale/missing role refuses."""
    try:
        snapshot = paired.read_json(path)
        fields = set(snapshot) if isinstance(snapshot, dict) else set()
        paired.need(fields in ({'schema', 'observed_at', 'roles'}, {'schema', 'observed_at', 'roles', 'weekly_pool'})
                    and snapshot['schema'] == 'cairn.provider-quota-snapshot/1', 'shape')
        observed = paired.utc(snapshot['observed_at'])
        paired.need(observed <= now and (now - observed).total_seconds() <= max_age_seconds, 'stale')
        paired.shape(snapshot['roles'], ' '.join(ROLES), 'roles')
        for value in [*snapshot['roles'].values(), *([snapshot['weekly_pool']] if 'weekly_pool' in snapshot else [])]:
            paired.shape(value, 'remaining_tokens remaining_requests http_429_active', 'role')
            paired.need(_int(value['remaining_tokens'], 0, 2**62) and _int(value['remaining_requests'], 0, 2**62)
                        and type(value['http_429_active']) is bool, 'role')
        return snapshot
    except (paired.Refusal, OSError):
        raise paired.Refusal('usage_snapshot_unavailable') from None


def usage_precheck(profile, now):
    usage = profile['usage']
    demand = plan_demand(paired.read_json_pin(usage['observation']), margin_percent=usage['margin_percent'])
    snapshot = read_quota_snapshot(paired.plain_path(usage['quota_snapshot']), now=now,
                                   max_age_seconds=usage['max_age_seconds'])
    result = check_usage_precheck(demand, snapshot)
    if not result['admitted']:
        raise PrecheckRefusal(result)
    return result


# ---------------------------------------------------------------- disposable environment

def environment(entry, bin_dir, embedding, arms):
    """Declare the disposable cluster the weekly owner creates; nothing is started."""
    return dict(schema='cairn.weekly-candidate-environment/1',
                postgres=dict(bin_dir=str(bin_dir), cluster_root=str(entry/'cluster'), listen_addresses='',
                              auth_local='trust', auth_host='reject', max_connections=200,
                              initdb=['--auth-local=trust', '--auth-host=reject', '--no-locale', '-E', 'UTF8']),
                embedding=embedding, arms={name: dict(commit=arm['commit'], binary=arm['binary'])
                                           for name, arm in arms.items()})


def smoke_postgres(entry, bin_dir):
    """Create, query and remove one disposable cluster owned by this call."""
    root = entry/'pg-smoke'
    socket = Path(tempfile.mkdtemp(prefix='cairn-pg-'))
    started = False
    try:
        root.mkdir(mode=0o700)
        run = lambda *args: subprocess.run([str(a) for a in args], check=True, capture_output=True, timeout=120)
        run(bin_dir/'initdb', '-D', root/'data', '--auth-local=trust', '--auth-host=reject', '--no-locale', '-E', 'UTF8')
        run(bin_dir/'pg_ctl', '-D', root/'data', '-l', root/'postgres.log',
            '-o', f"-F -k {socket} -c listen_addresses=''", '-w', 'start')
        started = True
        value = run(bin_dir/'psql', '-h', socket, '-d', 'postgres', '-Atc', 'select 1').stdout.decode().strip()
        paired.need(value == '1', 'postgres_smoke_failed')
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        raise paired.Refusal('postgres_smoke_failed') from None
    finally:
        stopped = True
        if started:
            stopped = subprocess.run([str(bin_dir/'pg_ctl'), '-D', str(root/'data'), '-m', 'immediate', '-w', 'stop'],
                                     capture_output=True, timeout=120).returncode == 0
        if stopped:
            shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(socket, ignore_errors=True)
    return dict(status='passed', query='select 1')


# ---------------------------------------------------------------- request

NAMESPACE = uuid.UUID('6f0c7a52-3d0e-5b8e-9a51-0c107b7a1e07')


def occurrence_ids(campaign_id, scheduled_at, candidate, current):
    """Deterministic IDs for one candidate/current pair in one weekly occurrence."""
    key = '|'.join((campaign_id, scheduled_at, candidate, current))
    return {name: str(uuid.uuid5(NAMESPACE, name + '|' + key))
            for name in ('request', 'occurrence', 'event', 'correlation')}


def request(profile, candidate_ref, *, now, compiler=None, smoke_postgres=False):
    """Queue one candidate-vs-current weekly draft, or refuse before queue writes.

    IDs are derived from campaign, occurrence and both commits, so repeating a
    request for an already queued pair returns the stored receipt without a
    rebuild. A failed attempt is moved under `failed/` and may be retried.
    """
    compiler = compiler or go_build
    repo, queue, bin_dir = check_profile(profile)
    current = resolve(repo, profile['current_ref'], 'current_unresolved')
    cand = resolve(repo, candidate_ref, 'candidate_unresolved')
    paired.need(cand != current, 'candidate_equals_current')
    start, end = next_occurrence(profile['schedule'], now)
    ids = occurrence_ids(profile['campaign_id'], _iso(start), cand, current)
    entry = queue/ids['request']
    if entry.exists():
        stored = _entry(entry)
        paired.need(stored is not None, 'candidate_request_in_progress')
        if stored['state'] != 'failed':
            return dict(stored['receipt'], status='already_queued', state=stored['state'])
        failed = queue/'failed'
        failed.mkdir(mode=0o700, exist_ok=True)
        attempt = 1 + sum(1 for p in failed.iterdir() if p.name.startswith(entry.name + '.'))
        entry.rename(failed/f'{entry.name}.{attempt}')
    precheck = usage_precheck(profile, now)

    try:
        entry.mkdir(mode=0o700)
    except FileExistsError:
        raise paired.Refusal('candidate_request_in_progress') from None
    record = dict(schema=ENTRY_SCHEMA, request_id=ids['request'], occurrence_id=ids['occurrence'],
                  campaign_id=profile['campaign_id'], scheduled_at=_iso(start), expires_at=_iso(end),
                  created_at=_iso(now), candidate=dict(ref=candidate_ref, commit=cand),
                  current=dict(ref=profile['current_ref'], commit=current),
                  precheck=dict(code=precheck['code'], totals=precheck['totals']))
    try:
        receipt = _queue(profile, entry, record, ids, repo, bin_dir, current, cand, compiler, smoke_postgres, now)
    except paired.Refusal as error:
        paired.write_selected(entry/'entry.json', dict(record, state='failed', code=str(error)))
        raise
    except (OSError, ValueError, TypeError, KeyError):
        paired.write_selected(entry/'entry.json', dict(record, state='failed', code='candidate_request_failed'))
        raise paired.Refusal('candidate_request_failed') from None
    paired.write_selected(entry/'entry.json', dict(record, state='queued_for_review', receipt=receipt))
    return receipt


def _entry(entry):
    try:
        return paired.read_json(entry/'entry.json')
    except (paired.Refusal, OSError):
        return None


def _queue(profile, entry, record, ids, repo, bin_dir, current, cand, compiler, smoke, now):
    import trial_task_eval
    import trial_weekly_draft
    from trial_weekly_input import EXECUTION_SCHEMA, REFERENCE_SCHEMA, generated_distractors

    arms = {name: build_arm(repo, commit, entry/'arms'/name, compiler)
            for name, commit in (('CURRENT', current), ('CAND', cand))}
    embedding = profile['embedding']
    memory_arms = {name: dict(binary=arm['binary'], hook=arm['hook'], bridge=arm['bridge'],
                              api_revision=arm['commit'], recall_revision=arm['commit'],
                              recall_mode=profile['recall']['recall_mode'],
                              semantic_fallback=profile['recall']['semantic_fallback'],
                              embedding_worker=embedding['worker'], embedding_config=embedding['config'],
                              embedding_model_identity=embedding['model_identity'])
                   for name, arm in arms.items()}
    source = profile['weekly_source']
    generator = Path(trial_task_eval.__file__).resolve()
    execution = dict(schema=EXECUTION_SCHEMA, weekly_source=dict(source), actor_api_base=current,
                     native=profile['native'], arms=['CURRENT', 'CAND'], memory_arms=memory_arms,
                     seeds=profile['seeds'], wording='task', workload=profile['workload'],
                     distractors=dict(count=1900, seed=20260928, generator_source=_file_pin(generator),
                                      generated_sha256=generated_distractors()['sha256']))
    paired.write_selected(entry/'execution.json', execution)
    env = environment(entry, bin_dir, embedding, arms)
    if smoke:
        env['smoke'] = smoke_postgres(entry, bin_dir)
    paired.write_selected(entry/'environment.json', env)
    proposal = dict(schema=trial_weekly_draft.SCHEMA, request_id=record['request_id'],
                    campaign_id=record['campaign_id'], occurrence_id=record['occurrence_id'],
                    scheduled_at=record['scheduled_at'], expires_at=record['expires_at'],
                    weekly_input=dict(schema=REFERENCE_SCHEMA, **source, execution=_file_pin(entry/'execution.json')),
                    capacity=profile['proposal']['capacity'], grading=profile['proposal']['grading'],
                    output=str(entry/'preparation'),
                    reply=dict(event_id=ids['event'], recipient_id=profile['proposal']['reply_recipient_id'],
                               correlation_id=ids['correlation']),
                    supervisor_source=profile['proposal']['supervisor_source'],
                    permission_profile=profile['proposal']['permission_profile'])
    paired.write_selected(entry/'proposal.json', proposal)
    draft = trial_weekly_draft.draft(proposal, entry/'draft', now=now)
    return dict(schema='cairn.weekly-candidate-request/1', status='queued_for_review',
                request_id=record['request_id'], occurrence_id=record['occurrence_id'],
                scheduled_at=record['scheduled_at'], expires_at=record['expires_at'],
                arms={name: dict(commit=arm['commit'], binary_sha256=arm['binary']['sha256'])
                      for name, arm in arms.items()},
                precheck=record['precheck'] | dict(admitted=True), draft=draft, launch=launch_status(),
                next='independent reviews of draft/review-subjects.json, then trial_weekly_request check/prepare',
                runtime_admitted=False)


def launch_status():
    try:
        dispatch.launch(None)
    except paired.Refusal as error:
        return dict(status='refused', code=str(error))
    raise AssertionError('weekly launch unexpectedly available')  # pragma: no cover


def list_queue(profile):
    _, queue, _ = check_profile(profile)
    paths = [p for p in queue.iterdir() if p.is_dir() and p.name not in ('failed', 'registry')]
    if (queue/'failed').is_dir():
        paths += [p for p in (queue/'failed').iterdir() if p.is_dir()]
    entries = []
    for path in paths:
        record = _entry(path)
        if record is None:
            entries.append(dict(request_id=path.name, state='incomplete'))
            continue
        state = 'prepared_for_review' if (path/'prepared.json').exists() else record['state']
        entries.append({k: record[k] for k in ('request_id', 'scheduled_at', 'candidate', 'current') if k in record}
                       | dict(state=state) | ({'code': record['code']} if 'code' in record else {}))
    return sorted(entries, key=lambda e: (e.get('scheduled_at', ''), e['request_id'], e['state']))


PREPARED_SCHEMA = 'cairn.weekly-candidate-prepared/1'


def prepare(profile, request_id, *, now, dispositions, companion, review):
    """Join a queued draft to reviews and run the existing weekly check/prepare.

    The queued core and schedule must still match the hashes recorded when the
    draft was queued. `dispositions` is a file pin to the six review records.
    `companion` and `review` are the independently selected file pins required
    by `trial_weekly_request`; a missing or stale one refuses in its `check`
    before the registry is created or any hold or output is written.
    """
    _, queue, _ = check_profile(profile)
    paired.identifier(request_id)
    entry = queue/request_id
    record = _entry(entry)
    paired.need(record is not None and record['state'] == 'queued_for_review', 'candidate_not_queued')
    paired.need(not (entry/'prepared.json').exists(), 'candidate_already_prepared')
    files = record['receipt']['draft']['files']
    core_bytes = paired.read_bytes(entry/'draft/request-core.json')
    for name, content in (('request-core.json', core_bytes),
                          ('schedule.json', paired.read_bytes(entry/'draft/schedule.json'))):
        paired.need(hashlib.sha256(content).hexdigest() == files[name]['sha256'], 'candidate_draft_changed')
    core = paired.decode_json(core_bytes)
    paired.need(core['request_id'] == request_id and core['occurrence_id'] == record['occurrence_id'],
                'candidate_draft_changed')
    full = dict(core, dispositions=paired.read_json_pin(dispositions))
    dispatch.check(full, now=now, companion=companion, review=review)
    registry = queue/'registry'
    registry.mkdir(mode=0o700, exist_ok=True)
    result = dispatch.prepare(full, registry=registry, now=now, companion=companion, review=review)
    paired.write_selected(entry/'prepared.json', dict(schema=PREPARED_SCHEMA, request_id=request_id,
        dispositions=dispositions, companion=companion, review=review, preparation=result))
    return result


def main(argv=None):
    class PublicParser(argparse.ArgumentParser):
        def error(self, message):
            raise paired.Refusal('invalid_arguments')

    parser = PublicParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    req = commands.add_parser('request')
    req.add_argument('--profile', default=str(DEFAULT_PROFILE))
    req.add_argument('--candidate', required=True)
    req.add_argument('--smoke-postgres', action='store_true')
    lst = commands.add_parser('list')
    lst.add_argument('--profile', default=str(DEFAULT_PROFILE))
    prep = commands.add_parser('prepare')
    prep.add_argument('request_id')
    prep.add_argument('--profile', default=str(DEFAULT_PROFILE))
    for field in ('dispositions', 'companion', 'review'):
        prep.add_argument('--' + field)
        prep.add_argument('--' + field + '-sha256')
    launch = commands.add_parser('launch')
    launch.add_argument('request_id')
    try:
        args = parser.parse_args(argv)
        if args.command == 'launch':
            result = launch_status()  # Refuses before reading any queue entry.
        else:
            profile = paired.read_json(paired.plain_path(args.profile))
            if args.command == 'list':
                result = dict(status='listed', entries=list_queue(profile))
            elif args.command == 'prepare':
                pins = {}
                for field in ('dispositions', 'companion', 'review'):
                    path, digest = getattr(args, field), getattr(args, field + '_sha256')
                    paired.need((path is None) == (digest is None), 'explicit_pin_required')
                    pins[field] = None if path is None else dict(path=path, sha256=digest)
                paired.need(pins['dispositions'] is not None, 'dispositions_required')
                result = prepare(profile, args.request_id, now=dt.datetime.now(dt.timezone.utc), **pins)
            else:
                result = request(profile, args.candidate, now=dt.datetime.now(dt.timezone.utc),
                                 smoke_postgres=args.smoke_postgres)
    except PrecheckRefusal as error:
        result = dict(status='refused', code=str(error), precheck=error.detail)
    except paired.Refusal as error:
        result = dict(status='refused', code=str(error))
    except (OSError, ValueError, TypeError, KeyError):
        result = dict(status='refused', code='invalid_or_unavailable_input')
    print(json.dumps(result, sort_keys=True))
    return 2 if result['status'] == 'refused' else 0


if __name__ == '__main__':
    from trial_candidate_request import main
    raise SystemExit(main())
