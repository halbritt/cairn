"""Local package fixtures with actual model, native mappings and Go supervisors.

Run inside the campaign wrapper. External assets/provenance are explicit inputs;
no managed-host inventory or fleet acceptance is inferred from this local run.
"""
import argparse
import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import mmap
import os
from pathlib import Path
import pwd
import re
import select
import shutil
import signal
import stat
import struct
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


def worker_root_visibility(proc, *, standalone=True):
    checkout = ((proc / 'root/work/go.mod').exists() or (proc / 'root/work/.git').exists())
    python = bool(list((proc / 'root/usr/bin').glob('python*')))
    if standalone:
        require(not checkout, 'development checkout exposed in standalone fixture')
    return checkout, python


def process_snapshot(payload, known, *, ancestor=None, minimal_root=False, standalone=True, strict_selection=None):
    if strict_selection is not None:
        return strict_process_snapshots(payload, known, ancestor, strict_selection)
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
                with fd.open('rb') as stream:
                    info = os.fstat(stream.fileno())
                    sha = held_file_record(stream.fileno())['sha256']
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
                    with actual.open('rb') as stream:
                        info = os.fstat(stream.fileno())
                        proof = held_file_record(stream.fileno())
                        require(info.st_ino == int(fields[4]) and
                                held_mapping_device(stream.fileno(), info) == fields[3],
                                'mapped system object changed: ' + path)
                    native[path] = {'path': path, 'sha256': proof['sha256'],
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
            checkout_visible, python_visible = worker_root_visibility(proc, standalone=standalone)
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


def file_signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def retain_object(fd, handles):
    handles.append((fd, file_signature(os.fstat(fd))))
    return fd


def held_file_record(fd):
    """Read bytes and metadata through one open object, never a pathname stat."""
    before = os.fstat(fd)
    require(stat.S_ISREG(before.st_mode), 'opened object is not a regular file')
    header = os.pread(fd, 64, 0)
    checksum, offset = hashlib.sha256(), 0
    while True:
        chunk = os.pread(fd, 1024 * 1024, offset)
        if not chunk:
            break
        checksum.update(chunk)
        offset += len(chunk)
    require(file_signature(before) == file_signature(os.fstat(fd)) and offset == before.st_size,
            'opened object changed during read')
    elf = (len(header) >= 52 and header[:4] == b'\x7fELF' and header[4] in (1, 2) and
           header[5] in (1, 2) and int.from_bytes(header[16:18], 'little' if header[5] == 1 else 'big') in (2, 3))
    return {'sha256': checksum.hexdigest(), 'elf': elf, 'device': before.st_dev,
            'inode': before.st_ino, 'bytes': before.st_size, 'mode': stat.S_IMODE(before.st_mode),
            'uid': before.st_uid, 'signature': list(file_signature(before))}


def parse_process_maps(raw):
    rows = []
    for line in raw.splitlines():
        fields = line.split(maxsplit=5)
        require(len(fields) in (5, 6), 'malformed maps row: ' + line)
        require(re.fullmatch(r'[0-9a-f]+-[0-9a-f]+', fields[0]) and
                re.fullmatch(r'[r-][w-][x-][ps]', fields[1]) and
                re.fullmatch(r'[0-9a-f]+', fields[2]) and
                re.fullmatch(r'[0-9a-f]+:[0-9a-f]+', fields[3]) and fields[4].isdigit(),
                'malformed maps fields: ' + line)
        low, high = (int(address, 16) for address in fields[0].split('-'))
        require(low < high, 'invalid maps interval')
        rows.append({'range': fields[0], 'permissions': fields[1], 'offset': fields[2],
                     'device': fields[3], 'inode': int(fields[4]), 'path': fields[5] if len(fields) == 6 else '',
                     'raw': line})
    require(rows, 'empty maps inventory')
    return rows


def held_mapping_device(fd, info):
    """Btrfs stat dev differs from maps dev; use a readonly nonexec same-FD map.

    This is the maintained check_semantic_python_closure.mapped_file method,
    using the observer's FD path so caller mount namespaces need not coincide.
    """
    path = os.readlink('/proc/self/fd/' + str(fd))
    with mmap.mmap(fd, 0, prot=mmap.PROT_READ):
        devices = {row['device'] for row in parse_process_maps(Path('/proc/self/maps').read_text())
                   if row['inode'] == info.st_ino and row['path'] == path and
                   os.readlink('/proc/self/map_files/' + map_files_name(row)) == path}
    require(len(devices) == 1, 'same-open-file mapping device unavailable')
    return devices.pop()


def map_files_name(row):
    # maps pads low addresses; map_files directory entries do not.
    return '-'.join(format(int(address, 16), 'x') for address in row['range'].split('-'))


def proc_text(procfd, name):
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC, dir_fd=procfd)
    with os.fdopen(fd, 'r') as stream:
        return stream.read()


def process_epoch(procfd, pid):
    text = proc_text(procfd, 'stat')
    fields = text[text.rindex(')') + 2:].split()
    status = proc_text(procfd, 'status')
    match = re.search(r'^Uid:\s+(.+)$', status, re.MULTILINE)
    require(len(fields) >= 20 and match is not None, 'malformed process identity')
    uids = [int(uid) for uid in match[1].split()]
    require(uids == [os.getuid()] * 4, 'foreign or privileged process')
    return {'pid': pid, 'ppid': int(fields[1]), 'starttime': int(fields[19]), 'uids': uids}


def proc_identity(procfd, pid):
    return process_epoch(procfd, pid) | {
        'namespaces': {name: os.readlink('ns/' + name, dir_fd=procfd) for name in ('mnt', 'pid', 'user')}}


def classify_executable(row, proof, selection):
    result = dict(row, classification='unknown', reason='no admitted executable mapping evidence')
    if row['path'] in ('[vdso]', '[vsyscall]'):
        if row['device'] == '00:00' and row['inode'] == 0:
            result.update(classification='kernel_virtual_mapping', reason='exact Linux kernel virtual range; no backing-file claim')
        return result
    if proof is None or not proof['elf']:
        result['reason'] = 'held FD unavailable or backing object is not ELF'
        return result
    major, minor = (int(part, 16) for part in row['device'].split(':'))
    if proof['inode'] != row['inode'] or proof['mapped_device'] != row['device']:
        result['reason'] = 'held FD device/inode differs from mapped object'
        return result
    result['reason'] = 'ELF path/hash/seals are outside the explicit selected class'
    if row['path'].startswith('/memfd:'):
        if (proof.get('seals') == 15 and proof['sha256'] in set(selection['native'].values()) and
                proof['device'] == os.makedev(major, minor)):
            result.update(classification='pinned_native_memfd', reason='same held FD hash/seals15/device/inode match selected native bytes', evidence=proof)
    elif row['path'] in selection['payload_files']:
        if proof['sha256'] == selection['payload_files'][row['path']]:
            result.update(classification='pinned_payload_elf', reason='same held mapped ELF matches selected payload pin', evidence=proof)
    elif row['path'] in selection['system_libraries'] and proof['sha256'] == selection['system_libraries'][row['path']]:
        result.update(classification='named_system_elf', reason='named selected system ELF with same held FD identity/hash', evidence=proof)
    return result


def mapped_object(procfd, row, handles):
    require(row['path'].startswith('/') and not row['path'].endswith(' (deleted)'), 'deleted or unnamed file has no held proof')
    require(os.readlink('map_files/' + map_files_name(row), dir_fd=procfd) == row['path'], 'mapped link changed')
    fd = os.open('root/' + row['path'].lstrip('/'), os.O_RDONLY | os.O_CLOEXEC, dir_fd=procfd)
    retain_object(fd, handles)
    info = os.fstat(fd)
    require(info.st_ino == row['inode'], 'mapped inode differs from opened object')
    proof = held_file_record(fd)
    proof['mapped_device'] = held_mapping_device(fd, info)
    require(proof['mapped_device'] == row['device'], 'mapped device differs from same opened object')
    require(os.readlink('map_files/' + map_files_name(row), dir_fd=procfd) == row['path'], 'mapped link drift')
    return proof


def native_objects(procfd, rows, handles):
    proofs = {}
    directory = '/proc/self/fd/' + str(procfd) + '/fd'
    for number in os.listdir(directory):
        target = os.readlink('fd/' + number, dir_fd=procfd)
        if not target.startswith('/memfd:'):
            continue
        fd = os.open('fd/' + number, os.O_RDONLY | os.O_CLOEXEC, dir_fd=procfd)
        retain_object(fd, handles)
        info = os.fstat(fd)
        key = (info.st_dev, info.st_ino)
        matching = [row for row in rows if row['inode'] == info.st_ino and
                    os.makedev(*(int(part, 16) for part in row['device'].split(':'))) == info.st_dev]
        if not matching:
            continue
        proof = held_file_record(fd)
        proof.update(seals=fcntl.fcntl(fd, fcntl.F_GET_SEALS), mapped_device=matching[0]['device'],
                     maps=[dict(row) for row in matching], descriptor_locator=number)
        require(proof['signature'] == list(file_signature(os.fstat(fd))), 'native FD changed after seal read')
        proofs[key] = proof
    return proofs


def capture_executable_maps(procfd, raw, selection, handles):
    rows = parse_process_maps(raw)
    native = native_objects(procfd, rows, handles)
    files, classified = {}, []
    for row in rows:
        if 'x' not in row['permissions']:
            continue
        proof = None
        try:
            if row['path'].startswith('/memfd:'):
                major, minor = (int(part, 16) for part in row['device'].split(':'))
                proof = native.get((os.makedev(major, minor), row['inode']))
            elif row['path'].startswith('/'):
                key = (row['device'], row['inode'], row['path'])
                if key not in files:
                    files[key] = mapped_object(procfd, row, handles)
                proof = files[key]
            classified.append(classify_executable(row, proof, selection))
        except (OSError, RuntimeError) as error:
            classified.append(dict(row, classification='unknown', reason=str(error)))
    expected = set(selection['native'].values())
    selected_native = {proof['sha256'] for proof in native.values() if proof['seals'] == 15 and
                       any('x' in row['permissions'] for row in proof['maps'])}
    return classified, list(native.values()), selected_native == expected and len(native) == 4


def strict_snapshot(pid, selection, ancestor):
    """Finite full-map witness. Does not attest private COW pages or continuous memory."""
    report = {'pid': pid, 'scope': 'strict_full_executable_maps', 'qualified': False,
              'raw_maps': None, 'raw_maps_after': None, 'executable_mappings': [], 'errors': [],
              'limits': 'Backing-file custody does not attest private COW pages or continuous memory.'}
    handles, procfd = [], None
    try:
        require(ancestor is not None and descendant_of(ancestor, os.getpid()) and descendant_of(pid, ancestor),
                'process is not owned by selected case')
        require(set(selection['native']) == set(package.LIBRARIES) and len(set(selection['native'].values())) == 4,
                'strict capture needs four distinct selected native hashes')
        require(selection['payload_files'].get(selection['worker']['path']) == selection['worker']['sha256'],
                'worker must have an explicit selected payload pin')
        procfd = os.open('/proc/' + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        before = proc_identity(procfd, pid)
        report['identity_before'] = before
        report['raw_maps'] = proc_text(procfd, 'maps')
        report['executable_mappings'] = [dict(row, classification='unknown', reason='full capture incomplete')
                                         for row in parse_process_maps(report['raw_maps']) if 'x' in row['permissions']]
        exe = os.open('exe', os.O_RDONLY | os.O_CLOEXEC, dir_fd=procfd)
        retain_object(exe, handles)
        report['worker_elf'] = held_file_record(exe)
        require(os.readlink('exe', dir_fd=procfd) == selection['worker']['path'] and
                report['worker_elf']['sha256'] == selection['worker']['sha256'] and report['worker_elf']['elf'],
                'running ELF differs from explicitly selected worker')
        env = dict(entry.split('=', 1) for entry in proc_text(procfd, 'environ').strip('\0').split('\0') if entry)
        require(env == selection['worker_environment'], 'selected worker environment differs')
        mappings, native, complete = capture_executable_maps(procfd, report['raw_maps'], selection, handles)
        report.update(executable_mappings=mappings, native_images=native, native_set_complete=complete)
        report['raw_maps_after'] = proc_text(procfd, 'maps')
        after = proc_identity(procfd, pid)
        report['identity_after'] = after
        require(all(before_signature == file_signature(os.fstat(fd)) for fd, before_signature in handles),
                'held object changed before capture boundary')
        require(before == after and descendant_of(pid, ancestor), 'process/starttime/namespace/ancestry changed')
        require(report['raw_maps'] == report['raw_maps_after'], 'maps changed during held-FD capture')
        require(not any(row['classification'] == 'unknown' for row in mappings), 'unknown executable mapping')
        require(complete, 'selected four sealed native images incomplete')
        report['qualified'] = True
    except (OSError, RuntimeError, ValueError) as error:
        report['errors'].append({'type': type(error).__name__, 'message': str(error)})
        for row in report['executable_mappings']:
            if row['reason'] == 'full capture incomplete':
                row['reason'] = 'capture stopped before held proof: ' + str(error)
    finally:
        for fd, _ in handles:
            os.close(fd)
        if procfd is not None:
            os.close(procfd)
    return report


def strict_process_snapshots(payload, known, ancestor, selection):
    require(ancestor is not None, 'strict capture requires an owned case ancestor')
    require(Path(payload).resolve() == Path(selection['payload']).resolve(), 'strict payload selection differs')
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if int(proc.name) in known:
                fd = os.open(proc, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
                try:
                    require(proc_identity(fd, int(proc.name)) == known[int(proc.name)]['identity_after'],
                            'previously captured process identity changed or PID reused')
                finally:
                    os.close(fd)
                continue
            argv = (proc / 'cmdline').read_bytes().strip(b'\0').split(b'\0')
            if not argv or argv[0] != os.fsencode(selection['worker']['path']):
                continue
            if not descendant_of(int(proc.name), ancestor):
                continue
            found.append(strict_snapshot(int(proc.name), selection, ancestor))
        except (FileNotFoundError, ProcessLookupError):
            continue  # No candidate witness is created for an exited selection scan.
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


def private_json(path, document):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(package.canonical(document) + b'\n')


def selected_file(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        record = held_file_record(fd)
        require(record['sha256'] == expected, 'selected bytes differ: ' + str(path))
        return record
    finally:
        os.close(fd)


def tree_record(root):
    root = Path(root)
    try:
        root.lstat()
    except FileNotFoundError:
        return {'root': str(root), 'state': 'absent', 'sha256': package.digest(b'absent')}
    def failed_walk(error):
        raise error
    paths = [root]
    for directory, dirs, files in os.walk(root, onerror=failed_walk, followlinks=False):
        paths.extend(Path(directory) / name for name in dirs + files)
    rows = {}
    for path in sorted(paths):
        info = path.lstat()
        row = {'mode': stat.S_IMODE(info.st_mode), 'uid': info.st_uid, 'gid': info.st_gid,
               'inode': info.st_ino, 'device': info.st_dev, 'mtime_ns': info.st_mtime_ns}
        if stat.S_ISLNK(info.st_mode):
            row.update(kind='symlink', target=os.readlink(path))
        elif stat.S_ISREG(info.st_mode):
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                proof = held_file_record(fd)
                require(list(file_signature(info)) == proof['signature'], 'tree object changed before open: ' + str(path))
                row.update(kind='file', **proof)
            finally:
                os.close(fd)
        else:
            require(stat.S_ISDIR(info.st_mode), 'unsupported tree object: ' + str(path))
            row['kind'] = 'directory'
        rows[str(path.relative_to(root))] = row
    return {'root': str(root), 'entries': rows, 'sha256': package.digest(package.canonical(rows))}


def staged_selection(selection):
    """Selection schema for --staged-case; all paths/hashes are operator inputs.

    Required: schema=cairn.semantic-staged-case/1, mode=embed|rank|stream,
    payload, payload_files={absolute-path:SHA256}, worker={path,sha256},
    native={ort,openblas,gfortran,quadmath:SHA256}, system_libraries={path:SHA256},
    worker_environment={exact-child-environment}, driver/launcher/input={path,sha256},
    private_home (owned0700/empty or absent; must match worker HOME),
    expected_identity={model_sha256,algorithm[,dimensions]},
    expected_projection (embed), timeout_seconds (positive, at most90).
    No build/stage/probe/control route is available through this entry.
    """
    require(selection['schema'] == 'cairn.semantic-staged-case/1', 'wrong staged-case schema')
    require(selection['mode'] in ('embed', 'rank', 'stream'), 'unsupported staged mode')
    expected = selection['expected_identity']
    require(re.fullmatch(r'[0-9a-f]{64}', expected['model_sha256']) is not None, 'expected identity must be selected SHA256')
    require(expected['algorithm'] == ('bge-original-passages/1' if selection['mode'] == 'embed' else 'bge-max-chunk/1'),
            'unsupported semantic algorithm')
    if selection['mode'] == 'embed':
        require(expected['dimensions'] == 384 and isinstance(selection['expected_projection'], dict), 'expected embedding schema missing')
    require(0 < selection['timeout_seconds'] <= 90, 'case budget must be positive and at most90 seconds')
    require(set(selection['native']) == set(package.LIBRARIES) and len(set(selection['native'].values())) == 4,
            'exactly four distinct native pins required')
    require(Path(selection['payload']).is_absolute(), 'selected payload must be absolute')
    payload = Path(selection['payload']).resolve(strict=True)
    require(payload.is_dir(), 'staged payload missing')
    for path, checksum in selection['payload_files'].items():
        require(Path(path).is_absolute() and Path(path).resolve(strict=True).is_relative_to(payload),
                'payload pin outside selected stage')
        selected_file(path, checksum)
    require(selection['payload_files'].get(selection['worker']['path']) == selection['worker']['sha256'],
            'worker is not explicitly payload-pinned')
    for name, checksum in selection['native'].items():
        require(selection['payload_files'].get(str(payload / 'native' / name)) == checksum,
                'native input is not explicitly payload-pinned: ' + name)
    for name in ('driver', 'launcher', 'input'):
        require(Path(selection[name]['path']).is_absolute(), 'selected command/input path must be absolute')
        selected_file(selection[name]['path'], selection[name]['sha256'])
    for path, checksum in selection['system_libraries'].items():
        require(Path(path).is_absolute() and selected_file(path, checksum)['elf'], 'system library is not a selected ELF')
    home = Path(selection['private_home'])
    require(home.is_absolute() and not home.is_symlink() and home.resolve() != Path.home().resolve(), 'invalid private application HOME')
    env = selection['worker_environment']
    require(env.get('HOME') == str(home), 'selected private HOME differs from actual worker HOME')
    for key, value in package.worker_environment().items():
        if key.endswith('_THREADS') or key in ('ORT_DISABLE_TELEMETRY', 'HF_HUB_OFFLINE', 'HF_HUB_DISABLE_TELEMETRY'):
            require(env.get(key) == value, 'selected worker guard differs: ' + key)
    require(not any(key.startswith(('LD_', 'DYLD_')) for key in env), 'selected child loader override refused')
    try:
        info = home.lstat()
    except FileNotFoundError:
        info = None
    if info is not None:
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700 and
                not list(home.iterdir()), 'private application HOME must be owned0700 and empty or absent')
    raw = Path(selection['input']['path']).read_bytes()
    require(raw.endswith(b'\n') and len(raw.splitlines()) == 1, 'case input must be one finite JSON line')
    json.loads(raw)
    return selection


def case_descendants(ancestor):
    identities = {}
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or not descendant_of(int(proc.name), ancestor):
            continue
        try:
            fd = os.open(proc, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                identities[int(proc.name)] = proc_identity(fd, int(proc.name))
            finally:
                os.close(fd)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return identities


def signal_owned_process(pid, expected):
    handle = os.pidfd_open(pid)
    try:
        fd = os.open('/proc/' + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            actual = process_epoch(fd, pid)
        finally:
            os.close(fd)
        require(actual['starttime'] == expected['starttime'], 'refusing reused process identity')
        signal.pidfd_send_signal(handle, signal.SIGKILL)
    finally:
        os.close(handle)


def track_case_processes(tracked, ancestor):
    for pid, fresh in case_descendants(ancestor).items():
        require(pid not in tracked or tracked[pid] == fresh, 'refusing changed owned process identity')
        tracked.setdefault(pid, fresh)


def stop_owned_case(child, tracked):
    """Kill witnessed descendants first, let their owning supervisor reap them."""
    if child.poll() is None:
        track_case_processes(tracked, child.pid)
    errors = []
    for pid, expected in reversed(list(tracked.items())):
        if pid == child.pid:
            continue
        try:
            signal_owned_process(pid, expected)
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, RuntimeError) as error:
            errors.append(str(error))
    try:
        child.wait(timeout=1)
    except subprocess.TimeoutExpired:
        require(child.pid in tracked, 'case root identity was not witnessed')
        signal_owned_process(child.pid, tracked[child.pid])
        child.wait(timeout=5)
    for pid, expected in tracked.items():
        try:
            fd = os.open('/proc/' + str(pid), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                actual = process_epoch(fd, pid)
            finally:
                os.close(fd)
            require(actual['starttime'] != expected['starttime'], 'owned descendant not reaped: ' + str(pid))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except (OSError, RuntimeError) as error:
            errors.append(str(error))
    require(not errors, 'owned case cleanup incomplete: ' + repr(errors))
    return {'tracked': list(tracked.values()), 'all_witnessed_processes_gone': True}


def staged_reply(selection, request, reply):
    require('error' not in reply and reply['id'] == request['id'], 'case error or reply id differs')
    if selection['mode'] == 'embed':
        require(reply['identity'] == selection['expected_identity'], 'embedding identity differs')
        require(reply['query_projection'] == selection['expected_projection'], 'query projection differs')
        vector = reply['vector']
        require(len(vector) == 384 and all(type(number) in (float, int) and math.isfinite(number) for number in vector),
                'embedding vector not384 finite components')
        norm = sum(float(struct.unpack('f', struct.pack('f', number))[0]) ** 2 for number in vector)
        require(any(vector) and abs(norm - 1) <= 1e-5, 'embedding vector fails selected unit criterion')
        return {'squared_norm': norm, 'criterion': 1e-5}
    result = reply['result']
    require({key: result[key] for key in ('model_sha256', 'algorithm')} == selection['expected_identity'],
            'rank identity differs')
    notes = request['request']['notes']
    require(len(result['scores']) == len(notes), 'score count differs')
    for score, note in zip(result['scores'], notes):
        require(type(score['score']) is int and all(score[key] == note[key] for key in ('record_id', 'version', 'body_sha256')),
                'rank score/join differs')
    return {'scores': result['scores']}


def case_trace_inventory(output):
    traces = sorted(output.glob('case.trace.*'))
    require(traces, 'complete descendant raw trace missing')
    audit, connects = [], []
    for path in traces:
        for line in path.read_text().splitlines():
            # A superset for a HUMAN reader, never an automatic filesystem effect verdict.
            if re.search(r'\b(?:open\w*|creat|mkdir\w*|rmdir|mknod\w*|write\w*|pwrite\w*|\w*truncate\w*|rename\w*|unlink\w*|link\w*|symlink\w*|\w*chmod\w*|\w*chown\w*|\w*utime\w*|fallocate|copy_file_range|sendfile\w*|splice|tee|fsync|fdatasync|msync|sync\w*)\(|resumed>', line):
                audit.append(path.name + ': ' + line)
            if re.match(r'^\s*(?:\d+\s+)?(?:connect\(|<\.\.\. connect resumed>)', line):
                connects.append(path.name + ': ' + line)
    fd = os.open(output / 'human-write-audit.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write('\n'.join(audit) + '\n')
    return {'raw_files': [str(path) for path in traces], 'connect_lines': connects,
            'persistent_effects': 'PENDING named human reading of complete raw traces/audit and before/after trees'}


def run_staged_case(args):
    output = Path(args.output)
    output.mkdir(mode=0o700)
    report = {'status': 'FAILED', 'samples': [], 'errors': [], 'runtime_attempts': 0}
    child, tracked, known = None, {}, {}
    old_umask = os.umask(0o077)
    try:
        selection = json.loads(Path(args.staged_case).read_bytes())
        private_json(output / 'selection.json', selection)
        staged_selection(selection)
        require(not output.resolve().is_relative_to(Path(selection['payload']).resolve()) and
                not output.resolve().is_relative_to(Path(selection['private_home']).resolve()), 'evidence output overlaps selected trees')
        report['trees_before'] = {key: tree_record(selection[key]) for key in ('payload', 'private_home')}
        argv = ['/usr/bin/strace', '-ff', '-qq', '-s', '0', '-yy', '-e',
                'trace=process,connect,%file,%desc,mmap,mprotect,munmap', '-o', str(output / 'case.trace'),
                selection['driver']['path'], '--mode', selection['mode'], '--launcher', selection['launcher']['path']]
        private_json(output / 'command.json', {'argv': argv, 'stdin': selection['input'], 'timeout_seconds': selection['timeout_seconds']})
        with open(selection['input']['path'], 'rb') as stdin, open(output / 'stdout.jsonl', 'xb') as stdout, open(output / 'stderr', 'xb') as stderr:
            input_proof = held_file_record(stdin.fileno())
            raw = stdin.read()
            require(input_proof['sha256'] == package.digest(raw) == selection['input']['sha256'], 'finite input changed before launch')
            request = json.loads(raw)
            stdin.seek(0)
            with open(output / 'input.jsonl', 'xb') as copy:
                copy.write(raw)
            child = subprocess.Popen(argv, stdin=stdin, stdout=stdout, stderr=stderr, start_new_session=True)
            report['runtime_attempts'] = 1
            deadline = time.monotonic() + selection['timeout_seconds']
            while child.poll() is None:
                require(time.monotonic() < deadline, 'owned staged case timed out')
                track_case_processes(tracked, child.pid)
                samples = process_snapshot(Path(selection['payload']), known, ancestor=child.pid,
                                           standalone=False, strict_selection=selection)
                report['samples'].extend(samples)
                for row in samples:
                    require(not any(mapping['classification'] == 'unknown' for mapping in row['executable_mappings']), 'unknown executable range in case')
                    require(all(error['message'] in ('maps changed during held-FD capture',
                                                     'selected four sealed native images incomplete') for error in row['errors']),
                            'missing or failed snapshot facts: ' + repr(row['errors']))
                    if row['qualified']:
                        known[row['pid']] = row
                time.sleep(0.01)
            report['returncode'] = child.wait()
            require(input_proof['signature'] == list(file_signature(os.fstat(stdin.fileno()))), 'finite input changed during case')
        require(report['returncode'] == 0 and known, 'case failed or no complete owned snapshot')
        replies = [json.loads(line) for line in (output / 'stdout.jsonl').read_text().splitlines()]
        require(len(replies) == 1, 'exactly one reply required')
        report['functional'] = staged_reply(selection, request, replies[0])
        report['trace'] = case_trace_inventory(output)
        require(not report['trace']['connect_lines'], 'connect attempt observed')
        report['status'] = 'CAPTURED_PENDING_HUMAN_EFFECT_AND_ADEQUACY_REVIEW'
    except BaseException as error:
        report['errors'].append({'type': type(error).__name__, 'message': str(error)})
        raise
    finally:
        try:
            if child is not None:
                report['cleanup'] = {'tracked': list(tracked.values()), 'all_witnessed_processes_gone': False}
                report['cleanup'] = stop_owned_case(child, tracked)
                if 'trace' not in report and list(output.glob('case.trace.*')):
                    report['trace'] = case_trace_inventory(output)
            if 'trees_before' in report:
                report['trees_after'] = {key: tree_record(selection[key]) for key in ('payload', 'private_home')}
                require(report['trees_before'] == report['trees_after'], 'selected stage/private HOME changed')
        except BaseException as error:
            report['status'] = 'FAILED'
            if child is not None:
                report['cleanup']['tracked'] = list(tracked.values())
            report['errors'].append({'type': type(error).__name__, 'message': str(error)})
            raise
        finally:
            private_json(output / 'receipt.json', report)
            os.umask(old_umask)
    return report


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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if any(arg == '--staged-case' or arg.startswith('--staged-case=') for arg in argv):
        parser = argparse.ArgumentParser(description=staged_selection.__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
        parser.add_argument('--staged-case', required=True, help='caller-selected pinned JSON; no build or staging')
        parser.add_argument('--output', required=True, help='NEW private evidence directory')
        run_staged_case(parser.parse_args(argv))
        return
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('source-root', 'build', 'model-dir', 'provenance', 'rank-request', 'output', *package.LIBRARIES):
        p.add_argument('--' + name, required=True)
    p.add_argument('--probe', required=True)
    p.add_argument('--go-driver', required=True)
    run(p.parse_args(argv))


if __name__ == '__main__':
    main()
