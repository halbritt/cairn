"""Inject EIO only into owned ledger publication writes or syncs (Linux tests only).

The child inherits a seccomp syscall notifier. Its parent checks the actual fd
path and file type before replying; scheduler eventfds and output pipes always
continue. No production code, shared process or filesystem is modified.
"""
import argparse
import array
import ctypes as C
import errno
import json
import os
from pathlib import Path
import select
import signal
import socket
import stat
import sys
import time


class Data(C.Structure):
    _fields_ = [('nr', C.c_int), ('arch', C.c_uint), ('ip', C.c_uint64),
                ('args', C.c_uint64 * 6)]


class Notification(C.Structure):
    _fields_ = [('id', C.c_uint64), ('pid', C.c_uint), ('flags', C.c_uint), ('data', Data)]


class Response(C.Structure):
    _fields_ = [('id', C.c_uint64), ('value', C.c_int64), ('error', C.c_int), ('flags', C.c_uint)]


def checked(code, operation):
    if code < 0:
        raise OSError(-code, operation)
    return code


class Child:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None

    def poll(self):
        if self.returncode is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self):
        if self.returncode is None:
            _, status = os.waitpid(self.pid, 0)
            self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode


def run(args):
    Path(args.trace).write_text('')
    # Load the ABI directly: the isolated fixture intentionally has no host
    # ldconfig cache or compiler lookup for ctypes.util.find_library.
    lib = C.CDLL('libseccomp.so.2', use_errno=True)
    for name, parameters, result in (
            ('seccomp_init', [C.c_uint], C.c_void_p),
            ('seccomp_syscall_resolve_name', [C.c_char_p], C.c_int),
            ('seccomp_rule_add', [C.c_void_p, C.c_uint, C.c_int, C.c_uint], C.c_int),
            ('seccomp_load', [C.c_void_p], C.c_int),
            ('seccomp_notify_fd', [C.c_void_p], C.c_int),
            ('seccomp_notify_alloc', [C.POINTER(C.POINTER(Notification)), C.POINTER(C.POINTER(Response))], C.c_int),
            ('seccomp_notify_free', [C.POINTER(Notification), C.POINTER(Response)], None),
            ('seccomp_notify_receive', [C.c_int, C.POINTER(Notification)], C.c_int),
            ('seccomp_notify_respond', [C.c_int, C.POINTER(Response)], C.c_int)):
        function = getattr(lib, name)
        function.argtypes, function.restype = parameters, result
    libc = C.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [C.c_int, C.c_ulong, C.c_ulong, C.c_ulong, C.c_ulong]
    libc.prctl.restype = C.c_int
    selected_call = checked(lib.seccomp_syscall_resolve_name(args.operation.encode('ascii')),
                            'resolve publication syscall')
    parent_pid = os.getpid()
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    parent.settimeout(args.timeout)

    def install():
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) or os.getppid() != parent_pid:
            os._exit(125)
        if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS
            os._exit(125)
        context = lib.seccomp_init(0x7fff0000)  # SCMP_ACT_ALLOW
        if not context:
            os._exit(125)
        checked(lib.seccomp_rule_add(context, 0x7fc00000, selected_call, 0), 'notify publication syscall')
        checked(lib.seccomp_load(context), 'load publication notifier')
        listener = lib.seccomp_notify_fd(context)
        if listener < 0:
            os._exit(125)
        try:
            child.sendmsg([b'F'], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array('i', [listener]))])
        except OSError:
            os._exit(125)
        os.close(listener)
        child.close()

    process = None
    listener = None
    request, response = C.POINTER(Notification)(), C.POINTER(Response)()
    directory = Path(args.directory).resolve()
    count = 0
    try:
        pid = os.fork()
        if pid == 0:
            try:
                parent.close()
                os.setsid()
                install()
                os.execvpe(args.command[0], args.command, os.environ)
            except BaseException:
                os._exit(125)
        process = Child(pid)
        child.close()
        tag, control, _, _ = parent.recvmsg(1, socket.CMSG_SPACE(array.array('i').itemsize))
        descriptors = array.array('i')
        for level, kind, raw in control:
            if (level, kind) == (socket.SOL_SOCKET, socket.SCM_RIGHTS):
                descriptors.frombytes(raw)
        if tag != b'F' or len(descriptors) != 1:
            raise RuntimeError('missing child publication notifier')
        listener = descriptors[0]
        os.set_inheritable(listener, False)
        checked(lib.seccomp_notify_alloc(C.byref(request), C.byref(response)), 'allocate notifier records')
        deadline = time.monotonic() + args.timeout
        with Path(args.trace).open('w') as trace:
            while process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('ledger fault child did not finish')
                if not select.select([listener], [], [], min(remaining, 0.1))[0]:
                    continue
                C.memset(request, 0, C.sizeof(Notification))
                code = lib.seccomp_notify_receive(listener, request)
                if code in (-errno.EINTR, -errno.ENOENT, -errno.ECANCELED):
                    continue
                checked(code, 'receive publication notification')
                event = request.contents
                fd = int(event.data.args[0])
                path = None
                regular = is_directory = False
                try:
                    proc_fd = f'/proc/{event.pid}/fd/{fd}'
                    path = os.readlink(proc_fd)
                    mode = os.stat(proc_fd).st_mode
                    regular, is_directory = stat.S_ISREG(mode), stat.S_ISDIR(mode)
                except FileNotFoundError:
                    pass  # A dead tracee's notification will be refused by the kernel.
                temporary = (regular and path is not None and Path(path).parent == directory
                             and Path(path).name.startswith('.cairn-state-'))
                directory_sync = (args.operation == 'fsync' and is_directory
                                  and path is not None and Path(path) == directory)
                matched = event.data.nr == selected_call and (temporary or directory_sync)
                if matched:
                    count += 1
                injected = matched and count == args.nth
                response.contents.id = event.id
                response.contents.value = 0
                response.contents.error = -errno.EIO if injected else 0
                response.contents.flags = 0 if injected else 1  # SECCOMP_USER_NOTIF_FLAG_CONTINUE
                code = lib.seccomp_notify_respond(listener, response)
                if code == -errno.ENOENT:
                    continue
                checked(code, 'respond to publication notification')
                if matched or (path and 'eventfd' in path):
                    trace.write(json.dumps(dict(pid=event.pid, fd=fd, path=path, regular=regular, directory=is_directory, operation=args.operation,
                                                publication_call=count if matched else None,
                                                injected=injected, errno=errno.EIO if injected else None)) + '\n')
                    trace.flush()
        return process.returncode
    finally:
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass  # The owned child exited between poll and kill.
            process.wait()
        if listener is not None:
            os.close(listener)
        lib.seccomp_notify_free(request, response)
        parent.close()
        child.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', required=True)
    parser.add_argument('--operation', choices=('write', 'fsync'), default='write')
    parser.add_argument('--nth', type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument('--trace', required=True)
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    if arguments.command[:1] == ['--']:
        arguments.command.pop(0)
    if arguments.operation == 'write' and arguments.nth > 2:
        parser.error('write ordinal must be 1 or 2')
    if not arguments.command:
        parser.error('child command required')
    sys.exit(run(arguments))
