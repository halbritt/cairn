"""Synthetic account selection by reference; no credential content is read or written by the code under test."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_account_selection as selection
import trial_paired_request as paired
import trial_task_eval as te


class Homes:
    def __init__(self, root):
        self.home = root / 'home'
        (self.home / '.claude').mkdir(parents=True)
        (self.home / '.claude/.credentials.json').write_text('{"synthetic": "ambient"}')
        (self.home / '.local/bin').mkdir(parents=True)
        (self.home / '.local/bin/claude').write_text('#!/bin/sh\n')
        self.selected = root / 'accounts/weekly'
        self.selected.mkdir(parents=True)
        (self.selected / '.credentials.json').write_text('{"synthetic": "selected"}')


class AccountSelectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.homes = Homes(self.root)
        self.home_patch = patch.object(Path, 'home', return_value=self.homes.home)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def command(self, environment):
        with patch.dict(os.environ, environment, clear=False):
            if selection.ENVIRONMENT not in environment:
                os.environ.pop(selection.ENVIRONMENT, None)
            return te.sandbox_command(self.root / 'work', '.', [], dict(HOME=str(self.homes.home)), ['claude', '-p'])

    def binds(self, command):
        return [tuple(command[i:i + 3]) for i, part in enumerate(command) if part in ('--bind', '--ro-bind')]

    def test_selected_config_home_is_bound_by_reference_in_place_of_ambient_credentials(self):
        command = self.command({selection.ENVIRONMENT: str(self.homes.selected)})
        target = str(self.homes.home / '.claude/.credentials.json')
        credential_binds = [b for b in self.binds(command) if b[2] == target]
        self.assertEqual(credential_binds, [('--bind', str(self.homes.selected / '.credentials.json'), target)])
        self.assertNotIn(('--bind', target, target), self.binds(command))

    def test_unselected_harness_keeps_the_original_ambient_binding(self):
        target = str(self.homes.home / '.claude/.credentials.json')
        self.assertIn(('--bind', target, target), self.binds(self.command({})))

    def test_selected_path_must_be_an_absolute_config_home_with_credentials(self):
        for value, code in (('relative/home', 'account_selection_invalid'),
                            (str(self.root / 'missing'), 'account_selection_invalid'),
                            (str(self.root), 'account_credentials_missing')):
            with self.subTest(value=value):
                with self.assertRaisesRegex(paired.Refusal, '^' + code + '$'):
                    self.command({selection.ENVIRONMENT: value})

    def test_selecting_the_ambient_host_credentials_is_refused(self):
        for value in (str(self.homes.home / '.claude'), str(self.homes.home / '.claude/../.claude')):
            with self.subTest(value=value):
                with self.assertRaisesRegex(paired.Refusal, '^ambient_credentials_refused$'):
                    self.command({selection.ENVIRONMENT: value})
        link = self.root / 'alias'
        link.symlink_to(self.homes.home / '.claude')
        with self.assertRaisesRegex(paired.Refusal, '^ambient_credentials_refused$'):
            self.command({selection.ENVIRONMENT: str(link)})

    def test_checks_use_metadata_only(self):
        opened = []
        real_open = open

        def guarded(path, *args, **kwargs):
            opened.append(str(path))
            return real_open(path, *args, **kwargs)
        with patch('builtins.open', guarded), patch.object(Path, 'read_text', side_effect=AssertionError('read')), \
                patch.object(Path, 'read_bytes', side_effect=AssertionError('read')):
            selection.selected_credentials(str(self.homes.selected))
        self.assertFalse([p for p in opened if 'credentials' in p])


if __name__ == '__main__':
    unittest.main()
