"""Synthetic final-artifact custody tests. No original case, provider or cgroup."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import trial_legacy_pregrade as pregrade
import trial_task_eval as task
from trial_attempt_custody import AttemptBaseline


class PregradeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name) / 'out'
        self.out.mkdir(mode=0o700)
        self.run_id = 'synthetic.none.s0'
        self.base = self.out / 'runs' / self.run_id
        (self.base / 'work').mkdir(parents=True)
        self.cwd = self.base / 'work' / 'repo'
        self.cwd.mkdir()
        (self.cwd / 'keep.txt').write_bytes(b'before\n')
        (self.cwd / 'delete.txt').write_bytes(b'delete me\n')
        (self.cwd / 'setup.sh').write_text('git init -q\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Synthetic')
        self.git('config', 'user.email', 'synthetic@example.invalid')
        self.git('add', '.')
        self.git('commit', '-qm', 'initial')
        self.git('tag', 'eval-base')
        snapshot = {}
        for path in (self.cwd / 'keep.txt', self.cwd / 'delete.txt'):
            snapshot[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        (self.base / 'work' / '.eval-snapshot.json').write_text(json.dumps(snapshot))
        self.case = dict(id='synthetic', cwd='repo')
        self.baseline = AttemptBaseline.capture(self.base, self.case, 'none', 0)
        self.stream = b'{"type":"result","result":"done"}\n'
        self.prompt = 'Make the synthetic change.'
        reviewed = dict(schema='cairn.writer-reviewed-identity/2',
                        kernel=dict(release='kernel', version='build'),
                        bwrap=dict(path='/usr/bin/bwrap', sha256='a'*64),
                        source_scope=dict(kernel='reviewed-kernel', bwrap='reviewed-bwrap'),
                        execution='sealed_memfd')
        self.writer_record = dict(
            schema='cairn.legacy-writer-release/1',
            attempt=dict(run_id=self.run_id, workspace=str(self.cwd)),
            state='released', release='released', bootstrap=dict(state='member'), close_errors=[],
            complete_descendant_custody=False,
            writer_quiescence=dict(state='namespace_init_exited',
                                   basis='reviewed_identity_source_zap_before_pidfd_notify',
                                   scope=reviewed['source_scope']),
            init=dict(state='bound', pid_namespace=dict(inode=123, dev=1)), init_exit='exited',
            launch_evidence=dict(namespaces=dict(pid=123)),
            runtime_identity=dict(kernel=reviewed['kernel'], bwrap=reviewed['bwrap']),
            reviewed_identity=reviewed,
            execution_binding=dict(mode='sealed_memfd', sha256='a'*64))

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.cwd), *args], stderr=subprocess.DEVNULL)

    def capture(self, writer=None):
        return pregrade.capture(base=self.base, case=self.case, arm='none', seed=0,
                                baseline=self.baseline, writer_record=writer or self.writer_record,
                                stream_bytes=self.stream, prompt=self.prompt)

    def load(self, pin):
        path = self.out / pin['record_path']
        raw = path.read_bytes()
        self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (pin['bytes'], pin['sha256']))
        return json.loads(raw)

    def test_captures_actor_bytes_modes_deletions_index_refs_and_subject(self):
        (self.cwd / 'keep.txt').write_bytes(b'after!\n')
        os.chmod(self.cwd / 'keep.txt', 0o755)
        (self.cwd / 'delete.txt').unlink()
        (self.cwd / 'new.txt').write_bytes(b'new artifact\n')
        self.git('add', 'keep.txt', 'delete.txt', 'new.txt')
        pin = self.capture()
        doc = self.load(pin)
        self.assertEqual(doc['schema'], pregrade.SCHEMA)
        self.assertEqual(doc['attempt'], dict(run_id=self.run_id, case='synthetic', arm='none', seed=0,
                                              workspace=str(self.cwd)))
        entries = {base64.b64decode(row['path_b64']): row for row in doc['workspace']['entries']}
        self.assertEqual(base64.b64decode(entries[b'keep.txt']['content_b64']), b'after!\n')
        self.assertEqual(entries[b'keep.txt']['mode'], 0o755)
        self.assertEqual(base64.b64decode(entries[b'new.txt']['content_b64']), b'new artifact\n')
        self.assertEqual(doc['workspace']['deletions'], [base64.b64encode(b'delete.txt').decode()])
        git_rows = {base64.b64decode(row['path_b64']): row for row in doc['git']['index_refs']}
        self.assertIn(b'worktree/index', git_rows)
        self.assertIn(b'worktree/HEAD', git_rows)
        self.assertTrue(any(path.startswith(b'common/refs/') for path in git_rows))
        self.assertEqual(doc['stream'], dict(bytes=len(self.stream), sha256=hashlib.sha256(self.stream).hexdigest()))
        self.assertEqual(doc['prompt']['sha256'], hashlib.sha256(self.prompt.encode()).hexdigest())

    def test_release_without_namespace_init_proof_refuses(self):
        writer = dict(self.writer_record)
        writer['writer_quiescence'] = dict(state='not_established', reason='init_unbound')
        with self.assertRaisesRegex(pregrade.PregradeError, 'writer_quiescence_unproved'):
            self.capture(writer)
        self.assertFalse((self.out / 'final-artifacts').exists())

    def test_mutation_between_snapshot_passes_refuses_without_pin(self):
        original = pregrade._open_regular
        mutated = False

        def read_then_mutate(path, before, *, remaining):
            nonlocal mutated
            data, after = original(path, before, remaining=remaining)
            if path == self.cwd / 'keep.txt' and not mutated:
                mutated = True
                path.write_bytes(b'changed while capturing\n')
            return data, after

        with patch.object(pregrade, '_open_regular', side_effect=read_then_mutate):
            with self.assertRaisesRegex(pregrade.PregradeError, 'changed_during_capture|file_changed|directory_changed'):
                self.capture()
        self.assertFalse((self.out / 'final-artifacts').exists())

    def test_git_worktree_baseline_binds_common_refs_and_captures_worktree_index(self):
        out = Path(self.temp.name) / 'worktree-output'
        base = out / 'runs' / self.run_id
        work = base / 'work'
        shared = work / 'shared'
        cwd = work / 'repo'
        shared.mkdir(parents=True)
        (shared / 'sample.txt').write_bytes(b'worktree original\n')
        env = dict(os.environ, GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                   GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid')
        for command in (['git', 'init', '-q'], ['git', 'add', '.'], ['git', 'commit', '-qm', 'initial'],
                        ['git', 'tag', 'eval-base']):
            subprocess.run(command, cwd=shared, env=env, check=True, capture_output=True)
        subprocess.run(['git', 'worktree', 'add', '-q', str(cwd), 'HEAD'], cwd=shared,
                       env=env, check=True, capture_output=True)
        snapshot = dict({'sample.txt': hashlib.sha256(b'worktree original\n').hexdigest()})
        (work / '.eval-snapshot.json').write_text(json.dumps(snapshot))
        baseline = AttemptBaseline.capture(base, self.case, 'none', 0)
        proof = dict(self.writer_record, attempt=dict(run_id=self.run_id, workspace=str(cwd)))
        pin = pregrade.capture(base=base, case=self.case, arm='none', seed=0, baseline=baseline,
                               writer_record=proof, stream_bytes=self.stream, prompt=self.prompt)
        document = json.loads((out / pin['record_path']).read_bytes())
        self.assertEqual(document['git']['common_repository'], baseline.git_common)
        git_rows = {base64.b64decode(row['path_b64']): row for row in document['git']['index_refs']}
        self.assertIn(b'worktree/commondir', git_rows)
        self.assertIn(b'worktree/index', git_rows)
        self.assertTrue(any(path.startswith(b'common/refs/') for path in git_rows))

    def test_attempt_or_repository_identity_mismatch_refuses(self):
        writer = dict(self.writer_record)
        writer['attempt'] = dict(run_id='other.none.s0', workspace=str(self.cwd))
        with self.assertRaisesRegex(pregrade.PregradeError, 'attempt_mismatch'):
            self.capture(writer)
        git_dir = self.cwd / '.git'
        moved = self.cwd / '.git.old'
        git_dir.rename(moved)
        subprocess.check_call(['cp', '-a', str(moved), str(git_dir)])
        with self.assertRaisesRegex(pregrade.PregradeError, 'repository_changed'):
            self.capture()

    def test_run_agent_persists_snapshot_pin_before_grade(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            output.mkdir(mode=0o700)
            case = dict(id='synthetic', category='synthetic', provenance=dict(type='synthetic'),
                        cwd='repo', workspace='unused', wordings=dict(task='Change the synthetic file.'),
                        expected=[], correct=[], mistake=[])
            args = SimpleNamespace(harness='claude', model='inert', wording='task', max_turns=2, timeout=10,
                                   semantic_recall=[], original_limits=True, _original_writer=dict(
                                       delegated='/synthetic/cgroup', controls={}, reviewed={}))
            reviewed = dict(schema='cairn.writer-reviewed-identity/2',
                            kernel=dict(release='kernel', version='build'),
                            bwrap=dict(path='/usr/bin/bwrap', sha256='b'*64),
                            source_scope=dict(kernel='reviewed-kernel', bwrap='reviewed-bwrap'),
                            execution='sealed_memfd')
            runtime = dict(kernel=reviewed['kernel'], bwrap=reviewed['bwrap'])

            class Leaf:
                parent = '/synthetic/cgroup'
                pass_fds = ()
                def __init__(self, *_args, **_kwargs):
                    self.group = SimpleNamespace(relative='synthetic-leaf')
                    self.identity = [1, 2]
                    self.owner_namespaces = dict(pid=3, mnt=4)
                    self.cgroup_dev = 5
                    self.controls = {}
                def probe(self, command): return command, ('probe-source', '/tmp/trial/probe.py', 'ro')
                def wrap(self, command): return command
                def observe_init(self, _child): pass
                def finish(self, _deadline): return {}

            def prepare(_case, root):
                cwd = root / 'repo'
                cwd.mkdir()
                (cwd / 'sample.txt').write_bytes(b'start\n')
                env = dict(os.environ, GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                           GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid')
                for command in (['git', 'init', '-q'], ['git', 'add', '.'], ['git', 'commit', '-qm', 'initial'],
                                ['git', 'tag', 'eval-base']):
                    subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True)
                (root / '.eval-snapshot.json').write_text(json.dumps({'sample.txt': hashlib.sha256(b'start\n').hexdigest()}))

            def release(run_id, workspace, leaf, observed, custody):
                return dict(schema='cairn.legacy-writer-release/1',
                            attempt=dict(run_id=run_id, workspace=str(workspace)),
                            state='released', release='released', bootstrap=dict(state='member'), close_errors=[],
                            complete_descendant_custody=False,
                            writer_quiescence=dict(state='namespace_init_exited',
                                basis='reviewed_identity_source_zap_before_pidfd_notify', scope=reviewed['source_scope']),
                            init=dict(state='bound', pid_namespace=dict(inode=123, dev=1)), init_exit='exited',
                            launch_evidence=dict(namespaces=dict(pid=123)), runtime_identity=runtime,
                            reviewed_identity=reviewed,
                            execution_binding=dict(mode='sealed_memfd', sha256='b'*64))

            stream = (json.dumps(dict(type='result', result='done', is_error=False))+'\n').encode()

            def grade(_case, _ctx):
                base = output / 'runs' / 'synthetic.none.s0'
                receipt = json.loads((base / 'writer-release.json').read_text())
                self.assertEqual(receipt['final_artifact_custody']['schema'], 'cairn.legacy-pregrade-snapshot-pin/1')
                path = output / receipt['final_artifact_custody']['record_path']
                self.assertTrue(path.is_file())
                return dict(outcome='correct', correct=[], mistake=[])

            custody = dict(classification='observed', leader_reaped=True,
                           cleanup=dict(status='observed_clear'), observed_wait_status=0,
                           observed_returncode=0, stdout_eof=True, stderr_eof=True)
            with patch.object(task, 'prepare_workspace', side_effect=prepare), \
                 patch.object(task, 'load_corpus', return_value=[]), \
                 patch.object(task, 'sandbox_command', return_value=['/usr/bin/true']), \
                 patch.object(task.writer_release, 'WriterLeaf', Leaf), \
                 patch.object(task.writer_release, 'result', side_effect=release), \
                 patch.object(task, 'capture', return_value=(stream, b'', 0, [])), \
                 patch.object(task, 'read_custody', return_value=custody), \
                 patch.object(task.legacy_limits, 'assess', return_value=dict(status='within_original_limits')), \
                 patch.object(task, 'grade', side_effect=grade):
                row = task.run_agent(case, 'none', 0, 0, args, {}, output)
            self.assertEqual(row['final_artifact_custody']['schema'], 'cairn.legacy-pregrade-snapshot-pin/1')
            self.assertEqual(row['writer_release']['final_artifact_custody'], row['final_artifact_custody'])

    def test_codex_weekly_run_agent_uses_same_reviewed_writer_path(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            output.mkdir(mode=0o700)
            auth = Path(temp) / 'auth.json'
            auth.write_text('{}')
            case = dict(id='synthetic', category='synthetic', provenance=dict(type='synthetic'),
                        cwd='repo', workspace='unused', wordings=dict(task='Change the synthetic file.'),
                        expected=[], correct=[], mistake=[])
            args = SimpleNamespace(harness='codex', model='gpt-6-luna', wording='task', max_turns=None, timeout=10,
                                   reasoning_effort='high', codex_auth_file=str(auth), codex_auth_sha256='c'*64,
                                   codex_install=str(Path(temp)/'codex'), semantic_recall=[], codex_weekly_limits=True,
                                   _original_writer=dict(delegated='/synthetic/cgroup', controls={}, reviewed={}))
            reviewed = dict(schema='cairn.writer-reviewed-identity/2',
                            kernel=dict(release='kernel', version='build'),
                            bwrap=dict(path='/usr/bin/bwrap', sha256='c'*64),
                            source_scope=dict(kernel='reviewed-kernel', bwrap='reviewed-bwrap'),
                            execution='sealed_memfd')
            runtime = dict(kernel=reviewed['kernel'], bwrap=reviewed['bwrap'])

            class Leaf:
                parent = '/synthetic/cgroup'
                pass_fds = ()
                def __init__(self, *_args, **_kwargs):
                    self.group = SimpleNamespace(relative='synthetic-leaf')
                    self.identity = [1, 2]
                    self.owner_namespaces = dict(pid=3, mnt=4)
                    self.cgroup_dev = 5
                    self.controls = {}
                def probe(self, command): return command, ('probe-source', '/tmp/trial/probe.py', 'ro')
                def wrap(self, command): return command
                def observe_init(self, _child): pass
                def finish(self, _deadline): return {}

            def prepare(_case, root):
                cwd = root / 'repo'
                cwd.mkdir()
                (cwd / 'sample.txt').write_bytes(b'start\n')
                env = dict(os.environ, GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                           GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid')
                for command in (['git', 'init', '-q'], ['git', 'add', '.'], ['git', 'commit', '-qm', 'initial'],
                                ['git', 'tag', 'eval-base']):
                    subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True)
                (root / '.eval-snapshot.json').write_text(json.dumps({'sample.txt': hashlib.sha256(b'start\n').hexdigest()}))

            def release(run_id, workspace, leaf, observed, custody):
                return dict(schema='cairn.legacy-writer-release/1',
                            attempt=dict(run_id=run_id, workspace=str(workspace)),
                            state='released', release='released', bootstrap=dict(state='member'), close_errors=[],
                            complete_descendant_custody=False,
                            writer_quiescence=dict(state='namespace_init_exited',
                                basis='reviewed_identity_source_zap_before_pidfd_notify', scope=reviewed['source_scope']),
                            init=dict(state='bound', pid_namespace=dict(inode=123, dev=1)), init_exit='exited',
                            launch_evidence=dict(namespaces=dict(pid=123)), runtime_identity=runtime,
                            reviewed_identity=reviewed,
                            execution_binding=dict(mode='sealed_memfd', sha256='c'*64))

            stream = (json.dumps(dict(type='turn.completed', usage={}))+'\n').encode()
            custody = dict(classification='observed', leader_reaped=True,
                           cleanup=dict(status='observed_clear'), observed_wait_status=0,
                           observed_returncode=0, stdout_eof=True, stderr_eof=True)

            def grade(_case, _ctx):
                base = output / 'runs' / 'synthetic.none.s0'
                receipt = json.loads((base / 'writer-release.json').read_text())
                self.assertEqual(receipt['final_artifact_custody']['schema'], 'cairn.legacy-pregrade-snapshot-pin/1')
                self.assertTrue((output / receipt['final_artifact_custody']['record_path']).is_file())
                return dict(outcome='correct', correct=[], mistake=[])

            with patch.object(task, 'prepare_workspace', side_effect=prepare), \
                 patch.object(task, 'load_corpus', return_value=[]), \
                 patch.object(task, 'sandbox_command', return_value=['/usr/bin/true']), \
                 patch.object(task.writer_release, 'WriterLeaf', Leaf), \
                 patch.object(task.writer_release, 'result', side_effect=release), \
                 patch.object(task, 'capture', return_value=(stream, b'', 0, [])), \
                 patch.object(task, 'read_custody', return_value=custody), \
                 patch.object(task.codex_account_selection, 'verify_auth_file'), \
                 patch.object(task.codex_weekly_limits, 'assess', return_value=dict(status='within_codex_weekly_limits')), \
                 patch.object(task, 'grade', side_effect=grade):
                row = task.run_agent(case, 'none', 0, 0, args, {}, output)
            self.assertEqual(row['final_artifact_custody']['schema'], 'cairn.legacy-pregrade-snapshot-pin/1')


if __name__ == '__main__':
    unittest.main()
