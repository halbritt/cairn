#!/usr/bin/env python3
"""Disposable real Python/2 -> Go/3 -> Python watcher and unit-control proof.

Run through mise host:exec. Native clients, models and installed units are not
used. The fixture owner, rather than the inspector, stops and joins processes.
"""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import check_r06_census_control as composition
import check_r06_unit_census as census
import check_r06_unit_control as control
import stage_lifecycle_compatibility as installer
from disposable_host import OwnedCluster, pg_bindir, require

ROOT = Path(__file__).resolve().parents[1]
CASES = ('roundtrip', 'running-callback', 'delayed-callback', 'unjoined-callback',
         'omitted-binding', 'changed-config', 'changed-runtime', 'incomplete-reverse', 'final-claim')


def copy_image(source, target):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(0o500)
    require(census.sha(source) == census.sha(target), 'copied executable changed')
    return target


def forward(path, argv):
    """Use the existing real-API read/claim injector; record watcher children."""
    if argv[-1] in control.READS:
        plan = json.loads(path.read_text())
        counts_path = Path(plan['counter'])
        counts = json.loads(counts_path.read_text()) if counts_path.exists() else {}
        # The complete two-session unit has three initial wake reads: one
        # principal scope plus both session scopes. Inject before read four.
        actor = plan.get('roundtrip_claim')
        if actor and argv[-1] == 'wake-attempts' and counts.get('wake-attempts', 0) == 3:
            census.write(path.with_name('actor-before.json'), control.snapshot(actor['dsn'], actor['pg_bin'], Path(actor['state_dir'])))
            admitted = control.run_envelope(actor['argv'], actor['body'], expected='OK', timeout=10)
            census.write(path.with_name('actor-result.json'), admitted)
            census.write(path.with_name('actor-after.json'), control.snapshot(actor['dsn'], actor['pg_bin'], Path(actor['state_dir'])))
        return control.forward_api(path, argv)
    require(argv[-1] == 'agent-heartbeat', 'watcher attempted an unexpected API operation')
    plan = json.loads(path.read_text())
    payload = sys.stdin.read()
    child = subprocess.Popen([plan['binary'], *argv], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    identity = census.process_ref(child.pid)
    try:
        stdout, stderr = child.communicate(payload, timeout=20)
    except subprocess.TimeoutExpired:
        require(child.pid > 1, 'unsafe API child PID')
        child.kill()
        child.communicate()
        raise
    row = dict(operation=argv[-1], input=json.loads(payload), forwarder=census.process_ref(os.getpid()),
               child=identity, exit=child.returncode, joined=True, stdout=stdout, stderr=stderr)
    with path.with_name('watcher-api.jsonl').open('a') as stream:
        stream.write(json.dumps(row) + '\n')
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    return child.returncode


class Fixture(composition.Fixture):
    def __init__(self, cluster, scratch, evidence, images, name):
        super().__init__(cluster, scratch, evidence, images, name)
        self.owned = []
        self.references = {}
        self.retired = []
        try:
            self.configure_transport()
            self.python = copy_image(Path(sys.executable).resolve(), self.universe / 'python-image/python')
            self.helper = copy_image(images['helper'], self.universe / 'callback-image/callback')
            self.templates = [json.loads((Path(self.selection['Directory']) / name).read_text())
                              for name in sorted(self.selection['Pins'])]
            for config in self.templates:
                config['idle_wakeup'] = False
                config['native_delivery'] = False
            self.memory = json.loads(Path(self.memories['a']).read_text())
            self.legacy = self.stage_legacy(images['stager'])
            self.go = self.stage_go(images['stager'])
            self.select_catalog(self.legacy, publish=True)
            self.next = self.go['next']
            owner = census.process_ref(os.getpid())
            for selected in self.catalog['Sessions']:
                if selected['Path'] in self.proposal['Sessions']:
                    state = json.loads(Path(selected['Path']).read_text())
                    state.update(process=dict(pid=owner['PID'], start=int(owner['Start']), boot=owner['Boot'],
                                              parent=owner['Parent']),
                                 workspace=state['agent']['metadata']['workspace'],
                                 roundtrip_opaque={'preserve': [1, 'custom', False]})
                    census.write(Path(selected['Path']), state)
            self.repin_sessions()
        except BaseException:
            self.close()
            raise

    def configure_transport(self):
        census.write(self.transport, dict(binary=str(self.images['api']),
            counter=str(self.evidence / 'read-counts.json'), calls=str(self.evidence / 'read-calls.jsonl')))
        wrapper = Path(self.catalog['Clients'][0]['Path'])
        wrapper.chmod(0o700)
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-B', str(Path(__file__).resolve()),
                           '--forward-api', str(self.transport)]) + ' "$@"\n')
        wrapper.chmod(0o500)
        self.catalog['Clients'][0]['SHA256'] = census.sha(wrapper)

    def profiles(self, final, names):
        binding = str(final / 'binding.json')
        memory = dict(self.memory, inbox_recall_binding=binding)
        configs = [dict(config, inbox_recall_binding=binding) for config in self.templates]
        inputs = self.universe / ('inputs-' + final.name)
        inputs.mkdir(mode=0o700)
        census.write(inputs / 'memory.json', memory)
        for name, config in zip(names, configs, strict=True):
            census.write(inputs / name, config)
        return inputs

    def stage_legacy(self, stager):
        spec = importlib.util.spec_from_file_location('roundtrip_bundle', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
        bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bundle)
        releases = self.universe / 'legacy-releases'
        releases.mkdir(mode=0o700)
        identity = control.uid()
        final = releases / ('selection-' + identity)
        names = sorted(self.selection['Pins'])
        inputs = self.profiles(final, names)
        bundle.stage_runtime(ROOT / 'integrations/lifecycle', inputs)
        descriptor = lambda name: dict(path=str(final / name), sha256=census.sha(inputs / name))
        manifest = dict(schema='cairn.inbox-recall-binding/2', enabled=False,
            memory_config=descriptor('memory.json'), coordination_configs=[descriptor(name) for name in names])
        for role, name in (('engine', 'memory.py'), ('bridge', 'inbox_recall.py')):
            manifest[role] = dict(descriptor(name), bundle=bundle.bundle_descriptor(inputs))
        census.write(inputs / 'binding.json', manifest)
        argv = [str(self.python), '-I', '-B', str(final / 'coordination.py'), 'watch',
                '--config', str(final / names[0]), '--config-dir', self.selection['Directory']]
        census.write(inputs / 'retained-argv.json', dict(watcher=argv))
        files = [dict(name=p.name, path=str(p), sha256=census.sha(p), executable=False)
                 for p in sorted(inputs.iterdir())]
        result = installer.backend(str(stager), ['generation', '--root', str(releases)], dict(id=identity, files=files))
        checked = installer.backend(str(stager), ['inspect-generation', '--path', result['path'], '--sha256', result['sha256']])
        selected = dict(schema=manifest['schema'],
            manifest=str(final / 'binding.json'), next=[dict(name=name, **descriptor(name)) for name in names],
            argv=argv, executable=str(self.python), entry=str(final / 'coordination.py'), config=str(final / names[0]),
            generation=result, inspected=checked)
        census.write(self.evidence / 'legacy-installed.json', selected)
        return selected

    def stage_go(self, stager):
        install_root = self.universe / 'go-release'
        install_root.mkdir(mode=0o700)
        identity = control.uid()
        final = install_root / 'generations' / ('selection-' + identity)
        names = sorted(self.selection['Pins'])
        inputs = self.profiles(final, names)
        desc = lambda p: dict(path=str(p), sha256=census.sha(p))
        plan = dict(id=identity, enabled=False, source_revision='2520c0dc+roundtrip-fixture',
            sources={name: desc(ROOT / 'integrations/lifecycle' / name) for name in self.compatibility_members()},
            binary=desc(self.images['control']), memory_config=desc(inputs / 'memory.json'),
            coordination_configs=[desc(inputs / name) for name in names], aliases={},
            python=str(Path(sys.executable).resolve()), shell=str(Path('/bin/sh').resolve()))
        selection = installer.stage(str(stager), install_root, plan)
        inspected = installer.inspect(str(stager), selection)
        receipt_path = final / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        config = receipt['coordination_configs'][0]['path']
        argv = [receipt['binary']['path'], 'coordination', '--receipt', str(receipt_path),
                '--receipt-sha256', census.sha(receipt_path), '--role', 'coordination', '--entrypoint', 'coordination.py',
                '--config', config, '--hook-timeout', '15', '--launch-root', str(install_root / 'launch'),
                '--shell', str(Path('/bin/sh').resolve()), '--', 'watch', '--config-dir', self.selection['Directory']]
        selected = dict(schema='cairn.inbox-recall-binding/3', manifest=str(final / 'binding.json'),
            next=[dict(name=name, **pin) for name, pin in zip(names, receipt['coordination_configs'], strict=True)],
            argv=argv, executable=receipt['binary']['path'], entry=str(receipt_path), config=config,
            generation=selection, inspected=inspected)
        census.write(self.evidence / 'go-installed.json', selected)
        return selected

    @staticmethod
    def compatibility_members():
        spec = importlib.util.spec_from_file_location('roundtrip_compatibility', ROOT / 'integrations/lifecycle/lifecycle_compatibility.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.MEMBERS

    def select_catalog(self, release, *, publish=False):
        if publish:
            for member in release['next']:
                path = Path(self.selection['Directory']) / member['name']
                path.chmod(0o600)
                path.write_bytes(Path(member['path']).read_bytes())
                path.chmod(0o400)
                self.selection['Pins'][member['name']] = census.sha(path)
        self.catalog['Bindings'][0] = dict(Path=release['manifest'], SHA256=census.sha(release['manifest']))
        self.proposal['Bindings'] = [copy.deepcopy(self.selection)]

    def repin_sessions(self):
        for pin in self.catalog['Sessions']:
            pin['SHA256'] = census.sha(pin['Path'])

    def add_reference(self, label, process, release, role):
        record = dict(Schema='cairn.fixture-callback-reference/1', Role=role,
            Bindings=[str(Path(self.selection['Directory']) / name) for name in sorted(self.selection['Pins'])],
            Config=dict(Path=release['config'], SHA256=census.sha(release['config'])),
            Binding=dict(Path=release['manifest'], SHA256=census.sha(release['manifest'])),
            Process=census.process_ref(process.pid), Executable=dict(Path=release['executable'], SHA256=census.sha(release['executable'])),
            EntryPoint=dict(Path=release['entry'], SHA256=census.sha(release['entry'])), Join=None)
        path = Path(self.catalog['CallbackRoot']) / (label + '.json')
        self.references[label] = (path, record)
        self.catalog['Callbacks'].append(census.write(path, record))
        self.proposal['Callbacks'].append(str(path))
        return record

    def spawn(self, argv, label, *, env=None, stdin=subprocess.DEVNULL):
        census.write(self.evidence / (label + '.before-use.json'),
                     dict(argv=argv, executable=dict(path=argv[0], sha256=census.sha(argv[0]))))
        stdout = (self.evidence / (label + '.stdout')).open('w')
        stderr = (self.evidence / (label + '.stderr')).open('w')
        child = subprocess.Popen(argv, cwd=ROOT, env=env or self.host.env, stdin=stdin,
                                 stdout=stdout, stderr=stderr, start_new_session=True)
        self.owned.append((child, stdout, stderr))
        ref = census.process_ref(child.pid)
        require(ref['PGID'] == child.pid, 'owned process group must be its new session')
        census.write(self.evidence / (label + '.launch.json'), dict(argv=argv, process=ref))
        return child

    def watch(self, release, label):
        require(all(not Path(f'/proc/{record["Process"]["PID"]}').exists()
                    for _, record in self.references.values() if record['Role'] == 'watcher'),
                'previous unit watcher has not been joined')
        expected_pins = {member['name']: member['sha256'] for member in release['next']}
        active_pins = {p.name: census.sha(p) for p in Path(self.selection['Directory']).glob('*.json')}
        require(active_pins == self.selection['Pins'] == expected_pins, 'watcher selected an incomplete or wrong config set')
        for member in release['next']:
            active = Path(self.selection['Directory']) / member['name']
            require(active.read_bytes() == Path(member['path']).read_bytes(), 'active copy differs from its immutable profile')
        protected = self.watcher_state()
        log = self.transport.with_name('watcher-api.jsonl')
        offset = len(log.read_text().splitlines()) if log.exists() else 0
        child = self.spawn(release['argv'], label)
        record = self.add_reference(label, child, release, 'watcher')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            require(child.poll() is None, f'{label}: selected watcher exited before readback')
            rows = [json.loads(line) for line in log.read_text().splitlines()[offset:]] if log.exists() else []
            if len(rows) >= 4:
                last_agent = {json.loads(row['stdout'])['data']['agent_id']: json.loads(row['stdout'])['data'] for row in rows}
                if all(json.loads(Path(path).read_text())['agent'] ==
                       last_agent.get(json.loads(Path(path).read_text())['agent']['agent_id']) for path in self.proposal['Sessions']):
                    break
            time.sleep(.02)
        require(len(rows) == 4 and all(row['exit'] == 0 and row['joined'] for row in rows),
                f'{label}: two configurations did not each heartbeat both real sessions')
        expected_sessions = {tuple(json.loads(Path(path).read_text())['agent'][key] for key in ('agent_id', 'execution_id'))
                             for path in self.proposal['Sessions']}
        observed_sessions = [tuple(row['input'][key] for key in ('agent_id', 'execution_id')) for row in rows]
        require(set(observed_sessions) == expected_sessions and all(observed_sessions.count(ref) == 2 for ref in expected_sessions),
                'watcher did not heartbeat both exact retained session/execution identities')
        actual = dict(executable=os.readlink(f'/proc/{child.pid}/exe'),
                      argv=Path(f'/proc/{child.pid}/cmdline').read_bytes().rstrip(b'\0').decode().split('\0'),
                      process=census.process_ref(child.pid))
        require(actual == dict(executable=release['executable'], argv=release['argv'], process=record['Process']),
                'running watcher image/argv/start identity differs from selection')
        lock = Path(self.selection['Directory']) / '.watch.lock'
        owners = [line for line in Path('/proc/locks').read_text().splitlines()
                  if len(line.split()) >= 6 and line.split()[4] == str(child.pid)
                  and line.split()[5].endswith(':' + str(lock.stat().st_ino))]
        require(len(owners) == 1 and 'FLOCK' in owners[0], 'selected watcher does not own the unit lock')
        self.repin_sessions()
        census.write(self.evidence / (label + '.readback.json'), dict(**actual, lock_inode=lock.stat().st_ino,
                     lock_owner=owners, pins=active_pins, api_calls=rows, protected_before=protected))
        return child

    def watcher_state(self):
        states = {}
        for path in self.proposal['Sessions']:
            state = json.loads(Path(path).read_text())
            for key in ('last_seen', 'expires_at'):
                state['agent'].pop(key, None)
            states[path] = state
        token = self.host.token('worker')
        return dict(states=states, ledger=census.snapshot(self.universe / 'memory-a'),
                    credentials=dict(path=str(token), sha256=census.sha(token), mode=token.stat().st_mode),
                    lock_inode=(Path(self.selection['Directory']) / '.watch.lock').stat().st_ino)

    def join_owned(self, child, label, *, watcher=False, receipt=True):
        path, record = self.references[label]
        require(child.pid > 1 and census.process_ref(child.pid) == record['Process'], 'owned process identity changed before stop')
        if watcher:
            child.send_signal(signal.SIGTERM)
        else:
            child.stdin.close()
        code = child.wait(timeout=8)
        require(code == 0, f'{label}: owner stop/join failed: {code}')
        require(not Path(f'/proc/{child.pid}').exists(), 'joined process remains')
        join = dict(Schema='cairn.fixture-callback-join/1', Process=record['Process'],
                    Reaper=census.process_ref(os.getpid()), ExitCode=code)
        join_pin = census.write(self.universe / 'joins' / (label + '.json'), join)
        census.write(self.evidence / (label + '.join.json'), dict(**join, joined=True, published_receipt=receipt))
        if receipt:
            record['Join'] = join_pin
            updated = census.write(path, record)
            self.catalog['Callbacks'] = [updated if pin['Path'] == str(path) else pin for pin in self.catalog['Callbacks']]
        self.repin_sessions()
        if watcher:
            readback = json.loads((self.evidence / (label + '.readback.json')).read_text())
            after = self.watcher_state()
            census.write(self.evidence / (label + '.state-after.json'), after)
            require(after == readback['protected_before'], 'watcher changed retained state, journal selectors, ledger, credentials or lock')
            require(not (self.evidence / (label + '.stdout')).read_text() and
                    not (self.evidence / (label + '.stderr')).read_text(), 'watcher returned diagnostics instead of a clean cycle')
            for row in readback['api_calls']:
                for ref in (row['forwarder'], row['child']):
                    require(not Path(f'/proc/{ref["PID"]}').exists(), 'watcher left a known API child unjoined')

    def retire_reference(self, label):
        path, record = self.references[label]
        require(record['Join'] and not Path(f'/proc/{record["Process"]["PID"]}').exists(), 'only exact joined references can be archived')
        archive = self.universe / 'joins' / (label + '-reference.json')
        path.rename(archive)
        self.retired.append(census.write(self.evidence / (label + '.retired.json'), dict(path=str(archive), sha256=census.sha(archive))))
        self.catalog['Callbacks'] = [pin for pin in self.catalog['Callbacks'] if pin['Path'] != str(path)]
        self.proposal['Callbacks'].remove(str(path))

    def callback_selected(self, label, *, delay=0):
        entry = self.universe / 'issued' / (label + '.json')
        census.write(entry, dict(selected_config=self.legacy['config']))
        release = dict(self.legacy, entry=str(entry), executable=str(self.helper))
        output = self.evidence / (label + '.observed.json')
        argv = [str(self.helper), '-test.run=^TestR06UnitCensusCallback$', '--', str(entry), release['config']]
        child = self.spawn(argv, label, stdin=subprocess.PIPE,
            env=dict(self.host.env, CAIRN_R06_CENSUS_CALLBACK_OUTPUT=str(output), CAIRN_R06_CENSUS_CALLBACK_DELAY=str(delay)))
        self.add_reference(label, child, release, 'callback')
        if not delay:
            deadline = time.monotonic() + 5
            while not output.exists() and time.monotonic() < deadline:
                require(child.poll() is None, 'selected callback exited before observation')
                time.sleep(.02)
            require(output.exists(), 'selected callback did not read its pinned old config')
        else:
            require(not output.exists(), 'delayed callback already completed readback')
        return child

    def invoke_control(self, operation, label, *, error=None, plan=None, actor=False):
        plan = plan or self.plan(reverse=operation == 'reverse')
        plan['retained'] = str(Path(self.selection['Directory']).with_name('kept-' + label))
        path = self.evidence / (label + '.plan.json')
        census.write(path, plan)
        before = control.snapshot(self.host.dsn, self.host.pg_bin, self.universe / 'state-a')
        files_before = census.snapshot(self.universe)
        active_before = {p.name: census.sha(p) for p in Path(self.selection['Directory']).glob('*.json')}
        result = control.command([self.images['control'], 'unit-control', operation, '--plan', path,
            '--sha256', census.sha(path)], self.evidence / label, env=self.host.env, expected=None)
        parsed = json.loads(result.stdout)
        after = control.snapshot(self.host.dsn, self.host.pg_bin, self.universe / 'state-a')
        files_after = census.snapshot(self.universe)
        census.write(self.evidence / (label + '.preservation.json'), dict(before=before, after=after,
                     files_before=files_before, files_after=files_after))
        if actor:
            require(before == json.loads(self.transport.with_name('actor-before.json').read_text()) and
                    after == json.loads(self.transport.with_name('actor-after.json').read_text()), 'inspector mutated API state beyond the recorded real claim')
        else:
            require(before == after, 'unit control mutated API/session state')
        protected = lambda rows: {p: row for p, row in rows.items() if not Path(p).is_relative_to(self.catalog['ConfigRoot'])}
        require(protected(files_before) == protected(files_after), 'unit control changed protected descriptors/state/ledger/credentials')
        if error is not None:
            require(result.returncode == 1 and error in parsed['error'] and not parsed['exchange']['Published'],
                    f'{label}: wrong refusal or uncertain publication; preserve and reconcile: {parsed}')
            require({p.name: census.sha(p) for p in Path(self.selection['Directory']).glob('*.json')} == active_before,
                    'refusal changed active selection')
            if not actor:
                require(files_before == files_after, 'early refusal changed unit files')
            else:
                prepared = Path(plan['retained'])
                require({p.name for p in prepared.iterdir()} == {member['name'] for member in plan['next']} | {'.watch.lock'},
                        'D6 refusal did not retain the complete preparation')
                require({p.name: census.sha(p) for p in prepared.glob('*.json')} ==
                        {member['name']: member['sha256'] for member in plan['next']}, 'D6 refusal changed prepared pins')
                require(os.path.samefile(prepared / '.watch.lock', Path(self.selection['Directory']) / '.watch.lock'),
                        'D6 refusal split unit lock inode')
        else:
            require(result.returncode == 0 and parsed['inspected'] and parsed['census']['FixtureReady'], f'{label}: {parsed}')
            require(parsed['census']['ExternalCoverage'] == 'unknown' and parsed['census']['NativeRefresh'] == 'unobserved', 'finite proof claimed native/external coverage')
            if operation != 'inspect':
                require(parsed['exchange']['Published'], 'successful exchange did not publish')
                self.retained.append(parsed['exchange']['Retained'])
                self.selection['Pins'] = dict(parsed['exchange']['Active']['Pins'])
                self.proposal['Bindings'] = [copy.deepcopy(self.selection)]
        return parsed

    def close(self):
        for child, stdout, stderr in getattr(self, 'owned', []):
            if child.poll() is None:
                require(child.pid > 1 and os.getpgid(child.pid) == child.pid, 'unsafe owned process cleanup')
                child.send_signal(signal.SIGTERM)
                child.wait(timeout=8)
            stdout.close()
            stderr.close()
            census.write(self.evidence / (Path(stdout.name).stem + '.cleanup.json'),
                         dict(pid=child.pid, exit=child.returncode, joined=True, group_signals=False))
        super().close()


def roundtrip(f):
    manifest = Path(f.manifests['b'])
    enabled = json.loads(manifest.read_text())
    enabled['enabled'] = True
    f.catalog['Bindings'][1] = census.write(manifest, enabled)
    f.callback(unit='b')
    f.proposal['Callbacks'] = []
    disjoint_before = census.snapshot(f.universe / 'state-b')
    old_pins = dict(f.selection['Pins'])
    old = f.watch(f.legacy, 'old-python')
    f.invoke_control('switch', 'old-watcher-live', error='watcher callback is running or lacks')
    f.join_owned(old, 'old-python', watcher=True)
    f.invoke_control('inspect', 'old-drained-inspect')
    forward_result = f.invoke_control('switch', 'to-go')
    f.retire_reference('old-python')
    f.select_catalog(f.go)
    selected = f.watch(f.go, 'selected-go')
    f.invoke_control('reverse', 'go-watcher-live', error='watcher callback is running or lacks')
    f.join_owned(selected, 'selected-go', watcher=True)
    f.invoke_control('inspect', 'go-drained-inspect')
    f.invoke_control('reverse', 'to-python')
    require(f.selection['Pins'] == old_pins, 'reverse did not restore every exact old config')
    f.retire_reference('selected-go')
    f.select_catalog(f.legacy)
    retained = f.watch(f.legacy, 'retained-python')
    f.join_owned(retained, 'retained-python', watcher=True)
    f.invoke_control('inspect', 'retained-drained-inspect')
    lock = Path(f.selection['Directory']) / '.watch.lock'
    require(all(os.path.samefile(lock, Path(copy['Directory']) / '.watch.lock') for copy in f.retained), 'handoff split unit lock inode')
    require(f.child.poll() is None and disjoint_before == census.snapshot(f.universe / 'state-b'), 'disjoint enabled unit changed or stopped')
    require(forward_result['exchange']['Active']['Pins'] != old_pins, 'handoff did not change the complete pinned selection')


def negative(f, name):
    if name.endswith('callback'):
        child = f.callback_selected(name, delay=1500 if name == 'delayed-callback' else 0)
        if name == 'unjoined-callback':
            f.join_owned(child, name, receipt=False)
        f.invoke_control('switch', 'negative', error='callback is running or lacks')
        if name != 'unjoined-callback':
            f.join_owned(child, name)
    elif name == 'omitted-binding':
        plan = f.plan()
        plan['census']['Proposal']['Bindings'][0]['Pins'].pop('two.json')
        f.invoke_control('switch', 'negative', plan=plan, error='proposal differs')
    elif name in ('changed-config', 'changed-runtime'):
        path = Path(f.selection['Directory']) / 'two.json' if name == 'changed-config' else Path(f.legacy['entry'])
        path.chmod(0o600)
        path.write_bytes(path.read_bytes() + b'\n')
        path.chmod(0o400)
        error = 'watch config pin changed' if name == 'changed-config' else 'census pin changed: ' + str(path)
        f.invoke_control('switch', 'negative', error=error)
    elif name == 'incomplete-reverse':
        f.invoke_control('switch', 'to-go')
        f.select_catalog(f.go)
        path = Path(f.retained[0]['Directory']) / 'two.json'
        path.unlink()
        plan = f.plan(reverse=True)
        plan['next'] = [member for member in plan['next'] if Path(member['path']).exists()]
        f.invoke_control('reverse', 'negative', plan=plan, error='watch exchange must retain every binding name')
    elif name == 'final-claim':
        state_path = Path(f.proposal['Sessions'][0])
        agent = json.loads(state_path.read_text())['agent']
        source = f.host.agent('sender', 'create', dict(request_id=control.uid(), draft=dict(kind='note', body='roundtrip D6 real admission',
            scope=dict(repo=f.host.repo, task_id='*', run_id='*'), claim_type='self', sensitivity='shareable')))
        event = f.host.agent('sender', 'event-publish', dict(request_id=control.uid(), kind='notice',
            ref=dict(record_id=source['record_id'], version=source['version']), destination=dict(type='agent', name=agent['inbox'])))
        delivery = f.host.agent('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
        transport = json.loads(f.transport.read_text())
        transport['roundtrip_claim'] = dict(dsn=f.host.dsn, pg_bin=str(f.host.pg_bin), state_dir=str(f.universe / 'state-a'),
            argv=[str(f.images['api']), 'agent', *map(str, f.host.connection('worker')), 'session-inbox-claim'],
            body=dict(request_id=control.uid(), session={key: agent[key] for key in ('agent_id', 'execution_id')},
                      delivery_id=delivery['delivery_id'], native_turn_id='roundtrip-injection', turn_exclusive=True))
        census.write(f.transport, transport)
        result = f.invoke_control('switch', 'negative', error='D6 drain receipt invalidated', actor=True)
        require(result['inspected'] and f.transport.with_name('actor-result.json').exists(), 'real claim was not admitted between receipt reads')


def check(evidence, selected):
    scratch = Path(tempfile.mkdtemp(prefix='r06-watch-unit-', dir=Path.home() / '.cache'))
    census.write(evidence / 'scratch.json', dict(path=str(scratch)))
    source_files = [Path(__file__), ROOT / 'go.mod', ROOT / 'go.sum', ROOT / 'mise.toml',
                    *sorted((ROOT / 'integrations/lifecycle').glob('*.py')),
                    *sorted((ROOT / 'internal/lifecycle/coordination').glob('shared_unit*.go')),
                    ROOT / 'internal/lifecycle/coordination/watch_cli.go',
                    *(ROOT / 'scripts' / name for name in ('check_r06_census_control.py', 'check_r06_unit_control.py',
                        'check_r06_unit_census.py', 'check_r06_status_consumer.py', 'disposable_host.py', 'stage_lifecycle_compatibility.py'))]
    census.write(evidence / 'source-before-use.json', {str(path.relative_to(ROOT)): census.sha(path) for path in source_files})
    images = control.build(scratch)
    for name, target in (('stager', './cmd/cairn-stage-lifecycle'), ('helper', './internal/lifecycle/coordination')):
        image = scratch / 'tools' / name
        argv = ['go', 'build', '-buildvcs=false', '-o', image, target] if name == 'stager' else ['go', 'test', '-race', '-buildvcs=false', '-c', '-o', image, target]
        control.command(argv, scratch / 'tools' / ('build-' + name))
        image.chmod(0o500)
        images[name] = image
        control.command(['go', 'version', '-m', image], scratch / 'tools' / ('modules-' + name))
    census.write(evidence / 'images-before-use.json', {name: dict(path=str(path), sha256=census.sha(path)) for name, path in images.items()})
    pg_bin = pg_bindir()
    census.write(evidence / 'pg-before-use.json', {name: dict(path=str(pg_bin / name), sha256=census.sha(pg_bin / name)) for name in ('initdb', 'pg_ctl', 'postgres', 'createdb', 'psql')})
    cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bin)
    outcomes = []
    try:
        for name in selected:
            f = Fixture(cluster, scratch, evidence, images, name)
            try:
                (roundtrip(f) if name == 'roundtrip' else negative(f, name))
                outcomes.append(dict(case=name, status='PASS'))
                print(json.dumps(outcomes[-1]), flush=True)
            finally:
                f.close()
    finally:
        pid_path = scratch / 'pg/data/postmaster.pid'
        pg_pid = int(pid_path.read_text().splitlines()[0]) if pid_path.exists() else None
        pg_process = census.process_ref(pg_pid) if pg_pid else None
        stopped = cluster.stop()
        census.write(evidence / 'cleanup.json', dict(process=pg_process, stopped=stopped, group_signals=False))
        require(stopped, 'owned PG cleanup unresolved')
    census.write(evidence / 'outcomes.json', outcomes)
    print(f'R06 watch-unit roundtrip: {len(outcomes)} passed, 0 failed, 0 skipped', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--case', choices=CASES, action='append')
    parser.add_argument('--forward-api', type=Path)
    args, extra = parser.parse_known_args()
    if args.forward_api:
        raise SystemExit(forward(args.forward_api, extra))
    require(args.evidence is not None and not extra, '--evidence required')
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    check(args.evidence, args.case or CASES)
