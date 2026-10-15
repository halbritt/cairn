"""Synthetic weekly fixtures only; no setup, provider, store or original labels."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_task_eval as weekly


def fixture(root):
    root.mkdir()
    (root / 'revisions').mkdir()
    (root / 'workspaces' / 'base').mkdir(parents=True)
    (root / 'workspaces' / 'base' / 'setup.sh').write_text('# inert source\n')
    (root / 'cases.json').write_text(json.dumps(dict(baseline_commit='1' * 40,
        cases=[dict(id='example', workspace='base', wordings=dict(task='original'))])))
    (root / 'corpus.json').write_text(json.dumps(dict(notes=[dict(id='lesson', body='original')])))
    (root / 'revisions' / 'v2.json').write_text(json.dumps(dict(
        cases={'example': dict(workspace='revisions/v2/source', wordings=dict(task='selected'))},
        corpus={'lesson': dict(body='selected')})))
    (root / 'revisions' / 'v2' / 'source').mkdir(parents=True)
    (root / 'revisions' / 'v2' / 'source' / 'setup.sh').write_text('# selected source\n')
    (root / 'revisions' / 'v3.json').write_text(json.dumps(dict(
        cases={'example': dict(wordings=dict(task='future'))}, corpus={'lesson': dict(body='future')})))
    return root


class WeeklyReaderTests(unittest.TestCase):
    def test_explicit_root_and_version_select_only_their_own_overlays(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            selected, unrelated = fixture(base / 'selected'), fixture(base / 'unrelated')
            with patch.object(weekly, 'TRIAL', unrelated):
                cases = weekly.load_cases(2, root=selected)
                notes = weekly.load_corpus(2, root=selected)
                self.assertEqual(cases[0]['wordings'], {'task': 'selected'})
                self.assertEqual(notes, [dict(id='lesson', body='selected')])
                self.assertEqual(weekly.workspace_dir(cases[0], root=selected),
                                 selected / 'revisions/v2/source')
                self.assertEqual(weekly.workspace_dir(weekly.load_cases(1, root=selected)[0], root=selected),
                                 selected / 'workspaces/base')
                self.assertEqual(weekly.revision_files(2, root=selected), [selected / 'revisions/v2.json'])

    def test_selected_manifest_retains_original_relative_path_hashing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = fixture(Path(directory) / 'input')
            # Existing Path ordering compares components: v2 precedes v2.json.
            names = ['cases.json', 'corpus.json', 'revisions/v2/source/setup.sh',
                     'revisions/v2.json', 'workspaces/base/setup.sh']
            digest = hashlib.sha256()
            for name in names:
                digest.update(name.encode() + b'\0' + hashlib.sha256((root / name).read_bytes()).hexdigest().encode() + b'\n')
            expected = dict(schema='cairn.task-eval.frozen/1', labels_sha256=digest.hexdigest(), files=5,
                cases_sha256=hashlib.sha256((root / 'cases.json').read_bytes()).hexdigest(),
                corpus_sha256=hashlib.sha256((root / 'corpus.json').read_bytes()).hexdigest(),
                baseline_commit='1' * 40, label_version=2)
            self.assertEqual(weekly.label_manifest(2, root=root), expected)
            selected = dict(expected, frozen_at='2026-10-14T00:00:00+0000')
            (root / 'FROZEN-v2.json').write_text(json.dumps(selected))
            self.assertEqual(weekly.frozen_path(2, root=root), root / 'FROZEN-v2.json')
            self.assertEqual(weekly.verify_frozen(2, root=root), selected)
            self.assertEqual(weekly.latest_version(root=root), 3)
            (root / 'revisions/v2/source/setup.sh').write_text('# changed selected source\n')
            with self.assertRaisesRegex(SystemExit, '^labels changed after freezing;'):
                weekly.verify_frozen(2, root=root)


if __name__ == '__main__':
    unittest.main()
