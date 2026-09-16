#!/usr/bin/env python3
"""Install native session presence hooks and one host liveness watcher."""
import argparse
import importlib.util
import json
import os
import queue
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
    shutil.copyfile(ROOT / 'integrations/lifecycle/coordination.py', script)
    script.chmod(0o700)
    command = [sys.executable, str(script), 'hook', '--config', str(config_path)]
    harness = config['harness']
    if harness in ('codex', 'claude'):
        data = json.loads(settings.read_text()) if settings.exists() else {}
        hooks = data.setdefault('hooks', {})
        for event in ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd'):
            text = shlex.join(command)
            groups = hooks.setdefault(event, [])
            for group in groups:
                group['hooks'] = [h for h in group['hooks'] if h.get('command') != text]
            groups[:] = [group for group in groups if group['hooks']]
            groups.append({'hooks': [{'type': 'command', 'command': text, 'timeout': 2 if event == 'SessionEnd' else 15}]})
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
            if len(hooks) != 4:
                raise RuntimeError('Codex did not load all four Cairn hook definitions')
            for hook in hooks:
                rpc('config/value/write', dict(filePath=str(config_home / 'config.toml'),
                    keyPath='hooks.state.' + json.dumps(hook['key']) + '.trusted_hash',
                    value=hook['currentHash'], mergeStrategy='replace'))
            verified = rpc('hooks/list', dict(cwds=[cwd]))
            selected = [h for entry in verified['data'] for h in entry['hooks'] if h['key'] in {h['key'] for h in hooks}]
            if len(selected) != 4 or any(h['trustStatus'] != 'trusted' or not h['enabled'] for h in selected):
                raise RuntimeError('Codex did not confirm the four reviewed Cairn hooks are enabled')
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
    parser.add_argument('--no-service', action='store_true', help='prepare hooks without installing/restarting the watcher')
    args = parser.parse_args()
    if not args.cairn or not args.token_file.is_file():
        parser.error('installed cairn and an existing profile token are required')
    if not args.binding or len(args.binding) > 128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in args.binding) or args.binding in ('.', '..'):
        parser.error('binding must be a bounded name without path components')
    config = dict(cairn=str(Path(args.cairn).resolve()), socket=str(args.socket.resolve()), token_file=str(args.token_file.resolve()),
                  repo=args.repo, harness=args.harness, binding=args.binding, model=args.model,
                  process_names=[args.harness], native_delivery=args.native_delivery)
    if args.harness in ('codex', 'claude'):
        config['config_home'] = str(args.settings.resolve().parent)
    installed = install(args.root.resolve(), args.settings.resolve(), config)
    if args.harness == 'codex':
        command = shlex.join([sys.executable, str(args.root.resolve() / 'coordination.py'), 'hook', '--config', str(installed)])
        trust_codex_hooks(args.settings.resolve().parent, args.settings.resolve(), command)
    if not args.no_service:
        install_service(args.root.resolve())
    print(f'Installed {args.harness} coordination binding {args.binding}; start a fresh native session to load hooks.')


if __name__ == '__main__':
    main()
