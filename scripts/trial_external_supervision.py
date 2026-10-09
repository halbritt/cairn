"""Provider-free supervision prototype; its closed API cannot launch a native route.

The controller retains kernel cgroup FDs, subprocess handles and bounded pipes.
Blocking capture/parser/retention/diagnostic work runs in disposable sandboxes.
No marker or caller-provided exit declaration substitutes for these handles.
"""
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import struct
import subprocess
import time
import uuid

from trial_task_eval import runtime_sandbox
from trial_process_custody import validate_custody


SCRIPTS = Path(__file__).resolve().parent
FIXTURE = SCRIPTS/'supervision_fixture'
RETENTION_SOURCE = Path('/home/halbritt/git/cairn/.local/agent267-recovery-20261004/distinct-query-contract-20261009/native-resume-pilot/selected-evidence-prototype/selected-retention-v2')
SOURCE_PINS = dict(
    frames='fd79aa5aa4b8fdffd09daf30619706f923e39e89462528a4e1228315e8ee44e6',
    evidence='f96e1596c3bf7a200a57cdfae7b44db04b0d0b394d7d130473c921b5e815136b',
    retention='b3b99c7f0d6a94c969e77d5179ca4b9b5f75c8b42c9cdec0b9afb05f06832f36')
ACTOR_MODES = frozenset({'normal', 'privacy', 'never_read', 'early_close', 'nonzero', 'descendant', 'memory', 'pids', 'capture_report_loss'})
SINK_MODES = frozenset({'normal', 'parse_hang', 'fsync_hang', 'after_prepared'})
CONTROLS = dict(memory_max=512*1024*1024, memory_swap_max=0, pids_max=32, cpu_max='100000 100000')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


@dataclasses.dataclass(frozen=True)
class FixtureRequest:
    run_id: str
    occurrence_id: str
    request_sha256: str
    task_sha256: str
    oracle_sha256: str
    config_sha256: str
    actor: str = 'normal'
    sink: str = 'normal'
    diagnostic: str = 'normal'
    wall_seconds: float = 4.0

    def validate(self):
        for value in [self.run_id, self.occurrence_id]:
            if type(value) is not str or str(uuid.UUID(value)) != value:
                raise ValueError('fixture_identity')
        for value in [self.request_sha256, self.task_sha256, self.oracle_sha256, self.config_sha256]:
            if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('fixture_hash')
        if any(type(v) is not str for v in [self.actor,self.sink,self.diagnostic]) or self.actor not in ACTOR_MODES or self.sink not in SINK_MODES or self.diagnostic not in {'normal', 'persist_hang'}:
            raise ValueError('fixture_mode')
        if type(self.wall_seconds) not in {int, float} or not 2 <= self.wall_seconds <= 600:
            raise ValueError('fixture_budget')


@dataclasses.dataclass(frozen=True)
class WaitFact:
    pid: int
    start_ticks: int | None
    raw_status: int | None
    returncode: int | None


@dataclasses.dataclass
class AttemptResult:
    schema: str = 'cairn.fixture-supervised-attempt/1'
    status: str = 'unknown'
    failures: list = dataclasses.field(default_factory=list)
    bindings: dict = dataclasses.field(default_factory=dict)
    intent: dict | None = None
    actor_wait: dict | None = None
    outer_waits: dict = dataclasses.field(default_factory=dict)
    containment: dict = dataclasses.field(default_factory=lambda: dict(released=None, resources=None))
    prepared_receipt: dict | None = None
    persistence: str = 'not_attempted'
    elapsed_seconds: float = 0
    provider_executed: bool = False
    native_route_admitted: bool = False
    task_outcome: str = 'not_evaluated'
    runtime_evidence_authenticated: bool = False
    owned_fixture_observations: bool = False
    # Returned handle-derived fixture facts may be consumed only in this
    # process. Re-reading this JSON never authenticates a runtime observation.
    standalone_record_is_success: bool = False
    raw_stream_retained: bool = False
    private_stderr_retained: bool = False
    limits: list = dataclasses.field(default_factory=lambda: [
        'fixture_only_no_native_admission', 'request_hashes_caller_pinned_not_independent_admission',
        'resource_ceilings_not_capacity_reservation', 'source_mounts_not_sealed_against_host_uid',
        'supervisor_and_host_loss_recovery_unestablished', 'kernel_scheduler_not_realtime',
        'coding_hook_reader_composition_unaccepted', 'export_trusted_adapter_unaccepted',
        'directory_cleanup_not_attested'])

    def fail(self, code):
        if code not in self.failures:
            self.failures.append(code)


def refuse_native():
    return AttemptResult(status='refused_prelaunch', failures=[
        'native_route_unadmitted', 'native_capacity_reservation_unestablished',
        'coding_and_hook_reader_composition_unaccepted', 'external_observer_loss_recovery_unestablished'])


class Unavailable(Exception):
    pass


def fixture_service_delegation():
    """Enable controllers only in this newly owned fixture service's domain.

    systemd-run must use Delegate=yes and DelegateSubgroup=controller. An
    ordinary agent session is refused; writable siblings are not a fallback.
    """
    membership = Path('/proc/self/cgroup').read_text().strip()
    if not membership.startswith('0::'):
        raise Unavailable('service_delegation_unavailable')
    current = Path('/sys/fs/cgroup'+membership[3:])
    root = current.parent
    if current.name != 'controller' or not root.name.startswith('cairn-fixture-') or not root.name.endswith('.service'):
        raise Unavailable('owned_fixture_service_required')
    available = set((root/'cgroup.controllers').read_text().split())
    if not {'cpu', 'memory', 'pids'} <= available:
        raise Unavailable('resource_controllers_unavailable')
    (root/'cgroup.subtree_control').write_text('+cpu +memory +pids')
    return str(root)


class OwnedCgroup:
    """Only an exclusive new domain leaf; all effects use held directory/file FDs."""
    def __init__(self, delegated):
        self.name = 'cairn-fixture-'+str(uuid.uuid4())
        self.parent = os.open(delegated, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.fd = None
        self.files = {}
        self.consumed = False
        try:
            os.mkdir(self.name, 0o700, dir_fd=self.parent)
            self.consumed = True
            self.fd = os.open(self.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            self.identity = (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino)
            self.path = str(Path(delegated)/self.name)
            self.relative = self.path.removeprefix('/sys/fs/cgroup')
            if self.read('cgroup.type').strip() != 'domain':
                raise Unavailable('cgroup_domain_unavailable')
            for field, value in CONTROLS.items():
                name = field.replace('_', '.')
                self.write(name, str(value).encode())
                if self.read(name).strip() != str(value):
                    raise Unavailable('resource_readback_unknown')
            self.files['cgroup.procs'] = self.open('cgroup.procs', os.O_WRONLY)
            self.files['cgroup.kill'] = self.open('cgroup.kill', os.O_WRONLY)
            for name in ['cgroup.events', 'memory.events', 'pids.events']:
                self.files[name] = self.open(name, os.O_RDONLY)
            if self.populated():
                raise Unavailable('new_cgroup_populated')
        except (OSError, Unavailable):
            self.close()
            raise Unavailable('containment_or_resource_unavailable') from None

    def open(self, name, flags):
        if (os.fstat(self.fd).st_dev, os.fstat(self.fd).st_ino) != self.identity:
            raise Unavailable('cgroup_identity_unknown')
        return os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=self.fd)

    def read(self, name):
        fd = self.files.get(name)
        owned = fd is None
        if owned:
            fd = self.open(name, os.O_RDONLY)
        try:
            data = os.pread(fd, 8192, 0)
            if len(data) == 8192:
                raise Unavailable('cgroup_status_bounds')
            return data.decode('ascii')
        finally:
            if owned:
                os.close(fd)

    def write(self, name, data):
        fd = self.files.get(name)
        owned = fd is None
        if owned:
            fd = self.open(name, os.O_WRONLY)
        try:
            if os.write(fd, data) != len(data):
                raise Unavailable('cgroup_write_unknown')
        finally:
            if owned:
                os.close(fd)

    def populated(self):
        pairs = dict(line.split() for line in self.read('cgroup.events').splitlines())
        if pairs.get('populated') not in {'0', '1'}:
            raise Unavailable('cgroup_release_unknown')
        return pairs['populated'] == '1'

    def kill(self):
        self.write('cgroup.kill', b'1')

    def events(self):
        return {name:dict(line.split() for line in self.read(name).splitlines())
                for name in ['memory.events', 'pids.events']}

    def close(self):
        for fd in self.files.values():
            os.close(fd)
        self.files.clear()
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.consumed:
            try:
                os.rmdir(self.name, dir_fd=self.parent)
            except OSError:
                # An unreleased leaf is preserved; never remove existing work.
                pass
        os.close(self.parent)


class Child:
    def __init__(self, group, argv):
        status_read, status_write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
        command = ['/usr/bin/python3', str(FIXTURE/'launcher.py'), str(group.files['cgroup.procs']),
                   'bwrap', '--json-status-fd', str(status_write), *argv]
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, pass_fds=(group.files['cgroup.procs'], status_write),
                env={'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'}, close_fds=True)
        except OSError:
            os.close(status_read)
            raise
        finally:
            os.close(status_write)
        self.status_fd = status_read
        self.namespace_fds = []
        self.namespace_identity = None
        self.buffers = dict(stdout=bytearray(), stderr=bytearray(), status=bytearray())
        self.eof = set()
        self.verified = False
        self.wait = None
        try:
            fields = Path(f'/proc/{self.process.pid}/stat').read_text().rsplit(') ', 1)[1].split()
            self.start = int(fields[19])
        except (OSError, ValueError, IndexError):
            self.start = None
        for stream in [self.process.stdin, self.process.stdout, self.process.stderr]:
            os.set_blocking(stream.fileno(), False)

    def observe_wait(self):
        if self.wait is not None:
            return self.wait
        pid, raw = os.waitpid(self.process.pid, os.WNOHANG)
        if pid:
            code = os.waitstatus_to_exitcode(raw)
            self.process.returncode = code
            self.wait = WaitFact(pid, self.start, raw, code)
        return self.wait

    def verify_namespace(self, group):
        if self.verified:
            return True
        if b'\n' not in self.buffers['status']:
            return False
        value = json.loads(self.buffers['status'].split(b'\n')[0])
        pid = value['child-pid']
        if type(pid) is not int or pid <= 0:
            raise Unavailable('namespace_status_unknown')
        # Reported IDs are checked against a living member of our cgroup,
        # then anchored with namespace FDs; they are never signal authority.
        before = Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
        if Path(f'/proc/{pid}/cgroup').read_text().strip() != '0::'+group.relative:
            raise Unavailable('namespace_membership_unknown')
        fds = []
        identities = dict(host_init_pid=pid, host_init_start_ticks=int(before))
        try:
            for name in ['pid', 'mnt', 'net', 'user']:
                fd = os.open(f'/proc/{pid}/ns/{name}', os.O_RDONLY | os.O_CLOEXEC)
                fds.append(fd)
                actual = os.fstat(fd).st_ino
                identities[name] = actual
                if actual == os.stat(f'/proc/self/ns/{name}').st_ino:
                    raise Unavailable('namespace_isolation_unknown')
                if name != 'user' and actual != value[name+'-namespace']:
                    raise Unavailable('namespace_status_mismatch')
            after = Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19]
            if before != after or Path(f'/proc/{pid}/cgroup').read_text().strip() != '0::'+group.relative:
                raise Unavailable('namespace_identity_unknown')
            self.namespace_fds = fds
            fds = []
            self.verified = True
            self.namespace_identity = identities
            return True
        finally:
            for fd in fds:
                os.close(fd)

    def close(self):
        for stream in [self.process.stdin, self.process.stdout, self.process.stderr]:
            stream.close()
        for fd in [self.status_fd, *self.namespace_fds]:
            os.close(fd)


def sandbox(sources, bindings, args):
    # Existing runtime mount helper explicitly shares host networking. This
    # fixture adds fresh network/user namespaces and disables nested userns.
    command = runtime_sandbox(common_runtime=None)
    command += ['--unshare-net', '--unshare-user', '--disable-userns', '--dir', '/sources',
                '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
                '--setenv', 'PYTHONPATH', '/sources', '--setenv', 'LANG', 'C.UTF-8']
    for source, target in sources:
        command += ['--ro-bind', str(source), '/sources/'+target]
    for source, target in bindings:
        command += ['--bind', str(source), target]
    return command[1:]+['--chdir', '/tmp', '/usr/bin/python3', '/sources/worker.py', *args]


def source_manifest(source_root):
    result = {}
    for name, expected in SOURCE_PINS.items():
        path = source_root/(name+'.py')
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise Unavailable('source_pin_unknown')
            data = source.read(512*1024+1)
        if digest(data) != expected:
            raise Unavailable('source_pin_mismatch')
        result[name+'.py'] = expected
    for path in [FIXTURE/'launcher.py', FIXTURE/'worker.py', FIXTURE/'actor.py',
                 FIXTURE/'service_main.py', SCRIPTS/'trial_native_capture.py',
                 SCRIPTS/'trial_process_custody.py', SCRIPTS/'trial_fixture_service.py',
                 SCRIPTS/'trial_task_eval.py', Path(__file__).resolve()]:
        result[path.name] = digest(path.read_bytes())
    return result


def read_events(children, timeout, actor_raw=None):
    with selectors.DefaultSelector() as selector:
        for role, child in children.items():
            for name, stream in [('stdout',child.process.stdout), ('stderr',child.process.stderr), ('status',child.status_fd)]:
                if name not in child.eof:
                    fd = stream if type(stream) is int else stream.fileno()
                    selector.register(fd, selectors.EVENT_READ, (role, name))
        for key, _ in selector.select(timeout):
            role, name = key.data
            block = os.read(key.fd, 65536)
            child = children[role]
            if not block:
                child.eof.add(name)
            elif role == 'actor' and name == 'stdout':
                actor_raw(block)
            else:
                buffer = child.buffers[name]
                buffer.extend(block)
                if len(buffer) > (128*1024 if role == 'actor' and name == 'stderr' else 8192):
                    raise Unavailable('control_output_bounds')


def control_lines(child):
    data = child.buffers['stdout']
    return [json.loads(line) for line in data.split(b'\n')[:-1]]


def end_containment(group, children, deadline):
    errors = []
    try:
        group.kill()
    except (OSError, Unavailable):
        errors.append('containment_kill_unknown')
    released = None
    while time.monotonic() < deadline:
        try:
            for child in children.values():
                child.observe_wait()
            if not group.populated():
                released = True
                if all(child.wait is not None for child in children.values()):
                    break
        except (OSError, Unavailable):
            errors.append('containment_release_unknown')
            break
        time.sleep(.005)
    if released is not True:
        errors.append('containment_release_unknown')
    for child in children.values():
        try:
            child.observe_wait()
        except OSError:
            errors.append('outer_wait_unknown')
        if child.wait is None:
            errors.append('outer_wait_unknown')
    return released, errors


def run_fixture(request, retained_root, workspace_root, *, delegated=None, source_root=RETENTION_SOURCE, controller_loss_stage=None):
    """Only fixed fixtures; request/workspace hashes are fixture bindings, not live admission.

    Controller death/power loss leaves standalone UNKNOWN. This prototype does
    not authorize a retry or give a replacement observer reconstructed custody.
    """
    result = AttemptResult()
    if controller_loss_stage not in {None, 'intent', 'capture', 'parser', 'sink'}:
        result.status = 'refused_prelaunch'
        result.fail('fixture_loss_stage_required')
        return result
    if type(request) is not FixtureRequest:
        result.status = 'refused_prelaunch'
        result.fail('fixture_request_required')
        return result
    try:
        request.validate()
        manifest = source_manifest(Path(source_root))
        for root in [Path(retained_root), Path(workspace_root)]:
            meta = root.lstat()
            if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != os.getuid() or stat.S_IMODE(meta.st_mode) != 0o700:
                raise Unavailable('fixture_root_custody')
        # Workspace creation is runner-owned, before any actor sees it. No
        # evaluator paths, repo indirection, memory or coordination notes enter.
        workspace = Path(workspace_root)/request.run_id
        workspace.mkdir(mode=0o700)
        workspace_hash = digest(encoded(dict(kind='fresh-empty-fixture-workspace', run_id=request.run_id)))
        delegated = delegated or fixture_service_delegation()
        group = OwnedCgroup(delegated)
    except (OSError, ValueError, Unavailable):
        result.status = 'refused_prelaunch'
        result.fail('source_workspace_or_containment_unavailable')
        return result
    result.bindings = dict(run_id=request.run_id, occurrence_id=request.occurrence_id,
        request_sha256=request.request_sha256, task_sha256=request.task_sha256,
        oracle_sha256=request.oracle_sha256, config_sha256=request.config_sha256,
        source_manifest_sha256=digest(encoded(manifest)), workspace_sha256=workspace_hash,
        fixture_only=True)
    result.containment = dict(released=None, identity=list(group.identity), resources=CONTROLS.copy(),
        resource_events=None, scope='exclusive_owned_kernel_cgroup', capacity_reservation=False)
    started = time.monotonic()
    final_deadline = started+request.wall_seconds
    work_deadline = final_deadline-1.0  # cleanup and diagnostic share the original budget
    children = {}
    ready = None
    receipt = None
    actor_fact = None
    input_queue = bytearray()
    stream_bytes = 0
    end_sent = False
    try:
        base = [(FIXTURE/'worker.py','worker.py')]
        sink_sources = base+[(Path(source_root)/(name+'.py'), name+'.py') for name in SOURCE_PINS]
        children['sink'] = Child(group, sandbox(sink_sources, [(retained_root,'/retention')],
            ['retention', request.sink, request.run_id, request.task_sha256, request.oracle_sha256,
             request.config_sha256, result.bindings['source_manifest_sha256'],
             Path('/proc/sys/kernel/random/boot_id').read_text().strip()]))

        def raw(block):
            nonlocal stream_bytes
            stream_bytes += len(block)
            if stream_bytes > 4*1024*1024 or len(input_queue)+len(block)+5 > 128*1024:
                raise Unavailable('stream_transport_bounds')
            input_queue.extend(b'D'+struct.pack('!I',len(block))+block)

        while time.monotonic() < work_deadline:
            read_events(children, .01, raw)
            sink = children['sink']
            messages = control_lines(sink)
            if any(m == dict(kind='STAGE', stage=controller_loss_stage) for m in messages):
                os.kill(os.getpid(), signal.SIGKILL)
            if ready is None and messages:
                if messages[0].get('kind') == 'FAILURE':
                    result.fail('target_consumed' if messages[0].get('code') == 'target_consumed' else 'intent_failed')
                    break
                candidate = messages[0]
                expected_intent = dict(schema='cairn.selected-retention-intent/1', run_id=request.run_id,
                    task_sha256=request.task_sha256, oracle_sha256=request.oracle_sha256,
                    config_sha256=request.config_sha256, source_manifest_sha256=result.bindings['source_manifest_sha256'],
                    admission_basis='caller_pinned_not_verified', launch_performed_by_sink=False)
                if candidate != dict(kind='READY', run_id=request.run_id, intent_sha256=digest(encoded(expected_intent))):
                    raise Unavailable('intent_receipt_unknown')
                if not sink.verify_namespace(group):
                    continue
                ready = candidate
                result.intent = dict(sha256=candidate['intent_sha256'], acknowledged_before_actor=True,
                                     exclusive_target=True, native_admission=False)
                if controller_loss_stage == 'intent':
                    os.kill(os.getpid(), signal.SIGKILL)
                actor_sources = base+[(FIXTURE/'actor.py','actor.py'), (SCRIPTS/'trial_native_capture.py','trial_native_capture.py')]
                children['actor'] = Child(group, sandbox(actor_sources, [(workspace,'/workspace')],
                    ['capture',request.actor,request.run_id,str(max(.05,work_deadline-time.monotonic()-.1))]))
            actor = children.get('actor')
            if actor is not None and not actor.verified:
                if actor.verify_namespace(group):
                    os.write(actor.process.stdin.fileno(), b'G')
                    actor.process.stdin.close()
                    if controller_loss_stage == 'capture':
                        os.kill(os.getpid(), signal.SIGKILL)
            if actor is not None and 'stderr' in actor.eof and actor_fact is None:
                # This channel belongs to the pinned helper; actor stderr is
                # separately captured and excluded. Actual waits are checked.
                fact = json.loads(actor.buffers['stderr'])
                if fact.get('kind') != 'ACTOR_WAIT' or type(fact.get('pid')) is not int or type(fact.get('start_ticks')) is not int:
                    raise Unavailable('actor_wait_unknown')
                custody = validate_custody(fact['custody'])
                actor_fact = fact
                result.actor_wait = dict(pid=fact['pid'], start_ticks=fact['start_ticks'],
                    raw_status=custody['observed_wait_status'], returncode=custody['observed_returncode'],
                    basis='pinned_capture_worker_owned_child_waitpid', input_bytes=custody['input_bytes'],
                    input_bytes_written=custody['input_bytes_written'], input_delivery_complete=custody['input_delivery_complete'])
                if custody['observed_returncode'] is None:
                    result.fail('actor_wait_unknown')
                elif custody['observed_returncode'] != 0:
                    result.fail('actor_nonzero')
                if not custody['input_delivery_complete']:
                    result.fail('input_delivery_incomplete')
                if custody['classification'] != 'completed':
                    result.fail('capture_not_completed')
            if actor is not None and actor_fact is not None and 'stdout' in actor.eof and not end_sent:
                if result.failures:
                    break
                exit_fact = dict(pid=actor_fact['pid'], start_ticks=actor_fact['start_ticks'],
                    exit_code=result.actor_wait['returncode'], signal_observed=result.actor_wait['returncode'] < 0)
                data = encoded(exit_fact)
                input_queue.extend(b'E'+struct.pack('!I',len(data))+data)
                end_sent = True
            if input_queue:
                try:
                    count = os.write(sink.process.stdin.fileno(), memoryview(input_queue)[:65536])
                    del input_queue[:count]
                except BlockingIOError:
                    pass
                except BrokenPipeError:
                    result.fail('sink_transport_closed')
                    break
            for child in children.values():
                child.observe_wait()
            if sink.wait is not None and 'stdout' in sink.eof:
                messages = control_lines(sink)
                receipts = [m for m in messages if m.get('kind') == 'RECEIPT']
                if sink.wait.returncode == 0 and len(receipts) == 1 and end_sent and not input_queue:
                    receipt = receipts[0]
                    if set(receipt) != {'kind','run_id','selected_sha256','selected_bytes','intent_sha256','return_receipt','exit_basis'} or receipt.get('run_id') != request.run_id or receipt.get('intent_sha256') != ready['intent_sha256'] or receipt.get('return_receipt') is not False or receipt.get('exit_basis') != 'caller_supplied_not_independently_attested' or type(receipt.get('selected_bytes')) is not int or not 0 < receipt['selected_bytes'] <= 64*1024*1024 or type(receipt.get('selected_sha256')) is not str or len(receipt['selected_sha256']) != 64 or any(c not in '0123456789abcdef' for c in receipt['selected_sha256']):
                        raise Unavailable('selected_receipt_unknown')
                    result.prepared_receipt = receipt
                else:
                    result.fail('sink_return_unconfirmed')
                break
        else:
            result.fail('wall_deadline')
    except (OSError, ValueError, TypeError, KeyError, IndexError, Unavailable, RecursionError):
        result.fail('supervisor_observation_unknown')
    except (KeyboardInterrupt, SystemExit):
        result.fail('supervisor_interrupted')
    finally:
        released, errors = end_containment(group, children, min(final_deadline-.45, time.monotonic()+.5))
        result.containment['released'] = released
        for error in errors:
            result.fail(error)
        try:
            result.containment['resource_events'] = group.events()
        except (OSError, Unavailable):
            result.fail('resource_events_unknown')
        for role, child in children.items():
            result.outer_waits[role] = dataclasses.asdict(child.wait) if child.wait else None
            if result.outer_waits[role] is not None:
                result.outer_waits[role]['namespace_identity'] = child.namespace_identity
            if child.wait is not None and child.wait.returncode != 0:
                result.fail(role+'_outer_nonzero')
            child.close()
        group.close()
    if ready is None and not children.get('actor'):
        result.status = 'refused_prelaunch'
    elif result.containment['released'] is not True or any(v is None for v in result.outer_waits.values()):
        result.status = 'unknown'
    elif result.failures:
        result.status = 'failed'
    elif receipt is not None and result.actor_wait is not None:
        result.status = 'fixture_complete'
        result.owned_fixture_observations = True
    else:
        result.status = 'unknown'
        result.fail('terminal_incomplete')
    # Persistence is another owned bounded worker. Its failure cannot replace
    # the primary result, and its file never certifies its own actual return.
    if ready is not None and final_deadline-time.monotonic() > .05:
        diagnostic_group = None
        diagnostic_child = None
        try:
            diagnostic_group = OwnedCgroup(delegated)
            diagnostic_child = Child(diagnostic_group, sandbox([(FIXTURE/'worker.py','worker.py')],
                [(Path(retained_root)/request.run_id,'/retained')], ['diagnostic',request.diagnostic]))
            data = encoded(dataclasses.asdict(result))
            pending = bytearray(data)
            while time.monotonic() < final_deadline-.1:
                read_events({'diagnostic':diagnostic_child}, .005)
                if pending:
                    try:
                        count = os.write(diagnostic_child.process.stdin.fileno(), pending)
                        del pending[:count]
                    except BlockingIOError:
                        pass
                elif not diagnostic_child.process.stdin.closed:
                    diagnostic_child.process.stdin.close()
                diagnostic_child.observe_wait()
                if diagnostic_child.wait is not None:
                    break
            result.persistence = 'returned' if diagnostic_child.wait is not None and diagnostic_child.wait.returncode == 0 and not pending else 'unknown'
        except (OSError, Unavailable, ValueError, TypeError, KeyError, BrokenPipeError):
            result.persistence = 'unknown'
        finally:
            if diagnostic_group is not None:
                released, errors = end_containment(diagnostic_group,
                    {'diagnostic':diagnostic_child} if diagnostic_child else {}, final_deadline)
                if released is not True or errors:
                    result.persistence = 'unknown'
                if diagnostic_child is not None:
                    diagnostic_child.close()
                diagnostic_group.close()
    elif ready is not None:
        result.persistence = 'unknown'
    result.elapsed_seconds = time.monotonic()-started
    return result
