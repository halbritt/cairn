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
from test_f04_deadlines import ROWS
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
            body['native_turn_id'] = TURN
        return super().call(role, operation, body, session=session, expected=expected)


class OwnerFixture(HookFixture):
    def __init__(self, *args, source_revision, **kwargs):
        self.source_revision = source_revision
        super().__init__(*args, **kwargs)

    def configuration_plan(self, **kwargs):
        plan = super().configuration_plan(**kwargs)
        plan['source_revision'] = self.source_revision
        return plan


class CurrentFixture(Fixture):
    def __init__(self, *args, harness):
        self.harness = harness
        super().__init__(*args)

    def register(self):
        self.native = SESSION
        return self.call('worker', 'agent-register', dict(request_id=control.uid(), binding=self.binding,
            native_session_id=self.native, metadata=dict(harness=self.harness, project='r06-currentness',
            workspace=str(self.root), state='idle', delivery_mode='existing-session')))

    def call(self, role, operation, body, session=None, expected='OK'):
        if operation == 'session-inbox-claim':
            body['native_turn_id'] = TURN if self.harness == 'codex' else 'claude-channel:fixture-prompt'
        return StatusFixture.call(self, role, operation, body, session=session, expected=expected)


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


def source_activation(f, harness, event):
    directory = f.root / 'python-owner-ledger'
    # Call the unmodified Python owner operation independently. The installed
    # /3 Codex start frontend delegates to Go and is only a frontend control.
    code = '''import importlib.util,json,pathlib,sys
spec=importlib.util.spec_from_file_location('owner_ledger',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
memory=dict(state_dir=sys.argv[2],context_bytes=9500)
event=json.loads(sys.argv[4]);harness=sys.argv[3]
turn=event['turn_id'] if harness=='codex' else m.claude_owner(event)
first=m.activation_decision(memory,harness,event['session_id'],turn,True)
before={p.name:p.read_bytes() for p in pathlib.Path(sys.argv[2]).iterdir() if p.suffix!='.lock'}
disabled=m.activation_decision(memory,harness,event['session_id'],turn,False)
after={p.name:p.read_bytes() for p in pathlib.Path(sys.argv[2]).iterdir() if p.suffix!='.lock'}
if first is not True or disabled is not True or before!=after:raise AssertionError('Source owner activation changed')
print(json.dumps(dict(first=first,disabled=disabled,turn=turn)))
'''
    result = control.command([sys.executable, '-I', '-S', '-B', '-c', code,
        ROOT / 'integrations/lifecycle/grant_ledger.py', directory, harness, json.dumps(event)],
        f.root / 'python-owner-ledger', env=f.host.env)
    control.write(f.root / 'python-owner-ledger-result.json', dict(result=json.loads(result.stdout),
        state=memory_state(directory), pins=file_pins(directory)))
    return memory_state(directory)


def run_activation(hook, event, evidence, *, expected=True):
    # This existing checked operation is used by both immutable frontends.
    argv = hook.activation_argv('memory')
    control.write(evidence.with_suffix('.event.json'), event)
    control.write(evidence.with_suffix('.argv.json'), argv)
    control.write(evidence.with_suffix('.before-use.json'), dict(
        image=dict(path=str(hook.binary), sha256=control.sha(hook.binary.read_bytes())),
        receipt=dict(path=str(hook.receipt_path), sha256=control.sha(hook.receipt_path.read_bytes()))))
    result = subprocess.run(argv, input=encoded(event), cwd=hook.root, env=hook.env(), capture_output=True, timeout=15)
    evidence.with_suffix('.stdout').write_bytes(result.stdout)
    evidence.with_suffix('.stderr').write_bytes(result.stderr)
    control.write(evidence.with_suffix('.exit.json'), result.returncode)
    require(result.returncode == 0 and not result.stderr and json.loads(result.stdout) == dict(enabled=expected),
            f'{evidence.name}: checked activation failed: {result.returncode} {result.stderr!r}')
    return dict(exit=result.returncode, stdout=result.stdout.decode(), stderr=result.stderr.decode())


def producer_activation(hook, side, event, evidence, *, expected=True):
    if side == 'go':
        return run_activation(hook, event, evidence, expected=expected)
    code = '''import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('owner_source',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
memory=json.load(open(sys.argv[2]));event=json.loads(sys.argv[4]);binding=json.load(open(sys.argv[3]))
turn=event['turn_id'] if memory['harness']=='codex' else m.claude_owner(event)
enabled=m.activation_decision(memory,memory['harness'],event['session_id'],turn,binding['enabled'])
print(json.dumps(dict(enabled=enabled)))
'''
    result = control.command([sys.executable, '-I', '-S', '-B', '-c', code,
        ROOT / 'integrations/lifecycle/grant_ledger.py', hook.memory_path, hook.binding, json.dumps(event)], evidence)
    require(json.loads(result.stdout) == dict(enabled=expected), 'Source activation decision differs')


def seed_opaque_owner_fields(hook, evidence):
    code = '''import importlib.util,json,sys
spec=importlib.util.spec_from_file_location('owner_source',sys.argv[1])
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
config=json.load(open(sys.argv[2]));ledger=m.GrantLedger(config['state_dir'],sys.argv[3])
with ledger.locked():
    if config['harness']=='codex':
        state=ledger.load_codex(9500);state['pending']['fixture_opaque']={'text':'日本語 😀','integer':900719925474099312345}
        ledger.save_codex(state)
    else:
        state=ledger.load_inbox();state['fixture_opaque']={'text':'日本語 😀','integer':900719925474099312345}
        ledger.save_inbox(state)
print(json.dumps({'seeded':True}))
'''
    control.command([sys.executable, '-I', '-S', '-B', '-c', code,
        ROOT / 'integrations/lifecycle/grant_ledger.py', hook.memory_path, SESSION], evidence)


def ordinary_codex_turn(hook, side, event, evidence, api):
    """Use actual ordinary owner bodies, without a synthetic currentness oracle."""
    if side == 'go':
        return run_owner(hook, side, event, evidence)
    config = json.loads(hook.memory_path.read_bytes())
    require(json.loads(hook.binding.read_bytes())['enabled'] is False, 'Source body requires selected disabled B')
    # This is the effective unbound body selected by disabled B. The Source
    # body uses the actual CLI; checked Go uses the actual SDK at the same API.
    config.pop('inbox_recall_binding')
    config['cairn'] = str(api)
    path = evidence.with_suffix('.config.json')
    control.write(path, config)
    control.write(evidence.with_suffix('.event.json'), event)
    argv = [sys.executable, '-I', '-S', '-B', str(ROOT / 'integrations/lifecycle/memory.py'), '--config', str(path)]
    control.write(evidence.with_suffix('.argv.json'), argv)
    result = subprocess.run(argv, input=encoded(event), cwd=hook.root, env=hook.env(), capture_output=True, timeout=20)
    evidence.with_suffix('.stdout').write_bytes(result.stdout)
    evidence.with_suffix('.stderr').write_bytes(result.stderr)
    control.write(evidence.with_suffix('.exit.json'), result.returncode)
    require(result.returncode == 0 and not result.stderr, f'Source ordinary owner failed: {result.stderr!r}')


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


def owner_case(cluster, images, scratch, harness, side, revision):
    label = harness + '_' + side
    f = Fixture(cluster, images['api'], scratch / label, label)
    try:
        f.retained()
        control.transport(f, images['api'])
        baseline_state = f.snapshot()
        baseline = control.invoke(f, images['control'], 'inspect', control.plan_for(f), 'settled-baseline')
        require(baseline['inspected'] and not baseline['exchange']['Published'] and not baseline.get('error'),
                'settled fixture did not establish its real API baseline')
        require(f.snapshot() == baseline_state, 'baseline inspection changed protected state')
        hook = OwnerFixture(f.root / 'owner', images['control'], stager=images['stager'], harness=harness, source_revision=revision)
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
        first = run_activation(hook, event, f.root / 'enabled-owner')
        directory = Path(hook.memory['state_dir'])
        initial = memory_state(directory)
        reference = source_activation(f, harness, event)
        require(scalar_tree(initial) == scalar_tree(reference), 'checked owner differs from independent Python owner ledger')
        activation = [body['inbox_activation'] for body in initial.values() if 'inbox_activation' in body]
        require(len(activation) == 1 and list(activation[0].values()) == [True],
                'actual checked owner did not create one positive activation')
        initial_pins = file_pins(directory)
        control.write(f.root / 'enabled-ledgers.json', dict(state=initial, pins=initial_pins))

        # Another whole unit remains positive while this owner attempts disposition.
        independent = OwnerFixture(f.root / 'independent', images['control'], stager=images['stager'], harness=harness, source_revision=revision)
        independent.memory.update(state_dir=str(f.root / 'independent-memory'), recall_observations=False, capture_observations=False)
        for config in (independent.memory, independent.coord):
            config.update(cairn=str(f.host.binary), socket=str(f.host.socket), token_file=str(f.host.token('worker')), repo=f.repo)
        independent.publish_configuration(enabled=True)
        independent_event = dict(independent.event, session_id=control.uid(), turn_id=control.uid())
        run_activation(independent, independent_event, f.root / 'independent-positive')
        independent_dir = Path(independent.memory['state_dir'])
        independent_pins = file_pins(independent_dir)
        require(any(True in body.get('inbox_activation', {}).values()
                    for body in memory_state(independent_dir).values()), 'independent positive missing')

        enabled_binding = control.sha(hook.binding.read_bytes())
        hook.publish_configuration(enabled=False)
        require(control.sha(hook.binding.read_bytes()) != enabled_binding, 'disabled selection was not staged')
        select_disabled(f, hook)
        disabled = run_activation(hook, event, f.root / 'refreshed-disabled-owner')
        require(memory_state(directory) == initial and file_pins(directory) == initial_pins,
                'disabled owner erased or rewrote the positive activation')
        blocked_before = refusal(f, images['control'], 'disabled-positive', 'retained positive or unknown activation grant')

        # These are the existing checked owning-session capture/finalization entries.
        # Empty dialogue intentionally needs no selector/model or native client.
        finals = []
        for name in ROWS[harness]['memory']:
            if name not in ('PreCompact', 'Stop', 'SessionEnd'):
                continue
            final_event = dict(hook_event_name=name, session_id=SESSION, cwd=str(hook.root), messages=[])
            finals.append(run_owner(hook, side, final_event, f.root / ('owner-' + name)))
            require(any(True in body.get('inbox_activation', {}).values()
                        for body in memory_state(directory).values()), name + ' lost positive activation')
            current_pins = file_pins(directory)
            require(all(current_pins.get(path) == pin for path, pin in initial_pins.items()),
                    name + ' changed retained ledger bytes, accounting or initialization')
        blocked_after = refusal(f, images['control'], 'finalized-positive', 'retained positive or unknown activation grant')
        require(file_pins(independent_dir) == independent_pins, 'other unit positive grant changed')
        final_state = memory_state(directory)
        control.write(f.root / 'owner-result.json', dict(harness=harness, side=side, first=first,
            independent_python_activation=reference,
            activation_origin='receipt-checked Go binding activation / independent Python ledger operation',
            capture_origin='installed Python Source' if side == 'source' else 'checked Go memory',
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
        if name == 'wrong_owner':
            foreign = f.host.agent('sender', 'agent-register', dict(request_id=control.uid(),
                binding='foreign-owner', native_session_id='foreign-owner',
                metadata=dict(harness='codex', project='r06', workspace=str(f.root), state='idle', delivery_mode='existing-session')))
            control.write(f.root / 'foreign-registration.json', foreign)
        control.transport(f, images['api'])
        if name in ('unknown_decision', 'malformed_decisions'):
            # Separate negative input: never overwrite any owner's positive ledger.
            body = dict(inbox_activation={TURN: 'unknown'} if name == 'unknown_decision' else [])
            control.write(f.root / 'memory' / 'negative.json', body)
            expected = 'activation'
        elif name == 'wrong_owner':
            # A genuine registration under another principal cannot authenticate
            # the retained worker-owned session through that principal's token.
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


def close_refresh_actor(plan_path):
    """Retain a new real owner close request after the same API confirms it."""
    plan = json.loads(plan_path.read_bytes())
    body = json.loads(sys.stdin.read())
    result = subprocess.run(plan['argv'], input=json.dumps(body), text=True, capture_output=True, timeout=15)
    reply = json.loads(result.stdout)
    require(result.returncode == 0 and reply.get('ok') is True and reply['data'].get('finished_at'),
            'ordinary owner close refresh did not commit')
    path = Path(plan['state'])
    state = json.loads(path.read_bytes())
    require(state['inbox_attempt']['attempt_id'] == body['attempt_id'] and state['agent']['agent_id'] == body['session']['agent_id'],
            'close refresh actor ownership mismatch')
    before = control.sha(path.read_bytes())
    state['inbox_close'] = body
    control.write(path, state)
    control.write(plan_path.with_suffix('.result.json'), dict(api_reply=reply, before_sha256=before,
        after_sha256=control.sha(path.read_bytes()), request=body))
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def currentness_forward(plan_path, argv):
    # Forward the actual read response unchanged. Refresh the owning close
    # only after capture's last store read, before the final D6 file reread.
    code = control.forward_api(plan_path, argv)
    plan = json.loads(plan_path.read_bytes())
    counts = json.loads(Path(plan['counter']).read_bytes())
    if argv[-1] == 'event-watch' and counts['event-watch'] == 2:
        actor = plan['close_refresh']
        before = control.snapshot(actor['dsn'], actor['pg_bin'], Path(actor['state_dir']))
        control.write(plan_path.with_name('close-actor-before.json'), before)
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--refresh-close-actor', actor['plan']]
        result = subprocess.run(command, input=json.dumps(actor['body']), text=True, capture_output=True, timeout=20)
        plan_path.with_name('close-actor.stdout').write_text(result.stdout)
        plan_path.with_name('close-actor.stderr').write_text(result.stderr)
        control.write(plan_path.with_name('close-actor.exit.json'), result.returncode)
        control.write(plan_path.with_name('close-actor-after.json'),
            control.snapshot(actor['dsn'], actor['pg_bin'], Path(actor['state_dir'])))
        require(result.returncode == 0 and json.loads(result.stdout).get('ok') is True,
                'actual owner close refresh failed')
    return code


def currentness_case(cluster, images, scratch, harness, side, case, revision):
    label = harness + '_' + side + '_' + case
    f = CurrentFixture(cluster, images['api'], scratch / label, label, harness=harness)
    try:
        ended = (case in ('ended_settled', 'ended_nonterminal', 'ended_mismatched_close', 'newer_false', 'close_invalidation')
                 or harness == 'codex' and case == 'current_active')
        f.retained(settle=case != 'ended_nonterminal', cleanup=ended)
        if case == 'ended_mismatched_close':
            other = CurrentFixture(cluster, images['api'], scratch / (label + '_wrong_close'),
                                   label + '_wrong_close', harness=harness)
            try:
                other.retained(cleanup=True)
                control.write(f.root / 'mismatched-close-origin.json', dict(
                    original=f.state['inbox_close'], unrelated_real_close=other.state['inbox_close'],
                    unrelated_actor_state=str(other.state_path)))
                # Deliberately misassociate a real other-owner close in this
                # negative fixture; no terminal fields or clearance are invented.
                f.state['inbox_close'] = other.state['inbox_close']
                control.write(f.state_path, f.state)
            finally:
                pid = other.host.process.pid
                require(pid > 1, 'owned witness API PID must exceed 1')
                code = other.host.close()
                control.write(other.root / 'api-cleanup.json', dict(pid=pid, exit=code, joined=True))
        control.transport(f, images['api'])
        hook = OwnerFixture(f.root / 'owner', images['control'], stager=images['stager'],
                            harness=harness, source_revision=revision)
        for config in (hook.memory, hook.coord):
            config.update(cairn=str(f.host.binary), socket=str(f.host.socket),
                          token_file=str(f.host.token('worker')), repo=f.repo)
        hook.memory.update(state_dir=str(f.root / 'memory'), recall_observations=False, capture_observations=False)
        hook.coord.update(state_dir=str(f.state_dir), binding=f.binding)
        hook.publish_configuration(enabled=True)
        event = dict(hook.event)
        seed_opaque_owner_fields(hook, f.root / 'owner-opaque-seed')
        producer_activation(hook, side, event, f.root / 'positive')
        directory = Path(hook.memory['state_dir'])
        positive = memory_state(directory)
        require(any(True in body.get('inbox_activation', {}).values() for body in positive.values()), 'positive activation missing')
        hook.publish_configuration(enabled=False)
        select_disabled(f, hook)
        producer_activation(hook, side, event, f.root / 'disabled', expected=True)
        require(memory_state(directory) == positive, 'disabled refresh rewrote retained positive')
        if harness == 'codex' and case in ('current_active', 'newer_false'):
            ordinary = dict(event, prompt='zzcurrentnessturnprobe')
            ordinary_codex_turn(hook, side, ordinary, f.root / 'current-turn', images['api'])
            budget = memory_state(directory)[SESSION + '.memory-budget.json']
            require(budget['active_turn_id'] == TURN, 'actual ordinary operation did not establish current owner')
        if case == 'newer_false':
            later = dict(event, turn_id='ffffffff-ffff-4fff-8fff-ffffffffffff', prompt_id='later-prompt')
            producer_activation(hook, side, later, f.root / 'newer-false', expected=False)
            if harness == 'codex':
                ordinary = dict(later, prompt='zzcurrentnessturnprobe')
                ordinary_codex_turn(hook, side, ordinary, f.root / 'later-turn', images['api'])
                budget = memory_state(directory)[SESSION + '.memory-budget.json']
                require(budget['active_turn_id'] == later['turn_id'], 'actual ordinary operation did not advance current turn')
        expected_pass = case in ('ended_settled', 'newer_false', 'close_invalidation')
        before = f.snapshot()
        pins = file_pins(directory)
        result = control.invoke(f, images['control'], 'inspect', control.plan_for(f), 'currentness', expected=0 if expected_pass else 1)
        require(f.snapshot() == before and file_pins(directory) == pins, 'currentness inspection mutated retained facts')
        require(not result['exchange']['Published'], 'inspection published bindings')
        if expected_pass:
            require(result['inspected'] and not result.get('error'), 'ended settled owner refused')
        else:
            require(not result['inspected'] and bool(result.get('error')), 'unsafe owner admitted')
            if case not in ('ended_nonterminal', 'ended_mismatched_close'):
                require('D1 retained positive or unknown activation grant' in result['error'], 'unrelated refusal masked D1')
            elif case == 'ended_mismatched_close':
                require(result['error'] == 'D2 retained close does not match the authoritative close',
                        'mismatched close lost exact correspondence refusal')
            else:
                require('D4' in result['error'], 'nonterminal case lost actual hold refusal')
        if case == 'close_invalidation':
            closing = dict(request_id=control.uid(), session=f.session,
                attempt_id=f.state['inbox_attempt']['attempt_id'], reason='delivery_completed')
            actor = f.root / 'refresh-close-actor.json'
            control.write(actor, dict(argv=[str(images['api']), 'agent', *map(str, f.host.connection('worker')),
                'session-inbox-reconcile'], state=str(f.state_path)))
            transport = json.loads((f.root / 'transport.json').read_bytes())
            transport['close_refresh'] = dict(dsn=f.host.dsn, pg_bin=str(f.host.pg_bin), state_dir=str(f.state_dir),
                plan=str(actor), body=closing)
            control.write(f.root / 'transport.json', transport)
            wrapper = f.root / 'read-api'
            wrapper.chmod(0o700)
            wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-B', str(Path(__file__).resolve()),
                '--currentness-forward', str(f.root / 'transport.json')]) + ' "$@"\n')
            wrapper.chmod(0o500)
            # A new public capture/switch observes the new transport bytes.
            counter = f.root / 'read-counts.json'
            control.write(counter, {})
            changed = control.invoke(f, images['control'], 'switch', control.plan_for(f), 'end-fact-change', expected=1)
            require(changed['inspected'] and not changed['exchange']['Published'] and
                    'D6 drain receipt invalidated' in changed.get('error', ''), 'changed close fact did not invalidate D6')
            require(actor.with_suffix('.result.json').exists(), 'ordinary owner close actor never executed')
            require(file_pins(directory) == pins and control.sha((f.active / 'one.json').read_bytes()) == f.pins['one.json'],
                    'D6 refusal changed grants or old complete selection')
        retained = {name: body for name, body in memory_state(directory).items()
                    if name.endswith(('.memory-budget.json', '.inbox-recall.json', '.initialized'))}
        opaque_ledger = retained[SESSION + ('.memory-budget.json' if harness == 'codex' else '.inbox-recall.json')]
        opaque = opaque_ledger['pending']['fixture_opaque'] if harness == 'codex' else opaque_ledger['fixture_opaque']
        require(opaque == dict(text='日本語 😀', integer=900719925474099312345), 'opaque Unicode/integer fields changed')
        control.write(f.root / 'currentness-result.json', dict(harness=harness, producer=side, case=case,
            expected_pass=expected_pass, result=result, ledger=retained, protected_pins=pins))
        return retained
    finally:
        pid = f.host.process.pid
        require(pid > 1, 'owned API PID must exceed 1')
        code = f.host.close()
        control.write(f.root / 'api-cleanup.json', dict(pid=pid, exit=code, joined=True))


def check(evidence):
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    scratch = Path(tempfile.mkdtemp(prefix='r06-owner-', dir=Path.home() / '.cache'))
    control.write(evidence / 'scratch.json', dict(path=str(scratch)))
    status = control.command(['git', 'status', '--porcelain'], evidence / 'source-status-before')
    require(not status.stdout, 'clean committed source required')
    source = control.command(['git', 'rev-parse', 'HEAD', 'HEAD^{tree}'], evidence / 'source-identity')
    revision, tree = source.stdout.splitlines()
    control.write(evidence / 'source.json', dict(head=revision, tree=tree))
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
            source = owner_case(cluster, images, scratch, harness, 'source', revision)
            go = owner_case(cluster, images, scratch, harness, 'go', revision)
            # Existing normalization: exact JSON values/types/order, excluding
            # only the checked nonnegative runtime measurements above.
            require(scalar_tree(go) == scalar_tree(source), f'{harness}: Go/Source retained-state/output mismatch')
            outcomes.append(dict(case=harness + '_owner_parity', status='PASS', positive_disposition='BLOCKED'))
            print(json.dumps(outcomes[-1]), flush=True)
        for name in ('unknown_decision', 'malformed_decisions', 'wrong_owner', 'unresolved_owner'):
            outcomes.append(guard_case(cluster, images, scratch, name))
            print(json.dumps(outcomes[-1]), flush=True)
        for harness in ('codex', 'claude'):
            for case in ('ended_settled', 'current_active', 'ended_nonterminal', 'ended_mismatched_close', 'unknown_end', 'newer_false', 'close_invalidation'):
                source = currentness_case(cluster, images, scratch, harness, 'source', case, revision)
                go = currentness_case(cluster, images, scratch, harness, 'go', case, revision)
                require(scalar_tree(source) == scalar_tree(go), f'{harness}/{case}: independent owner ledgers differ')
                outcomes.append(dict(case=harness + '_' + case, status='PASS'))
                print(json.dumps(outcomes[-1]), flush=True)
    finally:
        identity = None
        try:
            postmaster = scratch / 'pg/data/postmaster.pid'
            pid = int(postmaster.read_text().splitlines()[0]) if postmaster.exists() else None
            require(pid is None or pid > 1, 'owned PostgreSQL PID must exceed 1')
            if pid is not None:
                raw = Path(f'/proc/{pid}/stat').read_text()
                fields = raw[raw.rfind(')') + 1:].split()
                # The daemon's parent may be PID1; only its own PID/group are signalled.
                identity = dict(PID=pid, PGID=int(fields[2]), Parent=int(fields[1]), Start=fields[19],
                                Boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
                require(identity['PGID'] > 1, 'owned PostgreSQL group must exceed 1')
        finally:
            stopped = cluster.stop()
            control.write(evidence / 'cleanup.json', dict(identity=identity, postgres_stopped=stopped,
                postmaster_pid_files=list(map(str, scratch.rglob('postmaster.pid')))))
            shutil.copytree(scratch, evidence / 'fixtures', ignore=shutil.ignore_patterns(
                'data', 'socket', '*.token', 'identities.json', '*.sock', '*token*'))
            require(stopped, 'owned PostgreSQL cleanup unresolved')
    status = control.command(['git', 'status', '--porcelain'], evidence / 'source-status-after')
    require(not status.stdout, 'source changed during run')
    current = control.command(['git', 'rev-parse', 'HEAD', 'HEAD^{tree}'], evidence / 'source-identity-after')
    require(current.stdout.splitlines() == [revision, tree], 'source identity changed during run')
    control.write(evidence / 'outcomes.json', outcomes)
    control.write(evidence / 'disposition.json', dict(component='existing owner grant disposition',
        status='EXISTING_FACT_CURRENTNESS', decision='Historical true is ineffective only with observed ended/noncurrent owning turn and exact authoritative terminal delivery/close; current or unknown refuses.',
        full_R06='NOT_ESTABLISHED', production_grant='NONE'))
    print(f'R06 owner evidence: {len(outcomes)} cases passed; ended settled historical grants classified read-only; full R06 not established', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--refresh-close-actor', type=Path)
    parser.add_argument('--currentness-forward', type=Path)
    args, forwarded = parser.parse_known_args()
    if args.currentness_forward:
        raise SystemExit(currentness_forward(args.currentness_forward, forwarded))
    if args.refresh_close_actor:
        require(not forwarded, 'unexpected close actor arguments')
        raise SystemExit(close_refresh_actor(args.refresh_close_actor))
    require(args.evidence is not None and not forwarded, '--evidence required')
    check(args.evidence)
