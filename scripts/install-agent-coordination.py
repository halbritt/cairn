#!/usr/bin/env python3
"""Install native session presence hooks and one host liveness watcher."""
import argparse
import importlib.util
import json
import os
import queue
import re
import threading
import time
import tempfile
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('coordination', ROOT / 'integrations/lifecycle/coordination.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def backup(path):
    target = path.with_name(path.name + '.before-cairn-coordination')
    if path.exists() and not target.exists():
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)


def install(root, settings, config):
    config = dict(config, state_dir=str(root / 'state' / config['binding']))
    engine.validate_config(config)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    bindings = root / 'bindings'
    bindings.mkdir(exist_ok=True, mode=0o700)
    config_path = bindings / (config['binding'] + '.json')
    script = root / 'coordination.py'
    engine.write_state(config_path, config)
    engine.load_config(config_path)
    shutil.copyfile(ROOT / 'integrations/lifecycle/codex_queue.py', root / 'codex_queue.py')
    shutil.copyfile(ROOT / 'integrations/lifecycle/claude_channel.py', root / 'claude_channel.py')
    shutil.copyfile(ROOT / 'integrations/lifecycle/opencode_queue.py', root / 'opencode_queue.py')
    shutil.copyfile(ROOT / 'integrations/lifecycle/hermes_queue.py', root / 'hermes_queue.py')
    shutil.copyfile(ROOT / 'integrations/lifecycle/coordination.py', script)
    script.chmod(0o700)
    command = [sys.executable, str(script), 'hook', '--config', str(config_path)]
    harness = config['harness']
    if harness in ('codex', 'claude'):
        data = json.loads(settings.read_text()) if settings.exists() else {}
        hooks = data.setdefault('hooks', {})
        # Codex also reports Interrupt: a natively interrupted turn is actually
        # idle for presence, while its tools may still be running.
        events = ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd')
        if harness == 'codex':
            events = ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd', 'Interrupt')
        for event in events:
            text = shlex.join(command)
            groups = hooks.setdefault(event, [])
            for group in groups:
                group['hooks'] = [h for h in group['hooks'] if h.get('command') != text]
            groups[:] = [group for group in groups if group['hooks']]
            groups.append({'hooks': [{'type': 'command', 'command': text, 'timeout': 2 if event in ('SessionEnd', 'Interrupt') else 15}]})
        backup(settings)
        engine.write_state(settings, data)
    elif harness == 'agy':
        data = json.loads(settings.read_text()) if settings.exists() else {}
        data['cairn-coordination'] = {event: [dict(type='command', command=shlex.join([*command, '--event', event]), timeout=15)]
                                      for event in ('PreInvocation', 'Stop')}
        backup(settings)
        engine.write_state(settings, data)
    elif harness == 'opencode':
        plugins = settings / 'plugins'
        plugins.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'integrations/opencode/coordination.ts', plugins / 'cairn-coordination.ts')
        engine.write_state(settings / 'cairn-coordination.json', dict(python=sys.executable, script=str(script), config=str(config_path)))
    elif harness == 'hermes':
        import yaml
        path = settings / 'config.yaml'
        data = yaml.safe_load(path.read_text()) if path.exists() else {}
        data = data or {}
        plugin = settings / 'plugins/cairn-coordination'
        plugin.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(ROOT / 'integrations/hermes/coordination.py', plugin / '__init__.py')
        engine.write_state(plugin / 'plugin.yaml', dict(name='cairn-coordination', version='1.0', description='Native Cairn session presence'))
        enabled = data.setdefault('plugins', {}).setdefault('enabled', [])
        if 'cairn-coordination' not in enabled:
            enabled.append('cairn-coordination')
        engine.write_state(settings / 'cairn-coordination.json', dict(script=str(script), config=str(config_path)))
        backup(path)
        engine.write_state(path, data)  # JSON is valid YAML; preserve unrelated values.
    else:
        raise ValueError('unsupported harness')
    return config_path


def trust_codex_hooks(config_home, settings, command, verify=None, codex_binary=None):
    """Use the installed native protocol to trust only the reviewed Cairn commands."""
    codex = codex_binary or shutil.which('codex')
    if not codex:
        raise RuntimeError('Codex is required to register its hook definitions')
    messages = queue.Queue()
    with tempfile.TemporaryDirectory(prefix='cairn-hook-review-') as cwd:
        process = subprocess.Popen([codex, 'app-server', '--stdio'], cwd=cwd,
            env=dict(os.environ, CODEX_HOME=str(config_home)), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        def receive():
            try:
                for line in process.stdout:
                    messages.put(json.loads(line))
            except ValueError:
                messages.put({'transport_error': 'invalid JSON'})
            finally:
                messages.put({'transport_error': 'app server closed'})
        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        sequence = 0
        def rpc(method, params):
            nonlocal sequence
            sequence += 1
            process.stdin.write(json.dumps(dict(id=sequence, method=method, params=params)) + '\n')
            process.stdin.flush()
            deadline = time.monotonic() + 15
            while True:
                value = messages.get(timeout=max(0, deadline - time.monotonic()))
                if 'transport_error' in value:
                    raise RuntimeError(value['transport_error'])
                if value.get('id') != sequence:
                    continue
                if 'error' in value:
                    raise RuntimeError(f"Codex {method} refused: {value['error']}")
                return value['result']
        try:
            rpc('initialize', dict(clientInfo=dict(name='cairn-hook-installer', version='1'), capabilities=dict(experimentalApi=True)))
            process.stdin.write(json.dumps(dict(method='initialized')) + '\n')
            process.stdin.flush()
            response = rpc('hooks/list', dict(cwds=[cwd]))
            hooks = [h for entry in response['data'] for h in entry['hooks']
                     if h['sourcePath'] == str(settings) and h.get('command') == command]
            if len(hooks) != 5:
                raise RuntimeError('Codex did not load all five Cairn hook definitions')
            for hook in hooks:
                rpc('config/value/write', dict(filePath=str(config_home / 'config.toml'),
                    keyPath='hooks.state.' + json.dumps(hook['key']) + '.trusted_hash',
                    value=hook['currentHash'], mergeStrategy='replace'))
            verified = rpc('hooks/list', dict(cwds=[cwd]))
            selected = [h for entry in verified['data'] for h in entry['hooks'] if h['key'] in {h['key'] for h in hooks}]
            if len(selected) != 5 or any(h['trustStatus'] != 'trusted' or not h['enabled'] for h in selected):
                raise RuntimeError('Codex did not confirm the five reviewed Cairn hooks are enabled')
            if verify is not None:
                verify(rpc)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            reader.join(timeout=1)


# Idle wakeup matches a native conversation through Herdr's own integration in
# the selected account home. Codex is absent: watcher matching identifies it by
# its unique open rollout file and needs no Herdr hooks.
HERDR_WAKE_INTEGRATIONS = {
    'claude': ('claude', 'CLAUDE_CONFIG_DIR'),
    'agy': ('antigravity-cli', 'ANTIGRAVITY_CLI_CONFIG_DIR'),
    'hermes': ('hermes', 'HERMES_HOME'),
    'opencode': ('opencode', None),  # Herdr only reads ~/.config/opencode.
}


def herdr_integration(herdr, environment, target):
    result = subprocess.run([herdr, 'integration', 'status'], env=environment,
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError(f'herdr integration status failed: {result.stderr.strip()}')
    for line in result.stdout.splitlines():
        label, _, rest = line.partition(': ')
        if label != target:
            continue
        state = rest.split(' (', 1)[0]
        found = re.search(r'\(([^()]+)\)\s*$', rest)
        if not found:
            break
        return state, Path(found.group(1))
    raise RuntimeError(f'herdr did not report its {target} integration')


def ensure_herdr_integration(herdr, harness, settings):
    """Cover the selected account home with Herdr's installed native integration.

    Status and installation go through the installed Herdr CLI so Herdr keeps
    owning its private assets and its merge preserves unrelated hooks/config.
    """
    mapped = HERDR_WAKE_INTEGRATIONS.get(harness)
    if mapped is None:
        return None  # Codex rollout-file matching needs no Herdr hooks.
    target, variable = mapped
    config_home = settings if harness in ('opencode', 'hermes') else settings.parent
    environment = {k: v for k, v in os.environ.items() if not k.startswith('HERDR_')}
    if variable:
        environment[variable] = str(config_home)
    manual = shlex.join((['env', f'{variable}={config_home}'] if variable else []) +
                        [str(herdr), 'integration', 'install', target])
    state = None
    for attempt in (1, 2):
        state, path = herdr_integration(herdr, environment, target)
        if harness == 'opencode' and path.parent.parent != settings:
            raise RuntimeError(f'Herdr only reads its OpenCode integration from '
                               f'{path.parent.parent}; --settings {settings} cannot be woken. '
                               f'Install with --settings {path.parent.parent}')
        if config_home not in path.parents:
            raise RuntimeError(f'herdr reported its {target} integration at {path}, '
                               f'outside the selected account home {config_home}')
        if state == 'current':
            return target
        if attempt == 2:
            break
        result = subprocess.run([herdr, 'integration', 'install', target], env=environment,
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f'herdr integration install {target} failed: {result.stderr.strip()}; '
                               f'repair manually with: {manual}')
    raise RuntimeError(f'herdr {target} integration is "{state}" after installation; '
                       f'repair manually with: {manual}')


SHIM_MARKER = '# Cairn coordinated Codex launcher (managed by install-agent-coordination.py).'


def resolve_real_codex(shim_path, codex=None):
    """Find the native codex executable the shim must exec.

    An explicit argument wins. Otherwise the PATH result is used unless it is
    the shim entry itself: a managed shim contributes its recorded REAL, and
    a foreign entry — including a symlink to the real installation —
    contributes its fully resolved target. Entries compare before resolution
    so a symlink at the shim location never masquerades as the native binary
    and is never written through.
    """
    found = codex or shutil.which('codex')
    if not found:
        return None
    if Path(found) != Path(shim_path):
        return str(Path(found).resolve())
    entry = Path(shim_path)
    if entry.is_symlink():
        return os.path.realpath(entry)
    if entry.is_file():
        for line in entry.read_text().splitlines():
            if line.startswith('REAL='):
                return line.split('=', 1)[1].strip('"')
    return None


def install_codex_shim(root, codex=None):
    """Make the coordinated launcher the ordinary `codex` launch route.

    Interactive TUI sessions (including resume) run on a per-session
    app-server through the launcher; every non-interactive subcommand execs
    the real codex unchanged. A foreign entry at the shim location — file or
    symlink — is moved aside whole, so the native installation's bytes are
    never written through, and the shim entry itself is replaced atomically.
    """
    shim_dir = Path.home() / '.local' / 'bin'
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim = shim_dir / 'codex'
    real = resolve_real_codex(shim, codex)
    if not real or Path(real) == shim or not Path(real).is_file():
        raise RuntimeError('could not resolve the real codex executable for the launcher shim; '
                           'pass an explicit path with --codex')
    # All routing (non-interactive subcommands, explicit --remote endpoints,
    # and interactive starts) is classified by the launcher itself, keeping
    # the shim a trivial exec that never rewrites arguments.
    body = '\n'.join(['#!/bin/sh', SHIM_MARKER, f'REAL="{real}"',
                      f'LAUNCHER="{root / "launch-codex-coordination.py"}"',
                      f'PYTHON="{sys.executable}"',
                      'exec "$PYTHON" "$LAUNCHER" --codex "$REAL" -- "$@"', ''])
    managed = shim.is_file() and not shim.is_symlink() and SHIM_MARKER in shim.read_text()
    if (shim.exists() or shim.is_symlink()) and not managed:
        # Move the foreign entry aside whole: a symlink is preserved as a
        # symlink and its target's bytes stay untouched.
        os.replace(shim, shim.with_name(shim.name + '.before-cairn-coordination'))
    temp = shim_dir / ('.codex-shim-' + str(os.getpid()))
    temp.write_text(body)
    temp.chmod(0o700)
    os.replace(temp, shim)
    effective = shutil.which('codex')
    if effective and Path(effective) == shim:
        print(f'Installed the coordinated codex launcher as the ordinary route: {shim}.')
    else:
        print(f'Installed the coordinated codex launcher at {shim}, but the current PATH resolves '
              f'codex to {effective}. Put {shim_dir} earlier in PATH (e.g. export PATH="{shim_dir}:$PATH") '
              'so ordinary launches take the coordinated route.')


def register_claude_channel(cairn, directory, config_home):
    """Register the channel MCP server in the account home, honestly.

    Registration does not activate anything by itself: the observed Claude
    2.1.273 enables custom channels only through its launcher flag, so the
    exact activation line is reported instead of being assumed.
    """
    path = config_home / '.claude.json'
    data = json.loads(path.read_text()) if path.exists() else {}
    servers = data.setdefault('mcpServers', {})
    servers['cairn-events'] = {'command': str(cairn),
                               'args': ['claude-channel', '--directory', str(directory)]}
    backup(path)
    engine.write_state(path, data)
    print(f'Registered the cairn-events channel MCP server in {path}.')
    print('Activation requires launching Claude with '
          '--dangerously-load-development-channels=server:cairn-events (the = form: the flag is variadic and '
          'would otherwise consume a following prompt); '
          'MCP registration alone does not enable the channel and org policy is not bypassed.')


def install_service(root):
    service = Path.home() / '.config/systemd/user/cairn-presence.service'
    service.parent.mkdir(parents=True, exist_ok=True)
    command = shlex.join([sys.executable, str(root / 'coordination.py'), 'watch', '--config-dir', str(root / 'bindings')])
    service.write_text(f'''[Unit]
Description=Cairn native session presence
After=cairn-api.service

[Service]
Type=simple
ExecStart={command}
Environment=PYTHONDONTWRITEBYTECODE=1
Restart=on-failure
RestartSec=5
TimeoutStopSec=10

[Install]
WantedBy=default.target
''')
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', '--user', 'enable', '--now', service.name], check=True)
    subprocess.run(['systemctl', '--user', 'restart', service.name], check=True)


def main():
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', required=True, choices=('codex', 'claude', 'agy', 'opencode', 'hermes'))
    parser.add_argument('--binding', required=True)
    parser.add_argument('--settings', type=Path, required=True, help='hook settings file, or OpenCode/Hermes directory')
    parser.add_argument('--root', type=Path, default=home / '.local/share/cairn/coordination')
    parser.add_argument('--cairn', default=shutil.which('cairn'))
    parser.add_argument('--socket', type=Path, default=home / '.local/share/cairn/api.sock')
    parser.add_argument('--token-file', type=Path, default=home / '.local/share/cairn/hosted-agent.token')
    parser.add_argument('--repo', default=str(home / 'git/cairn'))
    parser.add_argument('--model', default='')
    parser.add_argument('--native-delivery', action='store_true', help='enable turn-boundary inbox handling (requires schema 039 and matching API)')
    parser.add_argument('--idle-wakeup', action='store_true', help='automatically prompt eligible idle Herdr sessions with pending inbox work; also installs Herdr\'s native integration in the selected account home through the installed herdr CLI when missing')
    parser.add_argument('--herdr', default=shutil.which('herdr'), help='Herdr executable for --idle-wakeup')
    parser.add_argument('--claude-channel-dir', type=Path, help='owner-only directory bridging Claude channel wakes (Claude + --idle-wakeup only)')
    parser.add_argument('--codex', help='native codex executable the launcher shim execs (default: resolved from PATH)')
    parser.add_argument('--no-service', action='store_true', help='prepare hooks without installing/restarting the watcher')
    args = parser.parse_args()
    if not args.cairn or not args.token_file.is_file():
        parser.error('installed cairn and an existing profile token are required')
    if not args.binding or len(args.binding) > 128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in args.binding) or args.binding in ('.', '..'):
        parser.error('binding must be a bounded name without path components')
    config = dict(cairn=str(Path(args.cairn).resolve()), socket=str(args.socket.resolve()), token_file=str(args.token_file.resolve()),
                  repo=args.repo, harness=args.harness, binding=args.binding, model=args.model,
                  process_names=[args.harness], native_delivery=args.native_delivery)
    if args.idle_wakeup:
        if not args.native_delivery or not args.herdr or not Path(args.herdr).is_file():
            parser.error('--idle-wakeup requires --native-delivery and an installed Herdr executable')
        if args.harness == 'codex' and importlib.util.find_spec('websocket') is None:
            parser.error('Codex native queue wakeups require websocket-client in this Python environment (Ubuntu: python3-websocket)')
        config['idle_wakeup'] = str(Path(args.herdr).resolve())
    if args.claude_channel_dir is not None:
        if args.harness != 'claude' or not args.idle_wakeup:
            parser.error('--claude-channel-dir requires --harness claude with --idle-wakeup')
        directory = args.claude_channel_dir.resolve()
        if not directory.is_absolute():
            parser.error('--claude-channel-dir must be absolute')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        config['claude_channel_dir'] = str(directory)
    if args.harness in ('codex', 'claude'):
        config['config_home'] = str(args.settings.resolve().parent)
    if args.idle_wakeup:
        target = ensure_herdr_integration(Path(args.herdr).resolve(), args.harness, args.settings.resolve())
        if target:
            print(f'Herdr {target} integration is current in the selected {args.harness} account home.')
    installed = install(args.root.resolve(), args.settings.resolve(), config)
    if args.claude_channel_dir is not None:
        register_claude_channel(Path(args.cairn).resolve(), config['claude_channel_dir'],
                                Path(config['config_home']))
    if args.harness == 'codex':
        launcher = args.root.resolve() / 'launch-codex-coordination.py'
        shutil.copyfile(ROOT / 'scripts/launch-codex-coordination.py', launcher)
        launcher.chmod(0o700)
        install_codex_shim(args.root.resolve(), str(Path(args.codex).resolve()) if args.codex else None)
        command = shlex.join([sys.executable, str(args.root.resolve() / 'coordination.py'), 'hook', '--config', str(installed)])
        trust_codex_hooks(args.settings.resolve().parent, args.settings.resolve(), command)
    if not args.no_service:
        install_service(args.root.resolve())
    print(f'Installed {args.harness} coordination binding {args.binding}; start a fresh native session to load hooks.')


if __name__ == '__main__':
    main()
