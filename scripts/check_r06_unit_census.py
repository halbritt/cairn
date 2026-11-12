#!/usr/bin/env python3
"""Real-file/process evidence for the finite R06 census, without native refresh.

Run through mise host:exec. Only this fixture's catalog, files and child
processes are used. No API, model, installed configuration or store is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def require(test, message):
    if not test:
        raise AssertionError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    path.chmod(0o600)
    return dict(Path=str(path), SHA256=sha(path))


def process_ref(pid):
    require(pid > 1, 'owned process PID must exceed 1')
    raw = Path(f'/proc/{pid}/stat').read_text()
    fields = raw[raw.rfind(')') + 1:].split()
    pgid, parent = int(fields[2]), int(fields[1])
    require(pgid > 1 and parent > 1, 'owned process group/parent must exceed 1')
    return dict(PID=pid, PGID=pgid, Parent=parent, Start=fields[19],
                Boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


def command(argv, prefix, *, env=None, timeout=90):
    argv = list(map(str, argv))
    write(prefix.with_suffix('.argv.json'), argv)
    with prefix.with_suffix('.stdout').open('w') as stdout, prefix.with_suffix('.stderr').open('w') as stderr:
        result = subprocess.run(argv, env=env, cwd=ROOT, stdout=stdout, stderr=stderr,
                                timeout=timeout, check=False)
    write(prefix.with_suffix('.exit.json'), result.returncode)
    require(result.returncode == 0, f'{prefix}: command exit {result.returncode}')


def snapshot(root):
    facts = {}
    for path in sorted(root.rglob('*')):
        info = path.lstat()
        row = dict(mode=info.st_mode, inode=info.st_ino, bytes=info.st_size)
        if path.is_file() and not path.name.endswith('.token'):
            row['sha256'] = sha(path)
        facts[str(path)] = row
    return facts


class Fixture:
    def __init__(self, scratch, evidence, image, name):
        self.name, self.image = name, image
        self.root = scratch / name
        self.root.mkdir(mode=0o700)
        self.evidence = evidence / name
        self.evidence.mkdir(mode=0o700)
        self.children = []
        self.serial = 0
        data = self.invoke(dict(Mode='create', Root=str(self.root / 'universe')))
        self.universe = Path(data['Root'])
        self.catalog = json.loads(Path(data['Catalog']['Path']).read_text())
        self.proposal = data['Proposal']
        self.manifests, self.memories = data['Manifests'], data['Memories']
        self.catalog_path = Path(data['Catalog']['Path'])

    def invoke(self, plan, trace=False):
        self.serial += 1
        prefix = self.evidence / f'command-{self.serial:02d}'
        plan = dict(plan, Result=str(prefix.with_suffix('.result.json')))
        path = prefix.with_suffix('.plan.json')
        write(path, plan)
        argv = [str(self.image), '-test.run=^TestR06UnitCensusProcess$', '-test.v']
        if trace:
            argv = ['strace', '-f', '-qq', '-s', '1024', '-e',
                    'trace=open,openat,creat,rename,renameat,renameat2,unlink,unlinkat,flock,kill',
                    '-o', str(prefix.with_suffix('.strace')), *argv]
        command(argv, prefix, env=dict(os.environ, CAIRN_R06_CENSUS_PLAN=str(path)))
        if trace:
            lines = prefix.with_suffix('.strace').read_text().splitlines()
            for line in lines:
                require(not ('.token"' in line and ('openat(' in line or 'open(' in line)),
                        'inspector opened a credential selector for content')
                if str(self.universe) not in line:
                    continue
                require(not any(flag in line for flag in ('O_WRONLY', 'O_RDWR', 'O_CREAT', 'O_TRUNC')),
                        'inspector opened a protected unit file for writing')
                require(not any(op in line for op in ('rename(', 'renameat(', 'renameat2(', 'unlink(', 'unlinkat(', 'flock(')),
                        'inspector mutated/locked a protected path')
            write(prefix.with_suffix('.trace-assertions.json'), dict(credential_opens=0, protected_write_opens=0,
                protected_rename_unlink_flock=0, trace_lines=len(lines)))
        return json.loads(prefix.with_suffix('.result.json').read_text())

    def inspect(self, expected, message, *, trace=True):
        pin = write(self.catalog_path, self.catalog)
        before = snapshot(self.universe)
        write(self.evidence / 'before.json', before)
        result = self.invoke(dict(Mode='inspect', Catalog=pin, Proposal=self.proposal), trace=trace)
        after = snapshot(self.universe)
        write(self.evidence / 'after.json', after)
        require(before == after, 'census changed protected bytes/membership/modes, grants or retained references')
        require(result['Census']['FixtureReady'] == expected, f'{self.name}: wrong census disposition: {result}')
        require(result['Census']['ExternalCoverage'] == 'unknown' and result['Census']['NativeRefresh'] == 'unobserved',
                'finite fixture claimed external closure or native supported refresh')
        if expected:
            require(not result['Error'], 'successful fixture census returned an error')
        else:
            require(message in result['Error'], f'{self.name}: expected {message!r}, got {result["Error"]!r}')
        return result

    def callback(self, *, role='callback', delay=0):
        manifest_path = Path(self.manifests['a'])
        manifest = json.loads(manifest_path.read_text())
        config = manifest['coordination_configs'][0]
        entry = self.universe / 'issued' / 'entry.json'
        entry_pin = write(entry, dict(schema='cairn.fixture-callback-entry/1', selected_config=config))
        output = self.evidence / 'callback-observation.json'
        executable = Path(manifest['binary']['path'])
        argv = [str(executable), '-test.run=^TestR06UnitCensusCallback$', '--', str(entry), config['path']]
        stdout = (self.evidence / 'callback.stdout').open('w')
        stderr = (self.evidence / 'callback.stderr').open('w')
        child = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
            env=dict(os.environ, CAIRN_R06_CENSUS_CALLBACK_OUTPUT=str(output), CAIRN_R06_CENSUS_CALLBACK_DELAY=str(delay)),
            start_new_session=True)
        self.children.append((child, stdout, stderr))
        ref = process_ref(child.pid)
        require(ref['PGID'] == child.pid, 'owned callback group must be its new session')
        record = dict(Schema='cairn.fixture-callback-reference/1', Role=role,
            Bindings=[str(Path(self.catalog['Selections'][0]['Directory']) / 'one.json')],
            Config=dict(Path=config['path'], SHA256=config['sha256']),
            Binding=dict(Path=str(manifest_path), SHA256=sha(manifest_path)),
            Process=ref, Executable=dict(Path=str(executable), SHA256=sha(executable)), EntryPoint=entry_pin, Join=None)
        record_path = Path(self.catalog['CallbackRoot']) / 'retained.json'
        self.record, self.record_path, self.observation = record, record_path, output
        self.child = child
        self.update_record()
        write(self.evidence / 'callback-launch.json', dict(argv=argv, process=ref, delay_ms=delay))
        if not delay:
            deadline = time.monotonic() + 5
            while not output.exists() and time.monotonic() < deadline:
                require(child.poll() is None, 'callback exited before actual selected-byte readback')
                time.sleep(.02)
            require(output.exists(), 'actual callback readback timed out')
            observed = json.loads(output.read_text())
            require(observed['Process'] == ref and observed['Config'] == record['Config'], 'callback selected-byte/process identity differs')
        else:
            require(not output.exists(), 'delayed callback was already observed before census')

    def update_record(self):
        self.catalog['Callbacks'] = [write(self.record_path, self.record)]
        self.proposal['Callbacks'] = [str(self.record_path)]

    def join(self, receipt=True):
        self.child.stdin.close()
        code = self.child.wait(timeout=8)
        require(code == 0, 'owned callback did not exit successfully')
        if receipt:
            receipt_pin = write(self.universe / 'joins' / 'retained.json',
                dict(Schema='cairn.fixture-callback-join/1', Process=self.record['Process'], Reaper=process_ref(os.getpid()), ExitCode=code))
            self.record['Join'] = receipt_pin
            self.update_record()
        write(self.evidence / 'callback-join.json', dict(process=self.record['Process'], exit=code, reaped=True, receipt=receipt))

    def publish_disabled(self):
        old_manifest = json.loads(Path(self.manifests['a']).read_text())
        new_root = self.universe / 'disabled-b'
        new_binding = new_root / 'binding.json'
        memory = json.loads(Path(self.memories['a']).read_text())
        memory['inbox_recall_binding'] = str(new_binding)
        memory_pin = write(new_root / 'memory.json', memory)
        descriptors = []
        selected = self.catalog['Selections'][0]
        for desc in old_manifest['coordination_configs']:
            cfg = json.loads(Path(desc['path']).read_text())
            cfg['inbox_recall_binding'] = str(new_binding)
            immutable = write(new_root / Path(desc['path']).name, cfg)
            descriptors.append(dict(path=immutable['Path'], sha256=immutable['SHA256']))
            active = write(Path(selected['Directory']) / Path(desc['path']).name, cfg)
            selected['Pins'][Path(desc['path']).name] = active['SHA256']
        new_manifest = dict(old_manifest, enabled=False, coordination_configs=descriptors,
                            memory_config=dict(path=memory_pin['Path'], sha256=memory_pin['SHA256']))
        self.catalog['Bindings'][0] = write(new_binding, new_manifest)
        self.proposal['Bindings'] = [selected]
        ledger = self.universe / 'memory-a' / 'retained.memory-budget.json'
        grant = json.loads(ledger.read_text())
        grant['inbox_activation']['old-turn'] = True
        write(ledger, grant)
        write(self.evidence / 'disabled-publication.json', dict(binding=str(new_binding), enabled=False,
            retained_callback_config=self.record['Config'], positive_grant=str(ledger)))

    def close(self):
        exits = []
        for child, stdout, stderr in self.children:
            require(child.pid > 1, 'refuse unsafe owned child PID')
            if child.poll() is None:
                child.stdin.close()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    group = os.getpgid(child.pid)
                    require(group > 1 and group == child.pid, 'refuse unsafe owned child group')
                    os.killpg(group, signal.SIGTERM)
                    child.wait(timeout=5)
            stdout.close(); stderr.close()
            exits.append(dict(pid=child.pid, exit=child.returncode, joined=True))
        write(self.evidence / 'cleanup.json', exits)


def check(evidence):
    require(shutil.which('strace'), 'strace prerequisite missing')
    tools = evidence / 'tools'
    tools.mkdir(mode=0o700)
    image = tools / 'census-tests'
    command(['go', 'test', '-race', '-buildvcs=false', '-c', '-o', image, './internal/lifecycle/coordination'], tools / 'build')
    image.chmod(0o500)
    toolchain = json.loads(subprocess.check_output(['go', 'env', '-json', 'GOVERSION', 'GOROOT', 'GOTOOLDIR', 'GOTOOLCHAIN'], cwd=ROOT, text=True))
    pins = dict(head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
        tree=subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=ROOT, text=True).strip(),
        image=dict(path=str(image), sha256=sha(image)), go=toolchain,
        files={str(p.relative_to(ROOT)):sha(p) for p in sorted((ROOT / 'internal/lifecycle/coordination').glob('shared_unit_census*.go'))},
        dependencies={name:sha(ROOT / name) for name in ('go.mod', 'go.sum', 'scripts/check_r06_unit_census.py')},
        native={str(Path(shutil.which(name)).resolve()):sha(Path(shutil.which(name)).resolve()) for name in ('go','python3','strace')},
        compiler_tools={str(p):sha(p) for p in sorted(Path(toolchain['GOTOOLDIR']).iterdir()) if p.is_file()})
    write(tools / 'pins.json', pins)
    scratch = Path(tempfile.mkdtemp(prefix='r06-census-', dir=Path.home() / '.cache'))
    write(evidence / 'scratch.json', dict(path=str(scratch)))
    cases = ('running', 'joined', 'delayed', 'unjoined', 'empty-proposed-callbacks', 'bad-group', 'reused-start', 'cached-disabled')
    outcomes = []
    fixtures = []
    try:
        for name in cases:
            f = Fixture(scratch, evidence, image, name)
            fixtures.append(f)
            try:
                if name == 'cached-disabled':
                    manifest_path = Path(f.manifests['a'])
                    enabled = json.loads(manifest_path.read_text()); enabled['enabled'] = True
                    f.catalog['Bindings'][0] = write(manifest_path, enabled)
                f.callback(role='issued' if name == 'cached-disabled' else 'callback', delay=1500 if name == 'delayed' else 0)
                expected, message = False, 'callback is running'
                if name == 'joined':
                    f.join(); expected, message = True, ''
                elif name == 'unjoined':
                    f.join(receipt=False); message = 'lacks its owner'
                elif name == 'empty-proposed-callbacks':
                    f.proposal['Callbacks'] = []; message = 'callback coverage'
                elif name == 'bad-group':
                    f.record['Process']['PGID'] = 1; f.update_record(); message = 'group/parent must exceed 1'
                elif name == 'reused-start':
                    f.record['Process']['Start'] = str(int(f.record['Process']['Start']) + 1); f.update_record(); message = 'PID was reused'
                elif name == 'cached-disabled':
                    require(json.loads(f.observation.read_text())['Enabled'] is True, 'callback did not cache actual enabled A')
                    f.publish_disabled(); message = 'old cached bytes'
                result = f.inspect(expected, message)
                if name == 'joined':
                    require(result['Census']['Callbacks'][0]['Joined'], 'actual join absent from successful finite census')
                if name == 'cached-disabled':
                    require(result['Census']['PositiveGrants'] and result['Census']['Callbacks'][0]['Config'] == f.record['Config'],
                            'disabled B hid the retained grant or old callback/issued reference')
                    require(Path(f.record['Config']['Path']).exists() and Path(f.record['Executable']['Path']).exists(), 'old issued paths retired')
                outcomes.append(dict(case=name, status='PASS', fixture_ready=expected, error=result['Error']))
                print(json.dumps(outcomes[-1]), flush=True)
            finally:
                f.close()
                shutil.copytree(f.universe, f.evidence / 'selected-files',
                    ignore=shutil.ignore_patterns('*.token', 'image'))
    finally:
        joined = all(child.poll() is not None for f in fixtures for child, _, _ in f.children)
        write(evidence / 'cleanup.json', dict(owned_callbacks_joined=joined, scratch=str(scratch),
            retained_for_reference_and_grant_custody=True))
    write(evidence / 'outcomes.json', outcomes)
    require(len(outcomes) == len(cases), 'required process cases incomplete')
    require(joined, 'owned callback cleanup is unresolved')
    print(f'R06 census: {len(outcomes)} passed, 0 failed, 0 skipped; native refresh UNOBSERVED', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    check(args.evidence)
