"""Real retained /2 material and selected-hook staging; no native client or API."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

import test_lifecycle_generation_install as retained
import test_r04_hook_settings as current3
from test_f04_activation import ROOT, encoded
from test_r02_compatibility_install import InstalledTools


def pin(path):
    return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def snapshot(root):
    return {str(path.relative_to(root)): (pin(path)['sha256'], path.stat().st_mode & 0o777, path.stat().st_ino)
            for path in root.iterdir() if path.is_file()}


class Retained2HookSettingsTests(InstalledTools, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        destination = os.environ.get('CAIRN_RETAINED2_HOOK_EVIDENCE')
        if destination:
            images = Path(destination).parent / 'images'
            images.mkdir(mode=0o700)
            for image in (cls.stager, cls.binary):
                target = images / image.name
                shutil.copyfile(image, target)
                target.chmod(0o500)
                build = subprocess.run(['go', 'version', '-m', str(image)], capture_output=True, check=True)
                (images / (image.name + '.buildinfo')).write_bytes(build.stdout)
            (images / 'anchors.json').write_bytes(encoded(dict(
                python=pin(Path(sys.executable)), python_version=sys.version,
                stager=pin(cls.stager), activation=pin(cls.binary), home=os.environ.get('HOME'),
                codex_home=os.environ.get('CODEX_HOME'))))

    def setUp(self):
        self.patch = patch.object(retained, 'STAGER', str(self.stager))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.counter = 0

    def fixture(self, harness='claude', settings=None, flags=('-I', '-B')):
        f = retained.GenerationInstallation(methodName='runTest')
        f.harness = harness
        original = f.write_configs

        def configurations(source, final, schema, bundle=None):
            original(source, final, schema, bundle)
            f.account = f.base / "account 日本語 ' with spaces"
            f.account.mkdir(mode=0o700, exist_ok=True)
            for role in ('memory', 'coordination'):
                path = source / (role + '.json')
                config = json.loads(path.read_bytes())
                config['config_home'] = str(f.account)
                config['opaque'] = {'日本語': ['🙂', 9007199254740993123456789]}
                if harness == 'agy':
                    del config['inbox_recall_binding']
                raw = encoded(config)
                path.write_bytes(b'{"context_bytes":123,' + raw[1:] if role == 'memory' else raw)
            binding = json.loads((source / 'binding.json').read_bytes())
            binding['memory_config']['sha256'] = pin(source / 'memory.json')['sha256']
            binding['coordination_configs'][0]['sha256'] = pin(source / 'coordination.json')['sha256']
            (source / 'binding.json').write_bytes(encoded(binding))

        f.write_configs = configurations
        # The maintained fixture publishes the complete bundle using real Go.
        # Select actual retained interpreter flags before its generation write.
        original_stage = f.stage

        def stage(source, identity):
            launches = json.loads((source / 'retained-argv.json').read_bytes())
            for role, argv in launches.items():
                launches[role] = [argv[0], *flags, *argv[3:]]
            f.launches = launches
            (source / 'retained-argv.json').write_bytes(encoded(launches))
            return original_stage(source, identity)

        f.stage = stage
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.counter += 1
        self.addCleanup(self.capture, f, self.counter)
        f.settings = f.account / ('hooks.json' if harness in ('codex', 'agy') else 'settings.json')
        settings = settings if settings is not None else dict(opaque={'日本語': ['🙂', 9007199254740993123456789]})
        f.settings.write_bytes(b'  ' + json.dumps(settings, ensure_ascii=False, indent=2).encode() + b'\n')
        f.settings.chmod(0o600)
        f.hook_root = f.base / 'selected-hooks'
        f.hook_root.mkdir(mode=0o700)
        f.plan = dict(id=str(uuid.uuid4()), selection=f.old, harness=harness, account_home=str(f.account),
                      settings=pin(f.settings), coordination_index=0, legacy=True,
                      replace_commands=dict(memory=['old memory'] if harness != 'agy' else [], coordination=['old coordination']))
        if harness == 'opencode':
            f.plan['replace_commands'] = dict(memory=[], coordination=[])
            f.plan['coordination_settings'] = pin(f.settings)
            f.plan['plugins'] = {}
            for role, name in (('memory', 'lifecycle.ts'), ('coordination', 'coordination.ts')):
                target = f.base / name
                shutil.copyfile(ROOT / 'integrations/opencode' / name, target)
                target.chmod(0o600)
                f.plan['plugins'][role] = pin(target)
        return f

    def capture(self, f, counter):
        destination = os.environ.get('CAIRN_RETAINED2_HOOK_EVIDENCE')
        if destination:
            target = Path(destination) / (self._testMethodName + '-' + str(counter))
            shutil.copytree(f.base, target)
            (target / 'original-disposable-root.txt').write_text(str(f.base) + '\n')

    def stage(self, f, plan=None):
        argv = [sys.executable, '-I', '-S', '-B', str(ROOT / 'scripts/stage_lifecycle_hooks.py'),
                '--stager', str(self.stager), '--root', str(f.hook_root)]
        before = dict(stager=pin(self.stager), producer=pin(ROOT / 'scripts/stage_lifecycle_hooks.py'),
                      compatibility=pin(ROOT / 'scripts/stage_lifecycle_compatibility.py'), plan=plan or f.plan)
        result = subprocess.run(argv, input=encoded(plan or f.plan), capture_output=True, timeout=40,
                                cwd=f.work, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        receipt = dict(before_use=before, argv=argv, exit=result.returncode,
                       stdout=result.stdout.decode(), stderr=result.stderr.decode())
        (f.base / ('stage-' + str(uuid.uuid4()) + '.json')).write_bytes(encoded(receipt))
        return result

    def selected(self, f, plan=None):
        result = self.stage(f, plan)
        self.assertEqual((result.returncode, result.stderr), (0, b''), result.stdout)
        selected = json.loads(result.stdout)
        root = Path(selected['selection']['path']).parent
        metadata = json.loads((root / 'hook-selection.json').read_bytes())
        return selected, root, metadata

    def refuse(self, f, plan=None):
        before = snapshot(f.old_dir), f.settings.read_bytes(), f.settings.stat().st_mode
        result = self.stage(f, plan)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'hook staging failed:', result.stderr)
        self.assertNotIn(b'Traceback', result.stderr)
        self.assertFalse(list(f.hook_root.glob('selection-*')))
        self.assertEqual((snapshot(f.old_dir), f.settings.read_bytes(), f.settings.stat().st_mode), before)
        f.assert_preserved()

    def repin(self, f, writes):
        """Fault injection beneath a valid manifest, never a producer substitute."""
        manifest_path = Path(f.old['path'])
        manifest = json.loads(manifest_path.read_bytes())
        for name, raw in writes.items():
            path = f.old_dir / name
            if path.exists(): path.chmod(0o600)
            path.write_bytes(raw)
            path.chmod(0o400)
        manifest['files'] = [dict(name=path.name, sha256=pin(path)['sha256'], bytes=path.stat().st_size,
                                  mode=path.stat().st_mode & 0o777)
                             for path in sorted(f.old_dir.iterdir()) if path != manifest_path]
        raw = json.dumps(manifest, separators=(',', ':')).encode()
        manifest_path.chmod(0o600)
        manifest_path.write_bytes(raw)
        manifest_path.chmod(0o400)
        f.old['sha256'] = hashlib.sha256(raw).hexdigest()

    def test_actual_retained_bundle_emits_literal_python_hooks_and_preserves_inputs(self):
        for harness in ('codex', 'claude', 'agy', 'opencode'):
            with self.subTest(harness=harness):
                f = self.fixture(harness)
                before, original = snapshot(f.old_dir), f.settings.read_bytes()
                selected, root, metadata = self.selected(f)
                self.assertEqual(metadata['lifecycle_selection'], f.old)
                record = metadata['retained_python']
                binding = json.loads((f.old_dir / 'binding.json').read_bytes())
                self.assertEqual(record['binding'], pin(f.old_dir / 'binding.json'))
                self.assertEqual(record['binding_schema'], 'cairn.inbox-recall-binding/2')
                for key in ('engine', 'bridge'):
                    self.assertEqual(record[key], binding[key])
                self.assertEqual(record['retained_argv'], pin(f.old_dir / 'retained-argv.json'))
                self.assertEqual(record['bundle_manifest'], pin(f.old_dir / 'lifecycle-bundle.json'))
                for row in metadata['rows']:
                    self.assertEqual(row['implementation'], 'python')
                    self.assertEqual(row['reason'], 'verified retained Python/2 hook')
                    self.assertEqual(row['retained_launcher'], f.launches[row['role']])
                    self.assertEqual(row['checked_launcher'], row['retained_launcher'])
                    expected = [*f.launches[row['role']], *(['--event', row['event']] if harness == 'agy' else [])]
                    self.assertEqual(row['argv'], expected)
                    self.assertEqual(shlex.split(row['hook']['command']), expected)
                if harness == 'opencode':
                    self.assertEqual((root / 'previous-cairn-lifecycle.json').read_bytes(), original)
                    self.assertEqual((root / 'previous-cairn-coordination.json').read_bytes(), original)
                    settings = json.loads((root / 'cairn-lifecycle.json').read_bytes())
                else:
                    self.assertEqual(Path(selected['previous_settings']['path']).read_bytes(), original)
                    settings = json.loads(Path(selected['settings']['path']).read_bytes())
                self.assertEqual(settings['opaque'], json.loads(original)['opaque'])
                for path in root.iterdir():
                    self.assertEqual(path.stat().st_mode & 0o777, 0o400)
                self.assertFalse((root / 'receipt.json').exists())
                self.assertFalse((f.old_dir / 'selection.json').exists())
                self.assertEqual(snapshot(f.old_dir), before)
                self.assertEqual(f.settings.read_bytes(), original)
                for key in ('native_trust', 'refresh', 'activation'):
                    self.assertEqual((metadata[key], selected[key]), ('not-performed', 'not-performed'))
                f.assert_preserved()

    def test_retained_plain_and_isolated_argv_remain_exact_and_token_is_never_read(self):
        for flags in ((), ('-I', '-B'), ('-I', '-S', '-B')):
            f = self.fixture(flags=flags)
            f.token.chmod(0o000)
            try:
                selected, _, metadata = self.selected(f)
                self.assertEqual(f.token.stat().st_mode & 0o777, 0)
                self.assertTrue(all(row['argv'] == f.launches[row['role']] for row in metadata['rows']))
                again, _, _ = self.selected(f)
                self.assertEqual(again, selected)
            finally:
                f.token.chmod(0o600)
            f.assert_preserved()

    def test_default_mode_and_malformed_or_unknown_plans_refuse_retained_generation(self):
        for kind in ('default', 'string-legacy', 'wrong-pin', 'wrong-index', 'boolean-index', 'wrong-account', 'wrong-harness'):
            f = self.fixture()
            plan = copy.deepcopy(f.plan)
            if kind == 'default': del plan['legacy']
            elif kind == 'string-legacy': plan['legacy'] = 'true'
            elif kind == 'wrong-pin': plan['selection']['sha256'] = '0' * 64
            elif kind == 'wrong-index': plan['coordination_index'] = 1
            elif kind == 'boolean-index': plan['coordination_index'] = True
            elif kind == 'wrong-account': plan['account_home'] += '-other'
            else: plan['harness'] = 'codex'
            with self.subTest(kind=kind): self.refuse(f, plan)

    def test_explicit_retained_plan_cannot_downgrade_genuine_current3_generation(self):
        legacy = self.fixture()
        owner = current3.R04HookSettingsTests(methodName='runTest')
        owner.root, owner.binary, owner.stager = legacy.base, self.binary, self.stager
        f = owner.fixture('genuine-current3', harness='claude')
        f.base = f.root
        f.work = f.root
        result = self.stage(f, dict(f.hook_plan, legacy=True))
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'explicit retained /2 generation required', result.stderr)
        self.assertFalse(list(f.hook_root.glob('selection-*')))
        self.assertEqual(f.api_calls(), [])

    def test_missing_extra_and_changed_real_generation_members_refuse(self):
        for kind in ('missing', 'extra', 'changed'):
            f = self.fixture()
            path = f.old_dir / ('unexpected.json' if kind == 'extra' else 'hook_admission.py')
            if kind == 'missing': path.unlink()
            else:
                if path.exists(): path.chmod(0o600)
                path.write_bytes(b'changed\n')
                path.chmod(0o400)
            with self.subTest(kind=kind): self.refuse(f)

    def test_repinned_inconsistent_binding_bundle_and_literal_argv_refuse(self):
        kinds = ('schema', 'binding-field', 'duplicate-binding', 'engine-hash', 'engine-role', 'mixed-bundles',
                 'missing-bundle-member', 'extra-bundle-member', 'unknown-member', 'wrong-profile',
                 'missing-argv', 'extra-argv', 'duplicate-config', 'wrong-entrypoint', 'python-code', 'repeated-flag')
        for kind in kinds:
            f = self.fixture()
            binding = json.loads((f.old_dir / 'binding.json').read_bytes())
            bundle = json.loads((f.old_dir / 'lifecycle-bundle.json').read_bytes())
            launches = copy.deepcopy(f.launches)
            writes = {}
            if kind == 'schema': binding['schema'] = 'cairn.inbox-recall-binding/99'
            elif kind == 'binding-field': binding['binary'] = pin(self.binary)
            elif kind == 'engine-hash': binding['engine']['sha256'] = '0' * 64
            elif kind == 'engine-role': binding['engine'].update(pin(f.old_dir / 'coordination.py'))
            elif kind == 'mixed-bundles': binding['engine']['bundle']['sha256'] = '0' * 64
            elif kind in ('missing-bundle-member', 'extra-bundle-member'):
                if kind == 'missing-bundle-member': del bundle['files']['hook_admission.py']
                else: bundle['files']['unknown.py'] = '0' * 64
                bundle['sha256'] = hashlib.sha256(encoded(bundle['files'])).hexdigest()
                binding['engine']['bundle'] = binding['bridge']['bundle'] = bundle
                writes['lifecycle-bundle.json'] = encoded(bundle)
            elif kind == 'unknown-member': writes['unknown.json'] = b'{}'
            elif kind == 'wrong-profile': binding['coordination_configs'][0]['path'] += '-other'
            elif kind == 'missing-argv': del launches['memory']
            elif kind == 'extra-argv': launches['other'] = launches['memory']
            elif kind == 'duplicate-config': launches['coordination'] += launches['coordination'][-2:]
            elif kind == 'wrong-entrypoint': launches['memory'][3] = str(f.old_dir / 'coordination.py')
            elif kind == 'python-code': launches['memory'][1:3] = ['-c', 'print("false success")']
            elif kind == 'repeated-flag': launches['memory'].insert(1, '-I')
            writes.update({'binding.json': encoded(binding), 'retained-argv.json': encoded(launches)})
            if kind == 'duplicate-binding': writes['binding.json'] = b'{"enabled":false,' + writes['binding.json'][1:]
            self.repin(f, writes)
            with self.subTest(kind=kind): self.refuse(f)

    def test_ambiguous_previous_hooks_refuse_without_settings_publication(self):
        for role in ('memory', 'coordination'):
            settings = dict(hooks=dict(SessionStart=[dict(hooks=[dict(command='old ' + role), dict(command='old ' + role)])]))
            f = self.fixture(settings=settings)
            with self.subTest(role=role): self.refuse(f)


if __name__ == '__main__':
    unittest.main()
