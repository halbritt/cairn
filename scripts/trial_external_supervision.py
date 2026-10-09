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
from trial_selected_native_mounts import reviewed_package
from trial_owned_workers import WaitFact, OwnedCgroup, Child, Unavailable, CONTROLS, end_containment


SCRIPTS = Path(__file__).resolve().parent
FIXTURE = SCRIPTS/'supervision_fixture'
SELECTED_PACKAGE = SCRIPTS/'selected_native'
# Reviewed repository port 0e09fc6; only these default sink dependencies mount.
SOURCE_PINS = {
    '__init__.py':'d48ff6c00bcad5cfad5a597c723b0ee082cea567ecadeace18fc7c372c8a318a',
    'evidence.py':'7f96bc5ee8418f6c93cb34de1175ec1c32c56e8ca8fdf8fa7185593eb2a77832',
    'frames.py':'68973016ba4352c7114a25c69256d4d277c6d0ce5b084a66383c8823d1973abd',
    'retention.py':'38bdfc87770c56ede47a05eb6d9e23d8620f4dec24a22d95592cc9638977ffd2'}
ACTOR_MODES = frozenset({'normal', 'privacy', 'never_read', 'early_close', 'nonzero', 'descendant', 'memory', 'pids', 'capture_report_loss'})
SINK_MODES = frozenset({'normal', 'parse_hang', 'fsync_hang', 'after_prepared'})


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
        'coding_and_hook_reader_composition_unaccepted'])


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


def sandbox(sources, bindings, args):
    # Existing runtime mount helper explicitly shares host networking. This
    # fixture adds fresh network/user namespaces and disables nested userns.
    command = runtime_sandbox(common_runtime=None)
    command += ['--unshare-net', '--unshare-user', '--disable-userns', '--dir', '/sources',
                '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'HOME', '/tmp',
                '--setenv', 'PYTHONPATH', '/sources', '--setenv', 'LANG', 'C.UTF-8']
    if any(target.startswith('selected_native/') for _, target in sources):
        command += ['--dir', '/sources/selected_native']
    for source, target in sources:
        command += ['--ro-bind', str(source), '/sources/'+target]
    for source, target in bindings:
        command += ['--bind', str(source), target]
    return command[1:]+['--chdir', '/tmp', '/usr/bin/python3', '-B', '/sources/worker.py', *args]


def source_manifest(package):
    result = package.manifest.copy()
    for path in [FIXTURE/'launcher.py', FIXTURE/'worker.py', FIXTURE/'actor.py',
                 FIXTURE/'service_main.py', SCRIPTS/'trial_native_capture.py',
                 SCRIPTS/'trial_process_custody.py', SCRIPTS/'trial_fixture_service.py',
                 SCRIPTS/'trial_task_eval.py', SCRIPTS/'trial_selected_native_mounts.py',
                 SCRIPTS/'trial_owned_workers.py',
                 Path(__file__).resolve()]:
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


def run_fixture(request, retained_root, workspace_root, *, delegated=None, source_root=SELECTED_PACKAGE, controller_loss_stage=None):
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
        package = reviewed_package(source_root, SOURCE_PINS)
        manifest = source_manifest(package)
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
    cleanup_unknown = False
    try:
        base = [(FIXTURE/'worker.py','worker.py')]
        sink_sources = base+list(package.sources)
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
        cleanup_unknown = bool(errors)
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
            try:
                close_errors = child.close()
            except OSError:
                close_errors = ['worker_close_unknown']
            for error in close_errors:
                result.fail(role+'_'+error)
            cleanup_unknown = cleanup_unknown or bool(close_errors)
        try:
            close_errors = group.close()
        except OSError:
            close_errors = ['cgroup_close_unknown']
        for error in close_errors:
            result.fail(error)
        cleanup_unknown = cleanup_unknown or bool(close_errors)
        result.containment['cleanup'] = 'unknown' if cleanup_unknown else 'observed_closed'
    if ready is None and not children.get('actor'):
        result.status = 'refused_prelaunch'
    elif cleanup_unknown or result.containment['released'] is not True or any(v is None for v in result.outer_waits.values()):
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
                result.containment['diagnostic'] = dict(released=released, errors=errors,
                    identity=list(diagnostic_group.identity))
                for error in errors:
                    result.fail('diagnostic_'+error)
                if released is not True or errors:
                    result.persistence = 'unknown'
                if diagnostic_child is not None:
                    result.outer_waits['diagnostic'] = dataclasses.asdict(diagnostic_child.wait) if diagnostic_child.wait else None
                    try:
                        close_errors = diagnostic_child.close()
                    except OSError:
                        close_errors = ['worker_close_unknown']
                    for error in close_errors:
                        result.fail('diagnostic_'+error)
                    if close_errors:
                        result.persistence = 'unknown'
                        if result.status == 'fixture_complete':
                            result.status = 'unknown'
                try:
                    close_errors = diagnostic_group.close()
                except OSError:
                    close_errors = ['cgroup_close_unknown']
                for error in close_errors:
                    result.fail('diagnostic_'+error)
                if close_errors:
                    result.persistence = 'unknown'
                    if result.status == 'fixture_complete':
                        result.status = 'unknown'
    elif ready is not None:
        result.persistence = 'unknown'
    if result.persistence == 'unknown' and result.status == 'fixture_complete':
        result.status = 'unknown'
        result.fail('diagnostic_return_or_cleanup_unknown')
    result.elapsed_seconds = time.monotonic()-started
    return result
