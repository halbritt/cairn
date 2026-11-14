#!/usr/bin/env python3
"""R07 bootstrap gap planner and newly owned filesystem/API regression fixture.

There is no installed-layout apply/reverse entrypoint. The current public Go
control cannot construct its private drain receipt for the original mixed /2
layout. A filesystem rehearsal and a checked bound-/2 roundtrip are reported
separately from that unsupported operator operation. Run through mise host:exec.
"""

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

import check_r06_unit_control as control
from disposable_host import OwnedCluster, pg_bindir, require, run_sql, stop_process
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


def source_pins():
    files = {ref.split(':')[0] for ref in GAPS.values()}
    files.update(('go.mod', 'go.sum', 'scripts/disposable_host.py',
                  'scripts/check_r06_unit_control.py', 'scripts/check_r06_status_consumer.py',
                  'scripts/check_r06_lifecycle_offline_restore.py',
                  'scripts/check_r07_bootstrap_reverse.py', 'scripts/test_r07_bootstrap_reverse.py',
                  'docs/verification/r07-bootstrap-reverse.md'))
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD', 'HEAD^{tree}'], cwd=ROOT, text=True).splitlines()
    return dict(head=revision[0], tree=revision[1],
                source={name: control.sha((ROOT / name).read_bytes()) for name in sorted(files)},
                python=dict(path=str(Path(sys.executable).resolve()), version=sys.version),
                application_images='two private fixture images, not a common RC build')


def run(evidence):
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
            cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bindir().resolve())
            result = authenticated_cases(cluster, images, scratch / 'authenticated')
            race = authenticated_d6_race(cluster, images, scratch / 'd6-race')
            # Retain selected command/status evidence, not tokens or selectors.
            shutil.copytree(scratch / 'tools', evidence / 'tools')
            for case in ('authenticated', 'd6-race'):
                destination = evidence / case
                destination.mkdir(mode=0o700)
                for path in sorted((scratch / case).iterdir()):
                    if path.is_file() and (path.suffix in ('.stdout', '.stderr') or path.name.endswith(('.argv.json', '.exit.json', '-plan.json')) or path.name in ('read-calls.jsonl', 'read-counts.json', 'protected-before.json', 'protected-after.json', 'actor-before.json', 'actor-after.json', 'actor-result.json')):
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
                result='SOURCE_GAP_CONFIRMED', operator_bootstrap='unsupported',
                operator_bootstrap_reverse='unsupported', checked_bound2_control=result, new_owner_d6=race,
                native_calls=0, production_actions=0, remote_commands=0,
                source_gaps=GAPS))
    finally:
        if cluster is not None:
            require(cluster.stop(), 'owned PostgreSQL stop is unresolved; retain scratch')
        remove_owned_scratch(scratch)
    control.write(evidence / 'cleanup.json', dict(scratch_absent=not scratch.exists(),
        direct_children=[dict(pid=row['pid'], exit=row['exit']) for row in process_receipts.values()],
        postgres='stopped by OwnedCluster.stop; scratch removed', home_and_auth='unchanged'))
    return json.loads((evidence / 'summary.json').read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.evidence.absolute())
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
