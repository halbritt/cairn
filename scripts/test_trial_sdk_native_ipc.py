"""Fresh-output ordinary Linux/Node addon integration checks.

This does not run the SDK, native CLI, namespaces, providers, or an evaluation.
"""
import argparse
import array
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import select
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent / 'integrations' / 'sdk-ipc'
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
CASES = ('api', 'roundtrip', 'valid32', 'valid544', 'rights1', 'rights32',
         'oversize', 'empty', 'wrong-sender', 'wrong-pid', 'stream',
         'unprotected', 'peer-exit', 'timeout-reply', 'timeout-send', 'originals-closed')


def read(path, maximum=131072):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        a = os.fstat(fd)
        if not stat.S_ISREG(a.st_mode) or a.st_size > maximum:
            raise ValueError('selected_file_boundary')
        if a.st_dev == 64512 and a.st_ino in {22838820, 22946591, 22946592, 22981266, 22981268}:
            raise ValueError('excluded_file')
        body = os.read(fd, a.st_size + 1)
        z = os.fstat(fd)
        if len(body) != a.st_size or (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns) != (
                z.st_dev, z.st_ino, z.st_size, z.st_mtime_ns, z.st_ctime_ns):
            raise ValueError('selected_file_changed')
        return body
    finally:
        os.close(fd)


def digest(body):
    return {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


def pins(base, names):
    return {name: digest(read(base / name)) for name in names}


def exclusive(path, value):
    with path.open('x') as out:
        json.dump(value, out, indent=2)
        out.write('\n')


def finish(child, deadline):
    """Own and retain the actual wait; no close/SIGKILL outcome is synthesized."""
    killed = False
    try:
        code = child.wait(timeout=max(.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        killed = True
        code = child.wait(timeout=2)
    return {'owned_pid': child.pid, 'actual_wait': code, 'killed': killed}


class Output:
    def __init__(self, child, deadline):
        self.selector = selectors.DefaultSelector()
        self.logs = {'stdout': bytearray(), 'stderr': bytearray()}
        self.pending = bytearray()
        self.deadline = deadline
        for name in self.logs:
            stream = getattr(child, name)
            os.set_blocking(stream.fileno(), False)
            self.selector.register(stream, selectors.EVENT_READ, name)

    def pump(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('owned_output_timeout')
        for key, _ in self.selector.select(min(.05, remaining)):
            block = os.read(key.fd, 4096)
            if not block:
                self.selector.unregister(key.fileobj)
                key.fileobj.close()
                continue
            if sum(map(len, self.logs.values())) + len(block) > 65536:
                raise ValueError('owned_output_bound')
            self.logs[key.data].extend(block)
            if key.data == 'stdout':
                self.pending.extend(block)

    def message(self):
        while b'\n' not in self.pending:
            if not self.selector.get_map():
                raise ValueError('owned_output_eof')
            self.pump()
        line, _, rest = self.pending.partition(b'\n')
        self.pending = bytearray(rest)
        return json.loads(line)

    def drain(self):
        while self.selector.get_map():
            self.pump()

    def close(self):
        for key in list(self.selector.get_map().values()):
            self.selector.unregister(key.fileobj)
            key.fileobj.close()
        self.selector.close()


def helper(mode, fd=None):
    args = ['/usr/bin/python3', str(ROOT / 'test_trial_sdk_native_ipc_helper.py'), mode]
    inherited = ()
    if fd is not None:
        args.append(str(fd))
        inherited = (fd,)
    return subprocess.Popen(args, env=ENV, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            pass_fds=inherited, start_new_session=True)


def case(mode, binary, suite_deadline=None):
    deadline = time.monotonic() + 3
    if suite_deadline is not None:
        deadline = min(deadline, suite_deadline)
    kind = socket.SOCK_STREAM if mode == 'stream' else socket.SOCK_SEQPACKET
    parent, node = socket.socketpair(socket.AF_UNIX, kind)
    for endpoint in (parent, node):
        endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    parent.settimeout(1)
    holder = helper('hold') if mode == 'peer-exit' else None
    peer_pid = holder.pid if holder else os.getpid()
    peer_pidfd = os.pidfd_open(peer_pid)
    child = output = node_pidfd = None
    result = {'case': mode, 'helper_waits': []}
    try:
        child = subprocess.Popen(['/usr/bin/node', '--disable-proto=throw', str(ROOT / 'test_trial_sdk_native_ipc.cjs'),
                                  str(binary), mode, str(node.fileno()), str(peer_pidfd),
                                  str(peer_pid), str(os.getuid()), str(os.getgid())],
                                 cwd=ROOT, env=ENV, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, pass_fds=(node.fileno(), peer_pidfd),
                                 start_new_session=True)
        node_pidfd = os.pidfd_open(child.pid)
        result['node_pidfd_held'] = True
        node.close()
        output = Output(child, deadline)
        ready = output.message()
        if ready != {'phase': 'ready', 'mode': mode, 'pid': child.pid}:
            result['setup_observation'] = ready
            raise ValueError('node_setup_failed')
        result['owned_pid_confirmed'] = True

        def receive(expected):
            wire, control, flags, _ = parent.recvmsg(545, socket.CMSG_SPACE(struct.calcsize('3i')))
            if wire != expected or flags:
                raise ValueError('parent_wire_mismatch')
            credentials = [(level, kind, struct.unpack('3i', body)) for level, kind, body in control]
            if credentials != [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
                                (child.pid, os.getuid(), os.getgid()))]:
                raise ValueError('parent_credential_mismatch')

        if mode in ('valid32', 'valid544', 'originals-closed'):
            length = int(mode[5:]) if mode.startswith('valid') else 32
            if parent.send(b'b' * length) != length:
                raise ValueError('parent_short_send')
        elif mode in ('rights1', 'rights32'):
            descriptor = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC)
            try:
                count = int(mode[6:])
                rights = array.array('i', [descriptor] * count)
                if parent.sendmsg([b'b' * 32], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, rights)]) != 32:
                    raise ValueError('parent_short_rights_send')
            finally:
                os.close(descriptor)
        elif mode in ('oversize', 'empty'):
            wire = b'b' * (545 if mode == 'oversize' else 0)
            if parent.send(wire) != len(wire):
                raise ValueError('parent_short_refusal_send')
        elif mode == 'wrong-sender':
            sender = helper('send', parent.fileno())
            sender.stdin.close()
            status = finish(sender, deadline)
            result['helper_waits'].append(status)
            if status['actual_wait'] or status['killed']:
                raise ValueError('wrong_sender_failed')
        elif mode == 'peer-exit':
            holder.stdin.write(b'x')
            holder.stdin.close()
            status = finish(holder, deadline)
            result['helper_waits'].append(status)
            if status['actual_wait'] or status['killed']:
                raise ValueError('holder_exit_failed')
            holder = None
        child.stdin.write(b'x')
        child.stdin.close()
        if mode == 'roundtrip':
            receive(b'Q' * 32)
            if parent.send(b'R' * 544) != 544:
                raise ValueError('parent_short_reply')
            receive(b'S' * 33)
            result['exchange_requests_observed'] = 1
        elif mode == 'timeout-reply':
            receive(b'Q' * 32)
            result['exchange_requests_observed'] = 1
        saturated = None
        if mode == 'timeout-send':
            saturated = output.message()
            if saturated.get('phase') != 'saturated' or not 0 < saturated['count'] < 1024:
                raise ValueError('missing_saturation')
        observed = output.message()
        result['node_result'] = observed
        if observed.get('phase') != 'result' or observed.get('mode') != mode or not observed.get('ok'):
            raise ValueError('node_case_failed')
        if saturated:
            parent.setblocking(False)
            count = 0
            while True:
                try:
                    wire = parent.recv(545)
                except BlockingIOError:
                    break
                if not wire:
                    break
                if wire != b'D' * 544 or count >= 1024:
                    raise ValueError('saturated_wire_mismatch')
                count += 1
            if count != saturated['count']:
                raise ValueError('saturation_count_mismatch')
            result['fill_records_drained'] = count
            result['exchange_requests_observed'] = 0
        output.drain()
        if output.logs['stderr'] or output.pending:
            raise ValueError('unexpected_output')
        result.update(finish(child, deadline))
        if result['actual_wait'] or result['killed']:
            raise ValueError('node_wait_failed')
        result['ok'] = True
    except Exception as error:
        result['ok'] = False
        result['failure'] = str(error)[:80] if isinstance(error, (ValueError, TimeoutError)) else type(error).__name__
    finally:
        if child and 'actual_wait' not in result:
            result.update(finish(child, deadline))
        if output:
            if not result.get('ok'):
                try:
                    output.drain()
                except Exception as error:
                    result['failure_drain'] = type(error).__name__
            result['logs'] = {name: digest(body) for name, body in output.logs.items()}
            if not result.get('ok'):
                result['failure_stdout'] = bytes(output.logs['stdout']).decode('utf-8', errors='replace')
                result['failure_stderr'] = bytes(output.logs['stderr']).decode('utf-8', errors='replace')
            output.close()
        if child and child.stdin and not child.stdin.closed:
            child.stdin.close()
        if holder:
            holder.stdin.close()
            result['helper_waits'].append(finish(holder, deadline))
        if node_pidfd is not None:
            os.close(node_pidfd)
        os.close(peer_pidfd)
        node.close()
        parent.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path,
                        help='fresh absolute output directory; never reuse an attempt')
    args = parser.parse_args()
    out = args.output
    if not out.is_absolute():
        parser.error('output must be absolute')
    source_names = ('binding.c', 'binding_util.h', 'peer_identity.h', 'peer_record.h', 'readiness.h')
    harness_names = ('test_trial_sdk_native_ipc.py', 'test_trial_sdk_native_ipc.cjs',
                     'test_trial_sdk_native_ipc_helper.py',
                     'trial_sdk_native_ipc_readiness_cases.py', 'trial_sdk_native_ipc_control_cases.py',
                     'test_trial_sdk_native_ipc_readiness.cjs', 'test_trial_sdk_native_ipc_teardown.cjs',
                     'test_trial_sdk_native_ipc_aggregate.cjs', 'test_trial_sdk_native_ipc_aggregate_worker.cjs',
                     'test_trial_sdk_native_ipc_control.cjs')
    def selected():
        return {'source': pins(SOURCE, source_names), 'harness': pins(ROOT, harness_names)}
    before = selected()
    out.mkdir(mode=0o700)  # Atomic refusal for existing directory or dangling symlink.
    snapshot = out / 'source'
    snapshot.mkdir(mode=0o700)
    for group, base in (('source', SOURCE), ('harness', ROOT)):
        for name, expected in before[group].items():
            body = read(base / name)
            if digest(body) != expected:
                raise ValueError('selected_source_changed')
            descriptor = os.open(snapshot / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o400)
            try:
                pending = memoryview(body)
                while pending:
                    count = os.write(descriptor, pending)
                    if count <= 0:
                        raise ValueError('snapshot_short_write')
                    pending = pending[count:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    def snapshot_module(name):
        spec = importlib.util.spec_from_file_location(name, snapshot / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    # Only the fixed case functions run. The snapshotted runner's main is inert.
    legacy = snapshot_module('test_trial_sdk_native_ipc')
    readiness = snapshot_module('trial_sdk_native_ipc_readiness_cases')
    control = snapshot_module('trial_sdk_native_ipc_control_cases')
    suites = (('legacy', legacy.CASES, legacy.case), ('readiness', readiness.CASES, readiness.case),
              ('control', control.CASES, control.case))
    if tuple(len(modes) for _, modes, _ in suites) != (16, 12, 10):
        raise ValueError('selected_case_counts')
    binary = out / 'binding.node'
    command = ['/usr/bin/cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-pedantic',
               '-fPIC', '-shared', '-fmax-errors=8', '-I/usr/include/node',
               str(snapshot / 'binding.c'), '-o', str(binary), '-lm']
    exclusive(out / 'INTENT.json', {'request_id': str(uuid.uuid4()), 'selected': before,
              'command': command, 'cases': {name: modes for name, modes, _ in suites},
              'scope': 'fresh ordinary owned processes only; no SDK/native CLI/namespace/provider/admission'})
    start = time.monotonic()
    child = subprocess.Popen(command, cwd=ROOT, env=ENV, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    output = Output(child, start + 10)
    result = {'cases': []}
    try:
        try:
            output.drain()
            result['build'] = finish(child, start + 10)
            result['build']['ok'] = result['build']['actual_wait'] == 0 and not result['build']['killed']
        except Exception as error:
            result['build'] = finish(child, time.monotonic())
            result['build'].update(ok=False, failure=type(error).__name__)
        result['build']['logs'] = {name: digest(body) for name, body in output.logs.items()}
        for name, body in output.logs.items():
            (out / ('build-' + name + '.log')).write_bytes(body)
        if result['build']['ok']:
            os.chmod(binary, 0o400)
            suite_deadline = time.monotonic() + 10
            for name, modes, run_case in suites:
                for mode in modes:
                    if time.monotonic() >= suite_deadline:
                        result['failure'] = 'overall_case_bound'
                        break
                    row = run_case(mode, binary, suite_deadline)
                    row['suite'] = name
                    result['cases'].append(row)
                    if not row['ok']:
                        break
                if result.get('failure') or not result['cases'][-1]['ok']:
                    break
        result['pins_stable'] = before == selected()
        result['ok'] = (result['build']['ok'] and result['pins_stable']
                        and len(result['cases']) == sum(len(modes) for _, modes, _ in suites)
                        and all(row['ok'] for row in result['cases']))
    finally:
        output.close()
        result['seconds'] = time.monotonic() - start
        if os.path.lexists(binary):
            result['binary'] = digest(read(binary, 1024*1024))
        exclusive(out / 'RESULT.json', result)
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
