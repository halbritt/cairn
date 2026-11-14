"""Owned Agy Go3 unit driver, authenticated preparation and connect audit.

Native execution requires one expiring, source/image/tool-pinned admission.
Preparation and finite nonnative checks never confer native admission.
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
import socket
import socketserver
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
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
PREPARATION_SCOPE = ('Preparation only; no native admission or execution. '
    'Retained Python /2 reverse remains unsupported.')


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


class PrerequisiteUnavailable(Refused):
    """Known pre-start prerequisite refusal; no native reservation is consumed."""


class OwnedKernelTrace:
    """Owned traced process; the held Agy image requires consumed admission."""
    SYSCALLS = ('execve,openat,fstat,connect,read,write,close,clone,clone3,fork,vfork,'
                'readlink,wait4,waitid,exit,exit_group')

    def __init__(self, argv, root, *, work=None, env=None, admission=None):
        self.root = private_directory(root)
        require(type(argv) is list and argv and all(type(a) is str and '\0' not in a for a in argv),
                'literal fixture argv required')
        self.image = checked_pin(argv[0])
        info=Path(argv[0]).lstat()
        self.image_identity=(info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode),info.st_nlink)
        if self.image['SHA256'] == AGY_SHA256:
            require(admission is not None, 'native image requires consumed pinned admission')
            selected=admission['admission']
            marker=json.loads(Path(selected['ledger']).read_bytes())
            require(marker.get('schema')=='cairn.r06-agy-native-attempt/1'
                    and marker.get('tuple')==selected and marker.get('state')=='consumed'
                    and self.root==Path(selected['catalog_root'])
                    and Path(argv[0])==self.root/'native-agy', 'native reservation/image/root disagree')
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
                '-o', str(self.trace), *argv], cwd=work or self.root, env=dict(os.environ) if env is None else env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                start_new_session=True,umask=0o077)
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
            self.native_pid = self.pid
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

    def close(self):
        if self.closed:
            return
        self.closed = True
        forced, gone = [], False
        handles={}
        try:
            if self.pidfd is not None:handles[self.pid]=os.dup(self.pidfd)
            if self.process.poll() is None:
                # The unreaped Popen child anchors ownership. Retain the
                # generation at the ancestry scan, then double-check it while
                # opening each pidfd; a reused PID never becomes a cleanup target.
                references={}
                for path in Path('/proc').iterdir():
                    if not path.name.isdigit():continue
                    try:
                        if path.stat().st_uid==os.getuid():
                            references[int(path.name)]=collector._process.process_reference(int(path.name))
                    except (FileNotFoundError,ProcessLookupError):continue
                depths={self.process.pid:0}
                while True:
                    new={pid:depths[row['parent']]+1 for pid,row in references.items()
                         if row['parent'] in depths and pid not in depths}
                    if not new:break
                    depths.update(new)
                for pid in sorted(depths,key=depths.get):
                    if pid in handles:continue
                    fd=None
                    try:
                        first=references[pid]
                        fd=os.pidfd_open(pid)
                        last=collector._process.process_reference(pid)
                        require(all(first[key]==last[key] for key in ('pid','start','boot','parent')),
                                'descendant generation raced cleanup')
                        handles[pid]=fd;fd=None
                    except (FileNotFoundError,ProcessLookupError):continue
                    finally:
                        if fd is not None:os.close(fd)
                self.process.stdin.close()
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    for number in (signal.SIGTERM, signal.SIGKILL):
                        for pid in sorted(handles,key=lambda p:depths.get(p,1),reverse=True):
                            require(pid>1,'unsafe descendant cleanup PID')
                            try:
                                signal.pidfd_send_signal(handles[pid],number)
                                forced.append(dict(pid=pid,signal=number))
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
            for handle in handles.values():os.close(handle)
        raw = self.trace.read_bytes()
        pids = sorted({int(x) for x in re.findall(rb'^(\d+)\s+', raw, re.M)} | {self.process.pid})
        self.cleanup = dict(pids=pids, exit=self.process.returncode, forced_signals=forced,
            group_signals=False, already_gone=gone,
            all_absent=all(pid > 1 and not Path(f'/proc/{pid}').exists() for pid in pids))
        require(self.cleanup['all_absent'], 'traced fixture PID remains after join')
        require(checked_pin(self.image['Path']) == self.image, 'fixture image changed')
        info=Path(self.image['Path']).lstat()
        require((info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode),info.st_nlink)==self.image_identity,
                'fixture image identity/mode changed')


def kernel_calls(raw, *, collecting=False):
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
            require(pid not in pending, 'syscall overlaps an unfinished call')
            calls.append(record)
    require(collecting or not any(row['name'] in ('connect','execve','clone','clone3','fork','vfork')
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
        currentness='this audit alone does not satisfy Go currentness; readback/2 separately verifies the sole native stdin wait',
        NativePASS=False)


def api_bridge_client(path, argv):
    """Transport bytes only. Authentication stays in the owned API parent."""
    payload=json.dumps(dict(argv=argv,input=sys.stdin.read()),separators=(',',':')).encode()
    require(len(payload)<=2*1024*1024,'API bridge request exceeds bound')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(35)
        connection.connect(str(path))
        connection.sendall(len(payload).to_bytes(4,'big')+payload)
        def receive(size):
            body=bytearray()
            while len(body)<size:
                chunk=connection.recv(size-len(body))
                require(chunk,'API bridge reply ended early')
                body.extend(chunk)
            return bytes(body)
        size=int.from_bytes(receive(4),'big')
        require(0<size<=2*1024*1024,'API bridge reply exceeds bound')
        reply=json.loads(receive(size))
    strict_object(reply,('exit','stdout','stderr'),'API bridge transport reply')
    require(type(reply['exit']) is int and type(reply['stdout']) is str and type(reply['stderr']) is str,
            'invalid API bridge transport reply')
    sys.stdout.write(reply['stdout']);sys.stderr.write(reply['stderr'])
    return reply['exit']


class AuthenticatedAPIBridge:
    """Real pinned CLI/API, outside the trace to exclude credential reads.

    This transports unchanged stdout/stderr/exit. It implements no API status,
    permission, session, currentness, or drain response.
    """
    def __init__(self, root, host, image, evidence):
        self.path=Path(root)/'api-bridge.sock'
        require(len(os.fsencode(self.path))<108,'API bridge Unix path exceeds bound')
        self.host,self.image,self.evidence=host,checked_pin(image),Path(evidence)
        self.calls,self.failures=[],[]
        self.after_call=None
        owner=self
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(35)
                def receive(size):
                    result=bytearray()
                    while len(result)<size:
                        piece=self.request.recv(size-len(result))
                        require(piece,'API bridge request ended early')
                        result.extend(piece)
                    return bytes(result)
                try:
                    size=int.from_bytes(receive(4),'big');require(0<size<=2*1024*1024,'API bridge request size')
                    request=json.loads(receive(size),object_pairs_hook=admission_pairs)
                    strict_object(request,('argv','input'),'API bridge request')
                    argv=request['argv'];require(type(argv) is list and argv and all(type(x) is str for x in argv)
                        and type(request['input']) is str,'API bridge request types')
                    require(argv[0]=='agent' and argv[-1] in roundtrip.READS | {
                        'version','agent-directory','agent-register','agent-context','agent-heartbeat','agent-leave'},
                        'unselected API operation')
                    for flag,expected in [('--socket',str(host.socket)),('--token-file',str(host.token('worker')))]:
                        positions=[i for i,x in enumerate(argv) if x==flag]
                        require(len(positions)==1 and positions[0]+1<len(argv) and argv[positions[0]+1]==expected,
                                'API bridge selector differs')
                    require(not any(x in ('--profile','--url') or x.startswith(('--profile=','--url=','--socket=','--token-file='))
                                    for x in argv),'alternate API bridge selector')
                    require(checked_pin(image)==owner.image,'API image changed before bridge call')
                    process=subprocess.Popen([str(image),*argv],env=host.env,text=True,
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    require(process.pid>1,'unsafe API CLI PID')
                    try:
                        out,err=process.communicate(request['input'],timeout=25)
                    except subprocess.TimeoutExpired:
                        process.kill();out,err=process.communicate()
                        owner.failures.append('API CLI timeout')
                    response=dict(exit=process.returncode,stdout=out,stderr=err)
                    owner.calls.append(dict(argv=argv,input=request['input'],**response,
                        pid=process.pid,joined=True,response_substitution=False))
                    if owner.after_call is not None:owner.after_call(argv)
                    encoded=json.dumps(response,separators=(',',':')).encode()
                    require(len(encoded)<=2*1024*1024,'API bridge reply exceeds bound')
                    self.request.sendall(len(encoded).to_bytes(4,'big')+encoded)
                except BaseException as error:
                    owner.failures.append(type(error).__name__)
                    # Transport failure closes the stream; it never fabricates
                    # a Cairn API envelope or an authenticated refusal.
        self.server=socketserver.ThreadingUnixStreamServer(str(self.path),Handler)
        self.path.chmod(0o600)
        self.thread=threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        require(not self.thread.is_alive(),'API bridge supervisor remains')
        write_json(self.evidence/'actual-api-bridge-calls.json',dict(calls=self.calls,failures=self.failures,
            authenticated_by='unchanged pinned API and owned issued worker profile',response_substitution=False,
            credential_contents='outside traced descendants; never logged or content-pinned'))
        require(not self.failures,'actual API bridge transport failed')


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
        ('manifest', 'index', 'build_complete'), packet_paths) for item in (p, selected[key]['SHA256'])], native=True)
    require(selected['images'] == {role:dict(Path=str(images[role]), SHA256=pins['images'][role]['sha256'])
                                   for role in ROLES}, 'admission image tuple disagrees with sealed packet')
    strict_object(selected['tools'], ('python3', 'strace'), 'native tools')
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
    destination = root/'native-agy'
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


class NativeUnitFixture:
    """Maintained genuine Go3 material and real disposable authenticated API."""
    def __init__(self, root, images, cluster, evidence):
        self.root,self.images,self.evidence=Path(root),images,Path(evidence)
        self.host,self.bridge=None,None
        self.published=None
        require(not self.root.exists(),'producer root must be fresh')
        case=r04.R04HostWiringTests(methodName='runTest')
        case.root,case.binary,case.stager=self.root.parent,images['control'],images['stager']
        self.f=case.fixture(self.root.name,harness='agy')
        f=self.f
        try:
            self.home=f.root/'native-home'
            self.account=self.home/'.gemini/config'
            self.account.mkdir(mode=0o700,parents=True)
            for directory in [self.home,self.home/'.gemini',self.account]:directory.chmod(0o700)
            # The publisher preserves the selected file's outer bytes. Seed
            # this NEW empty account with the producer's compact JSON style,
            # so installation preserves the exact genuine settings digest.
            (self.account/'hooks.json').write_bytes(b'{}');(self.account/'hooks.json').chmod(0o600)
            self.work=f.root/'workspace';self.work.mkdir(mode=0o700)
            (self.work/'.agents').mkdir(mode=0o700)
            subprocess.run(['git','init','--quiet',str(self.work)],check=True,capture_output=True,timeout=10,umask=0o077)
            self.host=DisposableHost.start(cluster,binary=images['api'],repo='fixture:agy:native:'+str(uuid.uuid4()),
                database='agy_'+uuid.uuid4().hex,home=f.root/'api-home',socket_path=f.root/'api.sock',
                log=evidence/'api.log',roles=('worker',),label='r06_agy_native')
            self.host_dsn=self.host.dsn
            self.bridge=AuthenticatedAPIBridge(f.root,self.host,images['api'],evidence)
            f.api.chmod(0o700)
            f.api.write_text('#!/bin/sh\nexec '+shlex.join([sys.executable,'-B',str(Path(__file__).resolve()),
                '--api-bridge-client',str(self.bridge.path)])+' "$@"\n')
            f.api.chmod(0o500)
            for config in (f.memory,f.coord):
                config.update(cairn=str(f.api),socket=str(self.host.socket),token_file=str(self.host.token('worker')),
                              repo=self.host.repo,config_home=str(self.account))
            f.coord.update(process_names=['native-agy'],process_exe_dirs=[],native_delivery=False)
            for directory in (f.coord['state_dir'],f.memory['state_dir']):Path(directory).mkdir(mode=0o700,exist_ok=True)
            f.settings=f.root/'original-hooks.json';f.settings.write_bytes(b'{}\n');f.settings.chmod(0o600)
            f.hook_plan.update(account_home=str(self.account),settings=r04.pin(f.settings))
            self.generations={}
            for label in ('A','B'):
                if label=='B':
                    f.coord['roundtrip_generation']=label;f.memory['roundtrip_generation']=label
                # Avoid HookFixture's global-home reset; retain the genuine
                # UnboundFixture plan and maintained Go3 producer unchanged.
                r04.InstalledFixture.publish_configuration(f,enabled=False)
                f.hook_plan.update(id=str(uuid.uuid4()),selection=f.selected)
                hook=roundtrip.hooks.stage(str(images['stager']),f.hook_root,f.hook_plan)
                metadata=json.loads((Path(hook['selection']['path']).parent/'hook-selection.json').read_bytes())
                selection=dict(Generation=dict(Path=hook['selection']['path'],SHA256=hook['selection']['sha256']),
                    Config=checked_pin(f.coord_path),Settings=dict(Path=hook['settings']['path'],SHA256=hook['settings']['sha256']),
                    Launcher=checked_pin(metadata['rows'][0]['argv'][0]))
                self.generations[label]=dict(Receipt=checked_pin(f.receipt_path),Config=selection['Config'],
                    Memory=checked_pin(f.memory_path),Binding=checked_pin(f.binding),hook=hook,
                    selection=selection,rows=metadata['rows'])
            config_root=f.root/'selected-configs';active=config_root/'unit';active.mkdir(parents=True,mode=0o700)
            config_root.chmod(0o700)
            current=active/'coordination.json';current.write_bytes(Path(self.generations['A']['Config']['Path']).read_bytes());current.chmod(0o400)
            (active/'.watch.lock').touch(mode=0o600)
            callbacks=f.root/'callbacks';callbacks.mkdir(mode=0o700)
            self.catalogs={}
            for label,generation in self.generations.items():
                selection=dict(Directory=str(active),Pins={current.name:generation['Config']['SHA256']})
                self.catalogs[label]=dict(Schema='cairn.watch-unit-catalog/1',Scope='disposable-fixture',Root=str(f.root),
                    ConfigRoot=str(config_root),CallbackRoot=str(callbacks),Selections=[selection],Sessions=[],Callbacks=[],
                    CredentialPaths=[str(self.host.token('worker'))],Bindings=[generation['Binding']],
                    Clients=[checked_pin(f.api)],HookSelections=[],UnboundReceipts=[generation['Receipt']])
            self.unit=dict(Root=str(f.root),ConfigRoot=str(config_root),Credential=str(self.host.token('worker')))
            for label,generation in self.generations.items():
                self.unit['Selection'+label]=self.catalogs[label]['Selections'][0]
                for key in ('Receipt','Config','Memory'):self.unit[key+label]=generation[key]
                self.unit['Catalog'+label]=roundtrip.write(f.root/('catalog-'+label+'.json'),self.catalogs[label])
            roundtrip.write(evidence/'native-produced-inputs.json',dict(unit=self.unit,generations=self.generations,
                source='maintained unbound Go3 producer; no native/session/status synthesized'))
        except BaseException:
            self.close()
            raise

    def publish(self,label):
        selected=self.generations[label]
        argv=[str(self.images['stager']),'agy-layer','--account-home',str(self.home),'--workspace',str(self.work),'--layer','account']
        plan=dict(schema='cairn.agy-layer-install/1',settings=dict(path=selected['selection']['Settings']['Path'],
            sha256=selected['selection']['Settings']['SHA256']))
        installed=self.account/'hooks.json'
        if installed.exists():
            if self.published is None:
                require(installed.read_bytes()==b'{}','initial owned account settings changed')
            else:
                require(r04.digest(installed)==self.generations[self.published]['selection']['Settings']['SHA256'],
                        'unselected installed settings before publication')
                group=json.loads(installed.read_bytes())['cairn-coordination']
                plan['replace_commands']=[row['command'] for event in ('PreInvocation','Stop') for row in group[event]]
        process=subprocess.run(argv,input=json.dumps(plan),text=True,capture_output=True,timeout=30)
        number=len(list(self.evidence.glob('publish-*.json')))
        roundtrip.write(self.evidence/f'publish-{number}-{label}.json',dict(argv=argv,exit=process.returncode,
            stdout=process.stdout,stderr=process.stderr))
        require(process.returncode==0,'actual agy-layer publication failed: '+process.stderr)
        actual=checked_pin(self.account/'hooks.json')
        require(actual['SHA256']==selected['selection']['Settings']['SHA256'],'published layer differs from exact producer')
        require(not (self.work/'.agents/hooks.json').exists(),'second active hook layer')
        self.published=label
        return actual

    def stage_live_b(self):
        """Prepare disabled B while the caller keeps the actual A client live."""
        f=self.f
        f.coord['roundtrip_generation']='B';f.memory['roundtrip_generation']='B'
        r04.InstalledFixture.publish_configuration(f,enabled=False)
        f.hook_plan.update(id=str(uuid.uuid4()),selection=f.selected)
        hook=roundtrip.hooks.stage(str(self.images['stager']),f.hook_root,f.hook_plan)
        metadata=json.loads((Path(hook['selection']['path']).parent/'hook-selection.json').read_bytes())
        selection=dict(Generation=dict(Path=hook['selection']['path'],SHA256=hook['selection']['sha256']),
            Config=checked_pin(f.coord_path),Settings=dict(Path=hook['settings']['path'],SHA256=hook['settings']['sha256']),
            Launcher=checked_pin(metadata['rows'][0]['argv'][0]))
        generation=dict(Receipt=checked_pin(f.receipt_path),Config=selection['Config'],Memory=checked_pin(f.memory_path),
            Binding=checked_pin(f.binding),hook=hook,selection=selection,rows=metadata['rows'])
        self.generations['B']=generation
        for key in ('Receipt','Config','Memory'):self.unit[key+'B']=generation[key]
        self.unit['SelectionB']['Pins']['coordination.json']=generation['Config']['SHA256']
        self.catalogs['B'].update(Bindings=[generation['Binding']],UnboundReceipts=[generation['Receipt']],
            Selections=[self.unit['SelectionB']])
        self.unit['CatalogB']=roundtrip.write(self.root/'catalog-B.json',self.catalogs['B'])
        roundtrip.write(self.evidence/'staged-live-B.json',generation)

    def close(self):
        errors=[]
        if self.bridge is not None:
            try:self.bridge.close()
            except BaseException as error:errors.append(type(error).__name__)
            self.bridge=None
        if self.host is not None:
            pid=self.host.process.pid if self.host.process is not None else None
            code=self.host.close()
            roundtrip.write(self.evidence/'api-cleanup.json',dict(pid=pid,exit=code,
                absent=pid is None or not Path(f'/proc/{pid}').exists(),group_signals=False))
            require(pid is None or pid>1 and not Path(f'/proc/{pid}').exists(),'owned API remains')
            self.host=None
        require(not errors,'API bridge cleanup/failure: '+','.join(errors))


def native_child_environment(fixture,provider_url):
    """Only the application child receives the private HOME and synthetic key."""
    require(provider_url.startswith('http://127.0.0.1:'),'nonlocal provider URL')
    settings=fixture.f.account/'native-settings.json'
    settings.write_text(json.dumps(dict(modelProvider='gemini'))+'\n');settings.chmod(0o600)
    env={key:os.environ[key] for key in ('PATH','CODEX_HOME') if key in os.environ}
    env.update(LANG='C.UTF-8',TERM='dumb',HOME=str(fixture.home),
        ANTIGRAVITY_CLI_CONFIG_DIR=str(fixture.f.account),GEMINI_API_KEY=LOCAL_PROVIDER['api_key'],
        GOOGLE_GEMINI_BASE_URL=provider_url,NO_PROXY=LOCAL_PROVIDER['NO_PROXY'])
    return env,settings


def open_native_session(approved,fixture,client,provider_url):
    """Use maintained stream-json receive/prompt/close, with the owned tracer."""
    env,settings=native_child_environment(fixture,provider_url)
    argv=[client['pin']['Path'],'--new-project','--settings',str(settings),'--model',r04.MODEL,
        '--input-format','stream-json','--output-format','stream-json','--print-timeout','30s','--print','']
    session=object.__new__(r04.AgySession)
    session.output,session.work=fixture.root,fixture.work
    session.child=OwnedKernelTrace(argv,fixture.root,work=fixture.work,env=env,admission=approved)
    session.selector=session.log=None
    session.buffer,session.events=bytearray(),[];session.closed=False
    try:
        session.selector=selectors.DefaultSelector();session.selector.register(session.child.process.stdout,selectors.EVENT_READ)
        session.log=(fixture.root/'native.events.jsonl').open('xb')
        session.identity=session.receive(lambda event:event.get('event')=='init')['conversation_id']
    except BaseException:
        try:session.child.close()
        finally:
            if session.selector is not None:session.selector.close()
            if session.log is not None:session.log.close()
        raise
    return session


def kernel_io(row):
    quoted=r'"(?:\\.|[^"\\])*"'
    match=re.fullmatch(r'(read|write)\((\d+), ('+quoted+r'), \d+\)\s+= (\d+)',row['body'])
    require(match is not None,'unknown/truncated callback or producer I/O')
    data=ast.literal_eval(match[3]).encode('latin1')
    require(len(data)==int(match[4]),'incomplete kernel I/O bytes')
    return int(match[2]),data


def kernel_hook_frame(row):
    # Use the maintained classifier: arbitrary files/wire chunks are not hook
    # frames. A selected hook still needs complete exact bytes, not a prefix.
    selected=trace.hook_writes(str(row['pid'])+' '+row['body']+'\n',[row['pid']])
    if not selected:return None
    require(len(selected)==1,'ambiguous native hook write')
    _,data=kernel_io(row)
    payload=json.loads(data)
    require(payload==selected[0]['payload'],'native hook classifier/bytes disagree')
    return data,payload


def kernel_exec(row):
    rows=list(r04.successful_execs(trace.exec_lines(str(row['pid'])+' '+row['body']+'\n')))
    require(len(rows)==1,'missing literal successful exec')
    return rows[0]['path'],rows[0]['argv']


def kernel_threads(calls,leader):
    threads={leader}
    for row in calls:
        if row['pid'] not in threads or row['name'] not in ('clone','clone3'):continue
        flags=re.search(r'\bflags=([^,}\s)]+)',row['body'])
        child=re.search(r'= ([1-9]\d*)$',row['body'])
        if flags and child and 'CLONE_THREAD' in flags[1].split('|'):
            require(int(child[1])>1 and int(child[1]) not in threads,'reused native thread PID')
            threads.add(int(child[1]))
    return threads


def collect_native_phase(session,fixture,label,start,destination):
    """Seal exact complete bytes; locators never substitute for Go readback."""
    expected=fixture.generations[label]
    def finished():
        raw=session.child.trace.read_bytes()
        if not raw or not raw.endswith(b'\n'):return None
        calls,pending=kernel_calls(raw,collecting=True)
        threads=kernel_threads(calls,session.child.pid)
        outer=[]
        known={g['selection']['Launcher']['Path'] for g in fixture.generations.values()}
        for row in calls:
            if row['name']=='execve' and row['start']>=start:
                path,args=kernel_exec(row)
                if path in known:outer.append((row,path,args))
        if len(outer)<2:return None
        require(len(outer)==2,'extra managed callback in phase')
        frames=[]
        for row in calls:
            if row['name']!='write' or row['pid'] not in threads or row['start']<start:continue
            selected=kernel_hook_frame(row)
            if selected is not None:
                data,payload=selected;frames.append((row,data,payload))
        if len(frames)<2:return None
        require(len(frames)==2,'extra native event frame in phase')
        callbacks=[]
        histories=[]
        for index,event in enumerate(('PreInvocation','Stop')):
            row,path,args=outer[index]
            emitted=expected['rows'][index]
            require(args==emitted['argv'] and path==expected['selection']['Launcher']['Path'],
                    'literal callback does not match exact producer/event')
            exits=[call for call in calls if call['pid']==row['pid'] and call['name']=='exit' and call['start']>row['start']]
            if not exits:return None
            require(len(exits)==1 and exits[0]['body']=='+++ exited with 0 +++','callback did not exit zero')
            end=exits[0]['start']
            inner=[call for call in calls if call['pid']==row['pid'] and call['name']=='execve'
                   and row['start']<call['start']<end]
            require(len(inner)==1,'launcher inner exec coverage')
            vector=shlex.split(Path(path).read_text().splitlines()[1])
            require(vector[0]=='exec' and vector[-1]=='$@','unrecognized producer launcher')
            inner_path,inner_args=kernel_exec(inner[0])
            require(inner_args==vector[1:-1]+args[1:] and inner_path==vector[1],
                    'launcher did not exec exact emitted program')
            callback_threads=kernel_threads([call for call in calls if inner[0]['start']<=call['start']<end],row['pid'])
            parts=[];eof=False
            for call in calls:
                if call['pid'] not in callback_threads or call['name']!='read' or not inner[0]['start']<call['start']<end:continue
                if not re.match(r'read\(0,',call['body']):continue
                fd,part=kernel_io(call)
                require(not eof,'callback read after stdin EOF')
                parts.append(part);eof=not part
            require(eof and parts and b''.join(parts),'callback stdin lacks complete bytes/EOF')
            frame,data,payload=frames[index]
            require(data==b''.join(parts) and frame['start']<end,'callback stdin differs from actual native write')
            require(payload['hookEventName']==event and payload['conversationId']==session.identity
                    and payload['workspacePaths']==[str(fixture.work)],'native callback identity/workspace/event mismatch')
            if event=='Stop':require(payload.get('fullyIdle') is True,'Stop is not Boolean fullyIdle=true')
            history=Path(payload['transcriptPath'])
            require(history.is_absolute() and history.is_relative_to(fixture.home) and session.identity in history.parts,
                    'native history is outside owned session HOME')
            histories.append(str(history))
            callbacks.append(dict(Event=event,Exec=row['start'],Join=end,Frame=frame['start']))
        require(len(set(histories))==1,'callbacks name different native histories')
        # Polling can see an in-flight callback before its actual EOF/exit.
        # Accept only a final complete prefix with the approved sole stdin wait.
        if not (len(pending)<=1 and all(row['name']=='read' and row['body'].strip()=='read(0'
                and row['pid'] in threads for row in pending)):return None
        kernel_calls(raw)
        return raw,callbacks,histories[0]
    raw,callbacks,history=r04.wait_for(finished,'actual emitted callbacks, stdin EOF, zero exits and idle frame',timeout=35)
    destination.write_bytes(raw);destination.chmod(0o400)
    phase=collector.trace_interval(destination,start,len(raw),callbacks)
    return phase,history


def capture_native_catalogs(fixture,session,before_capture,before,after_capture,after,client):
    root=fixture.root
    generation=collector.write_generation_pair(root/'native-generation.json',before_capture,after_capture)
    states=[]
    for path in sorted(Path(fixture.f.coord['state_dir']).glob('*.json')):
        value=json.loads(path.read_bytes())
        require(value.get('schema')=='cairn.native-session/1','unknown actual native state')
        require(value.get('native_session_id')==session.identity,'actual native state identity differs')
        states.append(checked_pin(path))
    require(len(states)==1,'expected exactly one actual native state')
    readback=roundtrip.write(root/'native-readback.json',dict(Schema='cairn.watch-agy-readback/2',Harness='agy',
        EvidenceKind='current-observation',NativeSession=session.identity,Workspace=str(fixture.work),
        Client=client['pin'],ClientArgv=after_capture['ClientArgv'],Generation=generation,
        A=fixture.generations['A']['selection'],B=fixture.generations['B']['selection'],Before=before,After=after))
    layerfiles=[]
    for directory in (fixture.account,fixture.work):
        for path in sorted(directory.rglob('*')):
            if path.is_file():layerfiles.append(checked_pin(path))
    for label in ('A','B'):
        catalog=copy.deepcopy(fixture.catalogs[label])
        current=dict(Path=str(Path(fixture.unit['Selection'+label]['Directory'])/'coordination.json'),
                     SHA256=fixture.generations[label]['Config']['SHA256'])
        selected=roundtrip.write(root/('native-hooks-'+label+'.json'),dict(Schema='cairn.watch-agy-hooks/1',Harness='agy',
            Account=str(fixture.account),Workspace=str(fixture.work),Config=current,Session=states[0],Readback=readback,
            Layers=[dict(Layer='account',Generation=fixture.generations['B']['selection']['Generation'],
                Settings=fixture.generations['B']['selection']['Settings'],Installed=checked_pin(fixture.account/'hooks.json')),
                dict(Layer='project',Generation=dict(Path='',SHA256=''),Settings=dict(Path='',SHA256=''),
                    Installed=dict(Path=str(fixture.work/'.agents/hooks.json'),SHA256=''))],LayerFiles=layerfiles))
        catalog.update(Sessions=states,HookSelections=[selected],Clients=[checked_pin(fixture.f.api),client['pin']])
        fixture.unit['Catalog'+label]=roundtrip.write(root/('native-catalog-'+label+'.json'),catalog)
    return states,readback,generation


def native_preflight(approved):
    """Known prerequisites precede reservation and every application startup."""
    try:
        selected=approved['admission']
        require(not list(private_directory(selected['catalog_root']).iterdir())
                and not list(private_directory(selected['evidence']).iterdir()),'native output roots no longer empty')
        require(os.getuid()!=0,'normal nonroot PG/application owner required')
        require(len(os.fsencode(Path(selected['catalog_root'])/'api-bridge.sock'))<108,
                'selected catalog root is too long for owned Unix sockets')
        require(hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'),'pidfd prerequisite unavailable')
        pg=pg_bindir()
        pg_pins={name:tool_pin((pg/name).resolve()) for name in ('initdb','pg_ctl','postgres','createdb','psql')}
        require(all(os.access(pg/name,os.X_OK) for name in pg_pins),'PG tool is not executable')
        require(not Path('/var/run/nscd/socket').exists(),'nscd socket is present; exact absent-socket contract unavailable')
        assert_images_unchanged(approved['images'],approved['image_pins'])
        candidate=json.loads(Path(approved['packet']['candidate.json']['Path']).read_bytes())
        for role in ROLES:
            validate_build_recipe(role,candidate['roles'][role],approved['image_pins']['images'][role],approved['image_pins'])
        require({key:selected[key] for key in ('source_head','source_tree')}==native_source_identity(),
                'driver source changed before reservation')
        for pin in selected['tools'].values():tool_pin(pin['Path'],pin['SHA256'])
        source_pins={name:checked_pin(ROOT/name) for name in (*REUSED,'scripts/check_r06_agy_native_unit.py')}
        require(read_admission(approved['pin']['Path'],approved['pin']['SHA256'])==selected,'admission changed during preflight')
        native_admission_window(selected)
        approved['preflight']=dict(pg=str(pg),pg_pins=pg_pins,source_pins=source_pins,
            parent=dict(HOME=os.environ['HOME'],CODEX_HOME=os.environ.get('CODEX_HOME')))
    except (ValueError,OSError,AssertionError,RuntimeError,KeyError,TypeError,subprocess.SubprocessError) as error:
        raise PrerequisiteUnavailable('INPUT_NOT_READY: '+str(error)) from error


def execute_native(approved):
    """One future admitted A/B/restored-A run; authoring never calls this."""
    selected=approved['admission'];evidence=Path(selected['evidence']);root=Path(selected['catalog_root'])
    runtime=Path(tempfile.mkdtemp(prefix='r06n-',dir=Path.home()/'.cache'))
    cluster=fixture=provider=session=None
    result=dict(state='failed',attempt_id=selected['attempt_id'],NativePASS=False)
    pg=Path(approved['preflight']['pg']);pg_pids=[]
    try:
        roundtrip.write(evidence/'application-start.json',dict(attempt_id=selected['attempt_id'],runtime=str(runtime),
            admission=approved['pin'],scope='one actual disposable Agy; no automatic retry'))
        private_directory(root);require(not list(root.iterdir()),'catalog root became nonempty');root.rmdir()
        cluster=OwnedCluster.start(runtime/'pg',pg_bin=pg)
        pg_pids=owned_pg_processes(runtime/'pg/data/postmaster.pid')
        fixture=NativeUnitFixture(root,approved['images'],cluster,evidence)
        client=stage_native_client(approved)
        fixture.publish('A')
        provider=r04.GeminiFixture()
        session=open_native_session(approved,fixture,client,provider.url)
        before_capture=session.child.generation
        prompts=['R06-owned-A-'+selected['attempt_id'],'R06-owned-B-'+selected['attempt_id'],
                 'R06-owned-restored-A-'+selected['attempt_id']]
        source_pins=approved['preflight']['source_pins']
        results=[]
        # No script-generated callback/state/session is permitted here: each
        # prompt goes through the actual stream-json client and local provider.
        start=0;results.append(session.prompt(prompts[0]))
        before,history=collect_native_phase(session,fixture,'A',start,root/'phase-A.trace')
        audit_a=audit_host_connections(Path(before['Trace']['Path']).read_bytes(),before_capture,root,
            ports={provider.server.server_port},unix_paths=[fixture.bridge.path],final=False)
        fixture.stage_live_b()
        require(session.child.capture_generation()==before_capture,'staging B changed native generation')
        fixture.publish('B')
        start=session.child.trace.stat().st_size
        require(start>0 and session.child.trace.read_bytes().endswith(b'\n'),'B boundary is not a complete raw line')
        results.append(session.prompt(prompts[1]))
        after,history_b=collect_native_phase(session,fixture,'B',start,root/'phase-B.trace')
        after_capture=session.child.capture_generation()
        require(before_capture==after_capture and history_b==history,'same native generation/history not preserved')
        audit_b=audit_host_connections(Path(after['Trace']['Path']).read_bytes(),after_capture,root,
            ports={provider.server.server_port},unix_paths=[fixture.bridge.path],final=False)
        states,readback,generation=capture_native_catalogs(fixture,session,before_capture,before,after_capture,after,client)
        forward=roundtrip.plan(fixture.unit);forward['census']['Proposal']['Sessions']=[p['Path'] for p in states]
        public_forward=roundtrip.invoke(approved['images'],fixture.host,fixture.unit,forward,evidence,'native-A-to-B')
        reverse=roundtrip.plan(fixture.unit,reverse=True);reverse['census']['Proposal']['Sessions']=[p['Path'] for p in states]
        public_reverse=roundtrip.invoke(approved['images'],fixture.host,fixture.unit,reverse,evidence,
            'native-B-to-retained-Go3-A',reverse=True)
        locks=[Path(fixture.unit['SelectionA']['Directory'])/'.watch.lock',
            Path(fixture.unit['ConfigRoot'])/'retained-A/.watch.lock',Path(fixture.unit['ConfigRoot'])/'retained-B/.watch.lock']
        require(all(os.path.samefile(locks[0],path) for path in locks),'watch lock identity split')
        fixture.publish('A')
        start=session.child.trace.stat().st_size
        require(session.child.trace.read_bytes().endswith(b'\n'),'restored A boundary is not a complete raw line')
        results.append(session.prompt(prompts[2]))
        restored,history_a=collect_native_phase(session,fixture,'A',start,root/'phase-restored-A.trace')
        require(history_a==history and session.child.capture_generation()==before_capture,'restored A changed native generation/history')
        require(not provider.failures and len(provider.requests)>=3,'local synthetic provider failed or missing turns')
        texts=[text for request in provider.requests for text in r04.provider_texts(request)]
        require(all(any(prompt in text for text in texts) for prompt in prompts),'provider did not observe every actual turn')
        require(all(prompt in json.dumps(provider.requests[-1]) for prompt in prompts),'native conversation history did not persist')
        session.close()
        require(session.child.cleanup['exit']==0 and not session.child.cleanup['forced_signals'],
                'actual client required forced/nonzero shutdown')
        full=session.child.trace.read_bytes()
        require(full[:after['End']]==Path(after['Trace']['Path']).read_bytes(),'sealed B interval changed or hid restored A')
        final_audit=audit_host_connections(full,before_capture,root,ports={provider.server.server_port},
            unix_paths=[fixture.bridge.path],final=True)
        require(all(checked_pin(ROOT/name)==pin for name,pin in source_pins.items()),'maintained source changed during run')
        result.update(state='passed',NativePASS=True,native_session=session.identity,client=client,
            generation=generation,readback=readback,Before=before,After=after,RestoredA=restored,
            public_forward=public_forward,public_reverse=public_reverse,results=results,
            audit=dict(A=audit_a,B=audit_b,full=final_audit),scope='one owned Agy Go3 A/B/restored-A interval; not global refresh or legacy Python2 reverse')
        roundtrip.write(evidence/'provider-requests.json',provider.requests)
        roundtrip.write(evidence/'native-cleanup.json',session.child.cleanup)
    finally:
        errors=[];active_error=sys.exc_info()[0] is not None
        for name,owner in [('native',session),('provider',provider),('fixture',fixture)]:
            if owner is not None:
                try:owner.close()
                except BaseException as error:errors.append(name+':'+type(error).__name__)
        if cluster is not None:
            try:
                stopped=cluster.stop()
                cleanup=dict(stopped=stopped,pids=pg_pids,all_absent=all(p>1 and not Path(f'/proc/{p}').exists() for p in pg_pids),group_signals=False)
                roundtrip.write(evidence/'pg-cleanup.json',cleanup)
                if not stopped or not cleanup['all_absent']:errors.append('PG cleanup unresolved')
            except BaseException as error:errors.append('PG:'+type(error).__name__)
        if session is not None:
            if hasattr(session.child,'cleanup'):roundtrip.write(evidence/'native-cleanup.json',session.child.cleanup)
            else:errors.append('native cleanup receipt unavailable')
        if fixture is not None:
            # Stat-only custody for every credential inode alias and API home.
            try:
                custody=roundtrip.snapshot_root(root,fixture.unit['Credential'],api_home=root/'api-home')
                roundtrip.write(evidence/'owned-root-custody.json',custody)
                archive=evidence/'owned-root-nonprivate.tar.gz'
                with tarfile.open(archive,'x:gz') as output:
                    for name,row in custody.items():
                        if 'sha256' in row:
                            require(roundtrip.sha(name)==row['sha256'],'asset changed before archive')
                            require(fixture.host_dsn.encode() not in Path(name).read_bytes(),'raw fixture DSN in native asset')
                            output.add(name,arcname=str(Path(name).relative_to(root)),recursive=False)
                archive.chmod(0o400)
            except BaseException as error:errors.append('custody:'+type(error).__name__)
        try:
            require(all(tool_pin((pg/name).resolve())==pin for name,pin in approved['preflight']['pg_pins'].items()),'PG tool changed')
            assert_images_unchanged(approved['images'],approved['image_pins'])
            candidate=json.loads(Path(approved['packet']['candidate.json']['Path']).read_bytes())
            for role in ROLES:
                validate_build_recipe(role,candidate['roles'][role],approved['image_pins']['images'][role],approved['image_pins'])
            require(all(checked_pin(pin['Path'])==pin for pin in approved['packet'].values()),'image packet changed')
            verify_tool_provenance(Path(selected['manifest']['Path']).parent)
            for pin in selected['tools'].values():tool_pin(pin['Path'],pin['SHA256'])
            require({key:selected[key] for key in ('source_head','source_tree')}==native_source_identity(),
                    'driver source changed during native case')
            require(checked_pin(AGY_SOURCE,AGY_SHA256) and stat.S_IMODE(AGY_SOURCE.stat().st_mode)==0o500,'held client changed')
            require(approved['preflight']['parent']==dict(HOME=os.environ['HOME'],CODEX_HOME=os.environ.get('CODEX_HOME')),
                    'parent HOME/CODEX_HOME changed')
        except BaseException as error:errors.append('final-pins:'+type(error).__name__)
        if not errors:
            shutil.rmtree(runtime)
        if errors or active_error:result.update(state='failed',NativePASS=False)
        roundtrip.write(evidence/'native-result.json',dict(result,cleanup_errors=errors,PG_runtime_removed=not runtime.exists()))
        require(not errors,'owned cleanup unresolved: '+','.join(errors))
    return result


def validate_image_manifest(pins, *, source_head=BASE, source_tree=BASE_TREE):
    require(pins.get('schema') == 'cairn.r06-unit-images/1'
            and pins.get('source_head') == source_head and pins.get('source_tree') == source_tree,
            'exact selected image source tree required')
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


def checked_packet(manifest, manifest_sha256, index, index_sha256, complete, complete_sha256, *, native=False):
    """Pins select BUILD-ONLY bytes; they never admit a native client."""
    require(all((manifest, manifest_sha256, index, index_sha256, complete, complete_sha256)),
            'INPUT_NOT_READY: complete sealed image packet and exact pins required')
    selections = {name: checked_pin(path, expected) for name, path, expected in (
        ('manifest', manifest, manifest_sha256), ('index', index, index_sha256),
        ('build_complete', complete, complete_sha256))}
    root = Path(manifest).parent
    private_directory(root)
    pins = json.loads(Path(manifest).read_bytes(),object_pairs_hook=admission_pairs)
    source_head,source_tree = (pins.get('source_head'),pins.get('source_tree')) if native else (BASE,BASE_TREE)
    require(type(source_head) is str and re.fullmatch('[0-9a-f]{40}',source_head)
            and type(source_tree) is str and re.fullmatch('[0-9a-f]{40}',source_tree), 'exact image source required')
    require(subprocess.check_output(['git','rev-parse',source_head+'^{tree}'],cwd=ROOT,text=True,timeout=15).strip()==source_tree,
            'image source head/tree mismatch')
    require(Path(manifest).name == 'images-host.json' and Path(index) == root/'index.json'
            and Path(complete) == root/'build-complete.json', 'INPUT_NOT_READY: final packet filenames required')
    seal = json.loads(Path(index).read_bytes())
    require(seal.get('schema') == 'cairn.image-build-custody-index/1'
            and seal.get('source_head') == source_head and seal.get('source_tree') == source_tree,
            'INPUT_NOT_READY: selected sealed image index required')
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
            and done.get('source_head') == candidate.get('source_head') == source_head
            and done.get('source_tree') == candidate.get('source_tree') == source_tree
            and done.get('roles') == list(ROLES)
            and done.get('upstream_exact_tree_check_exit') == 0
            and done.get('all_owned_processes_joined') is True
            and done.get('source_archive_verified') is True
            and done.get('pids_gt_one') is True and done.get('group_signals') is False
            and done.get('source_tools_images_unchanged') is candidate.get('source_tools_images_unchanged') is True
            and candidate.get('source_edits') == candidate.get('tests_executed') == 0
            and candidate.get('native_model_or_service_fixtures') == 0,
            'INPUT_NOT_READY: completed selected BUILD_ONLY provenance required')
    for kind in ('build_exits', 'buildid_exits', 'buildinfo_exits'):
        require(done.get(kind) == {role: 0 for role in ROLES}, 'incomplete image build receipt '+kind)
    require(candidate['manifest']['path'] == done['manifest']['path'] == str(manifest)
            and candidate['manifest']['sha256'] == done['manifest']['sha256'] == manifest_sha256,
            'candidate/build completion selected another manifest')
    checked_pin(root/'candidate.json', done['candidate']['sha256'])
    validate_image_manifest(pins,source_head=source_head,source_tree=source_tree)
    require(set(candidate['roles']) == set(ROLES) and candidate.get('distinct_images') is True
            and candidate.get('helper_observer_alias') is False, 'wrong five-role build composition')
    for role in ROLES:
        validate_build_recipe(role, candidate['roles'][role], pins['images'][role], pins)
    archive = candidate['source_archive']
    require(str(archive['path']) in indexed, 'full image-source archive is not sealed')
    selections['source_archive'] = checked_pin(archive['path'], archive['sha256'])
    verify_source_archive(Path(archive['path']), pins['source_files'],revision=source_head)
    for name in ('candidate.json', 'tool-provenance-before.json', 'tool-provenance-after.json'):
        selections[name] = checked_pin(root/name)
    verify_tool_provenance(root)
    images, _ = roundtrip.images_from(Path(manifest), ROLES)
    if native:
        # Driver and image commits are separately selected. All Go/embedded
        # compilation inputs must nevertheless match the current checkout.
        current = subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).split(b'\0')
        def compiled(name):
            return Path(name).suffix in ('.go','.s','.c','.h','.sql','.ts','.html','.css','.js','.json') or name in ('go.mod','go.sum')
        inputs = {name.decode() for name in current if name and compiled(name.decode())}
        require(inputs and inputs=={name for name in pins['source_files'] if compiled(name)}
                and all(pins['source_files'].get(name)==r04.digest(ROOT/name) for name in inputs),
                'native images lack current Go/embedded source behavior')
        return images,pins,selections
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
    return images, pins, selections


def verify_source_archive(path, source_files, *, revision=BASE):
    listing = subprocess.check_output(['git', 'ls-tree', '-rz', revision], cwd=ROOT)
    expected = {}
    for row in listing.split(b'\0'):
        if row:
            prefix, name = row.split(b'\t', 1)
            expected[name.decode()] = prefix.split()[2].decode()
    require(set(source_files) == set(expected), 'image source map is not the complete selected tree')
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
            native_entry=PREPARATION_SCOPE, native_refresh='unobserved', actual_native_D1_D2_D5_D6='unestablished',
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
        complete_unit_qualified=False, scope='finite Go /3 target-aware public control; preparation only')
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
        result.update(state='passed', outcomes=outcomes, native_entry=PREPARATION_SCOPE,
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
                  refresh='unobserved', actual_case_stop=PREPARATION_SCOPE, pins=pins)
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
    selected_argv=list(sys.argv[1:] if argv is None else argv)
    if selected_argv and selected_argv[0]=='--api-bridge-client':
        require(len(selected_argv)>=2,'API bridge socket required')
        return api_bridge_client(Path(selected_argv[1]),selected_argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-stubbed', action='store_true')
    mode.add_argument('--prepare-authenticated', action='store_true')
    mode.add_argument('--prepare-target-observation', action='store_true')
    mode.add_argument('--native-unit', action='store_true', help='one explicitly pinned future disposable Agy case')
    parser.add_argument('--admission',type=Path)
    parser.add_argument('--admission-sha256')
    parser.add_argument('--attempt-id')
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
            try:
                approved=checked_native_admission(args.admission,args.admission_sha256,args.attempt_id)
            except (ValueError,OSError,AssertionError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as error:
                raise PrerequisiteUnavailable('INPUT_NOT_READY: '+str(error)) from error
            result=run_native_attempt(approved,native_preflight,execute_native)
            print(json.dumps(result,sort_keys=True))
            return 0
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
