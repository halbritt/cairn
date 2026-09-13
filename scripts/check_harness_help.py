"""Check offline CLI help without credentials, a store or a model."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def check(binary):
    binary = str(Path(binary).resolve())
    with tempfile.TemporaryDirectory(prefix='cairn-help-') as directory:
        environment = dict(os.environ, HOME=directory, CAIRN_HOME=directory,
                           CAIRN_DATABASE_URL='host=/absent-help-db dbname=denied')
        for command in ('mcp', 'opencode-config', 'codex-config', 'claude-config'):
            for option in ('--help', '-h'):
                result = subprocess.run([binary, command, option], cwd=directory,
                                        env=environment, text=True, capture_output=True, timeout=5)
                assert result.returncode == 0 and not result.stderr, (command, result.returncode, result.stderr)
                for expected in ('Usage: cairn ' + command, '-socket string', 'Cairn Unix socket (required)',
                                 '-token-file string', '-repo string', '-tokens int', 'default 32000'):
                    assert expected in result.stdout, (command, expected, result.stdout)
            for args in ([], ['--unknown-flag'], ['--', '--help']):
                result = subprocess.run([binary, command, *args], cwd=directory, env=environment,
                                        text=True, capture_output=True, timeout=5)
                assert result.returncode != 0 and not result.stdout and result.stderr, (command, args, result)
        # A flag value containing --help is data, not a help request.
        for command in ('opencode-config', 'codex-config', 'claude-config'):
            result = subprocess.run([binary, command, '--socket', '/absent.sock', '--token-file', '/absent.token',
                                     '--repo', '--help', '--task', 'task', '--run', 'run'], cwd=directory,
                                    env=environment, text=True, capture_output=True, timeout=5)
            assert result.returncode == 0 and not result.stderr and result.stdout, (command, result)
            assert '--help' in result.stdout and 'Usage: cairn' not in result.stdout
        assert not list(Path(directory).iterdir()), 'Help or config wrote state'
    print('MCP and harness configuration help works offline; normal output and invalid argument behavior remain')


if __name__ == '__main__':
    check(sys.argv[1])
