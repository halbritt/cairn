#!/usr/bin/env python3
"""Install Cairn tools, skill and lifecycle provider into one Hermes profile."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
_bundle_spec = importlib.util.spec_from_file_location('cairn_lifecycle_bundle_installer', ROOT / 'integrations/lifecycle/lifecycle_bundle.py')
bundle = importlib.util.module_from_spec(_bundle_spec)
_bundle_spec.loader.exec_module(bundle)


# Engine-config retention helpers. They were shared with the retired direct
# Claude/Codex/OpenCode memory-hook installers; Hermes is their only caller.
def retain_model_overrides(config, existing_path, preview_model=None, recall_model=None):
    existing = json.loads(existing_path.read_text()) if existing_path.exists() else {}
    result = dict(config)
    for key, supplied in (("preview_model", preview_model), ("recall_model", recall_model)):
        if supplied is None and key not in existing:
            continue
        value = supplied if supplied is not None else existing.get(key)
        if (not isinstance(value, str) or not value or value != value.strip()
                or len(value) > 256
                or any(char.isspace() or ord(char) < 32 or 127 <= ord(char) <= 159 for char in value)):
            raise ValueError(f"invalid lifecycle {key}: expected a nonempty model name without whitespace or controls")
        result[key] = value
    return result


def retain_recall_mode(config, existing_path, recall_mode=None):
    existing = json.loads(existing_path.read_text()) if existing_path.exists() else {}
    result = dict(config)
    if recall_mode is None and 'recall_mode' not in config and 'recall_mode' not in existing:
        return result  # Absence retains the ambient default.
    mode = recall_mode if recall_mode is not None else config.get('recall_mode', existing.get('recall_mode'))
    if mode not in ('ambient', 'agent_tools'):
        raise ValueError('invalid lifecycle recall_mode: expected ambient or agent_tools')
    result['recall_mode'] = mode
    return result


def retain_capture_observations(config, existing_path):
    existing = json.loads(existing_path.read_text()) if existing_path.exists() else {}
    result = dict(config)
    value = result.get('capture_observations', existing.get('capture_observations'))
    if value is not None:
        if type(value) is not bool:
            raise ValueError('capture_observations must be a boolean')
        result['capture_observations'] = value
    return result


def retain_inbox_binding(config, existing_path):
    """Require a disabled binding before an explicit installed-runtime upgrade."""
    existing = json.loads(existing_path.read_text()) if existing_path.exists() else {}
    result = dict(existing)
    result.update(config)
    if "state_dir" in existing:
        result["state_dir"] = existing["state_dir"]
    binding = existing.get("inbox_recall_binding") or result.get("inbox_recall_binding")
    if binding:
        if result.get("inbox_recall_binding", binding) != binding:
            raise ValueError("lifecycle reinstall cannot replace the recall binding")
        manifest = json.loads(Path(binding).read_text())
        if manifest.get("enabled") is not False:
            raise ValueError("disable the recall binding before reinstalling; repin and validate before re-enabling")
        result["inbox_recall_binding"] = binding
    return result


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as out:
        temporary = Path(out.name)
        try:
            json.dump(value, out, indent=2, ensure_ascii=False)
            out.write("\n")
            out.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)



def selected_memory(selection, stager):
    """Render Hermes argv using the existing inspected R-02 selection.

    Foundation remains the runtime validator. The shared installer inspects
    immutable material; its existing launcher producer supplies literal L.
    """
    spec = importlib.util.spec_from_file_location('hermes_hook_installer', ROOT / 'scripts/stage_lifecycle_hooks.py')
    hooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hooks)
    installation = hooks.installation
    if not installation.canonical(stager):
        raise ValueError('canonical selected stager required')
    inspected = installation.inspect(stager, selection)
    receipt = hooks.selected_data(inspected['receipt'])
    profile = hooks.selected_data(receipt['memory_config'])
    if profile.get('harness') != 'hermes':
        raise ValueError('selected memory profile must be Hermes')
    profiles = [profile, *(hooks.selected_data(pin) for pin in receipt['coordination_configs'])]
    if any(config.get('inbox_recall_binding') for config in profiles):
        raise ValueError('checked Hermes memory requires unbound profiles')
    generation = installation.backend(stager, ['inspect-generation', '--path', selection['path'],
                                               '--sha256', selection['sha256']])
    member = next(item for item in generation['files'] if item['name'] == 'selection.json')
    metadata = hooks.selected_data({key: member[key] for key in ('path', 'sha256')})
    commands = {}
    for entry, timeout, events in (
            ('memory', 13, ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure')),
            ('memory-capture', 150, ('PreCompact', 'SessionEnd'))):
        vector = metadata['anchors']['memory:0:' + str(timeout)]['vector']
        for flag, expected in (('--role', 'memory'), ('--config', receipt['memory_config']['path']),
                               ('--hook-timeout', str(timeout))):
            if vector.count(flag) != 1 or vector[vector.index(flag) + 1:][:1] != [expected]:
                raise ValueError('selected launch anchor differs from Hermes memory row')
        argv = hooks.checked_go_launcher(stager, receipt['binary']['path'], entry, vector)
        commands.update({event: list(argv) for event in events})
    return dict(commands=commands, engine_config=receipt['memory_config']['path'],
                lifecycle_selection=selection, receipt=inspected['receipt'], binary=receipt['binary'])


def install(home, native, claude, skill, model=None, preview_model=None, recall_model=None, recall_mode=None,
            *, lifecycle_selection=None, stager=None):
    if (lifecycle_selection is None) != (stager is None):
        raise ValueError('lifecycle selection and stager must be supplied together')
    if lifecycle_selection is not None and any(value is not None for value in (model, preview_model, recall_model, recall_mode)):
        raise ValueError('selected memory configuration is immutable; select model and recall settings in R-02 inputs')
    path = home / 'config.yaml'
    original = path.read_bytes() if path.exists() else b''
    config = yaml.safe_load(original) or {}
    if not isinstance(config, dict):
        raise ValueError('Hermes config must be a mapping')
    memory = config.setdefault('memory', {})
    if memory.get('provider') not in (None, '', 'cairn'):
        raise ValueError('Hermes already has an external memory provider; integration would replace it')
    # Validate inputs before changing the active profile.
    skill_body = skill.read_bytes()
    for key in ('executable', 'socket', 'token_file', 'repo'):
        if not isinstance(native.get(key), str) or not native[key]:
            raise ValueError(f'missing Cairn profile field: {key}')
    selected = selected_memory(lifecycle_selection, stager) if lifecycle_selection is not None else None
    engine_path = home / 'cairn/engine.json'
    model_overrides = retain_model_overrides({}, engine_path, preview_model, recall_model)
    model_overrides = retain_recall_mode(model_overrides, engine_path, recall_mode)
    model_overrides = retain_capture_observations(model_overrides, engine_path)
    model_overrides = retain_inbox_binding(model_overrides, engine_path)
    plugin = home / 'plugins/cairn'
    plugin.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source, target in [(ROOT / 'integrations/hermes/__init__.py', plugin / '__init__.py'),
                           (ROOT / 'integrations/hermes/controls.py', plugin / 'controls.py')]:
        shutil.copyfile(source, target)
    runtime = bundle.stage_runtime(ROOT / 'integrations/lifecycle', plugin)
    commands = home / 'plugins/cairn-controls'
    commands.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source, name in [('commands.py', '__init__.py'), ('controls.py', 'controls.py'), ('commands-plugin.yaml', 'plugin.yaml')]:
        shutil.copyfile(ROOT / 'integrations/hermes' / source, commands / name)
    enabled = config.setdefault('plugins', {}).setdefault('enabled', [])
    if 'cairn-controls' not in enabled:
        enabled.append('cairn-controls')
    engine = dict(cairn=native['executable'], socket=native['socket'], token_file=native['token_file'],
                  repo=native['repo'], harness='hermes', semantic_fallback=True, claude=claude,
                  capture_observations=True,
                  state_dir=model_overrides.get('state_dir', str(home / 'cairn/state')))
    if model:
        engine['model'] = model
    if native.get('context'):
        engine['context'] = native['context']
    if selected is None:
        write_json(engine_path, {**engine, **model_overrides})
    write_json(home / 'cairn-lifecycle.json', selected if selected is not None else
                      dict(script=str(plugin / 'memory.py'), engine_config=str(engine_path)))
    skill_dir = home / 'skills/cairn'
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath('SKILL.md').write_bytes(skill_body)
    # Hermes shares one MCP connection within a profile. These are profile labels,
    # while automatic lifecycle operations use the actual Hermes session ID.
    label = 'hermes/profile/' + hashlib.sha256(str(home).encode()).hexdigest()[:16]
    # This installer writes the Hermes configuration, so it is the explicit
    # configuration that names the harness; `cairn mcp` never infers the label.
    args = [
        'mcp', '--socket', native['socket'], '--token-file', native['token_file'], '--repo', native['repo'],
        '--task', label, '--run', label, '--search-view', 'compact/1', '--tool-profile', 'compact/2',
        '--client-harness', 'hermes']
    config.setdefault('mcp_servers', {})['cairn'] = dict(command=native['executable'], args=args, timeout=30, connect_timeout=10)
    memory['provider'] = 'cairn'
    backup = home / 'config.yaml.before-cairn'
    if not backup.exists():
        backup.write_bytes(original)
        backup.chmod(0o600)
    # Use the existing atomic writer: JSON is also valid YAML and preserves values.
    write_json(path, config)
    revision, modified = None, None
    try:
        revision = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL,timeout=5).strip()
        modified = bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True,stderr=subprocess.DEVNULL,timeout=5))
    except (OSError,subprocess.SubprocessError):
        pass  # An archive install has hashes but no verified Git identity.
    write_json(home / 'cairn/installed.json', {
        'schema': 'cairn.hermes-install/1',
        'source_revision': revision, 'source_modified': modified,
        'selected_memory': selected,
        'files': {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (plugin / '__init__.py', plugin / 'controls.py',
                            commands / '__init__.py', commands / 'controls.py', commands / 'plugin.yaml', skill_dir / 'SKILL.md',
                            *runtime.values())}})
    return config


def main():
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-home', type=Path, default=home / '.hermes')
    parser.add_argument('--native-config', type=Path, default=home / '.config/opencode/cairn.json')
    parser.add_argument('--claude', default=shutil.which('claude'))
    parser.add_argument('--skill', type=Path, default=home / '.codex-harm/skills/cairn/SKILL.md')
    parser.add_argument('--model')
    parser.add_argument('--preview-model')
    parser.add_argument('--recall-model')
    parser.add_argument('--recall-mode', choices=('ambient', 'agent_tools'),
                        help='optional recall path; omitted preserves the installed mode (default ambient)')
    parser.add_argument('--lifecycle-selection', type=Path,
                        help='JSON path/sha256 descriptor for an existing immutable R-02 selection')
    parser.add_argument('--stager', help='approved immutable cairn-stage-lifecycle executable')
    args = parser.parse_args()
    if (args.lifecycle_selection is None) != (args.stager is None):
        parser.error('--lifecycle-selection and --stager must be supplied together')
    if not args.claude and args.lifecycle_selection is None:
        parser.error('installed Claude is required for selected capture')
    model = args.model
    if model is None and args.lifecycle_selection is None:
        settings = home / '.claude/settings.json'
        model = json.loads(settings.read_text()).get('model') if settings.exists() else None
    install(args.hermes_home.resolve(), json.loads(args.native_config.read_text()),
            str(Path(args.claude).absolute()) if args.claude else None,
            args.skill, model, args.preview_model, args.recall_model, args.recall_mode,
            lifecycle_selection=json.loads(args.lifecycle_selection.read_bytes()) if args.lifecycle_selection else None,
            stager=args.stager)
    if args.lifecycle_selection:
        print(f'Installed selected Hermes memory in {args.hermes_home}; validate it and start a fresh CLI.')
    else:
        print(f'Installed Cairn in {args.hermes_home}; start a fresh CLI and restart its gateway.')


if __name__ == '__main__':
    main()
