"""Literal execution frames only; does not start a native client or model."""
from pathlib import Path
import shlex
import tempfile
import unittest

from check_r06_native_hook_census import hook_process_frame


class RetainedHookFrames(unittest.TestCase):
    def test_retained_python_has_no_shell_or_added_tail(self):
        for role in ('memory', 'coordination'):
            with self.subTest(role=role):
                argv = ['/usr/bin/python3', '-I', '-B', '/owned/real retained/' + role + '.py']
                if role == 'coordination':
                    argv.append('hook')
                argv += ['--config', '/owned/real retained/' + role + '.json']
                before = list(argv)
                program, actual = hook_process_frame(dict(implementation='python', argv=argv))
                self.assertEqual(program, '/usr/bin/python3')
                self.assertEqual(actual, before)
                self.assertEqual(argv, before)

    def test_checked_go_keeps_emitted_shell_tail(self):
        with tempfile.TemporaryDirectory() as scratch:
            launcher = Path(scratch) / 'inbox'
            vector = ['/owned/pinned image/cairn', 'coordination', '--receipt', '/owned/receipt.json']
            launcher.write_text('#!/bin/sh\nexec ' + shlex.join(vector) + ' -- "$@"\n')
            row = dict(implementation='go', argv=[str(launcher), 'hook', '--config', '/owned/café config.json'])
            self.assertEqual(hook_process_frame(row),
                             (vector[0], [*vector, '--', 'hook', '--config', '/owned/café config.json']))

    def test_changed_shell_frame_refuses(self):
        with tempfile.TemporaryDirectory() as scratch:
            launcher = Path(scratch) / 'inbox'
            launcher.write_text('#!/bin/sh\nexec /owned/cairn coordination "$@"\n')
            with self.assertRaisesRegex(AssertionError, 'unexpected emitted launcher frame'):
                hook_process_frame(dict(implementation='go', argv=[str(launcher)]))
