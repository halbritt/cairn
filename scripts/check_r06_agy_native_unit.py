"""Owned Agy Go3 unit driver, authenticated preparation and connect audit.

Native execution requires one expiring, source/image/tool-pinned admission.
Preparation and finite nonnative checks never confer native admission.
"""
import argparse
import ast
import copy
import ctypes
from contextlib import contextmanager
import errno
from datetime import datetime, timezone
import hashlib
import ipaddress
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
import sysconfig
import tarfile
import tempfile
import threading
import time
import uuid

import check_r04_native_agy as r04
import r04_agy_trace as trace
import r06_agy_current_observation as collector
import check_r06_unbound_roundtrip as roundtrip
from disposable_host import DisposableHost, OwnedCluster, pg_bindir, stop_postgres
from test_r04_host_wiring import UnboundFixture


ROOT = Path(__file__).resolve().parents[1]
BASE = '15080de3b1205b161faa66f60b8faf93c30f18da'
BASE_TREE = '11ec0c51352505930e64236a99b929767e71cc2c'
ROLES = ('api', 'control', 'stager', 'helper', 'observer')
SCALE_SHAPE = 'UNIT_CONTROL_SCALE_FIXTURE_SHAPE'
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
SCRIPTED_CLIENT = r'''"""NONNATIVE stream-json rehearsal client; never Agy or a native PASS."""
import ctypes
import errno
import http.client
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import subprocess
import threading
import urllib.parse
import uuid

plan = json.loads(Path(sys.argv[1]).read_text())
endpoint = urllib.parse.urlsplit(plan['provider'])
if endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or not 1024 < endpoint.port < 65536:
    raise ValueError('rehearsal provider must be the owned loopback fixture')
conversation = str(uuid.uuid4())
history = Path(plan['home']) / 'rehearsal-history' / conversation / 'transcript.jsonl'
history.parent.mkdir(mode=0o700, parents=True)
history.touch(mode=0o600)
contents = []

def install_probe():
    probe=Path(plan['client']).parent/'.update_test123'
    fd=os.open(probe,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600);os.close(fd)
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.unlinkat(-100,os.fsencode(probe),0)!=0:raise OSError(ctypes.get_errno(),'probe removal')

install_probe()
updater=subprocess.run([plan['client'],'--bg-updater','--app_data_dir=antigravity-cli','--gemini_dir=.gemini'],
    capture_output=True,timeout=15)
if updater.returncode!=0:raise ValueError('NONNATIVE exact updater failed: '+updater.stderr.decode())

vendor_config=Path(plan['home'])/'.gemini/config'
projects=vendor_config/'projects';projects.mkdir(mode=0o700,exist_ok=True)
project_id=str(uuid.uuid4())
for name in ('mcp_config.json','.migrated'):
    path=vendor_config/name
    if path.exists():raise ValueError('unexpected preexisting vendor fixture file')
    path.write_text('{}\n');path.chmod(0o600)

def vendor_turn_churn():
    for destination in (vendor_config/'config.json',projects/(project_id+'.json')):
        temporary=destination.with_name(destination.name+'.'+str(uuid.uuid4())+'.tmp')
        with temporary.open('x') as stream:stream.write('{"NONNATIVE_vendor_churn":true}\n')
        temporary.chmod(0o600);os.replace(temporary,destination)

vendor_turn_churn()

def churn(message):
    internal = Path(plan['internal'])
    with (internal / 'rehearsal-data').open('ab') as stream:
        stream.write(b'owned concurrent client-internal churn\n')
    os.write(2, b'NONNATIVE rehearsal background stderr append\n')
    if message.get('target'):
        target = Path(message['target'])
        mode = target.stat().st_mode & 0o777 if target.exists() else 0o600
        if target.exists(): target.chmod(mode | 0o200)
        with target.open('ab') as stream:
            stream.write(b' ')
        target.chmod(mode)
    (internal / 'rehearsal-ack').write_text(message['nonce'])

def replace_client(fault):
    client = Path(plan['client'])
    if fault == 'client-rename':
        replacement = client.parent / 'replacement'
        replacement.write_bytes(b'NONNATIVE deliberately different client\n')
        replacement.chmod(0o500)
        os.replace(replacement, client)
    elif fault == 'client-sibling':
        (client.parent / '.old').write_bytes(b'NONNATIVE unwanted sibling\n')

def emit(value):
    sys.stdout.buffer.write((json.dumps(value, separators=(',', ':')) + '\n').encode())
    sys.stdout.buffer.flush()

def hook(event):
    payload = dict(conversationId=conversation, workspacePaths=[plan['workspace']],
                   host_pid=os.getpid(), hookEventName=event,
                   transcriptPath=str(history), fullyIdle=True)
    body = (json.dumps(payload, separators=(',', ':')) + '\n').encode()
    settings = json.loads(Path(plan['hooks']).read_text())['cairn-coordination']
    rows = settings[event]
    if len(rows) != 1:
        raise ValueError('exactly one genuine managed hook required')
    subprocess.run(shlex.split(rows[0]['command']), input=body, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

idle_stop = threading.Event()
def idle_background():
    # Real interleaving makes strace complete its outstanding stdin record.
    # These are genuine owned writes, not trace edits or synthetic resumes.
    while not idle_stop.wait(0.05):
        os.write(2, b'NONNATIVE idle background write\n')
idle_thread = threading.Thread(target=idle_background, daemon=True)
idle_thread.start()
emit(dict(event='init', conversation_id=conversation, init=dict(rehearsal=True)))
for raw in sys.stdin.buffer:
    message = json.loads(raw)
    if message.get('event') == 'rehearsal-churn':
        threading.Thread(target=churn, args=(message,)).start()
        continue
    if message.get('event') != 'user' or message.get('message', {}).get('role') != 'user':
        raise ValueError('expected stream-json user message')
    prompt = message['message']['content']
    hook('PreInvocation')
    contents.append(dict(role='user', parts=[dict(text=prompt)]))
    body = json.dumps(dict(contents=contents)).encode()
    connection = http.client.HTTPConnection('127.0.0.1', endpoint.port, timeout=15)
    try:
        connection.request('POST', '/v1beta/models/rehearsal:streamGenerateContent', body,
                           {'x-goog-api-key': 'r04-synthetic-unused', 'Content-Type': 'application/json'})
        response = connection.getresponse()
        reply = response.read()
        if response.status != 200 or not reply.startswith(b'data: '):
            raise ValueError('local synthetic fixture did not return its stream')
    finally:
        connection.close()
    with history.open('a') as stream:
        stream.write(json.dumps(dict(rehearsal=True, user=prompt)) + '\n')
    hook('Stop')
    vendor_turn_churn()
    if len(contents) == 1:
        replace_client(plan.get('fault'))
    emit(dict(event='result', result=dict(conversation_id=conversation, rehearsal=True)))
idle_stop.set()
idle_thread.join()
'''
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


class KernelCall(ctypes.Structure):
    _fields_=[('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint32)]


def network_filter():
    # linux/{seccomp,filter,bpf_common,audit}.h and asm/unistd_64.h. Reject
    # alternate ABIs and io_uring's socket operations as well as inet datagrams.
    rows=[('load',4),('eq',0xc000003e,'nr','deny'),('label','nr'),('load',0),
        ('set',0x40000000,'deny','calls'),('label','calls'),('eq',41,'socket','pair'),
        ('label','pair'),('eq',53,'socket','ring0'),('label','ring0'),('eq',425,'deny','ring1'),
        ('label','ring1'),('eq',426,'deny','ring2'),('label','ring2'),('eq',427,'deny','allow'),
        ('label','socket'),('load',16),('eq',socket.AF_UNIX,'allow','inet'),
        ('label','inet'),('eq',socket.AF_INET,'type','inet6'),
        ('label','inet6'),('eq',socket.AF_INET6,'type','deny'),('label','type'),('load',24),
        ('and',0xffffffff ^ (socket.SOCK_CLOEXEC|socket.SOCK_NONBLOCK)),
        ('eq',socket.SOCK_STREAM,'protocol','deny'),('label','protocol'),('load',32),
        ('eq',0,'allow','tcp'),('label','tcp'),('eq',socket.IPPROTO_TCP,'allow','deny'),
        ('label','deny'),('ret',0x00050000|errno.EACCES),('label','allow'),('ret',0x7fff0000)]
    labels={};position=0
    for row in rows:
        if row[0]=='label':labels[row[1]]=position
        else:position+=1
    program=[]
    for row in rows:
        if row[0]=='label':continue
        if row[0] in ('eq','set'):
            jumps=[labels[label]-len(program)-1 for label in row[2:]]
            require(all(0<=jump<=255 for jump in jumps),'invalid forward seccomp branch')
            program.append((0x15 if row[0]=='eq' else 0x45,*jumps,int(row[1])))
        else:program.append(({'load':0x20,'and':0x54,'ret':0x06}[row[0]],0,0,int(row[1])))
    return program


def install_network_policy(policy):
    strict_object(policy,('schema','tcp_ports'),'network policy')
    require(policy['schema']=='cairn.r06-kernel-network/1' and type(policy['tcp_ports']) is list
        and len(policy['tcp_ports'])<=16 and policy['tcp_ports']==sorted(set(policy['tcp_ports']))
        and all(type(port) is int and 1024<port<65536 for port in policy['tcp_ports']),'invalid network policy ports')
    require(os.uname().machine=='x86_64' and ctypes.sizeof(ctypes.c_void_p)==8,
            'unsupported seccomp syscall architecture')
    # The bootstrap has just execed; no untrusted code or threads have run.
    # Popen closes other FDs; reject any network socket retained as stdio too.
    for entry in list(Path('/proc/self/fd').iterdir()):
        try:
            info=entry.stat();target=os.readlink(entry)
        except FileNotFoundError:continue
        require(target!='anon_inode:[io_uring]','inherited io_uring descriptor')
        if stat.S_ISSOCK(info.st_mode):
            view=socket.socket(fileno=os.dup(int(entry.name)))
            try:require(view.family==socket.AF_UNIX,'inherited network descriptor')
            finally:view.close()
    libc=ctypes.CDLL(None,use_errno=True);libc.syscall.restype=ctypes.c_long
    def call(number,*arguments):
        result=libc.syscall(number,*arguments)
        if result<0:raise OSError(ctypes.get_errno(),os.strerror(ctypes.get_errno()))
        return result
    abi=call(444,None,0,1)
    require(abi>=4,'Landlock ABI4 network restriction required')
    require(libc.prctl(38,1,0,0,0)==0,'PR_SET_NO_NEW_PRIVS failed')
    class Ruleset(ctypes.Structure):_fields_=[('fs',ctypes.c_uint64),('net',ctypes.c_uint64)]
    class Port(ctypes.Structure):_fields_=[('access',ctypes.c_uint64),('port',ctypes.c_uint64)]
    attributes=Ruleset(0,2);fd=call(444,ctypes.byref(attributes),ctypes.sizeof(attributes),0)
    try:
        for port in policy['tcp_ports']:
            rule=Port(2,port);call(445,fd,2,ctypes.byref(rule),0)
        call(446,fd,0)
    finally:os.close(fd)
    program=network_filter();instructions=(KernelCall*len(program))(*(KernelCall(*row) for row in program))
    class Filter(ctypes.Structure):_fields_=[('length',ctypes.c_ushort),('instructions',ctypes.POINTER(KernelCall))]
    selected=Filter(len(program),instructions);call(317,1,0,ctypes.byref(selected))
    require(libc.prctl(39,0,0,0,0)==1 and libc.prctl(21,0,0,0,0)==2,'kernel filter readback failed')
    return dict(policy,landlock_abi=abi,handled_access_fs=0,handled_access_net=2,
        no_new_privs=1,seccomp_mode=2,architecture='x86_64',uid=os.getuid(),
        bpf_sha256=hashlib.sha256(bytes(instructions)).hexdigest(),
        scope='Inherited TCP connect PORT allowlist; Landlock does not filter IP addresses. Inet non-stream, other socket domains and io_uring are refused. Address coverage requires the separate descendant connect audit.')


def confined_exec(arguments):
    require(len(arguments)>=4 and arguments[2]=='--','network bootstrap arguments required')
    policy=json.loads(arguments[0],object_pairs_hook=admission_pairs);receipt=Path(arguments[1])
    require(receipt.is_absolute() and receipt.parent==receipt.parent.resolve(),'private policy receipt path required')
    private_directory(receipt.parent)
    # Exclusive creation also refuses a prewritten or symlinked policy receipt.
    with receipt.open('x',encoding='utf-8') as output:
        selected=install_network_policy(policy)
        json.dump(selected,output,sort_keys=True);output.write('\n');output.flush();os.fsync(output.fileno())
    receipt.chmod(0o400)
    argv=arguments[3:]
    require(Path(argv[0]).is_absolute(),'absolute confined executable required')
    os.execve(argv[0],argv,dict(os.environ))


CLIENT_FILE_SYSCALLS = (
    'open,openat2,creat,execveat,unlink,unlinkat,rename,renameat,renameat2,link,linkat,symlink,symlinkat,'
    'mkdir,mkdirat,rmdir,chdir,fchdir,dup,dup2,dup3,fcntl,close_range,writev,pwrite64,pwritev,pwritev2,'
    'truncate,ftruncate,fallocate,chmod,fchmod,fchmodat,fchmodat2,chown,fchown,lchown,fchownat,'
    'utime,utimes,futimesat,utimensat,mknod,mknodat,mmap,copy_file_range,sendfile,splice,tee,vmsplice,'
    'pipe,pipe2,socket,socketpair,accept,accept4,eventfd,eventfd2,epoll_create,epoll_create1,'
    'signalfd,signalfd4,timerfd_create,inotify_init,inotify_init1,memfd_create,pidfd_open,pidfd_getfd,'
    'unshare,setns,chroot,pivot_root'
)


class OwnedKernelTrace:
    """Owned traced process; the held Agy image requires consumed admission."""
    SYSCALLS = ('execve,openat,fstat,connect,read,write,close,clone,clone3,fork,vfork,'
                'readlink,wait4,waitid,exit,exit_group,'+CLIENT_FILE_SYSCALLS)
    STRING_LIMIT = 2048

    @classmethod
    def command(cls, destination, argv):
        tracer = shutil.which('strace')
        require(tracer is not None, 'strace prerequisite')
        tool_pin(Path(tracer).resolve())
        return [str(Path(tracer).resolve()), '-f', '-xx', '-s', str(cls.STRING_LIMIT),
                '-e', 'trace='+cls.SYSCALLS, '-e', 'abbrev=!fstat', '-o', str(destination), *argv]

    def __init__(self, argv, root, *, work=None, env=None, admission=None, ports=()):
        self.root = private_directory(root)
        self.work=Path(work) if work is not None else self.root
        self._kernel_updater_disabled=type(env) is dict and env.get('AGY_CLI_DISABLE_AUTO_UPDATE')=='1'
        require(type(argv) is list and argv and all(type(a) is str and '\0' not in a for a in argv),
                'literal fixture argv required')
        self.image = checked_pin(argv[0])
        info=Path(argv[0]).lstat()
        self.image_identity=(info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode),info.st_nlink,info.st_size,
                             info.st_mtime_ns,info.st_ctime_ns)
        self.client_parent=Path(argv[0]).parent if Path(argv[0])==self.root/'client/native-agy' else None
        self.client_parent_identity=client_directory_identity(self.client_parent) if self.client_parent else None
        if self.image['SHA256'] == AGY_SHA256:
            require(admission is not None, 'native image requires consumed pinned admission')
            selected=admission['admission']
            marker=json.loads(Path(selected['ledger']).read_bytes())
            require(marker.get('schema')=='cairn.r06-agy-native-attempt/1'
                    and marker.get('tuple')==selected and marker.get('state')=='consumed'
                    and self.root==Path(selected['catalog_root'])
                    and Path(argv[0])==self.root/'client/native-agy', 'native reservation/image/root disagree')
            require(type(env) is dict and env.get('AGY_CLI_DISABLE_AUTO_UPDATE')=='1'
                    and stat.S_IMODE(Path(argv[0]).parent.stat().st_mode)==0o700,
                    'native updater disable/owned0700 client directory required')
            require(type(ports) in (tuple,list,set) and len(ports)==1
                    and env.get('GOOGLE_GEMINI_BASE_URL')=='http://127.0.0.1:'+str(next(iter(ports))),
                    'native kernel port must be the actual local provider port')
        require(Path(argv[0]).is_relative_to(self.root), 'fixture image must be owned in-root')
        require(stat.S_IMODE(Path(argv[0]).lstat().st_mode) == 0o500, 'controlled0500 fixture image required')
        self.verify_client()
        require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'), 'pidfd prerequisite')
        self.trace = self.root/'kernel.trace'
        self.trace.touch(mode=0o600, exist_ok=False)
        self.stderr = (self.root/'kernel.stderr').open('xb')
        os.fchmod(self.stderr.fileno(),0o600)
        try:
            self.network_policy=dict(schema='cairn.r06-kernel-network/1',tcp_ports=sorted(ports))
            command=[sys.executable,'-B',str(Path(__file__).resolve()),'--confined-exec',
                json.dumps(self.network_policy),str(self.root/'kernel-policy.json'),'--',*self.command(self.trace,argv)]
            self.process = subprocess.Popen(command,
                cwd=work or self.root, env=dict(os.environ) if env is None else env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr,
                start_new_session=True,umask=0o077)
        except BaseException:
            self.stderr.close()
            raise
        self.pidfd, self.generation, self.closed = None, None, False
        try:
            def identify():
                if self.process.poll() is not None:
                    raise Refused('owned tracee exited before capture: '+(self.root/'kernel.stderr').read_text()[:8192])
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
            self.policy=json.loads((self.root/'kernel-policy.json').read_bytes())
            require({key:self.policy[key] for key in self.network_policy}==self.network_policy
                and self.policy['uid']==os.getuid() and self.policy['no_new_privs']==1
                and self.policy['seccomp_mode']==2 and self.policy['landlock_abi']>=4,
                'missing/forged kernel policy receipt')
            for selected_pid in (self.process.pid,self.pid):
                status=Path(f'/proc/{selected_pid}/status').read_text()
                require(re.search(r'^NoNewPrivs:\s+1$',status,re.M) and re.search(r'^Seccomp:\s+2$',status,re.M),
                        'tracer/tracee lacks inherited kernel restriction')
            policy_path=self.root/'kernel-policy.json'
            policy_info=policy_path.lstat()
            require(stat.S_ISREG(policy_info.st_mode) and policy_info.st_uid==os.getuid()
                    and stat.S_IMODE(policy_info.st_mode)==0o400 and policy_info.st_nlink==1,
                    'owned immutable kernel policy receipt required')
            self._kernel_capture=copy.deepcopy(self.generation)
            self._kernel_policy_pin=checked_pin(policy_path)
            self._kernel_policy_identity=policy_identity(policy_path)
            self._kernel_installer=checked_pin(Path(__file__).resolve())
            info=self.trace.lstat()
            self._kernel_trace_identity=(info.st_dev,info.st_ino,info.st_uid,info.st_nlink)
            require(self.generation['Client'] == self.image and self.generation['ClientArgv'] == argv,
                    'fixture identity/argv mismatch')
            self.verify_confinement()
        except BaseException:
            self.close()
            raise


    def verify_client(self):
        def identity():
            info=Path(self.image['Path']).lstat()
            return (info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode),info.st_nlink,info.st_size,
                    info.st_mtime_ns,info.st_ctime_ns)
        require(identity()==self.image_identity,'client image identity/mode/size changed')
        if self.client_parent:
            require(self.client_parent.resolve()==self.client_parent and
                self.client_parent.stat().st_uid==os.getuid() and
                stat.S_IMODE(self.client_parent.stat().st_mode)==0o700 and
                sorted(path.name for path in self.client_parent.iterdir())==['native-agy'] and
                client_directory_identity(self.client_parent)==self.client_parent_identity,
                'client directory identity or exact single-entry inventory changed')
        require(checked_pin(self.image['Path'])==self.image,'client image changed')
        require(identity()==self.image_identity,'client image changed during pin check')
        if self.client_parent:
            require(client_directory_identity(self.client_parent)==self.client_parent_identity and
                sorted(path.name for path in self.client_parent.iterdir())==['native-agy'],
                'client directory changed during pin check')
            if hasattr(self,'_kernel_capture'):
                self.client_file_audit=audit_client_directory(self.trace.read_bytes(),self._kernel_capture,
                    self.root,self.work,final=self.closed,enforcement=self)
        if hasattr(self,'_scripted_runtime_pins'):
            for path,pin in self._scripted_runtime_pins.items():require(tool_pin(path)==pin,'stand-in runtime image changed')
            if not self.closed:
                for path,pin in executable_mappings(self.pid).items():
                    require(path not in self._scripted_runtime_pins or self._scripted_runtime_pins[path]==pin,
                        'stand-in mapped runtime changed')
                    self._scripted_runtime_pins[path]=pin

    def verify_confinement(self):
        require(verified_kernel_policy(self,self.trace.read_bytes(),self.generation,self.root,
            self.network_policy['tcp_ports']),'actual child-bound kernel policy required')

    def capture_generation(self):
        self.verify_client()
        current = collector.capture_owned_generation(self.pid)
        require(current == self.generation, 'owned fixture generation changed')
        return current

    def snapshot(self, name):
        # Retain exactly what strace has emitted, including a partial last
        # record. The strict consumer decides whether that prefix is closed.
        raw = self.trace.read_bytes()
        require(raw and len(raw) <= collector.TRACE_LIMIT, 'empty/oversized kernel trace')
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
        self.verify_client()


def kernel_calls(raw, *, collecting=False):
    """Read the selected strace dialect; preserve original byte locators."""
    require(type(raw) is bytes and 0 < len(raw) <= collector.TRACE_LIMIT and raw.endswith(b'\n'),
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


def policy_identity(path):
    info=Path(path).lstat()
    return (info.st_dev,info.st_ino,info.st_uid,stat.S_IMODE(info.st_mode),info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def client_directory_identity(path):
    return policy_identity(path)[:-2]


def syscall_arguments(body):
    selected=re.fullmatch(r'\w+\((.*)\)\s+=\s+(.*)',body)
    require(selected is not None,'unresolved client-directory syscall framing')
    text=selected[1];arguments=[];stack=[];quoted=False;escaped=False;start=0
    for index,char in enumerate(text):
        if quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False
        elif char=='"':quoted=True
        elif char in '([{':stack.append(char)
        elif char in ')]}':
            require(stack and '([{'.index(stack.pop())==')]}'.index(char),'unresolved syscall nesting')
        elif char==',' and not stack:
            arguments.append(text[start:index].strip());start=index+1
    require(not quoted and not stack,'partial client-directory syscall arguments')
    arguments.append(text[start:].strip())
    return arguments,selected[2]


def client_execution_scope(raw,calls,capture,*,kernel_enforced,updater_disabled,final):
    leader=capture['Process']['PID'];client=capture['Client']['Path']
    parents={};known={leader};births={leader:-1}
    for row in calls:
        if row['name'] not in ('clone','clone3','fork','vfork'):continue
        result=row['body'].rsplit(' = ',1)[-1]
        if result.startswith('-1 '):continue
        require(result.isdigit() and int(result)>1 and int(result) not in known and row['pid'] in known,
            'unresolved client execution ancestry')
        parents[int(result)]=row['pid'];known.add(int(result))
        births[int(result)]=row['start']
    require(not any(row['name']=='execveat' for row in calls),'unselected client execveat')
    executions=list(r04.successful_execs(trace.exec_lines(raw.decode('ascii'),start_offset=0)))
    threads=kernel_threads(calls,leader);updater=None;version=None
    for execution in executions:
        pid=int(execution['pid']);argv=execution['argv'];path=execution['path']
        require(not argv or argv[0]!=client or path==client,'client argv/image path differs')
        if '--bg-updater' in argv:
            require(path==client and argv==[client,'--bg-updater','--app_data_dir=antigravity-cli','--gemini_dir=.gemini']
                and pid!=leader and updater is None and parents.get(pid) in threads
                and kernel_enforced and updater_disabled and checked_pin(client)==capture['Client'],
                'unapproved updater argv/image/parent/count/kernel policy')
            updater=pid
        elif path==client and pid!=leader:
            require(argv==[client,'--version'] and version is None,'nested client must be exact single --version')
            version=pid
    selected={pid for pid in (updater,version) if pid is not None}
    for pid in selected:
        require(len([row for row in calls if row['pid']==pid and row['name']=='execve'])==1,
            'updater/version child execs again')
    def child_tree(pid):
        if pid is None:return set(),None
        execution=next(row['start'] for row in calls if row['pid']==pid and row['name']=='execve')
        require(pid in births and births[pid]<execution,'child exec lacks prior attributed birth')
        tree={pid}
        while True:
            new={child for child,parent in parents.items() if parent in tree}-tree
            if not new:break
            require(all(births[child]>execution for child in new),'updater/version descendant predates approved exec')
            tree.update(new)
        return tree,execution
    updater_tree,updater_start=child_tree(updater)
    version_tree,_=child_tree(version)
    require(version is None or parents.get(version) in threads|updater_tree,
        'version child has unapproved creator')
    must_join=updater_tree|version_tree
    terminals={};stopping=set()
    for row in calls:
        pid=row['pid'];terminal=row['name']=='exit' and row['body'].startswith('+++')
        if pid in must_join:
            require(row['start']>births[pid] and pid not in terminals,'updater/version activity outside attributed lifetime')
            require(pid not in stopping or terminal,'updater/version activity after exit syscall')
            if terminal:require(row['body']=='+++ exited with 0 +++','updater/version descendant nonzero exit')
            elif row['name'] in ('exit','exit_group'):
                require(re.fullmatch(row['name']+r'\(0\)\s+= \?',row['body']),
                    'invalid updater/version exit syscall')
                stopping.add(pid)
        if row['name'] in ('clone','clone3','fork','vfork'):
            require(pid not in terminals,'child creator already exited')
        if terminal:terminals[pid]=row['start']
    joined=must_join<=terminals.keys()
    require(not final or joined,'unjoined updater/version descendant')
    return dict(updater=updater,version=version,updater_tree=updater_tree,version_tree=version_tree,
        updater_start=updater_start,parents=parents,joined=joined)


def audit_client_directory(raw,capture,root,work,*,final=False,enforcement=None):
    """Follow causal cwd/FD aliases; only complete zero-byte probes may mutate client/."""
    root,work=Path(root),Path(work);directory=root/'client';image=directory/'native-agy'
    leader=capture['Process']['PID']
    require(leader>1 and capture['Client']['Path']==str(image),'wrong client-directory capture')
    calls,pending=kernel_calls(raw)
    kernel_enforced=verified_kernel_policy(enforcement,raw,capture,root,
        enforcement.network_policy['tcp_ports']) if enforcement is not None else False
    execution_scope=client_execution_scope(raw,calls,capture,kernel_enforced=kernel_enforced,
        updater_disabled=getattr(enforcement,'_kernel_updater_disabled',False),final=final)
    paths={'unlink','unlinkat','rename','renameat','renameat2','link','linkat','symlink','symlinkat',
        'mkdir','mkdirat','rmdir','truncate','chmod','fchmodat','fchmodat2','chown','lchown','fchownat',
        'utime','utimes','futimesat','utimensat','mknod','mknodat'}
    opens={'open','openat','openat2','creat'}
    writes={'write','writev','pwrite64','pwritev','pwritev2','copy_file_range','sendfile','splice','tee','vmsplice'}
    fd_mutators={'ftruncate','fallocate','fchmod','fchown'}
    creators={'socket','accept','accept4','eventfd','eventfd2','epoll_create','epoll_create1',
        'signalfd','signalfd4','timerfd_create','inotify_init','inotify_init1','memfd_create','pidfd_open'}
    relevant=paths|opens|writes|fd_mutators|creators|{'chdir','fchdir','dup','dup2','dup3','fcntl',
        'close','close_range','pipe','pipe2','socketpair','pidfd_getfd','execve','execveat',
        'clone','clone3','fork','vfork','mmap','unshare','setns','chroot','pivot_root'}
    require(not final or not pending,'incomplete final client-directory coverage')
    require(not any(row['name'] in paths|opens|writes|fd_mutators for row in pending),
            'unfinished client-directory mutation coverage')
    cwd={leader:dict(path=work)};executables={leader:image}
    fds={leader:{0:dict(path=None,cloexec=False),1:dict(path=None,cloexec=False),
        2:dict(path=root/'kernel.stderr',cloexec=False)}}
    probes=[];active={};exited=set();absence_probes=[]
    def related(path):return path==directory or path.is_relative_to(directory) or directory.is_relative_to(path)
    def descriptor(pid,value):
        require(re.fullmatch(r'\d+',value) is not None,'unresolved client-directory FD')
        selected=fds[pid].get(int(value))
        require(selected is not None,'unresolved client-directory FD relation')
        return selected
    def pathname(pid,value,base='AT_FDCWD'):
        require(re.fullmatch(r'"(?:\\.|[^"\\])*"',value) is not None,'unresolved client-directory path')
        text=trace_string(value)
        if os.path.isabs(text):path=Path(os.path.normpath(text))
        else:
            parent=cwd[pid]['path'] if base=='AT_FDCWD' else descriptor(pid,base)['path']
            require(parent is not None,'non-file directory descriptor')
            path=Path(os.path.normpath(str(parent/text)))
        alias=re.fullmatch(r'/proc/(self|thread-self|[0-9]+)/(exe|cwd|fd/[0-9]+)(?:/(.*))?',str(path))
        if alias:
            owner=pid if alias[1] in ('self','thread-self') else int(alias[1])
            require(owner in cwd,'unresolved foreign proc path alias')
            if alias[2]=='exe':target=executables[owner]
            elif alias[2]=='cwd':target=cwd[owner]['path']
            else:target=descriptor(owner,alias[2].split('/')[1])['path']
            require(target is not None,'unresolved nonfile proc/FD alias')
            path=Path(os.path.normpath(str(target/(alias[3] or ''))))
        require(not str(path).startswith('/dev/fd/'),'unresolved device FD path alias')
        return path
    def reject_mutation(path):require(not related(path),'unexpected client-directory mutation')
    for row in calls:
        pid,name=row['pid'],row['name']
        require(pid in cwd and pid not in exited,'unattributed/reused client-directory PID')
        if name=='exit' and row['body'].startswith('+++'):
            exited.add(pid);continue
        if name not in relevant:continue
        args,result=syscall_arguments(row['body'])
        number=re.match(r'^(-?\d+)(?:\s|$)',result)
        returned=int(number[1]) if number else None
        if name in ('clone','clone3','fork','vfork'):
            if returned is not None and returned>0:
                require('CLONE_NEW' not in row['body'],'unresolved changed filesystem namespace')
                require(returned not in cwd,'reused client-directory descendant')
                cwd[returned]=cwd[pid] if 'CLONE_FS' in row['body'] else dict(cwd[pid])
                fds[returned]=fds[pid] if 'CLONE_FILES' in row['body'] else {fd:dict(value) for fd,value in fds[pid].items()}
                executables[returned]=executables[pid]
            continue
        if name in ('execve','execveat'):
            if result=='0':
                executables[pid]=pathname(pid,args[0]) if name=='execve' else pathname(pid,args[1],args[0])
                fds[pid]={fd:dict(value) for fd,value in fds[pid].items() if not value['cloexec']}
            continue
        if name in opens:
            index=1 if name in ('openat','openat2') else 0
            path=pathname(pid,args[index],args[0] if index else 'AT_FDCWD')
            flags=args[index+1] if name!='creat' else 'O_CREAT|O_WRONLY|O_TRUNC'
            mutation=any(flag in flags for flag in ('O_CREAT','O_TRUNC','O_TMPFILE','O_WRONLY','O_RDWR'))
            if related(path) and mutation:
                require(path.parent==directory and re.fullmatch(r'\.update_test[0-9]+',path.name)
                    and 'O_CREAT' in flags and 'O_EXCL' in flags and not any(flag in flags for flag in ('O_TRUNC','O_TMPFILE'))
                    and returned is not None and returned>=0 and path not in active,
                    'unexpected client-directory create/open')
                probe=dict(path=str(path),created=row['start'],creator_pid=pid,written_bytes=0,removed=None)
                probes.append(probe);active[path]=probe
            require(path.parent!=directory or path.name not in ('.new','.new.tar.gz'),
                'open/create of failed-absence name rejected')
            if returned is not None and returned>=0:fds[pid][returned]=dict(path=path,cloexec='O_CLOEXEC' in flags)
        elif name in writes|fd_mutators:
            index=2 if name in ('copy_file_range','splice') else 1 if name=='tee' else 0
            path=descriptor(pid,args[index])['path']
            if path is not None and related(path):
                require(name in writes and returned==0 and path in active,
                    'write or metadata change in client directory')
        elif name in ('unlink','unlinkat'):
            index=1 if name=='unlinkat' else 0
            path=pathname(pid,args[index],args[0] if index else 'AT_FDCWD')
            if related(path):
                if path.parent==directory and path.name in ('.new','.new.tar.gz'):
                    require(name=='unlinkat' and args[2] in ('0','AT_REMOVEDIR')
                        and result=='-1 ENOENT (No such file or directory)'
                        and pid in execution_scope['updater_tree'] and row['start']>execution_scope['updater_start'],
                        'unapproved failed-absence probe')
                    absence_probes.append(dict(label='FAILED_ABSENCE_PROBE',pid=pid,name=path.name,
                        path=str(path),flags=args[2],result=result,mutation=False,start=row['start'],end=row['end']))
                else:
                    require(path in active and result=='0','unexpected/persistent client-directory unlink')
                    active.pop(path)['removed']=row['end']
        elif name in ('rename','renameat','renameat2','link','linkat'):
            if name in ('rename','link'):old,new=pathname(pid,args[0]),pathname(pid,args[1])
            else:old,new=pathname(pid,args[1],args[0]),pathname(pid,args[3],args[2])
            reject_mutation(old);reject_mutation(new)
            if name.startswith('rename') and result=='0':
                for table in fds.values():
                    for value in table.values():
                        if value['path'] is not None and value['path'].is_relative_to(old):value['path']=new/value['path'].relative_to(old)
                for value in cwd.values():
                    if value['path'].is_relative_to(old):value['path']=new/value['path'].relative_to(old)
        elif name in ('symlink','symlinkat'):
            link=pathname(pid,args[1]) if name=='symlink' else pathname(pid,args[2],args[1])
            text=trace_string(args[0])
            target=Path(os.path.normpath(text if os.path.isabs(text) else str(link.parent/text)))
            reject_mutation(target);reject_mutation(link)
        elif name in paths:
            at=name.endswith('at') or name=='fchmodat2'
            reject_mutation(pathname(pid,args[1] if at else args[0],args[0] if at else 'AT_FDCWD'))
        elif name=='mmap':
            if 'PROT_WRITE' in args[2] and 'MAP_SHARED' in args[3] and not ('MAP_ANONYMOUS' in args[3] and args[4]=='-1'):
                path=descriptor(pid,args[4])['path']
                if path is not None:reject_mutation(path)
        elif name=='chdir':
            path=pathname(pid,args[0])
            if result=='0':cwd[pid]['path']=path
        elif name=='fchdir':
            path=descriptor(pid,args[0])['path'];require(path is not None,'unresolved fchdir target')
            if result=='0':cwd[pid]['path']=path
        elif name=='close':
            if result=='0':fds[pid].pop(int(args[0]),None)
        elif name in ('dup','dup2','dup3') or (name=='fcntl' and args[1] in ('F_DUPFD','F_DUPFD_CLOEXEC')):
            source=descriptor(pid,args[0])
            if returned is not None and returned>=0:fds[pid][returned]=dict(source,cloexec='CLOEXEC' in row['body'])
        elif name=='fcntl' and args[1]=='F_SETFD':
            source=descriptor(pid,args[0])
            if result=='0':source['cloexec']='FD_CLOEXEC' in args[2]
        elif name=='close_range':
            require(re.fullmatch(r'\d+',args[0]) and (args[1]=='~0U' or re.fullmatch(r'\d+',args[1])),
                'unresolved FD close range')
            if result=='0':
                fds[pid]={fd:dict(value) for fd,value in fds[pid].items()} if 'CLOSE_RANGE_UNSHARE' in args[2] else fds[pid]
                low,high=int(args[0]),2**32-1 if args[1]=='~0U' else int(args[1])
                for fd in list(fds[pid]):
                    if low<=fd<=high:
                        if 'CLOSE_RANGE_CLOEXEC' in args[2]:fds[pid][fd]['cloexec']=True
                        else:del fds[pid][fd]
        elif name in ('pipe','pipe2','socketpair') and result=='0':
            pair=re.fullmatch(r'\[(\d+), (\d+)\]',args[3] if name=='socketpair' else args[0])
            require(pair is not None,'unresolved pipe/socket pair descriptors')
            for fd in pair.groups():fds[pid][int(fd)]=dict(path=None,cloexec='CLOEXEC' in row['body'])
        elif name in creators and returned is not None and returned>=0:
            fds[pid][returned]=dict(path=None,cloexec='CLOEXEC' in row['body'])
        elif name=='pidfd_getfd':require(returned is not None and returned<0,'unresolved imported descriptor')
        elif name in ('unshare','setns','chroot','pivot_root'):
            require(result.startswith('-1 '),'unresolved changed filesystem namespace/root')
    require(not active,'persistent client-directory probe')
    return dict(NativePASS=False,probes=probes,failed_absence_probes=absence_probes,traced_pids=sorted(cwd),
        raw_sha256=hashlib.sha256(raw).hexdigest(),scope='Client-directory mutations only; not currentness or network acceptance')


def verified_kernel_policy(owner,raw,capture,root,ports):
    if owner is None:return False
    # This is the owning tracer's retained observation, never a wire boolean or
    # caller JSON assertion. Its capture and exact raw prefix must still match.
    require(type(owner) is OwnedKernelTrace and owner.root==root
            and owner._kernel_capture==capture,'kernel policy is not bound to this actual capture/root')
    path=root/'kernel-policy.json'
    require(checked_pin(path)==owner._kernel_policy_pin
            and policy_identity(path)==owner._kernel_policy_identity
            and checked_pin(Path(__file__).resolve())==owner._kernel_installer,
            'stale or altered kernel policy/installer binding')
    info=owner.trace.lstat()
    require(stat.S_ISREG(info.st_mode) and
        (info.st_dev,info.st_ino,info.st_uid,info.st_nlink)==owner._kernel_trace_identity,
        'kernel trace file identity differs')
    with owner.trace.open('rb') as stream:
        require(stream.read(len(raw))==raw,'kernel policy raw capture prefix differs')
    policy=json.loads(path.read_bytes())
    require(policy['schema']=='cairn.r06-kernel-network/1' and policy['landlock_abi']>=4
            and policy['handled_access_fs']==0 and policy['handled_access_net']==2
            and policy['seccomp_mode']==2 and policy['no_new_privs']==1
            and policy['architecture']=='x86_64' and policy['uid']==os.getuid()
            and policy['tcp_ports']==ports,'kernel policy does not enforce this exact selected port set')
    instructions=(KernelCall*len(network_filter()))(*(KernelCall(*row) for row in network_filter()))
    require(policy['bpf_sha256']==hashlib.sha256(bytes(instructions)).hexdigest(),'kernel filter program differs')
    return True


def audit_host_connections(raw, capture, root, *, ports, unix_paths, final=True, enforcement=None):
    """Finite connect(2) audit; neither egress enforcement nor currentness."""
    root = private_directory(root)
    require(Path(capture['Client']['Path']).is_relative_to(root)
            and checked_pin(capture['Client']['Path']) == capture['Client']
            and capture['ClientArgv'] and capture['ClientArgv'][0] == capture['Client']['Path'],
            'selected root image/argv pin differs')
    require(type(ports) in (set, list, tuple) and len(ports)==len(set(ports))
            and (type(ports) is set or list(ports)==sorted(ports))
            and all(type(p) is int and 1024 < p < 65536 for p in ports),
            'explicit owned loopback ports required')
    ports=sorted(ports)
    calls,pending=kernel_calls(raw)
    kernel_enforced=verified_kernel_policy(enforcement,raw,capture,root,ports)
    sockets = set()
    for selected in unix_paths:
        path = Path(selected)
        require(path.is_absolute() and path.resolve() == path and path.is_relative_to(root)
                and str(path) == os.path.normpath(str(path)), 'Unix selection outside owned root')
        info = path.lstat()
        require(stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
                and info.st_mode & 0o077 == 0, 'owned private Unix socket required')
        sockets.add(str(path))
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
    deaths,terminating = {},set()
    for row in calls:
        require(row['pid'] not in deaths, 'record after PID exit/reuse')
        terminal=row['name']=='exit' and row['body'].startswith('+++')
        require(row['pid'] not in terminating or terminal,'record after nonreturning exit syscall')
        if terminal:
            deaths[row['pid']] = row['start']
        elif row['name'] in ('exit','exit_group'):
            require(re.fullmatch(r'exit(?:_group)?\(-?\d+\)\s+= \?',row['body']),
                'unclassifiable nonreturning exit syscall')
            terminating.add(row['pid'])
    exited = set(deaths)
    require(not final or exited == owned, 'descendant interval does not include all exits')
    execution_scope=client_execution_scope(raw,calls,capture,kernel_enforced=kernel_enforced,
        updater_disabled=getattr(enforcement,'_kernel_updater_disabled',False),final=final)
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
            require(size==(16 if inet else 28) and 0<=port<65536
                    and ipaddress.ip_address(host).version==(4 if inet else 6),'invalid inet sockaddr size/address')
            selected_destination=host==('127.0.0.1' if inet else '::1') and port in ports
            kernel_denied=not selected_destination and port not in ports and kernel_enforced and result=='-1 EACCES (Permission denied)'
            require(selected_destination or kernel_denied,'external/unselected loopback connect attempt')
            destination = dict(family='AF_INET' if inet else 'AF_INET6',host=host,port=port)
        elif unix:
            path = trace_string(unix[1])
            # R04's sole outside-root allowance is a raw failed nscd lookup,
            # exactly sockaddr length110 and ENOENT.
            missing_nscd = path == '/var/run/nscd/socket' and size == 110 and result == '-1 ENOENT (No such file or directory)'
            require(missing_nscd or path in sockets and 2+len(path.encode()) <= size <= 110,
                    'foreign/unknown Unix connect attempt')
            destination = dict(family='AF_UNIX',path=path,missing_nscd=missing_nscd)
        else:
            raise Refused('unknown/truncated connect address family')
        connections.append(dict(pid=row['pid'],start=row['start'],end=row['end'],
                                result=result,destination=destination,
                                kernel_denied=bool((inet or inet6) and kernel_denied)))
    return dict(schema='cairn.r06-host-connect-audit/1',connections=connections,
        root_pid=leader,traced_pids=sorted(owned),parents=parents,interval=dict(Start=0,End=len(raw)),
        raw_sha256=hashlib.sha256(raw).hexdigest(),all_observed_descendants_exited=exited==owned,
        kernel_enforced=kernel_enforced,
        updater_pid=execution_scope['updater'],version_pid=execution_scope['version'],
        kernel_policy=enforcement._kernel_policy_pin if kernel_enforced else None,
        pending_nonconnect=[dict(pid=r['pid'],name=r['name'],start=r['start']) for r in pending],
        ExternalCoverage='Observed connect(2) attempts only in this raw interval for the selected exec and kernel-derived descendants. Includes failed attempts. '+
            ('Capture-bound kernel policy verified; TCP restriction is port-only, not an IP firewall. ' if kernel_enforced else 'No kernel egress enforcement. ')+
            'This audit excludes sendto/sendmsg/unconnected UDP, inherited/preconnected descriptors, tracer/controller traffic, future interval and global coverage.',
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
                    started=time.monotonic()
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
                        pid=process.pid,joined=True,response_substitution=False,
                        command_ms=round((time.monotonic()-started)*1000,3)))
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
    return run_reserved_case(approved,execute,reservation,
        approved.get('admission',{}).get('evidence'),'native-result.json')


def run_reserved_case(approved,execute,reservation,evidence,result_name):
    """Common first-error/result/atomic-terminal boundary for distinct ledgers."""
    result = dict(state='failed', application_start_boundary_entered=True)
    try:
        result = execute(approved)
        require(type(result) is dict and result.get('state') == 'passed', 'native unit did not pass')
        return result
    except BaseException as error:
        failure=dict(error_type=type(error).__name__,error=str(error))
        result=dict(state='failed',NativePASS=False,application_start_boundary_entered=True,
                    archive_state='pending-after-terminal',**failure)
        if evidence:
            existing=Path(evidence)/result_name
            if existing.exists():
                result=dict(json.loads(existing.read_bytes()),state='failed',NativePASS=False,
                            application_start_boundary_entered=True,archive_state='pending-after-terminal')
                for key,field in failure.items():result.setdefault(key,field)
                if any(result[key]!=field for key,field in failure.items()):result['finalization_error']=failure
        if 'RehearsalPASS' in result:result['RehearsalPASS']=False
        raise
    finally:
        try:
            if evidence:roundtrip.write(Path(evidence)/result_name,result)
        except BaseException as error:
            result=dict(result,state='failed',NativePASS=False,result_write_error=type(error).__name__)
            if 'RehearsalPASS' in result:result['RehearsalPASS']=False
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
    parent = root/'client'
    parent.mkdir(mode=0o700)
    destination = parent/'native-agy'
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
        parent.chmod(0o700)
        sync_directory(parent)
        sync_directory(root)
        return dict(pin=selected, device=info.st_dev, inode=info.st_ino,
                    source_device=before.st_dev, source_inode=before.st_ino)
    except BaseException:
        parent.chmod(0o700)
        destination.unlink(missing_ok=True)
        parent.rmdir()
        raise


class ScaleUnboundFixture(UnboundFixture):
    """Maintained producer inputs for three explicitly synthetic coordinators."""
    def configuration_plan(self,**kwargs):
        plan=super().configuration_plan(**kwargs)
        inputs=Path(plan['coordination_configs'][0]['path']).parent
        for index in (1,2):
            home=self.root/f'unbound-home-{index}';home.mkdir(mode=0o700,exist_ok=True)
            state=self.root/f'unbound-state-{index}';state.mkdir(mode=0o700,exist_ok=True)
            config=dict(self.coord,binding=f'scale-unbound-{index}',config_home=str(home),state_dir=str(state),
                opaque=['café',900719925474099312345])
            path=inputs/f'coordination-{index}.json'
            path.write_bytes((json.dumps(config,indent=2)+'\n').encode());path.chmod(0o600)
            plan['coordination_configs'].append(dict(path=str(path),sha256=r04.digest(path)))
        return plan


class NativeUnitFixture:
    """Maintained genuine Go3 material and real disposable authenticated API."""
    def __init__(self, root, images, cluster, evidence, *, scale=False):
        self.root,self.images,self.evidence=Path(root),images,Path(evidence)
        self.scale=scale;self.peers={}
        self.host,self.bridge=None,None
        self.published=None
        require(not self.root.exists(),'producer root must be fresh')
        case=r04.R04HostWiringTests(methodName='runTest')
        case.root,case.binary,case.stager=self.root.parent,images['control'],images['stager']
        if scale:
            self.f=ScaleUnboundFixture(self.root,images['control'],stager=images['stager'],harness='agy')
            self.f.hook_root=self.root/'hook-generations';self.f.hook_root.mkdir(mode=0o700)
            self.f.hook_plan=dict(harness='agy',coordination_index=0,replace_commands=dict(memory=[],coordination=[]))
        else:self.f=case.fixture(self.root.name,harness='agy')
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
            roundtrip.write(evidence/'fixture-command-runtime.json',dict(
                GORACE=self.host.env.get('GORACE'),baseline_environment_preserved=True,
                scope='Owned API CLI/public-control children inherit the normal host runtime options',
                race_instrumentation='as-built -race remains active',public_control_deadline_seconds=30))
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
            if scale:self.add_scale_peers()
            roundtrip.write(evidence/'native-produced-inputs.json',dict(unit=self.unit,generations=self.generations,
                source='maintained unbound Go3 producer; no native/session/status synthesized'))
        except BaseException:
            self.close()
            raise

    def add_scale_peers(self):
        """Eight bound producer groups alongside the three-member unbound group."""
        directory=self.root/'peers';directory.mkdir(mode=0o700)
        case=r04.R04HostWiringTests(methodName='runTest')
        case.root,case.binary,case.stager=directory,self.images['control'],self.images['stager']
        for index,harness in enumerate(('codex','claude')*4,1):
            name=f'bound-{index:02}.json';peer=case.fixture(f'b{index:02}',harness=harness,bound=True)
            # Inactive fixture peers have no native settings/readback. Keep
            # the factory's original settings assets, but declare fresh empty
            # homes as required by the existing census emptyHookLayer contract.
            profiles=peer.root/'profiles';profiles.mkdir(mode=0o700)
            account=profiles/'one';account.mkdir(mode=0o700)
            for config in (peer.coord,peer.memory):
                config.update(cairn=str(self.f.api),socket=str(self.host.socket),token_file=str(self.host.token('worker')),
                    repo=self.host.repo,config_home=str(account),opaque=['café',900719925474099312345])
                Path(config['state_dir']).mkdir(mode=0o700,exist_ok=True)
            peer.coord.update(binding=f'scale-bound-{index}',native_delivery=True)
            generations={}
            for label in ('A','B'):
                peer.coord['roundtrip_generation']=peer.memory['roundtrip_generation']=label
                r04.InstalledFixture.publish_configuration(peer,enabled=False)
                generations[label]=dict(Config=checked_pin(peer.coord_path),Memory=checked_pin(peer.memory_path),
                    Binding=checked_pin(peer.binding),Receipt=checked_pin(peer.receipt_path))
            self.peers[name]=generations
        for label in ('A','B'):self.refresh_scale_catalog(label,install=label=='A')

    def refresh_scale_catalog(self,label,*,install=False):
        selected=self.generations[label]
        receipt=json.loads(Path(selected['Receipt']['Path']).read_bytes())
        configs=receipt['coordination_configs']
        require(len(configs)==3,'scale producer did not publish three unbound coordinators')
        members={'coordination.json':selected['Config']}
        for index,descriptor in enumerate(configs[1:],1):
            members[f'unbound-{index}.json']=checked_pin(descriptor['path'],descriptor['sha256'])
        members.update({name:generations[label]['Config'] for name,generations in self.peers.items()})
        active=Path(self.unit['Selection'+label]['Directory'])
        if install:
            for name,pin in members.items():
                if name=='coordination.json':continue
                path=active/name;path.write_bytes(Path(pin['Path']).read_bytes());path.chmod(0o400)
        selection=dict(Directory=str(active),Pins={name:pin['SHA256'] for name,pin in members.items()})
        memories=[selected['Memory'],*[generations[label]['Memory'] for generations in self.peers.values()]]
        self.unit['Selection'+label]=selection
        self.unit['ScaleNext'+label]=members;self.unit['ScaleMemories'+label]=memories
        self.catalogs[label].update(Selections=[selection],Bindings=[selected['Binding'],
            *[generations[label]['Binding'] for generations in self.peers.values()]],UnboundReceipts=[selected['Receipt']])
        self.unit['Catalog'+label]=roundtrip.write(self.root/('catalog-'+label+'.json'),self.catalogs[label])
        if install:
            facts=unit_scale_input_facts(self.root,selection,memories,[self.host.token('worker')])
            roundtrip.write(self.evidence/'scale-producer-inputs.json',facts)

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
        if self.scale:self.refresh_scale_catalog('B')
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
        GOOGLE_GEMINI_BASE_URL=provider_url,NO_PROXY=LOCAL_PROVIDER['NO_PROXY'],AGY_CLI_DISABLE_AUTO_UPDATE='1')
    return env,settings


def open_native_session(approved,fixture,client,provider_url):
    """Use maintained stream-json receive/prompt/close, with the owned tracer."""
    env,settings=native_child_environment(fixture,provider_url)
    argv=[client['pin']['Path'],'--new-project','--settings',str(settings),'--model',r04.MODEL,
        '--input-format','stream-json','--output-format','stream-json','--print-timeout','30s','--print','']
    return open_stream_session(argv,fixture,env,provider_url,admission=approved)


def open_stream_session(argv,fixture,env,provider_url,*,admission=None):
    """Shared stream protocol and owning tracer; no client selection occurs here."""
    session=object.__new__(r04.AgySession)
    session.output,session.work=fixture.root,fixture.work
    session.child=OwnedKernelTrace(argv,fixture.root,work=fixture.work,env=env,admission=admission,
        ports=(int(provider_url.rsplit(':',1)[1]),))
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


SCRIPTED_UPDATER = r'''import ctypes,errno,os,subprocess,sys
from pathlib import Path
client=Path(sys.argv[1]);directory=client.parent
probe=directory/'.update_test456'
fd=os.open(probe,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC,0o600);os.close(fd)
libc=ctypes.CDLL(None,use_errno=True)
assert libc.unlinkat(-100,os.fsencode(probe),0)==0
version=subprocess.run([str(client),'--version'],capture_output=True,timeout=10)
assert version.returncode==0
for name in ('.new','.new.tar.gz'):
    for flag in (0,0x200):
        ctypes.set_errno(0)
        result=libc.unlinkat(-100,os.fsencode(directory/name),flag)
        assert result==-1 and ctypes.get_errno()==errno.ENOENT
'''


def stage_scripted_client(approved):
    """Embed the pinned normal CPython runtime so literal vendor child argv really executes."""
    root=Path(approved['rehearsal']['catalog_root'])
    directory=root/'client';directory.mkdir(mode=0o700)
    require(tool_pin(Path(sys.executable).resolve())==approved['tools']['python3'],
            'rehearsal interpreter changed')
    build=root/'scripted-build';build.mkdir(mode=0o700)
    source=build/'client.c';image=directory/'native-agy'
    source.write_text('#include <Python.h>\n#include <stdio.h>\n#include <string.h>\n'
        'int main(int argc,char **argv) {\n'
        ' if(argc==2 && !strcmp(argv[1],"--version")) {puts("NONNATIVE fixture version");return 0;}\n'
        ' if(argc==4 && !strcmp(argv[1],"--bg-updater") && '
        '!strcmp(argv[2],"--app_data_dir=antigravity-cli") && !strcmp(argv[3],"--gemini_dir=.gemini")) {\n'
        '  char *run[]={argv[0],"-I","-S","-B","-c",'+json.dumps(SCRIPTED_UPDATER)+',argv[0],NULL};\n'
        '  return Py_BytesMain(7,run);\n }\n return Py_BytesMain(argc,argv);\n}\n')
    source.chmod(0o400)
    config=Path(sys.executable).with_name(Path(sys.executable).name+'-config').resolve()
    compiler=Path(shutil.which('cc') or '/absent').resolve()
    library=(Path(sysconfig.get_config_var('LIBDIR'))/sysconfig.get_config_var('LDLIBRARY')).resolve()
    tools={name:tool_pin(path) for name,path in dict(config=config,compiler=compiler,libpython=library).items()}
    flags=[];queries=[]
    for option in ('--cflags','--ldflags'):
        process=subprocess.run([str(config),'--embed',option],capture_output=True,text=True,timeout=10)
        queries.append(dict(argv=process.args,exit=process.returncode,stdout=process.stdout,stderr=process.stderr))
        require(process.returncode==0,'pinned CPython build flags unavailable')
        flags.append(shlex.split(process.stdout))
    for name in ('cc1','as','collect2','ld'):
        query=subprocess.run([str(compiler),'-print-prog-name='+name],capture_output=True,text=True,timeout=10)
        queries.append(dict(argv=query.args,exit=query.returncode,stdout=query.stdout,stderr=query.stderr))
        require(query.returncode==0 and query.stdout.strip(),'stand-in compiler component unavailable')
        selected=Path(query.stdout.strip())
        if not selected.is_absolute():selected=Path(shutil.which(str(selected)) or '/absent')
        tools[name]=tool_pin(selected.resolve())
    dependencies=build/'headers.d'
    dependencies.touch(mode=0o600,exist_ok=False)
    header_scan=subprocess.run([str(compiler),*flags[0],'-M','-MF',str(dependencies),str(source)],
        capture_output=True,text=True,timeout=15)
    queries.append(dict(argv=header_scan.args,exit=header_scan.returncode,stdout=header_scan.stdout,stderr=header_scan.stderr))
    require(header_scan.returncode==0,'stand-in header scan failed')
    header_text=dependencies.read_text().replace('\\\n',' ')
    require(':' in header_text,'stand-in header scan receipt missing')
    headers_before={str(Path(path).resolve()):tool_pin(Path(path).resolve())
        for path in shlex.split(header_text.split(':',1)[1])}
    argv=[str(compiler),*flags[0],'-MD','-MF',str(dependencies),str(source),'-o',str(image),*flags[1]]
    process=subprocess.run(argv,capture_output=True,text=True,timeout=30)
    receipt=dict(kind='SCRIPTED_NONNATIVE',argv=argv,exit=process.returncode,stdout=process.stdout,
        stderr=process.stderr,queries=queries,source=checked_pin(source),tools=tools,NativePASS=False)
    roundtrip.write(build/'build.json',receipt)
    require(process.returncode==0,'nonnative embedded interpreter build failed')
    dependency_text=dependencies.read_text().replace('\\\n',' ')
    require(':' in dependency_text,'stand-in header dependency receipt missing')
    headers={str(Path(path).resolve()):tool_pin(Path(path).resolve())
        for path in shlex.split(dependency_text.split(':',1)[1])}
    require(headers==headers_before,'stand-in headers changed during compilation')
    require(str(Path('/usr/include/python'+str(sys.version_info.major)+'.'+str(sys.version_info.minor))/'Python.h') in headers,
        'stand-in build did not attest the selected CPython headers')
    dependencies.chmod(0o400)
    receipt.update(headers=headers,dependency_file=checked_pin(dependencies),joined=True)
    roundtrip.write(build/'build.json',receipt)
    image.chmod(0o500);directory.chmod(0o700)
    require(all(tool_pin(pin['Path'])==pin for pin in tools.values()),'stand-in build tools changed')
    require(checked_pin(image)['SHA256']!=AGY_SHA256,'stand-in must never select actual Agy')
    script=root/'scripted-client.py';script.write_text(SCRIPTED_CLIENT);script.chmod(0o400)
    return dict(pin=checked_pin(image),script=checked_pin(script),build=checked_pin(build/'build.json'),
        kind='SCRIPTED_NONNATIVE',actual_Agy=0)


def executable_mappings(pid):
    selected={}
    for line in Path(f'/proc/{pid}/maps').read_text().splitlines():
        parts=line.split(None,5)
        if len(parts)==6 and 'x' in parts[1] and parts[5].startswith('/'):
            path=Path(parts[5]);selected[str(path)]=tool_pin(path)
    require(selected,'stand-in executable mapping receipt empty')
    return selected


def open_scripted_session(approved,fixture,client,provider_url):
    require(client.get('kind')=='SCRIPTED_NONNATIVE' and client['pin']['SHA256']!=AGY_SHA256,
            'scripted rehearsal client required')
    env,_=native_child_environment(fixture,provider_url)
    internal=fixture.home/'.gemini/antigravity-cli';internal.mkdir(mode=0o700)
    (internal/'rehearsal-data').touch(mode=0o600)
    plan=fixture.root/'scripted-client-plan.json'
    roundtrip.write(plan,dict(kind='SCRIPTED_NONNATIVE',provider=provider_url,home=str(fixture.home),
        workspace=str(fixture.work),hooks=str(fixture.account/'hooks.json'),internal=str(internal),
        client=client['pin']['Path'],fault=approved.get('rehearsal_fault')))
    client['plan']=checked_pin(plan)
    argv=[client['pin']['Path'],'-I','-S','-B',client['script']['Path'],str(plan)]
    session=open_stream_session(argv,fixture,env,provider_url)
    approved['_opening_scripted_session']=session
    session.child._scripted_runtime_pins=executable_mappings(session.child.pid)
    client['runtime_initial']=roundtrip.write(fixture.root/'scripted-runtime-initial.json',
        dict(kind='SCRIPTED_NONNATIVE',pid=session.child.pid,images=session.child._scripted_runtime_pins))
    return session


def complete_trace_boundary(session,*,timeout=5):
    """Return one complete, validated byte snapshot; offset is its exact len."""
    def ready():
        with session.child.trace.open('rb') as stream:
            raw=stream.read(collector.TRACE_LIMIT+1)
        require(0<len(raw)<=collector.TRACE_LIMIT,'empty or oversized boundary trace')
        if not raw.endswith(b'\n'):return None
        calls,pending=kernel_calls(raw,collecting=True)
        threads=kernel_threads(calls,session.child.pid)
        if not (len(pending)<=1 and all(row['name']=='read' and row['body'].strip()=='read(0'
                and row['pid'] in threads for row in pending)):return None
        kernel_calls(raw)
        return raw
    return r04.wait_for(ready,'complete unchanged-dialect live boundary snapshot',timeout=timeout)


def client_config_file(path,account):
    try:parts=path.relative_to(account).parts
    except ValueError:return False
    canonical_uuid=r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
    if len(parts)==1:
        return parts[0] in ('.migrated','mcp_config.json','config.json') or bool(
            re.fullmatch(r'config\.json\.'+canonical_uuid+r'\.tmp',parts[0]))
    return len(parts)==2 and parts[0]=='projects' and bool(re.fullmatch(
        canonical_uuid+r'\.json(?:\.'+canonical_uuid+r'\.tmp)?',parts[1]))


def live_root_snapshot(fixture,session):
    """Protect the selected account across idle control after turn-boundary capture."""
    session.child.verify_client()
    root=fixture.root;internal=fixture.home/'.gemini/antigravity-cli'
    account=fixture.home/'.gemini/config';vendor_directories={account,account/'projects'}
    credential=Path(fixture.unit['Credential']);secret=credential.lstat()
    append_paths={session.child.trace,root/'kernel.stderr'}
    rows={};appends={};vendor_files={}
    def read_regular(path,expected,*,append=False):
        require(path.resolve()==path,'live snapshot path traverses a symlink')
        fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            observed=os.fstat(stream.fileno())
            require(stat.S_ISREG(observed.st_mode) and os.path.samestat(expected,observed)
                    and not os.path.samestat(observed,secret),'live snapshot file identity/credential alias changed')
            fields=('st_mode','st_uid','st_gid','st_nlink')
            require(all(getattr(expected,key)==getattr(observed,key) for key in fields),'live snapshot metadata changed')
            if append:
                raw=stream.read(collector.TRACE_LIMIT+1)
                require(len(raw)<=collector.TRACE_LIMIT,'live append exceeds fixed trace bound')
                return raw
            digest=hashlib.file_digest(stream,'sha256').hexdigest()
            final=os.fstat(stream.fileno())
            require(all(getattr(expected,key)==getattr(final,key) for key in
                (*fields,'st_size','st_mtime_ns','st_ctime_ns')),'protected file changed during snapshot')
            return digest
    def visit(path):
        try:info=path.lstat()
        except FileNotFoundError:
            require(client_config_file(path,account),'protected snapshot path disappeared')
            return
        row=dict(mode=info.st_mode,uid=info.st_uid,gid=info.st_gid,device=info.st_dev,
            inode=info.st_ino,bytes=info.st_size,links=info.st_nlink,mtime=info.st_mtime_ns,ctime=info.st_ctime_ns)
        if path==root/'client':
            require(stat.S_ISDIR(info.st_mode) and path.resolve()==path and info.st_uid==os.getuid()
                    and stat.S_IMODE(info.st_mode)==0o700
                    and sorted(entry.name for entry in path.iterdir())==['native-agy'],
                    'client directory identity or exact single-entry inventory changed')
            row.pop('mtime');row.pop('ctime')
        if client_config_file(path,account):
            require(stat.S_ISREG(info.st_mode) and path.resolve()==path and info.st_uid==os.getuid()
                    and info.st_nlink==1 and not os.path.samestat(info,secret),
                    'unsafe mutable vendor config ownership/type/credential alias')
        if path in vendor_directories:
            require(stat.S_ISDIR(info.st_mode) and path.resolve()==path and info.st_uid==os.getuid()
                    and stat.S_IMODE(info.st_mode)==0o700,'unsafe vendor config directory')
        if path==internal:
            require(stat.S_ISDIR(info.st_mode) and not path.is_symlink() and path.resolve()==path
                    and info.st_uid==os.getuid() and not info.st_mode&0o022,'unsafe mutable client subtree')
            for directory,dirs,files in os.walk(path,followlinks=False):
                for name in dirs+files:
                    entry=Path(directory)/name
                    try:observed=entry.lstat()
                    except FileNotFoundError:continue  # Only this explicitly mutable subtree may churn.
                    require(observed.st_uid==os.getuid() and not os.path.samestat(observed,secret),
                            'client subtree aliases protected ownership or credential')
                    if stat.S_ISLNK(observed.st_mode):
                        require(entry.resolve().is_relative_to(internal),'client subtree symlink escapes')
                    elif stat.S_ISREG(observed.st_mode):
                        require(observed.st_nlink==1,'client subtree hardlink escape')
            return
        if path in append_paths:
            require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1,
                    'live append must be an owned regular single-link file')
            raw=read_regular(path,info,append=True)
            appends[str(path)]=dict(identity={k:row[k] for k in ('mode','uid','gid','device','inode','links')},
                bytes=raw)
            return
        private=os.path.samestat(info,secret) or path.is_relative_to(fixture.host.home) or stat.S_IMODE(info.st_mode)==0
        if private:row['content_read']=False
        elif stat.S_ISREG(info.st_mode):row['sha256']=read_regular(path,info)
        elif stat.S_ISLNK(info.st_mode):row['link']=os.readlink(path)
        rows[str(path)]=row
        if client_config_file(path,account):vendor_files[str(path)]=dict(row,content_read=True)
        if stat.S_ISDIR(info.st_mode):
            require(path.resolve()==path,'protected directory traverses a symlink')
            for child in sorted(path.iterdir()):visit(child)
    visit(root)
    require(set(appends)=={str(path) for path in append_paths},'live append inventory differs')
    return dict(rows=rows,appends=appends,client_internal=str(internal),vendor_files=vendor_files)


def assert_live_preserved(before,after,config_root):
    require(before['client_internal']==after['client_internal'],'mutable client scope changed')
    require(set(before['appends'])==set(after['appends']),'live append inventory changed')
    for path,old in before['appends'].items():
        current=after['appends'][path]
        require(old['identity']==current['identity'] and current['bytes'].startswith(old['bytes']),
                'live trace/stderr was replaced, truncated or rewritten: '+path)
    require(roundtrip.protected(before['rows'],config_root)==roundtrip.protected(after['rows'],config_root),
            'live transition changed protected profile/source/credential/account/workspace/state data')


def assert_public_preserved(before,after,config_root,database_before,database_after):
    require(database_before==database_after,'public control changed protected API state')
    assert_live_preserved(before,after,config_root)


def live_unit_control(approved,fixture,session,client,body,evidence,label,*,reverse=False):
    """The public control contract with narrow, explicit live-client mutability."""
    path=evidence/(label+'.plan.json');selected=roundtrip.write(path,body)
    before=live_root_snapshot(fixture,session)
    database_before=roundtrip.api_snapshot(fixture.host.dsn,fixture.host.pg_bin,fixture.root/'state-placeholder')
    argv=[str(approved['images']['control']),'unit-control','reverse' if reverse else 'switch',
        '--plan',str(path),'--sha256',selected['SHA256']]
    interleaved=[]
    process_started=threading.Event()
    original=fixture.bridge.after_call
    def concurrent_background_write(api_argv):
        if interleaved:return
        require(process_started.wait(5),'public process did not start for rehearsal barrier')
        require(api_argv[-1] in roundtrip.READS,'rehearsal barrier requires a genuine public read')
        interleaved.append(dict(operation=api_argv[-1],control_pid=process.pid))
        nonce=str(uuid.uuid4())
        message=dict(event='rehearsal-churn',nonce=nonce)
        fault=approved.get('rehearsal_fault') if approved.get('rehearsal_fault_phase','forward')==(
            'reverse' if reverse else 'forward') else None
        if fault=='database':
            mutation=fixture.host.agent('worker','remember',
                'Selected NONNATIVE public-control database drift '+nonce,
                args=('--repo',fixture.host.repo,'--kind','note','--shareable',
                    '--request-id',nonce,'--stdin'))
            interleaved[-1]['database_mutation']=dict(operation='remember',
                record_id=mutation['record_id'],version=mutation['version'],
                transport='actual owned disposable API; unchanged response')
        elif fault:
            targets=dict(account=fixture.account/'hooks.json',workspace=fixture.work/'protected-fixture',
                state=next(Path(fixture.f.coord['state_dir']).glob('*.json')),cairn=Path(fixture.f.coord_path))
            require(fault in targets,'unknown nonnative protected mutation')
            message['target']=str(targets[fault])
        session.child.process.stdin.write((json.dumps(message)+'\n').encode());session.child.process.stdin.flush()
        ack=fixture.home/'.gemini/antigravity-cli/rehearsal-ack'
        r04.wait_for(lambda:ack.exists() and ack.read_text()==nonce,'owned concurrent stand-in acknowledgment',timeout=10)
        require(process.poll() is None,'public process exited before concurrent stand-in write')
    if client.get('kind')=='SCRIPTED_NONNATIVE':
        require(original is None,'unexpected existing rehearsal API interleaving')
        fixture.bridge.after_call=concurrent_background_write
    try:
        session.child.capture_generation();session.child.verify_confinement()
        started=time.monotonic()
        process=subprocess.Popen(argv,env=fixture.host.env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        process_started.set()
        try:out,err=process.communicate(timeout=150)
        except subprocess.TimeoutExpired:
            process.kill();out,err=process.communicate();raise
        elapsed=time.monotonic()-started
    finally:
        fixture.bridge.after_call=original
    (evidence/(label+'.stdout')).write_text(out);(evidence/(label+'.stderr')).write_text(err)
    roundtrip.write(evidence/(label+'.exit.json'),dict(exit=process.returncode,argv=argv,interleaved=interleaved,
        control_wall_seconds=elapsed,deadline_seconds=30,remaining_seconds=30-elapsed,
        timing_scope='Popen through joined exit, including inherited runtime exit behavior'))
    after=live_root_snapshot(fixture,session)
    database_after=roundtrip.api_snapshot(fixture.host.dsn,fixture.host.pg_bin,fixture.root/'state-placeholder')
    def receipt(snapshot):
        return dict(rows=snapshot['rows'],client_internal=snapshot['client_internal'],
            appends={p:dict(identity=v['identity'],bytes=len(v['bytes']),sha256=hashlib.sha256(v['bytes']).hexdigest())
                for p,v in snapshot['appends'].items()})
    roundtrip.write(evidence/(label+'.preservation.json'),dict(before=receipt(before),after=receipt(after),
        database_before=database_before,database_after=database_after))
    assert_public_preserved(before,after,Path(fixture.unit['ConfigRoot']),database_before,database_after)
    parsed=json.loads(out)
    require(process.returncode==0 and parsed['inspected'] and parsed['exchange']['Published'],
            'public live transition failed: '+str(parsed.get('error','unpublished result; see exact stdout receipt')))
    require(parsed['census']['NativeRefresh']=='unobserved' and parsed['census']['ExternalCoverage']=='unknown',
            'finite live transition claimed native qualification')
    if client.get('kind')=='SCRIPTED_NONNATIVE':
        require(elapsed<30,'nonnative public control exceeded the unchanged 30-second wall contract')
        require(len(interleaved)==1,'real public rehearsal interleaving was not reached')
    return parsed


def kernel_io(row):
    quoted=r'"(?:\\.|[^"\\])*"'
    match=re.fullmatch(r'(read|write)\((\d+), ('+quoted+r'), \d+\)\s+= (\d+)',row['body'])
    require(match is not None,'unknown/truncated callback or producer I/O')
    data=ast.literal_eval(match[3]).encode('latin1')
    require(len(data)==int(match[4]),'incomplete kernel I/O bytes')
    return int(match[2]),data


def kernel_hook_frame(row):
    match=re.match(r'write\(\d+, ("(?:\\.|[^"\\])*")',row['body'])
    if not match:return None
    prefix=ast.literal_eval(match[1]).encode('latin1')
    if not prefix.startswith(b'{'):return None
    purported=any(key in prefix for key in (b'"conversationId"',b'"hookEventName"'))
    try:
        _,data=kernel_io(row)
        payload=json.loads(data)
    except ValueError:
        if purported:raise
        return None
    if type(payload) is not dict or 'hookEventName' not in payload:return None
    require(type(payload) is dict and type(payload.get('conversationId')) is str and payload['conversationId']
            and type(payload.get('hookEventName')) is str and payload['hookEventName']
            and type(payload.get('workspacePaths')) is list and payload['workspacePaths']
            and all(type(path) is str and path for path in payload['workspacePaths']),
            'invalid purported native hook frame')
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
        require(session.child.trace.stat().st_size<=collector.TRACE_LIMIT,'raw trace exceeds fixed 256 MiB cap')
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
        owner=session.child
        scope=client_execution_scope(raw,calls,owner.generation,
            kernel_enforced=verified_kernel_policy(owner,raw,owner.generation,owner.root,
                owner.network_policy['tcp_ports']),
            updater_disabled=owner._kernel_updater_disabled,final=False)
        if not scope['joined']:return None
        return raw,callbacks,histories[0]
    raw,callbacks,history=r04.wait_for(finished,
        'actual emitted callbacks, stdin EOF, auxiliary-tree zero exits and idle frame',timeout=35)
    session.child.capture_generation();session.child.verify_confinement()
    destination.write_bytes(raw);destination.chmod(0o400)
    phase=collector.trace_interval(destination,start,len(raw),callbacks)
    return phase,history


def native_state_pins(state_dir,native_session):
    """Select the one product-written conversation state without synthesizing it."""
    require(type(native_session) is str and native_session.strip(),'nonempty client conversation required')
    paths=sorted(Path(state_dir).glob('*.json'))
    require(len(paths)==1,'expected exactly one actual native state')
    path=paths[0];selected=checked_pin(path)
    value=json.loads(path.read_bytes())
    require(type(value) is dict and value.get('schema')=='cairn.native-session/1','unknown actual native state')
    if 'native_session_id' in value:
        require(type(value['native_session_id']) is str and value['native_session_id']==native_session,
                'legacy outer native identity conflicts with the client')
    for field in ('registration','agent'):
        nested=value.get(field)
        require(type(nested) is dict and type(nested.get('native_session_id')) is str
                and nested['native_session_id'].strip(),'missing or malformed '+field+' native identity')
        require(nested['native_session_id']==native_session,'actual native state identity differs')
    require(checked_pin(path)==selected,'native state changed during identity read')
    return [selected]


def capture_native_catalogs(fixture,session,before_capture,before,after_capture,after,client):
    root=fixture.root
    generation=collector.write_generation_pair(root/'native-generation.json',before_capture,after_capture)
    states=native_state_pins(fixture.f.coord['state_dir'],session.identity)
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


PLANNED_CALLBACKS = 6
CLIENT_TRACE_OVERHEAD = 64*1024*1024


def scale_selection_facts(root,selection,credential_paths):
    """Observe eight real selected profiles; this is not producer or runtime proof."""
    root=Path(root)
    require(root.is_absolute() and root.resolve()==root and root.is_dir() and
        root.stat().st_uid==os.getuid() and stat.S_IMODE(root.stat().st_mode)==0o700,
        'private owned scale fixture root required')
    strict_object(selection,('Directory','Pins'),'scale selection')
    require(type(selection['Directory']) is str and type(selection['Pins']) is dict and
        len(selection['Pins'])==8,'scale fixture requires eight selected bindings')
    directory=Path(selection['Directory'])
    require(directory.is_absolute() and directory.resolve()==directory and directory.is_relative_to(root)
        and directory.is_dir() and directory.stat().st_uid==os.getuid() and
        not directory.stat().st_mode&0o022,'unsafe scale selection directory')
    names=sorted(path.name for path in directory.iterdir())
    require(set(selection['Pins'])=={name for name in names if name.endswith('.json')},
        'scale selection JSON inventory differs')
    require(type(credential_paths) in (list,tuple) and credential_paths,'scale credential selectors required')
    secrets=set()
    for selector in credential_paths:
        path=Path(selector)
        require(path.is_absolute() and path.resolve()==path and path.is_relative_to(root),
            'scale credential selector outside fixture')
        info=path.lstat();secrets.add((info.st_dev,info.st_ino))
    observations={}
    for name,pin in selection['Pins'].items():
        require(type(name) is str and Path(name).name==name and name.endswith('.json') and
            type(pin) is str and re.fullmatch('[0-9a-f]{64}',pin),'invalid scale profile pin')
        path=directory/name;info=path.lstat()
        require((info.st_dev,info.st_ino) not in secrets,'scale profile aliases credential inode')
        require(stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid() and info.st_nlink==1 and
            not info.st_mode&0o022 and path.resolve()==path,'unsafe scale profile owner/mode/alias')
        observations[name]=info
    profiles=[]
    fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
    for name,expected in sorted(observations.items()):
        path=directory/name
        fd=os.open(path,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW|os.O_NONBLOCK)
        with os.fdopen(fd,'rb') as stream:
            before=os.fstat(stream.fileno())
            require(all(getattr(before,k)==getattr(expected,k) for k in fields),
                'scale profile identity changed before read')
            require(before.st_size<=1048576,'scale profile exceeds bounded JSON size')
            raw=stream.read(1048577);after=os.fstat(stream.fileno())
        require(len(raw)==before.st_size and all(getattr(before,k)==getattr(after,k) for k in fields),
            'scale profile changed during read')
        require(hashlib.sha256(raw).hexdigest()==selection['Pins'][name],'scale profile pin changed')
        config=json.loads(raw,object_pairs_hook=admission_pairs)
        require(type(config) is dict and
            type(config.get('harness')) is str and config['harness'],'scale profile shape is incomplete')
        binding=config.get('inbox_recall_binding')
        require(binding is None or type(binding) is str and binding,'scale profile binding type differs')
        profiles.append(dict(pin=dict(Path=str(path),SHA256=selection['Pins'][name]),
            harness=config['harness'],unbound=binding is None,config_home=config.get('config_home')))
    require(names==sorted(path.name for path in directory.iterdir()),'scale directory inventory changed')
    unbound=sum(row['unbound'] for row in profiles)
    require(unbound==3,'scale fixture requires three actual unbound profiles')
    return dict(kind='NONNATIVE_SCALE_INPUT_OBSERVATION',binding_count=len(profiles),unbound_count=unbound,
        profiles=profiles,NativePASS=False,complete_unit_qualified=False,
        remaining_gates='genuine producer/receipt verification, full live trace, real control/bootstrap and cleanup')


def unit_scale_input_facts(root,selection,memories,credential_paths):
    """Observed synthetic fixture inputs, independent of B's installed shape."""
    root=private_directory(root)
    strict_object(selection,('Directory','Pins'),'unit scale selection')
    require(type(selection['Pins']) is dict and len(selection['Pins'])==11,'unit scale requires eleven members')
    directory=private_directory(selection['Directory'])
    require(directory.is_relative_to(root) and set(selection['Pins'])==
        {path.name for path in directory.iterdir() if path.name.endswith('.json')},'unit scale inventory differs')
    profiles=[];homes=set()
    def home(config):
        value=config.get('config_home')
        require(type(value) is str and Path(value).is_relative_to(root) and Path(value)!=root,
            'unit scale config_home must be declared inside catalog.Root')
        return str(private_directory(value))
    for name,pin in sorted(selection['Pins'].items()):
        require(Path(name).name==name and name.endswith('.json'),'unit scale member name differs')
        config,actual=fixture_source_json(directory/name,root,credential_paths,dict(Path=str(directory/name),SHA256=pin))
        require(type(config) is dict,'unit scale profile shape is incomplete')
        binding=config.get('inbox_recall_binding')
        require(binding is None or type(binding) is str and binding,
            'unit scale binding selector differs')
        if binding is not None:
            require(config.get('harness') in ('codex','claude') and config.get('native_delivery') is True,
                'unit scale bound profile must retain supported harness and native_delivery=true')
            require(not list(Path(home(config)).iterdir()),'unit scale requires an empty bound account without native readback')
        account=home(config);require(account not in homes,'unit scale coordinator homes are not distinct')
        homes.add(account);profiles.append(dict(pin=actual,config_home=account,unbound=binding is None))
    unbound=sum(row['unbound'] for row in profiles)
    require(unbound==3 and len(profiles)-unbound==8,'unit scale requires eight bound PLUS three unbound')
    require(type(memories) is list and len(memories)==9,'unit scale memory group inventory differs')
    observed_memories=[]
    for pin in memories:
        config,actual=fixture_source_json(pin['Path'],root,credential_paths,pin)
        require(home(config) in homes,'unit scale memory config_home is not a selected coordinator home')
        observed_memories.append(actual)
    return dict(kind=SCALE_SHAPE,binding_count=11,bound_count=8,unbound_count=3,profiles=profiles,
        memories=observed_memories,NativePASS=False,complete_unit_qualified=False,
        installed_roster_live_census='UNSUPPORTED/FIXTURE_ONLY')


def unit_control_plan(fixture,*,reverse=False):
    body=roundtrip.plan(fixture.unit,reverse=reverse)
    if fixture.scale:
        members=fixture.unit['ScaleNext'+('A' if reverse else 'B')]
        body['next']=[dict(name=name,path=str(Path(fixture.unit['ConfigRoot'])/'retained-A'/name)
            if reverse else pin['Path'],sha256=pin['SHA256']) for name,pin in members.items()]
        require(len(body['next'])==11 and len(body['current']['pins'])==11,'scale public plan lost a member')
    return body


def bootstrap_fixture_images(images,pins):
    """Map the validated five-role packet to B's local API, without new roles."""
    strict_object(images,ROLES,'bootstrap image roles')
    strict_object(pins['images'],ROLES,'bootstrap pinned image roles')
    require(all(str(images[role])==pins['images'][role]['path'] for role in ROLES),
        'bootstrap image path differs from packet')
    assert_images_unchanged(images,pins)
    return dict(api=images['api'],control=images['control'],stager=images['stager'],tests=images['observer'])


def production_fixture_plan(root):
    """Plan B's real directory/socket shape; never create a runtime directory."""
    root=Path(root)
    require(root.is_absolute() and root.resolve()==root and not root.exists(),
        'fresh normalized production fixture root required')
    private_directory(root.parent)
    fixture=root/'account-home/.local/share/cairn'
    require(len(os.fsencode(fixture/'api.sock'))<108,'production fixture Unix socket path too long')
    return dict(root=str(root),fixture_root=str(fixture),api_socket=str(fixture/'api.sock'),
        api_owner='B contextmanager; f.host.close()',caller_owned=['cluster','images','root','client','live_trace'])


def fixture_source_json(path,root,credential_paths,expected=None):
    """Bounded owned source input, excluding credential inodes before opening."""
    path=Path(path);root=private_directory(root)
    require(path.is_absolute() and path.resolve()==path and path.is_relative_to(root),
        'fixture JSON outside owned root or symlinked')
    secrets=set()
    require(type(credential_paths) in (list,tuple) and credential_paths,'fixture credential selectors required')
    for selector in credential_paths:
        credential=Path(selector)
        require(credential.is_absolute() and credential.resolve()==credential and credential.is_relative_to(root),
            'fixture credential selector outside root')
        info=credential.lstat();secrets.add((info.st_dev,info.st_ino))
    before=path.lstat()
    require((before.st_dev,before.st_ino) not in secrets,'fixture JSON aliases credential inode')
    require(stat.S_ISREG(before.st_mode) and before.st_uid==os.getuid() and before.st_nlink==1
        and not before.st_mode&0o022 and before.st_size<=1048576,'unsafe or oversized fixture JSON')
    fields=('st_dev','st_ino','st_mode','st_uid','st_gid','st_nlink','st_size','st_mtime_ns','st_ctime_ns')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        opened=os.fstat(stream.fileno())
        require(all(getattr(before,k)==getattr(opened,k) for k in fields),'fixture JSON changed before read')
        raw=stream.read(1048577);after=os.fstat(stream.fileno())
    current=path.lstat()
    require(len(raw)==before.st_size and all(getattr(before,k)==getattr(after,k) for k in fields)
        and all(getattr(before,k)==getattr(current,k) for k in fields),'fixture JSON changed during read')
    pin=dict(Path=str(path),SHA256=hashlib.sha256(raw).hexdigest())
    require(expected is None or pin==expected,'fixture JSON pin differs')
    return json.loads(raw,object_pairs_hook=admission_pairs),pin


def production_material_inputs(fixture,plan,pin,credential_paths):
    """Adapt actual B selection data, without inventing Go current/session facts."""
    root=Path(fixture.root)
    material,material_pin=fixture_source_json(root/'production-material.json',root,credential_paths)
    require(type(material) is dict and material.get('schema')=='cairn.r07-production-shape-material/1'
        and material.get('host')=='proximal' and material.get('native_admission') is False
        and material.get('root')==str(root),'unexpected production material scope')
    require(material['original_plan']==dict(Path=str(plan),SHA256=pin),'original plan selection differs')
    original,_=fixture_source_json(plan,root,credential_paths,material['original_plan'])
    target,_=fixture_source_json(material['target_plan']['Path'],root,credential_paths,material['target_plan'])
    require(original.get('schema')==target.get('schema')=='cairn.unit-bootstrap/2'
        and original.get('current')==material['original_current']
        and original.get('transition',{}).get('target')==material['target_plan']
        and target.get('current')==material['target_current'] and target.get('archives')==[],
        'production plan/material selection differs')
    current=material['target_current'];strict_object(current,('directory','pins'),'B target current')
    strict_object(material['original_current'],('directory','pins'),'B original current')
    require(type(material['original_current']['pins']) is dict and
        set(material['original_current']['pins'])==set(current['pins']),'production original member inventory differs')
    selection=dict(Directory=current['directory'],Pins=dict(current['pins']))
    facts=scale_selection_facts(root,selection,credential_paths)
    groups=material['groups'];require(type(groups) is list and groups,'production groups required')
    require(all(type(row) is dict and type(row.get('bound')) is bool and type(row.get('members')) is list
        and row['members'] for row in groups),'production group shape differs')
    members=[member for row in groups for member in row['members']]
    require(len(members)==len(set(members))==8 and set(members)==set(current['pins']),
        'production group member inventory differs')
    unbound=[row['target_receipt'] for row in groups if not row['bound']]
    for receipt in unbound:
        admission_pin(receipt,'production unbound receipt')
    require(len(unbound)==3 and unbound==material['target_unbound_receipts']
        and len({row['Path'] for row in unbound})==3
        and {member for row in groups if not row['bound'] for member in row['members']}==
            {Path(row['pin']['Path']).name for row in facts['profiles'] if row['unbound']},
        'production unbound receipt/member selection differs')
    boundaries=[dict(profile=row['pin'],field='config_home',reason='selected account coverage is unknown')
        for row in facts['profiles'] if type(row['config_home']) is not str or not row['config_home']]
    return dict(kind='NONNATIVE_PRODUCTION_SELECTION_INPUTS',material=material_pin,selection=selection,facts=facts,
        original_plan=material['original_plan'],target_plan=material['target_plan'],
        target_unbound_receipts=unbound,census_boundaries=boundaries,NativePASS=False,complete_unit_qualified=False)


def require_scale_census_inputs(observed):
    # A source precondition, never a substitute for the fresh real Go census.
    require(not observed['census_boundaries'],'production unit-census input lacks config_home; preserve inputs and report')


@contextmanager
def production_shape_adapter(cluster,packet,root):
    """Future packet-dependent runtime entry; caller owns PG/root/client/trace.

    Source-phase tests must not enter this context. B owns only its API join.
    The real producer/census and complete sequence remain separate open gates.
    """
    production_fixture_plan(root)
    keys=('manifest','index','build_complete')
    paths=[admission_pin(packet[key],key) for key in keys]
    images,pins,_=checked_packet(*[item for key,path in zip(keys,paths)
        for item in (path,packet[key]['SHA256'])],native=True)
    local=bootstrap_fixture_images(images,pins)
    import check_r07_bootstrap_reverse as bootstrap
    with bootstrap.production_shape_fixture(cluster,local,Path(root),'proximal') as (fixture,plan,pin):
        inputs=production_material_inputs(fixture,plan,pin,[fixture.host.token('worker')])
        yield fixture,plan,pin,local,inputs


def bootstrap_workflow_observation(result,stdout):
    """Check B receipt consistency; caller must retain the actual execution evidence."""
    proof='SHARED_CLI_WORKFLOW_WITH_FIXTURE_FENCE'
    unavailable='UNESTABLISHED until admitted R07 stopped boundary'
    require(type(result) is dict and result.get('result')=='PASS' and result.get('workflow_proof')==proof
        and type(result.get('deadline_seconds')) is int and result['deadline_seconds']==30
        and result.get('default_public_positive')==unavailable,'bootstrap workflow scope differs')
    negative=result.get('default_public_refusal')
    require(type(negative) is dict and negative.get('proof')=='DEFAULT_PUBLIC_REFUSAL_ACTIVE_WORKERS'
        and type(negative.get('unit_actions')) is int and negative['unit_actions']==0
        and type(negative.get('outside_owned_root_writes')) is int and negative['outside_owned_root_writes']==0,
        'default public active-worker refusal missing')
    refusal=negative.get('result',{})
    require(refusal.get('published') is False and refusal.get('inspected') is False
        and refusal.get('owner_fence')=='unknown'
        and 'unit or activation trigger is not stopped and masked' in refusal.get('error',''),
        'default public active-worker refusal differs')
    strict_object(stdout,('switch','reverse'),'bootstrap execution stdout')
    for operation,transition in (('switch','forward'),('reverse','reverse')):
        raw=stdout[operation]
        require(type(raw) is str and re.search(r'^=== RUN   TestUnitBootstrapOwnedTransition$',raw,re.M)
            and re.search(r'^--- PASS: TestUnitBootstrapOwnedTransition \([^\n]+\)$',raw,re.M)
            and '--- SKIP:' not in raw and 'no tests to run' not in raw,'selected non-skipped bootstrap test proof missing')
        require(result.get(transition,{}).get('published') is True
            and result[transition].get('rechecked') is True,'bootstrap semantic transition missing')
        for timings,margins in (('seconds','library_margin_seconds'),
                ('workflow_launch_to_join_seconds','workflow_margin_seconds')):
            strict_object(result.get(timings),('switch','reverse'),timings)
            strict_object(result.get(margins),('switch','reverse'),margins)
            elapsed=result[timings][operation];margin=result[margins][operation]
            require(type(elapsed) in (int,float) and 0<elapsed<30
                and type(margin) in (int,float) and margin==30-elapsed,
                'bootstrap fresh30s positive margin missing')
    return dict(kind='NONNATIVE_BOOTSTRAP_RECEIPT_OBSERVATION',workflow_proof=proof,
        default_public_refusal=negative['proof'],default_public_positive=unavailable,
        NativePASS=False,complete_unit_qualified=False,
        remaining_gates='actual raw reverse/state evidence, full unit-control/live trace, cleanup, independent review')


def trace_budget(callback_bytes):
    require(type(callback_bytes) is list and len(callback_bytes)==2
            and all(type(count) is int and count>0 for count in callback_bytes),'two measured genuine callbacks required')
    projected=max(callback_bytes)*PLANNED_CALLBACKS+CLIENT_TRACE_OVERHEAD
    require(projected<collector.TRACE_LIMIT,'measured callback budget exceeds fixed 256 MiB cap')
    return dict(callback_bytes=callback_bytes,planned_callbacks=PLANNED_CALLBACKS,
        client_overhead=CLIENT_TRACE_OVERHEAD,projected_bytes=projected,cap=collector.TRACE_LIMIT,
        margin_bytes=collector.TRACE_LIMIT-projected,
        overhead_basis='Explicit 64 MiB reserve for three-turn client trace traffic; not a new client measurement')


def measure_trace_budget(images,evidence):
    """Execute the genuine Go3 producer's two callbacks, never a native client."""
    evidence=Path(evidence);evidence.mkdir(mode=0o700)
    private_directory(evidence)
    control=checked_pin(images['control'])
    require(Path(control['Path']).stat().st_size>=24_000_000,'real common control image required, not a small stub')
    runtime=Path(tempfile.mkdtemp(prefix='r06b-',dir=Path.home()/'.cache'))
    cluster=fixture=None;measurements=[];pg_pids=[]
    original_images={role:checked_pin(image) for role,image in images.items()}
    try:
        copied=runtime/'control';shutil.copyfile(images['control'],copied);copied.chmod(0o500)
        require(checked_pin(copied)['SHA256']==control['SHA256'],'budget control copy differs')
        cluster=OwnedCluster.start(runtime/'pg',pg_bin=pg_bindir())
        pg_pids=owned_pg_processes(runtime/'pg/data/postmaster.pid')
        fixture=NativeUnitFixture(runtime/'fixture',dict(images,control=copied),cluster,evidence)
        native_id='synthetic-budget-'+str(uuid.uuid4())
        for index,row in enumerate(fixture.generations['A']['rows']):
            vector=shlex.split(Path(row['argv'][0]).read_text().splitlines()[1])
            require(vector[0]=='exec' and vector[-1]=='$@','genuine producer launcher required')
            argv=vector[1:-1]+row['argv'][1:]
            require(checked_pin(argv[0])['SHA256']==control['SHA256'],'actual callback is not the genuine control image')
            payload=dict(conversationId=native_id,workspacePaths=[str(fixture.work)],host_pid=os.getpid(),
                         hookEventName=('PreInvocation','Stop')[index],transcriptPath=str(runtime/'synthetic-history'),
                         fullyIdle=True,budgetPadding='x'*768)
            body=(json.dumps(payload,separators=(',',':'))+'\n').encode()
            require(1024<=len(body)<OwnedKernelTrace.STRING_LIMIT,
                    'budget frame must cover measured native payloads within the production string bound')
            env=dict(os.environ,ANTIGRAVITY_CLI_CONFIG_DIR=str(fixture.f.account))
            owner=OwnedKernelTrace(argv,runtime,work=fixture.work,env=env)
            try:
                require(owner.image['SHA256']==control['SHA256'],'callback image changed before capture')
                stdout,_=owner.process.communicate(body,timeout=30)
                roundtrip.write(evidence/f'callback-{index}-output.json',dict(argv=argv,exit=owner.process.returncode,
                    stdout=stdout.decode('utf-8'),kernel_stderr=(runtime/'kernel.stderr').read_text()))
            finally:
                owner.close()
                target=evidence/f'callback-{index}.trace';owner.trace.rename(target);target.chmod(0o400)
                (runtime/'kernel.stderr').rename(evidence/f'callback-{index}.stderr')
                (runtime/'kernel-policy.json').rename(evidence/f'callback-{index}-policy.json')
                roundtrip.write(evidence/f'callback-{index}-cleanup.json',owner.cleanup)
            require(owner.process.returncode==0,'genuine Go3 callback failed; see output/trace')
            raw=target.read_bytes();calls,pending=kernel_calls(raw)
            require(not pending and owner.cleanup['all_absent'] and not owner.cleanup['forced_signals'],
                    'budget callback did not join cleanly')
            threads=kernel_threads(calls,owner.pid)
            inputs=[kernel_io(call)[1] for call in calls if call['pid'] in threads
                    and call['name']=='read' and re.match(r'read\(0,',call['body'])]
            require(inputs and inputs[-1]==b'' and b''.join(inputs)==body,'budget callback lacks exact stdin/EOF')
            measurements.append(dict(event=payload['hookEventName'],bytes=len(raw),trace=checked_pin(target),
                argv=argv,stdin_sha256=hashlib.sha256(body).hexdigest(),stdin_bytes=len(body),cleanup=owner.cleanup,
                strace_argv=OwnedKernelTrace.command(owner.trace,argv),policy=owner.policy,
                image=owner.image,capture=owner.generation,installer=owner._kernel_installer,
                policy_pin=checked_pin(evidence/f'callback-{index}-policy.json')))
        require(all(checked_pin(images[role])==pin for role,pin in original_images.items()),'budget original image changed')
        result=dict(schema='cairn.r06-agy-trace-budget/1',**trace_budget([r['bytes'] for r in measurements]),
            control=control,control_bytes=Path(control['Path']).stat().st_size,measurements=measurements,
            native_executions=0,provider_executions=0,string_limit=OwnedKernelTrace.STRING_LIMIT)
        roundtrip.write(evidence/'trace-budget.json',result)
        return result
    finally:
        try:
            if fixture is not None:fixture.close()
        finally:
            if cluster is not None:
                stopped=cluster.stop()
                cleanup=dict(stopped=stopped,pids=pg_pids,
                    all_absent=all(p>1 and not Path(f'/proc/{p}').exists() for p in pg_pids),group_signals=False)
                roundtrip.write(evidence/'budget-pg-cleanup.json',cleanup)
                require(stopped and cleanup['all_absent'],'budget PG cleanup failed')
            shutil.rmtree(runtime)


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
        approved['preflight']['trace_budget']=measure_trace_budget(approved['images'],Path(selected['evidence'])/'trace-budget')
    except (ValueError,OSError,AssertionError,RuntimeError,KeyError,TypeError,subprocess.SubprocessError) as error:
        raise PrerequisiteUnavailable('INPUT_NOT_READY: '+str(error)) from error


def run_session_sequence(approved,fixture,session,client,provider,evidence):
    """One maintained A/B/public/reverse/restored-A sequence for both clients."""
    root=fixture.root
    source_pins=approved['preflight']['source_pins']
    before_capture=session.child.generation
    prompts=['R06-owned-A-'+approved['case_id'],'R06-owned-B-'+approved['case_id'],
             'R06-owned-restored-A-'+approved['case_id']]
    results=[]
    # No script-generated callback/state/session is permitted here: each
    # prompt goes through the actual stream-json client and local provider.
    start=0;results.append(session.prompt(prompts[0]))
    before,history=collect_native_phase(session,fixture,'A',start,root/'phase-A.trace')
    audit_a=audit_host_connections(Path(before['Trace']['Path']).read_bytes(),before_capture,root,
        ports={provider.server.server_port},unix_paths=[fixture.bridge.path],final=False,enforcement=session.child)
    file_a=roundtrip.write(evidence/'client-directory-A.json',audit_client_directory(
        Path(before['Trace']['Path']).read_bytes(),before_capture,root,fixture.work,enforcement=session.child))
    fixture.stage_live_b()
    require(session.child.capture_generation()==before_capture,'staging B changed native generation')
    fixture.publish('B')
    boundary_b=complete_trace_boundary(session)
    start=len(boundary_b)
    results.append(session.prompt(prompts[1]))
    after,history_b=collect_native_phase(session,fixture,'B',start,root/'phase-B.trace')
    require(Path(after['Trace']['Path']).read_bytes().startswith(boundary_b),'B boundary raw prefix changed')
    after_capture=session.child.capture_generation()
    require(before_capture==after_capture and history_b==history,'same native generation/history not preserved')
    audit_b=audit_host_connections(Path(after['Trace']['Path']).read_bytes(),after_capture,root,
        ports={provider.server.server_port},unix_paths=[fixture.bridge.path],final=False,enforcement=session.child)
    file_b=roundtrip.write(evidence/'client-directory-B.json',audit_client_directory(
        Path(after['Trace']['Path']).read_bytes(),after_capture,root,fixture.work,enforcement=session.child))
    states,readback,generation=capture_native_catalogs(fixture,session,before_capture,before,after_capture,after,client)
    forward=unit_control_plan(fixture);forward['census']['Proposal']['Sessions']=[p['Path'] for p in states]
    public_forward=live_unit_control(approved,fixture,session,client,forward,evidence,'native-A-to-B')
    reverse=unit_control_plan(fixture,reverse=True);reverse['census']['Proposal']['Sessions']=[p['Path'] for p in states]
    public_reverse=live_unit_control(approved,fixture,session,client,reverse,evidence,
        'native-B-to-retained-Go3-A',reverse=True)
    locks=[Path(fixture.unit['SelectionA']['Directory'])/'.watch.lock',
        Path(fixture.unit['ConfigRoot'])/'retained-A/.watch.lock',Path(fixture.unit['ConfigRoot'])/'retained-B/.watch.lock']
    require(all(os.path.samefile(locks[0],path) for path in locks),'watch lock identity split')
    fixture.publish('A')
    boundary_a=complete_trace_boundary(session)
    start=len(boundary_a)
    results.append(session.prompt(prompts[2]))
    restored,history_a=collect_native_phase(session,fixture,'A',start,root/'phase-restored-A.trace')
    require(Path(restored['Trace']['Path']).read_bytes().startswith(boundary_a),'restored A boundary raw prefix changed')
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
        unix_paths=[fixture.bridge.path],final=True,enforcement=session.child)
    file_final=roundtrip.write(evidence/'client-directory-final.json',audit_client_directory(
        full,before_capture,root,fixture.work,final=True,enforcement=session.child))
    if client.get('kind')=='SCRIPTED_NONNATIVE':
        client['runtime_final']=roundtrip.write(root/'scripted-runtime-final.json',
            dict(kind='SCRIPTED_NONNATIVE',images=session.child._scripted_runtime_pins,
                 scope='Executable file mappings observed at live boundaries; pinned files rechecked after join'))
    require(all(checked_pin(ROOT/name)==pin for name,pin in source_pins.items()),'maintained source changed during run')
    roundtrip.write(evidence/'provider-requests.json',provider.requests)
    roundtrip.write(evidence/'native-cleanup.json',session.child.cleanup)
    return dict(native_session=session.identity,client=client,
        generation=generation,readback=readback,Before=before,After=after,RestoredA=restored,
        public_forward=public_forward,public_reverse=public_reverse,results=results,
        audit=dict(A=audit_a,B=audit_b,full=final_audit),client_directory_audit=dict(A=file_a,B=file_b,full=file_final),
        scope='one owned Agy Go3 A/B/restored-A interval; not global refresh or legacy Python2 reverse')


def execute_native(approved):
    """One future admitted A/B/restored-A run; authoring never calls this."""
    require(approved['admission']['client']==dict(Path=str(AGY_SOURCE),SHA256=AGY_SHA256),
            'actual native execution requires the original held client')
    return execute_owned_case(approved,rehearsal=False)


def execute_rehearsal(approved):
    require('rehearsal' in approved and 'admission' not in approved,'separate nonnative rehearsal selection required')
    return execute_owned_case(approved,rehearsal=True)


def execute_owned_case(approved,*,rehearsal):
    selected=approved['rehearsal' if rehearsal else 'admission']
    if not rehearsal:
        require(selected['client']==dict(Path=str(AGY_SOURCE),SHA256=AGY_SHA256),
                'actual native execution requires the original held client')
    evidence=Path(selected['evidence']);root=Path(selected['catalog_root'])
    runtime=Path(tempfile.mkdtemp(prefix='r06r-' if rehearsal else 'r06n-',dir=Path.home()/'.cache'))
    cluster=fixture=provider=session=None
    result=dict(state='failed',attempt_id=selected['attempt_id'],NativePASS=False,
                trace_budget=approved['preflight']['trace_budget'])
    if rehearsal:result.update(kind='PREPARATION_REHEARSAL',RehearsalPASS=False,actual_Agy=0,actual_native_attempts=0)
    pg=Path(approved['preflight']['pg']);pg_pids=[]
    try:
        roundtrip.write(evidence/'application-start.json',dict(attempt_id=selected['attempt_id'],runtime=str(runtime),
            admission=approved.get('pin'),scope='NONNATIVE scripted rehearsal' if rehearsal else 'one actual disposable Agy; no automatic retry'))
        private_directory(root);require(not list(root.iterdir()),'catalog root became nonempty');root.rmdir()
        cluster=OwnedCluster.start(runtime/'pg',pg_bin=pg)
        pg_pids=owned_pg_processes(runtime/'pg/data/postmaster.pid')
        fixture=NativeUnitFixture(root,approved['images'],cluster,evidence,
            scale=rehearsal and approved.get('rehearsal_shape')==SCALE_SHAPE)
        client=stage_scripted_client(approved) if rehearsal else stage_native_client(approved)
        fixture.publish('A')
        provider=r04.GeminiFixture()
        session=(open_scripted_session if rehearsal else open_native_session)(approved,fixture,client,provider.url)
        approved.pop('_opening_scripted_session',None)
        approved['case_id']=selected['attempt_id']
        facts=run_session_sequence(approved,fixture,session,client,provider,evidence)
        result.update(state='passed',NativePASS=not rehearsal,**facts)
        if fixture.scale:result.update(fixture_shape=SCALE_SHAPE,bound_count=8,unbound_count=3,
            scale_inputs=json.loads((evidence/'scale-producer-inputs.json').read_bytes()))
        if rehearsal:
            result.update(RehearsalPASS=True,scope='NONNATIVE complete maintained sequence with scripted client; never native qualification')
    finally:
        errors=[];failure=sys.exc_info()[1];active_error=failure is not None
        if session is None:session=approved.pop('_opening_scripted_session',None)
        if active_error:result.update(error_type=type(failure).__name__,error=str(failure))
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
            approved['_archive_fixture']=fixture
        try:
            require(all(tool_pin((pg/name).resolve())==pin for name,pin in approved['preflight']['pg_pins'].items()),'PG tool changed')
            assert_images_unchanged(approved['images'],approved['image_pins'])
            candidate=json.loads(Path(approved['packet']['candidate.json']['Path']).read_bytes())
            for role in ROLES:
                validate_build_recipe(role,candidate['roles'][role],approved['image_pins']['images'][role],approved['image_pins'])
            require(all(checked_pin(pin['Path'])==pin for pin in approved['packet'].values()),'image packet changed')
            verify_tool_provenance(Path(approved['packet']['manifest']['Path']).parent)
            for pin in selected['tools'].values():tool_pin(pin['Path'],pin['SHA256'])
            require(all(checked_pin(ROOT/name)==pin for name,pin in approved['preflight']['source_pins'].items()),
                    'maintained source changed during case')
            if not rehearsal:
                require({key:selected[key] for key in ('source_head','source_tree')}==native_source_identity(),
                        'driver source changed during native case')
                require(checked_pin(AGY_SOURCE,AGY_SHA256) and stat.S_IMODE(AGY_SOURCE.stat().st_mode)==0o500,'held client changed')
            elif session is not None:
                require(all(checked_pin(client[key]['Path'])==client[key] for key in ('pin','script','plan','build','runtime_initial')),
                        'scripted rehearsal client inputs changed')
            require(approved['preflight']['parent']==dict(HOME=os.environ['HOME'],CODEX_HOME=os.environ.get('CODEX_HOME')),
                    'parent HOME/CODEX_HOME changed')
        except BaseException as error:errors.append('final-pins:'+type(error).__name__)
        if not errors:
            shutil.rmtree(runtime)
        if errors or active_error:
            result.update(state='failed',NativePASS=False)
            if rehearsal:result['RehearsalPASS']=False
        result.update(cleanup_errors=errors,PG_runtime_removed=not runtime.exists(),archive_state='pending-after-terminal')
        roundtrip.write(evidence/('rehearsal-result.json' if rehearsal else 'native-result.json'),result)
        require(not errors,'owned cleanup unresolved: '+','.join(errors))
    return result


def archive_native_after_terminal(approved):
    if approved.get('_archive_fixture') is None:return
    return archive_case_after_terminal(approved,approved['admission'])


def archive_case_after_terminal(approved,selected):
    """Custody after the already-published outcome, shared without native admission."""
    fixture=approved.get('_archive_fixture')
    if fixture is None:return
    evidence=Path(selected['evidence']);root=fixture.root
    require(Path(selected['ledger']).with_suffix('.terminal.json').exists(),'archive requires terminal native outcome')
    active_error=sys.exc_info()[0] is not None
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
        roundtrip.write(evidence/'archive-result.json',dict(state='completed',archive=checked_pin(archive)))
    except BaseException as error:
        roundtrip.write(evidence/'archive-result.json',dict(state='failed',error_type=type(error).__name__,error=str(error)))
        if not active_error:raise


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


def checked_packet(manifest, manifest_sha256, index, index_sha256, complete, complete_sha256, *, native=False, measure_only=False):
    """Pins select BUILD-ONLY bytes; they never admit a native client."""
    require(all((manifest, manifest_sha256, index, index_sha256, complete, complete_sha256)),
            'INPUT_NOT_READY: complete sealed image packet and exact pins required')
    selections = {name: checked_pin(path, expected) for name, path, expected in (
        ('manifest', manifest, manifest_sha256), ('index', index, index_sha256),
        ('build_complete', complete, complete_sha256))}
    root = Path(manifest).parent
    private_directory(root)
    pins = json.loads(Path(manifest).read_bytes(),object_pairs_hook=admission_pairs)
    source_head,source_tree = (pins.get('source_head'),pins.get('source_tree')) if native or measure_only else (BASE,BASE_TREE)
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
    if measure_only:return images,pins,selections
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


class RehearsalReservation:
    """Local preparation marker, never a native schema, selector or budget."""
    finish=NativeReservation.finish

    def __init__(self,approved):
        self.path=Path(approved['rehearsal']['ledger'])
        ledger=reserve_ledger(self.path,approved['rehearsal_pins'],kind='PREPARATION_REHEARSAL')
        ledger.close()
        self.binding=json.loads(self.path.read_bytes())
        self.binding.update(actual_Agy=0,actual_native_attempts=0,NativePASS=False)


def prepare_sequence_rehearsal(evidence,*,python_sha256,strace_sha256,manifest,
        manifest_sha256,index,index_sha256,complete,complete_sha256,fault=None,fault_phase='forward',shape=None):
    """Full maintained sequence using the scripted protocol, never held Agy."""
    evidence=private_directory(evidence)
    require(shape in (None,SCALE_SHAPE),'unknown nonnative fixture shape')
    require(fault in (None,'client-rename','client-sibling','account','workspace','state','cairn','database'),
            'unknown nonnative rehearsal fault')
    require(fault_phase in ('forward','reverse') and
        (fault_phase=='forward' or fault not in (None,'client-rename','client-sibling')),
        'invalid nonnative rehearsal fault phase')
    require(not list(evidence.iterdir()),'fresh empty rehearsal evidence required')
    tools=dict(python3=tool_pin(Path(sys.executable).resolve(),python_sha256),
        strace=tool_pin(Path(shutil.which('strace') or '/absent').resolve(),strace_sha256))
    images,pins,packet=checked_packet(manifest,manifest_sha256,index,index_sha256,
        complete,complete_sha256,native=True)
    pg=pg_bindir()
    pg_pins={name:tool_pin((pg/name).resolve()) for name in ('initdb','pg_ctl','postgres','createdb','psql')}
    source_pins={name:checked_pin(ROOT/name) for name in (*REUSED,
        'scripts/check_r06_agy_native_unit.py','scripts/test_r06_agy_native_unit_driver.py',
        'docs/verification/r06-agy-native-unit.md')}
    root=evidence/'catalog';root.mkdir(mode=0o700)
    require(len(os.fsencode(root/'api-bridge.sock'))<108,'rehearsal root too long for Unix transport')
    outputs=evidence/'case';outputs.mkdir(mode=0o700)
    selection=dict(catalog_root=str(root),evidence=str(outputs),ledger=str(evidence/'rehearsal-ledger.json'),
        attempt_id='NONNATIVE-scripted-rehearsal',tools=tools)
    selected_pins=dict(kind='PREPARATION_REHEARSAL',NativePASS=False,actual_Agy=0,
        actual_native_attempts=0,images=packet,image_source_head=pins['source_head'],
        driver_sources=source_pins,tools=tools,fault=dict(kind=fault,phase=fault_phase))
    roundtrip.write(evidence/'rehearsal-selection.json',selected_pins)
    approved=dict(rehearsal=selection,rehearsal_pins=selected_pins,rehearsal_fault=fault,
        rehearsal_fault_phase=fault_phase,rehearsal_shape=shape,images=images,image_pins=pins,
        packet=packet,tools=tools,preflight=dict(pg=str(pg),pg_pins=pg_pins,source_pins=source_pins,
            parent=dict(HOME=os.environ['HOME'],CODEX_HOME=os.environ.get('CODEX_HOME'))))
    # Same real-new-control pressure guard, with separately reported API/PG effects.
    approved['preflight']['trace_budget']=measure_trace_budget(images,evidence/'trace-budget')
    reservation=RehearsalReservation(approved)
    try:
        result=run_reserved_case(approved,execute_rehearsal,reservation,str(outputs),'rehearsal-result.json')
        require(result.get('NativePASS') is False and result.get('actual_Agy')==0
                and result.get('actual_native_attempts')==0 and result.get('RehearsalPASS') is True,
                'rehearsal cannot establish native PASS')
        return result
    finally:
        archive_case_after_terminal(approved,selection)


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
    if selected_argv and selected_argv[0]=='--confined-exec':
        return confined_exec(selected_argv[1:])
    if selected_argv and selected_argv[0]=='--api-bridge-client':
        require(len(selected_argv)>=2,'API bridge socket required')
        return api_bridge_client(Path(selected_argv[1]),selected_argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare-stubbed', action='store_true')
    mode.add_argument('--prepare-authenticated', action='store_true')
    mode.add_argument('--prepare-target-observation', action='store_true')
    mode.add_argument('--measure-trace-budget', action='store_true', help='genuine Go3 callbacks only; no native/provider')
    mode.add_argument('--native-unit', action='store_true', help='one explicitly pinned future disposable Agy case')
    parser.add_argument('--admission',type=Path)
    parser.add_argument('--admission-sha256')
    parser.add_argument('--attempt-id')
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--python-sha256')
    parser.add_argument('--strace-sha256')
    parser.add_argument('--rehearse-sequence',action='store_true',
        help='with --prepare-stubbed only: scripted complete sequence, never native PASS')
    parser.add_argument('--images', type=Path)
    parser.add_argument('--images-sha256')
    parser.add_argument('--packet-index', type=Path)
    parser.add_argument('--packet-index-sha256')
    parser.add_argument('--build-complete', type=Path)
    parser.add_argument('--build-complete-sha256')
    args = parser.parse_args(argv)
    try:
        require(not args.rehearse_sequence or (args.prepare_stubbed
            and not any((args.admission,args.admission_sha256,args.attempt_id))),
            'sequence rehearsal is stub-only and cannot use native admission')
        if args.measure_trace_budget:
            require(args.evidence is not None,'explicit private budget evidence required')
            images,pins,packet=checked_packet(args.images,args.images_sha256,args.packet_index,args.packet_index_sha256,
                                      args.build_complete,args.build_complete_sha256,measure_only=True)
            print(json.dumps(measure_trace_budget(images,args.evidence),sort_keys=True))
            return 0
        if args.native_unit:
            try:
                approved=checked_native_admission(args.admission,args.admission_sha256,args.attempt_id)
            except (ValueError,OSError,AssertionError,KeyError,TypeError,RuntimeError,subprocess.SubprocessError) as error:
                raise PrerequisiteUnavailable('INPUT_NOT_READY: '+str(error)) from error
            try:result=run_native_attempt(approved,native_preflight,execute_native)
            finally:archive_native_after_terminal(approved)
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
        if args.rehearse_sequence:
            result=prepare_sequence_rehearsal(args.evidence,python_sha256=args.python_sha256,
                strace_sha256=args.strace_sha256,manifest=args.images,manifest_sha256=args.images_sha256,
                index=args.packet_index,index_sha256=args.packet_index_sha256,
                complete=args.build_complete,complete_sha256=args.build_complete_sha256)
        else:
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
