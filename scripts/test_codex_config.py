"""Verify the CLI's generated configuration with an independent TOML parser."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tomllib
import unittest


class CodexConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory(prefix='cairn-config-')
        cls.addClassCleanup(cls.scratch.cleanup)
        cls.root = Path(cls.scratch.name)
        cls.binary = cls.root / 'cairn "quoted" 日本語'
        subprocess.run(['go', 'build', '-o', str(cls.binary), './cmd/cairn'],
                       cwd=Path(__file__).resolve().parent.parent,
                       check=True, capture_output=True)

    def render(self, *args):
        env = dict(os.environ, HOME='', CAIRN_HOME='',
                   CAIRN_DATABASE_URL='host=/missing-config-db dbname=denied')
        return subprocess.run([str(self.binary), 'codex-config', *args],
                              cwd=self.root, env=env, capture_output=True, check=False)

    def test_explicit_scope_literal_arguments_and_context(self):
        # No token or API exists. Quotes, shell syntax and every non-NUL ASCII
        # control must survive serialization without becoming TOML structure.
        controls = ''.join(chr(i) for i in range(1, 32)) + chr(127)
        socket = 'missing socket $(touch SHOULD_NOT_EXIST).sock'
        token = 'token "quoted" 日本語'
        task = 'task "\n[mcp_servers.injected]\ncommand = "bad"\n' + controls
        run = '--literal-run\\\U0001f9ed'
        pins = ['--revision', 'a' * 40, '--workspace-sha256', 'b' * 64,
                '--task-class', 'configuration', '--task-phase', 'validation', '--binding', 'b; literal',
                '--capability', 'native']
        args = ['--socket', socket, '--token-file', token, '--repo', 'repo:fixture',
                '--task', task, '--run', run, '--tokens', '64000', *pins]
        result = self.render(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b'')
        parsed = tomllib.loads(result.stdout.decode())
        self.assertEqual(set(parsed), {'mcp_servers'})
        self.assertEqual(set(parsed['mcp_servers']), {'cairn'})
        server = parsed['mcp_servers']['cairn']
        self.assertEqual(server['command'], str(self.binary))
        self.assertEqual(server['args'], ['mcp', '--socket', str(self.root / socket),
                         '--token-file', str(self.root / token), '--repo', 'repo:fixture',
                         '--task', task, '--run', run, '--tokens', '64000', *pins])
        self.assertEqual(server['enabled_tools'], ['cairn_search', 'cairn_pull',
                         'cairn_pull_evidence', 'cairn_remember', 'cairn_edit', 'cairn_history'])
        self.assertFalse(server['required'])
        self.assertEqual(server['startup_timeout_sec'], 15)
        self.assertEqual(set(server), {'command', 'args', 'enabled_tools',
                                      'required', 'startup_timeout_sec'})
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())
        self.assertFalse((self.root / '.codex').exists())

    def test_conversation_scope_and_required_startup(self):
        args = ['--socket', '/missing.sock', '--token-file', '/missing.token',
                '--repo', 'repo:fixture', '--codex-thread', '--required']
        result = self.render(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        server = tomllib.loads(result.stdout.decode())['mcp_servers']['cairn']
        self.assertTrue(server['required'])
        self.assertEqual(server['args'], ['mcp', *args[:-1], '--tokens', '32000'])

    def test_invalid_cli_is_stderr_only(self):
        result = self.render('--socket', '/missing.sock')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        self.assertIn(b'--token-file', result.stderr)

    def test_shared_argv_keeps_opencode_format(self):
        args = ['--socket', '/missing.sock', '--token-file', '/missing.token',
                '--repo', 'repo:fixture', '--task', 'task', '--run', 'run',
                '--tokens', '64000', '--binding', 'same-binding']
        codex = tomllib.loads(self.render(*args).stdout.decode())['mcp_servers']['cairn']
        other = subprocess.run([str(self.binary), 'opencode-config', *args],
                               check=True, capture_output=True)
        opencode = json.loads(other.stdout)
        self.assertEqual(opencode, {'mcp': {'cairn': {'type': 'local', 'enabled': True,
                         'command': [codex['command'], *codex['args']]}}})


if __name__ == '__main__':
    unittest.main()
