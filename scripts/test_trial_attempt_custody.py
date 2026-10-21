"""Synthetic original-baseline regressions; no original cases or provider calls."""
import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import trial_task_eval as task
from trial_attempt_custody import AttemptBaseline, BaselineError


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source'
        source.mkdir()
        (source / 'synthetic.txt').write_text('original\n')
        (source / 'setup.sh').write_text('git init -q\ngit add .\ngit commit -qm initial\n')
        self.case = dict(id='synthetic', workspace='synthetic', _prospective_workspace=str(source),
                         copy_to='repo', cwd='repo', category='synthetic', provenance={'type':'synthetic'},
                         expected=[], wordings={'task':'Make a synthetic change.'},
                         correct=[dict(type='unchanged', path='synthetic.txt')], mistake=[])
        self.args = SimpleNamespace(harness='claude', model='inert', wording='task',
                                    timeout=10, max_turns=2, semantic_recall=[])
        self.out = self.root / 'output'
        self.out.mkdir(mode=0o700)

    def run_actor(self, program):
        def inert(root, cwd, binds, env, argv, harness, **kwargs):
            return [sys.executable, '-c', program, str(root / cwd)]
        with patch.object(task, 'load_corpus', return_value=[]), \
             patch.object(task, 'sandbox_command', side_effect=inert):
            return task.run_agent(self.case, 'none', 0, 0, self.args, {}, self.out)

    def test_actor_snapshot_rewrite_cannot_turn_changed_file_into_unchanged(self):
        result = self.run_actor("""
import hashlib,json,pathlib,sys
cwd=pathlib.Path(sys.argv[1]); path=cwd/'synthetic.txt'; path.write_text('changed\\n')
(cwd.parent/'.eval-snapshot.json').write_text(json.dumps({'synthetic.txt':hashlib.sha256(path.read_bytes()).hexdigest()}))
print(json.dumps({'type':'result','result':'finished','is_error':False,'subtype':'success'}))
""")
        self.assertEqual(result['outcome'], 'incomplete')
        self.assertEqual(result['correct'], [False])
        self.assertIn('baseline_custody', result)

    def test_moved_tag_and_replacement_ref_preserve_added_and_committed_changes(self):
        self.case['correct'] = [dict(type='added', pattern='changed'),
                                dict(type='commit_files', includes=['synthetic.txt'])]
        result = self.run_actor("""
import json,pathlib,subprocess,sys
cwd=pathlib.Path(sys.argv[1])
def git(*args):
 return subprocess.check_output(['git','-C',str(cwd),*args]).decode().strip()
original=git('rev-parse','HEAD')
(cwd/'synthetic.txt').write_text('changed\\n')
git('add','synthetic.txt')
git('-c','user.name=inert','-c','user.email=inert@example.invalid','commit','-qm','actor change')
git('tag','-f','eval-base')
git('replace',original,'HEAD')
git('config','diff.external','false')
git('config','diff.trap.textconv','false')
(cwd/'.gitattributes').write_text('synthetic.txt diff=trap\\n')
print(json.dumps({'type':'result','result':'finished','is_error':False,'subtype':'success'}))
""")
        self.assertEqual(result['correct'], [True, True])
        self.assertEqual(result['outcome'], 'correct')
        base = self.out / 'runs' / result['run_id']
        baseline = AttemptBaseline.load(base, self.case, 'none', 0, result['baseline_custody'])
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=base/'work/repo').decode().strip()
        self.assertNotEqual(head, baseline.base_commit)
        self.assertEqual(task.evaluate(dict(type='unchanged', path='synthetic.txt'),
                         dict(cwd=base/'work/repo', snapshot=baseline.snapshot, baseline=baseline)), False)

    def regrade(self, result):
        (self.out/'agent.json').write_text(json.dumps(dict(records=[result], arms=['none'], frozen={})))
        args = SimpleNamespace(version=1, result_dir=str(self.out), adjudications=None)
        with patch.object(task, 'verify_frozen', return_value={}), \
             patch.object(task, 'load_cases', return_value=[self.case]), contextlib.redirect_stdout(io.StringIO()):
            task.cmd_regrade(args)
        return json.loads((self.out/'agent-v1.json').read_text())['records'][0]

    def test_added_uses_raw_files_without_actor_clean_filter_or_binary_attributes(self):
        self.case['correct'] = [dict(type='added', pattern='changed'),
                                dict(type='added', pattern='untracked line')]
        result = self.run_actor("""
import json,pathlib,subprocess,sys
cwd=pathlib.Path(sys.argv[1])
(cwd/'synthetic.txt').write_text('changed\\n')
(cwd/'untracked.txt').write_text('untracked line\\n')
(cwd/'.gitattributes').write_text('*.txt filter=hide -diff\\n')
subprocess.check_call(['git','-C',str(cwd),'config','filter.hide.clean','touch FILTER-RAN; printf original'])
print(json.dumps({'type':'result','result':'finished','is_error':False,'subtype':'success'}))
""")
        self.assertEqual(result['correct'], [True, True])
        cwd = self.out/'runs'/result['run_id']/'work/repo'
        self.assertFalse((cwd/'FILTER-RAN').exists())

    def test_regrade_uses_original_snapshot_and_refuses_missing_or_modified_custody(self):
        result = self.run_actor("""
import hashlib,json,pathlib,sys
cwd=pathlib.Path(sys.argv[1]); path=cwd/'synthetic.txt'; path.write_text('changed\\n')
(cwd.parent/'.eval-snapshot.json').write_text(json.dumps({'synthetic.txt':hashlib.sha256(path.read_bytes()).hexdigest()}))
print(json.dumps({'type':'result','result':'finished','is_error':False,'subtype':'success'}))
""")
        self.assertEqual(self.regrade(result)['outcome'], 'incomplete')
        missing = dict(result); missing.pop('baseline_custody')
        self.assertEqual(self.regrade(missing)['outcome'], 'not_regradable')
        path = next((self.out/'baselines').iterdir())
        document = json.loads(path.read_bytes())
        document['snapshot']['synthetic.txt'] = hashlib.sha256(b'changed\n').hexdigest()
        path.write_text(json.dumps(document))
        self.assertEqual(self.regrade(result)['outcome'], 'not_regradable')

    def test_unchanged_success_and_private_baseline_outside_writable_work(self):
        result = self.run_actor("import json; print(json.dumps({'type':'result','result':'done','is_error':False}))")
        self.assertEqual(result['correct'], [True])
        base = self.out / 'runs' / result['run_id']
        path = next((self.out/'baselines').iterdir())
        self.assertFalse(path.is_relative_to(base))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        baseline = AttemptBaseline.load(base, self.case, 'none', 0, result['baseline_custody'])
        with self.assertRaises(TypeError):
            baseline.snapshot['synthetic.txt'] = 'f'*64
        with self.assertRaises(BaselineError):
            AttemptBaseline.capture(base, self.case, 'none', 1)

    def test_raw_added_handles_deletion_untracked_symlink_and_preserves_actor_index(self):
        self.case['correct'] = [dict(type='added', pattern='original', expect=False),
                                dict(type='added', pattern='new raw line'),
                                dict(type='added', pattern='untracked.txt'),
                                dict(type='staged', paths=['synthetic.txt'])]
        result = self.run_actor("""
import hashlib,json,os,pathlib,subprocess,sys
cwd=pathlib.Path(sys.argv[1])
(cwd/'synthetic.txt').unlink()
subprocess.check_call(['git','-C',str(cwd),'add','-u'])
(cwd/'untracked.txt').write_text('new raw line\\n')
os.symlink('untracked.txt',cwd/'link')
(cwd/'index-before.txt').write_text(hashlib.sha256((cwd/'.git/index').read_bytes()).hexdigest())
print(json.dumps({'type':'result','result':'finished','is_error':False,'subtype':'success'}))
""")
        self.assertEqual(result['correct'], [True, True, True, True])
        cwd = self.out/'runs'/result['run_id']/'work/repo'
        self.assertEqual((cwd/'index-before.txt').read_text(), hashlib.sha256((cwd/'.git/index').read_bytes()).hexdigest())
        self.assertTrue((cwd/'link').is_symlink())

    def test_actor_worktree_redirect_is_unknown(self):
        self.case['correct'] = [dict(type='added', pattern='original')]
        result = self.run_actor("""
import json,pathlib,subprocess,sys
cwd=pathlib.Path(sys.argv[1]); outside=cwd.parent.parent/'decoy'; outside.mkdir()
(outside/'synthetic.txt').write_text('original\\n')
subprocess.check_call(['git','-C',str(cwd),'config','core.worktree',str(outside)])
print(json.dumps({'type':'result','result':'finished','is_error':False,'subtype':'success'}))
""")
        self.assertEqual(result['outcome'], 'undetermined')
        self.assertIn('original_baseline_repository_changed', result['correct'][0])

    def test_actor_git_directory_redirect_is_unknown(self):
        self.case['correct'] = [dict(type='commit_files', includes=['synthetic.txt'])]
        result = self.run_actor("""
import json,os,pathlib,shutil,sys
cwd=pathlib.Path(sys.argv[1]); outside=cwd.parent.parent/'decoy'
shutil.copytree(cwd/'.git',outside); shutil.rmtree(cwd/'.git'); os.symlink(outside,cwd/'.git')
print(json.dumps({'type':'result','result':'finished','is_error':False,'subtype':'success'}))
""")
        self.assertEqual(result['outcome'], 'undetermined')
        self.assertIn('original_baseline_repository_changed', result['correct'][0])

    def test_retention_failure_prevents_actor_admission(self):
        with patch('trial_attempt_custody.os.fsync', side_effect=OSError('synthetic fsync failure')), \
             patch.object(task, 'sandbox_command', side_effect=AssertionError('actor must not start')):
            with self.assertRaises(OSError):
                task.run_agent(self.case, 'none', 0, 0, self.args, {}, self.out)


if __name__ == '__main__':
    unittest.main()
