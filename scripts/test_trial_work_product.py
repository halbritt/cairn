"""Actual local git workspaces and provider-free selected terminal fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid

import trial_work_product as product
from trial_task_eval import runtime_sandbox


def git(root, *argv):
    return subprocess.check_output(['git','-C',str(root),*argv], env=product.git_environment()).decode().strip()


def fixture(root):
    source=root/'source';source.mkdir()
    git(source,'init','-q')
    (source/'README.md').write_text('base\n')
    git(source,'add','README.md');git(source,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','base')
    binding=dict(request_id=str(uuid.uuid4()),occurrence_id=str(uuid.uuid4()),request_sha256='a'*64,supervisor_source_sha256='b'*64)
    prepared=product.prepare_workspace(source,git(source,'rev-parse','HEAD'),root/'actor',binding)
    return prepared


def terminal(prepared):
    return dict(schema='cairn.work-product-terminal/1',binding=prepared['binding'],
        workspace_sha256=prepared['workspace_sha256'],actual_exit=0,
        containment_status='released',retention_status='closed',proof_sha256='c'*64)


class ExportTests(unittest.TestCase):
    def test_prepared_git_uses_the_empty_hook_path_for_actual_commits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root);actor=root/'actor'
            self.assertEqual(git(actor,'config','--get','core.hooksPath'),'/dev/null')
            hooks=actor/'.git/hooks';hooks.mkdir()
            hook=hooks/'pre-commit';hook.write_text('#!/bin/sh\ntouch hook-ran\n');hook.chmod(0o700)
            (actor/'README.md').write_text('actual local commit fixture\n')
            git(actor,'add','README.md');git(actor,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','task')
            self.assertFalse((actor/'hook-ran').exists())

    def test_prepared_actor_cannot_read_other_branch_refs_or_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';source.mkdir()
            git(source,'init','-q');(source/'README.md').write_text('ancestor\n')
            git(source,'add','.');git(source,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','ancestor')
            ancestor=git(source,'rev-parse','HEAD')
            (source/'README.md').write_text('base\n')
            git(source,'add','.');git(source,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','base')
            base=git(source,'rev-parse','HEAD')
            git(source,'checkout','-qb','private-oracle')
            (source/'oracle.txt').write_text('private sibling-branch fixture answer\n')
            git(source,'add','.');git(source,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','private')
            private_blob=git(source,'rev-parse','HEAD:oracle.txt')
            git(source,'tag','private-tag')
            binding=dict(request_id=str(uuid.uuid4()),occurrence_id=str(uuid.uuid4()),request_sha256='a'*64,supervisor_source_sha256='b'*64)
            prepared=product.prepare_workspace(source,base,root/'actor',binding)
            actor=root/'actor'
            result=subprocess.run(['git','-C',str(actor),'cat-file','-e',private_blob],capture_output=True,env=product.git_environment())
            self.assertNotEqual(result.returncode,0,'private branch object leaked into the actor Git database')
            self.assertEqual(git(actor,'for-each-ref','--format=%(refname)'), '')
            self.assertEqual(git(actor,'show',ancestor+':README.md'),'ancestor')
            self.assertEqual((actor/'README.md').read_text(),'base\n')
            expected={line.split()[0] for line in git(source,'rev-list','--objects',base).splitlines()}
            actual=set(git(actor,'cat-file','--batch-all-objects','--batch-check=%(objectname)').splitlines())
            self.assertEqual(actual,expected)
            self.assertEqual(prepared['initial_refs'],{})
            probe='''import subprocess,sys
for ident,available in ((sys.argv[1],True),(sys.argv[2],False)):
    result=subprocess.run(['/usr/bin/git','-C','/actor','cat-file','-e',ident],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    assert (result.returncode==0) == available
print('base_only_git_access')
'''
            command=runtime_sandbox()+['--unshare-net','--ro-bind',str(actor),'/actor',
                '--chdir','/actor','--',str(Path(sys.executable).resolve()),'-c',probe,base,private_blob]
            observed=subprocess.run(command,capture_output=True,text=True,timeout=20)
            if observed.returncode and observed.stderr.startswith('bwrap:'):
                self.skipTest('sandbox_unavailable: actual Git object access fixture could not run')
            self.assertEqual(observed.returncode,0,observed.stderr)
            self.assertEqual(observed.stdout.strip(),'base_only_git_access')

    def test_actual_sandbox_exposes_actor_workspace_and_hides_evaluator_siblings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);fixture(root)
            protected=[]
            for name in ('oracle','evaluator','corpus-origin','reviewer'):
                path=root/name;path.write_text('private fixture evidence')
                protected.extend((str(path), '/actor/../'+name))
            probe = '''import json,pathlib,sys
assert pathlib.Path('/actor/README.md').read_text() == 'base\\n'
for name in json.loads(sys.argv[1]):
    try:
        pathlib.Path(name).read_bytes()
    except (FileNotFoundError, PermissionError):
        continue
    raise SystemExit(3)
print('fixture_access_blocked')
'''
            command=runtime_sandbox()+['--unshare-net','--ro-bind',str(root/'actor'),'/actor',
                '--chdir','/actor','--',str(Path(sys.executable).resolve()),'-c',probe,json.dumps(protected)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=20)
            if result.returncode and result.stderr.startswith('bwrap:'):
                self.skipTest('sandbox_unavailable: host namespace boundary could not be exercised')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout.strip(),'fixture_access_blocked')

    def test_exports_committed_uncommitted_and_untracked_work_after_verified_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prepared=fixture(root);actor=root/'actor'
            (actor/'README.md').write_text('committed task edit\n')
            git(actor,'add','README.md');git(actor,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','task')
            (actor/'README.md').write_text('committed task edit\nuncommitted task edit\n')
            (actor/'DESIGN.md').write_text('Fixture design only.\n')
            (actor/'test.log').write_text('Fixture log, not a model run.\n')
            selected=product.export_work_product(prepared,root/'export',
                allowed_paths=['README.md','DESIGN.md','test.log'],test_log='test.log',
                verify_terminal=lambda binding:terminal(prepared))
            self.assertEqual(selected['status'],'exported_for_review')
            self.assertFalse(selected['task_acceptance'])
            patch=(root/'export/diff.patch').read_text()
            self.assertIn('committed task edit',patch)
            self.assertIn('uncommitted task edit',patch)
            self.assertEqual((root/'export/files/DESIGN.md').read_text(),'Fixture design only.\n')
            self.assertEqual(selected['commit'],git(actor,'rev-parse','HEAD'))
            self.assertEqual(selected['base_commit'],prepared['base_commit'])
            self.assertEqual(selected['files']['test.log']['sha256'],hashlib.sha256((actor/'test.log').read_bytes()).hexdigest())

    def test_unknown_termination_or_containment_cannot_create_an_export(self):
        for field,unknown in [('actual_exit',None),('containment_status','unknown'),('retention_status','unknown')]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);prepared=fixture(root)
                proof=terminal(prepared);proof[field]=unknown
                with self.assertRaisesRegex(product.Refusal,'terminal_unknown_or_failed'):
                    product.export_work_product(prepared,root/'export',allowed_paths=[],test_log='test.log',
                        verify_terminal=lambda binding:proof)
                self.assertFalse((root/'export').exists())

    def test_git_configuration_cannot_select_actor_supplied_helpers_or_another_object_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prepared=fixture(root);actor=root/'actor'
            (actor/'DESIGN.md').write_text('Fixture design.');(actor/'test.log').write_text('Fixture test.')
            with (actor/'.git/config').open('a') as config:config.write('\n[include]\npath = /private/config\n')
            with self.assertRaisesRegex(product.Refusal,'git_config_changed'):
                product.export_work_product(prepared,root/'export',allowed_paths=['DESIGN.md','test.log'],test_log='test.log',
                    verify_terminal=lambda binding:terminal(prepared))
            self.assertFalse((root/'export').exists())

    def test_committed_changes_cannot_be_hidden_by_restoring_an_unselected_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prepared=fixture(root);actor=root/'actor'
            (actor/'README.md').write_text('unselected committed edit\n')
            git(actor,'add','README.md');git(actor,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','unselected')
            (actor/'README.md').write_text('base\n')
            (actor/'DESIGN.md').write_text('Fixture.');(actor/'test.log').write_text('Fixture.')
            with self.assertRaisesRegex(product.Refusal,'unexpected_task_path'):
                product.export_work_product(prepared,root/'export',allowed_paths=['DESIGN.md','test.log'],test_log='test.log',
                    verify_terminal=lambda binding:terminal(prepared))

    def test_committed_symlink_cannot_be_hidden_by_a_regular_working_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prepared=fixture(root);actor=root/'actor'
            (actor/'README.md').unlink();(actor/'README.md').symlink_to('/private/fixture')
            git(actor,'add','README.md');git(actor,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','symlink')
            (actor/'README.md').unlink();(actor/'README.md').write_text('ordinary selected file')
            (actor/'DESIGN.md').write_text('Fixture.');(actor/'test.log').write_text('Fixture.')
            with self.assertRaisesRegex(product.Refusal,'source_special_file'):
                product.export_work_product(prepared,root/'export',allowed_paths=['README.md','DESIGN.md','test.log'],test_log='test.log',
                    verify_terminal=lambda binding:terminal(prepared))
            self.assertFalse((root/'export').exists())

    def test_export_refuses_indirection_special_files_and_external_git_objects(self):
        for mutation,code in [('symlink','workspace_special_file'),('hardlink','workspace_special_file'),
                              ('fifo','workspace_special_file'),('alternates','extra_git_state')]:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);prepared=fixture(root);actor=root/'actor'
                (actor/'DESIGN.md').write_text('Fixture.');(actor/'test.log').write_text('Fixture.')
                if mutation=='symlink':(actor/'escape').symlink_to(root/'source/README.md')
                if mutation=='hardlink':os.link(root/'source/README.md',actor/'escape')
                if mutation=='fifo':os.mkfifo(actor/'escape')
                if mutation=='alternates':(actor/'.git/objects/info/alternates').write_text(str(root/'source/.git/objects'))
                with self.assertRaisesRegex(product.Refusal,code):
                    product.export_work_product(prepared,root/'export',allowed_paths=['DESIGN.md','test.log','escape'],test_log='test.log',
                        verify_terminal=lambda binding:terminal(prepared))
                self.assertFalse((root/'export').exists())

    def test_terminal_from_another_workspace_cannot_authorize_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prepared=fixture(root)
            proof=terminal(prepared);proof['workspace_sha256']='d'*64
            with self.assertRaisesRegex(product.Refusal,'terminal_binding'):
                product.export_work_product(prepared,root/'export',allowed_paths=[],test_log='test.log',
                    verify_terminal=lambda binding:proof)
            self.assertFalse((root/'export').exists())

    @unittest.skipIf(os.geteuid()==0,'root can read permission-restricted fixture directories')
    def test_unreadable_workspace_directory_cannot_be_silently_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);prepared=fixture(root);actor=root/'actor'
            (actor/'DESIGN.md').write_text('Fixture.');(actor/'test.log').write_text('Fixture.')
            hidden=actor/'unreadable';hidden.mkdir();(hidden/'unexpected').write_text('Unselected task change')
            hidden.chmod(0)
            try:
                with self.assertRaisesRegex(product.Refusal,'filesystem_scan_failed'):
                    product.export_work_product(prepared,root/'export',allowed_paths=['DESIGN.md','test.log'],test_log='test.log',
                        verify_terminal=lambda binding:terminal(prepared))
                self.assertFalse((root/'export').exists())
            finally:
                hidden.chmod(0o700)


if __name__=='__main__':unittest.main()
