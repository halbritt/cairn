"""Exercise checked Agy publication through the real pinned Go CLI bridge."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / 'scripts/stage_lifecycle_hooks.py'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


class AgyLayerPublicationProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = tempfile.TemporaryDirectory(prefix='cairn-agy-publisher-tools-')
        cls.addClassCleanup(cls.tools.cleanup)
        cls.stager = Path(cls.tools.name) / 'cairn-stage-lifecycle'
        built = Path(cls.tools.name) / 'go-build-output'
        result = subprocess.run(['go', 'build', '-buildvcs=false', '-o', str(built),
                                 './cmd/cairn-stage-lifecycle'], cwd=ROOT,
                                capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr.decode())
        info = built.stat()
        print('build output metadata', oct(stat.S_IMODE(info.st_mode)), info.st_nlink,
              flush=True)
        cls.stager.write_bytes(built.read_bytes())
        cls.stager.chmod(0o500)
        built.unlink()
        cls.image_sha = hashlib.sha256(cls.stager.read_bytes()).hexdigest()
        print('actual Agy publisher image', cls.stager, cls.image_sha, flush=True)

    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='cairn-agy-layer-process-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.account = self.root / 'account'
        self.workspace = self.root / 'workspace'
        self.account.mkdir(mode=0o700)
        self.workspace.mkdir(mode=0o755)
        self.paths = {'account': self.account / '.gemini/config/hooks.json',
                      'project': self.workspace / '.agents/hooks.json'}
        self.original = (b'{\n "credential_selector" : "/owned/existing.token",'
                         b'\n "opaque" : 900719925474099312345,'
                         b'\n "third-party" : {"PreInvocation":[{"type":"command","command":"other-owner"}]},'
                         b'\n "cairn-coordination" : {}\n}\n')
        (self.account / '.gemini').mkdir(mode=0o755)
        for path in self.paths.values():
            path.parent.mkdir(mode=0o755)
            path.write_bytes(self.original)
            path.chmod(0o640)
        self.group = {event: [{'type': 'command', 'command': '/owned/cairn hook --event ' + event,
                               'timeout': 15}] for event in ['PreInvocation', 'Stop']}
        self.prepared = self.root / 'prepared.json'
        self.prepared.write_bytes(encoded({'cairn-coordination': self.group,
                                           'credential_selector': 'must-not-replace-current',
                                           'opaque': 0}))
        self.prepared.chmod(0o600)

    def snapshot(self):
        result = {}
        for path in sorted(self.root.rglob('*')):
            info = path.lstat()
            raw = None
            if stat.S_ISREG(info.st_mode) and info.st_mode & stat.S_IRUSR:
                raw = path.read_bytes()
            elif stat.S_ISLNK(info.st_mode):
                raw = os.readlink(path)
            result[str(path.relative_to(self.root))] = (
                stat.S_IMODE(info.st_mode), info.st_ino, info.st_nlink, raw)
        return result

    def publish(self, layer='account', *, image_sha=None, prepared_sha=None, workspace=None,
                replace_commands=None):
        plan = {'schema': 'cairn.agy-layer-install/1',
                'settings': {'path': str(self.prepared),
                             'sha256': prepared_sha or hashlib.sha256(self.prepared.read_bytes()).hexdigest()}}
        if replace_commands is not None:
            plan['replace_commands'] = replace_commands
        return subprocess.run([
            sys.executable, '-I', '-S', '-B', str(BRIDGE), 'publish-agy',
            '--stager', str(self.stager), '--stager-sha256', image_sha or self.image_sha,
            '--account-home', str(self.account), '--workspace', workspace or str(self.workspace),
            '--layer', layer,
        ], input=encoded(plan), cwd=ROOT, capture_output=True, timeout=30)

    def assert_refuses_unchanged(self, **kwargs):
        before = self.snapshot()
        result = self.publish(**kwargs)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(self.snapshot(), before, result.stderr.decode())
        return result

    def check_success(self, layer):
        other = 'project' if layer == 'account' else 'account'
        before = self.snapshot()
        result = self.publish(layer)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        selection = json.loads(result.stdout)
        self.assertEqual(selection['path'], str(self.paths[layer]))
        self.assertEqual(selection['layer'], layer)
        self.assertTrue(selection['changed'])
        actual = self.paths[layer].read_bytes()
        self.assertEqual(selection['sha256'], hashlib.sha256(actual).hexdigest())
        self.assertEqual(json.loads(actual)['cairn-coordination'], self.group)
        for key, value in json.loads(self.original).items():
            if key != 'cairn-coordination':
                self.assertEqual(json.loads(actual)[key], value)
        self.assertIn(b'"opaque" : 900719925474099312345', actual)
        self.assertEqual(stat.S_IMODE(self.paths[layer].stat().st_mode), 0o640)
        after = self.snapshot()
        for name, value in before.items():
            if name != str(self.paths[layer].relative_to(self.root)):
                self.assertEqual(after[name], value)
        self.assertEqual(self.paths[other].read_bytes(), self.original)
        stable = self.snapshot()
        repeat = self.publish(layer)
        self.assertEqual(repeat.returncode, 0, repeat.stderr.decode())
        self.assertFalse(json.loads(repeat.stdout)['changed'])
        self.assertEqual(self.snapshot(), stable)

    def test_account_selection_preserves_current_data_modes_and_noop(self):
        self.check_success('account')

    def test_project_selection_preserves_current_data_modes_and_noop(self):
        self.check_success('project')

    def test_identical_other_layer_dispatch_refuses_before_write(self):
        self.paths['project'].write_bytes(encoded({'cairn-coordination': self.group}))
        result = self.assert_refuses_unchanged()
        self.assertIn(b'Agy nonselected layer conflict: Cairn dispatch or unknown hooks',
                      result.stderr)

    def test_different_other_layer_dispatch_also_refuses(self):
        self.paths['account'].write_bytes(encoded({'cairn-coordination': {
            'Stop': [{'type': 'command', 'command': 'another-cairn-selection'}]}}))
        self.assert_refuses_unchanged(layer='project')

    def test_malformed_other_layer_refuses(self):
        self.paths['project'].write_bytes(b'{broken')
        self.assert_refuses_unchanged()

    def test_duplicate_other_layer_fields_refuse(self):
        self.paths['project'].write_bytes(b'{"cairn-coordination":{},"cairn-coordination":{}}')
        self.assert_refuses_unchanged()

    def test_symlink_other_layer_refuses(self):
        self.paths['project'].unlink()
        self.paths['project'].symlink_to(self.prepared)
        self.assert_refuses_unchanged()

    def test_hardlinked_layers_refuse(self):
        self.paths['project'].unlink()
        os.link(self.paths['account'], self.paths['project'])
        self.assert_refuses_unchanged()

    def test_unreadable_other_layer_refuses_without_mode_repair(self):
        path = self.paths['project']
        original = path.read_bytes()
        path.chmod(0)
        try:
            self.assert_refuses_unchanged()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0)
        finally:
            path.chmod(0o640)
        self.assertEqual(path.read_bytes(), original)

    def test_unsafe_other_ancestor_refuses_without_chmod(self):
        self.paths['project'].parent.chmod(0o777)
        self.assert_refuses_unchanged()
        self.assertEqual(stat.S_IMODE(self.paths['project'].parent.stat().st_mode), 0o777)

    def test_wrong_explicit_go_image_pin_refuses(self):
        self.assert_refuses_unchanged(image_sha='0' * 64)

    def test_wrong_prepared_settings_pin_refuses(self):
        self.assert_refuses_unchanged(prepared_sha='0' * 64)

    def test_cairn_word_is_not_permission_to_drop_an_unrelated_hook(self):
        current = json.loads(self.original)
        current['cairn-coordination'] = {'PreInvocation': [{
            'type': 'command', 'command': '/usr/bin/printf "cairn docs"',
            'owner': 'unrelated',
        }]}
        self.paths['account'].write_bytes(encoded(current))
        self.assert_refuses_unchanged()

    def test_complete_prepared_group_can_preserve_an_unrelated_hook(self):
        unrelated = {'type': 'command', 'command': '/usr/bin/printf "cairn docs"',
                     'owner': 'unrelated'}
        current = json.loads(self.original)
        current['cairn-coordination'] = {'PreInvocation': [unrelated]}
        self.paths['account'].write_bytes(encoded(current))
        self.group['PreInvocation'].insert(0, unrelated)
        self.prepared.write_bytes(encoded({'cairn-coordination': self.group}))
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        actual = json.loads(self.paths['account'].read_bytes())
        self.assertEqual(actual['cairn-coordination']['PreInvocation'][0], unrelated)
        self.assertEqual(actual['opaque'], 900719925474099312345)

    def test_explicit_prior_command_replaces_only_that_hook_and_can_repeat(self):
        unrelated = {'type': 'command', 'command': '/usr/bin/printf "cairn docs"',
                     'owner': 'unrelated'}
        previous = '/owned/retained-python-coordination --event PreInvocation'
        current = json.loads(self.original)
        current['cairn-coordination'] = {'PreInvocation': [unrelated, {
            'type': 'command', 'command': previous, 'timeout': 15}]}
        self.paths['account'].write_bytes(encoded(current))
        self.group['PreInvocation'].insert(0, unrelated)
        self.prepared.write_bytes(encoded({'cairn-coordination': self.group}))
        result = self.publish(replace_commands=[previous])
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        actual = json.loads(self.paths['account'].read_bytes())
        self.assertEqual(actual['cairn-coordination'], self.group)
        self.assertEqual(actual['credential_selector'], '/owned/existing.token')
        stable = self.snapshot()
        repeated = self.publish(replace_commands=[previous])
        self.assertEqual(repeated.returncode, 0, repeated.stderr.decode())
        self.assertFalse(json.loads(repeated.stdout)['changed'])
        self.assertEqual(self.snapshot(), stable)

    def test_prior_owned_looking_hook_is_not_removed_without_explicit_intent(self):
        current = json.loads(self.original)
        current['cairn-coordination'] = {'PreInvocation': [{
            'type': 'command', 'command': '/owned/cairn-old-selection'}]}
        self.paths['account'].write_bytes(encoded(current))
        self.assert_refuses_unchanged()

    def test_noncanonical_workspace_refuses(self):
        self.assert_refuses_unchanged(workspace=str(self.workspace) + '/.')


if __name__ == '__main__':
    unittest.main()
