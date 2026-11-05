#!/usr/bin/env python3
"""Launch an interactive Codex TUI served by its own local app-server.

Ordinary interactive starts (bare TUI, prompt, resume, fork) run the TUI with
--remote unix://SOCK so the conversation lives in that app-server and Cairn's
native queue wakes reach it without terminal input. Every non-interactive
subcommand, and any invocation carrying an explicit --remote endpoint, execs
the real codex unchanged. Global configuration options are forwarded to both
the serving app-server and the TUI so their effective config matches.
"""
import argparse
import base64
from contextlib import contextmanager, ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import selectors
import re
import socket
import stat
import subprocess
import sys
import time
import uuid

OWNER_CONTRACT = 'codex.owner-input/1'
ATTESTATION_CONTRACT = 'cairn.owner-input-attestation/1'
ATTESTATION_MAGIC = b'CAIRN-OWNER-ATTESTATION/1\n'
OWNER_BASE = '6ff1e1ca3b30fc8b914720681fcf5fe62e95f856'
OWNER_MAGIC = b'CAIRN-OWNER-INPUT/1\n'
OWNER_ENV = ('CAIRN_CODEX_OWNER_ORIGIN_MANIFEST',
             'CAIRN_CODEX_OWNER_ORIGIN_RUNTIME_SHA256',
             'CAIRN_CODEX_OWNER_ORIGIN_CONTRACT',
             'CAIRN_CODEX_OWNER_ORIGIN_LAUNCH_ID')


@contextmanager
def owner_origin_runtime(manifest_path, binary):
    """Verify the selected native artifact before granting a private connection."""
    path = Path(manifest_path)
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022 or info.st_size > 16384):
            raise ValueError('owner-origin manifest is not a bounded owner-controlled file')
        payload = source.read(16385)
        if len(payload) > 16384:
            raise ValueError('owner-origin manifest exceeded its size bound')
        selected = json.loads(payload)
    expected_keys = {'schema', 'binary_path', 'binary_sha256', 'contract', 'version',
                     'upstream_commit', 'patch_sha256', 'attestation_contract', 'code_mode_host'}
    if (not isinstance(selected, dict) or set(selected) != expected_keys
            or selected['schema'] != 'cairn.codex-owner-origin/1'
            or selected['attestation_contract'] != ATTESTATION_CONTRACT
            or selected['contract'] != OWNER_CONTRACT or type(selected['version']) is not int
            or selected['version'] != 1 or selected['upstream_commit'] != OWNER_BASE):
        raise ValueError('unsupported owner-origin manifest contract')
    for field in ('binary_sha256', 'patch_sha256'):
        value = selected[field]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('invalid owner-origin artifact digest')
    native = Path(binary).resolve(strict=True)
    if selected['binary_path'] != str(native):
        raise ValueError('owner-origin manifest names a different native binary')
    companion = selected['code_mode_host']
    if (not isinstance(companion, dict) or set(companion) != {'path', 'sha256'}
            or companion['path'] != str(native.with_name('codex-code-mode-host'))
            or not isinstance(companion['sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', companion['sha256'])):
        raise ValueError('owner-origin manifest names an invalid native companion')
    directory = native.parent.stat()
    if directory.st_uid != os.getuid() or directory.st_mode & 0o022:
        raise ValueError('owner-origin package directory is not owner-controlled')
    helper_fd = os.open(companion['path'], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(helper_fd, 'rb') as helper:
        info = os.fstat(helper.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o022 or not info.st_mode & 0o100 or info.st_size > 1024 ** 3):
            raise ValueError('owner-origin native companion is not a bounded protected executable')
        if hashlib.file_digest(helper, 'sha256').hexdigest() != companion['sha256']:
            raise ValueError('owner-origin native companion changed from its selected digest')
    with native.open('rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 1024 ** 3:
            raise ValueError('owner-origin binary is not a bounded protected file')
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != selected['binary_sha256']:
            raise ValueError('owner-origin binary changed from its selected digest')
        # Keep the checked inode open across probe and both execs. The descriptor
        # stays in this launcher, never in native children or their tools.
        executable = f'/proc/{os.getpid()}/fd/{source.fileno()}'
        capability = json.loads(owner_origin_probe(executable))
        expected = {key: selected[key] for key in ('contract', 'version', 'upstream_commit', 'patch_sha256', 'attestation_contract')}
        expected['schema'] = 'codex.owner-input-capabilities/1'
        if capability != expected:
            raise ValueError('owner-origin native capability does not match the selected artifact')
        yield selected, executable


def native_environment(environment):
    """Operator connection settings belong only to the registration helper."""
    return {key: value for key, value in environment.items()
            if key not in OWNER_ENV and key not in ('CAIRN_DATABASE_URL', 'CAIRN_TEST_DATABASE_URL', 'CAIRN_HOME')
            and not key.startswith(('CAIRN_OPERATOR_', 'PG'))}


def bounded_owner_command(arguments, *, request=None, env=None, timeout=5, output_limit=4096):
    child = subprocess.Popen(arguments, env=env, stdin=subprocess.PIPE if request is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = bytearray()
    deadline = time.monotonic() + timeout
    pending = memoryview(request or b'')
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            selector.register(child.stderr, selectors.EVENT_READ)
            if request is not None:
                if len(request) > 16384:
                    raise ValueError('owner-origin helper request exceeded its size bound')
                os.set_blocking(child.stdin.fileno(), False)
                selector.register(child.stdin, selectors.EVENT_WRITE)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError('owner-origin helper timed out')
                for key, _ in selector.select(remaining):
                    if key.fileobj is child.stdin:
                        pending = pending[os.write(key.fd, pending):]
                        if not pending:
                            selector.unregister(child.stdin)
                            child.stdin.close()
                        continue
                    limit = output_limit + 1 - len(output) if key.fileobj is child.stdout else 1
                    chunk = os.read(key.fd, limit)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.fileobj is child.stderr:
                        raise ValueError('owner-origin helper wrote diagnostics')
                    else:
                        output.extend(chunk)
                        if len(output) > output_limit:
                            raise ValueError('owner-origin helper exceeded its output bound')
        try:
            result = child.wait(timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise ValueError('owner-origin helper timed out') from None
        if result != 0:
            raise ValueError('owner-origin helper failed')
        return bytes(output)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        if child.stdin is not None:
            child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def owner_origin_probe(binary, timeout=5):
    return bounded_owner_command([str(binary), '--cairn-owner-origin-capabilities'],
                                 env=native_environment(os.environ), timeout=timeout)


def owner_origin_host_config(path):
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > 16384:
            raise ValueError('owner-origin host config is not a bounded owner-controlled file')
        payload = source.read(16385)
        if len(payload) > 16384:
            raise ValueError('owner-origin host config exceeded its size bound')
        config = json.loads(payload)
    if (not isinstance(config, dict) or set(config) != {'schema', 'cairn_path', 'repo', 'profile_principal', 'binding'}
            or config['schema'] != 'cairn.codex-owner-origin-host/1'
            or not all(isinstance(config[key], str) and 0 < len(config[key]) <= 4096
                       for key in ('cairn_path', 'repo', 'profile_principal', 'binding'))
            or not Path(config['cairn_path']).is_absolute() or not Path(config['repo']).is_absolute()
            or not re.fullmatch('[A-Za-z0-9_.-]{1,128}', config['binding'])):
        raise ValueError('invalid owner-origin host config')
    return config


def register_owner_launch(channel, selected, config, launch_id, deadline):
    """Only the trusted operator helper can bind a native ephemeral public key."""
    public = bytearray()
    while len(public) < 32:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('owner-input signing handshake timed out')
        channel.settimeout(remaining)
        chunk = channel.recv(32 - len(public))
        if not chunk:
            raise ValueError('owner-input signer closed before public key')
        public.extend(chunk)
    request = dict(request_id=str(uuid.uuid4()), launch_id=launch_id,
                   repo=config['repo'], profile_principal=config['profile_principal'], binding=config['binding'],
                   public_key=base64.urlsafe_b64encode(public).decode().rstrip('='),
                   origin_contract=OWNER_CONTRACT, origin_version=1,
                   upstream_commit=selected['upstream_commit'], patch_sha256=selected['patch_sha256'],
                   runtime_sha256=selected['binary_sha256'])
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('owner-input signing handshake timed out')
    response = json.loads(bounded_owner_command([config['cairn_path'], 'native-owner-launch-register'],
                          request=json.dumps(request).encode(), env=os.environ.copy(), timeout=remaining, output_limit=16384))
    data = response.get('data') if isinstance(response, dict) else None
    if (not isinstance(response, dict) or response.get('schema') != 'cairn.response/1' or response.get('ok') is not True or response.get('status') != 'OK'
            or not isinstance(data, dict)
            or data.get('launch_id') != launch_id or data.get('status') != 'active'
            or type(data.get('restore_generation')) is not int
            or not 0 <= data['restore_generation'] < 2 ** 63):
        raise ValueError('operator refused the owner-input launch binding')
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('owner-input signing handshake timed out')
    channel.settimeout(remaining)
    channel.sendall(data['restore_generation'].to_bytes(8, 'big', signed=True))
    channel.shutdown(socket.SHUT_WR)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError('owner-input signing handshake timed out')
    channel.settimeout(remaining)
    if channel.recv(1):
        raise ValueError('owner-input signer sent an unexpected response')


def owner_proof_descriptor(proof, endpoint):
    endpoint_bytes = os.fsencode(endpoint)
    if len(proof) != 32 or not 0 < len(endpoint_bytes) <= 107 or not endpoint_bytes.startswith(b'/') or b'\0' in endpoint_bytes:
        raise ValueError('invalid owner-ingress endpoint frame')
    reader, writer = socket.socketpair()
    try:
        writer.sendall(OWNER_MAGIC + proof + bytes([len(endpoint_bytes)]) + endpoint_bytes)
    except BaseException:
        reader.close()
        raise
    finally:
        writer.close()
    return reader

# Installed Codex 0.156 global options. Value-taking options consume the next
# argument unless written as --name=value; --image additionally consumes
# further non-option values (clap variadic).
VALUE_OPTIONS = {'-c', '--config', '--enable', '--disable', '--remote',
                 '--remote-auth-token-env', '-i', '--image', '-m', '--model',
                 '--local-provider', '-p', '--profile', '-s', '--sandbox',
                 '-C', '--cd', '--add-dir', '-a', '--ask-for-approval'}
FLAG_OPTIONS = {'--strict-config', '--oss', '--approve-for-me', '--no-daemon',
                '--dangerously-bypass-approvals-and-sandbox',
                '--dangerously-bypass-hook-trust', '--worktree', '--search',
                '--no-alt-screen', '-h', '--help', '-V', '--version',
                '--cairn-owner-origin-capabilities'}
# Subcommands that never run through the interactive --remote route: they are
# non-interactive, or administration over the shared daemon. `resume`, `fork`,
# a bare TUI and literal prompts are interactive starts and are served.
DIRECT_COMMANDS = {'exec', 'e', 'review', 'login', 'logout', 'mcp', 'plugin',
                   'app-server', 'remote-control', 'completion', 'update',
                   'doctor', 'sandbox', 'debug', 'apply', 'a', 'queue',
                   'archive', 'delete', 'migrate-rollouts', 'unarchive',
                   'cloud', 'exec-server', 'features', 'help', 'version',
                   'agents'}
# Codex 0.156 --no-daemon means "run without the shared background server"
# and is refused alongside --remote. The launcher's private app-server is not
# that shared daemon, so the served TUI already honors it; drop it from the
# option head only, never from literal prompt text after --.
SERVED_DROP = {'--no-daemon'}
# Options that change effective configuration loading: the serving app-server
# must receive them exactly like the TUI it serves.
CONFIG_OPTIONS = {'-c', '--config', '--enable', '--disable', '--strict-config',
                  '-p', '--profile'}


def classify(arguments):
    """Classify one codex argv without disturbing literal prompt arguments."""
    # Everything after a bare -- is a literal prompt; only the head counts.
    literal = arguments.index('--') if '--' in arguments else len(arguments)
    head = arguments[:literal]
    explicit_remote = any(a == '--remote' or a.startswith('--remote=') for a in head)
    help_or_version = any(a in ('-h', '--help', '-V', '--version', '--cairn-owner-origin-capabilities') for a in head)
    server_config = []
    command = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == '--':
            break  # Everything after -- is a literal prompt.
        if not argument.startswith('-'):
            command = argument
            break
        name, equals, _ = argument.partition('=')
        if name == '--remote':
            explicit_remote = True
        if argument in VALUE_OPTIONS:
            if name in CONFIG_OPTIONS:
                server_config.append(argument)
            if index + 1 < len(arguments):
                index += 1
                if name in CONFIG_OPTIONS:
                    server_config.append(arguments[index])
                if name in ('-i', '--image'):
                    while index + 1 < len(arguments) and not arguments[index + 1].startswith('-'):
                        index += 1
            index += 1
            continue
        if equals and name in CONFIG_OPTIONS:
            server_config.append(argument)
        index += 1
    direct = explicit_remote or help_or_version or (command is not None and command in DIRECT_COMMANDS)
    return dict(direct=direct, explicit_remote=explicit_remote,
                command=command, server_config=server_config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', default=shutil.which('codex'), help='installed codex executable')
    parser.add_argument('--owner-origin-manifest', type=Path,
                        help='verified native owner-input deployment manifest (optional)')
    parser.add_argument('--owner-origin-host-config', type=Path,
                        help='owner-controlled nonsecret launch registration configuration')
    parser.add_argument('tui', nargs=argparse.REMAINDER, help='arguments forwarded to codex')
    args = parser.parse_args()
    if not args.codex:
        parser.error('an installed codex is required')
    forwarded = list(args.tui)
    if forwarded and forwarded[0] == '--':
        forwarded = forwarded[1:]
    selected_owner_origin = args.owner_origin_manifest is not None or args.owner_origin_host_config is not None
    if (args.owner_origin_manifest is None) != (args.owner_origin_host_config is None):
        parser.error('owner-origin manifest and host config must be selected together')
    if classify(forwarded)['direct']:
        environment = (native_environment(os.environ) if selected_owner_origin else
                       {key: value for key, value in os.environ.items() if key not in OWNER_ENV})
        os.execve(args.codex, [args.codex, *forwarded], environment)

    child_env = {key: value for key, value in os.environ.items() if key not in OWNER_ENV}
    with ExitStack() as resources:
        selected = None
        host_config = None
        launch_id = None
        executable = args.codex
        if args.owner_origin_manifest is not None:
            try:
                host_config = owner_origin_host_config(args.owner_origin_host_config)
                selected, executable = resources.enter_context(
                    owner_origin_runtime(args.owner_origin_manifest, args.codex))
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                parser.error(f'owner-origin runtime refused: {error}')
            launch_id = str(uuid.uuid4())
            child_env = native_environment(child_env)
            child_env.update({OWNER_ENV[0]: str(args.owner_origin_manifest.resolve()),
                              OWNER_ENV[1]: selected['binary_sha256'], OWNER_ENV[2]: OWNER_CONTRACT, OWNER_ENV[3]: launch_id})
        return run_session(args, forwarded, child_env, selected, executable, parser, host_config, launch_id)


def run_session(args, forwarded, child_env, selected, executable, parser, host_config, launch_id):
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/tmp/codex-cairn-{os.geteuid()}')
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime, 0o700)
    socket_path = runtime.resolve() / f'queue-{os.getpid()}.sock'
    if len(os.fsencode(socket_path)) + 1 > 108:
        parser.error('socket path exceeds the Unix limit; set XDG_RUNTIME_DIR to a shorter directory')
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    config_options = classify(forwarded)['server_config']

    def terminated(signum, frame):
        raise SystemExit(128 + signum)

    # Armed before any child exists: termination during the startup wait must
    # still clean up the app-server through the finally path.
    for received in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(received, terminated)
    server = None
    tui = None
    proof = os.urandom(32) if selected is not None else None
    try:
        server_args = [args.codex, 'app-server', '--listen', 'unix://' + str(socket_path), *config_options]
        if proof is None:
            server = subprocess.Popen(server_args, executable=executable, env=child_env)
        else:
            with owner_proof_descriptor(proof, socket_path) as descriptor:
                native_signer, registration = socket.socketpair()
                with native_signer, registration:
                    handshake_deadline = time.monotonic() + 15
                    registration.sendall(ATTESTATION_MAGIC + uuid.UUID(launch_id).bytes + os.urandom(32))
                    server = subprocess.Popen([*server_args, '--cairn-owner-origin-fd', str(descriptor.fileno()),
                                               '--cairn-owner-attestation-fd', str(native_signer.fileno())],
                                              pass_fds=(descriptor.fileno(), native_signer.fileno()), executable=executable, env=child_env)
                    native_signer.close()
                    register_owner_launch(registration, selected, host_config, launch_id, handshake_deadline)
        deadline = time.monotonic() + 5
        while not socket_path.exists():
            if server.poll() is not None:
                print('cairn codex launcher: the app-server exited before listening', file=sys.stderr)
                return max(server.returncode, 1)
            if time.monotonic() > deadline:
                print('cairn codex launcher: the app-server socket never appeared', file=sys.stderr)
                return 125
            time.sleep(0.05)
        literal = forwarded.index('--') if '--' in forwarded else len(forwarded)
        served = [a for a in forwarded[:literal] if a not in SERVED_DROP] + forwarded[literal:]
        tui_args = [args.codex, '--remote', 'unix://' + str(socket_path)]
        if proof is None:
            tui = subprocess.Popen([*tui_args, *served], executable=executable, env=child_env)
        else:
            with owner_proof_descriptor(proof, socket_path) as descriptor:
                tui = subprocess.Popen([*tui_args, '--cairn-owner-origin-fd', str(descriptor.fileno()), *served],
                                       pass_fds=(descriptor.fileno(),), executable=executable, env=child_env)
            proof = None
        while True:
            try:
                return tui.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                if server.poll() is not None:
                    # The serving app-server died under the TUI: end the TUI
                    # and report the abrupt failure instead of hanging.
                    print('cairn codex launcher: the app-server exited while the TUI was running',
                          file=sys.stderr)
                    stop(tui)
                    return max(server.returncode, 1)
    except (OSError, ValueError) as error:
        print(f'cairn codex launcher: owner-input launch failed: {error}', file=sys.stderr)
        return 125
    finally:
        if tui is not None and tui.poll() is None:
            stop(tui)
        if server is not None:
            stop(server)
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass


def stop(child):
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


if __name__ == '__main__':
    sys.exit(main())
