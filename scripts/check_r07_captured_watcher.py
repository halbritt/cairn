#!/usr/bin/env python3
"""Captured Python watcher against complete disposable /2-/3 selections.

Run through mise host:exec. The API and PostgreSQL are real disposable fixtures;
the terminal is scripted and the owner invokes maintained hooks, never a model.
A named watcher failure ends the run; captured code is never patched or retried.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import check_r06_unit_control as control
import check_r07_bootstrap_reverse as bootstrap
from stage_lifecycle_hooks import checked_go_launcher
from disposable_host import OwnedCluster, pg_bindir, require, stop_process
from test_idle_wakeup import HOST, coordination
from stage_lifecycle_compatibility import RETAINED_MEMBERS


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def captured_sources(index_path, index_sha, sidecar, sidecar_sha, destination):
    raw = index_path.read_bytes()
    require(digest(raw) == index_sha, 'selected captured-source index changed')
    index = json.loads(raw)
    closure = index['closures']['cairn-424-a6dfabb3']
    require(set(closure['files']) == RETAINED_MEMBERS,
            'captured watcher requires its complete 17-file map')
    destination.mkdir(mode=0o700)
    pins = {}
    for name, row in closure['files'].items():
        require(Path(name).name == name and name.endswith('.py'), 'invalid public source member')
        copy = row['copy']
        body = Path(copy['path']).read_bytes()
        require(len(body) == copy['bytes'] and digest(body) == copy['sha256'] == row['sha256'], 'captured source copy changed')
        target = destination / name
        target.write_bytes(body)
        target.chmod(0o700)
        pins[name] = copy['sha256']
    map_sha = digest(json.dumps(pins, sort_keys=True, separators=(',', ':')).encode())
    require(map_sha == closure['source_map_sha256'], 'captured complete map changed')
    body = sidecar.read_bytes()
    require(len(body) == 1632 and digest(body) == sidecar_sha, 'selected E424 sidecar changed')
    manifest = json.loads(body)
    require(manifest == dict(schema='cairn.lifecycle-bundle/1', sha256=map_sha, files=pins), 'E424 sidecar/map mismatch')
    (destination / 'lifecycle-bundle.json').write_bytes(body)
    return dict(index=dict(path=str(index_path), sha256=index_sha), map_sha256=map_sha, files=pins,
                sidecar=dict(path=str(sidecar), bytes=len(body), sha256=sidecar_sha), captured_code_modified=False)


def owner_actor(root):
    """Disposable terminal owner; each command invokes the maintained hook once."""
    for line in sys.stdin:
        request = json.loads(line)
        prefix = root / request['label']
        event = dict(request['event'], host_pid=os.getpid()) if request.get('event') is not None else None
        control.write(prefix.with_suffix('.input.json'), event)
        control.write(prefix.with_suffix('.argv.json'), request['argv'])
        child = subprocess.Popen(request['argv'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        receipt = dict(pid=child.pid, process=coordination.process_reference(child.pid), owner=coordination.process_reference(os.getpid()))
        control.write(prefix.with_suffix('.start.json'), receipt)
        try:
            stdout, stderr = child.communicate(json.dumps(event).encode() if event is not None else b'', timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate()
            receipt['timeout'] = True
        prefix.with_suffix('.stdout').write_bytes(stdout)
        prefix.with_suffix('.stderr').write_bytes(stderr)
        receipt.update(exit=child.returncode, joined=child.poll() is not None)
        control.write(prefix.with_suffix('.join.json'), receipt)
        print(json.dumps(receipt), flush=True)


def owner_hook(f, owner, argv, native, label, event='PreInvocation'):
    request = dict(label=label, argv=list(map(str, argv)), event=dict(conversationId=native,
        workspacePaths=[str(f.root)], hook_event_name=event, fullyIdle=event == 'Stop'))
    return owner_command(f, owner, request)


def retain_owner_inputs(f, label, argv):
    """Keep the actual selected bytes before invocation and disposable cleanup."""
    scratch = f.production_home.parent.resolve()
    candidates = [Path(argv[0])]
    if Path(argv[0]).resolve() == Path(sys.executable).resolve():
        candidates = [Path(arg) for arg in argv[1:] if arg.endswith('.py')]
    require(len(candidates) == 1, 'issued command has no unique selected entry')
    selected = [('entry', candidates[0])]
    if '--context' in argv:
        require(argv.count('--context') == 1, 'issued context selector is ambiguous')
        selected.append(('context', Path(argv[argv.index('--context') + 1])))
    target = f.root / 'issued-custody' / label
    target.mkdir(mode=0o700, parents=True)
    pins = []
    for role, path in selected:
        require(path.resolve().is_relative_to(scratch), 'issued input left owned scratch')
        raw = path.read_bytes()
        copy = target / (role + '.raw')
        copy.write_bytes(raw)
        pins.append(dict(role=role, original=str(path), copy=str(copy), bytes=len(raw), sha256=digest(raw)))
    control.write(target / 'pins.json', dict(argv=argv, files=pins))


def owner_command(f, owner, request):
    label = request['label']
    retain_owner_inputs(f, label, request['argv'])
    owner.stdin.write(json.dumps(request) + '\n')
    owner.stdin.flush()
    receipt = json.loads(owner.stdout.readline())
    require(receipt['joined'] and receipt['exit'] == 0, label + ': maintained hook refused; no retry')
    require(not (f.root / (label + '.stderr')).read_bytes(), label + ': hook diagnostic; STOP')
    return receipt


def await_fact(predicate, *, seconds, label, watcher=None, stderr=None):
    deadline = time.monotonic() + seconds
    while True:
        if watcher is not None:
            require(watcher.poll() is None, label + ': captured watcher exited')
            if stderr is not None:
                require(not stderr.read_bytes(), label + ': captured watcher diagnostic: ' + stderr.read_text())
        result = predicate()
        if result:
            return result
        require(time.monotonic() < deadline, label + ': bounded proof unavailable')
        time.sleep(0.05)


def held_lock(process, lock):
    expected = lock.stat().st_ino
    for fd in (Path('/proc') / str(process.pid) / 'fdinfo').iterdir():
        try:
            rows = fd.read_text().splitlines()
            if any(row.startswith('ino:') and int(row.split()[1]) == expected for row in rows):
                locks = [row for row in rows if row.startswith('lock:') and 'FLOCK' in row and 'WRITE' in row]
                if locks:
                    return dict(pid=process.pid, inode=expected, fdinfo=locks)
        except FileNotFoundError:
            continue
    return None


def operation(f, images, plan, label, operation, refusal=None):
    result = f.root / (label + '-result.json')
    spec = f.root / (label + '-test.json')
    selected = dict(Plan=str(plan), Pin=digest(plan.read_bytes()), Result=str(result), Operation=operation)
    if refusal:
        selected['RefuseContains'] = refusal
    control.write(spec, selected)
    temporary = f.root / 'test-temporary'
    temporary.mkdir(mode=0o700, exist_ok=True)
    env = dict(f.host.env, TMPDIR=str(temporary), CAIRN_BOOTSTRAP_EFFECT_PLAN=str(spec))
    child = control.command([images['tests'], '-test.run=^TestUnitBootstrapOwnedTransition$', '-test.v', '-test.timeout=90s'],
                            f.root / label, env=env, timeout=100)
    require('--- PASS: TestUnitBootstrapOwnedTransition' in child.stdout and '--- SKIP:' not in child.stdout, 'owned bootstrap test did not run')
    data = json.loads(result.read_bytes())
    require(data['result'] == 'PASS' and data['operation'] == operation, 'bootstrap operation receipt mismatch')
    require(not data.get('published', False) if refusal else data['transition']['published'], 'bootstrap publication contradicted expected boundary')
    return data


def watch_process(f, script, label):
    out, err = f.root / (label + '.stdout'), f.root / (label + '.stderr')
    argv = [sys.executable, '-I', '-S', '-B', str(script), 'watch', '--config-dir', str(f.active)]
    control.write(f.root / (label + '.argv.json'), argv)
    with out.open('wb') as stdout, err.open('wb') as stderr:
        process = subprocess.Popen(argv, env=f.host.env, stdout=stdout, stderr=stderr)
    control.write(f.root / (label + '-start.json'), dict(pid=process.pid, process=coordination.process_reference(process.pid),
        api=coordination.process_reference(f.host.process.pid)))
    return process, err


def stop_watcher(f, process, label):
    process.terminate()
    code = process.wait(timeout=5)
    control.write(f.root / (label + '-exit.json'), dict(pid=process.pid, exit=code, joined=True, stopped_before_exchange=True))
    require(code == 0, 'captured watcher did not stop cleanly before exchange')


def wake_and_settle(f, agent, state_path, owner, argv, native, watcher, stderr, label):
    session = {key: agent[key] for key in ('agent_id', 'execution_id')}
    source = f.call('sender', 'create', dict(request_id=str(uuid.uuid4()), draft=dict(kind='note', body='scripted owner watcher fixture ' + label,
        scope=dict(repo=f.repo, task_id='*', run_id='*'), claim_type='self', sensitivity='shareable')))
    event = f.call('sender', 'event-publish', dict(request_id=str(uuid.uuid4()), kind='notice',
        ref=dict(record_id=source['record_id'], version=source['version']), destination=dict(type='agent', name=agent['inbox'])))
    delivery = f.call('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
    prompts = f.root / 'prompts.jsonl'
    def submitted():
        if not prompts.exists():
            return False
        return any(delivery['delivery_id'] in row for row in prompts.read_text().splitlines())
    await_fact(submitted, seconds=40, label=label + ' scripted owner wake', watcher=watcher, stderr=stderr)
    # Go hook_cli.handleHookWithLock -> OpenSession -> AdmitInbox, or the
    # captured /2 handle -> session_lock -> NativeSession.admit_inbox. Both
    # persist the intent before claim. SESSION_BUSY ends this case, no retry.
    hook = owner_hook(f, owner, argv, native, label + '-owner')
    state = json.loads(state_path.read_bytes())
    attempt = state.get('inbox_attempt')
    require(attempt and attempt['delivery']['delivery_id'] == delivery['delivery_id'], label + ': maintained owner did not claim selected delivery')
    control.write(f.root / (label + '-admitted-state.json'), state)
    context = json.loads((state_path.parent / 'inbox' / (attempt['attempt_id'] + '.json')).read_bytes())
    require(context['delivery_id'] == delivery['delivery_id'], label + ': owner handoff changed delivery')
    for role in ('completion', 'acknowledgement', 'response', 'finish_reply'):
        if role in context:
            retain_owner_inputs(f, label + '-issued-' + role, context[role])
    progress = dict(delivery=delivery['delivery_id'], attempt=attempt['attempt_id'],
                    hook=hook, admission='guarded maintained hook', settlement='UNESTABLISHED')
    control.write(f.root / (label + '-owner-progress.json'), progress)
    ack = owner_command(f, owner, dict(label=label + '-owner-ack', argv=context['acknowledgement']))
    progress['ack'] = ack
    control.write(f.root / (label + '-owner-progress.json'), progress)
    await_fact(lambda: 'inbox_intent' not in json.loads(state_path.read_bytes()), seconds=40,
               label=label + ' watcher settlement', watcher=watcher, stderr=stderr)
    remote = f.call('worker', 'session-inbox-control', session)
    require(not remote.get('attempt'), label + ': owner attempt remains held after watcher settlement')
    progress['settlement'] = 'PASS/authenticated API hold released'
    control.write(f.root / (label + '-owner-progress.json'), progress)
    control.write(f.root / (label + '-settled-state.json'), json.loads(state_path.read_bytes()))
    stop = owner_hook(f, owner, [*argv[:-2], '--event', 'Stop'] if argv[-2:] == ['--event', 'PreInvocation'] else argv,
                      native, label + '-owner-stop', event='Stop')
    return dict(event=event['event_id'], delivery=delivery['delivery_id'], attempt=attempt['attempt_id'],
                wake='actual captured watcher -> scripted terminal', settlement='actual captured watcher -> authenticated disposable API',
                hook=hook, ack=ack, stop=stop, native_agent_or_model=False, lock=held_lock(watcher, f.active / '.watch.lock'))


def run(index, index_sha, sidecar, sidecar_sha, contract, contract_sha, output):
    os.umask(0o077)
    require(not output.exists(), 'NEW watcher proof output required; no automatic rerun')
    output.mkdir(mode=0o700)
    scratch = Path(tempfile.mkdtemp(prefix='cw-', dir=Path.home() / '.cache'))
    cluster = f = owner = watcher = None
    phase = 'setup'
    result = dict(schema='cairn.r07-captured-watcher-fixture/1', result='INCOMPLETE', phase=phase,
                  production_actions=0, native_agent_or_model_calls=0, scripted_owner=True)
    try:
        require(digest(contract.read_bytes()) == contract_sha, 'pre-run owner contract changed')
        proof = json.loads(contract.read_bytes())
        require(proof['decision'] == 'ACTUAL_GO3_AND_CAPTURED_PYTHON2_HOOKS', 'unsupported owner contract')
        for pin in proof['source_pins']:
            raw = Path(pin['path']).read_bytes()
            require(len(raw) == pin['bytes'] and digest(raw) == pin['sha256'], 'pre-run source pin changed')
        shutil.copyfile(contract, output / 'owner-contract-before-run.json')
        images = control.build(scratch)
        for name, target in (('stager', './cmd/cairn-stage-lifecycle'), ('tests', './internal/lifecycle/coordination')):
            images[name] = scratch / 'tools' / name
            command = ['go', 'test', '-c'] if name == 'tests' else ['go', 'build', '-buildvcs=false']
            control.command([*command, '-o', images[name], target], scratch / 'tools' / ('build-' + name))
            images[name].chmod(0o500)
        source = scratch / 'captured424'
        result['source'] = captured_sources(index, index_sha, sidecar, sidecar_sha, source)
        control.write(output / 'built-tools-before-use.json', {name: dict(path=str(path), bytes=path.stat().st_size,
            sha256=digest(path.read_bytes())) for name, path in images.items()})
        cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bindir())
        home = scratch / 'h'
        (home / '.local/share').mkdir(mode=0o700, parents=True)
        # Parent creation is confined to this new disposable fixture.
        for directory in (home, home / '.local', home / '.local/share'):
            directory.chmod(0o700)
        f = control.Fixture(cluster, images['api'], home / '.local/share/cairn', 'watcher_' + uuid.uuid4().hex[:8])
        f.production_home = home
        f.production_layout = bootstrap.prepare_production_state_layout(f, 'proximal')
        f.configs()
        terminal = f.root / 'scripted-terminal'
        terminal.write_text(HOST)
        terminal.chmod(0o700)
        base = json.loads(f.coord.read_bytes())
        base.update(idle_wakeup=str(terminal), agy_home=str(f.root / 'absent-agy-home'))
        control.write(f.coord, base)
        plan, _ = bootstrap.production_effect_plan(f, images, 'proximal')
        material = json.loads((f.root / 'production-material.json').read_bytes())
        require(len(material['original_current']['pins']) == len(material['target_current']['pins']) == 8 and
                len(material['groups']) == 6 and sorted(len(g['members']) for g in material['groups']) == [1, 1, 1, 1, 1, 3],
                'full eight-member/six-cohort target missing')
        original = {p.name: bootstrap.preserved_metadata(p) for p in f.active.iterdir()}
        binding_originals = {g['original_binding']['Path']: (Path(g['original_binding']['Path']).read_bytes(),
            bootstrap.preserved_metadata(Path(g['original_binding']['Path']))) for g in material['groups']}
        lock = (f.active / '.watch.lock').stat().st_ino
        pre = output / 'pre-forward'
        pre.mkdir(mode=0o700)
        control.write(pre / 'metadata.json', dict(active=original, bindings={path: meta for path, (_, meta) in binding_originals.items()}))
        for group in material['groups']:
            target = pre / group['name']
            target.mkdir(mode=0o700)
            for path in Path(group['original_binding']['Path']).parent.glob('*.json'):
                shutil.copyfile(path, target / path.name)
        group = next(g for g in material['groups'] if 'agy-one.json' in g['members'])
        generation = Path(group['target_receipt']['Path']).parent
        selection = json.loads((generation / 'selection.json').read_bytes())
        receipt = json.loads((generation / 'receipt.json').read_bytes())
        vector = selection['anchors']['coordination:1:15']['vector']
        target_argv = [*checked_go_launcher(images['stager'], str(images['control']), 'coordination', vector),
            'hook', '--config', receipt['coordination_configs'][0]['path'], '--event', 'PreInvocation']
        restored_argv = [sys.executable, '-I', '-S', '-B', str(source / 'coordination.py'), 'hook',
            '--config', str(f.active / 'agy-one.json'), '--event', 'PreInvocation']
        control.write(output / 'owner-argv-before-use.json', dict(target3=target_argv, restored2=restored_argv))
        with (f.root / 'owner-actor.stderr').open('wb') as owner_error:
            owner = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), '--owner-actor', str(f.root)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=owner_error, text=True,
                env=dict(os.environ, HERDR_ENV='1', HERDR_SOCKET_PATH=str(f.root / 'scripted-host.sock')))
        control.write(f.root / 'owner-actor-start.json', dict(pid=owner.pid, process=coordination.process_reference(owner.pid),
            api=coordination.process_reference(f.host.process.pid)))
        config = json.loads((f.active / 'agy-one.json').read_bytes())
        native = 'scripted-owner-' + str(uuid.uuid4())
        registration = dict(request_id=str(uuid.uuid4()), binding=config['binding'], native_session_id=native,
            metadata=dict(harness='agy', project='watcher-disposable', workspace=str(f.root), state='idle', delivery_mode='existing-session'))
        agent = f.call('worker', 'agent-register', registration)
        state_path = Path(config['state_dir']) / (digest((config['binding'] + '\0' + native).encode()) + '.json')
        control.write(state_path, dict(schema='cairn.native-session/1', native_session_id=native, agent=agent,
            registration=registration, process=coordination.process_reference(owner.pid), workspace=str(f.root), inbox_journals=[]))
        control.write(f.root / 'fixture.json', dict(host=dict(agent='agy', agent_status='idle', pane_id='fixture:owner', terminal_id='fixture-terminal',
            revision=1, state_change_seq=1, focused=False, agent_session=dict(kind='id', value=native, agent='agy')),
            process_info=dict(foreground_processes=[dict(pid=owner.pid)], foreground_process_group_id=os.getpgid(owner.pid))))
        result.update(members=8, cohorts=6, shared_codex_members=3, same_lock_inode=lock,
                      api='real disposable API/PG/auth/socket/client; fixture fence only', default_manager_positive=False)
        phase = 'original watcher held-lock refusal'
        watcher, errors = watch_process(f, source / 'coordination.py', 'original-watcher')
        await_fact(lambda: held_lock(watcher, f.active / '.watch.lock'), seconds=10, label=phase, watcher=watcher, stderr=errors)
        result['held_forward_refusal'] = operation(f, images, plan, 'held-forward', 'switch', refusal='resource temporarily unavailable')
        stop_watcher(f, watcher, 'original-watcher')
        watcher = None
        phase = 'stopped forward exchange'
        result['forward'] = operation(f, images, plan, 'stopped-forward', 'switch')
        require((f.active / '.watch.lock').stat().st_ino == lock and len(list(f.active.glob('*.json'))) == 8, 'forward lock/member identity lost')
        phase = 'target3 watcher owner wake and settlement'
        watcher, errors = watch_process(f, source / 'coordination.py', 'target3-watcher')
        await_fact(lambda: held_lock(watcher, f.active / '.watch.lock'), seconds=10, label=phase, watcher=watcher, stderr=errors)
        result['target3_owner'] = wake_and_settle(f, agent, state_path, owner, target_argv, native, watcher, errors, 'target3')
        phase = 'target3 held-reverse refusal'
        result['held_reverse_refusal'] = operation(f, images, plan, 'held-reverse', 'reverse', refusal='resource temporarily unavailable')
        stop_watcher(f, watcher, 'target3-watcher')
        watcher = None
        phase = 'stopped reverse exchange'
        result['reverse'] = operation(f, images, plan, 'stopped-reverse', 'reverse')
        require(original == {p.name: bootstrap.preserved_metadata(p) for p in f.active.iterdir()}, 'reverse lost original directory bytes/inodes/modes')
        require(all(Path(p).read_bytes() == raw and bootstrap.preserved_metadata(Path(p)) == identity for p, (raw, identity) in binding_originals.items()),
                'reverse lost original association bytes/inodes/modes')
        phase = 'restored2 watcher owner wake and settlement'
        watcher, errors = watch_process(f, source / 'coordination.py', 'restored2-watcher')
        await_fact(lambda: held_lock(watcher, f.active / '.watch.lock'), seconds=10, label=phase, watcher=watcher, stderr=errors)
        result['restored2_owner'] = wake_and_settle(f, agent, state_path, owner, restored_argv, native, watcher, errors, 'restored2')
        stop_watcher(f, watcher, 'restored2-watcher')
        watcher = None
        require(all(digest((source / name).read_bytes()) == pin for name, pin in result['source']['files'].items()) and
                digest((source / 'lifecycle-bundle.json').read_bytes()) == sidecar_sha, 'captured watcher source/sidecar changed')
        result.update(result='PASS', phase='complete', stopped_step_A=True, captured_code_modified=False,
                      admission='disposable proof only; initial R07 and later Go watcher admission remain separate')
    except BaseException as error:
        result.update(result='STOP', phase=phase, error=str(error), further_watcher_qualification='STOP; report Root for BOTH W=B before widening')
        raise
    finally:
        if watcher is not None:
            stop_process(watcher, timeout=5, label='owned captured watcher')
            control.write(output / 'cleanup-watcher-join.json', dict(pid=watcher.pid, exit=watcher.returncode, joined=watcher.poll() is not None))
        if owner is not None:
            owner.stdin.close()
            owner.wait(timeout=5)
            control.write(output / 'cleanup-owner-join.json', dict(pid=owner.pid, exit=owner.returncode, joined=owner.poll() is not None))
        if f is not None:
            # Selected fixture receipts only; never copy credentials or raw sessions.
            for p in f.root.iterdir():
                if p.is_file() and (p.suffix in ('.stdout', '.stderr') or p.name.startswith('actor-') or
                        p.name.endswith(('-result.json', '-exit.json', '.argv.json', '-test.json', '.start.json', '.join.json', '-start.json', '-state.json', '.input.json', '-progress.json')) or
                        p.name in ('effect-bootstrap-plan.json', 'target-bootstrap-plan.json', 'production-material.json', 'prompts.jsonl')):
                    shutil.copyfile(p, output / p.name)
            custody = f.root / 'issued-custody'
            if custody.exists():
                shutil.copytree(custody, output / 'issued-custody')
            material_path = f.root / 'production-material.json'
            if material_path.exists():
                material = json.loads(material_path.read_bytes())
                for group in material['groups']:
                    for role in ('original_binding', 'target_binding'):
                        directory = Path(group[role]['Path']).parent
                        target = output / 'fixture-inputs' / group['name'] / role
                        target.mkdir(mode=0o700, parents=True)
                        for selected in directory.glob('*.json'):
                            shutil.copyfile(selected, target / selected.name)
            process = f.host.process
            code = f.host.close()
            result['api_cleanup'] = dict(pid=process.pid, exit=code, joined=process.poll() is not None)
        tools = scratch / 'tools'
        if tools.exists():
            retained = output / 'build-receipts'
            retained.mkdir(mode=0o700)
            for path in tools.iterdir():
                if path.is_file() and path.suffix in ('.json', '.stdout', '.stderr'):
                    shutil.copyfile(path, retained / path.name)
        if cluster is not None:
            cluster.remove()
            result['cluster_cleanup'] = dict(removed=True)
        result['owned_scratch'] = str(scratch)
        shutil.rmtree(scratch)
        result['scratch_removed'] = not scratch.exists()
        result['cases'] = {name: 'PASS' if name in result else 'NOT_RUN' for name in
            ('held_forward_refusal', 'forward', 'target3_owner', 'held_reverse_refusal', 'reverse', 'restored2_owner')}
        control.write(output / 'result.json', result)
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--owner-actor':
        owner_actor(Path(sys.argv[2]))
        raise SystemExit(0)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-index', required=True, type=Path)
    parser.add_argument('--source-index-sha256', required=True)
    parser.add_argument('--captured-sidecar', required=True, type=Path)
    parser.add_argument('--captured-sidecar-sha256', required=True)
    parser.add_argument('--owner-contract', required=True, type=Path)
    parser.add_argument('--owner-contract-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    run(args.source_index, args.source_index_sha256, args.captured_sidecar, args.captured_sidecar_sha256,
        args.owner_contract, args.owner_contract_sha256, args.output)
