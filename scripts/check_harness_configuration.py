"""Verify offline setup preserves valid text and rejects unusable configuration."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib


def check(binary):
    binary = str(Path(binary).resolve())
    with tempfile.TemporaryDirectory(prefix='cairn-config-text-') as directory:
        root = Path(directory)
        environment = dict(os.environ, HOME=directory, CAIRN_HOME=directory,
                           CAIRN_DATABASE_URL='host=/absent-config-db dbname=denied')
        base = ['--socket', '/missing.sock', '--token-file', '/missing.token',
                '--repo', 'fixture:setup', '--task', 'task', '--run', 'run']

        def invoke(command, args):
            return subprocess.run([os.fsencode(binary), command.encode(),
                                   *[os.fsencode(arg) for arg in args]], cwd=root,
                                  env=environment, capture_output=True, timeout=10)

        for command in ('mcp', 'opencode-config', 'codex-config', 'claude-config'):
            for field, value in [('--task', 'x' * 257), ('--run', '界' * 86),
                                 ('--repo', 'r' * 257), ('--revision', b'change-\xff'),
                                 ('--token-file', b'/missing/\xff.token')]:
                result = invoke(command, [*base, field, value])
                assert result.returncode != 0 and not result.stdout, (command, field, result)
                assert b'UTF-8' in result.stderr, (command, field, result.stderr)
                assert b'missing.token' not in result.stderr, 'Startup reached credential access'

        boundary = '界' * 85 + 'a'  # Exactly 256 UTF-8 bytes.
        literal = r'日本語 😀 � literal \uD800'
        args = [*base, '--repo', boundary, '--task', boundary, '--run', boundary,
                '--revision', literal, '--binding', literal]
        for command in ('opencode-config', 'codex-config', 'claude-config'):
            result = invoke(command, args)
            assert result.returncode == 0 and not result.stderr, (command, result)
            if command == 'codex-config':
                config = tomllib.loads(result.stdout.decode())['mcp_servers']['cairn']
                emitted = [config['command'], *config['args']]
            elif command == 'claude-config':
                config = json.loads(result.stdout)['mcpServers']['cairn']
                emitted = [config['command'], *config['args']]
            else:
                emitted = json.loads(result.stdout)['mcp']['cairn']['command']
            for field, value in [('--repo', boundary), ('--task', boundary), ('--run', boundary),
                                 ('--revision', literal), ('--binding', literal)]:
                assert emitted[emitted.index(field) + 1] == value, (command, field, emitted)
        assert not list(root.iterdir()), 'Rendering configuration created files'

        project = root / 'project'
        project.mkdir()
        installation = [*args, '--project', str(project)]
        result = invoke('opencode-install', installation)
        assert result.returncode == 0, result
        settings = json.loads((project / '.opencode/cairn.json').read_text())
        assert settings['task_id'] == boundary and settings['run_id'] == boundary
        assert settings['context']['revision'] == literal and settings['context']['binding'] == literal
        snapshot = {p.relative_to(project): p.read_bytes() for p in project.rglob('*') if p.is_file()}
        for field in ('--repo', '--revision', '--token-file'):
            result = invoke('opencode-install', [*installation, '--replace', field, b'bad-\xff'])
            assert result.returncode != 0, (field, result)
            assert {p.relative_to(project): p.read_bytes() for p in project.rglob('*') if p.is_file()} == snapshot
    print('Harness setup refuses unusable scope/text before credentials, output or replacement; valid Unicode round-trips')


if __name__ == '__main__':
    check(sys.argv[1])
