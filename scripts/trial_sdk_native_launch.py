"""Closed executable/config mapping held by the original session owner.

Construction and revalidation are read-only. Starting still requires the outer
owner's selected runtime, namespace/account/capacity decisions. No observation
of a process that has not yet been started is required for source readiness.
"""
import os
from pathlib import Path

from trial_bootstrap_snapshot import HeldTree, file_identity
from trial_sdk_session_driver import SDKSessionDriver
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_native_bootstrap import PreparationRefusal


def require(value, code):
    if not value:
        raise PreparationRefusal(code)


class SDKNativeLaunch:
    def __init__(self, session, source_directory, cwd, env, capture_origin_ns):
        require(type(session) is SDKSessionDriver and session._resources is not None,
                'sdk_native_launch_session')
        require(type(capture_origin_ns) is int and capture_origin_ns >= 0,
                'sdk_native_launch_clock')
        session.verify_sources()
        root = Path(source_directory)
        require(type(source_directory) is str and root.is_absolute(), 'sdk_native_launch_path')
        paths = {'/native/claude': str(root/'claude'),
                 '/native/cairn': str(root/'cairn'),
                 '/config/settings.json': str(root/'settings.json'),
                 '/config/mcp.json': str(root/'mcp.json')}
        self.session, self._snapshot = session, session._custody._snapshot
        self._tree = HeldTree()
        self._bindings = []
        self._closed = False
        try:
            for source, target in paths.items():
                name = {'/native/claude': 'claude', '/native/cairn': 'cairn', '/config/settings.json': 'settings.json',
                        '/config/mcp.json': 'mcp.json'}[source]
                original = self._snapshot.files[name]
                held, identity = self._tree.open_source(target, original[3])
                # Exact original copied inode, not equal caller-reported digests.
                require(file_identity(os.fstat(held.fd)) ==
                        file_identity(os.fstat(original[0].fd)), 'sdk_native_launch_source')
                self._bindings.append((held, identity))
            self._cwd = self._tree.directory(cwd)
            argv = list(session.launch.argv)
            require(argv[0] == '/native/claude', 'sdk_native_launch_argv')
            argv[0] = paths['/native/claude']
            for flag, source in (('--settings', '/config/settings.json'),
                                 ('--mcp-config', '/config/mcp.json')):
                require(argv.count(flag) == 1 and argv[argv.index(flag)+1] == source,
                        'sdk_native_launch_argv')
                argv[argv.index(flag)+1] = paths[source]
            self.argv = tuple(argv)
            NativeProcessOwner._plan(self.argv, cwd, env)
            self.cwd, self.env = cwd, dict(env)
            self.memory_executable = paths['/native/cairn']
            self.capture_origin_ns = capture_origin_ns
            self.deadline_ns = capture_origin_ns + 30_000_000_000
            self._initial = (self.argv, cwd, tuple(sorted(self.env.items())), self.memory_executable)
        except BaseException:
            self.close()
            raise

    def verify(self, session):
        require(not self._closed and session is self.session and
                (self.argv, self.cwd, tuple(sorted(self.env.items())), self.memory_executable) == self._initial,
                'sdk_native_launch_changed')
        session.verify_sources()
        self._tree.check()
        for held, identity in self._bindings:
            require(file_identity(os.fstat(held.fd)) == identity, 'sdk_native_launch_changed')

    def close(self):
        self._closed = True
        return self._tree.close()
