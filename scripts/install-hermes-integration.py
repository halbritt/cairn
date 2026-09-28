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

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('claude_installer', ROOT / 'scripts/install-claude-hooks.py')
shared = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shared)


def install(home, native, claude, skill, model=None):
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
    plugin = home / 'plugins/cairn'
    plugin.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source, target in [(ROOT / 'integrations/hermes/__init__.py', plugin / '__init__.py'),
                           (ROOT / 'integrations/lifecycle/memory.py', plugin / 'memory.py'),
                           (ROOT / 'integrations/hermes/controls.py', plugin / 'controls.py')]:
        shutil.copyfile(source, target)
    commands = home / 'plugins/cairn-controls'
    commands.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source, name in [('commands.py', '__init__.py'), ('controls.py', 'controls.py'), ('commands-plugin.yaml', 'plugin.yaml')]:
        shutil.copyfile(ROOT / 'integrations/hermes' / source, commands / name)
    enabled = config.setdefault('plugins', {}).setdefault('enabled', [])
    if 'cairn-controls' not in enabled:
        enabled.append('cairn-controls')
    engine = dict(cairn=native['executable'], socket=native['socket'], token_file=native['token_file'],
                  repo=native['repo'], harness='hermes', semantic_fallback=True, claude=claude, state_dir=str(home / 'cairn/state'))
    if model:
        engine['model'] = model
    if native.get('context'):
        engine['context'] = native['context']
    shared.write_json(home / 'cairn/engine.json', engine)
    shared.write_json(home / 'cairn-lifecycle.json', dict(script=str(plugin / 'memory.py'),
                                                       engine_config=str(home / 'cairn/engine.json')))
    skill_dir = home / 'skills/cairn'
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.joinpath('SKILL.md').write_bytes(skill_body)
    # Hermes shares one MCP connection within a profile. These are profile labels,
    # while automatic lifecycle operations use the actual Hermes session ID.
    label = 'hermes/profile/' + hashlib.sha256(str(home).encode()).hexdigest()[:16]
    config.setdefault('mcp_servers', {})['cairn'] = dict(command=native['executable'], args=[
        'mcp', '--socket', native['socket'], '--token-file', native['token_file'], '--repo', native['repo'],
        '--task', label, '--run', label], timeout=30, connect_timeout=10)
    memory['provider'] = 'cairn'
    backup = home / 'config.yaml.before-cairn'
    if not backup.exists():
        backup.write_bytes(original)
        backup.chmod(0o600)
    # Use the existing atomic writer: JSON is also valid YAML and preserves values.
    shared.write_json(path, config)
    revision, modified = None, None
    try:
        revision = subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True,stderr=subprocess.DEVNULL,timeout=5).strip()
        modified = bool(subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True,stderr=subprocess.DEVNULL,timeout=5))
    except (OSError,subprocess.SubprocessError):
        pass  # An archive install has hashes but no verified Git identity.
    shared.write_json(home / 'cairn/installed.json', {
        'schema': 'cairn.hermes-install/1',
        'source_revision': revision, 'source_modified': modified,
        'files': {str(p.relative_to(home)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (plugin / '__init__.py', plugin / 'memory.py', plugin / 'controls.py',
                            commands / '__init__.py', commands / 'controls.py', commands / 'plugin.yaml', skill_dir / 'SKILL.md')}})
    return config


def main():
    home = Path.home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-home', type=Path, default=home / '.hermes')
    parser.add_argument('--native-config', type=Path, default=home / '.config/opencode/cairn.json')
    parser.add_argument('--claude', default=shutil.which('claude'))
    parser.add_argument('--skill', type=Path, default=home / '.codex-harm/skills/cairn/SKILL.md')
    parser.add_argument('--model')
    args = parser.parse_args()
    if not args.claude:
        parser.error('installed Claude is required for selected capture')
    model = args.model
    if model is None:
        settings = home / '.claude/settings.json'
        model = json.loads(settings.read_text()).get('model') if settings.exists() else None
    install(args.hermes_home.resolve(), json.loads(args.native_config.read_text()),
            str(Path(args.claude).absolute()), args.skill, model)
    print(f'Installed Cairn in {args.hermes_home}; start a fresh CLI and restart its gateway.')


if __name__ == '__main__':
    main()
