"""Real collector/isolation seam; synthetic inputs never qualify an actual W run."""

import contextlib
import hashlib
import json
import os
from pathlib import Path
import pwd
import sys
import tempfile
import unittest
from unittest.mock import patch


MODEL_PACKAGES = {'numpy', 'fastembed', 'onnxruntime', 'tokenizers'}
SELECTORS = ('CAIRN_EMBEDDING_WORKER', 'CAIRN_SEMANTIC_WORKER', 'CAIRN_SEMANTIC_STREAM_WORKER',
             'CAIRN_SEMANTIC_ACCEPTANCE_REQUIRED', 'CAIRN_TEST_DATABASE_URL', 'CAIRN_DATABASE_URL')


class _NoModelImports:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in MODEL_PACKAGES:
            raise AssertionError('model package imported by collector integration test: ' + fullname)


@contextlib.contextmanager
def _without_model_imports():
    blocker = _NoModelImports()
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)


with _without_model_imports():
    import capture_semantic_fixtures as collector
    import semantic_acceptance_isolation as isolation
    import semantic_fixture_cases as cases
    import test_semantic_acceptance_isolation as fixture_inputs


OBSERVE = """
import json,os,pwd,socket,sys
from pathlib import Path
class NoModelImports:
 def find_spec(self,fullname,path=None,target=None):
  if fullname.split('.')[0] in {'numpy','fastembed','onnxruntime','tokenizers'}:
   raise AssertionError('model package imported by collector child: '+fullname)
sys.meta_path.insert(0,NoModelImports())
sys.path.insert(0,sys.argv[1])
import capture_semantic_fixtures as collector
try:
 collector._isolation_guard(json.loads(sys.argv[2]),json.loads(sys.argv[3]),sys.argv[4],profile=sys.argv[5])
except collector.EvidenceError as error:
 print(str(error),file=sys.stderr)
 raise SystemExit(2)
home=pwd.getpwuid(os.getuid()).pw_dir
print(json.dumps(dict(environment=dict(os.environ),cwd=os.getcwd(),home=home,
 expanduser=os.path.expanduser('~'),probe_inode=(Path(home)/'.semantic-isolation-probe').stat().st_ino,
 network_namespace=os.readlink('/proc/self/ns/net'),interfaces=[name for _,name in socket.if_nameindex()])))
"""


def _observation_command(proof, contract, profile):
    return ['/usr/bin/python3', '-I', '-B', '-S', '-c', OBSERVE,
            str(Path(collector.__file__).parent), json.dumps(proof), json.dumps(contract),
            os.readlink('/proc/self/ns/net'), profile]


class _ParentHomeLookup:
    """Translate only B1's parent-side task policy; child passwd lookup stays real."""
    def __init__(self, home):
        self.home = home

    def getpwuid(self, uid):
        if uid != os.getuid():
            raise AssertionError('home translation is restricted to the test owner')
        entry = pwd.getpwuid(uid)
        return pwd.struct_passwd((*entry[:5], str(self.home), entry.pw_shell))


class CollectorIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(_without_model_imports())
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='cairn-semantic-collector-')))
        self.read_only = [Path(collector.__file__), Path(cases.__file__)]

    @contextlib.contextmanager
    def _profile_inputs(self, profile):
        home = self.root / profile / 'owner'
        fixture_root = home / '.cache/cairn-hybrid-execution-1249/semantic-S-B1/scenario'
        fixture_root.mkdir(mode=0o700, parents=True)
        output = self.root / profile / 'output'
        output.mkdir(mode=0o700)
        arguments = dict(profile=profile, read_only_paths=self.read_only, output_dir=output / 'isolation')
        expected = {'PATH': '/usr/bin:/bin', 'HF_HUB_OFFLINE': '1',
                    'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false', 'PWD': '/'}
        with contextlib.ExitStack() as substitutions:
            frozen = before = None
            if profile == 'derived-W':
                frozen, task, seal_pin = fixture_inputs.SemanticIsolationTest().synthetic_w(fixture_root)
                frozen.chmod(0o700)  # Overlap controls must reach the overlap check, not a mode refusal.
                substitutions.enter_context(patch.object(collector, 'pwd', _ParentHomeLookup(home)))
                substitutions.enter_context(patch.object(isolation, 'SEALED_ORACLE_SHA256', seal_pin))
                before = isolation.check_sealed_oracle(frozen)
                manifest = json.loads((frozen / 'inventory/experiment-manifest.json').read_text())
                expected = dict(manifest['sandbox_environment'], PWD='/oracle-1276-180s')
                arguments.update(sealed_root=frozen, task_root=task, name='collector-child')
            yield {'arguments': arguments, 'output': output, 'expected_exec': expected,
                   'frozen': frozen, 'sealed_before': before}

    def _observe_owned_backing(self, boundary, fixture):
        backing = boundary.home_dir.parent if fixture['frozen'] is not None else boundary.home_dir
        info = backing.lstat()
        self.assertEqual(info.st_uid, os.getuid())
        self.assertEqual(info.st_mode & 0o777, 0o700)
        fixture['backing'] = str(backing)
        fixture['identity'] = dict(inode=info.st_ino, device=info.st_dev, owner=info.st_uid, mode=0o700)

    def _assert_cleanup(self, fixture, invocations):
        output = fixture['output']
        cleanup = collector._cleanup_evidence(output, invocations)
        self.assertEqual(cleanup['backing'], fixture['backing'])
        self.assertEqual(cleanup['initial_identity'], fixture['identity'])
        self.assertFalse(Path(cleanup['backing']).exists())
        self.assertEqual(cleanup['isolation_proof_sha256'], hashlib.sha256(
            (output / 'isolation/isolation-proof.json').read_bytes()).hexdigest())
        final = json.loads((output / cleanup['final_receipt']).read_text())
        self.assertTrue(final['all_observed_processes_exited'])
        self.assertTrue(final['backing_removed'])
        self.assertEqual(final['errors'], [])
        if fixture['frozen'] is not None:
            self.assertTrue(cleanup['sealed_oracle_unchanged'])
            self.assertEqual(isolation.check_sealed_oracle(fixture['frozen']), fixture['sealed_before'])

    def test_profiles_observe_real_contract_and_joined_cleanup(self):
        for profile in ('default', 'derived-W'):
            with self.subTest(profile=profile), self._profile_inputs(profile) as fixture:
                with collector._collector_isolation(isolation, **fixture['arguments']) as boundary:
                    self._observe_owned_backing(boundary, fixture)
                    contract, proof = boundary.command_contract(), boundary.proof
                    observed = boundary.run(_observation_command(proof, contract, profile), timeout=10,
                        stdout_limit=65536, role='collector-integration', mode='preflight')
                    self.assertEqual(observed.returncode, 0, observed.stderr)
                    facts = json.loads(observed.stdout)
                    self.assertEqual(facts['environment'], dict(fixture['expected_exec'], LC_CTYPE='C.UTF-8'))
                    self.assertEqual(facts['cwd'], fixture['expected_exec']['PWD'])
                    expected_home = '/sandbox/home' if profile == 'derived-W' else pwd.getpwuid(os.getuid()).pw_dir
                    self.assertEqual(facts['home'], expected_home)
                    self.assertEqual(facts['expanduser'], expected_home)
                    self.assertEqual(facts['probe_inode'], proof['home_probe_inode'])
                    self.assertNotEqual(facts['network_namespace'], os.readlink('/proc/self/ns/net'))
                    self.assertEqual(facts['interfaces'], ['lo'])
                    self.assertFalse(set(SELECTORS).intersection(facts['environment']))
                    self.assertEqual(observed.invocation['contract'], contract)
                    invocations = [observed.invocation]
                    other_profile = 'derived-W' if profile == 'default' else 'default'
                    wrong_probe = dict(proof, home_probe_inode=proof['home_probe_inode'] + 1)
                    controls = (
                        ('environment', ['/usr/bin/env', 'CAIRN_N1_UNDECLARED=refuse',
                            *_observation_command(proof, contract, profile)]),
                        ('profile', _observation_command(proof, contract, other_profile)),
                        ('home', _observation_command(wrong_probe, contract, profile)),
                    )
                    for reason, command in controls:
                        with self.subTest(refusal=reason):
                            refused = boundary.run(command, timeout=10, role='collector-integration', mode='preflight')
                            invocations.append(refused.invocation)
                            self.assertEqual(refused.returncode, 2)
                            self.assertIn(reason.encode(), refused.stderr)
                    if profile == 'derived-W':
                        prefix = json.loads((fixture['frozen'] / 'inventory/experiment-manifest.json').read_text())['sandbox_prefix']
                        self.assertEqual(len(prefix), 97)
                        expected = list(prefix)
                        expected[34] = str(fixture['arguments']['task_root'] / 'collector-child')
                        expected[-2:-2] = [part for path in self.read_only for part in ('--ro-bind', str(path), str(path))] + [
                            '--ro-bind', str(fixture['output'] / 'isolation/read-only-probe'), '/semantic-read-only-probe']
                        self.assertEqual(proof['effective_prefix'], expected)
                        self.assertEqual(proof['backing_argument_diff'], [
                            {'index': 34, 'original': prefix[34], 'derived': expected[34]}])
                self._assert_cleanup(fixture, invocations)

    def test_w_setup_refusals_leave_no_child_or_frozen_changes(self):
        with self._profile_inputs('derived-W') as fixture:
            frozen = fixture['frozen']
            self.assertEqual(frozen.stat().st_mode & 0o777, 0o700)
            for changes, reason in (
                    ({'task_root': frozen, 'name': 'disposable'}, 'task scratch root overlaps sealed or protected live state'),
                    ({'output_dir': frozen / 'forbidden-output'}, 'new W evidence must be outside the frozen oracle root')):
                with self.subTest(reason=reason), self.assertRaisesRegex(isolation.IsolationError, f'^{reason}$'):
                    with collector._collector_isolation(isolation, **dict(fixture['arguments'], **changes)):
                        pass
                self.assertFalse((frozen / 'disposable').exists())
                self.assertFalse((frozen / 'forbidden-output').exists())
                self.assertFalse((fixture['arguments']['task_root'] / 'collector-child').exists())
                self.assertEqual(isolation.check_sealed_oracle(frozen), fixture['sealed_before'])

    def test_profile_timeouts_preserve_partial_bytes_and_joined_cleanup(self):
        command = ['/usr/bin/python3', '-I', '-B', '-S', '-c',
                   "import os,time;os.write(1,b'partial\\x00stdout');os.write(2,b'partial\\xffstderr');time.sleep(60)"]
        for profile in ('default', 'derived-W'):
            with self.subTest(profile=profile), self._profile_inputs(profile) as fixture:
                with self.assertRaises(isolation.IsolationError) as failed:
                    with collector._collector_isolation(isolation, **fixture['arguments']) as boundary:
                        self._observe_owned_backing(boundary, fixture)
                        boundary.run(command, timeout=1, role='collector-integration', mode='fixture')
                self.assertEqual(failed.exception.code, 'timeout')
                evidence = failed.exception.evidence
                self.assertEqual(evidence['failure_code'], 'timeout')
                self.assertEqual(evidence['stdout'], b'partial\x00stdout')
                self.assertEqual(evidence['stderr'], b'partial\xffstderr')
                self.assertTrue(evidence['processes_joined'])
                self._assert_cleanup(fixture, [evidence['invocation']])


if __name__ == '__main__':
    unittest.main()
