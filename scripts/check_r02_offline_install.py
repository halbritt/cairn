#!/usr/bin/env python3
"""Build in the shared wrapper, then install with only a standalone package bound.

This is a host orchestration entrypoint. Every build and project assertion runs
inside the existing isolation wrapper; the second namespace has no checkout.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def packaged_check():
    assert not (ROOT / 'go.mod').exists(), 'checkout accidentally included in package'
    assert not (ROOT / '.git').exists(), 'checkout accidentally included in package'
    assert not Path('/home/halbritt/git/cairn').exists(), 'production checkout visible'
    sys.path.insert(0, str(ROOT / 'scripts'))
    from test_r02_compatibility_install import InstalledFixture
    from test_f04_activation import SESSION
    with tempfile.TemporaryDirectory() as temporary:
        f = InstalledFixture(Path(temporary) / 'home', ROOT / 'assets/private-activation',
                             stager=ROOT / 'assets/stager', enabled=True)
        result = f.public('memory')
        assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b''), result
        old_argv, old_pin = f.launcher('memory'), dict(f.selected)
        f.publish_configuration(enabled=False)
        inspected = f.installer_call('inspect', '--path', old_pin['path'], '--sha256', old_pin['sha256'])
        assert inspected.returncode == 0, inspected.stderr
        shutil.rmtree(f.package)
        # A fresh process after deleting the producer inputs still reaches the
        # selected real activation and retained frontend, with no checkout.
        result = subprocess.run([str(old_argv)], input=f.raw, capture_output=True, timeout=18, env=f.env())
        assert (result.returncode, result.stdout, result.stderr) == (0, b'{}\n', b''), result
        state = json.loads((Path(f.memory['state_dir']) / (SESSION + '.memory-budget.json')).read_bytes())
        assert list(state['inbox_activation'].values()) == [True]
    print('R02 standalone-package install, retained selection and fresh-process activation: PASS', flush=True)
    # Reuse the real renderer/process oracle with the package's prebuilt tools.
    # No build or source checkout is available in this second namespace.
    from test_r04_hook_settings import R04HookSettingsTests
    case = R04HookSettingsTests('test_each_emitted_hook_runs_selected_real_activation_after_producer_inputs_are_removed')
    case.binary, case.stager = ROOT / 'assets/private-activation', ROOT / 'assets/stager'
    case.setUp()
    try:
        case.test_each_emitted_hook_runs_selected_real_activation_after_producer_inputs_are_removed()
    finally:
        case.doCleanups()
    print('R04 standalone-package settings and every emitted host hook: PASS', flush=True)
    from test_r04_host_wiring import R04HostWiringTests
    case = R04HostWiringTests('test_opencode_uses_checked_source_cells_and_preserves_reverse_inputs')
    case.binary, case.stager = ROOT / 'assets/private-activation', ROOT / 'assets/stager'
    case.setUp()
    try:
        case.test_opencode_uses_checked_source_cells_and_preserves_reverse_inputs()
    finally:
        case.doCleanups()
    print('R04 standalone-package OpenCode receipt-checked Go/Source hooks: PASS', flush=True)
    case = R04HostWiringTests('test_opencode_tool_rows_match_retained_source_after_package_removal')
    case.binary, case.stager = ROOT / 'assets/private-activation', ROOT / 'assets/stager'
    case.setUp()
    try:
        case.test_opencode_tool_rows_match_retained_source_after_package_removal()
    finally:
        case.doCleanups()
    print('R04 standalone-package OpenCode tool commands Go/Source pair: PASS', flush=True)
    case = R04HostWiringTests('test_agy_installed_go_events_match_retained_source_after_package_removal')
    case.binary, case.stager = ROOT / 'assets/private-activation', ROOT / 'assets/stager'
    case.setUp()
    try:
        case.test_agy_installed_go_events_match_retained_source_after_package_removal()
    finally:
        case.doCleanups()
    print('R04 standalone-package Agy coordination Go/Source hooks: PASS', flush=True)


# Runs only inside the first namespace. The stdout archive is extracted as data
# by the host; no generated binary or project code executes on the host.
BUILD_PACKAGE = r'''
import os, pathlib, shutil, subprocess, sys, tarfile
os.umask(0o077)
root=pathlib.Path('/work'); package=pathlib.Path('/tmp/r02-package'); package.mkdir(mode=0o700)
for sub in ('assets','scripts','integrations/lifecycle','integrations/opencode'): (package/sub).mkdir(parents=True,exist_ok=True)
for name,target in (('stager','./cmd/cairn-stage-lifecycle'),('private-activation','./cmd/cairn-lifecycle')):
 path=package/'assets'/name
 subprocess.run(['go','build','-buildvcs=false','-o',str(path),target],cwd=root,check=True)
 path.chmod(0o500)
sys.path.insert(0,str(root/'scripts'))
from test_f04_activation import compat
for name in compat.MEMBERS:
 path=package/'integrations/lifecycle'/name
 shutil.copyfile(root/'integrations/lifecycle'/name,path);path.chmod(0o600)
for name in ('lifecycle.ts','coordination.ts'):
 path=package/'integrations/opencode'/name
 shutil.copyfile(root/'integrations/opencode'/name,path);path.chmod(0o600)
for name in ('check_r02_offline_install.py','stage_lifecycle_compatibility.py','test_r02_compatibility_install.py',
             'test_f04_activation.py','test_f04_issued.py','test_lifecycle_generation_install.py',
             'stage_lifecycle_hooks.py','test_r04_hook_settings.py','test_f04_deadlines.py',
             'test_r04_host_wiring.py','run_r04_opencode_hooks.mjs'):
 path=package/'scripts'/name;shutil.copyfile(root/'scripts'/name,path);path.chmod(0o600)
with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as archive:
 for path in sorted(package.iterdir()): archive.add(path,arcname=path.name)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside-package', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside_package:
        packaged_check()
        return
    sys.path.insert(0, str(ROOT / 'scripts'))
    import test_hybrid_runtime_operations as isolation
    with tempfile.TemporaryDirectory(prefix='cairn-r02-offline-') as temporary:
        base = Path(temporary)
        archive = base / 'package.tar.gz'
        command = ['env', 'CC=gcc', 'TZ=America/Los_Angeles', 'TMPDIR=/tmp', 'GOFLAGS=-buildvcs=false',
                   '/usr/bin/python3', '-I', '-B', '-c', BUILD_PACKAGE]
        with archive.open('wb') as output:
            subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/test_hybrid_runtime_operations.py'),
                            '--timeout', '600', '--', *command], cwd=ROOT, stdout=output, check=True, timeout=650)
        package = base / 'package'
        package.mkdir(mode=0o700)
        with tarfile.open(archive) as data:
            data.extractall(package, filter='data')
        for path in package.rglob('*'):
            if path.is_dir():
                path.chmod(0o700)
        # Tar's safe extraction filter removes write permissions for others;
        # explicitly retain the selected executable mode in the offline input.
        for path in (package / 'assets').iterdir():
            path.chmod(0o500)
        code, report = isolation.run_isolated(package, ['/usr/bin/python3', '-I', '-B', '-S',
                                                     '/work/scripts/check_r02_offline_install.py', '--inside-package'], timeout=600)
        if code or not report.get('passed'):
            raise SystemExit(code or 97)


if __name__ == '__main__':
    main()
