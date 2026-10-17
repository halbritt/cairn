"""Fresh fixed Linux host cases; no SDK, native CLI, provider or namespace."""
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

HERE = Path(__file__).resolve().parent
NAMES = ('check_trial_sdk_native_process_owner.py', 'trial_sdk_native_process_owner.py',
         'trial_sdk_native_process_owner_host_cases.py', 'trial_sdk_native_process_owner_child.py',
         'trial_sdk_native_input.py', 'trial_sdk_input_receiver.py', 'trial_sdk_input_custody.py',
         'trial_sdk_forwarding.py', 'trial_sdk_incoming.py')
EXCLUDED = {(64512, n) for n in (22838820, 22946591, 22946592, 22981266, 22981268)}


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


def supervise(snapshot):
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
        process = subprocess.Popen(['/usr/bin/python3', '-B',
            str(snapshot / 'trial_sdk_native_process_owner_host_cases.py')], cwd=snapshot,
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path, help='fresh absolute absent directory')
    args = parser.parse_args()
    out = args.output
    if not out.is_absolute() or '..' in out.parts:
        parser.error('fresh absolute output required')
    for part in out.parents:
        if not stat.S_ISDIR(part.lstat().st_mode) or part.is_symlink():
            raise ValueError('output_ancestry')
    bodies = {name: read(HERE / name) for name in NAMES}
    before = {name: digest(body) for name, body in bodies.items()}
    out.mkdir(mode=0o700)  # Atomic refusal for existing paths, including dangling symlinks.
    snapshot = out / 'source'
    snapshot.mkdir(mode=0o700)
    for name, body in bodies.items():
        fd = os.open(snapshot / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o400)
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
    write(out / 'INTENT.json', {'request_id': str(uuid.uuid4()), 'source': before,
          'host_cases': 11, 'fixed_ordinary_children': 9, 'work_seconds': 10,
          'cleanup_reserve_seconds': 2, 'retained_output_max': 65536,
          'scope': 'fixed ordinary owned Python children/socket/pidfd cases only; no SDK/native CLI/provider/namespace/evaluation'})
    result, logs = supervise(snapshot)
    for name, body in logs.items():
        with (out / (name + '.log')).open('xb') as stream:
            stream.write(body)
    result['logs'] = {name: digest(body) for name, body in logs.items()}
    result['pins_stable'] = before == {name: digest(read(HERE / name)) for name in NAMES}
    result['ok'] = False
    try:
        facts = json.loads(logs['stdout'])
        rows = facts['owned_children']
        result['host_observations'] = facts
        # One intentional post-close uncertainty must remain released:false.
        result['behavior_pass'] = (facts['test_count'] == 11 and facts['successful'] is True
            and len(rows) == 9 and facts['fd_counts'][0] == facts['fd_counts'][1]
            and all(type(row['pid']) is int and row['pid'] > 1
                    and row['wait']['pid'] == row['pid'] and row['release']['handles_detached']
                    and not row['has_handles'] for row in rows)
            and sum(row['release']['released'] is True for row in rows) == 8
            and sum(row['release']['released'] is False for row in rows) == 1)
        result['ok'] = (result['behavior_pass'] and result['test_wait'] is not None
            and result['test_wait']['returncode'] == 0 and result['group_children_empty']
            and not result['errors'] and not result['live_deadline_exceeded'] and result['pins_stable']
            and len(result['pipes']) == 2
            and all(row == {'close_attempts': 1, 'closed_known': True} for row in result['pipes'].values()))
    except (ValueError, KeyError, TypeError, IndexError) as error:
        result['errors'].append('result:' + type(error).__name__)
    write(out / 'RESULT.json', result)
    print(json.dumps({'output': str(out), 'ok': result['ok'], 'test_wait': result['test_wait'],
                      'group_children_empty': result['group_children_empty'], 'errors': result['errors']}))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
