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
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

# Installed Codex 0.154 global options. Value-taking options consume the next
# argument unless written as --name=value; --image additionally consumes
# further non-option values (clap variadic).
VALUE_OPTIONS = {'-c', '--config', '--enable', '--disable', '--remote',
                 '--remote-auth-token-env', '-i', '--image', '-m', '--model',
                 '--local-provider', '-p', '--profile', '-s', '--sandbox',
                 '-C', '--cd', '--add-dir', '-a', '--ask-for-approval'}
FLAG_OPTIONS = {'--strict-config', '--oss', '--approve-for-me',
                '--dangerously-bypass-approvals-and-sandbox',
                '--dangerously-bypass-hook-trust', '--worktree', '--search',
                '--no-alt-screen', '-h', '--help', '-V', '--version'}
# Subcommands that never run through the interactive --remote route: they are
# non-interactive, or administration over the shared daemon. `resume`, `fork`,
# a bare TUI and literal prompts are interactive starts and are served.
DIRECT_COMMANDS = {'exec', 'e', 'review', 'login', 'logout', 'mcp', 'plugin',
                   'app-server', 'remote-control', 'completion', 'update',
                   'doctor', 'sandbox', 'debug', 'apply', 'a', 'queue',
                   'archive', 'delete', 'migrate-rollouts', 'unarchive',
                   'cloud', 'exec-server', 'features', 'help', 'version',
                   'agents'}
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
    help_or_version = any(a in ('-h', '--help', '-V', '--version') for a in head)
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
    parser.add_argument('tui', nargs=argparse.REMAINDER, help='arguments forwarded to codex')
    args = parser.parse_args()
    if not args.codex:
        parser.error('an installed codex is required')
    forwarded = list(args.tui)
    if forwarded and forwarded[0] == '--':
        forwarded = forwarded[1:]
    if classify(forwarded)['direct']:
        os.execv(args.codex, [args.codex, *forwarded])

    runtime = Path(os.environ.get('XDG_RUNTIME_DIR') or f'/tmp/codex-cairn-{os.geteuid()}')
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(runtime, 0o700)
    socket_path = runtime / f'queue-{os.getpid()}.sock'
    if len(str(socket_path)) + 1 > 108:
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
    try:
        server = subprocess.Popen([args.codex, 'app-server', '--listen', 'unix://' + str(socket_path),
                                   *config_options])
        deadline = time.monotonic() + 5
        while not socket_path.exists():
            if server.poll() is not None:
                print('cairn codex launcher: the app-server exited before listening', file=sys.stderr)
                return max(server.returncode, 1)
            if time.monotonic() > deadline:
                print('cairn codex launcher: the app-server socket never appeared', file=sys.stderr)
                return 125
            time.sleep(0.05)
        tui = subprocess.Popen([args.codex, '--remote', 'unix://' + str(socket_path), *forwarded])
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
