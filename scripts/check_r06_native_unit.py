#!/usr/bin/env python3
"""Owned-unit driver support with real API routing and product-written journals.

The auth setup is a non-native prerequisite check. It never qualifies the unit
or starts a client/model. Native admission is a separate owner-held operation.
"""
import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import uuid

import check_r06_unit_census as census
from check_r06_watch_unit_roundtrip import Fixture as WatcherMaterial
import stage_lifecycle_compatibility as installer
import stage_lifecycle_hooks as hook_installer
from disposable_host import DisposableHost, OwnedCluster, pg_bindir, require

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    return census.write(Path(path), value)


def within(root, path):
    path, root = Path(path), Path(root)
    require(path.is_absolute() and path.resolve().is_relative_to(root.resolve()),
            'selector escaped the owned fixture: ' + str(path))
    return path


def owned_command(argv, prefix, *, body='', env=None, expected=0, timeout=40):
    """Observe and join only this invocation; never signal a process group."""
    prefix = Path(prefix)
    write(prefix.with_suffix('.argv.json'), list(map(str, argv)))
    child = subprocess.Popen(list(map(str, argv)), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
    require(child.pid > 1, 'unsafe owned child PID')
    group = os.getpgid(child.pid)
    require(group > 1, 'unsafe owned child group')
    try:
        stdout, stderr = child.communicate(body, timeout=timeout)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate(timeout=5)
        raise
    prefix.with_suffix('.stdout').write_text(stdout)
    prefix.with_suffix('.stderr').write_text(stderr)
    write(prefix.with_suffix('.exit.json'), dict(exit=child.returncode, pid=child.pid,
        group=group, joined=True, group_signals=False))
    require(expected is None or child.returncode == expected,
            f'{prefix}: actual exit {child.returncode}: {stderr} {stdout}')
    return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)


def forward_api(path, argv):
    plan = json.loads(Path(path).read_text())
    request = sys.stdin.read()
    prefix = Path(plan['calls']) / str(uuid.uuid4())
    write(prefix.with_suffix('.ingress.json'), dict(argv=argv, input=request))
    require(argv and argv[0] in ('agent', 'complete', 'publish', 'ack', 'renew'),
            'only agent API and issued completion/reply client forms are routed')
    flags = {}
    for key in ('--socket', '--token-file'):
        positions = [index for index, value in enumerate(argv) if value == key]
        require(len(positions) == 1 and positions[0] + 1 < len(argv), 'missing/ambiguous owned selector')
        flags[key] = argv[positions[0] + 1]
    require(not any(value in ('--profile', '--url') for value in argv), 'alternate API profile/endpoint')
    require(not any(value.startswith(('--profile=', '--url=', '--socket=', '--token-file=')) for value in argv),
            'alternate or duplicate API selector form')
    require(flags.get('--socket') == plan['socket'] and flags.get('--token-file') == plan['token_file'],
            'API selectors differ from the owned issued profile')
    within(plan['root'], flags['--socket'])
    within(plan['root'], flags['--token-file'])
    require(digest(plan['binary']) == plan['sha256'], 'API image changed before use')
    # The existing CLI/API owns the operation contract and permissions. The
    # adapter preserves every returned refusal instead of shadowing that API.
    result = owned_command([plan['binary'], *argv], prefix, body=request, expected=None)
    operation = argv[0]
    if operation == 'agent':
        index = 1
        while index < len(argv) and argv[index].startswith('--'):
            index += 2
        require(index < len(argv), 'agent API operation is missing')
        operation = argv[index]
    write(prefix.with_suffix('.request.json'), dict(operation=operation, input=request,
        socket=flags['--socket'], token_file=flags['--token-file'], response_substitution=False))
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def checked_images(path):
    pins = json.loads(Path(path).read_text())
    require(pins['schema'] == 'cairn.r06-unit-images/1', 'explicit unit image pins required')
    images = {}
    for role in ('api', 'control', 'helper', 'stager'):
        pin = pins['images'][role]
        image = Path(pin['path'])
        require(image.is_absolute() and not image.is_symlink() and image.is_file()
                and image.stat().st_mode & 0o022 == 0 and digest(image) == pin['sha256'],
                'missing, unsafe or changed ' + role + ' image')
        images[role] = image
    return images, pins


def retained_bundle():
    source = ROOT / 'integrations/lifecycle/lifecycle_bundle.py'
    spec = importlib.util.spec_from_file_location('owned_unit_retained_bundle', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class OwnedUnit:
    def __init__(self, root, evidence, images, cluster):
        self.root, self.evidence, self.images = Path(root), Path(evidence), images
        self.root.mkdir(mode=0o700)
        self.sequence = 0
        self.state_reads = 0
        self.native = str(uuid.uuid4())
        self.binding = 'r06-complete-owned-unit'
        self.account, self.work = self.root / 'account', self.root / 'workspace'
        self.state_dir, self.memory_dir = self.root / 'state', self.root / 'memory-state'
        for path in (self.account, self.work, self.state_dir, self.memory_dir):
            path.mkdir(mode=0o700)
        self.host = DisposableHost.start(cluster, binary=images['api'], repo='fixture:r06-owned-unit',
            database='r06_owned_' + uuid.uuid4().hex, home=self.root / 'api-home',
            socket_path=self.root / 'api.sock', roles=('sender', 'worker'),
            label='r06_owned_unit', log=self.evidence / 'api.log')
        try:
            self.forwarder = self.root / 'api-forwarder'
            calls = self.evidence / 'api-calls'
            calls.mkdir(mode=0o700)
            self.transport = self.root / 'transport.json'
            write(self.transport, dict(root=str(self.root), binary=str(images['api']),
                sha256=digest(images['api']), socket=str(self.host.socket),
                token_file=str(self.host.token('worker')), calls=str(calls)))
            self.forwarder.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-B',
                str(Path(__file__).resolve()), '--forward-api', str(self.transport)]) + ' "$@"\n')
            self.forwarder.chmod(0o500)
            common = dict(harness='claude', cairn=str(self.forwarder), socket=str(self.host.socket),
                token_file=str(self.host.token('worker')), repo=self.host.repo, config_home=str(self.account))
            self.memory = dict(common, state_dir=str(self.memory_dir), recall_mode='agent_tools',
                               recall_observations=False, context_bytes=9500)
            self.coordination = dict(common, state_dir=str(self.state_dir), native_delivery=True,
                binding=self.binding, process_names=['helper', 'claude'], process_exe_dirs=[])
            self.go = self.stage_go()
        except BaseException:
            self.close()
            raise

    def stage_go(self):
        root = self.root / 'go-install'
        root.mkdir(mode=0o700)
        identity = str(uuid.uuid4())
        final = root / 'generations' / ('selection-' + identity)
        inputs = self.root / 'go-inputs'
        inputs.mkdir(mode=0o700)
        configs = []
        for name, value in (('memory', self.memory), ('coordination', self.coordination)):
            selected = dict(value, inbox_recall_binding=str(final / 'binding.json'))
            path = inputs / (name + '.json')
            write(path, selected)
            configs.append(dict(path=str(path), sha256=digest(path)))
        sources = {name: dict(path=str(ROOT / 'integrations/lifecycle' / name),
                   sha256=digest(ROOT / 'integrations/lifecycle' / name)) for name in WatcherMaterial.compatibility_members()}
        staged_sources = self.root / 'sources'
        staged_sources.mkdir(mode=0o700)
        for name, pin in sources.items():
            copied = staged_sources / name
            shutil.copyfile(pin['path'], copied)
            copied.chmod(0o600)
            require(digest(copied) == pin['sha256'], 'Source copy changed before installation')
            pin['path'] = str(copied)
        plan = dict(id=identity, enabled=True, source_revision='R06 owned-unit pinned fixture source',
            sources=sources, binary=dict(path=str(self.images['control']), sha256=digest(self.images['control'])),
            memory_config=configs[0], coordination_configs=configs[1:], aliases={},
            python=str(Path(sys.executable).resolve()), shell='/bin/sh')
        selected = installer.stage(str(self.images['stager']), root, plan)
        inspected = installer.inspect(str(self.images['stager']), selected)
        receipt = final / 'receipt.json'
        actual = json.loads(receipt.read_text())
        write(self.evidence / 'go-installation.json', dict(plan=plan, selected=selected, inspected=inspected))
        return dict(root=root, selection=selected, receipt=receipt, receipt_sha256=digest(receipt),
                    config=Path(actual['coordination_configs'][0]['path']), binary=Path(actual['binary']['path']))

    def stage_legacy(self):
        """Install the existing complete /2 Python material without repinning it as Go."""
        bundle = retained_bundle()
        root = self.root / 'python2-install'
        root.mkdir(mode=0o700)
        final = root / ('selection-' + str(uuid.uuid4()))
        inputs = self.root / 'python2-inputs'
        inputs.mkdir(mode=0o700)
        bundle.stage_runtime(ROOT / 'integrations/lifecycle', inputs)
        for name, value in (('memory', self.memory), ('coordination', self.coordination)):
            write(inputs / (name + '.json'), dict(value, inbox_recall_binding=str(final / 'binding.json')))
        descriptor = lambda name: dict(path=str(final / name), sha256=digest(inputs / name))
        manifest = dict(schema='cairn.inbox-recall-binding/2', enabled=False,
            memory_config=descriptor('memory.json'), coordination_configs=[descriptor('coordination.json')])
        for role, name in (('engine', 'memory.py'), ('bridge', 'inbox_recall.py')):
            manifest[role] = dict(descriptor(name), bundle=bundle.bundle_descriptor(inputs))
        write(inputs / 'binding.json', manifest)
        python = Path(sys.executable).resolve()
        retained = dict(memory=[str(python), '-I', '-B', str(final / 'memory.py'), '--config', str(final / 'memory.json')],
            coordination=[str(python), '-I', '-B', str(final / 'coordination.py'), 'hook', '--config', str(final / 'coordination.json')])
        write(inputs / 'retained-argv.json', retained)
        files = [dict(name=path.name, path=str(path), sha256=digest(path), executable=False)
                 for path in sorted(inputs.iterdir())]
        selection = installer.backend(str(self.images['stager']), ['generation', '--root', str(root)],
                                      dict(id=final.name.removeprefix('selection-'), files=files))
        inspected = installer.inspect(str(self.images['stager']), selection, legacy=True)
        write(self.evidence / 'python2-installation.json', dict(selection=selection, inspected=inspected,
            manifest=manifest, retained_argv=retained, actual_source_members=bundle.bundle_descriptor(inputs)))
        return dict(selection=selection, config=final / 'coordination.json', manifest=final / 'binding.json')

    def check_legacy_hook_producer(self):
        legacy = self.stage_legacy()
        settings = self.account / 'settings.json'
        write(settings, dict(hooks={}, permissions=dict(defaultMode='dontAsk')))
        plan = dict(id=str(uuid.uuid4()), selection=legacy['selection'], harness='claude',
            account_home=str(self.account), settings=dict(path=str(settings), sha256=digest(settings)),
            coordination_index=0, replace_commands=dict(memory=[], coordination=[]))
        write(self.evidence / 'python2-hook-plan.json', plan)
        try:
            result = hook_installer.definitions(str(self.images['stager']), plan)
        except (KeyError, ValueError, RuntimeError) as error:
            write(self.evidence / 'python2-hook-producer-gap.json', dict(operation='stage_lifecycle_hooks.definitions',
                selection=legacy['selection'], exception=type(error).__name__, message=str(error),
                legacy_material_inspected=True, native_client_executed=False, complete_unit_qualified=False))
            raise
        write(self.evidence / 'python2-hook-definitions.json', result)

    def hook(self, event, label):
        self.sequence += 1
        timeout = '2' if event['hook_event_name'] in ('SessionEnd', 'PostToolUse') else '15'
        args = ['coordination', '--receipt', str(self.go['receipt']), '--receipt-sha256',
            self.go['receipt_sha256'], '--role', 'coordination', '--entrypoint', 'coordination.py',
            '--config', str(self.go['config']), '--hook-timeout', timeout, '--launch-root',
            str(self.go['root'] / 'launch'), '--shell', '/bin/sh', '--', 'hook', '--config', str(self.go['config'])]
        env = dict(self.host.env, CLAUDE_CONFIG_DIR=str(self.account))
        result = owned_command([self.go['binary'], *args], self.evidence / label,
            body=json.dumps(event, ensure_ascii=False), env=env)
        write(self.evidence / (label + '.product.json'), dict(exit=result.returncode,
            stdout=result.stdout, stderr=result.stderr, input=event,
            program=str(self.go['binary']), sha256=digest(self.go['binary'])))
        return result

    @property
    def state_path(self):
        return self.state_dir / (hashlib.sha256((self.binding + '\0' + self.native).encode()).hexdigest() + '.json')

    def state(self):
        require(self.state_path.is_file(), 'actual callback produced no selected native state: ' + str(self.state_path))
        raw = self.state_path.read_bytes()
        state = json.loads(raw)
        self.state_reads += 1
        name = 'produced-state-' + str(self.sequence) + '-' + str(self.state_reads)
        (self.evidence / (name + '.raw.json')).write_bytes(raw)
        info = self.state_path.stat()
        write(self.evidence / (name + '.metadata.json'), dict(path=str(self.state_path),
            sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw), mode=oct(info.st_mode & 0o777),
            uid=info.st_uid, gid=info.st_gid, device=info.st_dev, inode=info.st_ino))
        require(state['registration']['native_session_id'] == self.native and state['agent']['native_session_id'] == self.native,
                'product state selected another native session')
        return state

    def close(self):
        if hasattr(self, 'host'):
            process = self.host.process
            pid = process.pid if process else None
            require(pid is None or pid > 1, 'unsafe API PID')
            code = self.host.close()
            write(self.evidence / 'api-cleanup.json', dict(pid=pid, exit=code, joined=True, group_signals=False))


def auth_setup(images_path, evidence, *, legacy_stage=False):
    images, pins = checked_images(images_path)
    write(evidence / 'images-before-use.json', pins)
    scratch = Path(tempfile.mkdtemp(prefix='r06unit-'))
    write(evidence / 'scratch.json', dict(path=str(scratch)))
    cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bindir())
    unit = None
    try:
        unit = OwnedUnit(scratch / 'unit', evidence, images, cluster)
        if legacy_stage:
            unit.check_legacy_hook_producer()
            return
        event = dict(hook_event_name='SessionStart', session_id=unit.native, cwd=str(unit.work), source='startup')
        unit.hook(event, 'registration')
        state = unit.state()
        agent = state['agent']
        ref = {key: agent[key] for key in ('agent_id', 'execution_id')}
        directory = unit.host.agent('worker', 'agent-directory', dict(agent_id=ref['agent_id']))
        require(len(directory['agents']) == 1 and directory['agents'][0]['execution_id'] == ref['execution_id']
                and directory['agents'][0]['native_session_id'] == unit.native,
                'actual registration/directory correspondence differs')
        write(evidence / 'registration-correspondence.json', dict(native=unit.native, session=ref,
            state_path=str(unit.state_path), authoritative=directory, native_client_executed=False))
        source = unit.host.agent('sender', 'create', dict(request_id=str(uuid.uuid4()), draft=dict(kind='note',
            body='Selected result requested by the owned R06 auth setup.',
            scope=dict(repo=unit.host.repo, task_id='*', run_id='*'), claim_type='self', sensitivity='shareable')))
        event = unit.host.agent('sender', 'event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
            ref=dict(record_id=source['record_id'], version=source['version']),
            destination=dict(type='agent', name=agent['inbox'])))
        delivery = unit.host.agent('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
        prompt = 'Handle the single owned fixture request at this ordinary owner boundary.'
        unit.hook(dict(hook_event_name='UserPromptSubmit', session_id=unit.native, cwd=str(unit.work),
                       prompt=prompt), 'claim')
        state = unit.state()
        attempt = state['inbox_attempt']
        require(attempt['session'] == ref and attempt['delivery']['delivery_id'] == delivery['delivery_id'],
                'actual claim is not the registration/native delivery')
        context_path = unit.state_dir / 'inbox' / (attempt['attempt_id'] + '.json')
        context = json.loads(context_path.read_text())
        require(context['agent_id'] == ref['agent_id'] and context['execution_id'] == ref['execution_id'],
                'issued context is for another session')
        owned_command(context['read'], evidence / 'issued-read', body=json.dumps(context['read_input']), env=unit.host.env)
        finished = owned_command(context['finish_reply'], evidence / 'issued-finish',
            body='Selected owned auth-setup result. Native complete-unit qualification remains held.\n', env=unit.host.env)
        committed = json.loads(finished.stdout)
        require(committed['ok'] and committed['data']['completion_committed'] and committed['data']['reply_committed'],
                'actual issued completion/reply did not commit both stages')
        journal_path = unit.state_dir / 'intents' / (attempt['attempt_id'] + '.json')
        journal_raw = journal_path.read_bytes()
        journal = json.loads(journal_raw)
        (evidence / 'actual-product-journal.raw.json').write_bytes(journal_raw)
        require(journal['schema'] == 'cairn.inbox-intent/1' and journal['session'] == ref
                and journal['delivery_id'] == delivery['delivery_id'] and journal['kind'] == 'request'
                and journal['completion']['status'] == 'committed' and journal['response']['status'] == 'committed'
                and journal['completion']['result'] == committed['data']['result'],
                'actual product-written completion/reply journal does not correspond')
        unit.state()
        unit.hook(dict(hook_event_name='SessionEnd', session_id=unit.native, cwd=str(unit.work), reason='other'), 'close')
        closed = unit.state()
        stale = unit.host.agent('worker', 'session-inbox-attempt-status',
            dict(session=ref, attempt_id=attempt['attempt_id']), expected='STALE_SESSION')
        write(evidence / 'closed-execution-refusal.json', stale)
        unit.hook(dict(hook_event_name='SessionStart', session_id=unit.native,
                       cwd=str(unit.work), source='resume'), 'reopen-same-native')
        final = unit.state()
        current = {key: final['agent'][key] for key in ('agent_id', 'execution_id')}
        require(current['agent_id'] == ref['agent_id'] and current['execution_id'] != ref['execution_id'],
                'same native identity did not return the actual current execution')
        actual = unit.host.agent('worker', 'session-inbox-attempt-status',
            dict(session=current, attempt_id=attempt['attempt_id']))
        write(evidence / 'actual-historical-attempt.json', actual)
        require(actual['attempt_id'] == attempt['attempt_id'] and actual['session'] == ref and actual.get('finished_at'),
                'actual same-session historical attempt is not closed')
        write(evidence / 'auth-setup-result.json', dict(native=unit.native, session=ref,
            attempt_id=attempt['attempt_id'], delivery_id=delivery['delivery_id'], status=actual,
            current_session=current, closed_state=closed, produced_state=final,
            native_client_executed=False, complete_unit_qualified=False,
            journals_written_by_product=True))
        print('Owned Go/API auth route: actual registration/claim/issued completion/reply/close PASS; native NOTRUN', flush=True)
    finally:
        try:
            if unit is not None:
                unit.close()
        finally:
            pidfile = scratch / 'pg/data/postmaster.pid'
            pid = int(pidfile.read_text().splitlines()[0]) if pidfile.exists() else None
            require(pid is None or pid > 1, 'unsafe owned PG PID')
            stopped = cluster.stop()
            write(evidence / 'pg-cleanup.json', dict(pid=pid, stopped=stopped, group_signals=False))
            require(stopped, 'owned PG did not stop')
            after = {role: dict(path=str(path), sha256=digest(path)) for role, path in images.items()}
            require(all(after[role]['sha256'] == pins['images'][role]['sha256'] for role in images),
                    'actual setup image changed during use')
            write(evidence / 'images-after-use.json', after)


def auth_owner(images_path, evidence, *, legacy_stage=False):
    """Keep one real observed helper ancestor throughout the setup session."""
    images, pins = checked_images(images_path)
    write(evidence / 'images-before-owner.json', pins)
    plan = evidence / 'owner.plan.json'
    result_path = evidence / 'owner.product.json'
    child_evidence = evidence / 'auth'
    program = Path(sys.executable).resolve()
    write(plan, dict(program=str(program), sha256=digest(program), input='', result=str(result_path),
        args=['-B', str(Path(__file__).resolve()), '--auth-child', *(['--legacy-stage'] if legacy_stage else []), '--images', str(images_path),
              '--evidence', str(child_evidence), '--archive-stdout']))
    env = dict(os.environ, CAIRN_R06_OWNED_UNIT_PLAN=str(plan))
    owned_command([images['helper'], '-test.run=^TestR06OwnedUnitFixtureProcess$', '-test.v'],
                  evidence / 'owner', env=env, timeout=420)
    product = json.loads(result_path.read_text())
    require(product['exit'] == 0, 'actual owned auth setup refused: ' + product['stderr'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--forward-api', type=Path)
    parser.add_argument('--auth-setup', action='store_true')
    parser.add_argument('--auth-child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--legacy-stage', action='store_true', help='check the existing retained /2 native hook producer without a client')
    parser.add_argument('--images', type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--archive-stdout', action='store_true')
    args, extra = parser.parse_known_args()
    if args.forward_api:
        raise SystemExit(forward_api(args.forward_api, extra))
    require((args.auth_setup != args.auth_child) and args.images and args.evidence and not extra,
            'explicit non-native auth setup required')
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        if args.auth_setup:
            auth_owner(args.images, args.evidence, legacy_stage=args.legacy_stage)
        else:
            auth_setup(args.images, args.evidence, legacy_stage=args.legacy_stage)
    finally:
        if args.archive_stdout:
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w:gz') as package:
                for path in sorted(args.evidence.rglob('*')):
                    if path.is_file() and not path.is_symlink():
                        package.add(path, arcname=str(path.relative_to(args.evidence)))
            print(json.dumps(dict(archive_base64=base64.b64encode(archive.getvalue()).decode())), flush=True)
