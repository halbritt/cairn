"""Owned filesystem/privacy tests; integration is an explicit host:exec command."""

import copy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

import check_r07_bootstrap_reverse as bootstrap
from disposable_host import HostRefusal


class BootstrapFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='r07-bootstrap-unit-')
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for base, _, _ in os.walk(self.root, followlinks=False):
            Path(base).chmod(0o700)
        self.temporary.cleanup()

    def layout(self, host='proximal', observed_modes=False):
        return bootstrap.make_layout(self.root, host, observed_modes=observed_modes)

    def plan(self, selected):
        home, names, archives = selected
        return bootstrap.layout_plan(home, names, archives)


class MetadataPlanTests(BootstrapFixture):
    def test_all_eight_and_seventeen_are_metadata_only_and_never_authority(self):
        selected = self.layout()
        # Even JSON and lock contents must not be opened by this planner.
        with patch.object(Path, 'open', side_effect=AssertionError('content open forbidden')):
            plan = self.plan(selected)
        self.assertEqual(len(plan['members']), 26)
        self.assertEqual(plan['unknown_members'], [])
        self.assertEqual(plan['missing_members'], [])
        self.assertEqual(plan['operator_bootstrap'], 'unsupported')
        self.assertEqual(plan['operator_bootstrap_reverse'], 'unsupported')
        for field in ('authenticated_d1_d5', 'applicable_d6',
                      'stopped_joined_restart_fenced_writer_set', 'exact_s04r_payload_control_home'):
            self.assertEqual(plan[field], 'unknown')
        self.assertNotIn('sha256', json.dumps(plan['members']))
        self.assertTrue(all(row['mode'] == 0o600 for row in plan['members'].values()))

    def test_observed_unsafe_ancestors_get_a_plan_without_chmod(self):
        selected = self.layout(observed_modes=True)
        home = selected[0]
        paths = [home, home / '.local', home / '.local/share']
        before = [bootstrap.metadata(path) for path in paths]
        plan = self.plan(selected)
        self.assertEqual([bootstrap.metadata(path) for path in paths], before)
        self.assertEqual({row['mode'] for row in plan['remediation_plan']}, {0o750, 0o775})
        self.assertEqual(len(plan['remediation_plan']), 3)

    def test_archon_and_talos_are_distinct_initialization_cases(self):
        archon = self.plan(self.layout('archon', observed_modes=True))
        talos = self.plan(self.layout('talos', observed_modes=True))
        self.assertEqual(set(archon['members']), {'codex-archon.json', '.watch.lock'})
        self.assertEqual(archon['remediation_plan'], [])
        self.assertEqual(set(talos['members']), {'opencode-one.json'})
        self.assertIn('separate admitted initialization', talos['lock_initialization'])
        self.assertEqual(talos['members']['opencode-one.json']['mode'], 0o600)
        self.assertEqual(talos['stopped_joined_restart_fenced_writer_set'], 'unknown')
        self.assertIn(0o755, {row['mode'] for row in talos['remediation_plan']})

    def test_unknown_missing_and_symlink_members_are_reported_without_following(self):
        selected = self.layout()
        directory = selected[0] / '.local/share/cairn/coordination/bindings'
        (directory / 'agy-one.json').unlink()
        secret = self.root / 'credential'
        secret.write_bytes(b'PRIVATE CANARY; DO NOT OPEN OR HASH')
        (directory / 'unexpected').symlink_to(secret)
        with patch.object(Path, 'open', side_effect=AssertionError('content open forbidden')):
            plan = self.plan(selected)
        self.assertEqual(plan['missing_members'], ['agy-one.json'])
        self.assertEqual(plan['unknown_members'], ['unexpected'])
        self.assertEqual(plan['special_members'], ['unexpected'])
        self.assertNotIn('PRIVATE CANARY', json.dumps(plan))

    def test_credential_inode_alias_is_detected_without_content_hash(self):
        home, names, archives = self.layout()
        directory = home / '.local/share/cairn/coordination/bindings'
        secret = self.root / 'credential'
        secret.write_bytes(b'PRIVATE CANARY; DO NOT OPEN OR HASH')
        (directory / 'agy-one.json').unlink()
        os.link(secret, directory / 'agy-one.json')
        with patch.object(Path, 'open', side_effect=AssertionError('content open forbidden')):
            plan = bootstrap.layout_plan(home, names, archives, credential_selectors=[secret])
        self.assertEqual(plan['credential_aliases'], ['agy-one.json'])
        self.assertEqual(plan['members']['agy-one.json']['links'], 2)
        self.assertNotIn('PRIVATE CANARY', json.dumps(plan))

    def test_symlink_directory_is_refused_before_descending(self):
        selected = self.layout()
        parent = selected[0] / '.local/share/cairn/coordination'
        parked = parent.with_name('parked')
        parent.rename(parked)
        parent.symlink_to(parked)
        with self.assertRaisesRegex(HostRefusal, 'symlink or special'):
            self.plan(selected)

    def test_missing_lock_is_never_created_by_planner(self):
        selected = self.layout('talos')
        plan = self.plan(selected)
        self.assertNotIn('.watch.lock', plan['members'])
        self.assertFalse((Path(plan['directory']) / '.watch.lock').exists())


class PrimitiveRehearsalTests(BootstrapFixture):
    def test_raw_reverse_retains_original_inodes_modes_references_and_same_lock(self):
        result = bootstrap.raw_filesystem_rehearsal(self.root)
        self.assertEqual((result['bindings'], result['archives'], result['original_members']), (8, 17, 26))
        self.assertEqual(result['joined_fixture_child']['exit'], 0)
        self.assertEqual(result['operator_bootstrap'], 'unsupported')
        self.assertEqual(result['complete_writer_fence'], 'not established')
        active = self.root / 'proximal/.local/share/cairn/coordination/bindings'
        prepared = active.with_name('prepared')
        self.assertTrue((active / '.watch.lock').samefile(prepared / '.watch.lock'))
        self.assertEqual(set(p.name for p in active.iterdir()),
                         {n + '.json' for n in bootstrap.PROXIMAL} | set(bootstrap.ARCHIVES) | {'.watch.lock'})
        self.assertTrue(all(stat.S_IMODE((active / (n + '.json')).stat().st_mode) == 0o600 for n in bootstrap.PROXIMAL))
        self.assertTrue(all(stat.S_IMODE((prepared / (n + '.json')).stat().st_mode) == 0o400 for n in bootstrap.PROXIMAL))

    def test_link_failure_joins_child_and_preserves_complete_original_layout(self):
        spawned = []
        real_popen = bootstrap.subprocess.Popen

        def observe(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            return process

        with patch.object(bootstrap.subprocess, 'Popen', side_effect=observe), \
                patch.object(bootstrap.os, 'link', side_effect=OSError('injected fixture link failure')):
            with self.assertRaisesRegex(OSError, 'injected fixture link failure'):
                bootstrap.raw_filesystem_rehearsal(self.root)
        self.assertEqual(len(spawned), 1)
        self.assertEqual(spawned[0].returncode, 0)
        selected = self.root / 'proximal'
        plan = bootstrap.layout_plan(selected, bootstrap.PROXIMAL, bootstrap.ARCHIVES)
        self.assertEqual(len(plan['members']), 26)
        self.assertEqual(plan['missing_members'], [])
        self.assertTrue(all(row['mode'] == 0o600 for row in plan['members'].values()))

    def test_existing_home_is_never_reused_as_a_fixture(self):
        selected = self.layout()
        before = self.plan(selected)
        with self.assertRaises(FileExistsError):
            bootstrap.raw_filesystem_rehearsal(self.root)
        self.assertEqual(self.plan(selected), before)

    def test_failure_between_renames_retains_raw_original_without_automatic_reverse(self):
        real_rename = Path.rename

        def interrupt(path, target):
            if path.name == 'prepared':
                raise OSError('fixture stopped between primitive renames')
            return real_rename(path, target)

        with patch.object(Path, 'rename', interrupt):
            with self.assertRaisesRegex(OSError, 'stopped between primitive renames'):
                bootstrap.raw_filesystem_rehearsal(self.root)
        parent = self.root / 'proximal/.local/share/cairn/coordination'
        self.assertFalse((parent / 'bindings').exists())
        self.assertEqual(len(list((parent / 'raw-original').iterdir())), 26)
        self.assertTrue((parent / 'raw-original/.watch.lock').samefile(parent / 'prepared/.watch.lock'))


class SemanticContractTests(BootstrapFixture):
    def test_existing_contract_verifies_payload_control_and_separate_private_home(self):
        payload, controls, home, stage = bootstrap.semantic_file_fixture(self.root / 'semantic')
        self.assertNotEqual(payload['address'], controls['address'])
        self.assertFalse(home.is_relative_to(stage))
        self.assertEqual(bootstrap.semantic.private_home(home, stage), home)
        binding = json.loads((Path(controls['path']) / 'binding.json').read_text())
        self.assertEqual(binding['home'], str(home))
        self.assertEqual(binding['payload'], payload)
        self.assertEqual(set(binding['environment']), {'rank', 'stream', 'embed'})

    def test_missing_control_and_wrong_home_mode_refuse_without_repair(self):
        payload, controls, home, stage = bootstrap.semantic_file_fixture(self.root / 'semantic')
        selected = Path(controls['path']) / 'binding.json'
        selected.parent.chmod(0o700)
        selected.unlink()
        selected.parent.chmod(0o555)
        with self.assertRaisesRegex(bootstrap.semantic.ClosureError, 'refusing repair'):
            bootstrap.semantic.verify_generation(controls['path'], controls['descriptor'])
        self.assertFalse(selected.exists())
        home.chmod(0o750)
        with self.assertRaisesRegex(bootstrap.semantic.ClosureError, 'separate existing owned 0700'):
            bootstrap.semantic.private_home(home, stage)
        self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o750)
        bootstrap.semantic.verify_generation(payload['path'], payload['descriptor'])

    def test_readdressed_payload_does_not_equal_exact_retained_selection(self):
        payload, _, _, _ = bootstrap.semantic_file_fixture(self.root / 'semantic')
        changed = copy.deepcopy(payload['descriptor'])
        changed['provenance']['fixture'] = 'different selection'
        with self.assertRaisesRegex(bootstrap.semantic.ClosureError, 'refusing repair'):
            bootstrap.semantic.verify_generation(payload['path'], changed)


class SemanticReverseStageTests(BootstrapFixture):
    def test_operator_cli_publishes_file_verifier_references_and_rejects_wrong_pin(self):
        payload, _, _, _ = bootstrap.semantic_file_fixture(self.root / 'original')
        stage, home, references = (self.root / name for name in ('relocated', 'private-home', 'publications'))
        for path in (stage, home, references):
            path.mkdir(mode=0o700)
        original = self.root / 'original/original-pyvenv.cfg'
        staged = bootstrap.stage_semantic_reverse(payload, original, stage, home)
        payload_reference = self.root / 'payload.json'
        payload_reference.write_text(json.dumps(payload))
        plan = self.root / 'operator-selection.json'
        plan.write_text(json.dumps(dict(schema='cairn.r07-semantic-reverse-selection/1',
            payload=dict(path=str(payload_reference), sha256=bootstrap.control.sha(payload_reference.read_bytes())),
            source_payload=payload['path'], original_config=dict(path=str(original), sha256=bootstrap.control.sha(original.read_bytes())),
            stage=str(stage), home=str(home), expected_control=staged['controls']['address'])))
        command = [sys.executable, '-B', str(bootstrap.ROOT / 'scripts/check_r07_bootstrap_reverse.py'),
                   '--semantic-reverse-selection', str(plan), '--sha256', bootstrap.control.sha(plan.read_bytes()),
                   '--publication-dir', str(references)]
        result = bootstrap.subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        selected = json.loads(result.stdout)
        for path in selected['publications'].values():
            check = bootstrap.subprocess.run([sys.executable, '-B', str(bootstrap.ROOT / 'scripts/semantic_python_closure.py'),
                'verify', '--publication', path], capture_output=True, text=True, timeout=15)
            self.assertEqual((check.returncode, check.stderr), (0, ''))
            self.assertEqual(json.loads(check.stdout)['runtime_qualification'], 'unobserved')
        command[command.index('--sha256') + 1] = '0' * 64
        refused = bootstrap.subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(refused.returncode, 1)
        self.assertEqual(refused.stderr, '')
        self.assertIn('pin changed', json.loads(refused.stdout)['error'])

    def test_real_selected_file_copy_and_exact_serve_selectors_without_execution(self):
        payload, _, _, _ = bootstrap.semantic_file_fixture(self.root / 'original')
        destination, home = self.root / 'relocated', self.root / 'private-home'
        destination.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        references = self.root / 'publications'
        references.mkdir(mode=0o700)
        config = self.root / 'original/original-pyvenv.cfg'
        before = dict(os.environ)
        with patch.object(bootstrap.subprocess, 'Popen', side_effect=AssertionError('retained execution forbidden')):
            result = bootstrap.stage_semantic_reverse(payload, config, destination, home, publications=references)
        self.assertEqual(result['payload']['address'], payload['address'])
        self.assertEqual(result['service_actions'], 0)
        self.assertEqual(result['model_executions'], 0)
        flags = result['serve_selector_flags']
        self.assertEqual(flags[::2], ['--embedding-command', '--semantic-command', '--semantic-stream-command'])
        self.assertTrue(all(Path(path).is_file() for path in flags[1::2]))
        self.assertEqual(os.environ, before)
        self.assertEqual(list(home.iterdir()), [])
        for path in result['publications'].values():
            reference = json.loads(Path(path).read_bytes())
            bootstrap.semantic.verify_generation(reference['path'], reference['descriptor'])
            self.assertEqual(Path(path).stat().st_mode & 0o777, 0o600)

    def test_control_mismatch_never_changes_an_api_selector(self):
        payload, _, _, _ = bootstrap.semantic_file_fixture(self.root / 'original')
        destination, home = self.root / 'relocated', self.root / 'private-home'
        destination.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        selection = self.root / 'api-selector'
        selection.write_bytes(b'original literal API selection\n')
        before = selection.read_bytes(), selection.stat().st_ino
        with self.assertRaisesRegex(HostRefusal, 'control differs'):
            bootstrap.stage_semantic_reverse(payload, self.root / 'original/original-pyvenv.cfg',
                                             destination, home, expected_control='0' * 64)
        self.assertEqual((selection.read_bytes(), selection.stat().st_ino), before)

    def test_nonempty_or_aliased_home_is_refused(self):
        payload, _, _, _ = bootstrap.semantic_file_fixture(self.root / 'original')
        destination, home = self.root / 'relocated', self.root / 'private-home'
        destination.mkdir(mode=0o700)
        home.mkdir(mode=0o700)
        (home / 'unexpected').write_text('fixture')
        with self.assertRaisesRegex(HostRefusal, 'initially be empty'):
            bootstrap.stage_semantic_reverse(payload, self.root / 'original/original-pyvenv.cfg', destination, home)
        self.assertEqual(list(destination.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
