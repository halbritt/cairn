#!/usr/bin/env python3
"""Drive one declared installation against its real disposable API and store.

Preparation and auth setup never start native clients. The native roundtrip
requires Root's separate admission and final composed source/image pins.
"""
import argparse
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import selectors
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid

import check_r06_unit_census as census
from check_r06_watch_unit_roundtrip import Fixture as WatcherMaterial
from check_r06_watch_unit_roundtrip import copy_image
import stage_lifecycle_compatibility as installer
import stage_lifecycle_hooks as hook_installer
from disposable_host import DisposableHost, OwnedCluster, pg_bindir, require
from check_r04_native_claude import ClaudeSession, MessagesFixture
from check_r06_status_consumer import snapshot as drain_snapshot

ROOT = Path(__file__).resolve().parents[1]
UNIT_SOURCES = {
    'scripts/check_r06_native_unit.py', 'cmd/cairn-lifecycle/native_unit_fixture_test.go',
    'scripts/stage_lifecycle_hooks.py', 'scripts/stage_lifecycle_compatibility.py',
    'scripts/check_r06_native_hook_census.py',
    'internal/lifecycle/coordination/shared_unit_native_identity.go',
    'internal/lifecycle/coordination/shared_unit_drain_local.go',
    'internal/lifecycle/coordination/shared_unit_census_d2.go',
    'internal/lifecycle/coordination/shared_unit_census_files.go',
    'internal/lifecycle/coordination/shared_unit_census_hooks.go',
}


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
    timed_out = False
    try:
        stdout, stderr = child.communicate(body, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        child.terminate()
        try:
            stdout, stderr = child.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            stdout, stderr = child.communicate(timeout=5)
    prefix.with_suffix('.stdout').write_text(stdout)
    prefix.with_suffix('.stderr').write_text(stderr)
    write(prefix.with_suffix('.exit.json'), dict(exit=child.returncode, pid=child.pid,
        group=group, joined=True, group_signals=False, timed_out=timed_out))
    require(not timed_out, f'{prefix}: owned command deadline expired; streams and exit retained')
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
    pending = prefix.with_suffix('.request.pending')
    write(pending, dict(operation=operation, input=request,
        socket=flags['--socket'], token_file=flags['--token-file'], response_substitution=False))
    os.replace(pending, prefix.with_suffix('.request.json'))
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
    def __init__(self, root, evidence, images, cluster, *, enabled=True):
        self.root, self.evidence, self.images = Path(root), Path(evidence), images
        self.root.mkdir(mode=0o700)
        self.sequence = 0
        self.state_reads = 0
        self.enabled = enabled
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
        plan = dict(id=identity, enabled=self.enabled, source_revision='R06 owned-unit pinned fixture source',
            sources=sources, binary=dict(path=str(self.images['control']), sha256=digest(self.images['control'])),
            memory_config=configs[0], coordination_configs=configs[1:], aliases={},
            python=str(Path(sys.executable).resolve()), shell='/bin/sh')
        selected = installer.stage(str(self.images['stager']), root, plan)
        inspected = installer.inspect(str(self.images['stager']), selected)
        receipt = final / 'receipt.json'
        actual = json.loads(receipt.read_text())
        write(self.evidence / 'go-installation.json', dict(plan=plan, selected=selected, inspected=inspected))
        return dict(root=root, selection=selected, receipt=receipt, receipt_sha256=digest(receipt),
                    config=Path(actual['coordination_configs'][0]['path']), binary=Path(actual['binary']['path']),
                    manifest=final / 'binding.json', memory=Path(actual['memory_config']['path']))

    def stage_legacy(self, python=None):
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
        python = python or Path(sys.executable).resolve()
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
        return dict(root=root, selection=selection, config=final / 'coordination.json',
                    manifest=final / 'binding.json', memory=final / 'memory.json',
                    binary=python, entry=final / 'coordination.py')

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
    write(plan, dict(program=str(program), sha256=digest(program), input='', result=str(result_path), timeout_ms=420000,
        args=['-B', str(Path(__file__).resolve()), '--auth-child', *(['--legacy-stage'] if legacy_stage else []), '--images', str(images_path),
              '--evidence', str(child_evidence), '--archive-stdout']))
    env = dict(os.environ, CAIRN_R06_OWNED_UNIT_PLAN=str(plan))
    owned_command([images['helper'], '-test.run=^TestR06OwnedUnitFixtureProcess$', '-test.v'],
                  evidence / 'owner', env=env, timeout=420)
    product = json.loads(result_path.read_text())
    require(product['exit'] == 0, 'actual owned auth setup refused: ' + product['stderr'])


def selected_pin(path):
    return dict(Path=str(path), SHA256=digest(path))


def checked_admission(path, sha256, pins):
    """Pin Root's selected run inputs; the file does not confer authorization."""
    require(path and sha256 and digest(path) == sha256,
            'native/client/model invocation HELD: exact Root admission input required')
    admission = installer.decoded(Path(path).read_bytes())
    require(admission['schema'] == 'cairn.r06-native-unit-admission/1'
            and admission['purpose'] == 'one-complete-unit-native-run', 'wrong admitted operation')
    require(pins.get('source_tree') and admission['source_tree'] == pins['source_tree']
            and admission['images'] == pins['images'], 'admission does not name the composed images/tree')
    checked_unit_sources(admission)
    client = Path(admission['client']['path'])
    require(client.is_absolute() and client.name == 'claude' and client.is_file() and not client.is_symlink()
            and client.stat().st_mode & 0o022 == 0 and digest(client) == admission['client']['sha256'],
            'selected native image changed before use')
    require(admission['provider'] == 'local-scripted-messages', 'unsupported owned provider transport')
    return admission


def checked_unit_sources(pins):
    require(pins.get('source_tree') and UNIT_SOURCES <= pins.get('source_files', {}).keys(),
            'complete-unit composed predecessor/source pins are missing')
    for name, expected in pins['source_files'].items():
        require(not Path(name).is_absolute() and '..' not in Path(name).parts,
                'composed source name must be repository-relative')
        source = within(ROOT, ROOT / name)
        require(source.is_file() and digest(source) == expected, 'composed source changed: ' + name)


class NativeUnit(OwnedUnit):
    """One declared installation; all native state is written by its callbacks."""
    def __init__(self, root, evidence, images, cluster):
        self.native_clients, self.watchers, self.retained = [], [], []
        self.references, self.prompts = {}, []
        super().__init__(root, evidence, images, cluster, enabled=False)
        try:
            self.prepare()
        except BaseException:
            self.close()
            raise

    def prepare(self):
        self.native = None
        project = self.work / 'README.md'
        project.write_text('This nonempty workspace belongs to the declared disposable R06 native unit.\n')
        project.chmod(0o600)
        python = copy_image(Path(sys.executable).resolve(), self.root / 'python-image/python')
        self.legacy = self.stage_legacy(python=python)
        self.config_root = self.root / 'selected-configs'
        self.active = self.config_root / 'unit'
        self.active.mkdir(mode=0o700, parents=True)
        self.callbacks = self.root / 'callbacks'
        self.callbacks.mkdir(mode=0o700)
        self.current = self.legacy
        target = self.active / 'coordination.json'
        shutil.copyfile(self.legacy['config'], target)
        target.chmod(0o400)
        self.pins = {target.name: digest(target)}
        settings = self.account / 'settings.json'
        write(settings, dict(hooks={}, alwaysThinkingEnabled=False,
            permissions=dict(defaultMode='dontAsk'), fixture=dict(text='日本語 🙂', integer=900719925474099312345)))
        self.settings_pin = selected_pin(settings)
        self.stage_hooks(self.legacy, 'python2')
        self.stage_hooks(self.go, 'go3')
        self.declared_inventory()

    def stage_hooks(self, release, name):
        plan = dict(id=str(uuid.uuid4()), selection=release['selection'], harness='claude',
            account_home=str(self.account), settings=dict(path=self.settings_pin['Path'], sha256=self.settings_pin['SHA256']),
            coordination_index=0, replace_commands=dict(memory=[], coordination=[]))
        hooks_root = self.root / ('hooks-' + name)
        hooks_root.mkdir(mode=0o700)
        hooks = hook_installer.stage(str(self.images['stager']), hooks_root, plan)
        generation = installer.backend(str(self.images['stager']), ['inspect-generation',
            '--path', hooks['selection']['path'], '--sha256', hooks['selection']['sha256']])
        members = {member['name']: member for member in generation['files']}
        record_path = Path(members['hook-selection.json']['path'])
        require(digest(record_path) == members['hook-selection.json']['sha256'], 'hook producer changed')
        record = json.loads(record_path.read_bytes())
        require(record['lifecycle_selection'] == release['selection'], 'hook producer selected another lifecycle')
        require({row['role'] for row in record['rows']} == {'memory', 'coordination'}, 'hook role coverage differs')
        release.update(hooks=hooks, hook_record=record)
        write(self.evidence / (name + '-hooks.json'), dict(plan=plan, selected=hooks, producer=record))

    def declared_inventory(self):
        profiles = []
        for release in (self.legacy, self.go):
            manifest = json.loads(release['manifest'].read_bytes())
            require(manifest['enabled'] is False, 'native unit must begin with disabled bindings')
            for descriptor in [manifest['memory_config'], *manifest['coordination_configs']]:
                path = within(self.root, descriptor['path'])
                require(digest(path) == descriptor['sha256'], 'declared profile differs from its manifest')
                config = json.loads(path.read_bytes())
                require(config['socket'] == str(self.host.socket)
                        and config['token_file'] == str(self.host.token('worker'))
                        and config['cairn'] == str(self.forwarder) and config['repo'] == self.host.repo,
                        'declared profile selected a foreign API or principal')
                for name in ('state_dir', 'config_home', 'inbox_recall_binding'):
                    within(self.root, config[name])
                profiles.append(dict(path=str(path), sha256=descriptor['sha256'],
                                     state_dir=config['state_dir'], config_home=config['config_home']))
        require(digest(self.settings_pin['Path']) == self.settings_pin['SHA256'], 'original account settings changed')
        write(self.evidence / 'declared-unit.json', dict(root=str(self.root), account=str(self.account),
            workspace=str(self.work), config_dir=str(self.active), profiles=profiles,
            shared_coordination_state=str(self.state_dir), shared_memory_state=str(self.memory_dir),
            callbacks=str(self.callbacks), socket=str(self.host.socket),
            credential_selector=str(self.host.token('worker')), native_client_executed=False))

    def correspondence(self, label):
        state = self.state()
        agent = state['agent']
        current = {name: agent[name] for name in ('agent_id', 'execution_id')}
        directory = self.host.agent('worker', 'agent-directory', dict(agent_id=current['agent_id']))
        require(len(directory['agents']) == 1, 'native callback directory membership changed')
        actual = directory['agents'][0]
        require(actual['execution_id'] == current['execution_id'] and actual['native_session_id'] == self.native
                and actual['metadata']['workspace'] == str(self.work), 'native/current API reference disagrees')
        first = getattr(self, 'first_agent_id', current['agent_id'])
        require(current['agent_id'] == first, 'refresh created a different native agent')
        self.first_agent_id = first
        retained = state.get('inbox_attempt')
        historical = None
        if retained:
            historical = self.host.agent('worker', 'session-inbox-attempt-status',
                dict(session=current, attempt_id=retained['attempt_id']))
            require(historical['session'] == retained['session']
                    and historical['session']['agent_id'] == current['agent_id']
                    and historical.get('finished_at'), 'historical native ownership/terminal state disagrees')
        write(self.evidence / (label + '-api-correspondence.json'), dict(current=current,
            native=self.native, directory=actual, historical=historical))
        return current

    def finish_native_request(self):
        state = self.state()
        attempt = state['inbox_attempt']
        context_path = self.state_dir / 'inbox' / (attempt['attempt_id'] + '.json')
        context = json.loads(context_path.read_bytes())
        current = {name: state['agent'][name] for name in ('agent_id', 'execution_id')}
        require(attempt['session'] == current and context['agent_id'] == current['agent_id']
                and context['execution_id'] == current['execution_id'], 'issued native context selects another execution')
        owned_command(context['read'], self.evidence / 'native-issued-read',
                      body=json.dumps(context['read_input']), env=self.host.env)
        result = owned_command(context['finish_reply'], self.evidence / 'native-issued-finish',
            body='Selected complete-unit fixture result from the actual native execution.\n', env=self.host.env)
        committed = json.loads(result.stdout)
        require(committed['ok'] and committed['data']['completion_committed'] and committed['data']['reply_committed'],
                'native issued completion/reply did not commit')
        path = self.state_dir / 'intents' / (attempt['attempt_id'] + '.json')
        raw = path.read_bytes()
        journal = json.loads(raw)
        require(journal['session'] == current and journal['completion']['result'] == committed['data']['result']
                and journal['completion']['status'] == 'committed' and journal['response']['status'] == 'committed',
                'actual native product journal does not correspond')
        (self.evidence / 'native-product-journal.raw.json').write_bytes(raw)
        write(self.evidence / 'native-request-result.json', dict(attempt=attempt, committed=committed,
            context=selected_pin(context_path), journal=selected_pin(path)))

    def publish_request(self):
        state = self.state()
        source = self.host.agent('sender', 'create', dict(request_id=str(uuid.uuid4()), draft=dict(kind='note',
            body='Selected request for the same real native complete-unit conversation.',
            scope=dict(repo=self.host.repo, task_id='*', run_id='*'), claim_type='self', sensitivity='shareable')))
        published = self.host.agent('sender', 'event-publish', dict(request_id=str(uuid.uuid4()), kind='request',
            ref=dict(record_id=source['record_id'], version=source['version']),
            destination=dict(type='agent', name=state['agent']['inbox'])))
        write(self.evidence / 'native-request-published.json', dict(source=source, event=published))

    def catalog(self, readback):
        from check_r06_native_hook_census import layer_files
        session = self.state_path
        readback_pin = write(self.root / 'native-readback.json', readback)
        selected = self.active / 'coordination.json'
        hooks = self.current['hooks']
        observation = dict(Schema='cairn.watch-claude-hooks/1', Harness='claude', Layer='flagSettings',
            Account=str(self.account), Workspace=str(self.work), Config=selected_pin(selected),
            Session=selected_pin(session), Generation=dict(Path=hooks['selection']['path'], SHA256=hooks['selection']['sha256']),
            Settings=dict(Path=hooks['settings']['path'], SHA256=hooks['settings']['sha256']),
            Readback=readback_pin, LayerFiles=layer_files(self.account, self.work))
        callback_pins = [selected_pin(path) for path in sorted(self.callbacks.iterdir())]
        sessions = [selected_pin(path) for path in sorted(self.state_dir.glob('*.json'))]
        selection = dict(Directory=str(self.active), Pins=dict(self.pins))
        catalog = dict(Schema='cairn.watch-unit-catalog/1', Scope='disposable-fixture', Root=str(self.root),
            ConfigRoot=str(self.config_root), CallbackRoot=str(self.callbacks), Selections=[selection],
            Sessions=sessions, Callbacks=callback_pins, CredentialPaths=[str(self.host.token('worker'))],
            Bindings=[selected_pin(self.current['manifest'])], Clients=[selected_pin(self.forwarder)],
            HookSelections=[write(self.root / 'native-hooks.json', observation)])
        proposal = dict(Bindings=[selection], Sessions=[pin['Path'] for pin in sessions],
                        Callbacks=[pin['Path'] for pin in callback_pins])
        return catalog, proposal

    def control(self, operation, target, readback, label):
        catalog, proposal = self.catalog(readback)
        catalog_pin = write(self.root / ('catalog-' + label + '.json'), catalog)
        plan = dict(schema='cairn.unit-control/2', current=dict(directory=str(self.active), pins=dict(self.pins)),
            next=[dict(name='coordination.json', path=str(target['config']), sha256=digest(target['config']))],
            retained=str(self.config_root / ('retained-' + label)),
            census=dict(Catalog=catalog_pin, Proposal=proposal, Retained=copy.deepcopy(self.retained)))
        path = self.evidence / (label + '.plan.json')
        write(path, plan)
        before = census.snapshot(self.root)
        database_before = drain_snapshot(self.host.dsn, self.host.pg_bin, self.state_dir)
        result = owned_command([self.images['control'], 'unit-control', operation, '--plan', path,
            '--sha256', digest(path)], self.evidence / label, env=self.host.env, expected=None)
        after = census.snapshot(self.root)
        database_after = drain_snapshot(self.host.dsn, self.host.pg_bin, self.state_dir)
        write(self.evidence / (label + '-filesystem.json'), dict(before=before, after=after))
        write(self.evidence / (label + '-store.json'), dict(before=database_before, after=database_after))
        require(database_before == database_after, 'controller mutated authenticated API/native session state')
        protected = lambda rows: {name: facts for name, facts in rows.items()
                                  if not Path(name).is_relative_to(self.config_root)}
        require(protected(before) == protected(after), 'controller changed protected unit files')
        parsed = json.loads(result.stdout)
        write(self.evidence / (label + '-actual-control.json'), parsed)
        require(result.returncode == 0 and parsed['inspected'] and parsed['census']['FixtureReady'],
                'actual complete-unit operation refused; preserve exact result: ' + str(path))
        require(parsed['census']['ExternalCoverage'] == 'unknown'
                and parsed['census']['NativeRefresh'] == 'unobserved', 'controller claimed unmeasured native/external scope')
        if operation != 'inspect':
            require(parsed['exchange']['Published'], 'complete-unit publication is uncertain')
            self.retained.append(parsed['exchange']['Retained'])
            self.pins = dict(parsed['exchange']['Active']['Pins'])
            require({p.name for p in self.active.iterdir()} == set(self.pins) | {'.watch.lock'},
                    'published config-dir membership is incomplete')
            require({p.name: digest(p) for p in self.active.glob('*.json')} == self.pins,
                    'published config-dir readback differs from the actual returned pins')
            require(all(os.path.samefile(self.active / '.watch.lock', Path(copy['Directory']) / '.watch.lock')
                        for copy in self.retained), 'publication split the watcher lock identity')
        else:
            require(before == after, 'read-only controller inspection changed the unit')
        return parsed


    def watcher_argv(self):
        if self.current is self.legacy:
            return [self.legacy['binary'], '-I', '-B', self.legacy['entry'], 'watch',
                    '--config', self.legacy['config'], '--config-dir', self.active]
        return [self.go['binary'], 'coordination', '--receipt', self.go['receipt'],
            '--receipt-sha256', self.go['receipt_sha256'], '--role', 'coordination',
            '--entrypoint', 'coordination.py', '--config', self.go['config'], '--hook-timeout', '15',
            '--launch-root', self.go['root'] / 'launch', '--shell', '/bin/sh', '--', 'watch', '--config-dir', self.active]

    def start_watcher(self, label):
        require(all(child.poll() is not None for child, *_ in self.watchers), 'another unit watcher is running')
        require({path.name: digest(path) for path in self.active.glob('*.json')} == self.pins,
                'watcher config-dir membership/pins differ')
        argv = list(map(str, self.watcher_argv()))
        write(self.evidence / (label + '-before-use.json'), dict(argv=argv, executable=selected_pin(argv[0])))
        stdout = (self.evidence / (label + '.stdout')).open('w')
        stderr = (self.evidence / (label + '.stderr')).open('w')
        existing = set((self.evidence / 'api-calls').glob('*.request.json'))
        child = subprocess.Popen(argv, env=self.host.env, cwd=self.work, stdin=subprocess.DEVNULL,
                                 stdout=stdout, stderr=stderr, start_new_session=True)
        self.watchers.append((child, stdout, stderr, label))
        process = census.process_ref(child.pid)
        entry = self.legacy['entry'] if self.current is self.legacy else self.go['receipt']
        record = dict(Schema='cairn.fixture-callback-reference/1', Role='watcher',
            Bindings=[str(self.active / name) for name in sorted(self.pins)], Config=selected_pin(self.current['config']),
            Binding=selected_pin(self.current['manifest']), Process=process, Executable=selected_pin(argv[0]),
            EntryPoint=selected_pin(entry), Join=None)
        path = self.callbacks / (label + '.json')
        write(path, record)
        self.references[label] = (path, record)
        current = self.correspondence(label)
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            require(child.poll() is None, 'selected watcher exited before its actual API readback')
            for request_path in set((self.evidence / 'api-calls').glob('*.request.json')) - existing:
                request = json.loads(request_path.read_bytes())
                if request['operation'] == 'agent-heartbeat':
                    payload = json.loads(request['input'])
                    if all(payload.get(name) == actual for name, actual in current.items()):
                        observed = self.watcher_readback(request_path, current, child, argv)
                        write(self.evidence / (label + '-readback.json'), dict(process=process,
                            request=selected_pin(request_path), config_pins=self.pins, **observed))
                        return child
            time.sleep(.02)
        raise AssertionError('selected watcher did not heartbeat the actual native execution')

    def watcher_readback(self, request_path, current, child, argv):
        prefix = Path(str(request_path).removesuffix('.request.json'))
        response = json.loads(prefix.with_suffix('.stdout').read_bytes())
        exit_status = json.loads(prefix.with_suffix('.exit.json').read_bytes())
        require(exit_status['exit'] == 0 and response['ok']
                and all(response['data'][name] == actual for name, actual in current.items()),
                'actual selected watcher heartbeat was refused')
        actual_argv = Path(f'/proc/{child.pid}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0')
        require(actual_argv == argv and os.readlink(f'/proc/{child.pid}/exe') == argv[0],
                'actual watcher executable/argv differs from selection')
        lock = self.active / '.watch.lock'
        owners = [line for line in Path('/proc/locks').read_text().splitlines()
                  if len(line.split()) >= 6 and line.split()[4] == str(child.pid)
                  and line.split()[5].endswith(':' + str(lock.stat().st_ino))]
        require(len(owners) == 1 and 'FLOCK' in owners[0], 'selected watcher does not own the unit lock')
        return dict(actual_argv=actual_argv, lock_inode=lock.stat().st_ino, lock_owner=owners)

    def join_watcher(self, child, label):
        path, record = self.references[label]
        require(child.pid > 1 and census.process_ref(child.pid) == record['Process'], 'owned watcher identity changed')
        child.terminate()
        code = child.wait(timeout=12)
        require(code == 0 and not Path(f'/proc/{child.pid}').exists(), 'watcher did not stop and join cleanly')
        record['Join'] = write(self.root / 'joins' / (label + '.json'),
            dict(Schema='cairn.fixture-callback-join/1', Process=record['Process'],
                 Reaper=census.process_ref(os.getpid()), ExitCode=code))
        write(path, record)
        write(self.evidence / (label + '-joined.json'), dict(process=record['Process'], exit=code,
            joined=True, join=record['Join'], group_signals=False))

    def retire_watcher(self, label):
        path, record = self.references[label]
        require(record['Join'] and not Path(f'/proc/{record["Process"]["PID"]}').exists(), 'unjoined watcher cannot be retired')
        destination = self.root / 'joins' / (label + '-reference.json')
        path.rename(destination)
        write(self.evidence / (label + '-retired.json'), selected_pin(destination))

    def close(self):
        failures = []
        native_cleanup = []
        for client in getattr(self, 'native_clients', []):
            if client.process.poll() is None:
                try:
                    client.close()
                except (AssertionError, OSError, subprocess.SubprocessError) as error:
                    failures.append(repr(error))
            native_cleanup.append(dict(pid=client.process.pid, exit=client.process.poll(),
                                       joined=client.process.poll() is not None, group_signals=False))
        for child, stdout, stderr, label in getattr(self, 'watchers', []):
            try:
                if child.poll() is None:
                    require(child.pid > 1, 'unsafe owned watcher cleanup PID')
                    child.terminate()
                    child.wait(timeout=12)
                write(self.evidence / (label + '-cleanup.json'),
                    dict(pid=child.pid, exit=child.returncode, joined=True, group_signals=False))
            except (AssertionError, OSError, subprocess.SubprocessError) as error:
                failures.append(repr(error))
            finally:
                stdout.close()
                stderr.close()
        try:
            super().close()
        except (AssertionError, OSError, subprocess.SubprocessError) as error:
            failures.append(repr(error))
        finally:
            provider = getattr(self, 'provider', None)
            if provider is not None:
                try:
                    provider.close()
                except (AssertionError, OSError) as error:
                    failures.append(repr(error))
            write(self.evidence / 'native-cleanup.json', dict(processes=native_cleanup, failures=failures,
                provider_stopped=provider is None or not provider.thread.is_alive(), group_signals=False))
            if failures:
                write(self.evidence / 'cleanup-failures.json', failures)
        require(not failures, 'owned native unit cleanup failed: ' + repr(failures))


class OwnedMessages(MessagesFixture):
    def __init__(self):
        self.before_response = None
        super().__init__()

    def response(self, request):
        callback, self.before_response = self.before_response, None
        if callback:
            callback()
        return super().response(request)


class OwnedNativeSession(ClaudeSession):
    """Reuse the supported stream transport, preserving one HOME and account."""
    def __init__(self, unit, admission, provider, label):
        self.identity = unit.native or str(uuid.uuid4())
        self.output, self.root = unit.evidence, unit.root
        self.trace = unit.evidence / (label + '.exec')
        self.diagnostics = unit.evidence / (label + '.stderr')
        self.stderr = self.diagnostics.open('wb')
        self.log = (unit.evidence / (label + '.events.jsonl')).open('wb')
        self.events, self.buffer = [], bytearray()
        settings = unit.current['hooks']['settings']
        client = admission['client']
        require(digest(client['path']) == client['sha256'], 'native image changed immediately before use')
        require(digest(settings['path']) == settings['sha256'], 'native selected settings changed before use')
        env = dict(unit.host.env, CLAUDE_CONFIG_DIR=str(unit.account),
            CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1', ENABLE_TOOL_SEARCH='false',
            ANTHROPIC_API_KEY='r06-local-fixture-unused', ANTHROPIC_BASE_URL=provider.url,
            CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS='150000')
        argv = ['strace', '-f', '-s', '16384', '-e', 'trace=execve', '-o', str(self.trace), client['path'],
            '--print', '--verbose', '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--setting-sources', '', '--settings', settings['path'], '--strict-mcp-config', '--mcp-config',
            '{"mcpServers":{}}', '--tools', '', '--permission-mode', 'dontAsk', '--max-turns', '3', '--model', 'sonnet',
            '--resume' if unit.native else '--session-id', self.identity]
        write(unit.evidence / (label + '-before-use.json'), dict(client=client, settings=settings, argv=argv,
            home=env['HOME'], account=env['CLAUDE_CONFIG_DIR'], workspace=str(unit.work), provider=provider.url))
        self.process = subprocess.Popen(argv, cwd=unit.work, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.stderr, start_new_session=True)
        require(self.process.pid > 1, 'unsafe owned native supervisor PID')
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            self.control('initialize')
        except BaseException:
            self.close()
            raise


def observe_native(unit, client, provider, label):
    from check_r06_native_hook_census import native_observation
    selection = (unit.current['hooks'], None, unit.current['hook_record'])
    view = native_observation(client, selection, unit.evidence, label)
    unit.native = client.identity
    state = unit.state()
    require(state['agent']['native_session_id'] == view['NativeSession'] == unit.native,
            'real client, callback state and native readback identity disagree')
    prompt = 'R06 selected hook census ' + label
    actual_history = json.dumps(provider.requests[-1]['messages'], ensure_ascii=False)
    require(all(prior in actual_history for prior in unit.prompts), 'same native conversation lost its prior history')
    unit.prompts.append(prompt)
    unit.correspondence(label)
    write(unit.evidence / (label + '-history-correspondence.json'), dict(native=unit.native,
        prior_prompts=unit.prompts[:-1], current_prompt=prompt, observed_messages_sha256=hashlib.sha256(actual_history.encode()).hexdigest()))
    return view


def native_roundtrip(unit, admission):
    provider = unit.provider = OwnedMessages()
    original_home = os.environ['HOME']
    try:
        client = OwnedNativeSession(unit, admission, provider, 'python2-native')
        unit.native_clients.append(client)
        observe_native(unit, client, provider, 'python2-start')
        unit.publish_request()
        provider.before_response = unit.finish_native_request
        view = observe_native(unit, client, provider, 'python2-actual-request')
        for operation, target, label in (('switch', unit.go, 'to-go3'), ('reverse', unit.legacy, 'to-python2')):
            watcher = unit.start_watcher(label + '-old-watcher')
            unit.join_watcher(watcher, label + '-old-watcher')
            view = observe_native(unit, client, provider, label + '-drained')
            unit.control('inspect', target, view, label + '-inspect')
            changed = unit.control(operation, target, view, label)
            require(changed['final_census']['FixtureReady'], 'final publication census is not complete')
            unit.retire_watcher(label + '-old-watcher')
            client.close()
            unit.current = target
            client = OwnedNativeSession(unit, admission, provider, label + '-resume')
            unit.native_clients.append(client)
            view = observe_native(unit, client, provider, label + '-refreshed')
            require(all(previous.process.poll() is not None for previous in unit.native_clients[:-1]),
                    'more than one native executor remains')
        watcher = unit.start_watcher('restored-python2-watcher')
        unit.join_watcher(watcher, 'restored-python2-watcher')
        view = observe_native(unit, client, provider, 'restored-python2-drained')
        final = unit.control('inspect', unit.go, view, 'restored-final-inspect')
        require(os.environ['HOME'] == original_home and not provider.failures, 'native run changed HOME or provider failed')
        write(unit.evidence / 'native-result.json', dict(schema='cairn.r06-native-unit-result/1',
            native=unit.native, account=str(unit.account), workspace=str(unit.work), same_provider=provider.url,
            current=unit.correspondence('final'), final_control=final, native_client_executed=True,
            model_account_invoked=False, production_changed=False,
            limits=['Finite declared disposable unit only.', 'Native settings refresh is orderly join and same-history resume.',
                    'Per-fact reads and rename are not atomic; legacy writer and hard-kill limits remain.']))
    except BaseException as error:
        write(unit.evidence / 'native-failure.json', dict(exception=type(error).__name__, message=str(error),
            native=unit.native, native_processes=[dict(pid=client.process.pid, exit=client.process.poll())
                                                for client in unit.native_clients]))
        raise


def run_unit(images_path, evidence, admission=None):
    images, pins = checked_images(images_path)
    checked_unit_sources(admission or pins)
    write(evidence / 'images-before-use.json', pins)
    scratch = Path(tempfile.mkdtemp(prefix='r06-native-unit-'))
    write(evidence / 'scratch.json', dict(path=str(scratch)))
    cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bindir())
    unit = None
    previous_mask = os.umask(0o077)
    try:
        unit = NativeUnit(scratch / 'unit', evidence, images, cluster)
        if admission is not None:
            native_roundtrip(unit, admission)
        else:
            write(evidence / 'prepared-result.json', dict(schema='cairn.r06-unit-preparation/1',
                profiles_staged=True, native_client_executed=False, complete_unit_qualified=False,
                images=pins, inventory=selected_pin(evidence / 'declared-unit.json')))
    finally:
        try:
            if unit is not None:
                unit.close()
        finally:
            pidfile = scratch / 'pg/data/postmaster.pid'
            pid = int(pidfile.read_text().splitlines()[0]) if pidfile.exists() else None
            require(pid is None or pid > 1, 'unsafe owned PostgreSQL PID')
            stopped = cluster.stop()
            write(evidence / 'pg-cleanup.json', dict(pid=pid, stopped=stopped, group_signals=False))
            require(stopped, 'owned PostgreSQL did not stop')
            after = {role: dict(path=str(path), sha256=digest(path)) for role, path in images.items()}
            write(evidence / 'images-after-use.json', after)
            require(all(after[role]['sha256'] == pins['images'][role]['sha256'] for role in images),
                    'complete-unit image changed during use')
            if admission is not None:
                require(digest(admission['client']['path']) == admission['client']['sha256'], 'native image changed during use')
            os.umask(previous_mask)


def unit_owner(images_path, evidence, *, admission_path=None, admission_sha256=None, prepare=False):
    images, pins = checked_images(images_path)
    if not prepare:
        checked_admission(admission_path, admission_sha256, pins)
    else:
        checked_unit_sources(pins)
    write(evidence / 'images-before-owner.json', pins)
    program = Path(sys.executable).resolve()
    plan, result = evidence / 'owner.plan.json', evidence / 'owner.product.json'
    child = ['--unit-child'] if prepare else ['--native-child', '--admission', str(admission_path),
                                             '--admission-sha256', admission_sha256]
    write(plan, dict(program=str(program), sha256=digest(program), input='', result=str(result), timeout_ms=540000,
        args=['-B', str(Path(__file__).resolve()), *child, '--images', str(images_path),
              '--evidence', str(evidence / 'unit'), '--archive-stdout']))
    env = dict(os.environ, CAIRN_R06_OWNED_UNIT_PLAN=str(plan))
    owned_command([images['helper'], '-test.run=^TestR06OwnedUnitFixtureProcess$', '-test.v'],
                  evidence / 'owner', env=env, timeout=560)
    observed = json.loads(result.read_bytes())
    require(not observed['timed_out'] and observed['exit'] == 0,
            'actual unit driver refused; exact helper streams retained: ' + str(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--forward-api', type=Path)
    parser.add_argument('--auth-setup', action='store_true')
    parser.add_argument('--auth-child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--legacy-stage', action='store_true', help='check the existing retained /2 native hook producer without a client')
    parser.add_argument('--prepare-unit', action='store_true', help='stage both complete declared implementations without any native client')
    parser.add_argument('--unit-child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--native-unit', action='store_true', help='held complete-unit run; requires exact Root admission and composed inputs')
    parser.add_argument('--native-child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--admission', type=Path)
    parser.add_argument('--admission-sha256')
    parser.add_argument('--images', type=Path)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--archive-stdout', action='store_true')
    args, extra = parser.parse_known_args()
    if args.forward_api:
        raise SystemExit(forward_api(args.forward_api, extra))
    modes = [args.auth_setup, args.auth_child, args.prepare_unit, args.unit_child, args.native_unit, args.native_child]
    require(sum(modes) == 1 and args.images and args.evidence and not extra, 'select exactly one explicit owned-unit operation')
    require(not args.legacy_stage or args.auth_setup or args.auth_child, '--legacy-stage belongs to the non-native auth setup')
    if args.native_unit or args.native_child:
        _, image_pins = checked_images(args.images)
        checked_admission(args.admission, args.admission_sha256, image_pins)
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        if args.auth_setup:
            auth_owner(args.images, args.evidence, legacy_stage=args.legacy_stage)
        elif args.auth_child:
            auth_setup(args.images, args.evidence, legacy_stage=args.legacy_stage)
        elif args.prepare_unit or args.native_unit:
            unit_owner(args.images, args.evidence, admission_path=args.admission,
                       admission_sha256=args.admission_sha256, prepare=args.prepare_unit)
        else:
            admission = checked_admission(args.admission, args.admission_sha256, image_pins) if args.native_child else None
            run_unit(args.images, args.evidence, admission)
    finally:
        if args.archive_stdout:
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode='w:gz') as package:
                for path in sorted(args.evidence.rglob('*')):
                    if path.is_file() and not path.is_symlink():
                        package.add(path, arcname=str(path.relative_to(args.evidence)))
            print(json.dumps(dict(archive_base64=base64.b64encode(archive.getvalue()).decode())), flush=True)
