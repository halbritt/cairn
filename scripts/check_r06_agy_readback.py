#!/usr/bin/env python3
"""Reproduce the current Agy census interface gap, without native execution.

The genuine R02/R04 producer selects unbound profiles. The consumed census
requires a bound profile before inspecting hooks. Expected refusal is evidence
of that gap, not a positive Agy/native/D2 qualification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

import stage_lifecycle_hooks as hooks
from check_r06_native_unit import owned_command
from check_r06_unit_census import write
from disposable_host import require
from test_r04_host_wiring import R04HostWiringTests


GAP = 'census reference outside observation universe: '


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def not_ready(result):
    require(result['Census']['FixtureReady'] is False and result['Error'] == GAP,
        'expected the exact consumed unbound-profile refusal: ' + json.dumps(result))
    require(result['Census']['ExternalCoverage'] == 'unknown'
        and result['Census']['NativeRefresh'] == 'unobserved', 'refusal must not imply native coverage/refresh')
    return dict(status='NOTREADY', actual_error=result['Error'],
        native_qualification=False, authenticated_d2=False)


def snapshot(root, credential):
    result = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        row = dict(mode=info.st_mode, uid=info.st_uid, gid=info.st_gid,
            inode=info.st_ino, device=info.st_dev, bytes=info.st_size)
        if path.is_file() and path != credential:
            row['sha256'] = sha(path)
        result[str(path)] = row
    return result


def run(images, evidence):
    # Reuse construction, not a prior Agy test method or native baseline.
    case = R04HostWiringTests(methodName='runTest')
    case.root, case.binary, case.stager = evidence, images['control'], images['stager']
    fixture = case.fixture('owned-agy', harness='agy')
    selected = hooks.stage(str(images['stager']), fixture.hook_root, fixture.hook_plan)
    generation = Path(selected['selection']['path']).parent
    producer = json.loads((generation / 'hook-selection.json').read_bytes())
    require(producer['harness'] == 'agy' and producer['unsupported_operations'] == ['ordinary memory hook'],
        'actual producer support changed')
    require([(row['role'], row['event']) for row in producer['rows']] ==
        [('coordination', 'PreInvocation'), ('coordination', 'Stop')], 'actual Agy event coverage changed')
    for path in (fixture.coord_path, fixture.memory_path):
        require(json.loads(path.read_bytes()).get('inbox_recall_binding') is None,
            'probe must retain the actual unbound producer profile')
    require(fixture.api_calls() == [], 'setup unexpectedly executed the synthetic API')
    write(evidence / 'actual-producer.json', dict(selected=selected, producer=producer,
        coordination=dict(path=str(fixture.coord_path), sha256=sha(fixture.coord_path)),
        memory=dict(path=str(fixture.memory_path), sha256=sha(fixture.memory_path)),
        native_executed=False, callbacks_executed=False, api_calls=0))

    # A metadata-only, zero-byte, unreadable fixture selector. It is never
    # submitted as authentication and no session/registration/state is created.
    credential = Path(fixture.coord['token_file'])
    credential.touch(mode=0o000, exist_ok=False)
    credential.chmod(0o000)
    config_root = fixture.root / 'selected-configs'
    active = config_root / 'unit'
    config_root.mkdir(mode=0o700)
    active.mkdir(mode=0o700)
    config = active / 'coordination.json'
    config.write_bytes(fixture.coord_path.read_bytes())
    config.chmod(0o600)
    callbacks = fixture.root / 'callbacks'
    callbacks.mkdir(mode=0o700)
    selection = dict(Directory=str(active), Pins={config.name: sha(config)})
    catalog = dict(Schema='cairn.watch-unit-catalog/1', Scope='disposable-fixture', Root=str(fixture.root),
        ConfigRoot=str(config_root), CallbackRoot=str(callbacks), Selections=[selection],
        Sessions=[], Callbacks=[], CredentialPaths=[str(credential)],
        Bindings=[dict(Path=str(fixture.binding), SHA256=sha(fixture.binding))],
        Clients=[dict(Path=str(fixture.api), SHA256=sha(fixture.api))], HookSelections=[])
    proposal = dict(Bindings=[selection], Sessions=[], Callbacks=[])
    catalog_pin = write(evidence / 'catalog.json', catalog)
    result_path = evidence / 'consumer.result.json'
    plan = evidence / 'consumer.plan.json'
    write(plan, dict(Mode='inspect', Catalog=catalog_pin, Proposal=proposal, Result=str(result_path)))
    before = snapshot(fixture.root, credential)
    write(evidence / 'protected-before.json', before)
    trace = evidence / 'consumer.strace'
    argv = ['strace', '-f', '-qq', '-s', '4096', '-e',
        'trace=open,openat,creat,rename,renameat,renameat2,unlink,unlinkat,flock,kill,connect',
        '-o', str(trace), str(images['observer']), '-test.run=^TestR06UnitCensusProcess$', '-test.v']
    owned_command(argv, evidence / 'consumer', env=dict(os.environ, CAIRN_R06_CENSUS_PLAN=str(plan)))
    after = snapshot(fixture.root, credential)
    write(evidence / 'protected-after.json', after)
    require(before == after, 'read-only refusal changed the owned producer material')
    lines = trace.read_text().splitlines()
    require(not any(str(credential) in line and ('open(' in line or 'openat(' in line) for line in lines),
        'consumer opened the credential selector for content')
    forbidden = ('creat(', 'rename(', 'renameat(', 'renameat2(', 'unlink(', 'unlinkat(', 'flock(', 'kill(', 'connect(')
    require(not any(any(operation in line for operation in forbidden) for line in lines),
        'consumer mutation/lock/signal/network operation observed')
    require(not any(str(fixture.root) in line and any(flag in line for flag in
        ('O_WRONLY', 'O_RDWR', 'O_CREAT', 'O_TRUNC')) for line in lines),
        'consumer opened owned producer material for writing')
    result = json.loads(result_path.read_bytes())
    report = dict(schema='cairn.r06-agy-readback-gap/1', **not_ready(result),
        consumed_entry='CLI.InspectWatchUnitCensus', hook_inspection_reached=False,
        cause='members reads config.inbox_recall_binding before inspectHookSelections',
        required_outside_lease='internal/lifecycle/coordination/shared_unit_census_files.go',
        protected_entries=len(before), protected_unchanged=True, credential_content_opens=0,
        api_calls=0, native_executions=0, model_calls=0, tests_are_setup_only=True)
    write(evidence / 'gap-result.json', report)
    print(json.dumps(report), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    require(args.evidence.is_absolute() and not args.evidence.exists(), 'new absolute evidence root required')
    args.evidence.mkdir(mode=0o700)
    pins = json.loads(args.images.read_bytes())
    require(pins['schema'] == 'cairn.r06-agy-readback-images/1', 'explicit fixture image pins required')
    images = {}
    for role in ('control', 'stager', 'observer'):
        pin = pins['images'][role]
        path = Path(pin['path'])
        require(path.is_absolute() and not path.is_symlink() and path.is_file()
            and not path.stat().st_mode & 0o022 and sha(path) == pin['sha256'], 'changed/unsafe ' + role + ' image')
        images[role] = path
    write(args.evidence / 'images-before-use.json', pins)
    try:
        run(images, args.evidence)
    finally:
        after = {role: dict(path=str(path), sha256=sha(path)) for role, path in images.items()}
        write(args.evidence / 'images-after-use.json', after)
        require(all(after[role]['sha256'] == pins['images'][role]['sha256'] for role in images),
            'fixture image changed during use')


if __name__ == '__main__':
    main()
