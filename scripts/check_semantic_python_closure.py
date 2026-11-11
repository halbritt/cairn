"""Qualify a copied Python selection inside the unchanged campaign wrapper.

Host work only copies/hashes selected frozen data and routes wrapper archives.
Each of the six execution routes gets a separate isolated entry and private HOME.
No fresh reference, installed runtime, service or Rust worker is executed.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import mmap
import os
from pathlib import Path
import re
import shutil
import signal
import struct
import subprocess
import sys
import tarfile
import threading
import time

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
STAGE = '/opt/s04r-selection'
HOME = '/tmp/s04r-worker-home'
ROUTES = ('direct-rank', 'direct-stream', 'direct-embed', 'go-rank', 'go-stream', 'go-embed')
MAPPED_FILES = {}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def restore_exported_modes(root, descriptor):
    """Restore a fresh safe-tar extraction after checking all bytes/link targets.

    tarfile's data filter deliberately adds owner write permission and omits
    directory modes. Never use this on an existing selected generation: the
    caller creates a fresh output directory before extraction.
    """
    root = Path(root)
    require(not root.is_symlink() and root.is_dir(), 'unsafe exported generation root')
    encoded = json.dumps(descriptor, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    require(root.name == hashlib.sha256(encoded).hexdigest(), 'export descriptor address differs')
    actual = {'.': root}
    for base, dirs, files in os.walk(root, followlinks=False):
        for name in [*dirs, *files]:
            path = Path(base)/name
            actual[path.relative_to(root).as_posix()] = path
    require(set(actual) == set(descriptor['entries']), 'exported member inventory differs')
    # Validate everything before changing any mode; a corrupt clone is refused.
    for name, entry in descriptor['entries'].items():
        path = actual[name]
        if entry['type'] == 'symlink':
            require(path.is_symlink() and os.readlink(path) == entry['target'],
                    'exported symlink differs: ' + name)
        elif entry['type'] == 'directory':
            require(not path.is_symlink() and path.is_dir() and entry['mode'] == 0o555,
                    'exported directory differs: ' + name)
        else:
            require(not path.is_symlink() and path.is_file() and entry['mode'] in (0o444, 0o555)
                    and path.stat().st_size == entry['bytes'] and sha(path) == entry['sha256'],
                    'exported file differs: ' + name)
    for name in sorted(actual, key=lambda n: n.count('/'), reverse=True):
        entry = descriptor['entries'][name]
        if entry['type'] != 'symlink':
            actual[name].chmod(entry['mode'])


def frozen_bundle(reference):
    """Read the retained ledgers, verifying every selected captured byte."""
    reference = Path(reference)
    records, provenance = [], {}
    for name in ('captures.json', 'additional-captures.json'):
        ledger = reference / name
        provenance[name] = sha(ledger)
        for row in json.loads(ledger.read_text())['records']:
            scripts = {Path(arg).name for arg in row['command']}
            if not scripts & {'semantic_rank.py', 'embedding_worker.py'}:
                continue
            mode = ('embed' if 'embedding_worker.py' in scripts else
                    'stream' if '--stream' in row['command'] else 'rank')
            selected = {'name': row['name'], 'mode': mode, 'status': row['status'],
                        'exit_code': row.get('exit_code'), 'timeout_seconds': row['timeout_seconds']}
            for kind, field, checksum in (('input', 'input_path', 'input_sha256'),
                                          ('stdout', 'stdout', 'stdout_sha256'),
                                          ('stderr', 'stderr', 'stderr_sha256')):
                data = Path(row[field]).read_bytes()
                require(hashlib.sha256(data).hexdigest() == row[checksum],
                        'frozen captured bytes differ: ' + row['name'] + '/' + kind)
                selected[kind] = data.decode('utf-8')
                selected[kind + '_sha256'] = row[checksum]
            records.append(selected)
    require(records and all(any(r['mode'] == mode for r in records) for mode in ('rank', 'stream', 'embed')),
            'frozen route family missing')
    return {'schema': 'cairn.s04r-frozen-captures/1', 'source': str(reference),
            'ledger_sha256': provenance, 'records': records, 'fresh_reference_execution': False}


PREPARE = r'''
import hashlib,importlib.util,json,os,pathlib,shutil,subprocess,sys,tarfile
sys.dont_write_bytecode=True
root=pathlib.Path('/work');out=pathlib.Path('/tmp/s04r-standalone');out.mkdir(mode=0o700)
spec=importlib.util.spec_from_file_location('closure',root/'scripts/semantic_python_closure.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
tests=subprocess.run(['/usr/bin/python3','-I','-B',str(root/'scripts/test_semantic_python_closure.py'),'-v'],text=True,capture_output=True,timeout=120)
(out/'filesystem-tests.stdout').write_text(tests.stdout);(out/'filesystem-tests.stderr').write_text(tests.stderr)
if tests.returncode:raise RuntimeError(tests.stderr)
inputs=pathlib.Path('/opt/s04r-inputs');meta=pathlib.Path('/opt/s04r-meta')
provenance={name:hashlib.sha256((meta/name).read_bytes()).hexdigest() for name in ['proposed-input-set.json','native-input-inventory.json','runtime-support-data-inventory.json','selection-proposal-02.json','frozen-runtime-reconciliation.json']}
seal=c.seal_inputs({role:inputs/subdir for role,subdir in [('python','python'),('site','full-site'),('workers','workers'),('model','model'),('native','native'),('locale','locale'),('gconv','gconv')]},provenance)
for row in json.loads((meta/'proposed-input-set.json').read_text())['records']:
 observed=seal['selection']['entries'][row['path']]
 if observed['type']!=row['kind'] or observed['mode']!=row['original_mode']:raise RuntimeError('original selected member differs: '+row['path'])
 for key in ['bytes','sha256','target']:
  if key in row and observed[key]!=row[key]:raise RuntimeError('original selected bytes/link differ: '+row['path'])
for inventory in ['native-input-inventory.json','runtime-support-data-inventory.json']:
 for row in json.loads((meta/inventory).read_text())['files']:
  if seal['selection']['entries'][row['path']]['sha256']!=row['sha256']:raise RuntimeError('support/native input differs: '+row['path'])
stage=pathlib.Path('/opt/s04r-selection');stage.mkdir(mode=0o700)
home=pathlib.Path('/tmp/s04r-worker-home');home.mkdir(mode=0o700)
payload=c.publish_payload(stage,seal)
controls=c.render_python_controls(stage,payload,inputs/'original-pyvenv.cfg',home)
original=json.loads((meta/'frozen-runtime-reconciliation.json').read_text())
expected={'rank':original['original_rank_identity'],'embed':original['original_embed_identity']}
manifest=c.publish_observations(stage,payload,controls,expected,provenance)
os.environ.update(GOMAXPROCS='2',GOFLAGS='-p=2',GOTELEMETRY='off',CC='/usr/bin/gcc',CXX='/usr/bin/g++',AR='/usr/bin/ar')
(out/'tools').mkdir();(out/'scripts').mkdir()
subprocess.run(['go','build','-buildvcs=false','-o',str(out/'tools/go-driver'),'./scripts/testdata/semantic-python-closure-driver'],cwd=root,check=True,stdout=sys.stderr,stderr=sys.stderr)
for name in ['semantic_python_closure.py','check_semantic_python_closure.py']:shutil.copy2(root/'scripts'/name,out/'scripts'/name)
shutil.copy2('/opt/s04r-frozen.json',out/'frozen.json')
(out/'selection.json').write_bytes(c.canonical(seal)+b'\n')
candidate={'schema':'cairn.s04r-qualified-selection/1','payload':payload,'controls':controls,'manifest':manifest,'expected_identities':expected,'support_data_provenance':provenance,'frozen_native_sha256':{row['expected'] for row in original['native_comparisons']},'source_sha256':{name:hashlib.sha256((root/'scripts'/name).read_bytes()).hexdigest() for name in ['semantic_python_closure.py','check_semantic_python_closure.py']}}
candidate['frozen_native_sha256']=sorted(candidate['frozen_native_sha256'])
(out/'candidate.json').write_bytes(c.canonical(candidate)+b'\n')
tool={'driver_sha256':hashlib.sha256((out/'tools/go-driver').read_bytes()).hexdigest(),'go_version':subprocess.check_output(['go','version'],text=True),'driver_build':subprocess.check_output(['go','version','-m',str(out/'tools/go-driver')],text=True),'driver_source_sha256':hashlib.sha256((root/'scripts/testdata/semantic-python-closure-driver/main.go').read_bytes()).hexdigest(),'strace_sha256':hashlib.sha256(pathlib.Path('/usr/bin/strace').read_bytes()).hexdigest()}
(out/'tools.json').write_bytes(c.canonical(tool)+b'\n')
(out/'empty-locale').mkdir();(out/'empty-gconv').mkdir()
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
 for p in sorted(out.iterdir()):archive.add(p,arcname=p.name)
 archive.add(stage,arcname='stage')
'''


def mapped_file(proc, fields):
    """Bind a kernel mapping to a stable FD without equating Btrfs stat dev.

    /proc/maps uses inode->i_sb->s_dev; Btrfs getattr returns root->anon_dev.
    Compare against a readonly data mapping of the same open file instead.
    No selected code is executed by this observation.
    """
    path = fields[5]
    require(os.readlink(proc/'ns/mnt') == os.readlink('/proc/self/ns/mnt'),
            'worker mapping is in a different mount namespace')
    require(os.readlink(proc/'map_files'/fields[0]) == path, 'kernel mapped-file path differs')
    actual = proc/'root'/path.lstrip('/')
    with actual.open('rb') as stream:
        before = os.fstat(stream.fileno())
        require(before.st_ino == int(fields[4]), 'mapped inode differs: ' + path)
        key = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        if key not in MAPPED_FILES:
            with mmap.mmap(stream.fileno(), 0, prot=mmap.PROT_READ):
                devices = set()
                for line in Path('/proc/self/maps').read_text().splitlines():
                    own = line.split(maxsplit=5)
                    if len(own) != 6 or int(own[4]) != before.st_ino or own[5] != path:
                        continue
                    if os.readlink(Path('/proc/self/map_files')/own[0]) == path:
                        devices.add(own[3])
                require(len(devices) == 1, 'same-file data mapping identity unavailable')
            content = stream.read()
            after = os.fstat(stream.fileno())
            signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            require(signature(before) == signature(after), 'mapped file changed during hashing')
            MAPPED_FILES[key] = (hashlib.sha256(content).hexdigest(),
                                 content[:4] == b'\x7fELF', devices.pop())
        checksum, elf, mapped_device = MAPPED_FILES[key]
        require(fields[3] == mapped_device, 'kernel mapping device differs from same-file mapping')
    return {'path': path, 'sha256': checksum, 'device': before.st_dev,
            'mapped_device': mapped_device, 'inode': before.st_ino,
            'identity_method': 'kernel map_files path, inode, same-file readonly data mapping device, stable FD hash'}, elf


def mapped_worker(proc, payload, mode):
    argv = (proc / 'cmdline').read_bytes().strip(b'\0').split(b'\0')
    source = payload / 'workers' / ('embedding_worker.py' if mode == 'embed' else 'semantic_rank.py')
    if os.fsencode(source) not in argv:
        return None
    images, data = {}, {}
    for line in (proc / 'maps').read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) != 6 or not fields[5].startswith('/'):
            continue
        path = fields[5]
        require(' (deleted)' not in path, 'deleted worker mapping: ' + path)
        if path in images or path in data:
            continue
        row, elf = mapped_file(proc, fields)
        (images if elf else data)[path] = row
    return {'pid': int(proc.name), 'argv': [x.decode() for x in argv],
            'environment': (proc / 'environ').read_bytes().decode().strip('\0').split('\0'),
            'images': list(images.values()), 'data_mappings': list(data.values()),
            'exe_sha256': sha(proc / 'exe')}


def effects(text):
    result = {'connects': [], 'store_mutations': []}
    for line in text.splitlines():
        match = re.match(r'^\s*(?:\d+\s+)?(\w+)\((.*)', line)
        if not match:
            continue
        name, args = match.groups()
        if name == 'connect':
            result['connects'].append(line)
        elif name in ('write', 'writev', 'pwrite64', 'pwritev', 'ftruncate'):
            if re.search(r'<[^>]*(?:onnxruntime|Microsoft|DeveloperTools)', args.split(',', 1)[0]):
                result['store_mutations'].append(line)
        elif name in ('openat', 'openat2', 'creat', 'mkdir', 'mkdirat', 'truncate',
                      'rename', 'renameat', 'renameat2', 'unlink', 'unlinkat'):
            paths = re.findall(r'"((?:[^"\\]|\\.)*)"', args)
            if any(re.search(r'onnxruntime|Microsoft|DeveloperTools', p) for p in paths):
                if name not in ('openat', 'openat2') or re.search(r'O_CREAT|O_WRONLY|O_RDWR', args):
                    result['store_mutations'].append(line)
    return result


def process_terminal(proc):
    try:
        state = (proc/'stat').read_text().rsplit(')', 1)[1].split()[0]
        return state in ('Z', 'X')
    except (FileNotFoundError, ProcessLookupError):
        return True


def observe(command, input_bytes, timeout, output, payload, mode):
    trace = output.with_suffix('.trace')
    known, errors = {}, []
    permission_retries, exit_races = {}, set()
    done = threading.Event()

    def sample():
        try:
            while not done.is_set():
                for path in Path('/proc').iterdir():
                    if not path.name.isdigit():
                        continue
                    try:
                        row = mapped_worker(path, payload, mode)
                        if row and (row['pid'] not in known or
                                    len(row['images']) >= len(known[row['pid']]['images'])):
                            known[row['pid']] = row
                            permission_retries.pop(row['pid'], None)
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except PermissionError as exc:
                        pid = int(path.name)
                        if process_terminal(path):
                            exit_races.add(pid)
                            permission_retries.pop(pid, None)
                        else:
                            permission_retries[pid] = permission_retries.get(pid, 0)+1
                            if permission_retries[pid] > 2:
                                raise RuntimeError('live worker mapping access denied: '+str(exc)) from exc
                for pid in list(permission_retries):
                    if process_terminal(Path('/proc')/str(pid)):
                        exit_races.add(pid)
                        permission_retries.pop(pid)
                done.wait(0.02)
        except BaseException as exc:
            errors.append(str(exc))

    argv = ['/usr/bin/strace', '-f', '-qq', '-s', '256', '-yy', '-e',
            'trace=execve,connect,openat,openat2,creat,mkdir,mkdirat,write,writev,pwrite64,pwritev,ftruncate,truncate,rename,renameat,renameat2,unlink,unlinkat',
            '-o', str(trace), *command]
    started = time.monotonic()
    with output.with_suffix('.stdout').open('wb') as stdout, output.with_suffix('.stderr').open('wb') as stderr:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                                start_new_session=True, env={'PATH': '/usr/bin:/bin'})
        thread = threading.Thread(target=sample)
        thread.start()
        timed_out = False
        try:
            proc.communicate(input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)
        finally:
            done.set()
            thread.join(timeout=5)
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    unresolved = [pid for pid in permission_retries if not process_terminal(Path('/proc')/str(pid))]
    require(not thread.is_alive() and not errors and not unresolved,
            'mapping observer failed: ' + repr(errors) + '; live unresolved=' + repr(unresolved))
    observed = effects(trace.read_text())
    row = {'argv': argv, 'returncode': proc.returncode, 'timed_out': timed_out,
           'seconds': time.monotonic() - started, 'workers': list(known.values()),
           'effects': observed, 'terminal_mapping_races': sorted(exit_races)}
    output.with_suffix('.json').write_text(json.dumps(row, indent=2))
    require(not observed['connects'] and not observed['store_mutations'],
            'worker process-tree telemetry/network effect observed: ' + str(output))
    return row


def f32(value):
    return struct.unpack('<f', struct.pack('<f', value))[0]


def cosine(a, b):
    return math.fsum(x*y for x, y in zip(a, b)) / math.sqrt(
        math.fsum(x*x for x in a) * math.fsum(y*y for y in b))


def compare_embed(expected, actual, vectors):
    require(set(expected) == set(actual), 'embedding reply field population differs')
    for key, value in expected.items():
        other = actual[key]
        if key == 'vector':
            a, b = [f32(x) for x in value], [f32(x) for x in other]
            require(len(a) == len(b) == 384 and all(math.isfinite(x) for x in b)
                    and any(b), 'invalid embedding vector')
            loss = 1-cosine(a, b)
            require(loss <= 1e-6, 'embedding cosine tolerance exceeded')
            vectors.append({'expected': a, 'actual': b, 'cosine_loss': loss,
                            'max_abs_difference': max(abs(x-y) for x, y in zip(a, b)),
                            'norm': math.sqrt(math.fsum(x*x for x in b)),
                            'bit_exact': all(struct.pack('<f', x) == struct.pack('<f', y)
                                             for x, y in zip(a, b))})
        elif key == 'passages':
            require(len(value) == len(other), 'passage population differs')
            for reference, candidate in zip(value, other):
                compare_embed(reference, candidate, vectors)
        else:
            require(value == other, 'embedding discrete field differs: ' + key)


def compare_embedding_reply(expected, actual, candidate, vectors):
    compare_embed(expected, actual, vectors)
    require(actual['identity'] == candidate['expected_identities']['embed'],
            'embedding identity differs')


def observer_controls(output):
    code = ("import pathlib,socket; s=socket.socket();s.connect_ex(('127.0.0.1',1));s.close();"
            "p=pathlib.Path('/tmp/s04r-observer-home/.cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db');"
            "p.parent.mkdir(parents=True);p.write_bytes(b'observer control')")
    trace = output / 'positive-control.trace'
    subprocess.run(['/usr/bin/strace', '-f', '-qq', '-s', '256', '-yy', '-e',
                    'trace=connect,openat,mkdir,write', '-o', str(trace),
                    '/usr/bin/python3', '-I', '-B', '-c', code], check=True)
    observed = effects(trace.read_text())
    require(observed['connects'] and observed['store_mutations'], 'effect observer positive control failed')
    (output / 'positive-control.json').write_text(json.dumps(observed, indent=2))


def internal_route(route, output):
    output = Path(output)
    output.mkdir(mode=0o700)
    closure = load('closure', Path(__file__).with_name('semantic_python_closure.py'))
    candidate = json.loads(Path('/work/candidate.json').read_text())
    payload = Path(candidate['payload']['path'])
    require(str(payload).startswith(STAGE + '/payloads/'), 'unexpected selected payload path')
    closure.verify_generation(payload, candidate['payload']['descriptor'])
    closure.verify_generation(candidate['controls']['path'], candidate['controls']['descriptor'])
    home = Path(HOME)
    home.mkdir(mode=0o700)
    require(not list(home.iterdir()), 'worker HOME was not initially empty')
    observer_controls(output)
    go, mode = route.split('-', 1)
    is_go = go == 'go'
    launcher = candidate['controls']['launchers'][mode]
    records = [r for r in json.loads(Path('/work/frozen.json').read_text())['records'] if r['mode'] == mode]
    observations, vectors, failed = [], [], []
    prior = Path('/work/resume')/route
    if prior.exists():
        provenance = json.loads((prior/'provenance.json').read_text())
        require(provenance['worker_selection'] == {k: candidate[k] for k in
                                                  ('payload', 'controls', 'expected_identities')},
                'resume worker selection differs')
        require(provenance['frozen_sha256'] == sha('/work/frozen.json'), 'resume frozen reference differs')
        require(effects((prior/'positive-control.trace').read_text())['connects']
                and effects((prior/'positive-control.trace').read_text())['store_mutations'],
                'resume positive observer controls missing')
        passed_log = (prior/'previous-execute.log').read_text().splitlines()
    for index, record in enumerate(records):
        command = ([str(Path('/work/tools/go-driver')), '--mode', mode, '--launcher', launcher]
                   if is_go else [launcher])
        input_bytes = record['input'].encode('utf-8')
        if is_go:
            if mode == 'rank':
                input_bytes = (json.dumps({'id': record['name'], 'request': json.loads(record['input'])})+'\n').encode()
        limit = record['timeout_seconds'] + (30 if is_go and mode == 'embed' else 5 if is_go else 0)
        selected = output / ('case-%03d' % index)
        previous = prior/selected.name
        prior_pass = prior.exists() and route+' '+record['name']+' PASS' in passed_log
        prior_comparison = previous.with_suffix('.comparison.json')
        prior_type_error = (prior.exists() and prior_comparison.exists()
                            and json.loads(prior_comparison.read_text())['row'].get('error') ==
                            'embedding identity differs')
        if previous.with_suffix('.json').exists() and (prior_pass or prior_type_error):
            row = json.loads(previous.with_suffix('.json').read_text())
            require(row['argv'][-len(command):] == command, 'resume invocation differs')
            traced = effects(previous.with_suffix('.trace').read_text())
            require(traced == row['effects'] and not traced['connects'] and not traced['store_mutations'],
                    'resume traced effects differ')
            for suffix in ('.stdout', '.stderr', '.trace', '.json'):
                shutil.copy2(previous.with_suffix(suffix), selected.with_suffix(suffix))
            row['resumed_from'] = provenance['artifact']
            if prior_type_error:
                row['prior_comparison_failure'] = 'embedding identity differs (SHA/object comparer defect)'
        else:
            row = observe(command, input_bytes, limit, selected, payload, mode)
        row['case'] = record['name']
        actual_text = selected.with_suffix('.stdout').read_text()
        expected = [json.loads(x) for x in record['stdout'].splitlines()]
        actual = [json.loads(x) for x in actual_text.splitlines()]
        try:
            if is_go and mode == 'rank':
                actual = [r['result'] for r in actual if 'result' in r]
            elif is_go and mode == 'embed':
                actual = [r for r in actual if 'error' not in r]
            elif mode == 'stream':
                # Fixed stream cache=1 preserves Go's existing selection. The
                # additional cache envelope is recorded, never a numeric oracle.
                row['additional_cache_envelopes'] = sum('cache' in r for r in actual)
                actual = [{key: r[key] for key in ('id', 'result')} for r in actual if 'result' in r]
            require(len(actual) == len(expected), 'successful reply population differs')
            for reference, reply in zip(expected, actual):
                if mode == 'embed':
                    compare_embedding_reply(reference, reply, candidate, vectors)
                else:
                    require(reference == reply, 'rank/stream discrete result differs')
                    result = reply['result'] if mode == 'stream' else reply
                    require(result['model_sha256'] == candidate['expected_identities']['rank'],
                            'rank identity differs')
            successful = record['status'] == 'observed' and record['exit_code'] == 0
            if successful:
                require(row['returncode'] == 0 and not row['timed_out'], 'expected successful case failed')
                require(any(any('onnxruntime_pybind11_state' in image['path'] for image in worker['images'])
                            for worker in row['workers']), 'successful model case has no mapped runtime evidence')
            elif is_go:
                raw = [json.loads(x) for x in actual_text.splitlines()]
                require(row['timed_out'] or any('error' in r for r in raw), 'expected failure became success')
            else:
                require(row['timed_out'] or row['returncode'] != 0, 'expected failure became success')
            require(not list(home.rglob('*')), 'worker HOME/cache changed')
            row['comparison'] = 'PASS'
        except RuntimeError as error:
            row['comparison'], row['error'] = 'FAIL', str(error)
            failed.append({'case': record['name'], 'error': str(error)})
        row['home_empty_after_comparison'] = not list(home.rglob('*'))
        observations.append(row)
        selected.with_suffix('.comparison.json').write_text(json.dumps(
            {'record': record['name'], 'comparison': row['comparison'],
             'input_sha256': record['input_sha256'], 'frozen_stdout_sha256': record['stdout_sha256'],
             'worker_selection': {k: candidate[k]['address'] for k in ('payload', 'controls')},
             'harness_sha256': sha(__file__), 'row': row}, indent=2))
        print(route, record['name'], row['comparison'], flush=True)
    model_workers = [w for r in observations for w in r['workers']
                     if any('onnxruntime_pybind11_state' in image['path'] for image in w['images'])]
    require(model_workers, 'no actual model worker mapping observed')
    for worker in model_workers:
        for mapping in worker['images'] + worker['data_mappings']:
            path = Path(mapping['path'])
            require(path.is_relative_to(payload), 'ambient worker mapping: ' + str(path))
            member = path.relative_to(payload).as_posix()
            require(candidate['payload']['descriptor']['entries'][member]['sha256'] == mapping['sha256'],
                    'mapped image differs from selected bytes')
        env = dict(value.split('=', 1) for value in worker['environment'])
        require(env == closure.worker_environment(payload, Path(HOME), mode), 'actual worker environment differs')
    pair_loss = 0.0
    for index, a in enumerate(vectors):
        for b in vectors[index+1:]:
            pair_loss = max(pair_loss, abs(cosine(a['expected'], b['expected'])-
                                           cosine(a['actual'], b['actual'])))
    require(pair_loss <= 1e-6, 'query/passage cosine difference exceeded')
    result = {'route': route, 'cases': len(records), 'failed': failed,
              'model_workers_observed': len(model_workers), 'vectors': len(vectors),
              'vectors_bit_exact': sum(v['bit_exact'] for v in vectors),
              'max_vector_cosine_loss': max((v['cosine_loss'] for v in vectors), default=0),
              'max_vector_abs_difference': max((v['max_abs_difference'] for v in vectors), default=0),
              'max_pairwise_cosine_difference': pair_loss,
              'max_norm_error': max((abs(v['norm']-1) for v in vectors), default=0),
              'connect_attempts': 0, 'telemetry_store_mutations': 0,
              'home_empty_after': not list(home.rglob('*')),
              'observations': observations}
    (output / 'result.json').write_text(json.dumps(result, indent=2))
    require(not failed, 'frozen capture comparison failures: ' + repr(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--artifact-dir')
    parser.add_argument('--inputs')
    parser.add_argument('--metadata')
    parser.add_argument('--frozen-reference')
    parser.add_argument('--resume-artifact', help='reuse this exact prepared worker and revalidate completed case evidence')
    parser.add_argument('--stage-source', help='retained immutable stage when the resumed package references an earlier export')
    parser.add_argument('--internal-route', choices=ROUTES)
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.internal_route:
        internal_route(args.internal_route, args.output)
        return
    require(all((args.artifact_dir, args.inputs, args.metadata, args.frozen_reference)),
            'artifact-dir, inputs, metadata and frozen-reference are required')
    output = Path(args.artifact_dir)
    output.mkdir(mode=0o700)
    frozen = output / 'selected-frozen.json'
    frozen.write_text(json.dumps(frozen_bundle(args.frozen_reference), ensure_ascii=False))
    isolation = load('isolation', ROOT / 'scripts/test_hybrid_runtime_operations.py')
    orchestration = load('orchestration', ROOT / 'scripts/check_semantic_offline_package.py')
    metadata = Path(args.metadata)
    binds = [args.inputs+':/opt/s04r-inputs', str(frozen)+':/opt/s04r-frozen.json']
    binds += [str(metadata/name)+':/opt/s04r-meta/'+name for name in
              ('proposed-input-set.json','native-input-inventory.json','runtime-support-data-inventory.json',
               'selection-proposal-02.json','frozen-runtime-reconciliation.json')]
    standalone = output / 'standalone'
    standalone.mkdir(mode=0o700)
    if args.resume_artifact:
        previous = Path(args.resume_artifact)
        old = previous/'standalone'
        candidate = json.loads((old/'candidate.json').read_text())
        tools = json.loads((old/'tools.json').read_text())
        require(sha(ROOT/'scripts/semantic_python_closure.py') ==
                candidate['source_sha256']['semantic_python_closure.py'], 'resume worker helper source differs')
        require(sha(old/'tools/go-driver') == tools['driver_sha256'] and
                sha(ROOT/'scripts/testdata/semantic-python-closure-driver/main.go') ==
                tools['driver_source_sha256'], 'resume Go fixture binding differs')
        require(sha(old/'scripts/check_semantic_python_closure.py') ==
                candidate['source_sha256']['check_semantic_python_closure.py'],
                'previous execution harness binding differs')
        require(sha(old/'frozen.json') == sha(frozen), 'resume frozen bytes differ')
        (standalone/'scripts').mkdir();(standalone/'tools').mkdir()
        for name in ('semantic_python_closure.py','check_semantic_python_closure.py'):
            shutil.copy2(ROOT/'scripts'/name, standalone/'scripts'/name)
        for name in ('frozen.json','tools.json'):
            shutil.copy2(old/name, standalone/name)
        shutil.copy2(old/'tools/go-driver', standalone/'tools/go-driver')
        candidate['source_sha256']['check_semantic_python_closure.py'] = sha(
            ROOT/'scripts/check_semantic_python_closure.py')
        (standalone/'candidate.json').write_text(json.dumps(candidate))
        for name in ('empty-locale','empty-gconv'):
            (standalone/name).mkdir()
        stage_source = Path(args.stage_source) if args.stage_source else old/'stage'
        require(stage_source.is_dir() and not stage_source.is_symlink(), 'retained stage source is unavailable')
        (output/'continuation.json').write_text(json.dumps(
            {'prepared_artifact': str(previous), 'stage_source': str(stage_source),
             'helper_sha256': sha(ROOT/'scripts/semantic_python_closure.py'),
             'harness_sha256': sha(__file__), 'frozen_sha256': sha(frozen)}, indent=2))
        for route in ROUTES:
            source = previous/route
            if not (source/'execute.log').exists() or not (source/'execute.tar.gz').exists():
                continue
            if not (source/'route').exists():
                with tarfile.open(source/'execute.tar.gz') as archive:
                    archive.extractall(source, filter='data')
            if not (source/'route/positive-control.trace').exists():
                continue
            destination = standalone/'resume'/route
            destination.mkdir(parents=True)
            for selected in (source/'route').glob('*'):
                if selected.is_file() and not selected.is_symlink():
                    shutil.copy2(selected,destination/selected.name)
            shutil.copy2(source/'execute.log', destination/'previous-execute.log')
            (destination/'provenance.json').write_text(json.dumps(
                {'artifact': str(source), 'worker_selection': {k: candidate[k] for k in
                 ('payload','controls','expected_identities')}, 'frozen_sha256':sha(frozen),
                 'execution_harness_sha256':sha(old/'scripts/check_semantic_python_closure.py')}))
    else:
        orchestration.isolated_entry(isolation, ROOT, PREPARE, output, 'prepare', binds)
        with tarfile.open(output/'prepare.tar.gz') as archive:
            archive.extractall(standalone, filter='data')
        stage_source = standalone/'stage'
    candidate = json.loads((standalone/'candidate.json').read_text())
    if not args.resume_artifact:
        for name in ('payload', 'controls'):
            selected = candidate[name]
            restore_exported_modes(standalone/'stage'/('payloads' if name == 'payload' else 'controls')/
                                   selected['address'], selected['descriptor'])
        manifest = candidate['manifest']
        restore_exported_modes(standalone/'stage/manifests'/manifest['address'], manifest['descriptor'])
        for parent in ('stage', 'stage/payloads', 'stage/controls', 'stage/manifests'):
            (standalone/parent).chmod(0o700)
    for route in ROUTES:
        code = ("import pathlib,subprocess,sys,tarfile;out=pathlib.Path('/tmp/s04r-route');"
                "r=subprocess.run(['/usr/bin/python3','-I','-B','/work/scripts/check_semantic_python_closure.py',"
                "'--internal-route',"+repr(route)+",'--output',str(out)],stdout=sys.stderr,stderr=sys.stderr);"
                "a=tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz');a.add(out,arcname='route');a.close();"
                "sys.exit(r.returncode)")
        directory = output/route
        directory.mkdir(mode=0o700)
        shadows = [str(stage_source)+':'+STAGE,
                   str(standalone/'empty-locale')+':/usr/lib/locale',
                   str(standalone/'empty-gconv')+':/usr/lib/x86_64-linux-gnu/gconv']
        orchestration.isolated_entry(isolation, standalone, code, directory, 'execute', shadows)
        with tarfile.open(directory/'execute.tar.gz') as archive:
            archive.extractall(directory, filter='data')
        print(json.dumps({'route': route, 'result': json.loads((directory/'route/result.json').read_text())
                          | {'observations': 'retained in route/result.json'}}), flush=True)


if __name__ == '__main__':
    main()
