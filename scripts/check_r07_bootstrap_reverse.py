#!/usr/bin/env python3
"""R07 selected-file staging and owned bootstrap workflow verification.

The production-shape gate stages genuine /2-/3 groups and exercises the shared
CLI workflow with a private fixture fence. The unchanged default host producer
is tested for active-worker refusal; its first positive remains an admitted R07
gate. No production/native action is performed. Run tests through mise host:exec.
"""

import argparse
from contextlib import contextmanager
import io
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from types import SimpleNamespace

import check_r06_unit_control as control
from disposable_host import HostRefusal, OwnedCluster, pairs_unique, pg_bindir, private_dir, require, run_sql, stop_process, write_private
import semantic_python_closure as semantic


ROOT = Path(__file__).resolve().parents[1]
PROXIMAL = ('agy-one', 'claude-one', 'claude-two', 'codex-one', 'codex-two',
            'codex-three', 'hermes-one', 'opencode-one')
ARCHIVES = (
    'agy-one.before-idle-wake', 'agy-one.before-native-inbox',
    'claude-one.before-channel-detection', 'claude-one.before-idle-wake',
    'claude-one.before-native-inbox', 'claude-two.before-channel-detection',
    'claude-two.before-idle-wake', 'claude-two.before-native-inbox',
    'codex-one.before-idle-wake', 'codex-one.before-native-inbox',
    'codex-three.before-inbox-recall', 'codex-two.before-idle-wake',
    'codex-two.before-native-inbox', 'hermes-one.before-idle-wake',
    'hermes-one.before-native-inbox', 'opencode-one.before-idle-wake',
    'opencode-one.before-native-inbox')
GAPS = {
    'mixed_membership': 'internal/lifecycle/coordination/shared_unit.go:readWatchBindings',
    'private_drain': 'internal/lifecycle/coordination/shared_unit_control.go:RunUnitControl',
    'private_d6': 'internal/lifecycle/coordination/shared_unit_drain.go:recheckPrepared',
    'unbound_retained2': 'internal/lifecycle/coordination/shared_unit_census_unbound.go:unboundReceipt',
    'target_retained2': 'internal/lifecycle/coordination/shared_unit_control_target.go:loadUnitTarget',
    'fixed_identity': 'internal/lifecycle/coordination/shared_unit_control.go:unitControlPairIdentity',
    'fixture_scope': 'internal/lifecycle/coordination/shared_unit_census.go:inspectWatchUnitCensus',
    'semantic_selection': 'scripts/semantic_python_closure.py:verify_generation',
    'semantic_home': 'scripts/semantic_python_closure.py:private_home',
}


def stage_semantic_reverse(payload, original_config, stage, home, *, expected_control=None, publications=None):
    """Publish selected S-04R files through the maintained immutable publisher.

    Does not execute the retained interpreter/model or change an API service.
    The operator keeps the existing API's other arguments and credentials.
    """
    source = semantic.verify_generation(payload['path'], payload['descriptor'])
    descriptor = payload['descriptor']
    require(payload['address'] == semantic.digest(semantic.canonical(descriptor)),
            'semantic reverse payload address mismatch')
    stage, home = Path(stage), Path(home)
    semantic.private_home(home, stage)
    require(not list(home.iterdir()), 'semantic reverse HOME must initially be empty')
    if publications is not None:
        publications = semantic.absolute(publications)
        private_dir(publications)
        require(not list(publications.iterdir()), 'new empty publication-reference directory required')
        for protected in (source, stage, home):
            require(publications != protected and not publications.is_relative_to(protected)
                    and not protected.is_relative_to(publications), 'publication-reference directory overlaps selected generation/HOME')

    def copy_members(pending):
        for name, item in sorted(descriptor['entries'].items()):
            if name == '.':
                continue
            path = pending / name
            if item['type'] == 'directory':
                path.mkdir(mode=0o700)
            elif item['type'] == 'symlink':
                path.symlink_to(item['target'])
            else:
                with path.open('xb') as stream:
                    observed = semantic.regular(source / name, stream)
                    stream.flush()
                    os.fchmod(stream.fileno(), item['mode'])
                    os.fsync(stream.fileno())
                require(observed == item, 'selected semantic source changed while copying')
        semantic.verify_generation(source, descriptor)

    with semantic.stage_root(stage, (source,)) as root:
        target = semantic.publish_generation(root / 'payloads', descriptor, copy_members)
    relocated = dict(address=target.name, path=str(target), descriptor=descriptor)
    controls = semantic.render_python_controls(stage, relocated, original_config, home)
    if expected_control is not None:
        require(controls['address'] == expected_control,
                'relocated semantic control differs from selected descriptor; no API changed')
    semantic.verify_generation(controls['path'], controls['descriptor'])
    references = {}
    if publications is not None:
        for role, selected in (('payload', relocated), ('controls', controls)):
            path = publications / (role + '.json')
            write_private(path, json.dumps(selected, sort_keys=True) + '\n')
            references[role] = str(path)
    # These are the actual cairn serve selectors. They are a command tail, not
    # a made-up JSON configuration consumed by the API.
    flags = ['--embedding-command', controls['launchers']['embed'],
             '--semantic-command', controls['launchers']['rank'],
             '--semantic-stream-command', controls['launchers']['stream']]
    return dict(schema='cairn.r07-semantic-reverse-stage/1',
                payload=dict(path=relocated['path'], address=relocated['address']),
                controls={key: controls[key] for key in ('path', 'address', 'payload_address', 'launchers')},
                home=str(home), serve_selector_flags=flags,
                publications=references,
                qualification='selected-file integrity only; runtime/ABI/effects unqualified',
                service_actions=0, model_executions=0)


def read_selected_json(path, pin):
    path = semantic.absolute(path)
    content = io.BytesIO()
    observed = semantic.regular(path, content)
    require(observed['sha256'] == pin, 'selected JSON pin changed')
    return json.loads(content.getvalue(), object_pairs_hook=pairs_unique)


def operator_semantic_reverse(plan_path, plan_sha, publications=None):
    plan = read_selected_json(plan_path, plan_sha)
    require(isinstance(plan, dict), 'semantic reverse selection must be an object')
    require(set(plan) == {'schema', 'payload', 'source_payload', 'original_config',
                         'stage', 'home', 'expected_control'}, 'unknown semantic reverse selection field')
    require(plan['schema'] == 'cairn.r07-semantic-reverse-selection/1', 'unknown semantic reverse schema')
    for key in ('payload', 'original_config'):
        require(isinstance(plan[key], dict) and set(plan[key]) == {'path', 'sha256'}
                and all(isinstance(value, str) for value in plan[key].values()),
                'semantic reverse requires exact path/hash descriptors')
    require(all(isinstance(plan[key], str) for key in ('source_payload', 'stage', 'home', 'expected_control')),
            'semantic reverse requires literal path/control selections')
    payload = read_selected_json(plan['payload']['path'], plan['payload']['sha256'])
    payload['path'] = plan['source_payload']
    original = semantic.absolute(plan['original_config']['path'])
    require(semantic.regular(original)['sha256'] == plan['original_config']['sha256'],
            'original selected venv configuration changed')
    require(len(plan['expected_control']) == 64 and
            all(c in '0123456789abcdef' for c in plan['expected_control']), 'explicit control address required')
    return stage_semantic_reverse(payload, original, plan['stage'], plan['home'],
                                  expected_control=plan['expected_control'], publications=publications)


def metadata(path):
    """No content opens, hashes, symlink traversal or credential reads."""
    info = Path(path).lstat()
    return dict(device=info.st_dev, inode=info.st_ino, uid=info.st_uid,
                gid=info.st_gid, mode=stat.S_IMODE(info.st_mode),
                kind=stat.S_IFMT(info.st_mode), links=info.st_nlink,
                bytes=info.st_size, mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)


def layout_plan(home, names, archives=(), *, credential_selectors=()):
    """Read metadata only. Observation is neither drain nor swap admission."""
    home = Path(home)
    require(home.is_absolute() and str(home) == os.path.normpath(str(home)), 'canonical fixture home required')
    directory = home / '.local/share/cairn/coordination/bindings'
    directories = [home, home / '.local', home / '.local/share',
                   home / '.local/share/cairn', directory.parent, directory]
    # Refuse before traversing children of any symlink supplied as a directory.
    for path in [*reversed(home.parents), *directories]:
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode), 'layout directory is a symlink or special member')
    selectors = { (row['device'], row['inode']) for row in map(metadata, credential_selectors) }
    observed = {path.name: metadata(path) for path in sorted(directory.iterdir())}
    expected = {name + '.json' for name in names} | set(archives)
    permitted = expected | {'.watch.lock'}
    unknown = sorted(set(observed) - permitted)
    missing = sorted(expected - set(observed))
    unsafe = []
    for path in directories:
        row = metadata(path)
        if row['uid'] != os.getuid() or row['mode'] & 0o022 or (path in (home, directory) and row['mode'] != 0o700):
            unsafe.append(dict(path=str(path), mode=row['mode'], required='owned 0700' if path in (home, directory) else 'no group/other writes'))
    aliases = sorted(name for name, row in observed.items()
                     if (row['device'], row['inode']) in selectors)
    special = sorted(name for name, row in observed.items() if row['kind'] != stat.S_IFREG)
    lock = observed.get('.watch.lock')
    return dict(schema='cairn.r07-bootstrap-plan/1', home=str(home), directory=str(directory),
                members=observed, unknown_members=unknown, missing_members=missing,
                credential_aliases=aliases, special_members=special, remediation_plan=unsafe,
                lock_initialization='not-observed; separate admitted initialization required' if lock is None else 'existing inode must be retained',
                archive_content='unread; metadata only', original_json_mode=0o600,
                proposed_and_checked_retained_json_mode=0o400,
                operator_bootstrap='unsupported', operator_bootstrap_reverse='unsupported',
                authenticated_d1_d5='unknown', applicable_d6='unknown',
                stopped_joined_restart_fenced_writer_set='unknown',
                exact_s04r_payload_control_home='unknown', source_gaps=GAPS,
                production_admission='unavailable')


def make_layout(root, host, *, observed_modes=False):
    """Allocate only a NEW synthetic home; never accept an installed source."""
    home = Path(root) / host
    home.mkdir(mode=0o700)
    directories = [home / '.local', home / '.local/share', home / '.local/share/cairn',
                   home / '.local/share/cairn/coordination',
                   home / '.local/share/cairn/coordination/bindings']
    for directory in directories:
        directory.mkdir(mode=0o700)
    bindings = directories[-1]
    names = PROXIMAL if host == 'proximal' else ('codex-archon',) if host == 'archon' else ('opencode-one',)
    for name in names:
        control.write(bindings / (name + '.json'), dict(binding=name,
            state_dir=str(home / 'issued-state' / name), config_home=str(home / 'accounts' / name),
            retained_python_binding=str(home / 'retained2/binding.json')))
    if host != 'talos':
        control.write(bindings / '.watch.lock', {})
    if host == 'proximal':
        for name in ARCHIVES:
            # Opaque synthetic stand-in. The planner never opens its contents.
            control.write(bindings / name, dict(opaque='private historical fixture'))
    if observed_modes:
        if host in ('proximal', 'talos'):
            home.chmod(0o750)
            directories[0].chmod(0o775)
            directories[1].chmod(0o775)
        if host == 'talos':
            directories[-2].chmod(0o755)
            bindings.chmod(0o755)
    return home, names, ARCHIVES if host == 'proximal' else ()


def preserved_metadata(path):
    # ctime and parent timestamps are deliberately not promised by rename/link.
    row = metadata(path)
    return {key: row[key] for key in ('device', 'inode', 'uid', 'gid', 'mode', 'kind', 'bytes', 'mtime_ns')}


def raw_filesystem_rehearsal(root):
    """Primitive-only roundtrip on a home created HERE; no drain/admission claim.

    These renames are not exposed as an operator implementation. In particular,
    a joined child does not establish the complete production writer set.
    """
    home, names, archives = make_layout(root, 'proximal')
    active = home / '.local/share/cairn/coordination/bindings'
    before = {p.name: preserved_metadata(p) for p in active.iterdir()}
    original = {name: (active / (name + '.json')).read_bytes() for name in names}
    parked, prepared = active.with_name('raw-original'), active.with_name('prepared')
    prepared.mkdir(mode=0o700)
    owner = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c',
                              'import sys;sys.stdin.buffer.read()'], stdin=subprocess.PIPE)
    try:
        require(owner.poll() is None, 'new fixture owner exited early')
        owner.stdin.close()
        require(owner.wait(timeout=5) == 0, 'owned fixture child was not joined')
        for name in names:
            target = prepared / (name + '.json')
            target.write_bytes(original[name])
            target.chmod(0o400)
        os.link(active / '.watch.lock', prepared / '.watch.lock')
        active.rename(parked)
        prepared.rename(active)
        require((active / '.watch.lock').samefile(parked / '.watch.lock'), 'raw rehearsal split lock inode')
        require(all(stat.S_IMODE((active / (name + '.json')).stat().st_mode) == 0o400 for name in names), 'proposed mode differs')
        require(set(p.name for p in parked.iterdir()) == set(before), 'archive lost original members')
        # Separate stopped reverse primitive, restoring the complete original
        # directory, all 17 opaque archives and all original JSON inodes/modes.
        active.rename(prepared)
        parked.rename(active)
        require({p.name: preserved_metadata(p) for p in active.iterdir()} == before, 'raw original metadata changed')
        require(all((active / (n + '.json')).read_bytes() == original[n] for n in names), 'issued literal references changed')
        require((active / '.watch.lock').samefile(prepared / '.watch.lock'), 'reverse split lock inode')
        return dict(result='filesystem-primitive-only', bindings=len(names), archives=len(archives),
                    original_members=len(before), same_lock_inode=metadata(active / '.watch.lock')['inode'],
                    joined_fixture_child=dict(pid=owner.pid, exit=owner.returncode),
                    original_inodes_modes_owners_and_issued_references='preserved',
                    ctime_and_parent_timestamps='not preserved',
                    authenticated_drain='not exercised by this primitive',
                    complete_writer_fence='not established', operator_bootstrap='unsupported')
    finally:
        stop_process(owner, timeout=5, label='R07 owned primitive child')


def semantic_file_fixture(root):
    """Existing S04R selected-file contracts on inert files; never execute them."""
    root = Path(root)
    root.mkdir(mode=0o700)
    inputs, stage, home = root / 'inputs', root / 'stage', root / 'worker-home'
    inputs.mkdir(mode=0o700)
    stage.mkdir(mode=0o700)
    home.mkdir(mode=0o700)
    roles = {'python': ('bin/python3.11',), 'site': (),
             'workers': ('semantic_rank.py', 'embedding_worker.py'),
             'native': ('ld-linux-x86-64.so.2',), 'locale': (), 'gconv': (),
             'model': semantic.MODEL_FILES}
    for role, members in roles.items():
        directory = inputs / role
        directory.mkdir(mode=0o700)
        for name in members:
            target = directory / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(b'INERT S04R FILE CONTRACT FIXTURE; NEVER EXECUTED\n')
            target.chmod(0o500 if role in ('python', 'native') else 0o400)
    seal = semantic.seal_inputs({role: inputs / role for role in roles}, {'fixture': 'inert files; no model/runtime qualification'})
    payload = semantic.publish_payload(stage, seal)
    config = root / 'original-pyvenv.cfg'
    config.write_text('home = /fixture-original/python/bin\ninclude-system-site-packages = false\n')
    controls = semantic.render_python_controls(stage, payload, config, home)
    semantic.verify_generation(payload['path'], payload['descriptor'])
    semantic.verify_generation(controls['path'], controls['descriptor'])
    semantic.private_home(home, stage)
    return payload, controls, home, stage


def process_ref(process):
    raw = Path(f'/proc/{process.pid}/stat').read_text()
    return dict(pid=process.pid, start=int(raw[raw.rindex(')') + 2:].split()[19]),
                boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def authenticated_cases(cluster, images, root):
    """Actual read endpoints and process-local receipts; no serialized substitute."""
    f = control.Fixture(cluster, images['api'], root, 'r07_bootstrap')
    outcomes = []
    try:
        f.retained(resume=True)
        calls = control.transport(f, images['api'])
        before = f.snapshot()
        control.write(f.root / 'protected-before.json', before)
        (f.active / '.watch.lock').write_bytes(b'')
        (f.active / '.watch.lock').chmod(0o600)
        lock = metadata(f.active / '.watch.lock')

        def invoke(operation, plan, label, *, refusal=None):
            old = {p.name: preserved_metadata(p) for p in f.active.iterdir()}
            result = control.invoke(f, images['control'], operation, plan, label, expected=None,
                                    path_tail=label + '-plan.json')
            if refusal is not None:
                require(refusal in result.get('error', '') and not result['exchange']['Published'], label + ': wrong refusal')
                require({p.name: preserved_metadata(p) for p in f.active.iterdir()} == old, label + ': modified originals')
                require(not Path(plan['retained']).exists(), label + ': created unpublished preparation')
            else:
                require(result['inspected'] and not result.get('error'), label + ': real drain failed')
            outcomes.append(dict(case=label, result='PASS', inspected=result['inspected'], error=result.get('error')))
            return result

        inspected = invoke('inspect', control.plan_for(f), 'authenticated-inspect')
        require(f.snapshot() == before, 'read-only authenticated inspection mutated API/state')
        require(inspected['facts'] and any(k.startswith('store:') for k in inspected['facts']), 'authenticated store facts absent')
        for name in ARCHIVES:
            control.write(f.active / name, {'opaque': 'synthetic archive, never normalized'})
        invoke('switch', control.plan_for(f), 'all17-archives', refusal='watch config-dir membership changed')
        for name in ARCHIVES:
            (f.active / name).unlink()
        control.write(f.active / 'unknown-member', {})
        invoke('switch', control.plan_for(f), 'unknown-member', refusal='watch config-dir membership changed')
        (f.active / 'unknown-member').unlink()
        replay = control.plan_for(f)
        replay['facts'], replay['complete'] = inspected['facts'], True
        invoke('switch', replay, 'serialized-receipt-replay', refusal='unknown or nonliteral JSON field')
        original = (f.active / 'one.json').read_bytes()
        unbound = json.loads(original)
        unbound.pop('inbox_recall_binding')
        (f.active / 'one.json').chmod(0o600)
        control.write(f.active / 'one.json', unbound)
        (f.active / 'one.json').chmod(0o400)
        saved_pins = f.pins
        f.pins = {'one.json': control.sha((f.active / 'one.json').read_bytes())}
        invoke('switch', control.plan_for(f), 'unbound-missing-receipt', refusal='exact pinned current census selection')
        (f.active / 'one.json').chmod(0o600)
        (f.active / 'one.json').write_bytes(original)
        (f.active / 'one.json').chmod(0o400)
        f.pins = saved_pins
        child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', 'import sys;sys.stdin.buffer.read()'], stdin=subprocess.PIPE)
        try:
            ref = process_ref(child)
            invoke('switch', control.plan_for(f, callbacks=[ref]), 'live-owner', refusal='callback process has not been joined')
            child.stdin.close()
            require(child.wait(timeout=5) == 0, 'fixture child join failed')
            switched = invoke('switch', control.plan_for(f, callbacks=[ref]), 'checked-bound2-switch')
            require(switched['exchange']['Published'], 'checked switch did not publish')
            require(metadata(f.active / '.watch.lock')['inode'] == lock['inode'], 'checked switch replaced lock')
            current = dict(directory=str(f.active), pins=switched['exchange']['Active']['Pins'])
            retained = f.root / 'retained'
            old_lock = retained / '.watch.lock'
            old_lock.unlink()
            old_lock.write_bytes(b'')
            old_lock.chmod(0o600)
            reverse = control.plan_for(f, current=current, next_path=retained / 'one.json', retained=f.root / 'reverse-new', callbacks=[ref])
            invoke('reverse', reverse, 'changed-lock', refusal='watch-lock identity differs')
            old_lock.unlink()
            os.link(f.active / '.watch.lock', old_lock)
            reversed_ = invoke('reverse', reverse, 'checked-bound2-reverse')
            require(reversed_['exchange']['Published'] and (f.active / 'one.json').read_bytes() == original, 'checked reverse did not restore bytes')
            require((f.active / '.watch.lock').samefile(old_lock), 'checked reverse split lock')
            after = f.snapshot()
            control.write(f.root / 'protected-after.json', after)
            require(after == before, 'checked control mutated protected API/state')
        finally:
            stop_process(child, timeout=5, label='R07 callback fixture')
        read_calls = [json.loads(line) for line in calls.read_text().splitlines()]
        require(read_calls and all(row['operation'] in control.READS for row in read_calls), 'consumer attempted a nonread operation')
        return dict(cases=outcomes, authenticated_read_calls=len(read_calls),
                    operations=sorted({row['operation'] for row in read_calls}),
                    complete_unit_coverage='not established by bound2 control fixture',
                    original_mixed_layout_bootstrap='unsupported')
    finally:
        f.host.close()


def remove_owned_scratch(root):
    for base, _, _ in os.walk(root, followlinks=False):
        Path(base).chmod(0o700)
    shutil.rmtree(root)


def authenticated_d6_race(cluster, images, root):
    f = control.Fixture(cluster, images['api'], root, 'r07_d6_race')
    try:
        f.retained()
        claim = control.pending_claim(f, images['api'])
        control.transport(f, images['api'], claim=claim)
        before = f.snapshot()
        control.write(f.root / 'protected-before.json', before)
        original = (f.active / 'one.json').read_bytes()
        result = control.invoke(f, images['control'], 'switch', control.plan_for(f),
                                'new-owner-d6', expected=1)
        require(result['inspected'] and 'D6 drain receipt invalidated' in result.get('error', ''),
                'real new owning hold did not invalidate private drain')
        require(not result['exchange']['Published'] and (f.active / 'one.json').read_bytes() == original,
                'D6 refusal modified original selection')
        prepared = Path(result['exchange']['Preparation'])
        require(set(p.name for p in prepared.iterdir()) == {'one.json', '.watch.lock'},
                'D6 refusal lost complete unpublished preparation')
        require((f.active / '.watch.lock').samefile(prepared / '.watch.lock'),
                'D6 refusal split original lock')
        after = f.snapshot()
        control.write(f.root / 'protected-after.json', after)
        require(before == json.loads((f.root / 'actor-before.json').read_text())
                and after == json.loads((f.root / 'actor-after.json').read_text()),
                'read consumer changed state independently of separately recorded fixture actor')
        return dict(case='new-owner-d6', result='PASS', error=result['error'],
                    published=False, complete_unpublished_preparation='retained until fixture cleanup')
    finally:
        f.host.close()


def mixed_bound2_profiles(f):
    """Eight distinct synthetic Codex profiles, not the live host's hook roster."""
    base = json.loads((f.active / 'one.json').read_bytes())
    (f.active / 'one.json').unlink()
    descriptors = []
    f.pins = {}
    for i, name in enumerate(PROXIMAL):
        config = dict(base, binding=f.binding if i == 0 else 'r07-' + name,
                      state_dir=str(f.state_dir if i == 0 else f.root / ('state-' + name)),
                      config_home=str(f.root / ('account-' + name)))
        for directory in (Path(config['state_dir']), Path(config['config_home'])):
            directory.mkdir(mode=0o700, exist_ok=True)
        path = f.coord if i == 0 else f.root / ('coordination-' + name + '.json')
        control.write(path, config)
        descriptors.append(dict(path=str(path), sha256=control.sha(path.read_bytes())))
        active = f.active / (name + '.json')
        control.write(active, config)
        f.pins[active.name] = control.sha(active.read_bytes())
    memory = f.root / 'memory.json'
    control.write(f.root / 'binding.json', dict(schema='cairn.inbox-recall-binding/2', enabled=False,
        memory_config=dict(path=str(memory), sha256=control.sha(memory.read_bytes())),
        coordination_configs=descriptors))
    (f.active / '.watch.lock').write_bytes(b'')
    (f.active / '.watch.lock').chmod(0o600)
    for name in ARCHIVES:
        control.write(f.active / name, {'opaque': 'synthetic metadata-only archive'})


def bootstrap_input(f):
    def member(path):
        row = metadata(path)
        require(row['kind'] == stat.S_IFREG, 'fixture member must be regular')
        return dict(name=path.name, type='regular', **{key: row[key] for key in ('uid', 'gid', 'mode', 'device', 'inode')})
    return dict(schema='cairn.unit-bootstrap/1', root=str(f.root.parent),
        current=dict(directory=str(f.active), pins=dict(f.pins)),
        archives=[member(f.active / name) for name in ARCHIVES],
        watch_lock=member(f.active / '.watch.lock'),
        credential_selectors=[str(f.host.token('worker'))], callbacks=[])


def bootstrap_invoke(f, image, plan, label, *, operation='inspect', expected=0):
    path = f.root / (label + '-plan.json')
    control.write(path, plan)
    completed = control.command([image, 'unit-bootstrap', operation, '--plan', path,
        '--sha256', control.sha(path.read_bytes())], f.root / label, env=f.host.env, expected=expected)
    result = json.loads(completed.stdout)
    require(result['schema'] == 'cairn.unit-bootstrap-result/1' and not result['published']
            and result['owner_fence'] == 'unknown' and result['reverse'] == 'unsupported',
            label + ': bootstrap fabricated a consequential contract')
    if expected == 0:
        require(result['inspected'] and result['rechecked'] and not result.get('error'), label + ': private read-only receipt failed')
    else:
        require(result.get('error') and not result['rechecked'], label + ': missing explicit refusal')
    return result


def fixture_effect_plan(f, images, host, *, unbound=False, production_shape=False):
    if production_shape:
        return production_effect_plan(f, images, host)
    return homogeneous_effect_plan(f, images, host, unbound=unbound)


def homogeneous_effect_plan(f, images, host, *, unbound=False):
    """Prepare actual retained /2 and maintained /3 generations off-path."""
    import stage_lifecycle_compatibility as installer
    spec = importlib.util.spec_from_file_location('r07_retained_bundle', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    source = f.root / 'retained-python'
    bundle.stage_runtime(ROOT / 'integrations/lifecycle', source)
    descriptor = bundle.bundle_descriptor(source)
    names = PROXIMAL if host == 'proximal' else ('codex-archon',) if host == 'archon' else ('opencode-one',)
    base = json.loads(f.coord.read_bytes())
    memory = json.loads((f.root / 'memory.json').read_bytes())
    for path in f.active.iterdir():
        path.unlink()
    profiles, associations = [], {}
    binding = f.root / 'binding.json'
    memory['config_home'] = str(f.root / ('account-' + names[0]))
    if unbound:
        memory.pop('inbox_recall_binding', None)
    control.write(f.root / 'memory.json', memory)
    for i, name in enumerate(names):
        config = dict(base, binding=f.binding if i == 0 else 'r07-' + name,
                      state_dir=str(f.state_dir if i == 0 else f.root / ('state-' + name)),
                      config_home=str(f.root / ('account-' + name)))
        if unbound:
            config.pop('inbox_recall_binding', None)
        for directory in (Path(config['state_dir']), Path(config['config_home'])):
            directory.mkdir(mode=0o700, exist_ok=True)
        path = f.coord if i == 0 else f.root / ('coordination-' + name + '.json')
        control.write(path, config)
        profiles.append((name, path, config))
        control.write(f.active / (name + '.json'), config)
    def pin(path):
        return dict(path=str(path), sha256=control.sha(path.read_bytes()))
    manifest = dict(schema='cairn.inbox-recall-binding/2', enabled=False,
                    memory_config=pin(f.root / 'memory.json'),
                    coordination_configs=[pin(path) for _, path, _ in profiles],
                    engine=dict(**pin(source / 'memory.py'), bundle=descriptor),
                    bridge=dict(**pin(source / 'inbox_recall.py'), bundle=descriptor))
    control.write(binding, manifest)
    for name, path, _ in profiles:
        argv = f.root / ('argv-' + name) / 'retained-argv.json'
        argv.parent.mkdir(mode=0o700)
        control.write(argv, dict(memory=[str(Path(sys.executable).resolve()), '-I', '-B', str(source / 'memory.py'), '--config', str(f.root / 'memory.json')],
                                coordination=[str(Path(sys.executable).resolve()), '-I', '-B', str(source / 'coordination.py'), 'hook', '--config', str(path)]))
        associations[name + '.json'] = dict(binding=dict(Path=str(binding), SHA256=pin(binding)['sha256']),
                                             argv=dict(Path=str(argv), SHA256=pin(argv)['sha256']))
    if host != 'talos':
        control.write(f.active / '.watch.lock', {})
    else:
        f.active.chmod(0o755)
    for name in ARCHIVES if host == 'proximal' else ():
        control.write(f.active / name, {'opaque': 'retained original; never a profile'})

    stage = f.root / 'forward'
    stage.mkdir(mode=0o700)
    identity = str(uuid.uuid4())
    generation = stage / 'generations' / ('selection-' + identity)
    inputs = f.root / 'forward-inputs'
    inputs.mkdir(mode=0o700)
    selected_sources = f.root / 'forward-sources'
    selected_sources.mkdir(mode=0o700)
    for name in (*bundle.MODULE_FILES, 'lifecycle_compatibility.py'):
        # The maintained publisher requires private fixture inputs. The git
        # worktree stays in place; no host HOME/tool environment is replaced.
        target_source = selected_sources / name
        target_source.write_bytes((ROOT / 'integrations/lifecycle' / name).read_bytes())
        target_source.chmod(0o600)
    for role, config in [('memory', memory), *[(name, config) for name, _, config in profiles]]:
        control.write(inputs / (role + '.json'), dict(config) if unbound else dict(config, inbox_recall_binding=str(generation / 'binding.json')))
    installation = dict(id=identity, enabled=False, profile_mode='unbound' if unbound else 'bound',
        sources={name: pin(selected_sources / name) for name in (*bundle.MODULE_FILES, 'lifecycle_compatibility.py')},
        source_revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        binary=pin(images['control']), memory_config=pin(inputs / 'memory.json'),
        coordination_configs=[pin(inputs / (name + '.json')) for name, _, _ in profiles],
        aliases={}, python=str(Path(sys.executable).resolve()), shell='/bin/sh')
    installer.stage(images['stager'], stage, installation)
    prepared = f.active.with_name('prepared-bindings')
    prepared.mkdir(mode=0o700)
    if host == 'talos':
        control.write(prepared / '.watch.lock', {})
    else:
        os.link(f.active / '.watch.lock', prepared / '.watch.lock')
    target_associations = {}
    for i, (name, _, _) in enumerate(profiles):
        target = prepared / (name + '.json')
        target.write_bytes((generation / ('coordination-' + str(i) + '.json')).read_bytes())
        target.chmod(0o400)
        target_associations[target.name] = dict(
            binding=dict(Path=str(generation / 'binding.json'), SHA256=pin(generation / 'binding.json')['sha256']),
            argv=dict(Path='', SHA256=''), receipt=dict(Path=str(generation / 'receipt.json'), SHA256=pin(generation / 'receipt.json')['sha256']))

    def member(path, *, directory=False):
        row = metadata(path)
        return dict(name=path.name, type='directory' if directory else 'regular',
                    **{key: row[key] for key in ('uid', 'gid', 'mode', 'device', 'inode')})
    def plan(directory, archive_names, selected_associations):
        return dict(schema='cairn.unit-bootstrap/2', root=str(f.root),
                    current=dict(directory=str(directory), pins={name + '.json': pin(directory / (name + '.json'))['sha256'] for name in names}),
                    archives=[member(directory / name) for name in archive_names],
                    watch_lock=member(directory / '.watch.lock') if (directory / '.watch.lock').exists() else
                        dict(name='.watch.lock', type='absent', uid=0, gid=0, mode=0, device=0, inode=0),
                    credential_selectors=[str(f.host.token('worker'))], callbacks=[], associations=selected_associations)
    target = plan(prepared, (), target_associations)
    target_path = f.root / 'target-bootstrap-plan.json'
    control.write(target_path, target)
    original = plan(f.active, ARCHIVES if host == 'proximal' else (), associations)
    # The Go unit fixture replaces only this scope selection with its own
    # privately captured systemd/cgroup observation receipt. Production CLI
    # has no such replacement or environment path.
    original['transition'] = dict(target=dict(Path=str(target_path), SHA256=pin(target_path)['sha256']),
        original_identity=member(f.active, directory=True), target_identity=member(prepared, directory=True),
        fence=dict(UID=os.getuid(), Units=[], Executables=[], CommandSHA256=[], Database='', DatabaseSystemID='', Window=30_000_000_000))
    path = f.root / 'effect-bootstrap-plan.json'
    control.write(path, original)
    return path, pin(path)['sha256']



def production_roster(host):
    """Observed member/kind/home/bound grouping, with no default home injected."""
    if host == 'proximal':
        return [('agy-one', 'agy', None, False, 'agy'),
                ('claude-one', 'claude', '.claude', True, 'claude-one'),
                ('claude-two', 'claude', '.claude-harm', True, 'claude-two'),
                ('codex-one', 'codex', '.codex', True, 'codex'),
                ('codex-two', 'codex', '.codex-harm', True, 'codex'),
                ('codex-three', 'codex', '.codex-3', True, 'codex'),
                ('hermes-one', 'hermes', None, False, 'hermes'),
                ('opencode-one', 'opencode', None, False, 'opencode')]
    if host == 'archon':
        return [('codex-archon', 'codex', '.codex', False, 'codex')]
    require(host == 'talos', 'unknown production shape')
    return [('opencode-one', 'opencode', None, False, 'opencode')]


def production_state_paths(f, host):
    coordinator = {name: f.root / 'coordination/state' / name for name, *_ in production_roster(host)}
    memory = {}
    parents = {'codex': 'codex-hooks', 'claude-one': 'claude-hooks',
               'claude-two': 'claude-harm-hooks', 'opencode': 'opencode-hooks'}
    for *_, group in production_roster(host):
        if group == 'hermes':
            memory[group] = f.production_home / '.hermes/cairn/state'
        elif host == 'archon':
            # Actual unbound memory selection remains an operator input. This
            # path is the explicitly synthetic inactive memory fixture only.
            memory[group] = f.root / 'memory'
        else:
            memory[group] = f.root / parents.get(group, 'inactive-memory-' + group) / 'state'
    return coordinator, memory


def prepare_production_state_layout(f, host):
    """Record installed directory shapes, then prepare only disposable state."""
    require(f.root == f.production_home / '.local/share/cairn' and f.production_home != Path.home(),
            'production shape requires a separate owned fixture home')
    prefixes = [f.production_home / suffix for suffix in ('.local', '.local/share', '.local/bin')]
    for path in prefixes:
        mode = 0o775 if host == 'talos' or host == 'proximal' and path.name != 'bin' else 0o755
        path.mkdir(mode=mode, exist_ok=True)
        path.chmod(mode)
    coordination = f.root / 'coordination'
    coordination.mkdir(mode=0o755 if host == 'talos' else 0o700)
    f.active.rmdir()
    f.active = coordination / 'bindings'
    f.active.mkdir(mode=0o755 if host == 'talos' else 0o700)
    coordinator, memory = production_state_paths(f, host)
    parent = coordination / 'state'
    if host != 'talos':
        parent.mkdir(mode=0o775 if host == 'proximal' else 0o755)
        parent.chmod(0o775 if host == 'proximal' else 0o755)
        primary = coordinator['codex-one' if host == 'proximal' else 'codex-archon']
        f.state_dir.rename(primary)
        f.state_dir = primary
        f.state_path = primary / f.state_path.name
        for path in coordinator.values():
            path.mkdir(mode=0o700, exist_ok=True)
    for group, directory in memory.items():
        if group == 'hermes':
            (f.production_home / '.hermes').mkdir(mode=0o700)
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if group == 'hermes':
            directory.parent.chmod(0o755)
        if host == 'talos':
            directory.parent.chmod(0o755)
        else:
            directory.mkdir(mode=0o700, exist_ok=True)
    candidates = [*prefixes, parent, *coordinator.values(), *memory.values(), *(path.parent for path in memory.values())]
    before = {str(path): metadata(path) if path.exists() else dict(absent=True) for path in candidates}
    actions = []
    for path in prefixes:
        if stat.S_IMODE(path.stat().st_mode) != 0o755:
            prior = metadata(path)
            path.chmod(0o755)
            actions.append(dict(path=str(path), before=prior, prepared=metadata(path)))
    for path in [parent, *[directory.parent for directory in memory.values() if directory.is_relative_to(f.root)]]:
        if path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o700:
            continue
        prior = metadata(path) if path.exists() else dict(absent=True)
        if path.exists():
            path.chmod(0o700)
        else:
            path.mkdir(mode=0o700)
        actions.append(dict(path=str(path), before=prior, prepared=metadata(path)))
    result = dict(host=host, before=before, actions=actions,
                  claim='disposable state shapes and reversible preparation; no production observation or mutation',
                  archon_memory='synthetic inactive input; actual selected unbound memory remains PENDING' if host == 'archon' else 'declared fixture role paths',
                  fixture_control_state=str(f.state_dir))
    control.write(f.root / 'production-layout.json', result)
    return result


def reverse_production_state_layout(f):
    restored = []
    for action in reversed(f.production_layout['actions']):
        path, prior = Path(action['path']), action['before']
        if prior.get('absent'):
            path.rmdir()
            require(not path.exists(), 'fixture created state parent was not removed after reverse')
        else:
            path.chmod(prior['mode'])
            now = metadata(path)
            require(all(now[key] == prior[key] for key in ('device', 'inode', 'uid', 'gid', 'mode')),
                    'fixture layout reverse lost original directory identity/mode')
        restored.append(dict(path=str(path), restored=prior))
    result = dict(restored=restored, after_all_bootstrap_readers_joined=True, production_actions=0)
    control.write(f.root / 'production-layout-reverse.json', result)
    return result


def production_effect_plan(f, images, host):
    """Actual raw /2 inputs and Go-published /3 groups, inside an owned fixture.

    Unbound /2 association descriptors are explicit OFF-PATH selection data.
    There is no installed binding selector in any of those original profiles.
    Source closure validation here is not runtime or native admission.
    """
    import stage_lifecycle_compatibility as installer
    spec = importlib.util.spec_from_file_location('r07_production_bundle', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    roster = production_roster(host)
    coordinator_states, memory_states = production_state_paths(f, host)
    home = f.production_home
    require(f.root == home / '.local/share/cairn', 'production fixture must use actual private Cairn path shape')
    base = json.loads(f.coord.read_bytes())
    for path in f.active.iterdir():
        path.unlink()
    sources = f.root / 'retained-deployment'
    sources.mkdir(mode=0o755)
    source = sources / 'lifecycle'
    bundle.stage_runtime(ROOT / 'integrations/lifecycle', source)
    # Match the retained selected hook source's 0700 members and parent.
    for path in source.iterdir():
        if path.is_file():
            path.chmod(0o700)
    descriptor = bundle.bundle_descriptor(source)
    private_client = f.root / 'private-client'
    private_client.mkdir(mode=0o700)
    forward_client = private_client / 'cairn'
    forward_client.write_bytes(images['api'].read_bytes())
    forward_client.chmod(0o500)
    legacy_parent = home / '.local/bin'
    legacy_parent.mkdir(mode=0o755, exist_ok=True)
    legacy_client = legacy_parent / 'cairn'
    legacy_client.write_bytes(images['api'].read_bytes())
    legacy_client.chmod(0o755)
    api_sha = control.sha(images['api'].read_bytes())
    require(all(path.stat().st_size == images['api'].stat().st_size and control.sha(path.read_bytes()) == api_sha
                for path in (legacy_client, forward_client)),
            'legacy/private clients must retain the exact full-sized candidate API image')
    source_inputs = f.root / 'forward-sources'
    source_inputs.mkdir(mode=0o700)
    for name in (*bundle.MODULE_FILES, 'lifecycle_compatibility.py'):
        path = source_inputs / name
        path.write_bytes((ROOT / 'integrations/lifecycle' / name).read_bytes())
        path.chmod(0o600)
    pin = lambda path: dict(path=str(path), sha256=control.sha(path.read_bytes()))
    to_go = lambda path: dict(Path=str(path), SHA256=pin(path)['sha256'])
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    original_associations, target_associations, groups, configs = {}, {}, {}, {}
    prepared = f.active.with_name('prepared-bindings')
    prepared.mkdir(mode=0o700)
    if host != 'talos':
        control.write(f.active / '.watch.lock', {})
        os.link(f.active / '.watch.lock', prepared / '.watch.lock')
    else:
        f.active.chmod(0o755)
        control.write(prepared / '.watch.lock', {})
    for name in ARCHIVES if host == 'proximal' else ():
        control.write(f.active / name, {'opaque': 'original metadata-only archive; never a hook profile'})
    primary = 'codex-one' if host == 'proximal' else 'codex-archon' if host == 'archon' else None
    for name, harness, account, bound, group in roster:
        state = coordinator_states[name]
        if not (host == 'talos' and name == 'opencode-one'):
            state.mkdir(mode=0o700, exist_ok=True)
        profile = dict(base, harness=harness, cairn=str(legacy_client),
                       binding=f.binding if name == primary else 'r07-' + name,
                       state_dir=str(state))
        profile.pop('config_home', None)
        profile.pop('inbox_recall_binding', None)
        if account is not None:
            account_path = home / account
            account_path.mkdir(mode=0o700 if account == '.codex-3' else 0o755)
            profile['config_home'] = str(account_path)
        original_dir = f.root / ('original-' + group)
        original_dir.mkdir(mode=0o700, exist_ok=True)
        binding = original_dir / 'binding.json' if bound else original_dir / 'off-path-association.json'
        if bound:
            profile['inbox_recall_binding'] = str(binding)
        path = original_dir / (name + '.json')
        control.write(path, profile)
        control.write(f.active / (name + '.json'), profile)
        configs[name] = profile
        groups.setdefault(group, dict(harness=harness, bound=bound, binding=binding, directory=original_dir, profiles=[]))['profiles'].append((name, path))
    production_material = dict(schema='cairn.r07-production-shape-material/1', host=host,
                               native_admission=False, runtime_qualification='not claimed',
                               root=str(f.root), home=str(home), groups=[],
                               client_image=dict(path=str(images['api']), sha256=api_sha, bytes=images['api'].stat().st_size),
                               hook_image=dict(path=str(images['control']), sha256=control.sha(images['control'].read_bytes()), bytes=images['control'].stat().st_size))
    for group, selected in groups.items():
        original_dir, bound = selected['directory'], selected['bound']
        old_memory = original_dir / 'memory.json'
        memory_state = memory_states[group]
        if host != 'talos':
            memory_state.mkdir(mode=0o700, exist_ok=True)
        memory = dict(harness=selected['harness'], cairn=str(legacy_client), socket=base['socket'],
                      token_file=base['token_file'], repo=base['repo'], state_dir=str(memory_state),
                      recall_mode='agent_tools', context_bytes=9500)
        if bound:
            memory['inbox_recall_binding'] = str(selected['binding'])
        # Memory home is absent, as on all three installed bound /2 groups.
        control.write(old_memory, memory)
        manifest = dict(schema='cairn.inbox-recall-binding/2', enabled=bound,
                        memory_config=pin(old_memory), coordination_configs=[pin(path) for _, path in selected['profiles']],
                        engine=dict(**pin(source / 'memory.py'), bundle=descriptor),
                        bridge=dict(**pin(source / 'inbox_recall.py'), bundle=descriptor))
        control.write(selected['binding'], manifest)
        if not bound:
            require(not any('inbox_recall_binding' in configs[name] for name, _ in selected['profiles']),
                    'unbound original cannot acquire an installed binding selector')
        for name, path in selected['profiles']:
            argv = original_dir / ('argv-' + name + '.json')
            control.write(argv, dict(memory=[str(Path(sys.executable).resolve()), '-I', '-B', str(source / 'memory.py'), '--config', str(old_memory)],
                                    coordination=[str(Path(sys.executable).resolve()), '-I', '-B', str(source / 'coordination.py'), 'hook', '--config', str(path)]))
            original_associations[name + '.json'] = dict(binding=to_go(selected['binding']), argv=to_go(argv))
        stage = f.root / ('forward-' + group)
        stage.mkdir(mode=0o700)
        identity = str(uuid.uuid4())
        generation = stage / 'generations' / ('selection-' + identity)
        inputs = f.root / ('forward-inputs-' + group)
        inputs.mkdir(mode=0o700)
        for role, config in [('memory', memory), *[(name, configs[name]) for name, _ in selected['profiles']]]:
            forward = dict(config, cairn=str(forward_client))
            if bound:
                forward['inbox_recall_binding'] = str(generation / 'binding.json')
            control.write(inputs / (role + '.json'), forward)
        installation = dict(id=identity, enabled=False, profile_mode='bound' if bound else 'unbound',
            sources={name: pin(source_inputs / name) for name in (*bundle.MODULE_FILES, 'lifecycle_compatibility.py')},
            source_revision=revision, binary=pin(images['control']), memory_config=pin(inputs / 'memory.json'),
            coordination_configs=[pin(inputs / (name + '.json')) for name, _ in selected['profiles']],
            aliases={}, python=str(Path(sys.executable).resolve()), shell='/bin/sh')
        installer.stage(images['stager'], stage, installation)
        for i, (name, _) in enumerate(selected['profiles']):
            target = prepared / (name + '.json')
            target.write_bytes((generation / ('coordination-' + str(i) + '.json')).read_bytes())
            target.chmod(0o400)
            target_associations[target.name] = dict(binding=to_go(generation / 'binding.json'),
                argv=dict(Path='', SHA256=''), receipt=to_go(generation / 'receipt.json'))
        production_material['groups'].append(dict(name=group, kind=selected['harness'], bound=bound,
            original_binding=to_go(selected['binding']), original_binding_installed=bound, memory_state=str(memory_state),
            target_binding=to_go(generation / 'binding.json'), target_receipt=to_go(generation / 'receipt.json'),
            members=[name + '.json' for name, _ in selected['profiles']]))
    def member(path, directory=False):
        row = metadata(path)
        return dict(name=path.name, type='directory' if directory else 'regular',
                    **{key: row[key] for key in ('uid', 'gid', 'mode', 'device', 'inode')})
    def plan(directory, archives, associations):
        return dict(schema='cairn.unit-bootstrap/2', root=str(f.root),
            current=dict(directory=str(directory), pins={name + '.json': pin(directory / (name + '.json'))['sha256'] for name, *_ in roster}),
            archives=[member(directory / name) for name in archives],
            watch_lock=member(directory / '.watch.lock') if (directory / '.watch.lock').exists() else
                dict(name='.watch.lock', type='absent', uid=0, gid=0, mode=0, device=0, inode=0),
            credential_selectors=[str(f.host.token('worker'))], callbacks=[], associations=associations,
            **(dict(external_states=[dict(member='hermes-one.json', role='memory',
                directory=str(memory_states['hermes']), identity=member(memory_states['hermes'], True))])
                if host == 'proximal' else {}))
    target = plan(prepared, (), target_associations)
    target_path = f.root / 'target-bootstrap-plan.json'
    control.write(target_path, target)
    original = plan(f.active, ARCHIVES if host == 'proximal' else (), original_associations)
    original['transition'] = dict(target=to_go(target_path), original_identity=member(f.active, True),
        target_identity=member(prepared, True),
        fence=dict(UID=os.getuid(), Units=[], Executables=[], CommandSHA256=[], Database='', DatabaseSystemID='', Window=30_000_000_000))
    path = f.root / 'effect-bootstrap-plan.json'
    control.write(path, original)
    production_material.update(original_current=original['current'], target_current=target['current'],
        target_plan=to_go(target_path), original_plan=to_go(path),
        layout=f.production_layout,
        target_unbound_receipts=[group['target_receipt'] for group in production_material['groups'] if not group['bound']],
        account_identities={str(home / account): metadata(home / account) for _, _, account, *_ in roster if account},
        state_paths={name: configs[name]['state_dir'] for name, *_ in roster})
    control.write(f.root / 'production-material.json', production_material)
    return path, pin(path)['sha256']


@contextmanager
def production_shape_fixture(cluster, images, root, host):
    """Caller owns cluster/images/root; this context owns and joins one API.

    Yields (control.Fixture, original_plan_path, original_plan_sha). Exact /3
    current/pins and genuine unbound receipt references are in production-material.json;
    they are selection data for a caller's fresh census, not native session proof.
    """
    root.mkdir(mode=0o700)
    home = root / 'account-home'
    home.mkdir(mode=0o700 if host == 'archon' else 0o750)
    (home / '.local').mkdir(mode=0o755)
    (home / '.local/share').mkdir(mode=0o755)
    f = control.Fixture(cluster, images['api'], home / '.local/share/cairn', 'r07_production_' + host + '_' + uuid.uuid4().hex[:8])
    try:
        f.production_home = home
        f.production_layout = prepare_production_state_layout(f, host)
        f.retained(resume=True)
        control.transport(f, images['api'])
        path, pin = fixture_effect_plan(f, images, host, production_shape=True)
        yield f, path, pin
    finally:
        process = f.host.process
        code = f.host.close()
        require(process is not None and process.poll() is not None, 'owned production API exit unresolved')
        control.write(root / 'fixture-api-cleanup.json', dict(api_pid=process.pid, api_exit=code,
            api_closed=True, cluster='caller-owned', root='caller-owned', home_environment='unchanged'))


def owned_effect_consumer(f, images, plan, pin, label, *, kill_phase=None, refusal=None):
    """Package-library proof only; the real public host-fence gate is separate."""
    result_path = f.root / (label + '-result.json')
    spec = f.root / (label + '-test.json')
    selected = dict(Plan=str(plan), Pin=pin, Result=str(result_path))
    if refusal:
        selected['RefuseContains'] = refusal
    marker = f.root / (label + '-kill-marker')
    if kill_phase:
        selected.update(HoldPhase=kill_phase, Marker=str(marker))
    control.write(spec, selected)
    temporary = f.root / 'test-temporary'
    temporary.mkdir(mode=0o700, exist_ok=True)
    env = dict(f.host.env, CAIRN_BOOTSTRAP_EFFECT_PLAN=str(spec), TMPDIR=str(temporary))
    argv = [images['tests'], '-test.run=^TestUnitBootstrapOwnedTransition$', '-test.v', '-test.timeout=90s']
    if not kill_phase and not refusal:
        transitions, seconds, launch_seconds, operations = {}, {}, {}, []
        for operation in ('switch', 'reverse'):
            child_result = f.root / (label + '-' + operation + '-result.json')
            selected.update(Operation=operation, Pin=control.sha(plan.read_bytes()), Result=str(child_result))
            control.write(spec, selected)
            started = time.monotonic()
            completed = control.command(argv, f.root / (label + '-' + operation), env=env, timeout=100)
            elapsed = time.monotonic() - started
            require('=== RUN   TestUnitBootstrapOwnedTransition' in completed.stdout and
                    '--- PASS: TestUnitBootstrapOwnedTransition' in completed.stdout and
                    '--- SKIP:' not in completed.stdout and 'no tests to run' not in completed.stdout,
                    'owned effect gate did not execute the selected non-skipped test')
            require(child_result.is_file(), 'shared CLI transition receipt missing')
            observed = json.loads(child_result.read_bytes())
            require(observed.get('result') == 'PASS' and observed.get('workflow_proof') == 'SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE'
                    and observed.get('operation') == operation and observed.get('deadline_seconds') == 30
                    and observed['transition']['published'] and observed['transition']['rechecked'], 'shared CLI args/output/status proof missing')
            require(0 < elapsed < 30 and 0 < observed['seconds'][operation] < 30, 'shared CLI30s launch-to-joined-exit margin missing')
            transitions['forward' if operation == 'switch' else 'reverse'] = observed['transition']
            seconds[operation] = observed['seconds'][operation]
            launch_seconds[operation] = elapsed
            operations.extend(observed['api_read_operations'])
        result = dict(result='PASS', **transitions, seconds=seconds, deadline_seconds=30,
            api_read_operations=operations, workflow_proof='SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE',
            workflow_launch_to_join_seconds=launch_seconds, workflow_margin_seconds={name:30-value for name,value in launch_seconds.items()},
            library_margin_seconds={name:30-value for name,value in seconds.items()},
            outer_timeout_seconds=100, default_public_positive='UNESTABLISHED until admitted R07 stopped boundary')
        control.write(result_path, result)
        return result
    if kill_phase:
        with (f.root / (label + '-killed.stdout')).open('w') as out, (f.root / (label + '-killed.stderr')).open('w') as err:
            child = subprocess.Popen(list(map(str, argv)), env=env, stdout=out, stderr=err)
            try:
                deadline = time.monotonic() + 30
                while not marker.exists():
                    require(child.poll() is None and time.monotonic() < deadline, 'owned consumer did not reach interruption boundary')
                    time.sleep(0.01)
                child.kill()
                require(child.wait(timeout=5) == -9, 'owned killed consumer exit unresolved')
                control.write(f.root / (label + '-killed-child.json'), dict(pid=child.pid, exit=child.returncode, phase=kill_phase))
            finally:
                stop_process(child, timeout=5, label='owned bootstrap interrupted consumer')
        selected = dict(Plan=str(plan), Pin=control.sha(plan.read_bytes()), Result=str(result_path),
                        Recover=True, ExpectPublished=kill_phase in ('after:exchange', 'before:parent-sync', 'after:parent-sync'))
        control.write(spec, selected)
    started = time.monotonic()
    completed = control.command(argv, f.root / label, env=env, timeout=100)
    elapsed = time.monotonic() - started
    require('=== RUN   TestUnitBootstrapOwnedTransition' in completed.stdout and
            '--- PASS: TestUnitBootstrapOwnedTransition' in completed.stdout and
            '--- SKIP:' not in completed.stdout and 'no tests to run' not in completed.stdout,
            'owned effect gate did not execute the selected non-skipped test')
    require(result_path.is_file(), 'owned transition receipt missing')
    result = json.loads(result_path.read_bytes())
    require(result.get('result') == 'PASS', 'owned transition result missing PASS')
    require(result.get('deadline_seconds') == 30 and result.get('seconds') and
            all(0 < seconds < 30 for seconds in result['seconds'].values()), 'per-operation library30s receipts missing')
    result.update(library_margin_seconds={name: 30 - seconds for name, seconds in result['seconds'].items()},
                  outer_launch_to_join_seconds=elapsed, outer_timeout_seconds=100,
                  workflow_proof=result.get('workflow_proof', 'DIRECT_LIBRARY_WITH_FIXTURE_FENCE'),
                  default_public_positive='UNESTABLISHED until admitted R07 stopped boundary')
    control.write(result_path, result)
    return result



def production_shape_refusals(f, images, plan, pin, material):
    """Exercise the actual8-member producer material before any publication."""
    outcomes = []
    selected = json.loads(plan.read_bytes())
    target_path = Path(selected['transition']['target']['Path'])
    target_raw = target_path.read_bytes()
    target_base = json.loads(target_raw)
    def persist_target(target):
        control.write(target_path, target)
        selected['transition']['target']['SHA256'] = control.sha(target_path.read_bytes())
        control.write(plan, selected)
        return control.sha(plan.read_bytes())
    before = f.snapshot()
    for label, name, field, error in (
            ('bound-field-removed', 'codex-one.json', None, 'does not select exact active config'),
            ('unbound-field-present', 'agy-one.json', 'present', 'does not select exact active config')):
        target = json.loads(target_raw)
        current = Path(target['current']['directory']) / name
        raw = current.read_bytes()
        try:
            config = json.loads(raw)
            config.pop('inbox_recall_binding', None)
            if field:
                config['inbox_recall_binding'] = target['associations'][name]['binding']['Path']
            current.chmod(0o600)
            control.write(current, config)
            current.chmod(0o400)
            target['current']['pins'][name] = control.sha(current.read_bytes())
            result = owned_effect_consumer(f, images, plan, persist_target(target), label, refusal=error)
            outcomes.append(dict(case=label, error=result['error']))
        finally:
            current.chmod(0o600)
            current.write_bytes(raw)
            current.chmod(0o400)
            target_path.write_bytes(target_raw)
            selected['transition']['target']['SHA256'] = control.sha(target_raw)
            control.write(plan, selected)
    for fault in ('receipt-hash', 'receipt-schema', 'receipt-binding'):
        target = json.loads(target_raw)
        association = target['associations']['agy-one.json']
        receipt_path = Path(association['receipt']['Path'])
        raw = receipt_path.read_bytes()
        try:
            if fault == 'receipt-hash':
                association['receipt']['SHA256'] = '0'*64
                expected = 'association pin changed'
            else:
                receipt = json.loads(raw)
                if fault == 'receipt-schema':
                    receipt['schema'] = 'cairn.lifecycle-compatibility/0'
                    expected = 'receipt'
                else:
                    receipt['binding']['path'] = str(f.root / 'foreign-binding.json')
                    expected = 'receipt selects another binding'
                receipt_path.chmod(0o600)
                control.write(receipt_path, receipt)
                receipt_path.chmod(0o400)
                association['receipt']['SHA256'] = control.sha(receipt_path.read_bytes())
            result = owned_effect_consumer(f, images, plan, persist_target(target), fault, refusal=expected)
            outcomes.append(dict(case=fault, error=result['error']))
        finally:
            receipt_path.chmod(0o600)
            receipt_path.write_bytes(raw)
            receipt_path.chmod(0o400)
            target_path.write_bytes(target_raw)
            selected['transition']['target']['SHA256'] = control.sha(target_raw)
            control.write(plan, selected)
    for label, directory, error in (
            ('legacy-path-parent775', f.production_home / '.local/bin', 'unsafe ancestor'),
            ('retained-source-parent775', f.root / 'retained-deployment', 'unsafe path')):
        directory.chmod(0o775)
        try:
            result = owned_effect_consumer(f, images, plan, control.sha(plan.read_bytes()), label, refusal=error)
            outcomes.append(dict(case=label, error=result['error']))
        finally:
            directory.chmod(0o755)
    # The unsafe .local/share ancestor rejects even plan reading through the
    # real public read-only path. Restore only this newly owned fixture parent.
    share = f.production_home / '.local/share'
    share.chmod(0o775)
    try:
        completed = control.command([images['control'], 'unit-bootstrap', 'inspect', '--plan', plan,
            '--sha256', control.sha(plan.read_bytes())], f.root / 'share-ancestor775', env=f.host.env, expected=1)
        result = json.loads(completed.stdout)
        require(not result['published'] and 'ancestor' in result['error'], 'unsafe real share ancestry was not refused')
        outcomes.append(dict(case='share-ancestor775', error=result['error']))
    finally:
        share.chmod(0o755)
    require(before == f.snapshot(), 'production refusals changed protected API/state')
    require(not list(f.root.glob('.unit-bootstrap-legacy-*')), 'refused preflight created recovery/publication state')
    control.write(f.root / 'production-refusals.json', outcomes)
    return outcomes


def default_public_refusal(f, images, plan):
    """Real default producer, owned plans; never stop/mask a shared worker."""
    worker = 'cairn-wake-worker-01.service'
    argv = ['/usr/bin/systemctl', '--user', '--no-pager', 'show',
            '--property=Id,LoadState,ActiveState,SubState,UnitFileState', worker]
    status = control.command(argv, f.root / 'default-worker-before', env=f.host.env, timeout=20)
    properties = dict(line.split('=', 1) for line in status.stdout.splitlines() if '=' in line)
    require(properties.get('Id') == worker and properties.get('ActiveState') == 'active' and
            properties.get('SubState') == 'running', 'active-worker prerequisite absent; no default public attempt')
    original = json.loads(plan.read_bytes())
    target = json.loads(Path(original['transition']['target']['Path']).read_bytes())
    # A real live owned parent is a later refusal guard if the shared manager
    # changes unexpectedly. It cannot authorize an exchange.
    callbacks = [process_ref(SimpleNamespace(pid=os.getpid()))]
    original['callbacks'] = target['callbacks'] = callbacks
    target_path = f.root / 'default-refusal-target.json'
    control.write(target_path, target)
    original['transition']['target'] = dict(Path=str(target_path), SHA256=control.sha(target_path.read_bytes()))
    original['transition']['fence'] = dict(UID=os.getuid(), Units=[worker],
        Executables=[dict(Path=str(images['control']), SHA256=control.sha(images['control'].read_bytes()))],
        CommandSHA256=[], Database='', DatabaseSystemID='', Window=30_000_000_000)
    selected = f.root / 'default-refusal-plan.json'
    control.write(selected, original)
    directories = (Path(original['current']['directory']), Path(target['current']['directory']))
    protected = {str(path): preserved_metadata(path) for directory in directories for path in directory.iterdir()}
    before = f.snapshot()
    started = time.monotonic()
    completed = control.command([images['control'], 'unit-bootstrap', 'switch', '--plan', selected,
        '--sha256', control.sha(selected.read_bytes())], f.root / 'default-public-refusal', env=f.host.env, expected=1, timeout=30)
    elapsed = time.monotonic() - started
    result = json.loads(completed.stdout)
    require(not result['published'] and not result['inspected'] and result['owner_fence'] == 'unknown'
            and 'unit or activation trigger is not stopped and masked' in result.get('error', ''),
            'real default producer did not refuse at active-worker fence; no positive is claimed')
    after_status = control.command(argv, f.root / 'default-worker-after', env=f.host.env, timeout=20)
    require(after_status.stdout == status.stdout, 'shared worker selected status changed during refusal')
    require(before == f.snapshot() and protected == {str(path): preserved_metadata(path) for directory in directories for path in directory.iterdir()},
            'default refusal changed protected API/state/selection')
    receipt = dict(proof='DEFAULT_PUBLIC_REFUSAL_ACTIVE_WORKERS', result=result,
                   launch_to_join_seconds=elapsed, shared_worker_properties=properties,
                   unit_actions=0, outside_owned_root_writes=0, default_positive='UNESTABLISHED until admitted R07 stopped boundary')
    control.write(f.root / 'default-public-refusal.json', receipt)
    return receipt

def production_shape_cases(cluster, images, root, host, *, kill_phase=None):
    with production_shape_fixture(cluster, images, root, host) as (f, plan, pin):
        material = json.loads((f.root / 'production-material.json').read_bytes())
        expected_names = PROXIMAL if host == 'proximal' else ('codex-archon',) if host == 'archon' else ('opencode-one',)
        require(set(material['original_current']['pins']) == {name + '.json' for name in expected_names}, 'production selected membership changed')
        target_profiles = {name: json.loads((Path(material['target_current']['directory']) / name).read_bytes()) for name in material['target_current']['pins']}
        require({profile['harness'] for profile in target_profiles.values()} == ({'agy', 'claude', 'codex', 'hermes', 'opencode'} if host == 'proximal' else {'codex'} if host == 'archon' else {'opencode'}), 'real emitted target kinds differ')
        require(sum(not profile.get('inbox_recall_binding') for profile in target_profiles.values()) == (3 if host == 'proximal' else 1), 'real emitted profiles lost null binding selectors')
        require(len(material['target_unbound_receipts']) == (3 if host == 'proximal' else 1), 'production genuine unbound coverage changed')
        def affected_state():
            result = {}
            for directory in sorted(set(material['state_paths'].values()) | {group['memory_state'] for group in material['groups']}):
                path = Path(directory)
                if not path.exists():
                    result[directory] = 'absent'
                    continue
                selected = {}
                for child in sorted(path.rglob('*')):
                    row = preserved_metadata(child)
                    if child.is_file() and not child.name.endswith(('.lock', '.cuelock')):
                        row['sha256'] = control.sha(child.read_bytes())
                    selected[str(child.relative_to(path))] = row
                result[directory] = selected
            return result
        before = f.snapshot()
        state_before = affected_state()
        original_metadata = {path.name: preserved_metadata(path) for path in f.active.iterdir()}
        raw_originals = {group['original_binding']['Path']: (Path(group['original_binding']['Path']).read_bytes(),
            preserved_metadata(Path(group['original_binding']['Path']))) for group in material['groups']}
        accounts = {path: metadata(Path(path)) for path in material['account_identities']}
        home_mode = stat.S_IMODE(f.production_home.stat().st_mode)
        refusals = production_shape_refusals(f, images, plan, pin, material) if host == 'proximal' and kill_phase is None else []
        default_refusal = default_public_refusal(f, images, plan) if host == 'proximal' and kill_phase is None else None
        pin = control.sha(plan.read_bytes())
        public_started = time.monotonic()
        target_pin = material['target_plan']
        public = control.command([images['control'], 'unit-bootstrap', 'inspect', '--plan', target_pin['Path'],
                                  '--sha256', target_pin['SHA256']], f.root / 'public-inspect', env=f.host.env, timeout=30)
        public_seconds = time.monotonic() - public_started
        public_result = json.loads(public.stdout)
        require(public_seconds < 30 and public_result['inspected'] and public_result['rechecked'] and
                not public_result['published'], 'public inspect30s failed')
        result = owned_effect_consumer(f, images, plan, pin, 'production-effect', kill_phase=kill_phase)
        result['default_public_refusal'] = default_refusal
        result['public_inspect'] = dict(launch_to_join_seconds=public_seconds, margin_seconds=30-public_seconds,
                                       qualification='default public read-only inspect; default effectful positive UNESTABLISHED until R07')
        require(before == f.snapshot() and state_before == affected_state(), 'production-shape consumer changed protected API/all affected state')
        require(original_metadata == {path.name: preserved_metadata(path) for path in f.active.iterdir()}, 'production-shape raw reverse lost original directory members')
        for path, (raw, selected) in raw_originals.items():
            require(Path(path).read_bytes() == raw and preserved_metadata(Path(path)) == selected,
                    'production-shape reverse lost raw original manifest bytes/inode/metadata')
        require(accounts == {path: metadata(Path(path)) for path in accounts}, 'account identities changed')
        require(stat.S_IMODE(f.production_home.stat().st_mode) == home_mode, 'HOME mode changed')
        operations = result['api_read_operations']
        require(operations and all(operation in control.READS for operation in operations), 'production-shape consumer made a non-read API operation')
        result.update(host_shape=host, members=len(material['original_current']['pins']),
                      unbound=len(material['target_unbound_receipts']), kinds=sorted({group['kind'] for group in material['groups']}),
                      raw_original_manifest_count=len(raw_originals), raw_reverse='actual exact bytes/inodes/modes/mtime restored',
                      interruption=kill_phase, refusals=refusals, authenticated_read_calls=len(operations),
                      client_image_bytes=material['client_image']['bytes'], hook_image_bytes=material['hook_image']['bytes'],
                      systemd='privately captured observation fixture; no installed service qualification')
        control.write(root / 'production-result.json', result)
        result['layout_reverse'] = reverse_production_state_layout(f)
        control.write(root / 'production-result.json', result)
        # Retain selected receipts only; no credentials or raw original profiles.
        for name in ('production-material.json', 'effect-bootstrap-plan.json', 'target-bootstrap-plan.json',
                     'production-effect.argv.json', 'production-effect.stdout', 'production-effect.stderr',
                     'production-effect.exit.json', 'production-effect-result.json', 'production-refusals.json', 'read-counts.json',
                     'production-layout.json', 'production-layout-reverse.json'):
            path = f.root / name
            if path.exists():
                shutil.copyfile(path, root / name)
        for path in f.root.glob('production-effect-*'):
            if path.suffix in ('.stdout', '.stderr') or path.name.endswith(('.argv.json', '.exit.json', '-result.json')):
                shutil.copyfile(path, root / path.name)
        for path in f.root.glob('default-*'):
            if path.suffix in ('.stdout', '.stderr', '.json'):
                shutil.copyfile(path, root / path.name)
        return result

def effectful_cases(cluster, images, root, host, *, unbound=False, race=False, kill_phase=None):
    f = control.Fixture(cluster, images['api'], root, 'r07_effect_' + host + ('_unbound' if unbound else '') + ('_race' if race else '') + ('_kill_' + kill_phase.split(':')[0] if kill_phase else ''))
    try:
        f.retained(resume=True)
        actor = control.pending_claim(f, images['api']) if race else None
        calls = control.transport(f, images['api'])
        plan, pin = fixture_effect_plan(f, images, host, unbound=unbound)
        before = f.snapshot()
        original_metadata = {p.name: preserved_metadata(p) for p in f.active.iterdir()}
        control.write(f.root / 'protected-before.json', before)
        result_path = f.root / 'effect-result.json'
        spec = f.root / 'effect-test.json'
        selected = dict(Plan=str(plan), Pin=pin, Result=str(result_path))
        if race:
            selected.update(ActorArgv=actor['argv'], ActorBody=actor['body'], Refuse=True)
            control.write(f.root / 'actor-invocation.json', dict(argv=actor['argv'], body=actor['body']))
        marker = f.root / 'kill-phase-marker'
        if kill_phase:
            selected.update(HoldPhase=kill_phase, Marker=str(marker))
        control.write(spec, selected)
        temporary = f.root / 'test-temporary'
        temporary.mkdir(mode=0o700)
        env = dict(f.host.env, CAIRN_BOOTSTRAP_EFFECT_PLAN=str(spec), TMPDIR=str(temporary))
        if kill_phase:
            with (f.root / 'killed.stdout').open('w') as out, (f.root / 'killed.stderr').open('w') as err:
                child = subprocess.Popen([str(images['tests']), '-test.run=^TestUnitBootstrapOwnedTransition$', '-test.v', '-test.timeout=90s'], env=env, stdout=out, stderr=err)
                try:
                    deadline = time.monotonic() + 30
                    while not marker.exists():
                        require(child.poll() is None and time.monotonic() < deadline,
                                'owned child did not reach selected interruption boundary')
                        time.sleep(0.01)
                    child.kill()
                    require(child.wait(timeout=5) == -9, 'owned killed consumer was not joined')
                    control.write(f.root / 'killed-child.json', dict(pid=child.pid, exit=child.returncode, phase=kill_phase))
                finally:
                    stop_process(child, timeout=5, label='owned bootstrap interrupted consumer')
            selected = dict(Plan=str(plan), Pin=control.sha(plan.read_bytes()), Result=str(result_path),
                            Recover=True, ExpectPublished=kill_phase == 'after:exchange')
            control.write(spec, selected)
        control.command([images['tests'], '-test.run=^TestUnitBootstrapOwnedTransition$', '-test.v', '-test.timeout=90s'],
                        f.root / 'effect-run', env=env, timeout=100)
        result = json.loads(result_path.read_bytes())
        require({p.name: preserved_metadata(p) for p in f.active.iterdir()} == original_metadata,
                'source transition/recovery changed original config/opaque metadata')
        after = f.snapshot()
        control.write(f.root / 'protected-after.json', after)
        if race:
            require(not result['published'] and before != after and (f.root / 'actor-result.json').is_file(),
                    'actual separate owning actor/refusal was not observed')
        else:
            require(before == after, 'effectful filesystem consumer mutated protected API/state')
        rows = [json.loads(line) for line in calls.read_text().splitlines()]
        require(rows and all(row['operation'] in control.READS for row in rows), 'bootstrap attempted a non-read API operation')
        return dict(host_shape=host, retained2='unbound' if unbound else 'bound', race=race, interruption=kill_phase, result=result,
                    authenticated_read_calls=len(rows), operations=sorted({row['operation'] for row in rows}),
                    systemd='private observation fixture only; no installed service qualification')
    finally:
        f.host.close()


def bootstrap_cases(cluster, images, root, *, race=False):
    f = control.Fixture(cluster, images['api'], root, 'r07_bootstrap_race' if race else 'r07_bootstrap_read')
    child = None
    try:
        f.retained(resume=True)
        claim = control.pending_claim(f, images['api']) if race else None
        calls = control.transport(f, images['api'], claim=claim)
        mixed_bound2_profiles(f)
        plan = bootstrap_input(f)
        before = f.snapshot()
        control.write(f.root / 'protected-before.json', before)
        before_members = {p.name: preserved_metadata(p) for p in f.active.iterdir()}
        outcomes = []
        result = bootstrap_invoke(f, images['control'], plan, 'bootstrap-eight17-race' if race else 'bootstrap-eight17', expected=1 if race else 0)
        if race:
            require(result['inspected'] and 'D6 bootstrap drain invalidated' in result['error'], 'new owning hold escaped bootstrap final reread')
            require(before == json.loads((f.root / 'actor-before.json').read_text()), 'bootstrap capture mutated protected state before actor')
            require(f.snapshot() == json.loads((f.root / 'actor-after.json').read_text()), 'bootstrap reread mutated state after actor')
        else:
            require(len(result['associations']) == 8 and set(result['associations'].values()) == {'cairn.inbox-recall-binding/2:disabled-bound'}, 'eight genuine bound2 associations were not inspected')
            require(not any(key.startswith('file:') and ('.before-' in key or key == 'file:' + str(f.host.token('worker'))) for key in result['facts']), 'bootstrap read opaque archive or credential contents')
            require(f.snapshot() == before, 'bootstrap inspection mutated protected state')
        require({p.name: preserved_metadata(p) for p in f.active.iterdir()} == before_members, 'bootstrap reader changed original member metadata')
        outcomes.append(dict(case='eight17-real-auth-race' if race else 'eight17-real-auth', result='PASS', error=result.get('error')))
        if not race:
            for operation in ('switch', 'reverse'):
                refused = bootstrap_invoke(f, images['control'], plan, 'bootstrap-' + operation, operation=operation, expected=1)
                require('/2 transition selection' in refused['error'] and not refused['inspected'], 'read-only plan became effectful')
                outcomes.append(dict(case=operation + '-closed', result='PASS'))
            replay = dict(plan, facts=result['facts'], stopped=True)
            refused = bootstrap_invoke(f, images['control'], replay, 'bootstrap-replay', expected=1)
            require('serialized receipts' in refused['error'], 'replayed facts became private receipt')
            outcomes.append(dict(case='bootstrap-replay', result='PASS'))
            control.write(f.active / 'unknown', {})
            refused = bootstrap_invoke(f, images['control'], plan, 'bootstrap-unknown', expected=1)
            require('membership changed' in refused['error'], 'unknown member accepted')
            (f.active / 'unknown').unlink()
            outcomes.append(dict(case='bootstrap-unknown', result='PASS'))
            original_lock = f.active / '.watch.lock'
            parked_lock = f.root / 'original-lock'
            original_lock.rename(parked_lock)
            original_lock.write_bytes(b'')
            original_lock.chmod(0o600)
            refused = bootstrap_invoke(f, images['control'], plan, 'bootstrap-lock-change', expected=1)
            require('metadata changed' in refused['error'], 'different lock inode accepted')
            original_lock.unlink()
            parked_lock.rename(original_lock)
            outcomes.append(dict(case='bootstrap-lock-change', result='PASS'))
            selected = f.active / 'hermes-one.json'
            original = selected.read_bytes()
            unbound = json.loads(original)
            unbound.pop('inbox_recall_binding')
            control.write(selected, unbound)
            unbound_plan = dict(plan, current=dict(plan['current'], pins=dict(plan['current']['pins'])))
            unbound_plan['current']['pins'][selected.name] = control.sha(selected.read_bytes())
            refused = bootstrap_invoke(f, images['control'], unbound_plan, 'bootstrap-unbound2', expected=1)
            require('unbound retained /2 association unsupported' in refused['error'], 'unbound2 normalized to Go3')
            selected.write_bytes(original)
            outcomes.append(dict(case='bootstrap-unbound2', result='PASS'))
            archive = f.active / ARCHIVES[0]
            parked_archive = f.root / 'opaque-original'
            archive.rename(parked_archive)
            os.link(f.host.token('worker'), archive)
            refused = bootstrap_invoke(f, images['control'], plan, 'bootstrap-credential-alias', expected=1)
            require('alias' in refused['error'], 'credential inode alias accepted')
            archive.unlink()
            parked_archive.rename(archive)
            outcomes.append(dict(case='bootstrap-credential-alias', result='PASS'))
            child = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-c', 'import sys;sys.stdin.buffer.read()'], stdin=subprocess.PIPE)
            live_plan = dict(plan, callbacks=[process_ref(child)])
            refused = bootstrap_invoke(f, images['control'], live_plan, 'bootstrap-live-callback', expected=1)
            require('callback process has not been joined' in refused['error'], 'live callback accepted')
            child.stdin.close()
            require(child.wait(timeout=5) == 0, 'bootstrap fixture callback join failed')
            bootstrap_invoke(f, images['control'], live_plan, 'bootstrap-joined-callback')
            outcomes.extend([dict(case='bootstrap-live-callback', result='PASS'), dict(case='bootstrap-joined-callback', result='PASS')])
            # The separate checked Go3 command must retain its old refusal.
            checked = dict(schema='cairn.unit-control/1', current=plan['current'],
                next=[dict(name=name, path=str(f.active / name), sha256=pin) for name, pin in f.pins.items()],
                retained=str(f.root / 'not-created'), callbacks=[])
            refused = control.invoke(f, images['control'], 'inspect', checked, 'go3-still-refuses', expected=1)
            require(refused['error'] == 'watch config-dir membership changed', 'new bootstrap changed Go3 guard')
            outcomes.append(dict(case='go3-still-refuses', result='PASS'))
        after = f.snapshot()
        control.write(f.root / 'protected-after.json', after)
        if not race:
            require(after == before, 'bootstrap refusals changed protected store/state')
        rows = [json.loads(line) for line in calls.read_text().splitlines()]
        require(rows and all(row['operation'] in control.READS for row in rows), 'bootstrap consumer attempted non-read API operation')
        return dict(cases=outcomes, authenticated_read_calls=len(rows), members=8, archives=17,
                    association_scope='synthetic eight Codex bound2 profiles; not installed native roster',
                    complete_writer_fence='unknown', operator_bootstrap='unsupported')
    finally:
        if child is not None:
            stop_process(child, timeout=5, label='bootstrap own callback')
        f.host.close()


def source_pins():
    files = {ref.split(':')[0] for ref in GAPS.values()}
    files.update(('go.mod', 'go.sum', 'scripts/disposable_host.py',
                  'scripts/check_r06_unit_control.py', 'scripts/check_r06_status_consumer.py',
                  'scripts/check_r06_lifecycle_offline_restore.py',
                  'scripts/check_r07_bootstrap_reverse.py', 'scripts/test_r07_bootstrap_reverse.py',
                  'docs/verification/r07-bootstrap-reverse.md',
                  'internal/lifecycle/coordination/shared_unit_bootstrap.go',
                  'internal/lifecycle/coordination/shared_unit_bootstrap_test.go',
                  'internal/lifecycle/coordination/shared_unit_bootstrap_legacy.go',
                  'internal/lifecycle/coordination/shared_unit_bootstrap_legacy_test.go',
                  'internal/lifecycle/coordination/shared_unit_verified_files.go',
                  'internal/lifecycle/coordination/shared_unit_verified_files_test.go',
                  'internal/lifecycle/transcript/coordination_bootstrap.go',
                  'internal/lifecycle/transcript/coordination_bootstrap_test.go',
                  'cmd/cairn-lifecycle/bootstrap_test.go', 'cmd/cairn-lifecycle/main.go'))
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD', 'HEAD^{tree}'], cwd=ROOT, text=True).splitlines()
    return dict(head=revision[0], tree=revision[1],
                dirty=subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).splitlines(),
                source={name: control.sha((ROOT / name).read_bytes()) for name in sorted(files)},
                python=dict(path=str(Path(sys.executable).resolve()), version=sys.version),
                application_images='private same-source fixture images, not a common RC build')


def run(evidence, *, bootstrap_only=False, effects_only=False, production_shape_only=False):
    require(not evidence.exists(), 'evidence destination must be NEW')
    evidence.mkdir(mode=0o700, parents=True)
    scratch = Path(tempfile.mkdtemp(prefix='r07-bootstrap-', dir=Path.home() / '.cache'))
    cluster = None
    process_receipts = []
    # This existing observer records Popen/wait outcomes without replacing them.
    from check_r06_lifecycle_offline_restore import ProcessReceipts
    environment = dict(os.environ)
    try:
        with ProcessReceipts(evidence) as observer:
            control.write(evidence / 'source-pins.json', source_pins())
            if not bootstrap_only and not effects_only and not production_shape_only:
                for host in ('proximal', 'archon', 'talos'):
                    home, names, archives = make_layout(scratch, host, observed_modes=True)
                    control.write(evidence / (host + '-plan.json'), layout_plan(home, names, archives))
                primitive_root = scratch / 'primitive'
                primitive_root.mkdir(mode=0o700)
                control.write(evidence / 'filesystem-primitive.json', raw_filesystem_rehearsal(primitive_root))
                payload, controls, home, stage = semantic_file_fixture(scratch / 'semantic')
                control.write(evidence / 'semantic-file-contract.json', dict(
                    payload_address=payload['address'], control_address=controls['address'],
                    private_home=str(home), result='inert selected-file contract only',
                    accepted_s04r_exact_selection='unknown', runtime_qualification='not run'))
            images = control.build(scratch)
            if effects_only or production_shape_only:
                images['stager'] = scratch / 'tools/stager'
                images['tests'] = scratch / 'tools/bootstrap-tests'
                control.command(['go', 'build', '-buildvcs=false', '-o', images['stager'], './cmd/cairn-stage-lifecycle'], scratch / 'tools/build-stager')
                control.command(['go', 'test', '-race', '-c', '-o', images['tests'], './internal/lifecycle/coordination'], scratch / 'tools/build-bootstrap-tests')
                for name in ('stager', 'tests'):
                    images[name].chmod(0o500)
                control.write(evidence / 'effect-tool-pins.json', {name: control.sha(path.read_bytes()) for name, path in images.items()})
            pg = pg_bindir().resolve()
            control.write(evidence / 'postgres-tool-pins.json', {name: dict(path=str(pg / name), sha256=control.sha((pg / name).read_bytes())) for name in ('initdb', 'pg_ctl', 'postgres', 'psql')})
            control.command([pg / 'postgres', '--version'], evidence / 'postgres-version')
            cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg)
            result = dict(cases=[], status='not-run-focused-bootstrap-check')
            race = dict(status='not-run-focused-bootstrap-check')
            if not bootstrap_only and not effects_only and not production_shape_only:
                result = authenticated_cases(cluster, images, scratch / 'authenticated')
                race = authenticated_d6_race(cluster, images, scratch / 'd6-race')
            effects = []
            if production_shape_only:
                cases = []
                for host in ('archon', 'talos', 'proximal'):
                    case = 'production-' + host[0]
                    effects.append(production_shape_cases(cluster, images, scratch / case, host))
                    cases.append(case)
                case = 'production-kill'
                effects.append(production_shape_cases(cluster, images, scratch / case, 'proximal', kill_phase='after:legacy-disable-0'))
                cases.append(case)
                bootstrap = bootstrap_race = dict(status='not repeated in production-shape library fixture')
            elif effects_only:
                cases = []
                for host in ('archon', 'talos', 'proximal'):
                    for unbound in (False, True):
                        case = 'effect-' + host + ('-unbound' if unbound else '-bound')
                        effects.append(effectful_cases(cluster, images, scratch / case, host, unbound=unbound))
                        cases.append(case)
                case = 'effect-archon-race'
                effects.append(effectful_cases(cluster, images, scratch / case, 'archon', race=True))
                cases.append(case)
                for phase in ('before:exchange', 'after:exchange'):
                    case = 'effect-kill-' + phase.split(':')[0]
                    effects.append(effectful_cases(cluster, images, scratch / case, 'archon', kill_phase=phase))
                    cases.append(case)
                bootstrap = bootstrap_race = dict(status='not repeated in effect-only fixture')
            else:
                bootstrap = bootstrap_cases(cluster, images, scratch / 'bootstrap-read')
                bootstrap_race = bootstrap_cases(cluster, images, scratch / 'bootstrap-race', race=True)
                cases = ('bootstrap-read', 'bootstrap-race') if bootstrap_only else ('authenticated', 'd6-race', 'bootstrap-read', 'bootstrap-race')
            # Retain selected command/status evidence, not tokens or selectors.
            shutil.copytree(scratch / 'tools', evidence / 'tools',
                            ignore=shutil.ignore_patterns('api', 'control', 'stager', 'bootstrap-tests'))
            for case in cases:
                destination = evidence / case
                destination.mkdir(mode=0o700)
                for path in sorted((scratch / case).iterdir()):
                    if path.is_file() and (path.suffix in ('.stdout', '.stderr') or path.name.endswith(('.argv.json', '.exit.json', '-plan.json')) or path.name in ('read-calls.jsonl', 'read-counts.json', 'protected-before.json', 'protected-after.json', 'actor-before.json', 'actor-after.json', 'actor-result.json', 'effect-result.json', 'production-result.json', 'fixture-api-cleanup.json', 'production-effect-result.json', 'production-material.json', 'production-refusals.json', 'default-public-refusal.json')):
                        shutil.copyfile(path, destination / path.name)
            pg_pids = {int((cluster.root / 'data/postmaster.pid').read_text().splitlines()[0])}
            pg_pids.update(int(pid) for pid in run_sql(cluster.dsn('postgres'),
                'SELECT pid FROM pg_stat_activity', pg_bin=cluster.pg_bin).splitlines())
            require(cluster.stop(), 'owned PostgreSQL stop is unresolved; retain scratch')
            cluster = None
            process_receipts = observer.processes
            require(all(row['exit'] is not None for row in process_receipts.values()), 'a recorded direct child has no observed exit')
            require(all(not Path(f'/proc/{pid}').exists() for pid in pg_pids), 'owned PostgreSQL PID remains observable')
            require(os.environ == environment, 'fixture changed parent environment; values withheld')
            control.write(evidence / 'postgres-cleanup.json', dict(owned_pids=sorted(pg_pids), all_absent=True))
            control.write(evidence / 'summary.json', dict(schema='cairn.r07-bootstrap-rehearsal/1',
                result='SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE' if production_shape_only else 'EFFECTFUL_SOURCE_FIXTURE_PASS' if effects_only else 'SOURCE_GAP_CONFIRMED',
                operator_bootstrap='shared CLI workflow with private fixture fence; default positive UNESTABLISHED' if effects_only or production_shape_only else 'unsupported without actual stopped fence',
                operator_bootstrap_reverse='tested library with raw original bytes/inodes' if effects_only or production_shape_only else 'not exercised', checked_bound2_control=result, new_owner_d6=race,
                effectful_transitions=effects,
                bootstrap_read_only=bootstrap, bootstrap_read_only_race=bootstrap_race,
                native_calls=0, production_actions=0, remote_commands=0,
                source_gaps=GAPS))
    finally:
        # Preserve bounded command/fixture receipts even when a setup or
        # assertion fails; never copy tokens, whole homes or API credentials.
        for directory in (scratch / 'tools', *sorted(scratch.glob('effect-*')), *sorted(scratch.glob('production-*'))):
            if not directory.is_dir():
                continue
            destination = evidence / ('failure-' + directory.name)
            destination.mkdir(mode=0o700, exist_ok=True)
            for path in directory.rglob('*'):
                if path.is_file() and (path.suffix in ('.stdout', '.stderr') or path.name.endswith(('.argv.json', '.exit.json', '-plan.json')) or path.name in ('read-calls.jsonl', 'read-counts.json', 'protected-before.json', 'protected-after.json', 'effect-result.json')):
                    shutil.copyfile(path, destination / path.name)
        if cluster is not None:
            require(cluster.stop(), 'owned PostgreSQL stop is unresolved; retain scratch')
        remove_owned_scratch(scratch)
    control.write(evidence / 'cleanup.json', dict(scratch_absent=not scratch.exists(),
        direct_children=[dict(pid=row['pid'], exit=row['exit']) for row in process_receipts.values()],
        postgres='stopped by OwnedCluster.stop; scratch removed', home_and_auth='unchanged'))
    return json.loads((evidence / 'summary.json').read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--evidence', type=Path)
    mode.add_argument('--semantic-reverse-selection', type=Path,
                      help='operator staging only; immutable files and exact serve selectors, no execution')
    parser.add_argument('--sha256', help='exact semantic reverse selection pin')
    parser.add_argument('--publication-dir', type=Path,
                        help='new empty owned0700 directory for payload/control file-verifier references')
    parser.add_argument('--bootstrap-only', action='store_true', help='focus new bootstrap cases; do not repeat the independent checked-control fixtures')
    parser.add_argument('--effects-only', action='store_true', help='focused real API/PG exchange and exact retained2 reverse, with systemd observation fixtures')
    parser.add_argument('--production-shape-only', action='store_true', help='real8+1+1 shared CLI fixture-fence proof and default active-worker refusal')
    args = parser.parse_args()
    if args.semantic_reverse_selection is not None:
        try:
            result = operator_semantic_reverse(args.semantic_reverse_selection, args.sha256, args.publication_dir)
        except (HostRefusal, semantic.ClosureError, OSError, ValueError) as error:
            print(json.dumps(dict(schema='cairn.r07-semantic-reverse-stage/1', error=str(error),
                                  service_actions=0, model_executions=0)))
            return 1
    else:
        require(args.sha256 is None, '--sha256 requires --semantic-reverse-selection')
        require(args.publication_dir is None, '--publication-dir requires --semantic-reverse-selection')
        require(not (args.effects_only and args.bootstrap_only), 'choose one focused fixture')
        require(sum((args.bootstrap_only, args.effects_only, args.production_shape_only)) <= 1, 'choose one focused fixture')
        result = run(args.evidence.absolute(), bootstrap_only=args.bootstrap_only, effects_only=args.effects_only,
                     production_shape_only=args.production_shape_only)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    raise SystemExit(main())
