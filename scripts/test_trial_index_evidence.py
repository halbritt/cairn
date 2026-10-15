"""Disposable synthetic Git only; callback declarations are not runtime custody."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import trial_work_product as product
from test_trial_work_product import fixture, git
from test_trial_attempt_evidence import attempt_terminal


class IndexEvidenceTests(unittest.TestCase):
    def export(self, prepared, output, *, version=2, allowed=None):
        exporter = (product.export_attempt_evidence if version == 1 else
                    product.export_attempt_evidence_v2)
        return exporter(prepared, output, allowed_paths=allowed or ['README.md'],
                        required_paths=['README.md'],
                        verify_attempt=lambda binding: attempt_terminal(prepared))

    def test_public_export_distinguishes_staging_that_v1_cannot_observe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); actor = root/'actor'
            (actor/'README.md').write_text('after\n')
            old_before = self.export(prepared, root/'old-before', version=1)
            before = self.export(prepared, root/'before')
            git(actor, 'add', 'README.md')
            old_after = self.export(prepared, root/'old-after', version=1)
            after = self.export(prepared, root/'after')
            self.assertEqual(old_before, old_after)
            for name in ['attempt-evidence.json', 'diff.patch', 'working.patch',
                         'committed.patch', 'files/README.md']:
                self.assertEqual((root/'old-before'/name).read_bytes(),
                                 (root/'old-after'/name).read_bytes())
            self.assertEqual(old_before['schema'], 'cairn.work-product-attempt-evidence/1')
            self.assertNotIn('index_observation', old_before)
            for result, expected in [(before, []), (after, ['README.md'])]:
                self.assertEqual(result['schema'], 'cairn.work-product-attempt-evidence/2')
                observation = result['index_observation']
                self.assertEqual(observation['state'], 'observed')
                self.assertEqual(observation['staged_paths'], expected)
                self.assertEqual(observation['binding'], prepared['binding'])
                self.assertEqual(observation['workspace_sha256'], prepared['workspace_sha256'])
                self.assertEqual(observation['head_commit'], result['commit'])
                self.assertEqual(observation['attempt_content_sha256'], product.canonical_sha(old_before))
                self.assertFalse(result['task_acceptance'])
            self.assertNotEqual(before['index_observation']['index_files'],
                                after['index_observation']['index_files'])
            self.assertEqual(after['index_observation']['index_files']['index']['sha256'],
                             hashlib.sha256((actor/'.git/index').read_bytes()).hexdigest())
            self.assertEqual(json.loads((root/'after/attempt-evidence.json').read_text()), after)

    def test_split_index_refuses_before_git_can_refresh_dependency_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); actor = root/'actor'
            (actor/'README.md').write_text('staged split index\n')
            git(actor, 'add', 'README.md'); git(actor, 'update-index', '--split-index')
            def inventory():
                return {str(p.relative_to(actor/'.git')): p.read_bytes()
                        for p in (actor/'.git').rglob('*') if p.is_file()}
            before = inventory()
            index_names = {'index'} | {name for name in before if name.startswith('sharedindex.')}
            before_stats = {name: ((actor/'.git'/name).stat().st_mtime_ns,
                                   (actor/'.git'/name).stat().st_ctime_ns) for name in index_names}
            self.assertGreater(len(index_names), 1)
            with patch.object(subprocess, 'run', side_effect=AssertionError('Git inspection must not begin')):
                with self.assertRaisesRegex(product.Refusal, 'unsupported_split_index'):
                    self.export(prepared, root/'evidence')
            self.assertFalse((root/'evidence').exists())
            self.assertEqual(inventory(), before, 'observation wrote index, objects or other Git files')
            self.assertEqual({name: ((actor/'.git'/name).stat().st_mtime_ns,
                                     (actor/'.git'/name).stat().st_ctime_ns) for name in index_names}, before_stats)

    def test_rename_names_and_path_selection_are_separate_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); actor = root/'actor'
            git(actor, 'mv', 'README.md', 'new name é.md')
            result = self.export(prepared, root/'evidence', allowed=['README.md', 'new name é.md'])
            observation = result['index_observation']
            self.assertEqual(observation['staged_paths'], ['new name é.md'])
            self.assertEqual(observation['staged_change_paths'], ['README.md', 'new name é.md'])
            # The legacy evaluator's whitespace splitting is not our path decoder.
            self.assertNotEqual(git(actor, 'diff', '--cached', '--name-only').split(),
                                observation['staged_paths'])

    def test_corrupt_index_checksum_is_not_an_observed_empty_index(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); index = root/'actor/.git/index'
            content = index.read_bytes()
            index.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
            with self.assertRaisesRegex(product.Refusal, 'index_format'):
                self.export(prepared, root/'evidence')
            self.assertFalse((root/'evidence').exists())

    def test_missing_malformed_or_indirected_index_refuses_without_output(self):
        for mutation in ['missing', 'truncated', 'symlink', 'hardlink', 'directory',
                         'missing_dependency', 'symlink_dependency', 'corrupt_dependency']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared = fixture(root); actor = root/'actor'
                target = actor/'.git/index'
                if 'dependency' in mutation:
                    git(actor, 'update-index', '--split-index')
                    target = next((actor/'.git').glob('sharedindex.*'))
                content = target.read_bytes()
                target.unlink()
                if mutation == 'truncated': target.write_bytes(b'DIRC')
                if 'symlink' in mutation or mutation == 'hardlink':
                    elsewhere = root/'elsewhere'; elsewhere.write_bytes(content)
                    if 'symlink' in mutation: target.symlink_to(elsewhere)
                    else: os.link(elsewhere, target)
                if mutation == 'directory': target.mkdir()
                if mutation == 'corrupt_dependency': target.write_bytes(b'not an index')
                with self.assertRaises(product.Refusal):
                    self.export(prepared, root/'evidence')
                self.assertFalse((root/'evidence').exists())

    def test_staged_only_unselected_path_cannot_hide_in_unchanged_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); actor = root/'actor'
            (actor/'hidden.txt').write_text('staged, then removed from worktree\n')
            git(actor, 'add', 'hidden.txt'); (actor/'hidden.txt').unlink()
            with self.assertRaisesRegex(product.Refusal, 'unexpected_staged_path'):
                self.export(prepared, root/'evidence')
            self.assertFalse((root/'evidence').exists())

    def test_inspection_failure_and_index_head_or_dependency_drift_refuse_before_write(self):
        for fault in ['git_failure', 'index_read', 'index_drift', 'head_drift', 'dependency_drift']:
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); prepared = fixture(root); actor = root/'actor'
                (actor/'README.md').write_text('change\n')
                if fault == 'head_drift':
                    git(actor, 'add', 'README.md')
                original_run, original_open = subprocess.run, os.open
                fired = False
                def run(argv, *args, **kwargs):
                    nonlocal fired
                    result = original_run(argv, *args, **kwargs)
                    if '--cached' in argv and not fired:
                        fired = True
                        if fault == 'git_failure': return subprocess.CompletedProcess(argv, 1, b'')
                        if fault == 'index_drift': git(actor, 'add', 'README.md')
                        if fault == 'head_drift':
                            git(actor, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
                                'commit', '-qm', 'drift')
                        if fault == 'dependency_drift':
                            # Appearance of even an unused dependency must refuse;
                            # the original index bytes have not changed.
                            data = (actor/'.git/index').read_bytes()
                            (actor/'.git'/('sharedindex.' + data[-20:].hex())).write_bytes(data)
                    return result
                def open_file(path, *args, **kwargs):
                    if fault == 'index_read' and Path(path) == actor/'.git/index':
                        raise OSError('synthetic index read fault')
                    return original_open(path, *args, **kwargs)
                with patch.object(subprocess, 'run', side_effect=run), patch.object(os, 'open', side_effect=open_file):
                    with self.assertRaises(product.Refusal):
                        self.export(prepared, root/'evidence')
                self.assertFalse((root/'evidence').exists())

    def test_index_v4_and_git_pathspec_metacharacters_are_observed_as_names(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); actor = root/'actor'
            names = ['README.md', 'literal[1].txt', ' leading.txt', 'é.txt']
            for name in names[1:]: (actor/name).write_text('synthetic\n')
            git(actor, 'add', '--all'); git(actor, 'update-index', '--index-version=4')
            result = self.export(prepared, root/'evidence', allowed=names)
            self.assertEqual(result['index_observation']['staged_paths'], sorted(names[1:]))
            self.assertEqual(set(result['files']), set(names))

    def test_v2_selected_patch_treats_pathspec_magic_as_literal_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); actor = root/'actor'
            name = ':(literal)task.txt'
            (actor/name).write_text('unique literal filename content\n')
            git(actor, 'add', '--all')
            self.export(prepared, root/'evidence', allowed=['README.md', name])
            self.assertIn(b'unique literal filename content', (root/'evidence/diff.patch').read_bytes())

    def test_valid_empty_index_is_observed_while_missing_index_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); actor = root/'actor'
            git(actor, 'read-tree', '--empty')
            result = self.export(prepared, root/'evidence')
            # An empty index stages deletion of the tracked base file.
            self.assertEqual(result['index_observation']['staged_paths'], ['README.md'])
            self.assertEqual(result['index_observation']['state'], 'observed')

    def test_selected_manifest_pins_and_closure_binding_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root); actor = root/'actor'
            (actor/'README.md').write_text('pinned evidence\n'); git(actor, 'add', 'README.md')
            before = {str(p.relative_to(actor/'.git')): p.read_bytes()
                      for p in (actor/'.git').rglob('*') if p.is_file()}
            stat_before = (actor/'.git/index').stat()
            result = self.export(prepared, root/'evidence')
            self.assertEqual({str(p.relative_to(actor/'.git')): p.read_bytes()
                              for p in (actor/'.git').rglob('*') if p.is_file()}, before)
            stat_after = (actor/'.git/index').stat()
            self.assertEqual((stat_before.st_mtime_ns, stat_before.st_ctime_ns),
                             (stat_after.st_mtime_ns, stat_after.st_ctime_ns))
            for name, digest in result['patch_sha256'].items():
                self.assertEqual(hashlib.sha256((root/'evidence'/name).read_bytes()).hexdigest(), digest)
            for name, metadata in result['files'].items():
                content = (root/'evidence/files'/name).read_bytes()
                self.assertEqual((hashlib.sha256(content).hexdigest(), len(content)),
                                 (metadata['sha256'], metadata['bytes']))
            common = dict(result); observation = common.pop('index_observation')
            common['schema'] = 'cairn.work-product-attempt-evidence/1'
            self.assertEqual(product.canonical_sha(common), observation['attempt_content_sha256'])
            common['terminal']['binding']['occurrence_id'] = '00000000-0000-4000-8000-000000000099'
            self.assertNotEqual(product.canonical_sha(common), observation['attempt_content_sha256'])
            with self.assertRaisesRegex(product.Refusal, 'attempt_terminal_binding'):
                product.export_attempt_evidence_v2(prepared, root/'wrong', allowed_paths=['README.md'],
                    required_paths=[], verify_attempt=lambda binding: common['terminal'])
            self.assertFalse((root/'wrong').exists())

    def test_metadata_including_index_uses_existing_total_cap_before_publication(self):
        self.assertEqual(product.TOTAL_LIMIT, 64 * 1024 * 1024)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); prepared = fixture(root)
            names = ['README.md'] + ['missing-' + str(i).zfill(4) + 'x'*390 for i in range(2048)]
            for exporter in (product.export_attempt_evidence, product.export_attempt_evidence_v2):
                output = root/exporter.__name__
                # Workspace/patches fit the existing 1 MiB reservation. Actual
                # selected presence metadata exceeds this reduced test ceiling.
                with patch.object(product, 'TOTAL_LIMIT', 1536 * 1024):
                    with self.assertRaisesRegex(product.Refusal, 'export_limit'):
                        exporter(prepared, output, allowed_paths=names, required_paths=names,
                                 verify_attempt=lambda binding: attempt_terminal(prepared))
                self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
