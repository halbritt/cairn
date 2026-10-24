"""Private historical source fixture shared by fixed SDK mechanics tests.

Tests that exercise the pinned producer use the authored source epoch, not the
live checkout. Tests specifically about current-source refusal keep using the
live path and do not call this helper.
"""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
from unittest.mock import patch

import trial_sdk_fixed_bootstrap as fixed_bootstrap
from trial_sdk_fixed_source_pins import SOURCE_PINS

FIXED_SOURCE_REVISION = '43c4b6170f1f7784d70d55ca6669d770ab214ed1'
FIXED_MEMORY_BLOB = 'f1d5638f77b510298d90a4945479dfc2e256d8d9'


def stage_pinned_sources(test):
    """Stage and verify the fixed producer's authored sources in test-owned storage."""
    staged = test.root / 'fixed-pinned-source'
    staged.mkdir(mode=0o700)
    scripts = staged / 'scripts'
    scripts.mkdir(mode=0o700)
    integrations = staged / 'integrations'
    integrations.mkdir(mode=0o700)
    lifecycle = integrations / 'lifecycle'
    lifecycle.mkdir(mode=0o700)
    test.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o700)
    test.assertEqual(stat.S_IMODE(scripts.stat().st_mode), 0o700)
    test.assertEqual(stat.S_IMODE(integrations.stat().st_mode), 0o700)
    test.assertEqual(stat.S_IMODE(lifecycle.stat().st_mode), 0o700)

    repo = Path(__file__).resolve().parent.parent
    original_modes = {}
    for name, expected in SOURCE_PINS.items():
        relative = ('integrations/lifecycle/memory.py' if name == 'memory.py'
                    else 'scripts/' + name)
        source = repo / relative
        target = lifecycle / name if name == 'memory.py' else scripts / name
        original_modes[str(source)] = stat.S_IMODE(source.stat().st_mode)
        raw = subprocess.check_output(
            ['git', 'show', f'{FIXED_SOURCE_REVISION}:{relative}'], cwd=repo)
        test.assertLessEqual(len(raw), 256 * 1024 if name == 'memory.py' else 65536)
        test.assertEqual(hashlib.sha256(raw).hexdigest(), expected,
                         f'{relative} no longer matches the authored source pin')

        target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                            | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
        try:
            view = memoryview(raw)
            while view:
                written = os.write(target_fd, view)
                test.assertGreater(written, 0)
                view = view[written:]
            os.fsync(target_fd)
            os.fchmod(target_fd, 0o400)
            sealed_identity = os.fstat(target_fd)
        finally:
            os.close(target_fd)
        target_fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            initial = os.fstat(target_fd)
            test.assertEqual((initial.st_dev, initial.st_ino, initial.st_mode,
                              initial.st_uid, initial.st_gid, initial.st_nlink,
                              initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns),
                             (sealed_identity.st_dev, sealed_identity.st_ino,
                              sealed_identity.st_mode, sealed_identity.st_uid,
                              sealed_identity.st_gid, sealed_identity.st_nlink,
                              sealed_identity.st_size, sealed_identity.st_mtime_ns,
                              sealed_identity.st_ctime_ns))
            copied = bytearray()
            while len(copied) <= len(raw):
                block = os.read(target_fd, min(8192, len(raw) + 1 - len(copied)))
                if not block:
                    break
                copied.extend(block)
            final = os.fstat(target_fd)
            test.assertEqual((final.st_dev, final.st_ino, final.st_mode,
                              final.st_uid, final.st_gid, final.st_nlink,
                              final.st_size, final.st_mtime_ns, final.st_ctime_ns),
                             (initial.st_dev, initial.st_ino, initial.st_mode,
                              initial.st_uid, initial.st_gid, initial.st_nlink,
                              initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns))
            named = os.stat(target, follow_symlinks=False)
            test.assertEqual((named.st_dev, named.st_ino, named.st_mode),
                             (initial.st_dev, initial.st_ino, initial.st_mode))
            test.assertEqual(hashlib.sha256(copied).hexdigest(), expected)
            test.assertEqual(len(copied), len(raw))
            test.assertEqual(stat.S_IMODE(initial.st_mode), 0o400)
        finally:
            os.close(target_fd)
        raw = None

    test._pinned_source_original_modes = original_modes
    test._pinned_source_fixture = staged
    test._fixed_bootstrap_path_patch = patch.object(
        fixed_bootstrap, '__file__', str(scripts / 'trial_sdk_fixed_bootstrap.py'))
    test._fixed_bootstrap_path_patch.start()
    test.addCleanup(test._fixed_bootstrap_path_patch.stop)
