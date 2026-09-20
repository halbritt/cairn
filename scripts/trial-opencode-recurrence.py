#!/usr/bin/env python3
"""Opt-in historical repair trial. Models use a GPU lease or the bounded hosted relay.

Model workspaces contain one historical Git snapshot. Later fix and behavioral
checks stay outside the sandbox. Only selected patches and metadata are retained.
"""
import argparse
from collections import Counter
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import signal
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid

from trial_host import TrialHost, invoke

PROJECT = Path(__file__).resolve().parent.parent
SCENARIO = PROJECT / 'trials/opencode-recurrence/scenario.json'
GATE = PROJECT / 'trials/opencode-recurrence/supervisor_cache_test.go.txt'
GATE_PATH = 'internal/backend/llm/cairn_recurrence_gate_test.go'
MEMORY_ROOM = 32000


def sha(body):
    return hashlib.sha256(body).hexdigest()


def event(name, **data):
    print(json.dumps(dict(event=name, **data)), flush=True)


def git(source, *args):
    return subprocess.check_output(['git', '-C', str(source), *args], env=GIT_NEUTRAL_ENV,
                                   timeout=30)


def archive(source, revision, work):
    work.mkdir()
    with tarfile.open(fileobj=io.BytesIO(git(source, 'archive', revision))) as stream:
        stream.extractall(work, filter='data')


def gate(work, root, patch=None, *, trusted):
    evaluation = Path(tempfile.mkdtemp(prefix='gate-evaluation-', dir=root))
    safe_git(trusted, evaluation, 'read-tree', 'HEAD')
    safe_git(trusted, evaluation, 'checkout-index', '--all', '--force')
    if patch:
        safe_git(trusted, evaluation, 'apply', '--binary', '--whitespace=nowarn', '-', data=patch)
    _, _, outside = candidate_diff(evaluation, trusted)
    if outside:
        raise ValueError('gate candidate violates write scope')
    with (evaluation / GATE_PATH).open('xb') as stream:
        stream.write(GATE.read_bytes())
    argv = gate_command(evaluation, root)
    result = subprocess.run(argv, capture_output=True, timeout=150)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith(b'{')]
    actions = [e['Action'] for e in events if e.get('Test') == 'TestCairnRecurrenceRuntimeCacheLifetime'
               and e['Action'] in ('pass', 'fail', 'skip')]
    return dict(exit_code=result.returncode, actions=actions,
                passed=result.returncode == 0 and actions == ['pass'],
                stdout_sha256=sha(result.stdout), stderr_sha256=sha(result.stderr))


def gate_command(evaluation, root, tail=None):
    goroot = subprocess.check_output(['go', 'env', 'GOROOT'], text=True).strip()
    gomod = subprocess.check_output(['go', 'env', 'GOMODCACHE'], text=True).strip()
    environment = dict(PATH='/opt/go/bin:/usr/bin:/bin', HOME='/tmp/gate-home', TMPDIR='/tmp',
                       GOCACHE='/gate-cache', GOROOT='/opt/go', GOMODCACHE='/opt/gomod',
                       GOTOOLCHAIN='local', GOPROXY='off', STRIATUM_REQUIRE_CGROUP='1')
    launcher = ('IFS=: read -r hierarchy controllers relative < /proc/self/cgroup; '
                'test "$hierarchy" = 0; test -z "$controllers"; '
                'case "$relative" in /*) ;; *) exit 1 ;; esac; '
                'scope="/sys/fs/cgroup$relative"; '
                'exec bwrap --tmpfs / --ro-bind /sys /sys --bind "$scope" "$scope" "$@"')
    argv = ['systemd-run', '--user', '--scope', '--quiet', '-p', 'Delegate=yes',
            '/bin/sh', '-eu', '-c', launcher, 'cairn-gate',
            '--ro-bind', '/usr', '/usr', '--ro-bind', '/bin', '/bin',
            '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64', '--dir', '/etc',
            '--dir', '/tmp', '--tmpfs', '/gate-cache', '--unshare-net', '--unshare-ipc', '--new-session',
            '--unshare-pid', '--proc', '/proc', '--dev', '/dev', '--die-with-parent',
            '--bind', str(evaluation), '/work',
            '--ro-bind', goroot, '/opt/go', '--ro-bind', gomod, '/opt/gomod',
            '--chdir', '/work', '--clearenv']
    for name, value in environment.items():
        argv += ['--setenv', name, value]
    if tail is None:
        tail = ['--', 'go', 'test', '-json', '-count=1', '-timeout=90s', './internal/backend/llm',
                '-run', '^TestCairnRecurrenceRuntimeCacheLifetime$']
    return argv + tail


def prepare(source, root):
    root.mkdir(mode=0o700)
    scenario = json.loads(SCENARIO.read_text())
    fix = scenario['historical_fix']
    base = git(source, 'rev-parse', fix + '^').decode().strip()
    subprocess.run(['git', '-C', str(source), 'merge-base', '--is-ancestor', scenario['earlier_experience'], base], check=True)
    results = {}
    for label, revision in [('broken', base), ('reference', fix)]:
        work = root / label
        archive(source, revision, work)
        trusted = establish_trusted(work, root, label)
        results[label] = gate(work, root, trusted=trusted)
        event('preflight', arm=label, **results[label])
    if results['broken']['passed'] or results['broken']['actions'] != ['fail'] or not results['reference']['passed']:
        raise RuntimeError('historical gate did not distinguish the broken and repaired implementations')
    state = dict(schema='cairn.recurrence-preflight/1', source=str(source), base=base, fix=fix,
                 scenario_sha256=sha(SCENARIO.read_bytes()), gate_sha256=sha(GATE.read_bytes()), results=results)
    (root / 'preflight.json').write_text(json.dumps(state, indent=2))
    return state


def private_file(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
        stream.write(text)


def native_notes(source, state, scenario):
    if 'reviewed_lesson' in scenario:
        # This is an explicit later lesson, not evidence available at the
        # historical repair date. Its bytes are pinned by the scenario hash.
        return [scenario['reviewed_lesson']]
    raw = git(source, 'show', scenario['earlier_experience'], '--format=%B', '--no-patch').decode()
    start = raw.index('Correcting a claim I first wrote')
    end = raw.index('\n\n', start)
    paragraph = raw[start:end]
    path = 'decisions/D0013-adapter-supervision.md'
    clause = next(line for line in git(source, 'show', state['base'] + ':' + path).decode().splitlines()
                  if line.startswith('- **D0013.C2 '))
    return [dict(body=paragraph, source=scenario['earlier_experience'] + ':commit-message'),
            dict(body=clause, source=state['base'] + ':' + path + '#D0013.C2')]


def sandbox(opencode, work, home, cache, config, goroot, gomod, route):
    # Hide the real home, trial controller, later reference and PostgreSQL store.
    # Network remains available for the model endpoint by task policy; this is
    # filesystem isolation, not a network confinement claim.
    return ['bwrap', '--tmpfs', '/', '--ro-bind', '/usr', '/usr', '--ro-bind', '/bin', '/bin',
            '--ro-bind', '/lib', '/lib', '--ro-bind', '/lib64', '/lib64', '--ro-bind', '/etc', '/etc',
            '--ro-bind', '/sys', '/sys', '--dir', '/tmp', '--dir', '/run',
            '--unshare-pid', '--proc', '/proc', '--dev', '/dev', '--die-with-parent',
            '--bind', str(work), '/work', '--bind', str(home), '/trial-home',
            '--bind', str(cache), '/trial-cache', '--ro-bind', str(opencode), '/opt/opencode',
            '--ro-bind', str(config), '/opt/opencode.json', '--ro-bind', str(goroot), '/opt/go',
            '--ro-bind', str(gomod), '/opt/gomod', '--chdir', '/work', '--', '/opt/opencode',
            'run', '--pure', '--format', 'json', '-m', route['provider'] + '/' + route['model']]


GIT_NEUTRAL_ENV = dict(PATH='/usr/bin:/bin', HOME='/nonexistent', LC_ALL='C',
                       GIT_CONFIG_GLOBAL='/dev/null', GIT_CONFIG_NOSYSTEM='1',
                       GIT_ATTR_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0', GIT_LITERAL_PATHSPECS='1')


def copy_regular_tree(work, target, baseline_links=None):
    remaining = 256 * 1024 * 1024
    count = 0

    def copy(directory, destination, depth):
        nonlocal remaining, count
        if depth > 64:
            raise ValueError('candidate directory depth exceeds limit')
        for name in os.listdir(directory):
            if depth == 0 and name == '.git':
                continue
            count += 1
            if count > 20000:
                raise ValueError('candidate file count exceeds limit')
            path = destination / name
            if stat.S_ISLNK(os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode):
                link = os.readlink(name, dir_fd=directory)
                relative = str(path.relative_to(target))
                if baseline_links is not None and baseline_links.get(relative) != link:
                    raise ValueError('candidate changes a symlink')
                path.symlink_to(link)
                continue
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            try:
                metadata = os.fstat(fd)
                if stat.S_ISDIR(metadata.st_mode):
                    path.mkdir()
                    copy(fd, path, depth + 1)
                elif stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1:
                    with os.fdopen(os.dup(fd), 'rb') as source, path.open('xb') as output:
                        while chunk := source.read(min(remaining + 1, 1024 * 1024)):
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise ValueError('candidate bytes exceed limit')
                            output.write(chunk)
                    path.chmod(0o755 if metadata.st_mode & 0o111 else 0o644)
                else:
                    raise ValueError('candidate contains a special file or hardlink')
            finally:
                os.close(fd)

    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        copy(fd, target, 0)
    finally:
        os.close(fd)


def establish_trusted(work, root, arm):
    meta = root / (arm + '-trusted-meta')
    meta.mkdir(mode=0o700)
    subprocess.run(['git', 'init', '--bare', '--template=', '-q', str(meta)],
                   env=GIT_NEUTRAL_ENV, check=True, capture_output=True)
    (meta / 'config').write_text('[core]\n\tbare = false\n\tfilemode = true\n\thooksPath = /dev/null\n')
    (meta / 'info').mkdir()
    (meta / 'info/attributes').write_text('* -filter -text -ident -working-tree-encoding !diff !export-ignore !export-subst\n')
    with tempfile.TemporaryDirectory(prefix='baseline-', dir=root) as temporary:
        snapshot = Path(temporary)
        copy_regular_tree(work, snapshot)
        safe_git(meta, snapshot, 'add', '-f', '--all', '--', '.')
        safe_git(meta, snapshot, '-c', 'user.name=Cairn trial', '-c', 'user.email=trial@localhost',
                 'commit', '--allow-empty', '-qm', 'Historical trial input snapshot')
    return meta


def safe_git(meta, work, *args, data=None):
    return subprocess.run(['git', '--git-dir', str(meta), '--work-tree', str(work), *args],
                          env=GIT_NEUTRAL_ENV, input=data, capture_output=True,
                          check=True, cwd=work, timeout=30).stdout


def candidate_diff(work, trusted):
    with tempfile.TemporaryDirectory(prefix='candidate-', dir=trusted.parent) as temporary:
        snapshot = Path(temporary)
        links = {}
        for entry in safe_git(trusted, snapshot, 'ls-tree', '-rz', 'HEAD').split(b'\0')[:-1]:
            metadata, name = entry.split(b'\t', 1)
            mode, _, object_id = metadata.split()
            if mode == b'120000':
                links[name.decode()] = safe_git(trusted, snapshot, 'cat-file', 'blob', object_id.decode()).decode()
        copy_regular_tree(work, snapshot, links)
        safe_git(trusted, snapshot, 'read-tree', 'HEAD')
        tracked = set(safe_git(trusted, snapshot, 'ls-files', '-z').decode().split('\0')[:-1])
        safe_git(trusted, snapshot, 'add', '-f', '--all', '--', '.')
        names = set(safe_git(trusted, snapshot, 'diff', '--cached', '--no-renames', '--name-only', '-z',
                             'HEAD', '--').decode().split('\0')[:-1])
        allowed = {'internal/backend/llm/supervisor.go', 'internal/backend/supervise/supervise.go',
                   'internal/backend/supervise/init.go'}
        outside = []
        for name in sorted(names):
            if (snapshot / name).is_symlink():
                raise ValueError('candidate changes a symlink')
            new_test = name.endswith('_test.go') and str(Path(name).parent) in ('internal/backend/llm', 'internal/backend/supervise')
            if name == GATE_PATH or (name not in allowed and not (new_test and name not in tracked)):
                outside.append(name)
        patch = safe_git(trusted, snapshot, 'diff', '--cached', '--binary', '--no-ext-diff',
                         '--no-textconv', '--no-renames', 'HEAD', '--')
    return patch, sorted(names), outside


def check_selection(package, records, arm):
    expected = {r['record_id']: (r['version'], r['body_sha256']) for r in records} if arm == 'cairn_h0' else {}
    actual = {s['record']['record_id']: (s['record']['version'], sha(s['record']['body'].encode()))
              for s in package['semantic']['selected']}
    if actual != expected:
        raise RuntimeError('treatment mismatch before launch: ' + arm + ': ' + json.dumps(package['semantic']['omitted']))
    return dict(receipt_id=package['receipt_id'], seal=package['seal'], selected_records=sorted(actual),
                available_tokens=package['semantic']['available_tokens'], optional_limit=package['semantic']['optional_limit'])


def trace_summary(trace, selected_ids):
    # Keep bounded categories and counts, never tool arguments, output, or prose.
    tools, finishes, tokens = Counter(), Counter(), Counter()
    citations = set()
    for item in trace:
        part = item.get('part', {})
        if item.get('type') == 'tool_use':
            tool = part.get('tool', 'unknown')
            if tool not in ('bash', 'read', 'edit', 'write', 'apply_patch', 'glob', 'grep', 'task', 'webfetch', 'websearch', 'skill', 'todowrite'):
                tool = 'other'
            status = part.get('state', {}).get('status', 'unknown')
            if status not in ('pending', 'running', 'completed', 'error'):
                status = 'unknown'
            tools[tool + ':' + status] += 1
        if item.get('type') == 'step_finish':
            reason = part.get('reason', 'unknown')
            finishes[reason if reason in ('stop', 'length', 'tool-calls', 'error', 'other') else 'unknown'] += 1
            for key in ('input', 'output', 'reasoning'):
                value = part.get('tokens', {}).get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    tokens[key] += value
        if item.get('type') == 'text':
            citations.update(r for r in selected_ids if r in part.get('text', ''))
    return dict(tool_events=dict(tools), finish_reasons=dict(finishes), reported_tokens=dict(tokens),
                cited_record_ids=sorted(citations), citation_method='exact selected UUID in OpenCode text event; testimony only')


def retain_last_explanation(root, arm, trace):
    """Explicit selected diagnostic evidence; excludes reasoning and tool output."""
    texts = [e.get('part', {}).get('text', '') for e in trace if e.get('type') == 'text']
    if not texts:
        return None
    raw = texts[-1].encode()
    selected = raw[:8192].decode(errors='ignore')  # Drop only a partial trailing UTF-8 character.
    path = root / (arm + '-last-explanation.txt')
    private_file(path, selected)
    return dict(path=str(path), sha256=sha(selected.encode()), bytes=len(selected.encode()),
                truncated=len(raw) > 8192,
                interpretation='Last model text event, not proof of task completion or necessarily a terminal answer')


def assess_arm(binary, environment, result, records, host):
    receipt_id = result['receipt'].get('receipt_id')
    if not receipt_id:
        return dict(status='unavailable', reason='no run receipt')
    repo = 'trial:' + result['arm']
    request_id = lambda operation: str(uuid.uuid5(uuid.NAMESPACE_URL, 'cairn-recurrence/1/' + receipt_id + '/' + operation))
    evidence = host.call('evidence', dict(
        request_id=request_id('gate-evidence'), repo=repo, sensitivity='local',
        source='Explicit historical repair gate observation; no model output',
        body=json.dumps({k: result[k] for k in ('arm', 'receipt', 'gate', 'changed_paths', 'outside_scope', 'patch_sha256', 'runtime_observed')})))
    if not result['runtime_observed']:
        outcome, domain, kind = 'unknown', 'binding', 'adapter'
        reason = 'No model activity observed; mechanical failure cannot establish a model task failure.'
    elif not result['gate']['passed'] and result['gate'].get('actions') != ['fail'] and not result['outside_scope']:
        outcome, domain, kind = 'unknown', 'unknown', ''
        reason = 'The evaluator did not run the necessary behavioral condition to failure. A build or evaluation error needs separate diagnosis before task assessment.'
    elif not result['gate']['passed']:
        outcome, domain, kind = 'rejected', 'task', 'historical_cache_gate'
        reason = 'The candidate failed a necessary cache-lifetime condition or the authorized write scope. This is bounded trial assessment, not a Striatum verdict or a capability diagnosis.'
    else:
        outcome, domain, kind = 'unknown', 'unknown', ''
        reason = 'The cache-lifetime condition passed; full task correctness and Striatum acceptance remain unverified.'
    assessment = host.call('assess-run', dict(
        request_id=request_id('assessment'), receipt_id=receipt_id, expected_version=0,
        task_outcome=outcome, failure_domain=domain, failure_kind=kind,
        method='cairn.historical-cache-gate/2; host-observed condition, not full task acceptance', evidence_ids=[evidence['evidence_id']], reason=reason))
    citations = []
    by_id = {r['record_id']: r for r in records}
    for record_id in result.get('trace_summary', {}).get('cited_record_ids', []):
        if record_id not in result['selected_records']:
            raise RuntimeError('citation refers to a record outside the actual delivery')
        citations.append(host.call('usage', dict(
            request_id=request_id('citation/' + record_id), receipt_id=receipt_id, record_id=record_id,
            version=by_id[record_id]['version'], signal='cited',
            method='cairn.opencode-text-uuid/1; model-authored UUID is testimony, not causal influence')))
    joined = invoke(binary, environment, 'use-report', arguments=[repo])
    if joined['more']:
        raise RuntimeError('unexpectedly truncated trial use report')
    rows = [row for row in joined['rows'] if row['receipt_id'] == receipt_id]
    return dict(assessment=assessment, citation_observations=citations, run_use_rows=rows,
                excluded_preflight_rows=len(joined['rows']) - len(rows), interpretation=joined['interpretation'])


def run_trial(root, binary, opencode, arm=None, disable_thinking=False, context_tokens=65536, route=None, extended_budget=False, retain_final=False, output_tokens=8192):
    for key in ('GPU_FLEET_LEASE_ID', 'GPU_FLEET_ENDPOINT_URL', 'GPU_FLEET_SERVED_MODEL'):
        if route is None and not os.environ.get(key):
            raise RuntimeError('run requires an active gpu-fleet-run environment')
    if route is None:
        route = dict(provider='fleet', model=os.environ['GPU_FLEET_SERVED_MODEL'],
                     endpoint=os.environ['GPU_FLEET_ENDPOINT_URL'], api_key='local-fleet',
                     binding='opencode-local-fleet', lease_id=os.environ['GPU_FLEET_LEASE_ID'])
    process_seconds, steps = (900, 60) if extended_budget else (300, 20)
    scenario = json.loads(SCENARIO.read_text())
    if arm is not None:
        scenario['arms'] = [arm]
    state = json.loads((root / 'preflight.json').read_text())
    if state['scenario_sha256'] != sha(SCENARIO.read_bytes()) or state['gate_sha256'] != sha(GATE.read_bytes()):
        raise RuntimeError('scenario or held-out gate changed after preflight')
    source = Path(state['source'])
    notes = native_notes(source, state, scenario)
    pg_bin = Path(subprocess.check_output(['pg_config', '--bindir']).decode().strip())
    store = root / 'store'
    store.mkdir(mode=0o700)
    (store / 'socket').mkdir(mode=0o700)
    subprocess.run([str(pg_bin / 'initdb'), '-D', str(store / 'data'), '--auth-local=trust', '--auth-host=reject', '--no-locale', '-E', 'UTF8'], check=True, stdout=subprocess.DEVNULL)
    server = None
    host = None
    report = dict(schema='cairn.opencode-recurrence/1', scenario=scenario['id'], availability=scenario.get('availability', 'later_recurrence'),
                  base=state['base'], fix=state['fix'], preflight=state['results'],
                  scenario_sha256=sha(SCENARIO.read_bytes()), gate_sha256=sha(GATE.read_bytes()),
                  model=route['model'], lease_id=route['lease_id'], binding=route['binding'],
                  opencode_sha256=sha(opencode.read_bytes()), cairn_sha256=sha(binary.read_bytes()),
                  controller_sha256=sha(Path(__file__).read_bytes()), host_controller_sha256=sha((PROJECT / 'scripts/trial_host.py').read_bytes()), arms=[], limits=scenario['limits'])
    report['experiment_kind'] = scenario.get('experiment_kind', 'harness_calibration' if arm is not None else 'memory_comparison')
    if route['provider'] == 'trial-openrouter':
        report['relay_sha256'] = sha((PROJECT / 'scripts/trial_openrouter.py').read_bytes())
    report['settings'] = dict(arms=scenario['arms'], disable_thinking=disable_thinking,
                              context_tokens=context_tokens, output_limit=output_tokens, process_seconds=process_seconds, steps=steps,
                              work_budget='extended' if extended_budget else 'standard', retain_final=retain_final)
    try:
        subprocess.run([str(pg_bin / 'pg_ctl'), '-D', str(store / 'data'), '-l', str(store / 'postgres.log'), '-o', f"-k {store}/socket -c listen_addresses=''", '-w', 'start'], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(pg_bin / 'createdb'), '-h', str(store / 'socket'), 'cairn_trial'], check=True)
        environment = dict(os.environ, CAIRN_HOME=str(store), CAIRN_DATABASE_URL=f'host={store}/socket dbname=cairn_trial sslmode=disable')
        invoke(binary, environment, 'migrate')
        authority = invoke(binary, environment, 'bootstrap', dict(request_id=str(uuid.uuid4()), reason='Bootstrap isolated historical repair trial'))
        token = secrets.token_urlsafe(32)
        private_file(store / 'collector.token', token)
        identities = [dict(token_sha256=sha(token.encode()), principal='collector:recurrence',
                           repo='trial:cairn_h0', role='agent', destination='local')]
        for trial_arm in scenario['arms']:
            observer_token = secrets.token_urlsafe(32)
            private_file(store / (trial_arm + '-observer.token'), observer_token)
            identities.append(dict(token_sha256=sha(observer_token.encode()), principal='host:recurrence:' + trial_arm,
                                   repo='trial:' + trial_arm, role='observer', destination='hosted'))
        private_file(store / 'identities.json', json.dumps(identities))
        server = subprocess.Popen([str(binary), 'serve', '--identities', str(store / 'identities.json'), '--socket', str(store / 'api.sock')], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + 10
        while not (store / 'api.sock').exists():
            if server.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError('trial collector API did not start')
            time.sleep(.025)
        records = []
        for note in notes:
            args = ['--token-file', store / 'collector.token', '--socket', store / 'api.sock']
            record = invoke(binary, environment, 'agent', dict(request_id=str(uuid.uuid4()), draft=dict(kind='lesson', body=note['body'], sensitivity='shareable',
                         scope=dict(repo='trial:cairn_h0', task_id='*', run_id='*'), claim_type='self', pins=dict(revision=state['base']))), [*args, 'create'])
            evidence = invoke(binary, environment, 'agent', dict(request_id=str(uuid.uuid4()), repo='trial:cairn_h0', body=json.dumps(note), source='Explicit selected recurrence evidence capture', sensitivity='shareable'), [*args, 'evidence'])
            record = invoke(binary, environment, 'promote', dict(request_id=str(uuid.uuid4()), record_id=record['record_id'], expected_version=record['version'], grant_id=authority['grant_id'], evidence_ids=[evidence['evidence_id']], reason='Admit reviewed source-supported lesson for a bounded recurrence experiment'))
            records.append(dict(record_id=record['record_id'], version=record['version'], source=note['source'], body_sha256=sha(note['body'].encode())))
        report['records'] = records
        report['status'] = 'running'
        (root / 'report.json').write_text(json.dumps(report, indent=2))
        goroot = Path(subprocess.check_output(['go', 'env', 'GOROOT']).decode().strip())
        gomod = Path(subprocess.check_output(['go', 'env', 'GOMODCACHE']).decode().strip())
        # Check every arm before spending model time. This store has no other
        # writers after seeding; verify the launched receipt again afterward.
        report['selection_preflight'] = {}
        for arm in scenario['arms']:
            package = invoke(binary, environment, 'search', arguments=[
                '--repo', 'trial:' + arm, '--task', scenario['id'], '--run', arm,
                '--destination', 'hosted', '--tokens', str(MEMORY_ROOM), '--revision', state['base'],
                '--task-class', 'historical-go-cache-repair', '--binding', route['binding'],
                '--capability', route['model'], scenario['query']])
            report['selection_preflight'][arm] = check_selection(package, records, arm)
        (root / 'report.json').write_text(json.dumps(report, indent=2))
        for arm in scenario['arms']:
            work = root / arm
            archive(source, state['base'], work)
            trusted = establish_trusted(work, root, arm)
            shutil.copytree(trusted, work / '.git')
            home = root / (arm + '-home'); home.mkdir(mode=0o700)
            cache = root / (arm + '-cache'); cache.mkdir(mode=0o700)
            config = root / (arm + '-opencode.json')
            model = route['model']
            private_file(config, json.dumps(dict(autoupdate=False, share='disabled', enabled_providers=[route['provider']],
                provider={route['provider']:dict(npm='@ai-sdk/openai-compatible', name=route['binding'], options=dict(baseURL=route['endpoint'], apiKey=route['api_key']),
                     models={model:dict(name=model, limit=dict(context=context_tokens, output=output_tokens),
                         options={'chat_template_kwargs': {'enable_thinking': False}} if disable_thinking else {})})},
                agent=dict(build=dict(temperature=0, steps=steps)),
                permission=dict(external_directory='deny', webfetch='deny', websearch='deny', task='deny', skill='deny', edit='allow', read='allow', bash={'*':'deny','go test*':'allow','go version*':'allow','gofmt*':'allow','git diff*':'allow','git status*':'allow','rg *':'allow','ls*':'allow','pwd':'allow'}))))
            child_env = dict(PATH='/opt/go/bin:/usr/bin:/bin', HOME='/trial-home', GOROOT='/opt/go', GOPATH='/trial-home/go', GOMODCACHE='/opt/gomod', GOCACHE='/trial-cache', GOTOOLCHAIN='local', GOPROXY='off',
                             XDG_CONFIG_HOME='/trial-home/.config', XDG_DATA_HOME='/trial-home/.local/share', XDG_CACHE_HOME='/trial-home/.cache', XDG_STATE_HOME='/trial-home/.local/state',
                             OPENCODE_CONFIG='/opt/opencode.json', OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_DISABLE_MODELS_FETCH='true')
            # The wrapper uses the observer API. Its deliberately unusable DSN
            # makes accidental return to operator/database access fail.
            child_env.update(CAIRN_HOME=str(store), CAIRN_DATABASE_URL='host=/nonexistent-cairn-trial-client dbname=denied')
            prompt = scenario['task']
            if arm == 'native_excerpt':
                prompt += '\n\nSupplemental reviewed experience:\n' + '\n\n'.join(n['source']+'\n'+n['body'] for n in notes)
            arguments = ['--dir', str(work), '--destination', 'hosted', '--carrier', 'argv', '--tokens', str(MEMORY_ROOM), '--timeout', str(process_seconds) + 's',
                       '--task-class', 'historical-go-cache-repair', '--binding', route['binding'], '--capability', model,
                       '--revision', state['base'], '--query', scenario['query'], '--prompt', prompt, '--', *sandbox(opencode, work, home, cache, config, goroot, gomod, route)]
            report['active_arm'] = arm
            (root / 'report.json').write_text(json.dumps(report, indent=2))
            event('arm_preparing', arm=arm)
            host = TrialHost(root / (arm + '-host'),
                             [binary, 'agent', '--token-file', store / (arm + '-observer.token'), '--socket', store / 'api.sock'],
                             child_env, dict(repo='trial:' + arm, task_id=scenario['id'], run_id=arm))
            package = host.call('compile', dict(request_id=host.request_id, scope=host.scope,
                                query=scenario['query'], purpose='context', available_tokens=MEMORY_ROOM,
                                context=dict(revision=state['base'], task_class='historical-go-cache-repair',
                                             binding_id=route['binding'], capability_id=model)))
            selected = check_selection(package, records, arm)
            report['selection_preflight'][arm]['host_package'] = selected
            (root / 'report.json').write_text(json.dumps(report, indent=2))
            started = time.monotonic()
            result = host.run(arguments, timeout=process_seconds + 30)
            elapsed = time.monotonic() - started
            envelope = json.loads(result.stderr.splitlines()[-1])
            receipt = envelope.get('data', {})
            trace = []
            for line in result.stdout.splitlines():
                try:
                    trace.append(json.loads(line))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            patch, paths, outside = candidate_diff(work, trusted)
            private_file(root / (arm + '.patch'), patch.decode())
            # Correspondence is to the returned candidate, not its correctness.
            # A zero exit with no candidate remains no_result.
            terminal = host.finish('sha256:' + sha(patch) if patch else '')
            status = host.call('run-status', dict(receipt_id=receipt['receipt_id'])) if receipt.get('receipt_id') else None
            if status is not None and (receipt.get('attempt_id') != host.attempt_id or
                                       (status.get('outcome') or {}).get('observation_id') != receipt.get('outcome_id')):
                raise RuntimeError('host receipt/outcome correspondence mismatch')
            scored = gate(work, root, patch, trusted=trusted) if not outside else dict(passed=False, reason='write_scope_violation')
            if receipt.get('receipt_id') and (receipt['receipt_id'] != selected['receipt_id'] or receipt['seal'] != selected['seal']):
                raise RuntimeError('wrapped execution did not use its checked host package')
            arm_result = dict(arm=arm, process_exit=result.returncode, duration_seconds=round(elapsed,3), receipt=receipt,
                              host_attempt=dict(attempt_id=host.attempt_id, terminal=terminal, run_status=status,
                                                observer='host:recurrence:' + arm, journal=str(host.root)),
                              gate=scored, changed_paths=paths, outside_scope=outside, patch_sha256=sha(patch),
                              model_stdout_sha256=sha(result.stdout), model_stderr_sha256=sha(result.stderr),
                              runtime_observed=any(e.get('type') in ('step_finish', 'tool_use', 'text') for e in trace),
                              launch_diagnostics=[line[:512] for line in result.stderr.decode(errors='replace').splitlines() if line.startswith('bwrap:')],
                              trace_events=len(trace), trace_types=sorted(set(str(e.get('type')) for e in trace)),
                              trace_summary=trace_summary(trace, [r['record_id'] for r in records]),
                              config_sha256=sha(config.read_bytes()),
                              selected_records=selected['selected_records'] if receipt.get('receipt_id') else [])
            if retain_final:
                explanation = retain_last_explanation(root, arm, trace)
                if explanation is not None:
                    arm_result['last_explanation'] = explanation
            arm_result['assessment_join'] = assess_arm(binary, environment, arm_result, records, host)
            report['arms'].append(arm_result)
            (root / 'report.json').write_text(json.dumps(report, indent=2))
            event('arm_finished', arm=arm, process_exit=result.returncode, gate=scored, paths=paths, trace_types=arm_result['trace_types'])
            shutil.rmtree(home)
            shutil.rmtree(cache)
        report['status'] = 'completed'
        report.pop('active_arm', None)
        (root / 'report.json').write_text(json.dumps(report, indent=2))
        subprocess.run([str(pg_bin / 'pg_dump'), '-Fc', '-f', str(root / 'trial.dump'), environment['CAIRN_DATABASE_URL']], check=True)
    except BaseException as error:
        if host is not None:
            host.retain_terminal()
        report['status'] = 'interrupted' if isinstance(error, (KeyboardInterrupt, InterruptedError)) else 'failed'
        report['failure_type'] = type(error).__name__
        (root / 'report.json').write_text(json.dumps(report, indent=2))
        raise
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill(); server.wait(timeout=5)
        try:
            if (store / 'data/postmaster.pid').exists():
                subprocess.run([str(pg_bin / 'pg_ctl'), '-D', str(store / 'data'), '-m', 'fast', '-w', 'stop'], check=True, stdout=subprocess.DEVNULL)
        finally:
            for arm in scenario['arms']:
                for suffix in ('-home', '-cache'):
                    path = root / (arm + suffix)
                    if path.exists():
                        shutil.rmtree(path)
    event('trial_complete', report=str(root / 'report.json'))


def main():
    def interrupted(signum, frame):
        raise InterruptedError('trial termination signal')
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='operation', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--source', required=True, type=Path); p.add_argument('--output', required=True, type=Path)
    p = sub.add_parser('run'); p.add_argument('--trial', required=True, type=Path); p.add_argument('--cairn', required=True, type=Path); p.add_argument('--opencode', required=True, type=Path)
    p.add_argument('--arm', choices=['repo_only', 'native_excerpt', 'cairn_h0'], help='one-arm harness calibration; not a memory comparison')
    p.add_argument('--disable-thinking', action='store_true', help='request chat_template_kwargs.enable_thinking=false from the local model')
    p.add_argument('--context-tokens', type=int, choices=[65536, 131072], default=65536,
                   help='verified model context; acquire a fleet lease supporting at least this value')
    from trial_openrouter import MODEL, MODEL_PRICES
    p.add_argument('--openrouter-model', choices=list(MODEL_PRICES), default=MODEL, help='one of the existing configured hosted model bindings')
    p.add_argument('--openrouter', action='store_true', help='one-arm calibration through the bounded hosted credential relay')
    p.add_argument('--output-tokens', type=int, choices=[8192, 32768], default=8192, help='hosted model output allowance; reasoning shares this budget')
    p.add_argument('--retain-final', action='store_true', help='explicitly retain at most 8 KiB of the last model text event as private diagnostic evidence')
    p.add_argument('--extended-budget', action='store_true', help='hosted one-arm calibration: 900 seconds, 60 steps, 64 requests and 300 seconds per response')
    args = parser.parse_args()
    if args.operation == 'prepare':
        prepare(args.source.resolve(strict=True), args.output.resolve())
    elif args.output_tokens != 8192 and not args.openrouter:
        parser.error('--output-tokens currently requires --openrouter')
    elif args.openrouter_model != MODEL and not args.openrouter:
        parser.error('--openrouter-model requires --openrouter')
    elif args.extended_budget and not args.openrouter:
        parser.error('--extended-budget currently requires --openrouter')
    elif args.openrouter:
        if args.arm is None or args.disable_thinking:
            parser.error('--openrouter requires one --arm and does not support --disable-thinking')
        from trial_openrouter import configured_key, relay
        root = args.trial.resolve(strict=True)
        with relay(configured_key(args.openrouter_model), root / 'hosted-relay.json', model=args.openrouter_model,
                   max_requests=64 if args.extended_budget else 24,
                   response_seconds=600 if args.output_tokens == 32768 else (300 if args.extended_budget else 120),
                   max_output_tokens=args.output_tokens) as route:
            run_trial(root, args.cairn.resolve(strict=True), args.opencode.resolve(strict=True), args.arm, False, args.context_tokens, route, args.extended_budget, args.retain_final, args.output_tokens)
    else:
        run_trial(args.trial.resolve(strict=True), args.cairn.resolve(strict=True), args.opencode.resolve(strict=True), args.arm, args.disable_thinking, args.context_tokens, retain_final=args.retain_final)


if __name__ == '__main__':
    main()
