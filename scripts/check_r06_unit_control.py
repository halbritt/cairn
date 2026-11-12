#!/usr/bin/env python3
"""Exercise the private unit-control command against disposable API/Postgres.

Run via mise host:exec. Only fixture actors mutate API state; the command uses
the real read endpoints. No installed account, model, service or HOME changes.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from check_r06_status_consumer import Fixture, ROOT, sha, snapshot, uid, write
from disposable_host import OwnedCluster, pg_bindir, require, run_envelope


READS = ('wake-attempts', 'event-watch', 'session-inbox-control', 'session-inbox-attempt-status')


def forward_api(path, argv):
    """Fixture-owned transport forwards actual responses without substitution.

    On the third wake read the initial receipt already exists. The separately
    recorded actor acquires a real hold before the command's final read. There
    is no injection flag or callback in the production command.
    """
    plan = json.loads(path.read_text())
    operation = argv[-1]
    require(operation in READS, 'control attempted non-read API operation')
    body = sys.stdin.read()
    counter = Path(plan['counter'])
    counts = json.loads(counter.read_text()) if counter.exists() else {}
    counts[operation] = counts.get(operation, 0) + 1
    write(counter, counts)
    if plan.get('claim') and operation == 'wake-attempts' and counts[operation] == 3:
        actor = plan['claim']
        before = snapshot(actor['dsn'], actor['pg_bin'], Path(actor['state_dir']))
        write(path.with_name('actor-before.json'), before)
        result = run_envelope(actor['argv'], actor['body'], expected='OK', timeout=10)
        write(path.with_name('actor-result.json'), result)
        write(path.with_name('actor-after.json'), snapshot(actor['dsn'], actor['pg_bin'], Path(actor['state_dir'])))
    result = subprocess.run([plan['binary'], *argv], input=body, text=True, capture_output=True, timeout=20)
    with Path(plan['calls']).open('a') as stream:
        stream.write(json.dumps(dict(operation=operation, argv=argv, input=body,
                                    stdout=result.stdout, stderr=result.stderr, exit=result.returncode)) + '\n')
    if plan.get('move_parent') and operation == 'event-watch' and counts[operation] == 4:
        # A fixture-owned path race after the final API response forces the
        # accepted library's post-rename readback error. The command has no
        # production fault option and must retain Published=true uncertainty.
        os.rename(plan['move_parent']['from'], plan['move_parent']['to'])
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def command(argv, prefix, *, env=None, expected=0, timeout=60):
    write(prefix.with_suffix('.argv.json'), list(map(str, argv)))
    result = subprocess.run(list(map(str, argv)), cwd=ROOT, env=env, capture_output=True,
                            text=True, timeout=timeout)
    prefix.with_suffix('.stdout').write_text(result.stdout)
    prefix.with_suffix('.stderr').write_text(result.stderr)
    write(prefix.with_suffix('.exit.json'), result.returncode)
    if expected is not None:
        require(result.returncode == expected, f'{prefix}: exit {result.returncode}: {result.stderr} {result.stdout}')
    return result


def build(root):
    tools = root / 'tools'
    tools.mkdir(mode=0o700)
    native = {name: Path(path).resolve() for name, path in
              (('go', shutil.which('go')), ('python', sys.executable), ('shell', '/bin/sh'))}
    pins = dict(native={k: dict(path=str(v), sha256=sha(v.read_bytes())) for k, v in native.items()},
                python=sys.version,
                source={name: sha((ROOT / name).read_bytes()) for name in (
                    'go.mod', 'go.sum', 'cmd/cairn-lifecycle/main.go', 'cmd/cairn-lifecycle/main_test.go',
                    'internal/lifecycle/coordination/shared_unit_control.go',
                    'internal/lifecycle/coordination/shared_unit_control_test.go',
                    'internal/lifecycle/transcript/coordination_unit_control.go',
                    'docs/verification/r06-unit-control.md',
                    'scripts/check_r06_unit_control.py', 'scripts/check_r06_status_consumer.py',
                    'scripts/disposable_host.py')})
    version = command([native['go'], 'env', '-json', 'GOVERSION', 'GOROOT', 'GOTOOLDIR', 'GOTOOLCHAIN'], tools / 'go-env')
    pins['go'] = json.loads(version.stdout)
    pins['compiler_tools'] = {str(p): sha(p.read_bytes()) for p in Path(pins['go']['GOTOOLDIR']).iterdir() if p.is_file()}
    write(tools / 'before-use-pins.json', pins)
    images = {}
    for name, target in (('api', './cmd/cairn'), ('control', './cmd/cairn-lifecycle')):
        image = tools / name
        command([native['go'], 'build', '-buildvcs=false', '-o', image, target], tools / ('build-' + name))
        image.chmod(0o500)
        command([native['go'], 'version', '-m', image], tools / ('modules-' + name))
        images[name] = image
    pins['images'] = {k: dict(path=str(v), sha256=sha(v.read_bytes())) for k, v in images.items()}
    write(tools / 'pins.json', pins)
    return images


def transport(f, api, claim=None, socket=None, move_parent=None):
    plan = f.root / 'transport.json'
    calls = f.root / 'read-calls.jsonl'
    write(plan, dict(binary=str(api), counter=str(f.root / 'read-counts.json'), calls=str(calls), claim=claim,
                     move_parent=move_parent))
    wrapper = f.root / 'read-api'
    # These paths are entirely fixture-owned; argv is forwarded verbatim.
    import shlex
    wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-B', str(Path(__file__).resolve()),
                                                      '--forward-api', str(plan)]) + ' "$@"\n')
    wrapper.chmod(0o500)
    f.host.binary = wrapper
    f.configs(socket=socket)
    (f.active / 'one.json').chmod(0o400)
    f.next.chmod(0o400)
    return calls


def plan_for(f, *, current=None, next_path=None, retained=None, callbacks=()):
    return dict(schema='cairn.unit-control/1', current=current or dict(directory=str(f.active), pins=f.pins),
                next=[dict(name='one.json', path=str(next_path or f.next), sha256=sha((next_path or f.next).read_bytes()))],
                retained=str(retained or f.root / 'retained'), callbacks=list(callbacks) if callbacks is not None else None)


def invoke(f, image, operation, plan, label, *, expected=0, pin=None, mode=0o600, path_tail='plan.json'):
    path = f.root / path_tail
    write(path, plan)
    path.chmod(mode)
    raw = path.read_bytes()
    prefix = f.root / label
    result = command([image, 'unit-control', operation, '--plan', path, '--sha256', pin or sha(raw)],
                     prefix, env=f.host.env, expected=expected)
    parsed = json.loads(result.stdout)
    require(parsed['schema'] == 'cairn.unit-control-result/1', 'missing command result schema')
    return parsed


def pending_claim(f, api):
    source = f.call('sender', 'create', dict(request_id=uid(), draft=dict(kind='note', body='unit-control injection',
        scope=dict(repo=f.repo, task_id='*', run_id='*'), claim_type='self', sensitivity='shareable')))
    event = f.call('sender', 'event-publish', dict(request_id=uid(), kind='notice',
        ref=dict(record_id=source['record_id'], version=source['version']), destination=dict(type='agent', name=f.agent['inbox'])))
    delivery = f.call('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
    return dict(dsn=f.host.dsn, pg_bin=str(f.host.pg_bin), state_dir=str(f.state_dir),
                argv=[str(api), 'agent', *map(str, f.host.connection('worker')), 'session-inbox-claim'],
                body=dict(request_id=uid(), session=f.session, delivery_id=delivery['delivery_id'],
                          native_turn_id='control-injection', turn_exclusive=True))


def run_cases(evidence):
    scratch = Path(tempfile.mkdtemp(prefix='r06-unit-control-', dir=Path.home() / '.cache'))
    write(evidence / 'scratch.json', dict(path=str(scratch)))
    images = build(scratch)
    pg_bin = pg_bindir().resolve()
    pg_pins = {name: dict(path=str(pg_bin / name), sha256=sha((pg_bin / name).read_bytes()))
               for name in ('initdb', 'pg_ctl', 'postgres', 'createdb', 'psql')}
    write(scratch / 'tools/postgres-before-use-pins.json', pg_pins)
    command([pg_bin / 'postgres', '--version'], scratch / 'tools/postgres-version')
    cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bin)
    outcomes = []
    def record(case):
        outcomes.append(dict(case=case, status='PASS'))
        print(json.dumps(outcomes[-1]), flush=True)
    try:
        for case in ('roundtrip', 'inputs', 'affected-owning-attempt', 'hold', 'unavailable', 'api-error', 'claim-invalidation', 'publication-uncertainty'):
            f = Fixture(cluster, images['api'], scratch / case, case)
            moved = None
            try:
                f.retained(settle=case != 'affected-owning-attempt', resume=case == 'roundtrip')
                claim = pending_claim(f, images['api']) if case in ('hold', 'claim-invalidation') else None
                if case == 'hold':
                    run_envelope(claim['argv'], claim['body'], expected='OK', timeout=10)
                if case == 'api-error': f.register()  # Current local ref is now actually stale.
                moved = f.root.with_name('publication-moved') if case == 'publication-uncertainty' else None
                calls = transport(f, images['api'], claim if case == 'claim-invalidation' else None,
                                  socket=f.root / 'missing.sock' if case == 'unavailable' else None,
                                  move_parent=dict(**{'from':str(f.root)}, to=str(moved)) if moved else None)
                before = f.snapshot()
                write(f.root / 'before.json', before)
                if case == 'roundtrip':
                    inspected = invoke(f, images['control'], 'inspect', plan_for(f), 'inspect')
                    require(inspected['inspected'] and not inspected['exchange']['Published'], 'inspect did not stay read-only')
                    require(not (f.active / '.watch.lock').exists(), 'inspect acquired the watcher lock')
                    require(f.snapshot() == before, 'inspect changed protected state')
                    switched = invoke(f, images['control'], 'switch', plan_for(f), 'switch')
                    require(switched['exchange']['Published'] and switched['inspected'], 'switch refused terminal unit')
                    require(os.path.samefile(f.active / '.watch.lock', f.root / 'retained/.watch.lock'), 'switch lost lock identity')
                    current = dict(directory=str(f.active), pins=switched['exchange']['Active']['Pins'])
                    old_lock = f.root / 'retained/.watch.lock'
                    old_lock.unlink();old_lock.touch(mode=0o600)
                    bad_reverse = invoke(f, images['control'], 'reverse', plan_for(f, current=current,
                        next_path=f.root / 'retained/one.json', retained=f.root / 'retained-new'), 'reverse-wrong-lock', expected=None)
                    require(bad_reverse.get('error') and not bad_reverse['inspected'] and not bad_reverse['exchange']['Published'],
                            'reverse bypassed retained lock identity')
                    require(not (f.root / 'retained-new').exists(), 'bad reverse created a preparation')
                    old_lock.unlink();os.link(f.active / '.watch.lock', old_lock)
                    record('reverse-lock-refusal')
                    reversed_ = invoke(f, images['control'], 'reverse', plan_for(f, current=current,
                        next_path=f.root / 'retained/one.json', retained=f.root / 'retained-new'), 'reverse')
                    require(reversed_['exchange']['Published'], 'reverse refused same drain')
                    require({p.name for p in f.active.iterdir()} == {'one.json', '.watch.lock'}, 'partial active membership')
                    require(sha((f.active / 'one.json').read_bytes()) == f.pins['one.json'], 'reverse did not restore exact old bytes')
                    require(os.path.samefile(f.active / '.watch.lock', f.root / 'retained/.watch.lock') and
                            os.path.samefile(f.active / '.watch.lock', f.root / 'retained-new/.watch.lock'), 'reverse split lock inode')
                    require(f.snapshot() == before, 'control mutated protected API/state across roundtrip')
                    record(case)
                elif case == 'inputs':
                    for kind in ('pin', 'shape', 'plan-mode', 'next-mode', 'next-schema', 'census', 'callback-pid', 'callback-start', 'callback-field', 'membership', 'path'):
                        plan = plan_for(f)
                        pin, mode = None, 0o600
                        if kind == 'pin': pin = '0' * 64
                        elif kind == 'shape': plan['complete'] = True
                        elif kind == 'plan-mode': mode = 0o644
                        elif kind == 'next-mode': f.next.chmod(0o600)
                        elif kind == 'next-schema':
                            f.next.chmod(0o600);write(f.next, dict(unknown=True));f.next.chmod(0o400)
                            plan['next'][0]['sha256'] = sha(f.next.read_bytes())
                        elif kind == 'census': plan['callbacks'] = None
                        elif kind == 'callback-pid': plan['callbacks'] = [dict(pid=1,start=1,boot='fixture')]
                        elif kind == 'callback-start': plan['callbacks'] = [dict(pid=2,start=-1,boot='fixture')]
                        elif kind == 'callback-field': plan['callbacks'] = [dict(pid=2,start=1,boot='fixture',complete=True)]
                        elif kind == 'membership': plan['current']['pins']['absent.json'] = '0' * 64
                        elif kind == 'path': plan['retained'] = str(f.root) + '/./retained'
                        refused = invoke(f, images['control'], 'switch', plan, kind, expected=None, pin=pin, mode=mode)
                        require(refused.get('error') and not refused['inspected'] and not refused['exchange']['Published'], kind + ': invalid input accepted')
                        require(not calls.exists(), kind + ': validation reached API before refusing input')
                        require(not (f.root / 'retained').exists() and not (f.active / '.watch.lock').exists(), kind + ': input refusal created effects')
                        require(f.snapshot() == before, kind + ': input refusal changed protected state')
                        f.next.chmod(0o600);f.next.write_bytes(f.coord.read_bytes() + b'\n');f.next.chmod(0o400)
                        record('input-' + kind)
                elif case == 'publication-uncertainty':
                    plan = plan_for(f);path=f.root/'plan.json';write(path,plan)
                    completed=command([images['control'],'unit-control','switch','--plan',path,'--sha256',sha(path.read_bytes())],
                                      evidence/'publication-uncertainty',env=f.host.env,expected=1)
                    result=json.loads(completed.stdout)
                    require(result['inspected'] and result['exchange']['Published'] and result.get('error'), 'post-rename uncertainty lost')
                    after=snapshot(f.host.dsn,f.host.pg_bin,moved/'state')
                    before_files={str(Path(p).relative_to(f.state_dir)):v for p,v in before['state_files'].items()}
                    after_files={str(Path(p).relative_to(moved/'state')):v for p,v in after['state_files'].items()}
                    require(before['database']==after['database'] and before_files==after_files,'path actor/control changed protected contents')
                    require(sha((moved/'bindings/one.json').read_bytes())==plan['next'][0]['sha256'],'uncertain published selection incomplete')
                    require(sha((moved/'retained/one.json').read_bytes())==f.pins['one.json'],'uncertain retained selection incomplete')
                    require(os.path.samefile(moved/'bindings/.watch.lock',moved/'retained/.watch.lock'),'uncertain publish split lock inode')
                    write(moved/'after-path-race.json',after)
                    record(case)
                else:
                    result = invoke(f, images['control'], 'switch', plan_for(f), 'control', expected=None)
                    require(result.get('error') and not result['exchange']['Published'], case + ': unsafe publication')
                    after = f.snapshot();write(f.root / 'after.json', after)
                    if case == 'claim-invalidation':
                        require(result['inspected'] and 'D6 drain receipt invalidated' in result['error'], 'actual claim did not invalidate receipt')
                        require(before == json.loads((f.root / 'actor-before.json').read_text()), 'capture mutated protected state')
                        require(after == json.loads((f.root / 'actor-after.json').read_text()), 'final inspection mutated protected state')
                        require((f.root / 'retained/one.json').read_bytes() == f.next.read_bytes(), 'lost recoverable preparation')
                        require(os.path.samefile(f.active / '.watch.lock', f.root / 'retained/.watch.lock'), 'lost preparation lock identity')
                    else:
                        require(not result['inspected'], case + ': unknown or held state created receipt')
                        require(after == before, case + ': refusal mutated protected state')
                        require(not (f.root / 'retained').exists(), case + ': refusal prepared a selection')
                    require(sha((f.active / 'one.json').read_bytes()) == f.pins['one.json'], case + ': refusal changed active bytes')
                    record(case)
            finally:
                pid = f.host.process.pid
                require(pid > 1, 'owned API PID must exceed 1')
                group = os.getpgid(pid) if f.host.process.poll() is None else None
                require(group is None or group > 1, 'owned API process group must exceed 1')
                code = f.host.close()
                cleanup_root=moved if moved is not None and moved.exists() else f.root
                write(cleanup_root / 'cleanup.json', dict(pid=pid, process_group=group, exit=code, joined=True, group_signals=False))
    finally:
        pid_file = scratch / 'pg/data/postmaster.pid'
        pid = int(pid_file.read_text().splitlines()[0]) if pid_file.exists() else None
        require(pid is None or pid > 1, 'owned PostgreSQL PID must exceed 1')
        group = os.getpgid(pid) if pid is not None else None
        require(group is None or group > 1, 'owned PostgreSQL group must exceed 1')
        stopped = cluster.stop()
        write(evidence / 'cleanup.json', dict(postgres_pid=pid, process_group=group, stopped=stopped, group_signals=False))
        require(stopped, 'owned PostgreSQL cleanup unresolved')
        shutil.copytree(scratch, evidence / 'fixtures', ignore=shutil.ignore_patterns('data', 'socket', '*.token', 'identities.json', 'api.sock'))
    write(evidence / 'outcomes.json', outcomes)
    print(f'R06 unit control: {len(outcomes)} passed, 0 failed, 0 skipped', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--forward-api', type=Path)
    args, forwarded = parser.parse_known_args()
    if args.forward_api:
        raise SystemExit(forward_api(args.forward_api, forwarded))
    require(args.evidence is not None and not forwarded, '--evidence required')
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    run_cases(args.evidence)
