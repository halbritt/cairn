"""One fresh fixed ordinary Node-owner integration; no SDK/provider/namespace."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import stat
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
EXCLUDED = {(64512, n) for n in (22838820, 22946591, 22946592, 22981266, 22981268)}
SOURCE = (
    'scripts/check_trial_sdk_node_owner.py',
    'scripts/trial_sdk_node_owner.cjs', 'scripts/trial_sdk_node_launch.cjs',
    'scripts/trial_sdk_ipc.cjs', 'scripts/trial_sdk_spawn_bridge.cjs',
    'scripts/trial_sdk_node_owner_actor.cjs', 'scripts/trial_sdk_node_owner_host.py',
    'scripts/trial_sdk_node_owner_child.py',
    'scripts/trial_sdk_owned_reactor.py', 'scripts/trial_sdk_parent_reactor.py',
    'scripts/trial_sdk_parent_storage.py', 'scripts/trial_sdk_peer.py',
    'scripts/trial_sdk_native_process_owner.py', 'scripts/trial_sdk_native_input.py',
    'scripts/trial_sdk_ipc.py', 'scripts/trial_sdk_input_receiver.py',
    'scripts/trial_sdk_input_custody.py', 'scripts/trial_sdk_forwarding.py',
    'scripts/trial_sdk_incoming.py',
    'integrations/sdk-ipc/binding.c', 'integrations/sdk-ipc/binding_util.h',
    'integrations/sdk-ipc/peer_identity.h', 'integrations/sdk-ipc/peer_record.h',
    'integrations/sdk-ipc/readiness.h',
)


def read(path):
    for part in (path, *path.parents):
        value = part.lstat()
        if stat.S_ISLNK(value.st_mode) or (value.st_dev, value.st_ino) in EXCLUDED:
            raise ValueError('selected_path_refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if ((before.st_dev, before.st_ino) in EXCLUDED or not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid() or before.st_size > 65536):
            raise ValueError('selected_file_refused')
        body = os.read(fd, before.st_size + 1)
        after = os.fstat(fd)
        identity = lambda value: (value.st_dev, value.st_ino, value.st_size,
                                  value.st_mtime_ns, value.st_ctime_ns)
        if len(body) != before.st_size or identity(before) != identity(after):
            raise ValueError('selected_file_changed')
        return body
    finally:
        os.close(fd)


def digest(body):
    return {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


def write(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def supervise(snapshot, stage):
    """Own one fixed test leader and its inherited group; never reap before containment."""
    result = {'test_pid': None, 'test_wait': None, 'waits': [], 'group_kill': None,
              'group_children_empty': False, 'observed_exit': None, 'pipes': {}, 'errors': []}
    process = pidfd = None
    logs = {'stdout': bytearray(), 'stderr': bytearray()}
    streams = {}
    start = time.monotonic()
    deadline, work_end = start + 12, start + 10
    def close_pipe(stream):
        name = streams.pop(stream)
        row = result['pipes'][name]
        row['close_attempts'] += 1
        try:
            stream.close()
            row['closed_known'] = True
        except BaseException as error:
            result['errors'].append('pipe_close:' + type(error).__name__)
    def retain(name, body):
        room = 65536 - sum(map(len, logs.values()))
        logs[name].extend(body[:room])
        if len(body) > room:
            raise ValueError('output_bound')
    try:
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            raise ValueError('foreign_reaper')
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(36, 1, 0, 0, 0):
            raise OSError(ctypes.get_errno(), 'subreaper')
        value = ctypes.c_int()
        if libc.prctl(37, ctypes.addressof(value), 0, 0, 0) or value.value != 1:
            raise ValueError('subreaper_verify')
        commands = {
            'compile': ['/usr/bin/cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
                        '-fPIC', '-shared', '-fmax-errors=8', '-I/usr/include/node',
                        str(snapshot / 'integrations/sdk-ipc/binding.c'), '-o',
                        str(snapshot / 'integrations/sdk-ipc/binding.node'), '-lm'],
            'host': ['/usr/bin/python3', '-B', str(snapshot / 'scripts/trial_sdk_node_owner_host.py')],
        }
        process = subprocess.Popen(commands[stage], cwd=snapshot,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            close_fds=True, start_new_session=True, env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        result['test_pid'] = process.pid
        streams = {process.stdout: 'stdout', process.stderr: 'stderr'}
        result['pipes'] = {name: {'close_attempts': 0, 'closed_known': False} for name in logs}
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
        pidfd = os.pidfd_open(process.pid)
        while time.monotonic() < work_end:
            for stream in select.select(list(streams), [], [], min(.02, max(0, work_end-time.monotonic())))[0]:
                body = os.read(stream.fileno(), 4096)
                if not body:
                    close_pipe(stream)
                else:
                    retain(streams[stream], body)
            observed = os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if observed is not None:
                result['observed_exit'] = {'pid': observed.si_pid, 'code': observed.si_code,
                                           'status': observed.si_status}
                if not streams:
                    break
        else:
            raise TimeoutError('host_work_deadline')
    except BaseException as error:
        result['errors'].append('observe:' + type(error).__name__)
    finally:
        if process is not None:
            # The fresh owned leader is still unreaped, even after WNOWAIT observed exit.
            # The selected helper never escapes this inherited process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
                result['group_kill'] = 'accepted'
            except ProcessLookupError:
                result['group_kill'] = 'absent'
            except BaseException as error:
                result['errors'].append('containment:' + type(error).__name__)
            while time.monotonic() < deadline:
                try:
                    pid, status = os.waitpid(-process.pid, os.WNOHANG)
                except ChildProcessError:
                    result['group_children_empty'] = True
                    break
                except BaseException as error:
                    result['errors'].append('wait:' + type(error).__name__)
                    break
                if pid:
                    fact = {'pid': pid, 'raw_status': status,
                            'returncode': os.waitstatus_to_exitcode(status)}
                    result['waits'].append(fact)
                    if pid == process.pid:
                        result['test_wait'] = fact
                        process.returncode = fact['returncode']
                else:
                    time.sleep(.005)
            for stream in tuple(streams):
                try:
                    while not os.get_blocking(stream.fileno()) and time.monotonic() < deadline:
                        body = os.read(stream.fileno(), 4096)
                        if not body:
                            break
                        retain(streams[stream], body)
                except BlockingIOError:
                    pass
                except BaseException as error:
                    result['errors'].append('drain:' + type(error).__name__)
                close_pipe(stream)
            if result['test_wait'] is None or not result['group_children_empty']:
                result['errors'].append('unresolved_wait')
        if pidfd is not None:
            fd, pidfd = pidfd, None
            try:
                os.close(fd)
            except BaseException as error:
                result['errors'].append('pidfd_close:' + type(error).__name__)
        result['live_seconds'] = time.monotonic() - start
        result['live_deadline_exceeded'] = time.monotonic() > deadline
    return result, logs


def stage_known(facts):
    return (facts['test_wait'] is not None and facts['test_wait']['returncode'] == 0
            and facts['group_children_empty'] and not facts['errors']
            and not facts['live_deadline_exceeded'] and len(facts['pipes']) == 2
            and all(row == {'close_attempts': 1, 'closed_known': True} for row in facts['pipes'].values()))


def persist_stage(out, stage, facts, logs):
    # Keep actual waits before later source or output persistence can refuse.
    try:
        write(out / (stage + '-OUTER-OBSERVATION.json'), facts)
    except BaseException as error:
        facts['errors'].append('observation_write:' + type(error).__name__)
    for name, body in logs.items():
        try:
            with (out / (stage + '-' + name + '.log')).open('xb') as stream:
                stream.write(body)
        except BaseException as error:
            facts['errors'].append('log_write_' + name + ':' + type(error).__name__)
    facts['logs'] = {name: digest(body) for name, body in logs.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='fresh absolute absent directory')
    out = parser.parse_args().output
    if not out.is_absolute() or '..' in out.parts:
        parser.error('fresh absolute output required')
    for part in out.parents:
        if not stat.S_ISDIR(part.lstat().st_mode) or part.is_symlink():
            raise ValueError('output_ancestry')
    bodies = {name: read(ROOT / name) for name in SOURCE}
    pins = {name: digest(body) for name, body in bodies.items()}
    out.mkdir(mode=0o700)
    snapshot = out / 'source'
    snapshot.mkdir(mode=0o700)
    for name, body in bodies.items():
        path = snapshot / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o400)
        try:
            pending = memoryview(body)
            while pending:
                count = os.write(fd, pending)
                if count <= 0:
                    raise ValueError('snapshot_write')
                pending = pending[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
    write(out / 'INTENT.json', {'request_id': str(uuid.uuid4()), 'source': pins,
          'scope': 'one fresh source-bound ordinary 33-byte input/echo/output-EAGAIN/fullqueue/KILL/real-wait/release join; no SDK/nativeCLI/provider/namespace/admission',
          'stages': ['fixed current C compile', 'fixed combined integration'],
          'per_stage_work_seconds': 10, 'per_stage_cleanup_seconds': 2,
          'per_stage_retained_output_max': 65536, 'replay': False})
    result = {'ok': False, 'errors': [], 'stages': {}, 'pins_stable': False}
    compile_facts, compile_logs = supervise(snapshot, 'compile')
    result['stages']['compile'] = compile_facts
    persist_stage(out, 'compile', compile_facts, compile_logs)
    binary = snapshot / 'integrations/sdk-ipc/binding.node'
    try:
        if not stage_known(compile_facts):
            raise ValueError('compile_refused')
        binary.chmod(0o400)
        result['binary'] = digest(read(binary))
        if pins != {name: digest(read(ROOT / name)) for name in SOURCE}:
            raise ValueError('pre_host_source_drift')
        host_facts, host_logs = supervise(snapshot, 'host')
        result['stages']['host'] = host_facts
        persist_stage(out, 'host', host_facts, host_logs)
        result['pins_stable'] = pins == {name: digest(read(ROOT / name)) for name in SOURCE}
        result['binary_stable'] = result['binary'] == digest(read(binary))
        observed = json.loads(host_logs['stdout'])
        result['host_observation'] = observed
        owner = observed['actor_result']['owner']
        control = observed['actor_result']['control']
        pressure = observed['actor_result']['pressure']
        milestones = observed['milestones']
        result['behavior_pass'] = (observed['ok'] is True and not observed['errors']
            and observed['native_final_wait']['raw_status'] == 9
            and observed['actor_final_wait']['raw_status'] == 0
            and observed['engine']['state'] == 'RELEASED' and observed['engine']['backing_reserved'] == 0
            and observed['engine']['actor_wait'] == 0 and observed['engine']['actor_released']
            and observed['launch_fragments'] >= 2
            and owner['custodied'] == 33 and owner['nativeWritten'] == 33 and owner['releaseAck']
            and owner['phase'] == 'released' and owner['txLoan'] is None and owner['rxLoan'] is None
            and observed['actor_result']['inputBytes'] == 33 and observed['actor_result']['echoBytes'] == 33
            and observed['actor_result']['outputBytes'] == pressure['accepted'] + 33
            and pressure['eagain'] is True and pressure['errno'] == 11
            and control['readArmedBefore'] is True and control['maskBefore'] == 1
            and control['readableBefore'] == 2048 and control['accepted'] is True
            and control['controlAccepted'] is True and control['waitSeenAfterExchange'] is False
            and observed['actor_result']['exchanges'] == 1 and observed['actor_result']['lastExchangeCount'] == 33
            and any(row['state'] == 'WAIT_RELEASE' for row in milestones)
            and any(row['state'] == 'ENGINE_LOCAL_RELEASED' and row['local_reserved'] == 0
                    and row['counterparty_reserved'] == row['backing_reserved'] == 22386 for row in milestones)
            and milestones[-1]['state'] == 'RELEASED')
        result['ok'] = (stage_known(host_facts) and result['behavior_pass']
                        and result['pins_stable'] and result['binary_stable'])
    except BaseException as error:
        result['errors'].append('verification:' + type(error).__name__)
    try:
        write(out / 'RESULT.json', result)
    except BaseException as error:
        result['errors'].append('result_write:' + type(error).__name__)
        result['ok'] = False
    print(json.dumps({'output': str(out), 'ok': result['ok'], 'errors': result['errors'],
          'stages': result['stages']}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
