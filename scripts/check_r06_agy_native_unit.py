"""Genuine authenticated Go /3 preparation and separate non-native STUB proof.

This composes the maintained Go /3 producer/public-control fixture and, in a
separate entry, the R04 tracer and current-generation collector. The fixed
Python stub does not produce Agy events, sessions, or hook callbacks. Genuine
receipts and authenticated API reads belong to the separate setup entry.
"""
import argparse
import ast
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid

import check_r04_native_agy as r04
import r04_agy_trace as trace
import r06_agy_current_observation as collector
import check_r06_unbound_roundtrip as roundtrip
from disposable_host import DisposableHost, OwnedCluster, pg_bindir, stop_postgres


ROOT = Path(__file__).resolve().parents[1]
BASE = '15080de3b1205b161faa66f60b8faf93c30f18da'
BASE_TREE = '11ec0c51352505930e64236a99b929767e71cc2c'
ROLES = ('api', 'control', 'stager', 'helper', 'observer')
CONTROLLED_PACKET = Path('/home/halbritt/.cache/cairn301-release-1465/r06-main-images-15080de3-controlled-20261114')
LINEAGE_SHA256 = '61cedd96993792cdc9d0d6becc3633e33c2a4361752f9b377f2bd3c3b2dc43b5'
LINEAGE_FINAL_SHA256 = '3a5ebeebde91562fd1bb99856ce661c99cbba739a9fb204877fcba7ab0dbae13'
AGY_SHA256 = '5df525a2d6908989c57b897723caea66e00d7c4d2e24411f91ad306989574642'
AGY_SOURCE = Path('/home/halbritt/.cache/cairn301-release-1383/agy-receipt-filetype-20261112') / ('agy-' + AGY_SHA256)
NATIVE_SCHEMA = 'cairn.r06-agy-native-admission/1'
LOCAL_PROVIDER = dict(kind='GeminiFixture', host='127.0.0.1', port=0,
                      api_key='r04-synthetic-unused', NO_PROXY='127.0.0.1,localhost')
REUSED = (
    'scripts/check_r04_native_agy.py', 'scripts/check_r04_native_opencode.py',
    'scripts/r04_agy_trace.py', 'scripts/r06_agy_current_observation.py',
    'scripts/test_r02_compatibility_install.py', 'scripts/test_r04_host_wiring.py',
    'scripts/stage_lifecycle_compatibility.py', 'scripts/stage_lifecycle_hooks.py',
    'scripts/check_r06_unbound_roundtrip.py', 'scripts/disposable_host.py',
    'scripts/check_r06_status_consumer.py',
)
STUB = (
    "import sys\n"
    "sys.stdout.buffer.write(b'CAIRN_PREPARATION_STUB_READY\\n')\n"
    "sys.stdout.buffer.flush()\n"
    "raw = sys.stdin.buffer.read()\n"
    "sys.stdout.buffer.write(b'CAIRN_PREPARATION_STUB_EOF=' + str(len(raw)).encode() + b'\\n')\n"
    "sys.stdout.buffer.flush()\n"
)
INPUT = b'CAIRN_NON_NATIVE_PREPARATION_INPUT\n'
STOP = (
    'PREREQUISITE_STOP: actual Agy entry remains CLOSED. The finite Go /3 '
    'target resource closure and private target observation do not resolve '
    'the live trace contract. agyTraceCalls rejects every unfinished syscall; '
    'the retained held-image idle prefix contains a native-thread read(0) '
    'that is still pending after fullyIdle Stop. Current readback also requires '
    'that native generation to remain live. No actual Agy pause/input-boundary '
    'contract is implemented or authorized by this checkpoint; nonnative '
    'kernel mechanics do not qualify Agy. Preserve complete raw '
    'prefixes, callback EOF/zero-exit and live-generation guards. No Agy, '
    'provider, API, PG or native ledger starts at this entry. Native0; '
    'retained Python /2 reverse remains unsupported.'
)


class Refused(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def checked_pin(path, expected=None):
    selected = collector.pin(Path(path))
    require(expected is None or selected['SHA256'] == expected, 'input pin mismatch')
    return selected


def tool_pin(path, expected=None):
    path = Path(path)
    info = path.lstat()
    require(path.is_absolute() and path.resolve() == path and stat.S_ISREG(info.st_mode)
            and info.st_uid in (0, os.getuid()) and info.st_mode & 0o022 == 0,
            'controlled normal-host tool image required')
    selected = dict(Path=str(path), SHA256=r04.digest(path))
    require(expected is None or selected['SHA256'] == expected, 'input pin mismatch')
    return selected


def private_directory(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(str(path)),
            'absolute normalized evidence directory required')
    require(path.resolve() == path, 'evidence directory cannot traverse a symlink')
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
            and stat.S_IMODE(info.st_mode) == 0o700,
            'owned private 0700 evidence directory required')
    return path


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as output:
        output.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
    Path(path).chmod(0o400)


def reserve_ledger(path, pins, *, kind='PREPARATION_STUBBED'):
    """Exclusive local preparation reservation; never native admission/counter."""
    private_directory(Path(path).parent)
    ledger = Path(path).open('x+', encoding='utf-8')
    ledger.write(json.dumps(dict(kind=kind, state='reserved', pins=pins)) + '\n')
    ledger.flush()
    os.fsync(ledger.fileno())
    Path(path).chmod(0o400)
    return ledger


def finish_ledger(ledger, outcome):
    ledger.seek(0)
    ledger.truncate()
    ledger.write(json.dumps(outcome, sort_keys=True, indent=2) + '\n')
    ledger.flush()
    os.fsync(ledger.fileno())
    ledger.close()


def refuse_native():
    # No caller flag, supplied status, schema, or admission file releases this.
    raise PrerequisiteUnavailable(STOP)


class PrerequisiteUnavailable(Refused):
    """Known pre-start prerequisite refusal; no native reservation is consumed."""


class OwnedKernelTrace:
    """Private nonnative kernel fixture; no Agy admission or pause contract."""
    SYSCALLS = ('execve,openat,fstat,connect,read,write,close,clone,clone3,fork,vfork,'
                'readlink,wait4,waitid,futex,poll,ppoll,pselect6,exit,exit_group')

    def __init__(self, argv, root):
        self.root = private_directory(root)
        require(type(argv) is list and argv and all(type(a) is str and '\0' not in a for a in argv),
                'literal fixture argv required')
        self.image = checked_pin(argv[0])
        require(self.image['SHA256'] != AGY_SHA256, 'nonnative fixture only')
        require(Path(argv[0]).is_relative_to(self.root), 'fixture image must be owned in-root')
        require(stat.S_IMODE(Path(argv[0]).lstat().st_mode) == 0o500, 'controlled0500 fixture image required')
        require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'pidfd prerequisite')
        tracer = shutil.which('strace')
        require(tracer is not None, 'strace prerequisite')
        tool_pin(Path(tracer).resolve())
        self.trace = self.root/'kernel.trace'
        self.trace.touch(mode=0o600, exist_ok=False)
        self.stderr = (self.root/'kernel.stderr').open('xb')
        os.fchmod(self.stderr.fileno(),0o600)
        try:
            self.process = subprocess.Popen([str(Path(tracer).resolve()), '-f', '-xx',
                '-s', '262144', '-e', 'trace='+self.SYSCALLS, '-e', 'abbrev=!fstat',
                '-o', str(self.trace), *argv], cwd=self.root, env=dict(os.environ),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, start_new_session=True)
        except BaseException:
            self.stderr.close()
            raise
        self.pidfd, self.generation, self.closed = None, None, False
        try:
            def identify():
                for path in Path('/proc').iterdir():
                    if not path.name.isdigit():
                        continue
                    try:
                        parent = int((path/'stat').read_text().rsplit(')', 1)[1].split()[1])
                        if parent == self.process.pid and os.readlink(path/'exe') == argv[0]:
                            return int(path.name)
                    except (OSError, ValueError, IndexError):
                        continue
                return None
            self.pid = r04.wait_for(identify, 'owned nonnative tracee', timeout=10)
            self.pidfd = os.pidfd_open(self.pid)
            self.generation = collector.capture_owned_generation(self.pid)
            require(self.generation['Client'] == self.image and self.generation['ClientArgv'] == argv,
                    'fixture identity/argv mismatch')
        except BaseException:
            self.close()
            raise


    def capture_generation(self):
        current = collector.capture_owned_generation(self.pid)
        require(current == self.generation, 'owned fixture generation changed')
        return current

    def snapshot(self, name):
        # Retain exactly what strace has emitted, including a partial last
        # record. The strict consumer decides whether that prefix is closed.
        raw = self.trace.read_bytes()
        require(raw and len(raw) <= 256*1024*1024, 'empty/oversized kernel trace')
        path = self.root/name
        path.write_bytes(raw)
        path.chmod(0o400)
        return checked_pin(path)

    def task_states(self):
        return {int(path.name):(path/'stat').read_text().rsplit(')', 1)[1].split()[0]
                for path in Path(f'/proc/{self.pid}/task').iterdir()}


    def close(self):
        if self.closed:
            return
        self.closed = True
        forced, gone = [], False
        try:
            if self.process.poll() is None:
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if self.pidfd is None:
                        raise Refused('unidentified fixture cleanup unresolved')
                    for number in (signal.SIGTERM, signal.SIGKILL):
                        try:
                            signal.pidfd_send_signal(self.pidfd,number)
                            forced.append(number)
                        except ProcessLookupError:
                            gone = True
                        try:
                            self.process.wait(timeout=5)
                            break
                        except subprocess.TimeoutExpired:
                            continue
                    require(self.process.poll() is not None, 'owned fixture cleanup unresolved')
        finally:
            for stream in (self.process.stdin, self.process.stdout, self.stderr):
                stream.close()
            if self.pidfd is not None:
                os.close(self.pidfd)
        raw = self.trace.read_bytes()
        pids = sorted({int(x) for x in re.findall(rb'^(\d+)\s+', raw, re.M)} | {self.process.pid})
        self.cleanup = dict(pids=pids, exit=self.process.returncode, forced_signals=forced,
            group_signals=False, already_gone=gone,
            all_absent=all(pid > 1 and not Path(f'/proc/{pid}').exists() for pid in pids))
        require(self.cleanup['all_absent'], 'traced fixture PID remains after join')
        require(checked_pin(self.image['Path']) == self.image, 'fixture image changed')


def kernel_calls(raw):
    """Read the selected strace dialect; preserve original byte locators."""
    require(type(raw) is bytes and 0 < len(raw) <= 256*1024*1024 and raw.endswith(b'\n'),
            'partial/empty/oversized trace')
    allowed = set(OwnedKernelTrace.SYSCALLS.split(','))
    calls, pending, position = [], {}, 0
    for line in raw.splitlines(keepends=True):
        start, position = position, position+len(line)
        match = re.fullmatch(rb'([1-9]\d*)\s+([^\n]*)\n', line)
        require(match is not None and int(match[1]) > 1, 'unknown trace PID/prefix')
        pid, body = int(match[1]), match[2].decode('ascii')
        resumed = re.fullmatch(r'<\.\.\. (\w+) resumed>(.*)', body)
        if resumed:
            old = pending.pop(pid, None)
            require(old is not None and old['name'] == resumed[1], 'unpaired syscall resume')
            body, start = old['body']+resumed[2], old['start']
        name = re.match(r'(\w+)\(', body)
        if name is None:
            require(re.fullmatch(r'\+\+\+ (?:exited with \d+|killed by SIG\w+(?: \(core dumped\))?) \+\+\+', body)
                    or re.fullmatch(r'--- SIG\w+ .* ---', body)
                    or re.fullmatch(r'--- stopped by SIG\w+ ---', body), 'unknown trace record')
            calls.append(dict(pid=pid,name='exit' if body.startswith('+++') else 'signal',
                              body=body,start=start,end=position))
            continue
        require(name[1] in allowed, 'unselected/unclassifiable syscall')
        record = dict(pid=pid,name=name[1],body=body,start=start,end=position)
        if body.endswith(' <unfinished ...>'):
            require(pid not in pending, 'overlapping unfinished syscall')
            record['body'] = body.removesuffix(' <unfinished ...>')
            pending[pid] = record
        else:
            calls.append(record)
    require(not any(row['name'] in ('connect','execve','clone','clone3','fork','vfork')
                    for row in pending.values()), 'unfinished connect/exec/descendant coverage')
    return sorted(calls,key=lambda row:row['start']), list(pending.values())


def trace_string(quoted):
    try:
        value = ast.literal_eval(quoted)
        require(type(value) is str, 'nonstring trace address')
        return value.encode('latin1').decode('utf-8')
    except (ValueError, SyntaxError, UnicodeError) as error:
        raise Refused('invalid trace address string') from error


def audit_host_connections(raw, capture, root, *, ports, unix_paths, final=True):
    """Finite connect(2) audit; neither egress enforcement nor currentness."""
    root = private_directory(root)
    require(Path(capture['Client']['Path']).is_relative_to(root)
            and checked_pin(capture['Client']['Path']) == capture['Client']
            and capture['ClientArgv'] and capture['ClientArgv'][0] == capture['Client']['Path'],
            'selected root image/argv pin differs')
    require(type(ports) in (set, list, tuple) and all(type(p) is int and 0 < p < 65536 for p in ports),
            'explicit owned loopback ports required')
    sockets = set()
    for selected in unix_paths:
        path = Path(selected)
        require(path.is_absolute() and path.resolve() == path and path.is_relative_to(root)
                and str(path) == os.path.normpath(str(path)), 'Unix selection outside owned root')
        info = path.lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                and info.st_mode & 0o077 == 0, 'owned private Unix socket required')
        sockets.add(str(path))
    calls, pending = kernel_calls(raw)
    leader = capture['Process']['PID']
    require(type(leader) is int and leader > 1, 'selected root PID required')
    births, parents = {leader:-1}, {}
    for row in calls:
        if row['name'] not in ('clone','clone3','fork','vfork'):
            continue
        result = re.search(r'\)\s+= (-?\d+)(?: ([A-Z][A-Z0-9_]+) \([^\n]*\))?$', row['body'])
        require(result is not None, 'unclassifiable descendant result')
        child = int(result[1])
        if child == -1:
            require(result[2] is not None, 'unknown failed descendant result')
            continue
        require(child > 1 and child not in births and result[2] is None, 'unsafe/reused descendant PID')
        births[child], parents[child] = row['start'], row['pid']
    owned = {leader}
    while True:
        expanded = owned | {child for child,parent in parents.items() if parent in owned}
        if expanded == owned:
            break
        owned = expanded
    require(set(births) == owned, 'unattributed descendant creator')
    for row in [*calls,*pending]:
        require(row['pid'] in owned and row['start'] >= births[row['pid']], 'unattributed trace PID')
    executions = list(r04.successful_execs(trace.exec_lines(raw.decode('ascii'), start_offset=0)))
    selected_execs = [row for row in executions if int(row['pid']) == leader]
    require(len(selected_execs) == 1 and selected_execs[0]['path'] == capture['Client']['Path']
            and selected_execs[0]['argv'] == capture['ClientArgv'], 'root literal exec/argv differs')
    root_execs = [row for row in calls if row['pid'] == leader and row['name'] == 'execve']
    require(len(root_execs) == 1 and all(row['start'] >= root_execs[0]['start'] for row in calls),
            'records precede selected root exec')
    deaths = {}
    for row in calls:
        require(row['pid'] not in deaths, 'record after PID exit/reuse')
        if row['name'] == 'exit':
            deaths[row['pid']] = row['start']
    exited = set(deaths)
    require(not final or exited == owned, 'descendant interval does not include all exits')
    quoted = r'"(?:\\.|[^"\\])*"'
    connections = []
    for row in calls:
        if row['name'] != 'connect':
            continue
        body = row['body']
        require('...' not in body, 'truncated connect address')
        call = re.fullmatch(r'connect\((\d+), (\{.*\}), (\d+)\)\s+= (0|-1 [A-Z][A-Z0-9_]+ \([^\n]*\))',body)
        require(call is not None, 'unclassifiable connect record/result')
        address, size, result = call[2], int(call[3]), call[4]
        inet = re.fullmatch(r'\{sa_family=AF_INET, sin_port=htons\((\d+)\), sin_addr=inet_addr\(('+quoted+r')\)\}',address)
        inet6 = re.fullmatch(r'\{sa_family=AF_INET6, sin6_port=htons\((\d+)\), sin6_flowinfo=htonl\(0\), inet_pton\(AF_INET6, ('+quoted+r'), &sin6_addr\), sin6_scope_id=0\}',address)
        unix = re.fullmatch(r'\{sa_family=AF_UNIX, sun_path=('+quoted+r')\}',address)
        if inet or inet6:
            selected = inet or inet6
            host, port = trace_string(selected[2]), int(selected[1])
            require(host == ('127.0.0.1' if inet else '::1') and port in ports
                    and size == (16 if inet else 28), 'external/unselected loopback connect attempt')
            destination = dict(family='AF_INET' if inet else 'AF_INET6',host=host,port=port)
        elif unix:
            path = trace_string(unix[1])
            # R04's sole outside-root allowance is a raw failed nscd lookup,
            # exactly sockaddr length110 and ENOENT. No DNS/proxy allowance.
            missing_nscd = path == '/var/run/nscd/socket' and size == 110 and result == '-1 ENOENT (No such file or directory)'
            require(missing_nscd or path in sockets and 2+len(path.encode()) <= size <= 110,
                    'foreign/unknown Unix connect attempt')
            destination = dict(family='AF_UNIX',path=path,missing_nscd=missing_nscd)
        else:
            raise Refused('unknown/truncated connect address family')
        connections.append(dict(pid=row['pid'],start=row['start'],end=row['end'],
                                result=result,destination=destination))
    return dict(schema='cairn.r06-host-connect-audit/1',connections=connections,
        root_pid=leader,traced_pids=sorted(owned),parents=parents,interval=dict(Start=0,End=len(raw)),
        raw_sha256=hashlib.sha256(raw).hexdigest(),all_observed_descendants_exited=exited==owned,
        pending_nonconnect=[dict(pid=r['pid'],name=r['name'],start=r['start']) for r in pending],
        ExternalCoverage='Observed connect(2) attempts only in this raw interval for the selected exec and kernel-derived descendants. Includes failed attempts. No kernel egress enforcement, sendto/sendmsg/unconnected UDP, inherited/preconnected descriptors, tracer/controller traffic, future interval or global coverage.',
        currentness='not evaluated; pending nonconnect input does not satisfy the strict Go currentness decoder',
        NativePASS=False)


def strict_object(value, names, label):
    require(type(value) is dict and set(value) == set(names), 'unknown/incomplete ' + label)
    return value


def admission_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate admission field')
        result[key] = value
    return result


def admission_pin(value, label):
    strict_object(value, ('Path', 'SHA256'), label)
    require(type(value['Path']) is str and type(value['SHA256']) is str
            and re.fullmatch('[0-9a-f]{64}', value['SHA256']) is not None,
            'invalid ' + label + ' pin')
    path = Path(value['Path'])
    require(path.is_absolute() and str(path) == os.path.normpath(str(path))
            and path.resolve() == path, 'noncanonical ' + label + ' path')
    return path


def read_admission(path, expected_sha256):
    """Read one owned immutable selector, without treating it as authentication."""
    path = Path(path)
    private_directory(path.parent)
    admission_pin(dict(Path=str(path), SHA256=expected_sha256), 'admission')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        first = os.fstat(fd)
        require(stat.S_ISREG(first.st_mode) and first.st_uid == os.getuid()
                and stat.S_IMODE(first.st_mode) in (0o400, 0o600)
                and first.st_nlink == 1 and 0 < first.st_size <= 65536,
                'admission must be one private owned regular file')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            raw = source.read(65537)
        last = os.fstat(fd)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        require(identity(first) == identity(last) == identity(path.lstat())
                and len(raw) == first.st_size
                and hashlib.sha256(raw).hexdigest() == expected_sha256,
                'admission bytes/identity changed or pin mismatch')
        return json.loads(raw, object_pairs_hook=admission_pairs,
                          parse_constant=lambda _: require(False, 'nonfinite admission value'))
    finally:
        os.close(fd)


def native_source_identity():
    def git(*args):
        return subprocess.check_output(['git', '-C', str(ROOT), *args], text=True, timeout=15).strip()
    require(not git('status', '--porcelain'), 'native driver requires a clean frozen checkout')
    return dict(source_head=git('rev-parse', 'HEAD'), source_tree=git('rev-parse', 'HEAD^{tree}'))


def native_admission_window(selected, now=None):
    times = []
    for key in ('issued_at', 'expires_at'):
        value = selected[key]
        require(type(value) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value) is not None,
                'strict UTC admission time required')
        times.append(datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc))
    current = now if now is not None else datetime.now(timezone.utc)
    require(current.tzinfo is not None and times[0] <= current < times[1]
            and 0 < (times[1] - times[0]).total_seconds() <= 900,
            'expired/not-yet-issued/unbounded native admission')


def checked_native_admission(path, expected_sha256, attempt_id, *, now=None):
    """All selection/expiry/source checks precede application startup/reservation."""
    require(path is not None and type(expected_sha256) is str and type(attempt_id) is str,
            'explicit pinned Agy admission and attempt required')
    require(str(uuid.UUID(attempt_id)) == attempt_id, 'canonical attempt UUID required')
    selected = read_admission(path, expected_sha256)
    strict_object(selected, ('schema', 'source_head', 'source_tree', 'manifest', 'index',
        'build_complete', 'images', 'client', 'provider', 'attempt_id', 'attempt_limit',
        'issued_at', 'expires_at', 'ledger', 'evidence', 'catalog_root', 'tools'), 'native admission')
    require(selected['schema'] == NATIVE_SCHEMA and selected['attempt_id'] == attempt_id
            and type(selected['attempt_limit']) is int and selected['attempt_limit'] == 1,
            'wrong schema/attempt/limit')
    require(type(selected['source_head']) is str and type(selected['source_tree']) is str
            and re.fullmatch('[0-9a-f]{40}', selected['source_head']) is not None
            and re.fullmatch('[0-9a-f]{40}', selected['source_tree']) is not None,
            'exact driver source head/tree required')
    native_admission_window(selected, now)
    require({key:selected[key] for key in ('source_head', 'source_tree')} == native_source_identity(),
            'wrong driver source head/tree; image source is separate')
    require(type(selected['provider']) is dict and selected['provider'] == LOCAL_PROVIDER
            and type(selected['provider'].get('port')) is int,
            'only the exact local synthetic GeminiFixture provider is supported')
    admission_pin(selected['client'], 'held client')
    require(selected['client'] == dict(Path=str(AGY_SOURCE), SHA256=AGY_SHA256),
            'wrong held Agy client')
    checked_pin(AGY_SOURCE, AGY_SHA256)
    require(stat.S_IMODE(AGY_SOURCE.stat().st_mode) == 0o500, 'held Agy source must remain0500')
    strict_object(selected['images'], ROLES, 'image roles')
    for role in ROLES:
        admission_pin(selected['images'][role], role)
    packet_paths = [admission_pin(selected[key], key) for key in ('manifest', 'index', 'build_complete')]
    images, pins, packet = checked_packet(*[item for key, p in zip(
        ('manifest', 'index', 'build_complete'), packet_paths) for item in (p, selected[key]['SHA256'])])
    require(selected['images'] == {role:dict(Path=str(images[role]), SHA256=pins['images'][role]['sha256'])
                                   for role in ROLES}, 'admission image tuple disagrees with sealed packet')
    strict_object(selected['tools'], ('python3', 'strace', 'unshare', 'ip'), 'native tools')
    for name, value in selected['tools'].items():
        actual = Path(sys.executable).resolve() if name == 'python3' else Path(shutil.which(name) or '/absent').resolve()
        require(admission_pin(value, name) == actual, 'selected normal-host tool path changed: ' + name)
        tool_pin(actual, value['SHA256'])
    require(all(type(selected[key]) is str for key in ('evidence', 'catalog_root', 'ledger')),
            'typed evidence/catalog/ledger paths required')
    evidence, catalog = (private_directory(selected[key]) for key in ('evidence', 'catalog_root'))
    require(not list(evidence.iterdir()) and not list(catalog.iterdir()), 'fresh empty native evidence/catalog required')
    ledger = Path(selected['ledger'])
    require(ledger.is_absolute() and ledger.resolve() == ledger
            and ledger.name == attempt_id + '.json', 'ledger must bind the canonical attempt UUID')
    private_directory(ledger.parent)
    roots = (evidence, catalog, ledger.parent, Path(path).parent)
    require(evidence != catalog and not evidence.is_relative_to(catalog) and not catalog.is_relative_to(evidence)
            and all(not root.is_relative_to(evidence) and not root.is_relative_to(catalog)
                    for root in roots[2:]), 'admission/ledger/evidence/catalog custody must not overlap')
    require(not ledger.exists() and not ledger.with_suffix('.terminal.json').exists(), 'native attempt already reserved/consumed')
    return dict(admission=selected, pin=dict(Path=str(Path(path)), SHA256=expected_sha256),
                images=images, image_pins=pins, packet=packet)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class NativeReservation:
    """Immutable exclusive consumption marker plus atomic terminal publication."""
    def __init__(self, approved):
        snapshot = read_admission(approved['pin']['Path'], approved['pin']['SHA256'])
        native_admission_window(snapshot)
        require(snapshot == approved['admission'], 'admission tuple changed after preflight')
        self.path = Path(approved['admission']['ledger'])
        private_directory(self.path.parent)
        self.binding = dict(schema='cairn.r06-agy-native-attempt/1', attempt_id=approved['admission']['attempt_id'],
            admission=copy.deepcopy(approved['pin']), tuple=copy.deepcopy(snapshot), state='consumed')
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(self.binding, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fchmod(stream.fileno(), 0o400)
            os.fsync(stream.fileno())
        sync_directory(self.path.parent)

    def finish(self, outcome):
        terminal = self.path.with_suffix('.terminal.json')
        # link(2) publishes complete fsynced bytes exclusively, without replacing
        # an earlier terminal result. The immutable consumption marker survives.
        temporary = self.path.parent / ('.' + self.path.name + '.' + str(uuid.uuid4()))
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(dict(self.binding, state=outcome['state'], outcome=outcome), stream, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fchmod(stream.fileno(), 0o400)
                os.fsync(stream.fileno())
            os.link(temporary, terminal, follow_symlinks=False)
            sync_directory(self.path.parent)
        finally:
            temporary.unlink(missing_ok=True)
            sync_directory(self.path.parent)
        return terminal


def run_native_attempt(approved, preflight, execute):
    """Injected boundaries keep pre-start75 distinct from a consumed failure."""
    preflight(approved)
    reservation = NativeReservation(approved)
    result = dict(state='failed', application_start_boundary_entered=True)
    try:
        result = execute(approved)
        require(type(result) is dict and result.get('state') == 'passed', 'native unit did not pass')
        return result
    except BaseException as error:
        result = dict(state='failed', application_start_boundary_entered=True, error_type=type(error).__name__)
        raise
    finally:
        reservation.finish(result)


def stage_native_client(approved):
    """Copy, never execute, the selected image inside the NEW owned catalog."""
    root = private_directory(approved['admission']['catalog_root'])
    source = Path(approved['admission']['client']['Path'])
    expected = approved['admission']['client']['SHA256']
    require(source == AGY_SOURCE and expected == AGY_SHA256, 'wrong held client source')
    before = source.stat()
    require(checked_pin(source, expected) and stat.S_IMODE(before.st_mode) == 0o500,
            'controlled0500 source required')
    destination = root/'native-agy-client'
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o500)
    try:
        with os.fdopen(fd, 'wb') as output, source.open('rb') as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fchmod(output.fileno(), 0o500)
            os.fsync(output.fileno())
        selected = checked_pin(destination, expected)
        after = source.stat()
        identity = lambda info: (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        require(identity(before) == identity(after) and checked_pin(source, expected), 'held source changed while copying')
        info = destination.stat()
        require(destination.resolve().is_relative_to(root) and stat.S_IMODE(info.st_mode) == 0o500
                and info.st_uid == os.getuid() and info.st_nlink == 1
                and (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino),
                'native image must be an owned0500 in-root copy, not an alias')
        sync_directory(root)
        return dict(pin=selected, device=info.st_dev, inode=info.st_ino,
                    source_device=before.st_dev, source_inode=before.st_ino)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def validate_image_manifest(pins):
    require(pins.get('schema') == 'cairn.r06-unit-images/1'
            and pins.get('source_head') == BASE and pins.get('source_tree') == BASE_TREE,
            'exact1508 image source tree required')
    require(set(pins.get('images', {})) == set(ROLES), 'all five explicit image roles required')
    paths, hashes = [], []
    for role in ROLES:
        selected = pins['images'][role]
        require(set(selected) >= {'path', 'sha256'}, 'incomplete image role ' + role)
        paths.append(selected['path'])
        hashes.append(selected['sha256'])
    require(len(set(paths)) == len(ROLES) and len(set(hashes)) == len(ROLES),
            'image roles must not alias one another')


def validate_build_recipe(role, record, image, pins):
    """Check the historical recipe without rewriting its real option order."""
    packages = dict(api='./cmd/cairn', control='./cmd/cairn-lifecycle', stager='./cmd/cairn-stage-lifecycle',
                    helper='./cmd/cairn-lifecycle', observer='./internal/lifecycle/coordination')
    require(role in packages, 'unknown image role')
    build = record['build']
    argv = build['inner_argv']
    require(isinstance(argv, list) and 5 <= len(argv) <= 16
            and all(isinstance(arg, str) and '\0' not in arg for arg in argv), 'invalid build argv')
    go_hash = pins.get('native_tools', {}).get(argv[0])
    require(go_hash is not None and Path(argv[0]).is_absolute() and Path(argv[0]).name == 'go',
            'unapproved Go tool identity')
    tool_pin(Path(argv[0]).resolve(), go_hash)
    compiled_test = role in ('helper', 'observer')
    required = {'-c', '-race', '-buildvcs=false'} if compiled_test else {'-buildvcs=false'}
    require(argv[1] == ('test' if compiled_test else 'build') and argv[-1] == packages[role],
            'wrong build mode or package')
    seen, output = set(), None
    index = 2
    while index < len(argv)-1:
        flag = argv[index]
        if flag == '-o':
            require(output is None and index+1 < len(argv)-1, 'missing or duplicate exact output')
            output = argv[index+1]
            index += 2
        else:
            require(flag in required and flag not in seen, 'unknown or duplicate build flag')
            seen.add(flag)
            index += 1
    require(seen == required and output == image['path'], 'missing compile/race/VCS flag or wrong output')
    require(record['image']['path'] == image['path'] and record['image']['sha256'] == image['sha256']
            and build['exit'] == 0 and build['joined'] is True and build['pid'] > 1,
            'image role/build receipt differs')
    tool_pin(Path(image['path']), image['sha256'])
    info = Path(image['path']).lstat()
    require(info.st_dev == record['image']['device'] and info.st_ino == record['image']['inode']
            and stat.S_IMODE(info.st_mode) == 0o500, 'original role image identity changed')


def checked_packet(manifest, manifest_sha256, index, index_sha256, complete, complete_sha256):
    """Pins select BUILD-ONLY bytes; they never admit a native client."""
    require(all((manifest, manifest_sha256, index, index_sha256, complete, complete_sha256)),
            'INPUT_NOT_READY: complete sealed1508 packet and exact pins required')
    selections = {name: checked_pin(path, expected) for name, path, expected in (
        ('manifest', manifest, manifest_sha256), ('index', index, index_sha256),
        ('build_complete', complete, complete_sha256))}
    root = Path(manifest).parent
    private_directory(root)
    require(Path(manifest).name == 'images-host.json' and Path(index) == root/'index.json'
            and Path(complete) == root/'build-complete.json', 'INPUT_NOT_READY: final packet filenames required')
    seal = json.loads(Path(index).read_bytes())
    require(seal.get('schema') == 'cairn.image-build-custody-index/1'
            and seal.get('source_head') == BASE and seal.get('source_tree') == BASE_TREE,
            'INPUT_NOT_READY: exact1508 sealed index required')
    entries = seal.get('entries')
    require(isinstance(entries, list) and 1 <= len(entries) <= 2048, 'invalid bounded packet index')
    indexed = set()
    for entry in entries:
        relative = Path(entry['path'])
        path = root/relative
        require(entry.get('content_read') is True and not relative.is_absolute()
                and path.is_relative_to(root)
                and str(path) == os.path.normpath(str(path)) and str(path) not in indexed,
                'unsafe/duplicate image packet index entry')
        checked_pin(path, entry['sha256'])
        info = path.lstat()
        require(info.st_size == entry['bytes'] and info.st_uid == entry['uid']
                and info.st_dev == entry['device'] and info.st_ino == entry['inode']
                and info.st_nlink == entry['links'] == 1 and stat.S_IMODE(info.st_mode) == entry['mode'] == 0o600,
                'controlled image packet metadata changed')
        indexed.add(str(path))
    required = [Path(manifest), Path(complete), root/'candidate.json',
                root/'tool-provenance-before.json', root/'tool-provenance-after.json']
    require(all(str(path) in indexed for path in required), 'INPUT_NOT_READY: incomplete sealed packet')
    done = json.loads(Path(complete).read_bytes())
    candidate = json.loads((root/'candidate.json').read_bytes())
    require(done.get('schema') == 'cairn.r06-image-build-complete/1'
            and candidate.get('schema') == 'cairn.r06-image-candidate/1'
            and done.get('status') == 'BUILD_COMPLETE' and candidate.get('status') == 'BUILD_ONLY'
            and done.get('source_head') == candidate.get('source_head') == BASE
            and done.get('source_tree') == candidate.get('source_tree') == BASE_TREE
            and done.get('roles') == list(ROLES)
            and done.get('upstream_exact_tree_check_exit') == 0
            and done.get('all_owned_processes_joined') is True
            and done.get('source_archive_verified') is True
            and done.get('pids_gt_one') is True and done.get('group_signals') is False
            and done.get('source_tools_images_unchanged') is candidate.get('source_tools_images_unchanged') is True
            and candidate.get('source_edits') == candidate.get('tests_executed') == 0
            and candidate.get('native_model_or_service_fixtures') == 0,
            'INPUT_NOT_READY: completed exact1508 BUILD_ONLY provenance required')
    for kind in ('build_exits', 'buildid_exits', 'buildinfo_exits'):
        require(done.get(kind) == {role: 0 for role in ROLES}, 'incomplete image build receipt '+kind)
    require(candidate['manifest']['path'] == done['manifest']['path'] == str(manifest)
            and candidate['manifest']['sha256'] == done['manifest']['sha256'] == manifest_sha256,
            'candidate/build completion selected another manifest')
    checked_pin(root/'candidate.json', done['candidate']['sha256'])
    pins = json.loads(Path(manifest).read_bytes())
    validate_image_manifest(pins)
    require(set(candidate['roles']) == set(ROLES) and candidate.get('distinct_images') is True
            and candidate.get('helper_observer_alias') is False, 'wrong five-role build composition')
    for role in ROLES:
        validate_build_recipe(role, candidate['roles'][role], pins['images'][role], pins)
    archive = candidate['source_archive']
    require(str(archive['path']) in indexed, 'full image-source archive is not sealed')
    selections['source_archive'] = checked_pin(archive['path'], archive['sha256'])
    verify_source_archive(Path(archive['path']), pins['source_files'])
    for name in ('candidate.json', 'tool-provenance-before.json', 'tool-provenance-after.json'):
        selections[name] = checked_pin(root/name)
    verify_tool_provenance(root)
    require(root == CONTROLLED_PACKET, 'only the selected controlled packet is supported')
    selections['lineage'] = checked_pin(root/'lineage.json', LINEAGE_SHA256)
    selections['lineage_final'] = checked_pin(root/'communication/lineage-final.json', LINEAGE_FINAL_SHA256)
    lineage = json.loads((root/'lineage.json').read_bytes())
    require(lineage['schema'] == 'cairn.controlled-packet-lineage/1'
            and lineage['controlled_root'] == str(root) and lineage['source_head'] == BASE
            and lineage['source_tree'] == BASE_TREE and lineage['builds'] == lineage['tests'] == 0,
            'controlled packet lineage differs')
    for row in lineage['raw_to_copy']:
        original, copied = Path(row['original_path']), Path(row['copy_path'])
        require(original.is_relative_to(lineage['original_root']) and copied.is_relative_to(root)
                and row['bytes_identical'] is True, 'external controlled copy relation')
        checked_pin(copied, row['original']['sha256'])
        info = original.lstat()
        require(r04.digest(original) == row['original']['sha256']
                and info.st_dev == row['original']['device'] and info.st_ino == row['original']['inode']
                and stat.S_IMODE(info.st_mode) == row['original']['mode'], 'original packet changed')
    images, _ = roundtrip.images_from(Path(manifest), ROLES)
    return images, pins, selections


def verify_source_archive(path, source_files):
    listing = subprocess.check_output(['git', 'ls-tree', '-rz', BASE], cwd=ROOT)
    expected = {}
    for row in listing.split(b'\0'):
        if row:
            prefix, name = row.split(b'\t', 1)
            expected[name.decode()] = prefix.split()[2].decode()
    require(set(source_files) == set(expected), 'image source map is not the complete1508 tree')
    seen, total = set(), 0
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            if member.isdir():
                continue
            require(member.name in expected and member.name not in seen
                    and member.size <= 32*1024*1024, 'unexpected/duplicate/oversized source archive member')
            require(member.isfile() or member.issym(), 'unsupported source archive member')
            raw = archive.extractfile(member).read() if member.isfile() else member.linkname.encode()
            total += len(raw)
            require(total <= 512*1024*1024, 'image source archive exceeds bound')
            blob = hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw, usedforsecurity=False).hexdigest()
            require(blob == expected[member.name] and hashlib.sha256(raw).hexdigest() == source_files[member.name],
                    'image source archive differs from exact1508 Git/source pins')
            seen.add(member.name)
    require(seen == set(expected), 'incomplete image source archive')


def verify_tool_provenance(root):
    before = json.loads((root/'tool-provenance-before.json').read_bytes())
    after = json.loads((root/'tool-provenance-after.json').read_bytes())
    require(before == after, 'image build tool/runtime provenance changed')
    observed = {}
    for group in ('native_tools', 'compiler_tools', 'go_runtime_files', 'mapped_native_runtime_files'):
        files = before.get(group)
        require(isinstance(files, dict) and files, 'missing image build tool/runtime group '+group)
        for path, expected in files.items():
            observed[path] = tool_pin(Path(path).resolve(), expected)
    return observed


def assert_images_unchanged(images, pins):
    actual = {role: tool_pin(path, pins['images'][role]['sha256']) for role, path in images.items()}
    require(len({pin['Path'] for pin in actual.values()}) == len(ROLES), 'resolved image aliases')
    return actual


def null_profiles(fixture):
    selected = {}
    for label in ('A', 'B'):
        for role in ('Config', 'Memory'):
            pin = fixture[role + label]
            path = Path(pin['Path'])
            require(roundtrip.sha(path) == pin['SHA256'], 'producer profile pin changed')
            require(json.loads(path.read_bytes())['inbox_recall_binding'] is None,
                    'producer null profile changed')
            selected[role + label] = pin
    return selected


def owned_pg_processes(pidfile):
    """Observe this NEW cluster's postmaster and descendants; never signal."""
    leader = int(pidfile.read_text().splitlines()[0])
    require(leader > 1, 'unsafe owned postmaster PID')
    parents = {}
    for path in Path('/proc').iterdir():
        if path.name.isdigit():
            try:
                if path.stat().st_uid != os.getuid():
                    continue
                fields = (path/'stat').read_text().rsplit(')', 1)[1].split()
                parents[int(path.name)] = int(fields[1])
            except (FileNotFoundError, ProcessLookupError):
                continue
    selected = {leader}
    while True:
        expanded = selected | {pid for pid, parent in parents.items() if parent in selected}
        if expanded == selected:
            return sorted(selected)
        selected = expanded


def real_unit(images, cluster, runtime, evidence, *, final_drift=False):
    """Compose maintained producers and public control; create no agent/session."""
    state = {}
    def configure(f):
        host = DisposableHost.start(cluster, binary=images['api'], repo='fixture:agy:auth:prep',
            database='agy_final' if final_drift else 'agy_positive', home=f.root/'api-home',
            socket_path=f.root/'api.sock', log=evidence/'api.log', roles=('worker',), label='agy_auth_prep')
        state['host'] = host
        require(host.process.pid > 1, 'unsafe owned API PID')
        roundtrip.write(evidence/'api-start.json', dict(pid=host.process.pid))
        # This is the maintained roundtrip read transport, not an API/status
        # implementation. Every allowed read executes the actual pinned API.
        roundtrip.write(evidence/'transport.json', dict(api=str(images['api']), root=str(f.root),
            credential=str(host.token('worker')), api_home=str(host.home)))
        f.api.chmod(0o700)
        f.api.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-B',
            str(ROOT/'scripts/check_r06_unbound_roundtrip.py'), '--forward-api',
            str(evidence/'transport.json')]) + ' "$@"\n')
        f.api.chmod(0o500)
        for config in (f.coord, f.memory):
            config.update(socket=str(host.socket), token_file=str(host.token('worker')), repo=host.repo)
    try:
        f, fixture = roundtrip.produced_fixture(images, evidence, scratch=runtime, configure=configure)
        host = state['host']
        profile_pins = null_profiles(fixture)
        initial = roundtrip.plan(fixture)
        require(initial['census']['TargetReceipt'] == fixture['ReceiptB'], 'wrong B target receipt')
        if final_drift:
            transport = json.loads((evidence/'transport.json').read_bytes())
            transport['actor'] = dict(operation='event-watch', count=2,
                path=fixture['MemoryB']['Path'], action='append')
            roundtrip.write(evidence/'transport.json', transport)
            result = roundtrip.invoke(images, host, fixture, initial, evidence, 'final-target-memory',
                error='D6 drain receipt invalidated', final=True)
            require(result['inspected'] and not result['exchange']['Published'], 'missing actual final refusal')
            return dict(public_operations=1, publications=0, refusals=1, final_D6_refusals=1)
        missing = copy.deepcopy(initial)
        del missing['census']['TargetReceipt']
        roundtrip.invoke(images, host, fixture, missing, evidence, 'missing-target',
            error='lacks a tracked receipt descriptor')
        unlinked = f.root/'unlinked-profile.json'
        unlinked.write_bytes(Path(fixture['ConfigB']['Path']).read_bytes())
        unlinked.chmod(0o400)
        unknown = copy.deepcopy(initial)
        unknown['next'][0]['path'] = str(unlinked)
        roundtrip.invoke(images, host, fixture, unknown, evidence, 'unlinked-profile',
            error='lacks a tracked receipt descriptor')
        alias = f.root/'credential-alias'
        os.link(host.token('worker'), alias)
        refused = copy.deepcopy(initial)
        refused['census']['TargetReceipt'] = dict(Path=str(alias), SHA256='0'*64)
        roundtrip.invoke(images, host, fixture, refused, evidence, 'credential-alias',
            error='refuses credential-content reads through aliases')
        roundtrip.invoke(images, host, fixture, initial, evidence, 'A-to-B')
        reverse = roundtrip.plan(fixture, reverse=True)
        require(reverse['census']['TargetReceipt'] == fixture['ReceiptA'], 'wrong retained A target receipt')
        result = roundtrip.invoke(images, host, fixture, reverse, evidence, 'B-to-retained-A', reverse=True)
        require(result['exchange']['Active']['Pins'] == fixture['SelectionA']['Pins'],
                'retained Go /3 reverse did not restore exact A')
        locks = [Path(fixture['SelectionA']['Directory'])/'.watch.lock',
                 Path(fixture['ConfigRoot'])/'retained-A/.watch.lock',
                 Path(fixture['ConfigRoot'])/'retained-B/.watch.lock']
        require(all(os.path.samefile(locks[0], path) for path in locks), 'watch-lock identity split')
        require(null_profiles(fixture) == profile_pins, 'null profile bytes changed')
        # Preserve exact produced inputs for the next decision, without claiming
        # these stopped disposable credentials/services can be reused as native.
        roundtrip.write(evidence/'prepared-inputs.json', dict(fixture=fixture, profiles=profile_pins,
            generations=json.loads((evidence/'actual-producer.json').read_bytes())['generations'],
            current_A_to_B=initial, B_to_retained_A=reverse, session_paths=[], callback_paths=[],
            native_entry='CLOSED', native_refresh='unobserved', actual_native_D1_D2_D5_D6='unestablished',
            reverse_scope='retained Go /3 A; not Python /2', service_state_after_return='stopped'))
        reads = [json.loads(line) for line in (evidence/'authenticated-reads.jsonl').read_text().splitlines()]
        require(reads and all(row['operation'] in roundtrip.READS and row['exit'] == 0 for row in reads),
                'genuine authenticated read failed or unsupported operation attempted')
        return dict(public_operations=5, publications=2, refusals=3, final_D6_refusals=0,
                    authenticated_reads=len(reads), sessions=0, callbacks=0, per_turn_grants=0)
    finally:
        host = state.get('host')
        if host is not None:
            pid = host.process.pid if host.process is not None else None
            require(pid is None or pid > 1, 'unsafe owned API cleanup PID')
            code = host.close()
            absent = pid is None or not Path(f'/proc/{pid}').exists()
            roundtrip.write(evidence/'api-cleanup.json', dict(pid=pid, exit=code, joined=absent,
                group_signals=False, home_content='metadata-only; excluded from custody hashes/archives'))
            require(absent, 'owned API process remains')
            if 'fixture' in locals():
                custody = roundtrip.snapshot_root(Path(fixture['Root']), Path(fixture['Credential']), api_home=host.home)
                roundtrip.write(evidence/'owned-unit-custody.json', custody)
                # Only already-observed non-private regular files enter the
                # archive. Credential inode aliases/API-home have no content pin.
                archive_path = evidence/'producer-assets.tar.gz'
                with tarfile.open(archive_path, 'x:gz') as archive:
                    for name, row in custody.items():
                        if 'sha256' in row:
                            path = Path(name)
                            require(path.is_relative_to(f.root) and roundtrip.sha(path) == row['sha256'],
                                    'producer asset drift before custody archive')
                            require(host.dsn.encode() not in path.read_bytes(), 'raw fixture DSN in producer asset')
                            archive.add(path, arcname=str(path.relative_to(f.root)), recursive=False)
                archive_path.chmod(0o400)
                roundtrip.write(evidence/'producer-assets.json', dict(archive=checked_pin(archive_path),
                    original_root=str(f.root), credentials_API_home_and_aliases='metadata-only; excluded',
                    content_paths=[name for name, row in custody.items() if 'sha256' in row]))


def prepare_authenticated(evidence, *, manifest, manifest_sha256, index, index_sha256,
                          complete, complete_sha256):
    evidence = private_directory(evidence)
    images, pins, packet = checked_packet(manifest, manifest_sha256, index, index_sha256,
                                         complete, complete_sha256)
    before = assert_images_unchanged(images, pins)
    require(not list(evidence.iterdir()), 'fresh empty authenticated evidence required; no replay')
    pg = pg_bindir()
    pg_pins = {name: tool_pin((pg/name).resolve()) for name in ('initdb','pg_ctl','postgres','createdb','psql')}
    ledger = reserve_ledger(evidence/'preparation-ledger.json', dict(packet=packet, images=before),
                            kind='AUTHENTICATED_PREPARATION')
    result = dict(kind='AUTHENTICATED_PREPARATION', native_runs=0, NativePASS=False,
        native_client_executed=False, provider_requests=0, hooks_executed=0,
        complete_unit_qualified=False, scope='finite Go /3 target-aware public control; native entry CLOSED')
    runtime = None
    cluster = None
    old_umask = os.umask(0o077)
    try:
        runtime = Path(tempfile.mkdtemp(prefix='r06a-', dir=Path.home()/'.cache'))
        roundtrip.write(evidence/'runtime.json', dict(path=str(runtime),
            private_exclusions='PG data and API-home/credentials/aliases are not content-hashed or archived'))
        roundtrip.write(evidence/'images-before-use.json', dict(manifest=pins, packet=packet, images=before))
        roundtrip.write(evidence/'pg-before-use.json', pg_pins)
        cluster = OwnedCluster.start(runtime/'pg', pg_bin=pg)
        pg_pids = owned_pg_processes(runtime/'pg/data/postmaster.pid')
        roundtrip.write(evidence/'owned-pg-processes.json', dict(pids=pg_pids))
        outcomes = {}
        for name, final in [('positive', False), ('final-D6', True)]:
            target = evidence/name
            target.mkdir(mode=0o700)
            scratch = runtime/name
            scratch.mkdir(mode=0o700)
            outcomes[name] = real_unit(images, cluster, scratch, target, final_drift=final)
        result.update(state='passed', outcomes=outcomes, native_entry=STOP,
            preserved_null_profiles=True, legacy_Python_2_reverse='unestablished',
            actual_Agy_generation_session_hook_refresh='unestablished',
            ordinary_Agy_memory='unsupported', ExternalCoverage='unknown', NativeRefresh='unobserved')
    except BaseException as error:
        result.update(state='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        try:
            pidfile = runtime/'pg/data/postmaster.pid' if runtime is not None else None
            pid = int(pidfile.read_text().splitlines()[0]) if pidfile is not None and pidfile.exists() else None
            require(pid is None or pid > 1, 'unsafe owned PG PID')
            stopped = cluster.stop() if cluster is not None else (
                runtime is None or stop_postgres(pg, runtime/'pg/data'))
            cleanup = dict(pid=pid, stopped=stopped, absent=pid is None or not Path(f'/proc/{pid}').exists(),
                           owned_pg_pids=locals().get('pg_pids', []), group_signals=False, runtime=str(runtime))
            cleanup['all_observed_pg_pids_absent'] = all(p > 1 and not Path(f'/proc/{p}').exists()
                for p in cleanup['owned_pg_pids'])
            roundtrip.write(evidence/'pg-cleanup.json', cleanup)
            require(stopped and cleanup['absent'] and cleanup['all_observed_pg_pids_absent'],
                    'owned PG cleanup unresolved')
            api_starts = [json.loads(path.read_bytes()) for path in evidence.glob('*/api-start.json')]
            require(all(row['pid'] > 1 and not Path(f"/proc/{row['pid']}").exists() for row in api_starts),
                    'owned API cleanup unresolved')
            after = assert_images_unchanged(images, pins)
            require(before == after, 'image metadata changed during authenticated preparation')
            require(all(tool_pin((pg/name).resolve()) == pin for name, pin in pg_pins.items()), 'PG tool drift')
            require(all(checked_pin(pin['Path']) == pin for pin in packet.values()), 'sealed packet changed during use')
            verify_tool_provenance(Path(manifest).parent)
            roundtrip.write(evidence/'images-after-use.json', after)
            roundtrip.write(evidence/'pg-after-use.json', pg_pins)
            result['cleanup'] = cleanup
            if runtime is not None:
                private_directory(runtime)
                require(runtime.parent == Path.home()/'.cache' and runtime.name.startswith('r06a-'),
                        'refuse foreign scratch removal')
                shutil.rmtree(runtime)
                cleanup['scratch_removed'] = not runtime.exists()
                require(cleanup['scratch_removed'], 'owned scratch retained')
                roundtrip.write(evidence/'pg-cleanup.json', cleanup)
            roundtrip.write(evidence/'auth-result.json', result)
        except BaseException as error:
            result.update(state='failed', cleanup_error_type=type(error).__name__, cleanup_error=str(error))
            raise
        finally:
            finish_ledger(ledger, result)
            os.umask(old_umask)
    return result


def prepare_stubbed(evidence, *, python_sha256, strace_sha256):
    evidence = private_directory(evidence)
    python = Path(sys.executable).resolve()
    tracer_path = shutil.which('strace')
    require(tracer_path is not None, 'strace prerequisite unavailable')
    tracer = Path(tracer_path).resolve()
    pins = dict(python=tool_pin(python, python_sha256),
                strace=tool_pin(tracer, strace_sha256),
                sources={name: checked_pin(ROOT / name) for name in REUSED},
                driver=checked_pin(Path(__file__).resolve()))
    require(not list(evidence.iterdir()), 'fresh empty evidence directory required; attempt is not replayable')
    ledger = reserve_ledger(evidence / 'stub-ledger.json', pins)
    child = None
    result = dict(kind='PREPARATION_STUBBED', native_client_executed=False,
                  native_runs=0, NativePASS=False, complete_unit_qualified=False,
                  refresh='unobserved', actual_case_stop=STOP, pins=pins)
    old_umask = os.umask(0o077)
    try:
        work = evidence / 'workspace'
        work.mkdir(mode=0o700)
        # The landed collector requires a caller-owned executable. Preserve that
        # guard: use an owned byte-identical copy of the non-native interpreter.
        stub_image = evidence / 'preparation-python'
        shutil.copyfile(python, stub_image)
        stub_image.chmod(0o500)
        stub_pin = checked_pin(stub_image, pins['python']['SHA256'])
        argv = [str(stub_image), '-I', '-S', '-B', '-c', STUB]
        child = r04.TracedProcess(argv, evidence / 'preparation-stub',
                                  dict(os.environ), evidence, work)
        before = collector.capture_owned_generation(child.native_pid)
        require(before['Client'] == stub_pin and before['ClientArgv'] == argv,
                'stub generation does not match pinned literal argv/image')
        with selectors.DefaultSelector() as selector:
            selector.register(child.process.stdout, selectors.EVENT_READ)
            require(bool(selector.select(timeout=10)), 'stub readiness output missing')
            ready = os.read(child.process.stdout.fileno(), 4096)
        require(ready == b'CAIRN_PREPARATION_STUB_READY\n', 'unexpected stub readiness bytes')
        child.process.stdin.write(INPUT)
        child.process.stdin.flush()
        after = collector.capture_owned_generation(child.native_pid)
        require(before == after, 'stub generation changed before EOF')
        generation = collector.write_generation_pair(evidence / 'stub-generation.json', before, after)
        child.process.stdin.close()
        # Fixed stub output is smaller than a pipe; TracedProcess owns the wait.
        child.process.wait(timeout=10)
        output = ready + child.process.stdout.read()
        child.close(timeout=10)
        expected = ready + b'CAIRN_PREPARATION_STUB_EOF=' + str(len(INPUT)).encode() + b'\n'
        require(output == expected, 'stub did not consume its complete stdin EOF')
        (evidence / 'stub-stdout').write_bytes(output)
        raw = child.trace.read_bytes()
        interval = collector.trace_interval(child.trace, 0, len(raw), [])
        text = raw.decode()
        parsed = list(r04.successful_execs(trace.exec_lines(text)))
        require(len(parsed) == 1 and parsed[0]['path'] == str(stub_image)
                and parsed[0]['argv'] == argv and int(parsed[0]['pid']) == child.native_pid,
                'unexpected executable or descendant in STUB trace')
        require(trace.io_bytes(text, child.native_pid, 0, 'read') == INPUT
                and any(re.fullmatch(r'read\(0, "", \d+\)\s+= 0', event)
                        for _, _, event in trace.calls(text, {'read'}, pid=child.native_pid)),
                'raw stub stdin bytes or EOF witness missing')
        require(trace.io_bytes(text, child.native_pid, 1, 'write') == expected
                and trace.exits(text, child.native_pid) == ['exited with 0'],
                'raw stub output or zero-exit witness missing')
        pids = sorted({int(pid) for pid in child.pids} | {child.process.pid})
        require(all(pid > 1 and not Path(f'/proc/{pid}').exists() for pid in pids),
                'owned tracee/tracer did not join and disappear')
        require(tool_pin(python) == pins['python'] and tool_pin(tracer) == pins['strace']
                and checked_pin(stub_image) == stub_pin,
                'tool image changed during preparation')
        require(all(checked_pin(ROOT / name) == selected for name, selected in pins['sources'].items())
                and checked_pin(Path(__file__).resolve()) == pins['driver'],
                'source changed during preparation')
        result.update(state='passed', owned_pids=pids, generation=generation, interval=interval,
                      stub_image=stub_pin,
                      output=checked_pin(evidence / 'stub-stdout'), stdin_bytes=len(INPUT),
                      parsed_execs=len(parsed), callbacks=0, provider_requests=0,
                      producer_installations=0, API_requests=0, database_clusters=0)
        write_json(evidence / 'stub-result.json', result)
        return result
    except BaseException as error:
        result.update(state='failed', error_type=type(error).__name__, error=str(error))
        raise
    finally:
        try:
            if child is not None:
                child.close(timeout=10)
        finally:
            try:
                finish_ledger(ledger, result)
                for path in evidence.iterdir():
                    if path.is_file() and not path.is_symlink():
                        path.chmod(0o400)
            finally:
                os.umask(old_umask)



def prepare_target_observation(evidence):
    """Genuine Go /3 DATA for focused Go tests; no session/hook/API/PG."""
    evidence = private_directory(evidence)
    require(not list(evidence.iterdir()), 'fresh empty target evidence required')
    packet = CONTROLLED_PACKET
    images, _, _ = checked_packet(packet/'images-host.json',
        'e132531f982044e89546e67242f0ae28d7b0d3db05e8fee4f21c8f428feca068',
        packet/'index.json', 'f169590e02f41ba1b9e6578713a7f030a24d0a1f21432bf2a2c118bc200bbeaa',
        packet/'build-complete.json', 'ddfcf61f4e63f44378e5009f16b510fa2436914d270dc807dcd865201a55745f')
    runtime = evidence/'owned-runtime'
    runtime.mkdir(mode=0o700)
    def configure(f):
        f.settings.write_bytes(b'{}\n')
        f.settings.chmod(0o600)
        Path(f.coord['token_file']).touch(mode=0o000)
        Path(f.coord['token_file']).chmod(0o000)
    f, fixture = roundtrip.produced_fixture(images, evidence, scratch=runtime, configure=configure)
    work = f.root/'native-unit-workspace'
    work.mkdir(mode=0o700)
    (work/'.agents').mkdir(mode=0o700)
    produced = json.loads((evidence/'actual-producer.json').read_bytes())['generations']
    upper = lambda pin: dict(Path=pin['path'], SHA256=pin['sha256'])
    for label in ('A','B'):
        generation = produced[label]
        hooks = generation['hook']
        metadata = json.loads((Path(hooks['selection']['path']).parent/'hook-selection.json').read_bytes())
        launcher = Path(metadata['rows'][0]['argv'][0])
        fixture[label] = dict(Generation=upper(hooks['selection']), Config=generation['Config'],
            Settings=upper(hooks['settings']), Launcher=checked_pin(launcher))
    fixture.update(Account=str(f.account),Workspace=str(work),
        Installed=str(f.account/'hooks.json'),
        Scope='genuine producer DATA; empty actual native/session/callback inventories; no API/PG/provider',
        Validation='new Go reader must be tested separately; old1508 material does not prove new behavior')
    roundtrip.write(evidence/'agy-target-fixture.json', fixture)
    return fixture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-stubbed', action='store_true')
    mode.add_argument('--prepare-authenticated', action='store_true')
    mode.add_argument('--prepare-target-observation', action='store_true')
    mode.add_argument('--native-unit', action='store_true', help='always refuses; actual case remains held')
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--python-sha256')
    parser.add_argument('--strace-sha256')
    parser.add_argument('--images', type=Path)
    parser.add_argument('--images-sha256')
    parser.add_argument('--packet-index', type=Path)
    parser.add_argument('--packet-index-sha256')
    parser.add_argument('--build-complete', type=Path)
    parser.add_argument('--build-complete-sha256')
    args = parser.parse_args(argv)
    try:
        if args.native_unit:
            refuse_native()
        if args.prepare_target_observation:
            require(args.evidence is not None, 'explicit private target evidence required')
            print(json.dumps(prepare_target_observation(args.evidence), sort_keys=True))
            return 0
        if args.prepare_authenticated:
            require(args.evidence is not None, 'explicit private evidence required')
            result = prepare_authenticated(args.evidence, manifest=args.images,
                manifest_sha256=args.images_sha256, index=args.packet_index,
                index_sha256=args.packet_index_sha256, complete=args.build_complete,
                complete_sha256=args.build_complete_sha256)
            print(json.dumps(result, sort_keys=True))
            return 0
        require(args.evidence is not None and args.python_sha256 and args.strace_sha256,
                'explicit private evidence and exact Python/strace pins required')
        result = prepare_stubbed(args.evidence, python_sha256=args.python_sha256,
                                 strace_sha256=args.strace_sha256)
        print(json.dumps(result, sort_keys=True))
        return 0
    except PrerequisiteUnavailable as error:
        print(str(error), file=sys.stderr)
        return 75
    except (ValueError, OSError, AssertionError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
