#!/usr/bin/env python3
"""Exercise existing owner memory operations against the real R06 drain reader."""
import argparse
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

import check_r06_unit_control as control
from check_r06_status_consumer import Fixture as StatusFixture
from check_r06_unit_census import process_ref
from disposable_host import OwnedCluster, pg_bindir, require
from test_f04_activation import SESSION, TURN, encoded, scalar_tree
from test_r04_hook_settings import HookFixture

ROOT = Path(__file__).resolve().parents[1]


class Fixture(StatusFixture):
    def __init__(self, *args):
        super().__init__(*args)
        control.write(self.root / 'api-launch.json', process_ref(self.host.process.pid))

    def register(self):
        self.native = SESSION
        return super().register()

    def call(self, role, operation, body, session=None, expected='OK'):
        if operation == 'session-inbox-claim':
            body = dict(body, native_turn_id=TURN)
        return super().call(role, operation, body, session=session, expected=expected)


def memory_state(directory):
    result = {}
    if directory.exists():
        for path in sorted(directory.iterdir()):
            if path.suffix == '.lock':
                continue
            body = json.loads(path.read_bytes())
            for label in ('last_recall', 'last_capture'):
                for key in ('at', 'duration_ms', 'elapsed_seconds'):
                    measurement = body.get(label, {}).pop(key, None)
                    if measurement is not None:
                        require(type(measurement) in (int, float) and measurement >= 0,
                                'invalid existing runtime measurement')
            result[path.name] = body
    return result


def file_pins(directory):
    return {str(p.relative_to(directory)): dict(bytes=p.stat().st_size,
            sha256=control.sha(p.read_bytes())) for p in sorted(directory.rglob('*'))
            if p.is_file() and p.suffix != '.lock'}


def run_owner(hook, side, event, evidence):
    timeout = 150 if event['hook_event_name'] in ('Stop', 'SessionEnd', 'PreCompact') else 13
    if side == 'go':
        vector = [str(hook.binary), 'memory', '--receipt', str(hook.receipt_path),
                  '--receipt-sha256', hook.receipt_sha, '--role', 'memory',
                  '--entrypoint', 'memory-hook.py', '--config', str(hook.memory_path),
                  '--hook-timeout', str(timeout), '--launch-root', str(hook.launch_root),
                  '--shell', '/bin/sh']
        raw = ('#!/bin/sh\nexec ' + shlex.join(vector) + ' -- "$@"\n').encode()
        launcher = hook.launch_root / control.sha(raw) / 'inbox'
        launcher.parent.mkdir(mode=0o700, exist_ok=True)
        if launcher.exists():
            require(launcher.read_bytes() == raw, 'checked launcher changed')
        else:
            launcher.write_bytes(raw)
            launcher.chmod(0o500)
    else:
        launcher = hook.launcher('memory', timeout)
    control.write(evidence.with_suffix('.event.json'), event)
    control.write(evidence.with_suffix('.before-use.json'), dict(
        launcher=dict(path=str(launcher), sha256=control.sha(launcher.read_bytes())),
        image=dict(path=str(hook.binary), sha256=control.sha(hook.binary.read_bytes())),
        receipt=dict(path=str(hook.receipt_path), sha256=control.sha(hook.receipt_path.read_bytes()))))
    argv = [str(launcher)]
    control.write(evidence.with_suffix('.argv.json'), argv)
    result = subprocess.run(argv, input=encoded(event), cwd=hook.root, env=hook.env(),
                            capture_output=True, timeout=timeout + 10)
    evidence.with_suffix('.stdout').write_bytes(result.stdout)
    evidence.with_suffix('.stderr').write_bytes(result.stderr)
    control.write(evidence.with_suffix('.exit.json'), result.returncode)
    require(result.returncode == 0 and not result.stderr,
            f'{evidence.name}: owner failed: {result.returncode} {result.stderr!r}')
    return dict(exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode())


def select_disabled(f, hook):
    selected = json.loads(hook.coord_path.read_bytes())
    active = f.active / 'one.json'
    if active.exists():
        active.chmod(0o600)
    control.write(active, selected)
    active.chmod(0o400)
    f.pins = {'one.json': control.sha(active.read_bytes())}
    f.next.chmod(0o600)
    f.next.write_bytes(hook.coord_path.read_bytes() + b'\n')
    f.next.chmod(0o400)


def refusal(f, image, label, expected):
    before = f.snapshot()
    coordination = json.loads((f.active / 'one.json').read_bytes())
    manifest = json.loads(Path(coordination['inbox_recall_binding']).read_bytes())
    memory_config = json.loads(Path(manifest['memory_config']['path']).read_bytes())
    memory = Path(memory_config['state_dir'])
    pins = file_pins(memory)
    result = control.invoke(f, image, 'inspect', control.plan_for(f), label, expected=1)
    require(expected in result.get('error', ''), f'{label}: wrong refusal: {result}')
    require(not result['inspected'] and not result['exchange']['Published'], 'refusal admitted publication')
    require(f.snapshot() == before and file_pins(memory) == pins, 'inspector changed owner state')
    require(control.sha((f.active / 'one.json').read_bytes()) == f.pins['one.json'], 'selection changed')
    control.write(f.root / (label + '.protected.json'), dict(memory=pins, state=before, result=result))
    return result['error']


def owner_case(cluster, images, scratch, harness, side):
    label = harness + '_' + side
    f = Fixture(cluster, images['api'], scratch / label, label)
    try:
        f.retained()
        control.transport(f, images['api'])
        hook = HookFixture(f.root / 'owner', images['control'], stager=images['stager'], harness=harness)
        for config in (hook.memory, hook.coord):
            config.update(cairn=str(f.host.binary), socket=str(f.host.socket),
                          token_file=str(f.host.token('worker')), repo=f.repo)
        hook.memory.update(state_dir=str(f.root / 'memory'), recall_observations=False, capture_observations=False)
        hook.coord.update(state_dir=str(f.state_dir), binding=f.binding)
        hook.publish_configuration(enabled=True)
        event = dict(hook.event)
        event['prompt'] = hook.wake().replace('33333333-3333-4333-8333-333333333333', f.session['agent_id']).replace(
            '44444444-4444-4444-8444-444444444444', f.session['execution_id']).replace(
            '55555555-5555-4555-8555-555555555555', f.state['inbox_attempt']['delivery']['delivery_id'])
        owner = dict(session_id=SESSION, turn_id=TURN, native_owner=event.get('prompt_id'),
                     agent=f.session, native_session_id=f.native, event=event)
        control.write(f.root / 'owner-identity.json', owner)
        first = run_owner(hook, side, event, f.root / 'enabled-owner')
        directory = Path(hook.memory['state_dir'])
        initial = memory_state(directory)
        activation = [body['inbox_activation'] for body in initial.values() if 'inbox_activation' in body]
        require(len(activation) == 1 and list(activation[0].values()) == [True],
                'actual checked owner did not create one positive activation')
        initial_pins = file_pins(directory)
        control.write(f.root / 'enabled-ledgers.json', dict(state=initial, pins=initial_pins))

        # Another whole unit remains positive while this owner attempts disposition.
        independent = HookFixture(f.root / 'independent', images['control'], stager=images['stager'], harness=harness)
        independent.memory.update(state_dir=str(f.root / 'independent-memory'), recall_observations=False, capture_observations=False)
        for config in (independent.memory, independent.coord):
            config.update(cairn=str(f.host.binary), socket=str(f.host.socket), token_file=str(f.host.token('worker')), repo=f.repo)
        independent.publish_configuration(enabled=True)
        independent_event = dict(independent.event, session_id=control.uid(), turn_id=control.uid())
        run_owner(independent, side, independent_event, f.root / 'independent-positive')
        independent_dir = Path(independent.memory['state_dir'])
        independent_pins = file_pins(independent_dir)
        require(any(True in body.get('inbox_activation', {}).values()
                    for body in memory_state(independent_dir).values()), 'independent positive missing')

        enabled_binding = control.sha(hook.binding.read_bytes())
        hook.publish_configuration(enabled=False)
        require(control.sha(hook.binding.read_bytes()) != enabled_binding, 'disabled selection was not staged')
        select_disabled(f, hook)
        disabled = run_owner(hook, side, event, f.root / 'refreshed-disabled-owner')
        require(memory_state(directory) == initial and file_pins(directory) == initial_pins,
                'disabled owner erased or rewrote the positive activation')
        blocked_before = refusal(f, images['control'], 'disabled-positive', 'retained positive or unknown activation grant')

        # These are the existing checked owning-session capture/finalization entries.
        # Empty dialogue intentionally needs no selector/model or native client.
        finals = []
        for name in ('PreCompact', 'Stop', 'SessionEnd'):
            final_event = dict(hook_event_name=name, session_id=SESSION, cwd=str(hook.root), messages=[])
            finals.append(run_owner(hook, side, final_event, f.root / ('owner-' + name)))
            require(any(True in body.get('inbox_activation', {}).values()
                        for body in memory_state(directory).values()), name + ' lost positive activation')
        blocked_after = refusal(f, images['control'], 'finalized-positive', 'retained positive or unknown activation grant')
        require(file_pins(independent_dir) == independent_pins, 'other unit positive grant changed')
        final_state = memory_state(directory)
        control.write(f.root / 'owner-result.json', dict(harness=harness, side=side, first=first,
            disabled=disabled, finals=finals, initial=initial, final=final_state,
            blocked_before=blocked_before, blocked_after=blocked_after,
            independent_before=independent_pins, independent_after=file_pins(independent_dir),
            grant_disposition='BLOCKED: existing owner operations retain the positive activation'))
        return dict(initial=initial, final=final_state, first=first, disabled=disabled, finals=finals)
    finally:
        pid = f.host.process.pid
        require(pid > 1, 'owned API PID must exceed 1')
        code = f.host.close()
        control.write(f.root / 'api-cleanup.json', dict(pid=pid, exit=code, joined=True))


def guard_case(cluster, images, scratch, name):
    f = Fixture(cluster, images['api'], scratch / name, name)
    try:
        f.retained(settle=name != 'unresolved_owner')
        control.transport(f, images['api'])
        if name in ('unknown_decision', 'malformed_decisions'):
            # Separate negative input: never overwrite any owner's positive ledger.
            body = dict(inbox_activation={TURN: 'unknown'} if name == 'unknown_decision' else [])
            control.write(f.root / 'memory' / 'negative.json', body)
            expected = 'activation'
        elif name == 'wrong_owner':
            # A genuine registration under another principal cannot authenticate
            # the retained worker-owned session through that principal's token.
            foreign = f.host.agent('sender', 'agent-register', dict(request_id=control.uid(),
                binding='foreign-owner', native_session_id='foreign-owner',
                metadata=dict(harness='codex', project='r06', workspace=str(f.root), state='idle', delivery_mode='existing-session')))
            control.write(f.root / 'foreign-registration.json', foreign)
            (f.active / 'one.json').chmod(0o600)
            f.next.chmod(0o600)
            f.configs(token=f.host.token('sender'))
            (f.active / 'one.json').chmod(0o400)
            f.next.chmod(0o400)
            expected = 'NOT_FOUND'
        else:
            expected = 'D4'
        error = refusal(f, images['control'], name, expected)
        return dict(case=name, status='PASS', error=error)
    finally:
        pid = f.host.process.pid
        require(pid > 1, 'owned API PID must exceed 1')
        code = f.host.close()
        control.write(f.root / 'api-cleanup.json', dict(pid=pid, exit=code, joined=True))


def check(evidence):
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    scratch = Path(tempfile.mkdtemp(prefix='r06-owner-', dir=Path.home() / '.cache'))
    control.write(evidence / 'scratch.json', dict(path=str(scratch)))
    images = control.build(scratch)
    stager = scratch / 'tools/stager'
    control.command(['go', 'build', '-buildvcs=false', '-o', stager, './cmd/cairn-stage-lifecycle'], scratch / 'tools/build-stager')
    stager.chmod(0o500)
    images['stager'] = stager
    control.command(['go', 'version', '-m', stager], scratch / 'tools/modules-stager')
    control.write(scratch / 'tools/owner-before-use-pins.json', dict(images={k: dict(path=str(v), sha256=control.sha(v.read_bytes())) for k, v in images.items()},
        source={name: control.sha((ROOT / name).read_bytes()) for name in (
            'scripts/check_r06_owner_grant_drain.py', 'scripts/test_f04_activation.py',
            'scripts/test_r02_compatibility_install.py', 'scripts/test_r04_hook_settings.py',
            'integrations/lifecycle/grant_ledger.py', 'internal/lifecycle/ledger/activation.go',
            'internal/lifecycle/ledger/inbox_compaction.go')}))
    pg_bin = pg_bindir().resolve()
    control.write(scratch / 'tools/postgres-before-use-pins.json', {name: dict(path=str(pg_bin / name), sha256=control.sha((pg_bin / name).read_bytes())) for name in ('initdb', 'pg_ctl', 'postgres', 'createdb', 'psql')})
    cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bin)
    outcomes = []
    try:
        for harness in ('codex', 'claude'):
            source = owner_case(cluster, images, scratch, harness, 'source')
            go = owner_case(cluster, images, scratch, harness, 'go')
            # Existing normalization: exact JSON values/types/order, excluding
            # only the checked nonnegative runtime measurements above.
            require(scalar_tree(go) == scalar_tree(source), f'{harness}: Go/Source retained-state/output mismatch')
            outcomes.append(dict(case=harness + '_owner_parity', status='PASS', positive_disposition='BLOCKED'))
            print(json.dumps(outcomes[-1]), flush=True)
        for name in ('unknown_decision', 'malformed_decisions', 'wrong_owner', 'unresolved_owner'):
            outcomes.append(guard_case(cluster, images, scratch, name))
            print(json.dumps(outcomes[-1]), flush=True)
    finally:
        postmaster = scratch / 'pg/data/postmaster.pid'
        pid = int(postmaster.read_text().splitlines()[0]) if postmaster.exists() else None
        require(pid is None or pid > 1, 'owned PostgreSQL PID must exceed 1')
        identity = process_ref(pid) if pid is not None else None
        stopped = cluster.stop()
        control.write(evidence / 'cleanup.json', dict(identity=identity, postgres_stopped=stopped,
            postmaster_pid_files=list(map(str, scratch.rglob('postmaster.pid')))))
        shutil.copytree(scratch, evidence / 'fixtures', ignore=shutil.ignore_patterns(
            'data', 'socket', '*.token', 'identities.json', '*.sock', '*token*'))
        require(stopped, 'owned PostgreSQL cleanup unresolved')
    control.write(evidence / 'outcomes.json', outcomes)
    control.write(evidence / 'disposition.json', dict(component='existing owner grant disposition',
        status='BLOCKED', decision='Source retains positive activation after disabled refresh and ordinary owner finalization; an explicit owner policy/interface decision is required before D1 can become true.',
        full_R06='NOT_ESTABLISHED', production_grant='NONE'))
    print(f'R06 owner evidence: {len(outcomes)} cases passed; positive disposition BLOCKED; full R06 not established', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    check(parser.parse_args().evidence)
