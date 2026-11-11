#!/usr/bin/env python3
"""Stage selected shell/OpenCode hook settings; never apply or trust live hooks."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import shlex
import signal
import sys
import tempfile

# This is the explicitly selected installer package, not a runtime payload
# discovered next to a receipt. Its companion tool is an external trust anchor.
spec = importlib.util.spec_from_file_location('selected_install_tool', Path(__file__).with_name('stage_lifecycle_compatibility.py'))
installation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installation)

MEMORY = {
    'codex': {'SessionStart': 13, 'UserPromptSubmit': 13, 'PreCompact': 150, 'Stop': 150},
    'claude': {'SessionStart': 13, 'UserPromptSubmit': 13, 'PreCompact': 150, 'SessionEnd': 150,
               'PostToolUse': 13, 'PostToolUseFailure': 13},
    'opencode': {'SessionStart': 13, 'UserPromptSubmit': 13, 'PreCompact': 150, 'SessionEnd': 150,
                 'PostToolUse': 13, 'PostToolUseFailure': 13},
}
COORDINATION = {
    harness: {event: 2 if event in ('SessionEnd', 'Interrupt', 'PostToolUse') else 15 for event in events}
    for harness, events in {
        'codex': ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd', 'Interrupt', 'PostToolUse'),
        'claude': ('SessionStart', 'UserPromptSubmit', 'Stop', 'SessionEnd', 'PostToolUse'),
        'opencode': ('TurnStart', 'TurnEnd', 'SessionEnd', 'PostToolUse', 'ToolStart', 'ToolEnd'),
    }.items()
}


def selected_data(pin, limit=1024 * 1024):
    return json.loads(installation.read_selected(pin, limit).decode('utf-8'))


def definitions(stager, plan):
    required = {'id', 'selection', 'harness', 'account_home', 'settings', 'replace_commands', 'coordination_index'}
    if plan.get('harness') == 'opencode':
        required |= {'coordination_settings', 'plugins'}
    if set(plan) != required or plan['harness'] not in MEMORY or not installation.canonical(plan['account_home']):
        raise ValueError('explicit supported harness, account home and hook plan required')
    index = plan['coordination_index']
    if type(index) is not int or not 0 <= index < 8:
        raise ValueError('explicit coordination configuration index required')
    install = installation.inspect(stager, plan['selection'])
    generation = installation.backend(stager, ['inspect-generation', '--path', plan['selection']['path'],
                                              '--sha256', plan['selection']['sha256']])
    files = {entry['name']: entry for entry in generation['files']}
    pin = {key: files['selection.json'][key] for key in ('path', 'sha256')}
    metadata = selected_data(pin)
    receipt = selected_data(install['receipt'])
    if index >= len(receipt['coordination_configs']):
        raise ValueError('selected coordination configuration is absent')
    profiles = {'memory': receipt['memory_config'], 'coordination': receipt['coordination_configs'][index]}
    rows = []
    for role, profile in profiles.items():
        config = selected_data(profile)
        if config['harness'] != plan['harness']:
            raise ValueError('hook harness differs from selected configuration')
        if config.get('config_home') and config['config_home'] != plan['account_home']:
            raise ValueError('hook account differs from selected configuration')
        events = MEMORY[plan['harness']] if role == 'memory' else COORDINATION[plan['harness']]
        for event, timeout in events.items():
            label = role + ':' + str(0 if role == 'memory' else index + 1) + ':' + str(timeout)
            launcher = install['launchers'][label]
            argv = launcher
            vector = metadata['anchors'][label]['vector']
            for flag, value in (('--role', role), ('--config', profile['path']), ('--hook-timeout', str(timeout))):
                if vector.count(flag) != 1 or vector[vector.index(flag) + 1:][:1] != [value]:
                    raise ValueError('launcher does not match the selected hook row')
            implementation = 'python'
            reason = ('bound callback' if config.get('inbox_recall_binding') else
                      'ordinary recall for this harness remains Python' if event in ('SessionStart', 'UserPromptSubmit') else
                      'capture or tool observation')
            if role == 'coordination':
                flags = ['--receipt', '--receipt-sha256', '--role', '--entrypoint',
                         '--config', '--hook-timeout', '--launch-root', '--shell']
                if len(vector) != 21 or vector[5::2] != flags:
                    raise ValueError('unsupported checked coordination launch vector')
                argv = [receipt['binary']['path'], 'coordination', *vector[5:],
                        '--', 'hook', '--config', profile['path']]
                implementation = 'go'
                reason = 'receipt-checked Go coordination hook'
            if not config.get('inbox_recall_binding'):
                if (role == 'memory' and plan['harness'] == 'opencode'
                        and event in ('SessionStart', 'UserPromptSubmit')):
                    if config.get('recall_mode') == 'agent_tools':
                        reason = 'receipt-checked ordinary Go consumer unavailable; checked Python recall'
                    else:
                        reason = 'provider-selected recall'
            hook = dict(type='command', command=shlex.join(argv), timeout=timeout)
            if role == 'memory' and plan['harness'] == 'codex':
                if event in ('SessionStart', 'UserPromptSubmit'): hook['additionalContextLimit'] = 12000
                if event == 'Stop': hook['async'] = True
            rows.append(dict(role=role, event=event, argv=argv, checked_launcher=launcher, hook=hook,
                             implementation=implementation, reason=reason))
    return rows, profiles


def render(plan, rows):
    settings = selected_data(plan['settings'])
    if type(settings) is not dict:
        raise ValueError('hook settings must be an object')
    settings = copy.deepcopy(settings)
    hooks = settings.setdefault('hooks', {})
    if type(hooks) is not dict:
        raise ValueError('hook groups must be an object')
    replacements = plan['replace_commands']
    if type(replacements) is not dict or set(replacements) != {'memory', 'coordination'}:
        raise ValueError('explicit previous command lists for both roles required')
    seen = set()
    selected_roles = {command: row['role'] for row in rows
                      for command in (row['hook']['command'], shlex.join(row['checked_launcher']))}
    for role, values in replacements.items():
        if type(values) is not list:
            raise ValueError('previous commands must be lists')
        for command in values:
            if type(command) is not str or not command or command in seen:
                raise ValueError('previous commands must be distinct nonempty strings')
            if command in selected_roles and selected_roles[command] != role:
                raise ValueError('previous command selects another current role')
            seen.add(command)
    for row in rows:
        groups = hooks.setdefault(row['event'], [])
        if type(groups) is not list:
            raise ValueError('event hook groups must be a list')
        matches = []
        commands = [*replacements[row['role']], row['hook']['command']]
        for group in groups:
            if type(group) is not dict or type(group.get('hooks')) is not list:
                raise ValueError('unsupported hook group shape')
            for offset, hook in enumerate(group['hooks']):
                if type(hook) is not dict:
                    raise ValueError('unsupported hook definition')
                if hook.get('command') in commands:
                    matches.append((group, offset))
        if len(matches) > 1:
            raise ValueError('ambiguous previous hook definitions; select an unambiguous staged input')
        if matches:
            group, offset = matches[0]
            group['hooks'][offset] = dict(row['hook'])
        else:
            groups.append({'hooks': [dict(row['hook'])]})
    return settings


def opencode_payloads(plan, rows):
    """Render the two actual plugin configurations, retaining their old bytes.

    Plugins stay in their installed plugins/ directory. The flat immutable
    generation records that layout; installing it remains a separate action.
    """
    pins = plan['plugins']
    if type(pins) is not dict or set(pins) != {'memory', 'coordination'}:
        raise ValueError('explicit memory and coordination plugin source pins required')
    if plan['replace_commands'] != {'memory': [], 'coordination': []}:
        raise ValueError('OpenCode stages plugin configurations, not shell command replacement lists')
    payloads, layout = {}, {}
    for role, name, key in (('memory', 'lifecycle', 'settings'), ('coordination', 'coordination', 'coordination_settings')):
        original = installation.read_selected(plan[key], 1024 * 1024)
        config = json.loads(original.decode('utf-8'))
        if type(config) is not dict:
            raise ValueError('plugin configuration must be an object')
        config['commands'] = {row['event']: row['argv'] for row in rows if row['role'] == role}
        configuration = 'cairn-' + name + '.json'
        plugin = 'cairn-' + name + '.ts'
        payloads[configuration] = installation.encoded(config)
        payloads['previous-' + configuration] = original
        payloads[plugin] = installation.read_selected(pins[role], 1024 * 1024)
        layout[configuration] = configuration
        layout[plugin] = 'plugins/' + plugin
    return payloads, layout


def stage(stager, root, plan):
    if not installation.canonical(stager) or not installation.canonical(str(root)):
        raise ValueError('canonical tool and staging root required')
    rows, profiles = definitions(stager, plan)
    if plan['harness'] == 'opencode':
        payloads, layout = opencode_payloads(plan, rows)
    else:
        settings = render(plan, rows)
        original = installation.read_selected(plan['settings'], 1024 * 1024)
        payloads = {'hook-settings.json': installation.encoded(settings), 'previous-settings.json': original}
        layout = {'hook-settings.json': 'settings.json'}
    record = dict(schema='cairn.lifecycle-hook-settings/1', harness=plan['harness'], account_home=plan['account_home'],
                  lifecycle_selection=plan['selection'], source_settings=plan['settings'], role_configurations=profiles,
                  rows=rows, layout=layout, plugin_sources=plan.get('plugins', {}),
                  native_trust='not-performed', refresh='not-performed', activation='not-performed')
    payloads['hook-selection.json'] = installation.encoded(record)
    with installation.directory(str(root), private=True):
        pass
    with tempfile.TemporaryDirectory(prefix='.render-', dir=root) as temporary:
        files = []
        for name, raw in payloads.items():
            path = Path(temporary) / name
            path.write_bytes(raw); path.chmod(0o600)
            files.append(dict(name=name, path=str(path), sha256=installation.sha(raw)))
        selection = installation.backend(stager, ['generation', '--root', str(root)], dict(id=plan['id'], files=files))
    verified = installation.backend(stager, ['inspect-generation', '--path', selection['path'], '--sha256', selection['sha256']])
    published = {entry['name']: {key: entry[key] for key in ('path', 'sha256')} for entry in verified['files']}
    if plan['harness'] == 'opencode':
        return dict(selection=selection, files=published, layout=layout,
                    native_trust='not-performed', refresh='not-performed', activation='not-performed')
    return dict(selection=selection, settings=published['hook-settings.json'], previous_settings=published['previous-settings.json'],
                native_trust='not-performed', refresh='not-performed', activation='not-performed')


def main():
    def interrupted(*args):
        raise installation.InstallationInterrupted('hook staging interrupted; reconcile selected generation')
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stager', required=True)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('hook plan exceeds 1 MiB')
    if not installation.canonical(args.root):
        raise ValueError('canonical staging root required')
    result = stage(args.stager, Path(args.root), installation.decoded(raw))
    sys.stdout.buffer.write(installation.encoded(result) + b'\n')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, installation.subprocess.SubprocessError) as error:
        sys.stderr.write('hook staging failed: ' + str(error) + '\n')
        raise SystemExit(1)
