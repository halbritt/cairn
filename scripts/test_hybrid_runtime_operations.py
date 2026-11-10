"""Run campaign tests and inactive operations fixtures inside an isolated namespace."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / 'integrations/lifecycle/operations'
F1_CLOSURES = {
    'collector-code': ('collector.py', 'parsers.py', 'read_process.py'),
    'helper-code': ('metadata_helper.py', 'parsers.py', 'read_process.py'),
}
F3_MODULES = (
    'claims.py', 'domains.py', 'effects.py', 'journal.py',
    'runner.py', 'sequence.py', 'transport.py',
    'backend_binding.py', 'backend_entry.py', 'backend_files.py',
    'backend_process.py', 'backend_proof.py', 'backend_wire.py',
    'remote_executor.py', 'root_launcher.py',
)
SYSTEM_MOUNT_ROOTS = frozenset({
    '/', '/bin', '/dev', '/etc', '/home', '/lib', '/lib64',
    '/opt', '/proc', '/tmp', '/usr', '/usr/bin', '/usr/lib', '/usr/lib64', '/work',
})
EXPECTED_SANDBOX_ROOT_ENTRIES = frozenset({
    'bin', 'dev', 'etc', 'home', 'lib', 'lib64', 'opt', 'proc', 'tmp', 'usr', 'work',
})


def private_copy(source, destination):
    shutil.copyfile(source, destination)
    destination.chmod(0o600)


def stage(suite, destination):
    destination.mkdir(mode=0o700)
    destination.chmod(0o700)
    source = SOURCE / suite
    if suite == 'f1':
        for directory, names in F1_CLOSURES.items():
            origin = source / directory
            if {p.name for p in origin.iterdir()} != set(names):
                raise RuntimeError('unexpected F1 code closure: ' + directory)
            target = destination / directory
            target.mkdir(mode=0o700)
            target.chmod(0o700)
            for name in names:
                private_copy(origin / name, target / name)
        for name in ('test_f1.py', 'executor.py', 'test_executor.py'):
            private_copy(source / name, destination / name)
    else:
        for name in F3_MODULES:
            private_copy(source / name, destination / name)
        (destination / 'tests').mkdir(mode=0o700)
        (destination / 'tests').chmod(0o700)
        for path in sorted((source / 'tests').glob('*.py')):
            private_copy(path, destination / 'tests' / path.name)


def is_under(child, parent):
    parent = os.path.normpath(parent)
    child = os.path.normpath(child)
    if parent == '/':
        return child.startswith('/')
    return child == parent or child.startswith(parent + '/')


def discover_passwd_homes():
    homes = set()
    for entry in pwd.getpwall():
        if entry.pw_dir and entry.pw_dir.startswith('/'):
            homes.add(os.path.normpath(entry.pw_dir))
    passwd_file = Path('/etc/passwd')
    if passwd_file.is_file():
        for line in passwd_file.read_text(encoding='utf-8', errors='replace').splitlines():
            parts = line.split(':')
            if len(parts) >= 6 and parts[5].startswith('/'):
                homes.add(os.path.normpath(parts[5]))
    for raw in list(homes):
        if raw in SYSTEM_MOUNT_ROOTS:
            continue
        try:
            resolved = os.path.normpath(str(Path(raw).resolve()))
            if resolved.startswith('/') and resolved not in SYSTEM_MOUNT_ROOTS:
                homes.add(resolved)
        except OSError:
            pass
    if not homes:
        raise RuntimeError('passwd database returned no home directories')
    return sorted(homes)


def discover_go_root(owner_home):
    go_mod_file = REPO_ROOT / 'go.mod'
    required_version = None
    if go_mod_file.is_file():
        for line in go_mod_file.read_text(encoding='utf-8', errors='replace').splitlines():
            parts = line.strip().split()
            if len(parts) == 2 and parts[0] == 'go':
                required_version = parts[1]
                break
    toolchain_dir = owner_home / 'go/pkg/mod/golang.org'
    if required_version and toolchain_dir.is_dir():
        exact = toolchain_dir / f'toolchain@v0.0.1-go{required_version}.linux-amd64'
        if (exact / 'bin/go').is_file():
            return exact.resolve()
        prefix = '.'.join(required_version.split('.')[:2])
        matches = sorted(
            p for p in toolchain_dir.glob(f'toolchain@*-go{prefix}*.linux-amd64')
            if (p / 'bin/go').is_file()
        )
        if matches:
            return matches[-1].resolve()
    go_bin = shutil.which('go') or str(owner_home / '.local/go/bin/go')
    return Path(go_bin).resolve().parents[1] if Path(go_bin).exists() else owner_home / '.local/go'


def discover_toolchain_binds(account_homes):
    owner_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    candidates = []
    go_root = discover_go_root(owner_home)
    if (go_root / 'bin/go').is_file():
        candidates.append((go_root.resolve(), '/opt/toolchains/go'))
    go_mod = owner_home / 'go/pkg/mod'
    if go_mod.is_dir():
        candidates.append((go_mod.resolve(), '/opt/toolchains/gomodcache'))
    rust_toolchains = owner_home / '.rustup/toolchains'
    if rust_toolchains.is_dir():
        preferred = rust_toolchains / '1.99.1-x86_64-unknown-linux-gnu'
        if not (preferred / 'bin/cargo').is_file():
            matches = sorted(p for p in rust_toolchains.iterdir() if (p / 'bin/cargo').is_file())
            preferred = matches[-1] if matches else preferred
        if (preferred / 'bin/cargo').is_file():
            candidates.append((preferred.resolve(), '/opt/toolchains/rust'))
    for sub in ('registry', 'git'):
        cache = owner_home / '.cargo' / sub
        if cache.is_dir():
            candidates.append((cache.resolve(), '/opt/toolchains/cargo-cache/' + sub))
    home_set = set(account_homes)
    binds = []
    for src, dst in candidates:
        src_str = os.path.normpath(str(src))
        if src_str in home_set:
            raise RuntimeError('refusing to bind account home as toolchain: ' + src_str)
        binds.append((src_str, dst))
    return binds


def _map_subpath(path, src, dst):
    src = os.path.normpath(src)
    dst = os.path.normpath(dst)
    path = os.path.normpath(path)
    if not is_under(path, src):
        return None
    suffix = path[len(src):] if src != '/' else path
    return os.path.normpath(dst + suffix)


def discover_masked_homes(passwd_homes, bind_pairs):
    masked = {'/home'}
    normalized_pairs = []
    for item in bind_pairs:
        src, dst = (item, item) if isinstance(item, str) else item
        normalized_pairs.append((os.path.normpath(src), os.path.normpath(dst)))
        try:
            real_src = os.path.normpath(os.path.realpath(src))
            if (real_src, os.path.normpath(dst)) not in normalized_pairs:
                normalized_pairs.append((real_src, os.path.normpath(dst)))
        except OSError:
            pass
    for home in passwd_homes:
        if home in SYSTEM_MOUNT_ROOTS:
            continue
        home_variants = {os.path.normpath(home)}
        try:
            real_home = os.path.normpath(os.path.realpath(home))
            if real_home not in SYSTEM_MOUNT_ROOTS:
                home_variants.add(real_home)
        except OSError:
            pass
        for h in home_variants:
            for src, dst in normalized_pairs:
                mapped = _map_subpath(h, src, dst)
                if mapped and mapped not in SYSTEM_MOUNT_ROOTS:
                    masked.add(mapped)
            for _, dst in normalized_pairs:
                if is_under(h, dst) and h not in SYSTEM_MOUNT_ROOTS:
                    masked.add(h)
    return sorted(masked)


def discover_mapped_passwd_homes(passwd_homes, bind_pairs, symlink_pairs=()):
    mapped = set(passwd_homes) | set(discover_masked_homes(passwd_homes, bind_pairs))
    for target, link_path in symlink_pairs:
        abs_target = os.path.normpath('/' + target.lstrip('/'))
        for item in list(mapped):
            if item in SYSTEM_MOUNT_ROOTS:
                continue
            alias = _map_subpath(item, abs_target, link_path)
            if alias and alias not in SYSTEM_MOUNT_ROOTS:
                mapped.add(alias)
    return sorted(mapped)


def build_sandbox_spec(source, *, chdir='/work'):
    passwd_homes = discover_passwd_homes()
    toolchain_binds = discover_toolchain_binds(passwd_homes)
    script_path = str(Path(__file__).resolve())
    source_str = str(Path(source).resolve())
    symlink_pairs = []
    extra_sys_binds = []
    for sys_lib in ('/lib', '/lib64'):
        if os.path.islink(sys_lib):
            symlink_pairs.append((os.readlink(sys_lib), sys_lib))
        elif os.path.exists(sys_lib):
            extra_sys_binds.append((sys_lib, sys_lib))
    bind_pairs = [
        ('/usr', '/usr'),
        *extra_sys_binds,
        (source_str, '/work'),
        *toolchain_binds,
    ]
    bound_destinations = [dst for _, dst in bind_pairs]
    masked_homes = discover_masked_homes(passwd_homes, bind_pairs)
    mapped_passwd_homes = discover_mapped_passwd_homes(passwd_homes, bind_pairs, symlink_pairs)
    root_stat = os.stat('/')
    return {
        'source': source_str,
        'chdir': chdir,
        'script_path': script_path,
        'passwd_homes': passwd_homes,
        'mapped_passwd_homes': mapped_passwd_homes,
        'masked_homes': masked_homes,
        'symlink_pairs': symlink_pairs,
        'extra_sys_binds': extra_sys_binds,
        'toolchain_binds': toolchain_binds,
        'bound_destinations': bound_destinations,
        'host_pid_ns': os.readlink('/proc/self/ns/pid'),
        'host_net_ns': os.readlink('/proc/self/ns/net'),
        'host_root_dev_ino': [root_stat.st_dev, root_stat.st_ino],
    }


def namespace_argv(source, command, *, spec=None, report_fd=-1, self_test_only=False):
    if spec is None:
        spec = build_sandbox_spec(source)
    argv = [
        '/usr/bin/bwrap', '--unshare-all', '--die-with-parent',
        '--ro-bind', '/usr', '/usr',
    ]
    for target, link_path in spec.get('symlink_pairs', ()):
        argv.extend(['--symlink', target, link_path])
    for src, dst in spec.get('extra_sys_binds', ()):
        argv.extend(['--ro-bind', src, dst])
    argv.extend([
        '--dir', '/bin',
        '--ro-bind', '/usr/bin/sh', '/bin/sh',
        '--ro-bind', '/usr/bin/env', '/bin/env',
        '--chmod', '0111', '/bin',
        '--dir', '/etc',
        '--ro-bind', '/etc/passwd', '/etc/passwd',
        '--chmod', '0111', '/etc',
    ])
    for src, dst in spec['toolchain_binds']:
        argv.extend(['--ro-bind', src, dst])
    argv.extend([
        '--ro-bind', spec['script_path'], '/opt/toolchains/isolation_runner.py',
        '--ro-bind', spec['source'], '/work',
    ])
    for masked in spec['masked_homes']:
        argv.extend(['--tmpfs', masked, '--chmod', '0000', masked])
    path_parts = ['/usr/bin', '/bin']
    for _, dst in spec['toolchain_binds']:
        if dst in ('/opt/toolchains/go', '/opt/toolchains/rust'):
            path_parts.insert(0, dst + '/bin')
    argv.extend([
        '--tmpfs', '/tmp',
        '--dev', '/dev', '--chmod', '0111', '/dev',
        '--proc', '/proc',
        '--clearenv',
        '--setenv', 'PATH', ':'.join(path_parts),
        '--chdir', spec['chdir'],
        '/usr/bin/python3', '-I', '-B', '/opt/toolchains/isolation_runner.py',
        '--internal-sandbox-entry', json.dumps(spec, sort_keys=True),
        '--report-fd', str(report_fd),
    ])
    if self_test_only:
        argv.append('--self-test-only')
    argv.extend(['--', *command])
    return argv


def scan_bound_tree_for_sockets(roots, masked_set):
    sockets = []
    errors = []
    scanned = 0
    visited_real = set()

    def walk(directory):
        nonlocal scanned
        if directory in masked_set:
            return
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    scanned += 1
                    path = entry.path
                    if path in masked_set:
                        continue
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            walk(path)
                        elif not entry.is_file(follow_symlinks=False):
                            mode = entry.stat(follow_symlinks=False).st_mode
                            if stat.S_ISSOCK(mode):
                                sockets.append(path)
                    except OSError as exc:
                        errors.append(f'{path}: {exc}')
        except OSError as exc:
            errors.append(f'{directory}: {exc}')

    for root in roots:
        if root in masked_set:
            continue
        try:
            st = os.lstat(root)
            if stat.S_ISSOCK(st.st_mode):
                sockets.append(root)
                continue
            if stat.S_ISLNK(st.st_mode):
                continue
            real = os.path.realpath(root)
            if real in visited_real:
                continue
            visited_real.add(real)
            if stat.S_ISDIR(st.st_mode):
                walk(root)
        except OSError as exc:
            errors.append(f'{root}: {exc}')
    return scanned, sockets, errors


def run_sandbox_self_test(spec):
    failures = []
    masked_set = set(spec['masked_homes'])
    sandbox_passwd = [
        os.path.normpath(e.pw_dir)
        for e in pwd.getpwall()
        if e.pw_dir and e.pw_dir.startswith('/')
    ]
    all_homes = sorted(
        set(spec['passwd_homes'])
        | set(spec.get('mapped_passwd_homes', ()))
        | set(sandbox_passwd)
    )
    if not all_homes:
        failures.append('no passwd homes discovered')

    root_stat = os.stat('/')
    root_isolated = (
        [root_stat.st_dev, root_stat.st_ino] != spec['host_root_dev_ino']
        and set(os.listdir('/')) <= EXPECTED_SANDBOX_ROOT_ENTRIES
    )
    if not root_isolated:
        failures.append('sandbox root / is not an isolated mount table')

    readable_homes = []
    for home in all_homes:
        if home == '/':
            continue
        is_readable = os.access(home, os.R_OK) or os.access(home, os.W_OK)
        listable = False
        try:
            os.listdir(home)
            listable = True
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            pass
        except OSError:
            listable = True
        if is_readable or listable:
            readable_homes.append(home)
    for masked in spec['masked_homes']:
        try:
            mode = stat.S_IMODE(os.lstat(masked).st_mode)
            if mode != 0 or os.access(masked, os.R_OK):
                readable_homes.append(masked)
        except OSError as exc:
            failures.append(f'masked home check failed for {masked}: {exc}')
    if readable_homes:
        failures.append(f'readable passwd homes: {sorted(set(readable_homes))}')

    bound_files = ['/bin/sh', '/bin/env', '/etc/passwd', '/opt/toolchains/isolation_runner.py']
    scanned, visible_sockets, scan_errors = scan_bound_tree_for_sockets(
        [*spec['bound_destinations'], *bound_files], masked_set,
    )
    unix_lines = Path('/proc/net/unix').read_text(encoding='utf-8', errors='replace').strip().splitlines()
    unix_entries = max(0, len(unix_lines) - 1)
    if visible_sockets or scan_errors or unix_entries != 0:
        failures.append(
            f'sockets={visible_sockets} scan_errors={scan_errors} unix_entries={unix_entries}'
        )

    ipv4_lines = Path('/proc/net/route').read_text(encoding='utf-8', errors='replace').strip().splitlines()
    ipv4_routes = max(0, len(ipv4_lines) - 1)
    ipv6_lines = Path('/proc/net/ipv6_route').read_text(encoding='utf-8', errors='replace').strip().splitlines()
    ipv6_non_lo = [line for line in ipv6_lines if line.split() and line.split()[-1] != 'lo']
    ifaces = [name for _, name in socket.if_nameindex()]
    net_ns = os.readlink('/proc/self/ns/net')
    if ipv4_routes != 0 or ipv6_non_lo or ifaces != ['lo'] or net_ns == spec['host_net_ns']:
        failures.append(
            f'network route/ns leak: ipv4={ipv4_routes} ipv6_non_lo={len(ipv6_non_lo)} ifaces={ifaces}'
        )

    pid_ns = os.readlink('/proc/self/ns/pid')
    visible_pids = sorted(int(e) for e in os.listdir('/proc') if e.isdigit())
    expected_pids = sorted({1, os.getpid()})
    if pid_ns == spec['host_pid_ns'] or visible_pids != expected_pids:
        failures.append(f'proc leak: pid_ns={pid_ns} visible_pids={visible_pids}')

    ro_checks = {}
    for dst in ('/usr', '/work', *(d for _, d in spec['toolchain_binds'])):
        ro_checks[dst] = bool(os.statvfs(dst).f_flag & os.ST_RDONLY)
        if not ro_checks[dst]:
            failures.append(f'mount not read-only: {dst}')

    go_version = None
    bound_dsts = {d for _, d in spec['toolchain_binds']}
    if '/opt/toolchains/go' in bound_dsts:
        try:
            ver_res = subprocess.run(
                ['/opt/toolchains/go/bin/go', 'version'],
                capture_output=True, text=True, timeout=10, check=True,
                env={'PATH': '/opt/toolchains/go/bin:/usr/bin:/bin', 'GOROOT': '/opt/toolchains/go', 'GOTOOLCHAIN': 'local', 'GOENV': 'off'},
            )
            go_version = ver_res.stdout.strip()
        except Exception as exc:
            failures.append(f'go toolchain check failed: {exc}')

    return {
        'passed': len(failures) == 0,
        'failures': failures,
        'passwd_homes_checked': len(all_homes),
        'readable_passwd_homes': sorted(set(readable_homes)),
        'masked_homes': spec['masked_homes'],
        'root_isolated': root_isolated,
        'bound_destinations': spec['bound_destinations'],
        'scanned_entries': scanned,
        'visible_sockets': visible_sockets,
        'scan_errors': scan_errors,
        'unix_table_entries': unix_entries,
        'ipv4_routes': ipv4_routes,
        'ipv6_non_lo_routes': len(ipv6_non_lo),
        'network_interfaces': ifaces,
        'net_namespace': net_ns,
        'pid_namespace': pid_ns,
        'visible_pids': visible_pids,
        'read_only_mounts': ro_checks,
        'go_version': go_version,
    }


def prepare_sandbox_env(spec):
    for name in (
        'home', 'work-tmp', 'go-cache', 'gopath', 'cargo-home', 'cargo-target',
        'xdg-cache', 'xdg-config', 'xdg-data', 'xdg-state',
    ):
        directory = Path('/tmp') / name
        directory.mkdir(mode=0o700, exist_ok=True)
        directory.chmod(0o700)
    bound_dsts = {dst for _, dst in spec['toolchain_binds']}
    for sub in ('registry', 'git'):
        src = Path('/opt/toolchains/cargo-cache') / sub
        dst = Path('/tmp/cargo-home') / sub
        if str(src) in bound_dsts and not dst.exists():
            dst.symlink_to(src)
    env = {
        'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
        'HOME': '/tmp/home',
        'TMPDIR': '/tmp/work-tmp',
        'GOCACHE': '/tmp/go-cache',
        'GOPATH': '/tmp/gopath',
        'GOTOOLCHAIN': 'local',
        'GOENV': 'off',
        'GOPROXY': 'off',
        'CARGO_HOME': '/tmp/cargo-home',
        'CARGO_TARGET_DIR': '/tmp/cargo-target',
        'CARGO_NET_OFFLINE': 'true',
        'XDG_CACHE_HOME': '/tmp/xdg-cache',
        'XDG_CONFIG_HOME': '/tmp/xdg-config',
        'XDG_DATA_HOME': '/tmp/xdg-data',
        'XDG_STATE_HOME': '/tmp/xdg-state',
        'HF_HUB_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1',
    }
    if '/opt/toolchains/go' in bound_dsts:
        env['GOROOT'] = '/opt/toolchains/go'
    if '/opt/toolchains/gomodcache' in bound_dsts:
        env['GOMODCACHE'] = '/opt/toolchains/gomodcache'
    if '/opt/toolchains/rust' in bound_dsts:
        env['RUSTC'] = '/opt/toolchains/rust/bin/rustc'
    return env


def normalize_sandbox_command(command):
    if len(command) == 1 and ' ' in command[0] and shutil.which(command[0]) is None:
        command = shlex.split(command[0])
    if command[0].startswith('-'):
        command = ['/usr/bin/python3', '-I', '-B', *command]
    if os.path.basename(command[0]).startswith('python') and '-I' in command and '-m' in command:
        m_idx = command.index('-m')
        if m_idx + 1 < len(command) and command[m_idx + 1] == 'unittest':
            rest = command[m_idx + 2:]
            if not rest or rest[0] != 'discover':
                prefix = command[:m_idx]
                return [
                    *prefix, '-c',
                    'import os,sys,unittest; sys.path.insert(0, os.getcwd()); unittest.main(module=None, argv=["unittest", *sys.argv[1:]])',
                    *rest,
                ]
    return command


def sandbox_entry(spec_json, report_fd, self_test_only, command):
    spec = json.loads(spec_json)
    report = run_sandbox_self_test(spec)
    if report_fd >= 0:
        with os.fdopen(report_fd, 'wb') as stream:
            stream.write(json.dumps(report, sort_keys=True).encode('utf-8'))
    if not report['passed']:
        sys.stderr.write('ISOLATION_SELF_TEST_FAILED: ' + json.dumps(report, sort_keys=True) + '\n')
        return 97
    if self_test_only:
        sys.stdout.write(json.dumps(report, indent=2, sort_keys=True) + '\n')
        return 0
    if not command:
        sys.stderr.write('ISOLATION_COMMAND_REQUIRED\n')
        return 2
    command = normalize_sandbox_command(command)
    env = prepare_sandbox_env(spec)
    completed = subprocess.run(command, env=env, cwd=spec['chdir'])
    return completed.returncode


def run_isolated(source, command, *, chdir='/work', timeout=None, self_test_only=False):
    spec = build_sandbox_spec(source, chdir=chdir)
    r_fd, w_fd = os.pipe()
    raw_report = b''
    try:
        argv = namespace_argv(
            source, command, spec=spec, report_fd=w_fd, self_test_only=self_test_only,
        )
        proc = subprocess.Popen(
            argv, env={'PATH': '/usr/bin:/bin'}, pass_fds=(w_fd,),
        )
        os.close(w_fd)
        w_fd = -1
        with os.fdopen(r_fd, 'rb') as reader:
            r_fd = -1
            raw_report = reader.read(1048576)
        returncode = proc.wait(timeout=timeout)
    finally:
        if w_fd >= 0:
            os.close(w_fd)
        if r_fd >= 0:
            os.close(r_fd)
    report = json.loads(raw_report.decode('utf-8')) if raw_report else {'passed': False, 'failures': ['missing self-test report']}
    if not report.get('passed') and returncode == 0:
        returncode = 97
    return returncode, report


def write_receipt(path, mode, runs):
    head = None
    clean = None
    try:
        res = subprocess.run(
            ['git', 'rev-parse', 'HEAD'], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        head = res.stdout.strip()
        st = subprocess.run(
            ['git', 'status', '--porcelain'], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        clean = st.stdout.strip() == ''
    except Exception:
        pass
    script_sha256 = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()
    payload = {
        'schema': 'cairn.hybrid-runtime.isolation-receipt/1',
        'at': datetime.now(timezone.utc).isoformat(),
        'mode': mode,
        'repo': {'path': str(REPO_ROOT), 'head': head, 'clean': clean},
        'script_sha256': script_sha256,
        'passed': all(r['returncode'] == 0 and r['self_test'].get('passed') for r in runs),
        'runs': runs,
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == '--internal-sandbox-entry':
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument('--internal-sandbox-entry', required=True)
        parser.add_argument('--report-fd', type=int, default=-1)
        parser.add_argument('--self-test-only', action='store_true')
        parser.add_argument('command', nargs=argparse.REMAINDER)
        ns = parser.parse_args(argv)
        cmd = ns.command[1:] if ns.command and ns.command[0] == '--' else ns.command
        return sandbox_entry(ns.internal_sandbox_entry, ns.report_fd, ns.self_test_only, cmd)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', help='Write JSON isolation and self-test receipt to PATH')
    parser.add_argument('--cwd', default='/work', help='Working directory inside /work')
    parser.add_argument('--timeout', type=int, default=None, help='Timeout in seconds')
    parser.add_argument('--self-test-only', action='store_true', help='Run only the in-sandbox self-test')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='Command to run inside isolation')
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command and args.command[0] == '--' else args.command
    chdir = args.cwd if args.cwd.startswith('/') else '/work/' + args.cwd

    if args.self_test_only:
        rc, report = run_isolated(REPO_ROOT, [], chdir=chdir, timeout=args.timeout or 60, self_test_only=True)
        if args.receipt:
            write_receipt(args.receipt, 'self-test-only', [{'command': [], 'returncode': rc, 'self_test': report}])
        return rc

    if command:
        rc, report = run_isolated(REPO_ROOT, command, chdir=chdir, timeout=args.timeout)
        if args.receipt:
            write_receipt(args.receipt, 'command', [{'command': command, 'returncode': rc, 'self_test': report}])
        return rc

    commands = {
        'f1': ['-m', 'unittest', 'discover', '-s', '/work', '-p', 'test_*.py', '-v'],
        'f3': ['-m', 'unittest', 'discover', '-s', '/work/tests', '-v'],
    }
    runs = []
    with tempfile.TemporaryDirectory(prefix='cairn-operations-tests-') as temporary:
        root = Path(temporary)
        root.chmod(0o700)
        for suite, suite_cmd in commands.items():
            source = root / suite
            stage(suite, source)
            print('Running inactive operations ' + suite + ' fixtures', flush=True)
            rc, report = run_isolated(source, suite_cmd, timeout=args.timeout or 60)
            runs.append({'suite': suite, 'command': suite_cmd, 'returncode': rc, 'self_test': report})
            if rc:
                if args.receipt:
                    write_receipt(args.receipt, 'operations', runs)
                return rc
    if args.receipt:
        write_receipt(args.receipt, 'operations', runs)
    return 0


class HybridRuntimeIsolationTests(unittest.TestCase):
    def test_discover_masked_homes_masks_passwd_homes_under_bound_paths(self):
        homes = ['/root', '/home/alice', '/usr/share/ollama', '/usr/sbin', '/var/lib/postgresql', '/']
        masked = discover_masked_homes(homes, [('/usr', '/usr'), ('/repo', '/work')])
        self.assertEqual(masked, ['/home', '/usr/sbin', '/usr/share/ollama'])

    def test_discover_masked_homes_maps_aliased_bind_and_symlink_paths(self):
        homes = ['/usr/lib/dhcpcd', '/usr/share/ollama']
        bind_pairs = [('/usr', '/usr'), ('/usr/lib', '/lib')]
        masked = discover_masked_homes(homes, bind_pairs)
        self.assertEqual(masked, ['/home', '/lib/dhcpcd', '/usr/lib/dhcpcd', '/usr/share/ollama'])
        mapped = discover_mapped_passwd_homes(homes, [('/usr', '/usr')], [('usr/lib', '/lib')])
        self.assertIn('/lib/dhcpcd', mapped)
        self.assertIn('/usr/lib/dhcpcd', mapped)

    def test_scan_bound_tree_detects_unix_socket(self):
        with tempfile.TemporaryDirectory() as tmp:
            sock_path = Path(tmp) / 'live.sock'
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                srv.bind(str(sock_path))
                scanned, sockets, errors = scan_bound_tree_for_sockets([tmp], set())
                self.assertGreater(scanned, 0)
                self.assertEqual(sockets, [str(sock_path)])
                self.assertEqual(errors, [])
            finally:
                srv.close()

    def _passing_self_test_patches(self, stack, *, unix_text='Num RefCount\n',
                                   route_text='Iface\tDestination\n',
                                   ipv6_text='00000000000000000000000000000001 80 00000000000000000000000000000000 00 00000000000000000000000000000000 00000000 00000001 00000000 00000001 lo\n',
                                   proc_entries=None, readable_home=False, visible_sockets=None):
        if proc_entries is None:
            proc_entries = ['1', str(os.getpid()), 'net', 'self']
        if visible_sockets is None:
            visible_sockets = []
        spec = {
            'passwd_homes': ['/home/user', '/usr/share/ollama'],
            'masked_homes': ['/home', '/usr/share/ollama'],
            'bound_destinations': ['/usr', '/work'],
            'toolchain_binds': [('/host/go', '/opt/toolchains/go')],
            'host_pid_ns': 'pid:[100]',
            'host_net_ns': 'net:[100]',
            'host_root_dev_ino': [999, 999],
        }
        stack.enter_context(mock.patch.object(pwd, 'getpwall', return_value=[]))
        stack.enter_context(mock.patch.object(os, 'stat', return_value=os.stat_result((0o40755, 1, 2, 1, 0, 0, 0, 0, 0, 0))))
        stack.enter_context(mock.patch.object(os, 'lstat', return_value=os.stat_result((0o40000, 1, 2, 1, 0, 0, 0, 0, 0, 0))))
        stack.enter_context(mock.patch.object(os, 'access', return_value=readable_home))

        def fake_listdir(path):
            if path == '/':
                return ['usr', 'work', 'tmp', 'proc', 'dev', 'home', 'bin', 'etc', 'lib', 'lib64', 'opt']
            if path == '/proc':
                return proc_entries
            raise PermissionError('masked')

        stack.enter_context(mock.patch.object(os, 'listdir', side_effect=fake_listdir))
        stack.enter_context(mock.patch(f'{__name__}.scan_bound_tree_for_sockets', return_value=(10, list(visible_sockets), [])))

        def fake_read_text(path_obj, *args, **kwargs):
            s = str(path_obj)
            if s == '/proc/net/unix':
                return unix_text
            if s == '/proc/net/route':
                return route_text
            if s == '/proc/net/ipv6_route':
                return ipv6_text
            raise FileNotFoundError(s)

        stack.enter_context(mock.patch.object(Path, 'read_text', fake_read_text))
        stack.enter_context(mock.patch.object(socket, 'if_nameindex', return_value=[(1, 'lo')]))
        stack.enter_context(mock.patch.object(os, 'readlink', side_effect=lambda p: 'pid:[200]' if 'pid' in p else 'net:[200]'))
        stack.enter_context(mock.patch.object(os, 'statvfs', return_value=mock.Mock(f_flag=os.ST_RDONLY)))
        stack.enter_context(mock.patch.object(subprocess, 'run', return_value=mock.Mock(stdout='go version go1.25.0 linux/amd64\n')))
        return spec

    def test_self_test_passes_when_all_four_conditions_hold(self):
        with mock.patch.dict(os.environ, {}, clear=False), contextlib_exit_stack() as stack:
            spec = self._passing_self_test_patches(stack)
            report = run_sandbox_self_test(spec)
            self.assertTrue(report['passed'], report)

    def test_self_test_fails_on_each_violation(self):
        cases = [
            {'readable_home': True},
            {'visible_sockets': ['/usr/share/ollama/live.sock']},
            {'route_text': 'Iface\tDestination\neth0\t00000000\n'},
            {'proc_entries': ['1', str(os.getpid()), '9999']},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), contextlib_exit_stack() as stack:
                spec = self._passing_self_test_patches(stack, **kwargs)
                report = run_sandbox_self_test(spec)
                self.assertFalse(report['passed'], report)


def contextlib_exit_stack():
    import contextlib
    return contextlib.ExitStack()


if __name__ == '__main__':
    raise SystemExit(main())
