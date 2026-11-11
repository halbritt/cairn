"""Local package fixtures with actual model, native mappings and Go supervisors.

Run inside the campaign wrapper. External assets/provenance are explicit inputs;
no managed-host inventory or fleet acceptance is inferred from this local run.
"""
import argparse
import copy
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time

sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location('semantic_package', Path(__file__).with_name('semantic_package.py'))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def system_dependencies(paths):
    """Seed the clean filesystem from ELF declarations; runtime mappings prove use."""
    pending = [Path(p).resolve(strict=True) for p in paths]
    seen, system = set(), {}
    selected_sonames = set()
    for path in pending:
        dynamic = package.capture(['/usr/bin/readelf', '-d', str(path)])
        selected_sonames.update(re.findall(r'\(SONAME\).*\[(.+?)\]', dynamic))
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        dynamic = package.capture(['/usr/bin/readelf', '-d', str(path)])
        names = re.findall(r'\(NEEDED\).*\[(.+?)\]', dynamic)
        headers = package.capture(['/usr/bin/readelf', '-l', str(path)])
        interpreters = re.findall(r'Requesting program interpreter: (.+?)\]', headers)
        for name in names + interpreters:
            if name in selected_sonames:
                continue
            candidates = [Path(name)] if name.startswith('/') else [
                Path(base) / name for base in ('/usr/lib/x86_64-linux-gnu', '/usr/lib64', '/usr/lib')]
            selected = next((p for p in candidates if p.is_file()), None)
            require(selected is not None, 'unresolved system ABI dependency: ' + name)
            found = selected.resolve(strict=True)
            row = system.setdefault(str(found), {'path': str(found), 'aliases': [],
                                     **package.file_record(package.regular_bytes(found))})
            if str(selected) not in row['aliases']:
                row['aliases'].append(str(selected))
            pending.append(found)
    return list(system.values())


def fixture_command(driver, command):
    # Entered by the host through the existing wrapper with standalone inputs.
    # Never create a nested namespace or an unconfined alternate execution path.
    return [str(driver) if arg == '/fixture/go-driver' else arg for arg in command]


def descendant_of(pid, ancestor):
    """Limit outer observations to this entry's actual child tree."""
    while pid > 1:
        if pid == ancestor:
            return True
        try:
            status = Path('/proc', str(pid), 'status').read_text()
        except (FileNotFoundError, ProcessLookupError):
            return False
        pid = int(re.search(r'^PPid:\s+(\d+)$', status, re.MULTILINE).group(1))
    return False


def process_snapshot(payload, known, *, ancestor=None, minimal_root=False):
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) in known:
            continue
        try:
            argv = (proc / 'cmdline').read_bytes().strip(b'\0').split(b'\0')
            if not argv or not argv[0].startswith(str(payload / 'bin/').encode()):
                continue
            if ancestor is not None and not descendant_of(int(proc.name), ancestor):
                continue
            selected_payload = proc / 'root' / str(payload).lstrip('/') if minimal_root else payload
            maps = (proc / 'maps').read_text().splitlines()
            images, native = {}, {}
            for fd in (proc / 'fd').iterdir():
                target = os.readlink(fd)
                if not target.startswith('/memfd:cairn-'):
                    continue
                info = fd.stat()
                with fd.open('rb') as stream:
                    sha = package.digest(stream.read())
                    seals = fcntl.fcntl(stream.fileno(), fcntl.F_GET_SEALS)
                matching = []
                for line in maps:
                    fields = line.split(maxsplit=5)
                    if len(fields) < 5:
                        continue
                    major, minor = (int(x, 16) for x in fields[3].split(':'))
                    if int(fields[4]) == info.st_ino and os.makedev(major, minor) == info.st_dev:
                        matching.append(line)
                if matching:
                    images[sha] = {'sha256': sha, 'seals': seals, 'mapped_path': target, 'maps': matching}
            for line in maps:
                fields = line.split(maxsplit=5)
                if len(fields) != 6 or not fields[5].startswith('/') or ' (deleted)' in fields[5]:
                    continue
                path = fields[5]
                if path in native or path.startswith(str(payload)):
                    continue
                actual = proc / 'root' / path.lstrip('/')
                if actual.is_file():
                    info = actual.stat()
                    major, minor = (int(x, 16) for x in fields[3].split(':'))
                    require(info.st_ino == int(fields[4]) and info.st_dev == os.makedev(major, minor),
                            'mapped system path changed before hashing: ' + path)
                    native[path] = {'path': path, 'sha256': package.digest(actual.read_bytes()),
                                    'device': info.st_dev, 'inode': info.st_ino}
            expected = {package.digest((selected_payload / 'native' / name).read_bytes()) for name in package.LIBRARIES}
            if set(images) != expected:
                continue
            require(all(r['seals'] == 15 for r in images.values()), 'native image is not sealed')
            environment = (proc / 'environ').read_bytes().decode().strip('\0').split('\0')
            require('ORT_DISABLE_TELEMETRY=1' in environment, 'telemetry disable missing from worker')
            require('OPENBLAS_NUM_THREADS=2' in environment, 'thread cap missing from worker')
            require(not any(e.startswith(('LD_LIBRARY_PATH=', 'LD_PRELOAD=', 'LD_AUDIT=')) for e in environment),
                    'ambient loader setting reached worker')
            # The standalone root has no development checkout. Bootstrap Python
            # remains visible and is recorded rather than claimed absent.
            checkout_visible = ((proc / 'root/work/go.mod').exists() or (proc / 'root/work/.git').exists())
            python_visible = bool(list((proc / 'root/usr/bin').glob('python*')))
            require(not checkout_visible, 'development checkout exposed in standalone fixture')
            if minimal_root:
                require(not python_visible, 'Python exposed in native fixture worker root')
            binary_sha = package.digest((proc / 'exe').read_bytes())
            selected_binary = proc / 'root' / argv[0].decode().lstrip('/') if minimal_root else Path(argv[0].decode())
            require(binary_sha == package.digest(selected_binary.read_bytes()),
                    'actual running image differs from selected binary')
            found.append({'pid': int(proc.name), 'argv': [x.decode() for x in argv],
                          'binary_sha256': binary_sha,
                          'environment': environment, 'native_images': list(images.values()),
                          'system_mappings': list(native.values()),
                          'development_checkout_visible': checkout_visible, 'python_visible': python_visible})
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


def trace_effects(text):
    effects = {'connects': [], 'store_mutations': []}
    for line in text.splitlines():
        call = re.match(r'^\s*(?:\d+\s+)?(\w+)\((.*)', line)
        if not call:
            continue
        name, args = call.groups()
        if name == 'connect':
            effects['connects'].append(line)
        elif name in ('write', 'pwrite64'):
            # -yy annotates the FD's actual path. Query/output bytes containing
            # a store name are not a write to that store.
            fd = args.split(',', 1)[0]
            if re.search(r'<[^>]*(?:onnxruntime\.db|Microsoft|DeveloperTools)', fd):
                effects['store_mutations'].append(line)
        elif name in ('openat', 'creat', 'mkdir', 'mkdirat', 'truncate', 'rename', 'unlink'):
            paths = re.findall(r'"((?:[^"\\]|\\.)*)"', args)
            if any(re.search(r'onnxruntime\.db|Microsoft|DeveloperTools', path) for path in paths):
                if name != 'openat' or re.search(r'O_CREAT|O_WRONLY|O_RDWR', args):
                    effects['store_mutations'].append(line)
    return effects


def shadow_fixtures(inventory, driver, rows, staged):
    paths = inventory['shadow_paths']
    require(len(paths) == 3, 'missing system math shadow fixture inputs')
    shadow_sha = package.digest(driver.read_bytes())
    for path in paths:
        require(package.digest(Path(path).read_bytes()) == shadow_sha, 'shadow input binding differs')
    for row in rows:
        for worker in row['workers']:
            require(not any(mapping['path'] in paths for mapping in worker['system_mappings']),
                    'shadow system object was mapped')
    refused = []
    for mode in package.MODES:
        result = subprocess.run([staged['launchers'][mode]['path']], input=b'', capture_output=True,
                                env=dict(os.environ, LD_LIBRARY_PATH='/usr/lib/x86_64-linux-gnu'), timeout=15)
        require(result.returncode != 0 and not result.stdout and b'loader override refused' in result.stderr,
                'inherited shadow search path did not refuse')
        refused.append({'mode': mode, 'returncode': result.returncode})
    return [{'case': 'shadow-system-library', 'paths': paths, 'shadow_sha256': shadow_sha,
             'selected_memfd_images_verified_in_positive_runs': len(rows)},
            {'case': 'shadow-loader-environment', 'runs': refused}]


def worker_home_snapshot():
    home = Path(package.worker_environment()['HOME'])
    mapped = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.cache/Microsoft/DeveloperTools/.onnxruntime'
    try:
        mapped.stat()
        mapped_state = 'present'
    except FileNotFoundError:
        mapped_state = 'absent'
    except PermissionError:
        mapped_state = 'inaccessible in masked OS home'
    return {'fixed_home_entries': sorted(str(p.relative_to(home)) for p in home.rglob('*')),
            'mapped_os_home_ort_store': mapped_state}


def observe_run(name, command, input_bytes, expected, output, payload):
    trace = output / (name + '.trace')
    stdout, stderr = output / (name + '.stdout'), output / (name + '.stderr')
    argv = ['/usr/bin/strace', '-f', '-s', '256', '-yy', '-e',
            'trace=execve,connect,openat,creat,mkdir,mkdirat,write,pwrite64,truncate,rename,unlink',
            '-o', str(trace), *command]
    known, errors = {}, []
    done = threading.Event()
    before_home = worker_home_snapshot()
    require(not before_home['fixed_home_entries'] and before_home['mapped_os_home_ort_store'] in
            ('absent', 'inaccessible in masked OS home'),
            name + ': worker HOME/cache not initially empty')

    def sample():
        try:
            while not done.is_set():
                for row in process_snapshot(payload, known):
                    known[row['pid']] = row
                done.wait(0.01)
        except BaseException as error:
            errors.append(str(error))

    with stdout.open('wb') as out, stderr.open('wb') as err:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=out, stderr=err, start_new_session=True)
        thread = threading.Thread(target=sample)
        thread.start()
        try:
            proc.communicate(input_bytes, timeout=120)
        except BaseException:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        finally:
            done.set()
            thread.join()
    require(not errors, 'mapping observer failed: ' + repr(errors))
    require(proc.returncode == 0, name + ' failed: ' + stderr.read_text()[-2000:])
    replies = [json.loads(line) for line in stdout.read_text().splitlines()]
    require(len(replies) == expected and known, name + ': replies/mapping evidence missing')
    require(all('error' not in r for r in replies), name + ': Go supervisor reported error')
    text = trace.read_text()
    worker_pids = set(known)
    # All descendant connect attempts are checked, including failed attempts.
    effects = trace_effects(text)
    require(not effects['connects'], name + ': connect attempt observed')
    require(not effects['store_mutations'], name + ': telemetry store mutation observed')
    after_home = worker_home_snapshot()
    require(after_home == before_home, name + ': worker HOME/cache changed')
    observation = {'name': name, 'command': command, 'returncode': proc.returncode,
            'replies': replies, 'workers': list(known.values()), 'worker_pids': sorted(worker_pids),
            'connect_attempts': 0, 'telemetry_store_mutations': 0,
            'home_before': before_home, 'home_after': after_home,
            'trace_sha256': package.digest(trace.read_bytes())}
    (output / (name + '.observations.json')).write_bytes(package.canonical(observation) + b'\n')
    return observation


def controls(root, driver, dependencies, output):
    home = Path(package.worker_environment()['HOME'])
    home.mkdir(mode=0o700, exist_ok=True)
    require(home.is_dir() and not list(home.iterdir()),
            'control requires empty disposable worker HOME: ' + repr(list(home.iterdir())))
    trace = output / 'observer-control.trace'
    argv = ['/usr/bin/strace', '-f', '-s', '256', '-yy', '-e',
            'trace=connect,openat,creat,mkdir,mkdirat,write', '-o', str(trace),
            *fixture_command(driver, ['/fixture/go-driver', '--observer-control'])]
    p = subprocess.run(argv, capture_output=True, timeout=15)
    require(p.returncode == 0, 'observer control failed: ' + repr(p.stderr))
    effects = trace_effects(trace.read_text())
    require(effects['connects'] and effects['store_mutations'], 'observer did not detect positive controls')
    # Remove only the fixture's exact generated store and empty ancestors. The
    # subsequent workers must still start with an empty disposable HOME/cache.
    store = home / '.cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db'
    require(store.read_bytes() == b'observer control; no telemetry payload', 'wrong observer control store')
    store.unlink()
    parent = store.parent
    while parent != home:
        parent.rmdir()
        parent = parent.parent
    require(not list(home.iterdir()), 'control cleanup left HOME entries')
    return {'connect_attempts_detected': len(effects['connects']),
            'telemetry_store_mutations_detected': len(effects['store_mutations']),
            'trace_sha256': package.digest(trace.read_bytes())}


def remove_owned_fixture(root):
    for base, _, _ in os.walk(root):
        Path(base).chmod(0o700)
    shutil.rmtree(root)


def negative_fixtures(output, source, build, model, libraries, provenance, dependencies, driver):
    rows = []
    for name, target, missing in (
        ('missing-library', 'native/ort', True), ('wrong-library', 'native/openblas', False),
        ('missing-asset', 'model/config.json', True), ('wrong-asset', 'model/tokenizer.json', False),
        ('digest-mismatch', 'bin/cairn-semantic-rank', False),
    ):
        root = output / ('negative-' + name)
        root.mkdir(mode=0o700)
        staged = package.stage(root, source, build, model, libraries, provenance, dependencies)
        changed = Path(staged['payload']) / target
        changed.parent.chmod(0o755)
        if missing:
            changed.unlink()
        else:
            changed.chmod(0o644)
            changed.write_bytes(b'wrong selected bytes; never repair this immutable path')
            changed.chmod(0o444)
        changed.parent.chmod(0o555)
        runs = []
        for mode in package.MODES:
            command = fixture_command(driver, [staged['launchers'][mode]['path']])
            p = subprocess.run(command, input=b'', capture_output=True, timeout=15)
            require(p.returncode != 0 and p.stdout == b'', name + ': selected bytes did not refuse')
            runs.append({'mode': mode, 'returncode': p.returncode, 'stderr_sha256': package.digest(p.stderr)})
        try:
            package.stage(root, source, build, model, libraries, provenance, dependencies)
        except package.PackageError as error:
            require('refusing repair' in str(error), 'unexpected staging error for invalid immutable path')
        else:
            raise RuntimeError('staging repaired an invalid immutable path')
        rows.append({'case': name, 'runs': runs, 'staging_refused_repair': True})
        remove_owned_fixture(root)
    return rows


def live_staging(root, staged, source, build, model, libraries, provenance, dependencies, driver, request, output):
    selected = Path(staged['launchers']['stream']['path'])
    before = {'sha256': package.digest(selected.read_bytes()), 'inode': selected.stat().st_ino,
              'mtime_ns': selected.stat().st_mtime_ns}
    command = fixture_command(driver, ['/fixture/go-driver', '--mode', 'stream', '--launcher', str(selected)])
    transcript, workers = [], {}
    with (output / 'live-staging.stderr').open('wb') as err:
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                start_new_session=True, bufsize=0)
        def exchange():
            proc.stdin.write(package.canonical({'request': request}) + b'\n')
            proc.stdin.flush()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                for row in process_snapshot(Path(staged['payload']), workers):
                    workers[row['pid']] = row
                if select.select([proc.stdout], [], [], 0.01)[0]:
                    reply = json.loads(proc.stdout.readline())
                    transcript.append(reply)
                    return reply
            raise RuntimeError('live-staging exchange timed out')
        try:
            first = exchange()
            require('result' in first, 'initial selected service failed')
            again = package.stage(root, source, build, model, libraries, provenance, dependencies)
            require(again == staged and proc.poll() is None, 'staging changed selection or stopped service')
            time.sleep(3.5)  # Explicitly exercise the real Go idle release.
            second = exchange()
            require(second == first, 'identity/results changed across idle respawn')
            live = [row for row in process_snapshot(Path(staged['payload']), {})
                    if Path('/proc', str(row['pid'])).exists()]
            require(len(live) == 1, 'cannot uniquely select owned disposable stream worker')
            os.kill(live[0]['pid'], signal.SIGKILL)
            failed = exchange()
            require('error' in failed, 'failed worker was not reported')
            third = exchange()
            require(third == first, 'identity/results changed across failure respawn')
            require(before == {'sha256': package.digest(selected.read_bytes()), 'inode': selected.stat().st_ino,
                               'mtime_ns': selected.stat().st_mtime_ns}, 'selected launcher was rewritten')
            proc.stdin.close()
            require(proc.wait(timeout=10) == 0, 'disposable service did not close')
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
    return {'selected': str(selected), 'before_after': before, 'transcript': transcript,
            'workers': list(workers.values()), 'idle_respawn': True, 'failure_respawn': True}


def build_probe(build_root, source, output):
    deps = build_root / 'target' / package.TARGET / 'release/deps'
    argv = [os.environ.get('RUSTC', 'rustc'), '--edition=2021', '--target', package.TARGET,
            '-C', 'opt-level=3', '-L', 'dependency=' + str(deps),
            '-L', 'dependency=' + str(build_root / 'target/release/deps')]
    for crate in ('cairn_semantic_backend', 'cairn_semantic_math', 'serde_json'):
        libraries = list(deps.glob('lib' + crate + '-*.rlib'))
        require(len(libraries) == 1, 'ambiguous probe dependency: ' + crate)
        argv += ['--extern', crate + '=' + str(libraries[0])]
    argv += [str(source / 'scripts/testdata/semantic-package-observer.rs'), '-o', str(output)]
    subprocess.run(argv, check=True)


def run(args):
    output, source, build = map(Path, (args.output, args.source_root, args.build))
    output.mkdir(mode=0o700)
    driver, probe = Path(args.go_driver), Path(args.probe)
    root = Path('/work')
    require(not (root / 'go.mod').exists() and not (root / '.git').exists(), 'development checkout exposed')
    require(not Path('/home/halbritt/git/cairn').exists(), 'host checkout exposed')
    inventory = package.read_json(root / 'standalone.json')
    require(inventory['schema'] == 'cairn.semantic-standalone-fixture/1', 'wrong standalone descriptor')
    actual = {p.relative_to(root).as_posix(): package.digest(p.read_bytes())
              for p in root.rglob('*') if p.is_file() and p.name != 'standalone.json'}
    require(actual == inventory['files'], 'standalone package input binding differs')
    libraries = {role: getattr(args, role) for role in package.LIBRARIES}
    dependencies = system_dependencies([build / 'cairn-semantic-rank', build / 'cairn-semantic-embed',
                                        *libraries.values(), driver, '/usr/bin/sh', '/usr/bin/env',
                                        '/usr/bin/sha256sum', '/usr/bin/find'])
    root = output / 'stage'
    root.mkdir(mode=0o700)
    provenance = package.read_json(args.provenance)
    staged = package.stage(root, source, build, args.model_dir, libraries, provenance, dependencies)
    payload = Path(staged['payload'])
    (output / 'stage-result.json').write_bytes(package.canonical(staged) + b'\n')
    templates = json.loads(package.capture([str(probe), str(payload)], env=package.worker_environment()))
    (output / 'probe-observations.json').write_bytes(package.canonical(templates) + b'\n')
    request = package.read_json(args.rank_request)
    if 'request' in request:
        request = request['request']
    rank_input = package.canonical(request) + b'\n'
    stream_input = package.canonical({'id': 'package', 'request': request}) + b'\n'
    embed_input = b'{"id":"package","operation":"identity","text":""}\n'
    control_report = controls(root, driver, dependencies, output)
    rows = []
    for route in ('direct', 'go'):
        for mode in package.MODES:
            launch = staged['launchers'][mode]['path']
            command = [launch] if route == 'direct' else ['/fixture/go-driver', '--mode', mode, '--launcher', launch]
            input_bytes = (embed_input if mode == 'embed' else stream_input if mode == 'stream' else rank_input)
            if route == 'go':
                input_bytes = package.canonical({'request': request}) + b'\n'
            argv = fixture_command(driver, command)
            row = observe_run(route + '-' + mode, argv, input_bytes, 1, output, payload)
            row['route'], row['mode'] = route, mode
            rows.append(row)
    derived = {}
    for mode in ('rank', 'embed'):
        inputs = copy.deepcopy(templates[mode + '_template'])
        inputs['compilation']['binary_sha256'] = package.digest((payload / 'bin' / ('cairn-semantic-' + mode)).read_bytes())
        identity = package.digest(package.canonical(inputs))
        for row in rows:
            if (row['mode'] == 'embed') != (mode == 'embed'):
                continue
            result = row['replies'][0]
            if row['route'] == 'go':
                result = result['result']
            elif mode == 'embed':
                result = result['identity']
            elif row['mode'] == 'stream':
                result = result['result']
            require(result['model_sha256'] == identity, 'derived inputs do not match actual ' + row['name'] + ' identity')
        derived[mode] = {'state': 'observed-and-derived', 'identity': identity, 'inputs': inputs,
                         'method': 'public report template; only running-image digest replaced, verified against both actual protocol routes'}
    report = {'schema': 'cairn.semantic-package-local-fixtures/1', 'positives': rows,
              'observer_controls': control_report,
              'derivations': derived, 'managed_host_inventory': 'unobserved',
              'cpu_kernel_class': 'unobserved; bounded native observations retained in derivation inputs',
              'clean_root': 'standalone package; no development checkout; Python/bootstrap tools visible',
              'qualification': 'incomplete; local fixtures do not establish 6*N supported-class acceptance'}
    report['negative_cases'] = negative_fixtures(output, source, build, args.model_dir, libraries,
                                                provenance, dependencies, driver)
    report['negative_cases'].extend(shadow_fixtures(inventory, driver, rows, staged))
    report['live_staging'] = live_staging(root, staged, source, build, args.model_dir, libraries,
                                         provenance, dependencies, driver, request, output)
    (output / 'local-report.json').write_bytes(package.canonical(report) + b'\n')
    manifest = package.read_json(staged['manifest'])
    manifest['derivations'] = derived
    manifest['qualification']['local_fixtures'] = {'positive_runs': len(rows),
        'clean_root': report['clean_root'],
        'report_sha256': package.digest(package.canonical(report) + b'\n'),
        'actual_loaded_native_images': 'all four selected hashes, mapped sealed memfds in each run',
        'worker_connect_and_telemetry_writes': 'zero in scoped syscall captures'}
    with package.stage_root(root):
        address = package.digest(package.canonical(manifest))
        path = package.publish_tree(root / 'manifests', address, {'manifest.json': package.canonical(manifest) + b'\n'})
    (output / 'observed-manifest-path').write_text(str(path / 'manifest.json') + '\n')
    print(json.dumps({'positive_runs': len(rows), 'manifest': str(path / 'manifest.json'),
                      'rank_identity': derived['rank']['identity'], 'embed_identity': derived['embed']['identity'],
                      'qualification': report['qualification']}), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source-root', 'build', 'model-dir', 'provenance', 'rank-request', 'output', *package.LIBRARIES):
        p.add_argument('--' + name, required=True)
    p.add_argument('--probe', required=True)
    p.add_argument('--go-driver', required=True)
    run(p.parse_args())
