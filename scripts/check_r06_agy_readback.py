#!/usr/bin/env python3
"""Consume genuine unbound Agy layers without claiming native qualification."""
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


GAP = 'account hook/config coverage is unknown: selected native settings/readback seam is unavailable'
AGY_GAP = 'Agy native session/file-read/exec provenance is unobserved; code preparation is not native qualification'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def not_ready(result, *, association_only=False, typed=False):
    require(result['Census']['FixtureReady'] is association_only and result['Error'] ==
        ('' if association_only else AGY_GAP if typed else GAP),
        'unexpected consumed association/coverage result: ' + json.dumps(result))
    require(result['Census']['ExternalCoverage'] == 'unknown'
        and result['Census']['NativeRefresh'] == 'unobserved', 'refusal must not imply native coverage/refresh')
    return dict(status='ASSOCIATION_ONLY' if association_only else 'NOTREADY', actual_error=result['Error'],
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


def run(images, evidence, *, association_only=False, typed=False):
    # Reuse construction, not a prior Agy test method or native baseline.
    case = R04HostWiringTests(methodName='runTest')
    case.root, case.binary, case.stager = evidence, images['control'], images['stager']
    fixture = case.fixture('owned-agy', harness='agy')
    if association_only:
        # Fixture construction: source settings live outside the empty account.
        # No native client has loaded them, and the profile bytes stay exact.
        settings = fixture.root / 'original-settings.json'
        fixture.settings.rename(settings)
        fixture.settings = settings
        fixture.hook_plan['settings'] = dict(path=str(settings), sha256=sha(settings))
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
    for directory in (fixture.coord['state_dir'], fixture.memory['state_dir']):
        Path(directory).mkdir(mode=0o700, exist_ok=True)
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
        Clients=[dict(Path=str(fixture.api), SHA256=sha(fixture.api))], HookSelections=[],
        UnboundReceipts=[dict(Path=str(fixture.receipt_path), SHA256=sha(fixture.receipt_path))])
    proposal = dict(Bindings=[selection], Sessions=[], Callbacks=[])
    if typed:
        workspace = fixture.root / 'project'
        project_hooks = workspace / '.agents'
        workspace.mkdir(mode=0o700)
        project_hooks.mkdir(mode=0o700)
        layers = []
        for name, destination in (('account', fixture.account / 'hooks.json'),
                                  ('project', project_hooks / 'hooks.json')):
            destination.write_bytes(Path(selected['settings']['path']).read_bytes())
            destination.chmod(0o600)
            layers.append(dict(Layer=name,
                Generation=dict(Path=selected['selection']['path'], SHA256=selected['selection']['sha256']),
                Settings=dict(Path=selected['settings']['path'], SHA256=selected['settings']['sha256']),
                Installed=dict(Path=str(destination), SHA256=sha(destination))))
        readback = write(fixture.root / 'agy-readback.json',
            dict(Schema='cairn.watch-agy-readback/1', Harness='agy', EvidenceKind='code-preparation'))
        inventory = [dict(Path=str(path), SHA256=sha(path))
            for directory in (fixture.account, workspace) for path in sorted(directory.rglob('*')) if path.is_file()]
        packet = write(fixture.root / 'agy-hook-selection.json', dict(Schema='cairn.watch-agy-hooks/1',
            Harness='agy', Account=str(fixture.account), Workspace=str(workspace),
            Config=dict(Path=str(config), SHA256=sha(config)), Readback=readback,
            Layers=layers, LayerFiles=inventory))
        catalog['HookSelections'] = [packet]
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
    require(result['Census']['Facts'].get('file:' + str(fixture.receipt_path)) == sha(fixture.receipt_path),
        'consumed result lacks the selected receipt fact')
    report = dict(schema='cairn.r06-agy-readback-association/1', **not_ready(result, association_only=association_only, typed=typed),
        consumed_entry='CLI.InspectWatchUnitCensus', unbound_association_observed=True,
        typed_agy_reader_reached=typed, cumulative_layers=2 if typed else 0,
        protected_entries=len(before), protected_unchanged=True, credential_content_opens=0,
        api_calls=0, native_executions=0, model_calls=0, tests_are_setup_only=True)
    write(evidence / 'gap-result.json', report)
    if association_only:
        write(evidence / 'go-unit-fixture.json', dict(Catalog=catalog_pin, Proposal=proposal,
            Receipt=dict(Path=str(fixture.receipt_path), SHA256=sha(fixture.receipt_path)),
            Config=dict(Path=str(config), SHA256=sha(config)), Root=str(fixture.root)))
    if typed:
        write(evidence / 'go-typed-fixture.json', dict(Catalog=catalog_pin, Proposal=proposal,
            Selection=packet, Readback=readback, Root=str(fixture.root)))
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
        for name, empty, typed in (('native-unknown', False, False), ('association-only', True, False),
                                    ('typed-agy-preparation', False, True)):
            evidence = args.evidence / name
            evidence.mkdir(mode=0o700)
            run(images, evidence, association_only=empty, typed=typed)
    finally:
        after = {role: dict(path=str(path), sha256=sha(path)) for role, path in images.items()}
        write(args.evidence / 'images-after-use.json', after)
        require(all(after[role]['sha256'] == pins['images'][role]['sha256'] for role in images),
            'fixture image changed during use')


if __name__ == '__main__':
    main()
