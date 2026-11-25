#!/usr/bin/env python3
"""Install native session presence hooks and one host liveness watcher."""
import argparse
import importlib.util
import json
import os
import queue
import re
import signal
import threading
import time
import tempfile
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from trial_codex_turn_deadline import ActiveTurnDeadline

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('coordination', ROOT / 'integrations/lifecycle/coordination.py')
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
_bundle_spec = importlib.util.spec_from_file_location('cairn_lifecycle_bundle_installer', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
bundle = importlib.util.module_from_spec(_bundle_spec)
_bundle_spec.loader.exec_module(bundle)


def backup(path):
    target = path.with_name(path.name + '.before-cairn-coordination')
    if path.exists() and not target.exists():
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)


def install_compact_inbox_launcher(root, home):
    target = home / '.local/bin/cairn-inbox'
    if str(target.parent) not in os.environ.get('PATH', '').split(os.pathsep):
        raise RuntimeError('compact inbox needs ~/.local/bin on PATH')
    script = ('#!/bin/sh\nexec env CAIRN_INBOX_ROOT=' + shlex.quote(str(root)) + ' ' +
              shlex.quote(sys.executable) + ' ' + shlex.quote(str(root / 'coordination.py')) +
              ' inbox compact "$@"\n')
    if target.exists() and target.read_text() != script:
        raise RuntimeError(f'compact inbox launcher at {target} belongs to another installation')
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.write_text(script)
    target.chmod(0o700)


def install(root, settings, config):
    config = dict(config, state_dir=str(root / 'state' / config['binding']))
    # Several accounts can share this runtime directory; every existing bound
    # account must be disabled before any of its implementation files change.
    for installed_config in (root / 'bindings').glob('*.json'):
        previous = json.loads(installed_config.read_text())
        if previous.get('binding') == config['binding']:
            config = dict(previous, **config)
            if 'state_dir' in previous:
                config['state_dir'] = previous['state_dir']
        binding = previous.get('inbox_recall_binding')
        if binding:
            manifest = json.loads(Path(binding).read_text())
            if manifest.get('enabled') is not False:
                raise ValueError('disable the recall binding before reinstalling; repin and validate before re-enabling')
            if previous.get('binding') == config['binding']:
                if config.get('inbox_recall_binding', binding) != binding:
                    raise ValueError('coordination reinstall cannot replace the recall binding')
                config['inbox_recall_binding'] = binding
    if config.get('inbox_recall_binding'):
        manifest = json.loads(Path(config['inbox_recall_binding']).read_text())
        if manifest.get('enabled') is not False:
            raise ValueError('disable the recall binding before reinstalling; repin and validate before re-enabling')
    engine.validate_config(config)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    bindings = root / 'bindings'
    bindings.mkdir(exist_ok=True, mode=0o700)
    config_path = bindings / (config['binding'] + '.json')
    script = root / 'coordination.py'
    engine.write_state(config_path, config)
    engine.load_config(config_path)
    bundle.stage_runtime(ROOT / 'integrations/lifecycle', root)
    command = [sys.executable, str(script), 'hook', '--config', str(config_path)]
    harness = config['harness']
    if harness in ('codex', 'claude'):
        data = json.loads(settings.read_text()) if settings.exists() else {}
        hooks = data.setdefault('hooks', {})
        # Codex also reports Interrupt: a natively interrupted turn is actually
        # idle for presence, while its tools may still be running.
        # PostToolUse makes one bounded read-only count lookup to cue new inbox work mid-turn.
        events = ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd', 'PostToolUse')
        if harness == 'codex':
            events = ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd', 'Interrupt', 'PostToolUse')
        for event in events:
            text = shlex.join(command)
            groups = hooks.setdefault(event, [])
            for group in groups:
                group['hooks'] = [h for h in group['hooks'] if h.get('command') != text]
            groups[:] = [group for group in groups if group['hooks']]
            groups.append({'hooks': [{'type': 'command', 'command': text, 'timeout': 2 if event in ('SessionEnd', 'Interrupt', 'PostToolUse') else 15}]})
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


class CodexRPCError(RuntimeError):
    """RPC refusal with only a redacted, bounded diagnostic retained."""
    def __init__(self, method, error):
        self.code = error.get('code') if isinstance(error, dict) else None
        raw = error.get('message', '') if isinstance(error, dict) else ''
        if not isinstance(raw, str):
            raw = ''
        safe = raw.lower()
        safe = re.sub(r'(?i)(token|authorization|secret|api[_ -]?key)\s*[:=]\s*\S+',
                      r'\1=<redacted>', safe)
        safe = re.sub(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b', '<uuid>', safe)
        safe = re.sub(r'\b[a-z0-9_-]{32,}\b', '<id>', safe)
        safe = re.sub(r'/(?:[^\s,;]+)', '<path>', safe)
        self.safe_message = re.sub(r'\s+', ' ', safe).strip()[:160]
        super().__init__(f"Codex {method} RPC refused")


def trust_codex_hooks(config_home, settings, command, verify=None, codex_binary=None, expected=6,
                      timeout=None):
    """Trust reviewed hooks with a deadline for app-server setup RPCs.

    The timeout does not cancel or bound an active native turn. A caller that
    supplies ``verify`` owns its turn and descendant cancellation policy.
    """
    codex = codex_binary or shutil.which('codex')
    if not codex:
        raise RuntimeError('Codex is required to register its hook definitions')
    if timeout is not None and (type(timeout) not in (int, float) or timeout <= 0):
        raise ValueError('Codex hook deadline must be positive')
    overall_deadline = None if timeout is None else time.monotonic() + timeout

    def remaining(default=15):
        if overall_deadline is None:
            return default
        left = overall_deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError('Codex hook trial exceeded its active deadline')
        return min(default, left)

    messages = queue.Queue()
    notifications = queue.Queue()
    with tempfile.TemporaryDirectory(prefix='cairn-hook-review-') as cwd:
        process = subprocess.Popen([codex, 'app-server', '--stdio'], cwd=cwd,
            env=dict(os.environ, CODEX_HOME=str(config_home)), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
            start_new_session=True)
        stop_lock = threading.Lock()
        server_stopped = False

        def process_group_exists():
            try:
                os.killpg(process.pid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True

        def stop_server():
            nonlocal server_stopped
            with stop_lock:
                if server_stopped:
                    return
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline and process_group_exists():
                    time.sleep(.02)
                if process_group_exists():
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=1)
                finally:
                    server_stopped = True
        def receive():
            try:
                for line in process.stdout:
                    value = json.loads(line)
                    target = notifications if 'method' in value and 'id' not in value else messages
                    target.put(value)
            except ValueError:
                messages.put({'transport_error': 'invalid JSON'})
            finally:
                messages.put({'transport_error': 'app server closed'})
        reader = threading.Thread(target=receive, daemon=True)
        reader.start()
        sequence = 0
        rpc_lock = threading.Lock()
        def rpc(method, params, _timeout=None):
            nonlocal sequence
            wait_limit = remaining(_timeout or 15)
            if not rpc_lock.acquire(timeout=wait_limit):
                raise TimeoutError('Codex app-server RPC lock timed out')
            try:
                sequence += 1
                request_id = sequence
                process.stdin.write(json.dumps(dict(id=request_id, method=method, params=params)) + '\n')
                process.stdin.flush()
                deadline = time.monotonic() + min(wait_limit, remaining(wait_limit))
                while True:
                    left = min(deadline - time.monotonic(), remaining(wait_limit))
                    if left <= 0:
                        raise TimeoutError('Codex hook RPC timed out')
                    try:
                        value = messages.get(timeout=left)
                    except queue.Empty as exc:
                        if overall_deadline is not None and time.monotonic() >= overall_deadline:
                            raise TimeoutError('Codex hook setup deadline expired') from exc
                        raise TimeoutError('Codex hook RPC timed out') from exc
                    if 'transport_error' in value:
                        raise RuntimeError(value['transport_error'])
                    if value.get('id') != request_id:
                        continue
                    if 'error' in value:
                        raise CodexRPCError(method, value['error'])
                    return value['result']
            finally:
                rpc_lock.release()
        def next_notification(timeout=15):
            try:
                return notifications.get(timeout=remaining(timeout))
            except queue.Empty as exc:
                if overall_deadline is not None and time.monotonic() >= overall_deadline:
                    raise TimeoutError('Codex hook trial exceeded its active deadline') from exc
                raise
        rpc.next_notification = next_notification
        def arm_turn_deadline(thread_id, turn_id, seconds):
            return ActiveTurnDeadline(seconds,
                lambda: rpc('turn/interrupt', dict(threadId=thread_id, turnId=turn_id), _timeout=2),
                stop_server).start()
        rpc.arm_turn_deadline = arm_turn_deadline
        try:
            rpc('initialize', dict(clientInfo=dict(name='cairn-hook-installer', version='1'), capabilities=dict(experimentalApi=True)))
            process.stdin.write(json.dumps(dict(method='initialized')) + '\n')
            process.stdin.flush()
            response = rpc('hooks/list', dict(cwds=[cwd]))
            hooks = [h for entry in response['data'] for h in entry['hooks']
                     if h['sourcePath'] == str(settings) and h.get('command') == command]
            if len(hooks) != expected:
                raise RuntimeError(f'Codex did not load all {expected} Cairn hook definitions')
            for hook in hooks:
                rpc('config/value/write', dict(filePath=str(config_home / 'config.toml'),
                    keyPath='hooks.state.' + json.dumps(hook['key']) + '.trusted_hash',
                    value=hook['currentHash'], mergeStrategy='replace'))
            verified = rpc('hooks/list', dict(cwds=[cwd]))
            selected = [h for entry in verified['data'] for h in entry['hooks'] if h['key'] in {h['key'] for h in hooks}]
            if len(selected) != expected or any(h['trustStatus'] != 'trusted' or not h['enabled'] for h in selected):
                raise RuntimeError(f'Codex did not confirm the {expected} reviewed Cairn hooks are enabled')
            if verify is not None:
                verify(rpc)
        finally:
            stop_server()
            reader.join(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


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
                               f'{path.parent.parent}; --settings {settings} cannot be woken through Herdr. '
                               f'Install with --settings {path.parent.parent}, or pass --no-herdr to use '
                               f"OpenCode's native bridge wake without Herdr")
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


def configure_installed_session(args, home):
    if args.session is not None and args.agent_id is not None:
        return 'pass at most one of --session and --agent-id', None
    if args.clear_session_options and (
            args.compact_inbox or args.notice_batch or args.notice_digest
            or args.digest_window_seconds is not None or args.auto_settle_action_none):
        return ('cannot combine --clear-session-options with --compact-inbox, '
                '--notice-batch, --notice-digest or --auto-settle-action-none'), None
    if args.notice_batch and not args.compact_inbox:
        return '--notice-batch requires --compact-inbox', None
    if args.notice_digest and not args.compact_inbox:
        return '--notice-digest requires --compact-inbox', None
    if args.digest_window_seconds is not None and (
            not args.notice_digest or not (1 <= args.digest_window_seconds <= 3600)):
        return '--digest-window-seconds requires 1..3600 with --notice-digest', None
    if not args.clear_session_options and not args.compact_inbox and not args.auto_settle_action_none:
        return '--session/--agent-id requires --compact-inbox, --auto-settle-action-none or --clear-session-options', None
    for forbidden, name in ((args.opencode_cancel_trial, '--opencode-cancel-trial'),
                            (args.idle_wakeup, '--idle-wakeup'),
                            (args.no_herdr, '--no-herdr'),
                            (args.claude_channel_dir is not None, '--claude-channel-dir')):
        if forbidden:
            return f'{name} cannot be combined with --session or --agent-id', None
    root = args.root.resolve()
    config_path = args.config.resolve() if args.config is not None else (root / 'bindings' / (args.binding + '.json'))
    if not config_path.is_file():
        return f'installed binding configuration {config_path} does not exist', None
    installed = engine.load_config(config_path)
    if args.binding and installed.get('binding') != args.binding:
        return '--binding does not match installed binding configuration', None
    if args.harness and installed.get('harness') != args.harness:
        return '--harness does not match installed binding configuration', None
    if args.compact_inbox and (installed.get('harness') not in ('codex', 'claude') or not installed.get('native_delivery')):
        return '--compact-inbox requires an installed Codex or Claude binding with --native-delivery', None
    if args.auto_settle_action_none and not installed.get('native_delivery'):
        return '--auto-settle-action-none requires an installed binding with --native-delivery', None
    native = engine.resolve_session_target(installed, native_session_id=args.session, agent_id=args.agent_id)
    pinned = installed.get('inbox_recall_binding')
    manifest_schema = None
    if pinned and Path(pinned).is_file():
        try:
            parsed_manifest = json.loads(Path(pinned).read_text())
        except (OSError, ValueError):
            return f'bound recall manifest {pinned} is unreadable or invalid JSON', None
        if not isinstance(parsed_manifest, dict):
            return f'bound recall manifest {pinned} must be a JSON object', None
        manifest_schema = parsed_manifest.get('schema')
    default_cfg_path = root / 'bindings' / (installed['binding'] + '.json')
    if (args.compact_inbox and manifest_schema != 'cairn.inbox-recall-binding/3'
            and not (config_path.parent / 'receipt.json').is_file()
            and (args.config is None or config_path == default_cfg_path)):
        install_compact_inbox_launcher(root, home)
    result = engine.configure_session_inbox(
        installed, native_session_id=native,
        compact_inbox=args.compact_inbox, notice_batch=args.notice_batch,
        notice_digest=args.notice_digest, digest_window_seconds=args.digest_window_seconds,
        auto_settle_action_none=args.auto_settle_action_none,
        clear=args.clear_session_options)
    return None, result


def main():
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harness', choices=('codex', 'claude', 'agy', 'opencode', 'hermes'))
    parser.add_argument('--binding', required=True)
    parser.add_argument('--settings', type=Path, help='hook settings file, or OpenCode/Hermes directory')
    parser.add_argument('--config', type=Path, help='installed binding config path for --session/--agent-id')
    parser.add_argument('--session', help='configure compact/batch options for one native session without modifying binding config or restarting the watcher')
    parser.add_argument('--agent-id', help='configure compact/batch options for one active Cairn agent UUID on the binding')
    parser.add_argument('--clear-session-options', action='store_true', help='remove per-session compact/batch options')
    parser.add_argument('--root', type=Path, default=home / '.local/share/cairn/coordination')
    parser.add_argument('--cairn', default=shutil.which('cairn'))
    parser.add_argument('--socket', type=Path, default=home / '.local/share/cairn/api.sock')
    parser.add_argument('--token-file', type=Path, default=home / '.local/share/cairn/hosted-agent.token')
    parser.add_argument('--repo', default=str(home / 'git/cairn'))
    parser.add_argument('--model', default='')
    parser.add_argument('--native-delivery', action='store_true', help='enable turn-boundary inbox handling (requires schema 039 and matching API)')
    parser.add_argument('--compact-inbox', action='store_true', help='opt this Codex or Claude binding into compact notice/response delivery')
    parser.add_argument('--notice-batch', action='store_true', help='opt a compact native binding into bounded already-waiting notice batches')
    parser.add_argument('--notice-digest', action='store_true', help='opt a compact native binding or session into FYI notice digests')
    parser.add_argument('--digest-window-seconds', type=int, help='FYI notice digest window in seconds (1..3600, requires --notice-digest)')
    parser.add_argument('--auto-settle-action-none', action='store_true',
                        help='opt a native binding or session into settling action:none notices without a model turn')
    parser.add_argument('--opencode-cancel-trial', action='store_true',
                        help='opt in the OpenCode binding to the request-cancellation trial (requires native delivery and idle wakeup)')
    parser.add_argument('--idle-wakeup', action='store_true', help='automatically prompt eligible idle Herdr sessions with pending inbox work; also installs Herdr\'s native integration in the selected account home through the installed herdr CLI when missing')
    parser.add_argument('--herdr', help='Herdr executable for --idle-wakeup (default: resolved from PATH)')
    parser.add_argument('--no-herdr', action='store_true',
                        help="OpenCode only, with --idle-wakeup --no-service: prepare native bridge wake without Herdr. "
                             'Skips the Herdr executable and integration checks and changes nothing outside the '
                             'selected --settings directory and --root, so a scratch directory is usable')
    parser.add_argument('--claude-channel-dir', type=Path, help='owner-only directory bridging Claude channel wakes (Claude + --idle-wakeup only)')
    parser.add_argument('--no-service', action='store_true', help='prepare hooks without installing/restarting the watcher')
    args = parser.parse_args()
    if not args.binding or len(args.binding) > 128 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in args.binding) or args.binding in ('.', '..'):
        parser.error('binding must be a bounded name without path components')
    if args.session is not None or args.agent_id is not None or args.clear_session_options or args.config is not None:
        if args.session is None and args.agent_id is None:
            parser.error('--clear-session-options and --config require --session or --agent-id')
        try:
            err_msg, result = configure_installed_session(args, home)
        except engine.CoordinationError as exc:
            parser.error(str(exc))
        if err_msg is not None:
            parser.error(err_msg)
        print(json.dumps(result, sort_keys=True))
        return
    if not args.harness or args.settings is None:
        parser.error('the following arguments are required: --harness, --settings')
    if not args.cairn or not args.token_file.is_file():
        parser.error('installed cairn and an existing profile token are required')
    if args.opencode_cancel_trial and (args.harness != 'opencode' or not args.native_delivery or not args.idle_wakeup):
        parser.error('--opencode-cancel-trial requires --harness opencode --native-delivery --idle-wakeup')
    if args.compact_inbox and (args.harness not in ('codex', 'claude') or not args.native_delivery):
        parser.error('--compact-inbox requires --harness codex|claude --native-delivery')
    if args.notice_batch and not args.compact_inbox:
        parser.error('--notice-batch requires --compact-inbox')
    if args.notice_digest and not args.compact_inbox:
        parser.error('--notice-digest requires --compact-inbox')
    if args.digest_window_seconds is not None:
        if not args.notice_digest or not (1 <= args.digest_window_seconds <= 3600):
            parser.error('--digest-window-seconds requires 1..3600 with --notice-digest')
    if args.auto_settle_action_none and not args.native_delivery:
        parser.error('--auto-settle-action-none requires --native-delivery')
    config = dict(cairn=str(Path(args.cairn).resolve()), socket=str(args.socket.resolve()), token_file=str(args.token_file.resolve()),
                  repo=args.repo, harness=args.harness, binding=args.binding, model=args.model,
                  process_names=[args.harness], native_delivery=args.native_delivery)
    if args.opencode_cancel_trial:
        config['opencode_cancel_enabled'] = True
    if args.auto_settle_action_none:
        config['notice_auto_settle_version'] = 1
    if args.compact_inbox:
        config['compact_inbox_version'] = 1
        if args.notice_batch:
            config['notice_batch_version'] = 1
        if args.notice_digest:
            config['notice_digest_version'] = 1
            if args.digest_window_seconds is not None:
                config['notice_digest_window_seconds'] = args.digest_window_seconds
    if args.compact_inbox or args.auto_settle_action_none:
        previous = args.root.resolve() / 'bindings' / (args.binding + '.json')
        if previous.exists():
            installed = json.loads(previous.read_text())
            pinned = installed.get('inbox_recall_binding')
            if pinned and json.loads(Path(pinned).read_text()).get('enabled') is True:
                flag_name = '--compact-inbox' if args.compact_inbox else '--auto-settle-action-none'
                parser.error(f'{flag_name} needs the bound recall manifest disabled before its configuration changes; '
                             'repin and validate that manifest before re-enabling it, or pass --session/--agent-id '
                             'to configure one session without changing the pinned binding')
            if pinned:
                config['inbox_recall_binding'] = pinned
    if args.no_herdr and (args.harness != 'opencode' or not args.idle_wakeup or args.herdr):
        parser.error('--no-herdr requires --harness opencode with --idle-wakeup, and cannot be combined with --herdr')
    if args.no_herdr and not args.no_service:
        parser.error('--no-herdr requires --no-service; isolated setup must not replace the shared presence watcher')
    if not args.no_herdr:
        args.herdr = args.herdr or shutil.which('herdr')
    if args.idle_wakeup:
        if args.no_herdr:
            if not args.native_delivery:
                parser.error('--idle-wakeup requires --native-delivery')
            config['idle_wakeup'] = True  # OpenCode's bridge never calls Herdr.
        else:
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
    if args.no_herdr:
        print(f'OpenCode idle wake uses its native bridge; Herdr was not consulted and no Herdr integration was '
              f'installed or changed. Only {args.settings.resolve()} and {args.root.resolve()} are written.')
    elif args.idle_wakeup:
        target = ensure_herdr_integration(Path(args.herdr).resolve(), args.harness, args.settings.resolve())
        if target:
            print(f'Herdr {target} integration is current in the selected {args.harness} account home.')
    if args.compact_inbox:
        install_compact_inbox_launcher(args.root.resolve(), home)
    installed = install(args.root.resolve(), args.settings.resolve(), config)
    if args.claude_channel_dir is not None:
        register_claude_channel(Path(args.cairn).resolve(), config['claude_channel_dir'],
                                Path(config['config_home']))
    if args.harness == 'codex':
        # Codex starts natively (no launcher shim), as on every staged Go
        # cohort; Herdr idle wake and turn-boundary delivery reach it.
        command = shlex.join([sys.executable, str(args.root.resolve() / 'coordination.py'), 'hook', '--config', str(installed)])
        trust_codex_hooks(args.settings.resolve().parent, args.settings.resolve(), command)
    if not args.no_service:
        install_service(args.root.resolve())
    print(f'Installed {args.harness} coordination binding {args.binding}; start a fresh native session to load hooks.')


if __name__ == '__main__':
    main()
