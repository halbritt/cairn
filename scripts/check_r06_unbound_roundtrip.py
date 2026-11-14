#!/usr/bin/env python3
"""Genuine finite Go /3 A -> B -> retained-/3 A unit-control evidence.

This does not execute a native client, qualify current Agy, or claim legacy /2
reverse. All profiles come from the maintained R02/R04 producer.
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
import shlex
import stat
import uuid

from check_r06_unit_census import write
from disposable_host import require, OwnedCluster, DisposableHost, pg_bindir
from check_r06_status_consumer import snapshot as api_snapshot
from test_r04_host_wiring import R04HostWiringTests
import stage_lifecycle_hooks as hooks


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def produced_fixture(images, evidence, *, scratch=None, configure=None, target_inputs=None):
    case = R04HostWiringTests(methodName='runTest')
    case.root = scratch or evidence
    case.binary, case.stager = images['control'], images['stager']
    f = case.fixture('unit', harness='agy')
    if configure:
        configure(f)
        f.publish_configuration(enabled=False)
    credential = Path(f.coord['token_file'])
    if not configure:
        credential.touch(mode=0o000)
        credential.chmod(0o000)
    for path in (f.coord['state_dir'], f.memory['state_dir']):
        Path(path).mkdir(mode=0o700, exist_ok=True)
    settings = f.root / 'original-settings.json'
    f.settings.rename(settings)
    f.settings = settings
    f.hook_plan.update(settings=dict(path=str(settings), sha256=sha(settings)), selection=f.selected)
    generations = {}
    for label in ('A', 'B'):
        if label == 'B':
            # Change producer inputs, never published profile bytes.
            f.coord['roundtrip_generation'] = label
            f.memory['roundtrip_generation'] = label
            if target_inputs:
                target_inputs(f)
            f.publish_configuration(enabled=False)
            f.hook_plan.update(id=str(uuid.uuid4()), selection=f.selected)
        selected = hooks.stage(str(images['stager']), f.hook_root, f.hook_plan)
        generation = dict(Receipt=dict(Path=str(f.receipt_path), SHA256=sha(f.receipt_path)),
            Config=dict(Path=str(f.coord_path), SHA256=sha(f.coord_path)),
            Memory=dict(Path=str(f.memory_path), SHA256=sha(f.memory_path)),
            Binding=dict(Path=str(f.binding), SHA256=sha(f.binding)), hook=selected)
        for key in ('Config', 'Memory'):
            require(json.loads(Path(generation[key]['Path']).read_bytes())['inbox_recall_binding'] is None,
                    'actual producer did not retain null profile binding')
        generations[label] = generation
    config_root = f.root / 'selected-configs'
    active = config_root / 'unit'
    config_root.mkdir(mode=0o700)
    active.mkdir(mode=0o700)
    config = active / 'coordination.json'
    config.write_bytes(Path(generations['A']['Config']['Path']).read_bytes())
    config.chmod(0o400)
    (active / '.watch.lock').touch(mode=0o600)
    callbacks = f.root / 'callbacks'
    callbacks.mkdir(mode=0o700)
    selections, catalogs = {}, {}
    for label, generation in generations.items():
        selection = dict(Directory=str(active), Pins={config.name:generation['Config']['SHA256']})
        selections[label] = selection
        catalog = dict(Schema='cairn.watch-unit-catalog/1', Scope='disposable-fixture', Root=str(f.root),
            ConfigRoot=str(config_root), CallbackRoot=str(callbacks), Selections=[selection],
            Sessions=[], Callbacks=[], CredentialPaths=[str(credential)], Bindings=[generation['Binding']],
            Clients=[dict(Path=str(f.api), SHA256=sha(f.api))], HookSelections=[], UnboundReceipts=[generation['Receipt']])
        catalogs[label] = write(evidence / ('catalog-' + label + '.json'), catalog)
    fixture = dict(Root=str(f.root), ConfigRoot=str(config_root), Credential=str(credential),
        CatalogA=catalogs['A'], CatalogB=catalogs['B'], SelectionA=selections['A'], SelectionB=selections['B'],
        ReceiptA=generations['A']['Receipt'], ReceiptB=generations['B']['Receipt'],
        ConfigA=generations['A']['Config'], ConfigB=generations['B']['Config'],
        MemoryA=generations['A']['Memory'], MemoryB=generations['B']['Memory'])
    write(evidence / 'actual-producer.json', dict(generations=generations, native_executed=False,
        ordinary_agy_memory=False, reverse_scope='retained Go /3, not legacy /2'))
    write(evidence / 'go-target-fixture.json', fixture)
    return f, fixture


def images_from(path, roles):
    pins = json.loads(path.read_bytes())
    require(pins['schema'] == 'cairn.r06-unit-images/1', 'explicit unit image manifest required')
    result = {}
    for role in roles:
        pin = pins['images'][role]
        image = Path(pin['path'])
        require(image.is_absolute() and image.is_file() and not image.is_symlink()
            and image.stat().st_mode & 0o022 == 0 and sha(image) == pin['sha256'], 'unsafe/changed ' + role)
        result[role] = image
    return result, pins


READS = {'wake-attempts', 'event-watch', 'session-inbox-control',
         'session-inbox-attempt-status', 'session-inbox-poll-status'}


def snapshot_root(root, credential, *, api_home=None):
    """Whole owned-root metadata; selectors and every inode alias are stat-only."""
    root, credential = Path(root), Path(credential)
    credential_info = credential.lstat()
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        info = path.lstat()
        row = dict(mode=info.st_mode, uid=info.st_uid, gid=info.st_gid,
            device=info.st_dev, inode=info.st_ino, bytes=info.st_size,
            links=info.st_nlink, mtime=info.st_mtime_ns, ctime=info.st_ctime_ns)
        private = path == credential or os.path.samestat(info, credential_info) or (
            api_home is not None and path.is_relative_to(api_home))
        if private or stat.S_IMODE(info.st_mode) == 0:
            row['content_read'] = False
        elif stat.S_ISREG(info.st_mode):
            row['sha256'] = sha(path)
        elif stat.S_ISLNK(info.st_mode):
            row['link'] = os.readlink(path)
        result[str(path)] = row
    return result


def forward(path, argv):
    transport = json.loads(path.read_bytes())
    operation = argv[-1]
    require(operation in READS, 'fixture transport attempted a non-read operation')
    counts_path = path.with_name('read-counts.json')
    counts = json.loads(counts_path.read_bytes()) if counts_path.exists() else {}
    counts[operation] = counts.get(operation, 0) + 1
    write(counts_path, counts)
    result = subprocess.run([transport['api'], *argv], input=sys.stdin.read(), text=True,
        capture_output=True, timeout=20)
    with path.with_name('authenticated-reads.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(operation=operation, argv=argv, exit=result.returncode,
            stdout=result.stdout, stderr=result.stderr)) + '\n')
    actor = transport.get('actor')
    if actor and operation == actor['operation'] and counts[operation] == actor['count']:
        selected = Path(actor['path'])
        root = Path(transport['root'])
        require(selected.is_relative_to(root), 'actor path outside NEW owned unit')
        before = snapshot_root(root, transport['credential'], api_home=Path(transport['api_home']))
        if actor['action'] == 'append':
            mode = selected.stat().st_mode & 0o777
            selected.chmod(mode | 0o200)
            with selected.open('ab') as stream:
                stream.write(b' ')
            selected.chmod(mode)
        elif actor['action'] in ('inode', 'credential-inode'):
            info = selected.stat()
            # A credential is moved, never read/copied/hashed.
            raw = None if actor['action'] == 'credential-inode' else selected.read_bytes()
            backup = root / ('api-home/credential-before-actor' if raw is None else 'actor-original')
            selected.rename(backup)
            selected.write_bytes(b'' if raw is None else raw)
            selected.chmod(info.st_mode & 0o777)
        else:
            raise AssertionError('unknown actor action')
        after = snapshot_root(root, transport['credential'], api_home=Path(transport['api_home']))
        write(path.with_name('actor.json'), dict(path=str(selected), action=actor['action'],
            count=counts[operation], before=before, after=after))
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def plan(fixture, *, reverse=False):
    current = fixture['SelectionB' if reverse else 'SelectionA']
    source = fixture['ConfigA' if reverse else 'ConfigB']
    current_dir = Path(current['Directory'])
    retained_a = Path(fixture['ConfigRoot']) / 'retained-A'
    source_path = retained_a / 'coordination.json' if reverse else Path(source['Path'])
    return dict(schema='cairn.unit-control/2', current=dict(directory=str(current_dir), pins=current['Pins']),
        next=[dict(name='coordination.json', path=str(source_path), sha256=source['SHA256'])],
        retained=str(Path(fixture['ConfigRoot']) / ('retained-B' if reverse else 'retained-A')),
        census=dict(Catalog=fixture['CatalogB' if reverse else 'CatalogA'],
            Proposal=dict(Bindings=[current], Sessions=[], Callbacks=[]),
            Retained=[dict(Directory=str(retained_a), Pins=fixture['SelectionA']['Pins'])] if reverse else [],
            TargetReceipt=fixture['ReceiptA' if reverse else 'ReceiptB']))


def protected(rows, config_root):
    # Only the declared selection/preparation/lock transition is mutable.
    return {p:v for p,v in rows.items() if not Path(p).is_relative_to(config_root)}


def invoke(images, host, fixture, body, evidence, label, *, reverse=False, error=None, final=False):
    path = evidence / (label + '.plan.json')
    pin = write(path, body)
    root, credential = Path(fixture['Root']), Path(fixture['Credential'])
    before = snapshot_root(root, credential, api_home=host.home)
    database_before = api_snapshot(host.dsn, host.pg_bin, root / 'state-placeholder')
    argv = [str(images['control']), 'unit-control', 'reverse' if reverse else 'switch',
            '--plan', str(path), '--sha256', pin['SHA256']]
    process = subprocess.run(argv, env=host.env, text=True, capture_output=True, timeout=150)
    (evidence / (label + '.stdout')).write_text(process.stdout)
    (evidence / (label + '.stderr')).write_text(process.stderr)
    write(evidence / (label + '.exit.json'), dict(exit=process.returncode, argv=argv))
    after = snapshot_root(root, credential, api_home=host.home)
    database_after = api_snapshot(host.dsn, host.pg_bin, root / 'state-placeholder')
    write(evidence / (label + '.preservation.json'), dict(before=before, after=after,
        database_before=database_before, database_after=database_after))
    require(database_before == database_after, 'public control changed protected API state')
    parsed = json.loads(process.stdout)
    if error:
        require(process.returncode == 1 and error in parsed['error'] and not parsed['exchange']['Published'],
                'wrong public refusal: ' + json.dumps(parsed))
        require(parsed['inspected'] is final, 'refusal happened in the wrong capture/D6 phase')
        if final:
            actor_path = evidence / 'actor.json'
            require(actor_path.exists(), 'final actor was not consumed')
            actor = json.loads(actor_path.read_bytes())
            require(protected(before, Path(fixture['ConfigRoot'])) == protected(actor['before'], Path(fixture['ConfigRoot']))
                and protected(after, Path(fixture['ConfigRoot'])) == protected(actor['after'], Path(fixture['ConfigRoot'])),
                'control changed protected bytes/metadata beyond the declared actor')
        else:
            require(before == after, 'initial refusal changed owned unit state')
    else:
        require(process.returncode == 0 and parsed['inspected'] and parsed['exchange']['Published'],
                'public transition failed: ' + json.dumps(parsed))
        require(protected(before, Path(fixture['ConfigRoot'])) == protected(after, Path(fixture['ConfigRoot'])),
                'transition changed protected profile/source/credential/state data')
        require(parsed['census']['NativeRefresh'] == 'unobserved' and parsed['census']['ExternalCoverage'] == 'unknown',
                'finite transition claimed native qualification')
    return parsed


CASES = ('roundtrip', 'missing-target', 'wrong-target-pin', 'corrupt-target-source',
    'foreign-target', 'ambiguous-current', 'omitted-target', 'unlinked-copy',
    'changed-memory-identity', 'credential-target-alias', 'credential-profile-alias',
    'wrong-current-pin', 'retained-unknown', 'retained-partial', 'retained-lock',
    'retained-wrong-bytes', 'final-target-memory', 'final-target-inode',
    'final-current-credential', 'final-retained-inode')


def authenticated_cases(images, evidence):
    scratch = Path(tempfile.mkdtemp(prefix='r06urt-'))
    write(evidence / 'scratch.json', dict(path=str(scratch)))
    pg = pg_bindir()
    write(evidence / 'pg-before-use.json', {n:dict(path=str(pg/n), sha256=sha(pg/n))
        for n in ('initdb','pg_ctl','postgres','createdb','psql')})
    cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg)
    outcomes = []
    try:
        for name in CASES:
            case = evidence / name
            case.mkdir(mode=0o700)
            owned = scratch / name
            owned.mkdir(mode=0o700)
            state = {}
            try:
                def configure(f):
                    host = DisposableHost.start(cluster, binary=images['api'], repo='fixture:unbound:roundtrip',
                        database='ur_' + name.replace('-', '_'), home=f.root/'api-home',
                        socket_path=f.root/'api.sock', log=case/'api.log', roles=('worker',), label='unbound_roundtrip')
                    state['host'] = host
                    require(host.process.pid > 1, 'unsafe owned API PID')
                    transport = dict(api=str(images['api']), root=str(f.root), credential=str(host.token('worker')),
                        api_home=str(host.home))
                    write(case/'transport.json', transport)
                    f.api.chmod(0o700)
                    f.api.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable,'-B',str(Path(__file__).resolve()),
                        '--forward-api',str(case/'transport.json')]) + ' "$@"\n')
                    f.api.chmod(0o500)
                    for config in (f.coord, f.memory):
                        config.update(socket=str(host.socket), token_file=str(host.token('worker')), repo=host.repo)
                def target_inputs(f):
                    if name == 'foreign-target':
                        f.coord['repo'] = f.memory['repo'] = 'foreign:target'
                    elif name == 'changed-memory-identity':
                        directory = f.root/'foreign-memory-state'
                        directory.mkdir(mode=0o700)
                        f.memory['state_dir'] = str(directory)
                f, fixture = produced_fixture(images, case, scratch=owned, configure=configure, target_inputs=target_inputs)
                host = state['host']
                body = plan(fixture)
                expected = None
                final = False
                reverse = False
                if name.startswith('retained-') or name == 'final-retained-inode':
                    invoke(images,host,fixture,body,case,'forward')
                    body = plan(fixture, reverse=True)
                    reverse = True
                if name == 'missing-target':
                    del body['census']['TargetReceipt']
                    expected = 'lacks a tracked receipt descriptor'
                elif name == 'wrong-target-pin':
                    body['census']['TargetReceipt'] = dict(body['census']['TargetReceipt'], SHA256='0'*64)
                    expected = 'census pin changed'
                elif name in ('corrupt-target-source','credential-profile-alias'):
                    receipt = json.loads(Path(fixture['ReceiptB']['Path']).read_bytes())
                    if name == 'corrupt-target-source':
                        receipt['members']['coordination.py']['sha256'] = '0'*64
                        expected = 'census pin changed'
                    else:
                        alias = f.root/'profile-credential-alias.json'
                        os.link(host.token('worker'),alias)
                        receipt['coordination_configs'][0] = dict(path=str(alias), sha256='0'*64)
                        expected = 'hardlink alias'
                    body['census']['TargetReceipt'] = write(f.root/'derived-negative-receipt.json',receipt)
                elif name == 'foreign-target':
                    expected = 'foreign to the actual current unit'
                elif name == 'ambiguous-current':
                    path = Path(fixture['SelectionA']['Directory'])/'duplicate.json'
                    path.write_bytes(Path(fixture['ConfigA']['Path']).read_bytes());path.chmod(0o400)
                    current = dict(fixture['SelectionA'], Pins=dict(fixture['SelectionA']['Pins'], **{'duplicate.json':fixture['ConfigA']['SHA256']}))
                    catalog = json.loads(Path(fixture['CatalogA']['Path']).read_bytes());catalog['Selections']=[current]
                    body['current']['pins']=current['Pins'];body['census']['Proposal']['Bindings']=[current]
                    body['census']['Catalog']=write(f.root/'ambiguous-catalog.json',catalog)
                    body['next'].append(dict(body['next'][0],name='duplicate.json'))
                    expected = 'ambiguous target/current unit identity'
                elif name == 'omitted-target':
                    body['next'][0].update(path=fixture['ConfigA']['Path'],sha256=fixture['ConfigA']['SHA256'])
                    expected = 'target proposal omits or changes'
                elif name == 'unlinked-copy':
                    path=f.root/'unlinked.json';path.write_bytes(Path(fixture['ConfigB']['Path']).read_bytes());path.chmod(0o400)
                    body['next'][0]['path']=str(path);expected='lacks a tracked receipt descriptor'
                elif name == 'changed-memory-identity':
                    expected = 'cannot change the affected memory state directory'
                elif name == 'credential-target-alias':
                    alias=f.root/'target-credential-alias.json';os.link(host.token('worker'),alias)
                    body['census']['TargetReceipt']=dict(Path=str(alias),SHA256='0'*64)
                    expected='refuses credential-content reads through aliases'
                elif name == 'wrong-current-pin':
                    body['current']['pins']=dict(body['current']['pins'],**{'coordination.json':'0'*64})
                    expected='watch config pin changed'
                elif name == 'retained-unknown':
                    body['census']['Retained']=[];expected='unknown or omitted selected config directory'
                elif name == 'retained-partial':
                    Path(body['next'][0]['path']).unlink();expected='no such file or directory'
                elif name == 'retained-lock':
                    lock=Path(body['next'][0]['path']).parent/'.watch.lock';lock.unlink();lock.touch(mode=0o600)
                    expected='retained copy watch-lock identity differs'
                elif name == 'retained-wrong-bytes':
                    path=Path(body['next'][0]['path']);path.chmod(0o600);path.write_bytes(Path(fixture['ConfigB']['Path']).read_bytes());path.chmod(0o400)
                    body['next'][0]['sha256']=fixture['ConfigB']['SHA256']
                    body['census']['Retained'][0]['Pins']={'coordination.json':fixture['ConfigB']['SHA256']}
                    expected='target proposal omits or changes'
                elif name.startswith('final-'):
                    actor_path = fixture['MemoryB']['Path'] if name=='final-target-memory' else fixture['ConfigB']['Path']
                    action = 'append' if name=='final-target-memory' else 'inode'
                    if name=='final-current-credential':actor_path=str(host.token('worker'));action='credential-inode'
                    if name=='final-retained-inode':actor_path=body['next'][0]['path']
                    transport=json.loads((case/'transport.json').read_bytes());transport['actor']=dict(operation='event-watch',count=4 if reverse else 2,path=actor_path,action=action)
                    write(case/'transport.json',transport);expected='D6 drain receipt invalidated';final=True
                result=invoke(images,host,fixture,body,case,'reverse' if reverse else 'control',reverse=reverse,error=expected,final=final)
                if name=='roundtrip':
                    result=invoke(images,host,fixture,plan(fixture,reverse=True),case,'reverse',reverse=True)
                    require(result['exchange']['Active']['Pins']==fixture['SelectionA']['Pins'],'roundtrip did not restore exact Go /3 A')
                    locks=[Path(fixture['SelectionA']['Directory'])/'.watch.lock',Path(fixture['ConfigRoot'])/'retained-A/.watch.lock',Path(fixture['ConfigRoot'])/'retained-B/.watch.lock']
                    require(all(os.path.samefile(locks[0],p) for p in locks),'roundtrip split watch-lock identity')
                outcomes.append(dict(case=name,status='PASS',public_operation='reverse' if reverse else 'switch',scope='Go /3 finite correspondence; no legacy /2/native claim'))
                write(evidence/'outcomes-partial.json',outcomes)
                print(json.dumps(outcomes[-1]),flush=True)
            finally:
                host=state.get('host')
                if host:
                    pid=host.process.pid if host.process else None;require(pid is None or pid>1,'unsafe owned API PID');code=host.close()
                    write(case/'api-cleanup.json',dict(pid=pid,exit=code,joined=True,group_signals=False))
    finally:
        pidfile=scratch/'pg/data/postmaster.pid';pid=int(pidfile.read_text().splitlines()[0]) if pidfile.exists() else None
        require(pid is None or pid>1,'unsafe owned PG PID');stopped=cluster.stop()
        write(evidence/'cleanup.json',dict(pid=pid,stopped=stopped,group_signals=False));require(stopped,'owned PG cleanup failed')
    write(evidence/'outcomes.json',outcomes)
    print(f'Unbound public roundtrip: {len(outcomes)} passed, 0 skipped; no native qualification',flush=True)


def main():
    if len(sys.argv)>1 and sys.argv[1]=='--forward-api':
        return forward(Path(sys.argv[2]),sys.argv[3:])
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images',type=Path,required=True);parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--setup-only',action='store_true');args=parser.parse_args()
    require(args.evidence.is_absolute() and not args.evidence.exists(),'new absolute evidence required')
    args.evidence.mkdir(mode=0o700)
    roles=('control','stager','observer') if args.setup_only else ('control','stager','observer','api')
    images,pins=images_from(args.images,roles);write(args.evidence/'images-before-use.json',pins)
    try:
        if args.setup_only:
            produced_fixture(images,args.evidence)
            write(args.evidence/'setup-result.json',dict(scope='genuine setup only',authentication_claim=False,native_executed=False))
        else:
            authenticated_cases(images,args.evidence)
    finally:
        after={k:dict(path=str(v),sha256=sha(v)) for k,v in images.items()};write(args.evidence/'images-after-use.json',after)
        require(all(after[k]['sha256']==pins['images'][k]['sha256'] for k in images),'image drift during fixture use')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
