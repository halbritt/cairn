"""Inside-wrapper preparation and observation for explicitly selected S05 cases."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import traceback


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def restore_private_rust_modes(stage):
    """Safe tar exports omit directory modes; restore the sealed format on a copy."""
    for category in ('payloads', 'launchers', 'manifests'):
        for generation in (stage / category).iterdir():
            for base, directories, files in os.walk(generation):
                directory = Path(base)
                for name in [*directories, *files]:
                    if (directory / name).is_symlink():
                        raise RuntimeError('Rust immutable generation contains a symlink')
                for name in files:
                    path = directory / name
                    executable = category == 'launchers' or path.relative_to(generation).parts[0] == 'bin'
                    path.chmod(0o555 if executable else 0o444)
                directory.chmod(0o555)


def prepare(artifacts, *, sample_checkpoint=False):
    package = load('transition_package', '/work/scripts/semantic_package.py')
    closure = load('transition_closure', '/work/scripts/semantic_python_closure.py')
    python = json.loads(Path('/opt/s05-python-candidate.json').read_bytes())
    if python['payload']['address'] != 'c6c1bd58dccd51ae1b06c0213c94ef9f87206a80951fc198ff0674654b0c7221' or python['controls']['address'] != 'c3ca7679ff400d9d075dd870007224204839cb9c0ba09bdbff5fc2b462a24cc1':
        raise RuntimeError('not the accepted retained Python selection')
    for role in ('payload', 'controls'):
        closure.verify_generation(python[role]['path'], python[role]['descriptor'])
    stage = json.loads(Path('/opt/s05-rust-result.json').read_bytes())
    target = Path(stage['payload']).parents[1]
    if str(target) != '/tmp/semantic-package-fixtures/stage':
        raise RuntimeError('retained Rust absolute selection differs')
    target.parent.mkdir(mode=0o700)
    shutil.copytree('/opt/s05-rust-stage', target, copy_function=shutil.copy2, symlinks=True)
    restore_private_rust_modes(target)
    original = package.tree_records(Path(stage['payload']))
    descriptor = json.loads((Path(stage['payload']) / 'payload.json').read_bytes())
    expected = dict(descriptor['files'])
    expected['payload.json'] = package.file_record((Path(stage['payload']) / 'payload.json').read_bytes())
    if original != expected:
        raise RuntimeError('retained Rust payload content/modes differ')
    rust_manifest = json.loads(Path(stage['manifest']).read_bytes())
    for item in rust_manifest['requirements']['host_abi_prerequisites']:
        if package.digest(Path(item['path']).read_bytes()) != item['sha256']:
            raise RuntimeError('retained Rust ABI prerequisite differs: ' + item['path'])
    for home in ('/tmp/s04r-worker-home', '/nonexistent'):
        path = Path(home)
        path.mkdir(mode=0o700, exist_ok=True)
        if list(path.iterdir()):
            raise RuntimeError('worker HOME/cache not initially empty: ' + home)
    trace_prefix = str(artifacts / 'worker-trace')
    selected = {'trace_prefix': trace_prefix}
    if sample_checkpoint:
        selected['sample_pids'] = str(artifacts / 'worker-sample-pids.json')
    for label, launchers, rank, embed in (
        ('python', python['controls']['launchers'], python['expected_identities']['rank'],
         python['expected_identities']['embed']['model_sha256']),
        ('rust', {mode: item['path'] for mode, item in stage['launchers'].items()},
         '3dc4ce117382b3953f986aafeccac4f25660c848f9b00e43f34f4d2d4a1cf0e7',
         '4b95900aba686cffb0d0b09f4ece54c54de954341588752a8b992333b58dafdb')):
        hashes = {path: package.digest(Path(path).read_bytes()) for path in launchers.values()}
        if label == 'rust' and any(hashes[item['path']] != item['sha256'] for item in stage['launchers'].values()):
            raise RuntimeError('retained Rust launcher bytes differ')
        selected[label] = {'rank': launchers['rank'], 'embed': launchers['embed'],
                           'rank_identity': rank, 'embed_identity': embed, 'launcher_sha256': hashes}
    (artifacts / 'real-selection.json').write_text(json.dumps(selected, indent=2) + '\n')
    (artifacts / 'prepared-inputs.json').write_text(json.dumps({
        'verified_before_build_and_workers': True,
        'python_candidate_sha256': package.digest(Path('/opt/s05-python-candidate.json').read_bytes()),
        'rust_stage_result_sha256': package.digest(Path('/opt/s05-rust-result.json').read_bytes()),
        'rust_manifest_sha256': package.digest(Path(stage['manifest']).read_bytes()),
        'python_generations_verified': [python[role]['address'] for role in ('payload', 'controls')],
        'rust_payload_records_verified': original,
        'selection': selected}, indent=2) + '\n')
    return selected, python, Path(stage['payload'])


def worker_trace_scope(traces, launchers):
    """Follow recorded clone/fork edges, including short-lived worker threads."""
    roots, children = set(), {}
    for pid, text in traces.items():
        if any('execve(' + json.dumps(path) + ',' in text for path in launchers):
            roots.add(pid)
        edges = children.setdefault(pid, set())
        for line in text.splitlines():
            if re.search(r'(?:clone3?|fork|vfork)(?:\(| resumed>)', line):
                returned = re.search(r'=\s*(\d+)\s*$', line)
                if returned:
                    edges.add(int(returned.group(1)))
    selected = set(roots)
    pending = list(roots)
    while pending:
        for child in children.get(pending.pop(), set()) - selected:
            selected.add(child)
            pending.append(child)
    return roots, selected


def require_worker_samples(roots_by_label, samples_by_label):
    """Every executed worker needs its own mapped-byte/environment observation."""
    for label in ('python', 'rust'):
        roots = {int(pid) for pid in roots_by_label[label]}
        samples = {int(pid) for pid in samples_by_label[label]}
        if roots != samples:
            raise RuntimeError(label + ' worker root/sample mismatch: traced=' +
                               repr(sorted(roots)) + ' sampled=' + repr(sorted(samples)))


def ready_sample_pids(python_workers, rust_workers):
    """Faults wait for an actual verified runtime mapping, not launcher startup."""
    python = []
    for pid, row in python_workers.items():
        paths = [image['path'] for image in row['images']]
        if any('onnxruntime_pybind11_state' in path for path in paths) and any('numpy' in path for path in paths):
            python.append(pid)
    return {'python': sorted(python),
            'rust': sorted(pid for pid, row in rust_workers.items() if len(row['native_images']) == 4)}


def mapping_permission(proc, pending, exit_races, terminal):
    """Use the qualified S04R observer's bounded live/terminal race policy."""
    pid = int(proc.name)
    if terminal(proc):
        exit_races.add(pid)
        pending.pop(pid, None)
        return
    pending[pid] = pending.get(pid, 0) + 1
    if pending[pid] > 2:
        raise RuntimeError('live worker mapping access denied after two retries: ' + str(pid))


def run_observed(command, env, artifacts, selected, python, rust_payload):
    python_observer = load('transition_python_observer', '/work/scripts/check_semantic_python_closure.py')
    closure = load('transition_python_environment', '/work/scripts/semantic_python_closure.py')
    rust_observer = load('transition_rust_observer', '/work/scripts/test_semantic_package_real.py')
    python_observer.observer_controls(artifacts)
    done, errors = threading.Event(), []
    python_workers, rust_workers = {}, {}
    pending_permissions, exit_races = {}, set()
    payload = Path(python['payload']['path'])
    entries = python['payload']['descriptor']['entries']
    argv = ['/usr/bin/strace', '-ff', '-qq', '-s', '256', '-yy', '-e',
            'trace=process,connect,openat,openat2,creat,mkdir,mkdirat,write,writev,pwrite64,pwritev,ftruncate,truncate,rename,renameat,renameat2,unlink,unlinkat',
            '-o', selected['trace_prefix'], *command]
    (artifacts / 'real-test-command.json').write_text(json.dumps(argv, indent=2) + '\n')

    def sample():
        published = None
        try:
            while not done.is_set():
                for proc in Path('/proc').iterdir():
                    if not proc.name.isdigit() or not rust_observer.descendant_of(int(proc.name), child.pid):
                        continue
                    try:
                        for mode in ('rank', 'embed'):
                            row = python_observer.mapped_worker(proc, payload, mode)
                            if row is None:
                                continue
                            actual_env = dict(item.split('=', 1) for item in row['environment'])
                            if actual_env != closure.worker_environment(payload, Path('/tmp/s04r-worker-home'), mode):
                                raise RuntimeError('retained Python worker environment differs')
                            for mapping in row['images'] + row['data_mappings']:
                                path = Path(mapping['path'])
                                if not path.is_relative_to(payload):
                                    raise RuntimeError('Python mapping escaped retained payload: ' + str(path))
                                if entries[path.relative_to(payload).as_posix()]['sha256'] != mapping['sha256']:
                                    raise RuntimeError('Python mapped bytes differ: ' + str(path))
                            prior = python_workers.get(row['pid'])
                            if prior is None or len(row['images']) + len(row['data_mappings']) >= len(prior['images']) + len(prior['data_mappings']):
                                python_workers[row['pid']] = row
                                pending_permissions.pop(row['pid'], None)
                    except (FileNotFoundError, ProcessLookupError):
                        continue
                    except PermissionError:
                        mapping_permission(proc, pending_permissions, exit_races, python_observer.process_terminal)
                for row in rust_observer.process_snapshot(rust_payload, rust_workers, ancestor=child.pid, standalone=False):
                    rust_workers[row['pid']] = row
                if selected.get('sample_pids'):
                    ready = ready_sample_pids(python_workers, rust_workers)
                    if ready != published:
                        path = Path(selected['sample_pids'])
                        temporary = path.with_suffix('.tmp')
                        temporary.write_text(json.dumps(ready) + '\n')
                        temporary.replace(path)
                        published = ready
                for pid in list(pending_permissions):
                    if python_observer.process_terminal(Path('/proc') / str(pid)):
                        exit_races.add(pid)
                        pending_permissions.pop(pid)
                done.wait(0.02)
        except BaseException as error:
            errors.append(traceback.format_exc())

    with (artifacts / 'tests.jsonl').open('wb') as output:
        child = subprocess.Popen(argv, env=env, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
        thread = threading.Thread(target=sample)
        thread.start()
        try:
            while child.poll() is None:
                if errors:
                    if child.pid <= 1:
                        raise RuntimeError('refusing unsafe fixture process control')
                    if env.get('CAIRN_TRANSITION_REAL_CASE') == '2':
                        child.terminate()
                    else:
                        if os.getpgid(child.pid) != child.pid:
                            raise RuntimeError('refusing unsafe fixture process-group control')
                        os.killpg(child.pid, signal.SIGTERM)
                    child.wait(timeout=10)
                    break
                done.wait(0.1)
        finally:
            done.set()
            thread.join()
    observations = {'python_workers': list(python_workers.values()), 'rust_workers': list(rust_workers.values()),
                    'mapping_errors': errors, 'returncode': child.returncode,
                    'terminal_mapping_races': sorted(exit_races),
                    'unresolved_live_mapping_pids': [pid for pid in pending_permissions if not python_observer.process_terminal(Path('/proc') / str(pid))]}
    (artifacts / 'real-worker-observations.json').write_text(json.dumps(observations, indent=2) + '\n')
    if child.returncode or errors or observations['unresolved_live_mapping_pids']:
        raise RuntimeError('real transition execution/mapping failed: ' + repr(errors))
    traces = {int(path.name.rsplit('.', 1)[1]): path.read_text() for path in artifacts.glob('worker-trace.*')}
    launchers = [selected[label][mode] for label in ('python', 'rust') for mode in ('rank', 'embed')]
    roots, descendants = worker_trace_scope(traces, launchers)
    if descendants - traces.keys():
        raise RuntimeError('worker descendant trace missing: ' + repr(sorted(descendants - traces.keys())))
    roots_by_label = {label: worker_trace_scope(traces, [selected[label][mode] for mode in ('rank', 'embed')])[0]
                      for label in ('python', 'rust')}
    samples_by_label = {'python': python_workers, 'rust': rust_workers}
    effects = python_observer.effects('\n'.join(traces[pid] for pid in sorted(descendants)))
    (artifacts / 'worker-effects.json').write_text(json.dumps({
        'root_pids': sorted(roots), 'tree_pids': sorted(descendants),
        'roots_by_label': {label: sorted(pids) for label, pids in roots_by_label.items()},
        'sampled_pids_by_label': {label: sorted(pids) for label, pids in samples_by_label.items()},
        **effects}, indent=2) + '\n')
    require_worker_samples(roots_by_label, samples_by_label)
    if not roots or not python_workers or not rust_workers or effects['connects'] or effects['store_mutations']:
        raise RuntimeError('actual worker/effect evidence missing or nonzero')
    for home in ('/tmp/s04r-worker-home', '/nonexistent'):
        if list(Path(home).rglob('*')):
            raise RuntimeError('worker HOME/cache changed: ' + home)
    for role in ('payload', 'controls'):
        closure.verify_generation(python[role]['path'], python[role]['descriptor'])
    return observations
