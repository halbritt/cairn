"""Disposable Git workspaces and independently waited children; no native actor."""
import json
import os
from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import patch

import trial_work_product as product
from test_trial_work_product import fixture, git, terminal


def attempt_terminal(prepared, raw_status=0, returncode=0, stop='completed'):
    # A constructed trusted-callback fixture, not an authenticated runtime adapter.
    return dict(schema='cairn.work-product-attempt-terminal/1',
        binding=dict(prepared['binding']), workspace_sha256=prepared['workspace_sha256'],
        actual_wait_status=raw_status, actual_returncode=returncode,
        containment_status='released', failure_evidence_status='closed',
        stop_cause=stop, observations=[], proof_sha256='c'*64)


class AttemptEvidenceTests(unittest.TestCase):
    def export(self, prepared, output, proof):
        return product.export_attempt_evidence(prepared,output,
            allowed_paths=['README.md','DESIGN.md','test.log'], required_paths=['DESIGN.md','test.log'],
            verify_attempt=lambda binding:proof)

    def test_known_zero_exit_exports_observed_absent_required_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prepared=fixture(root)
            pid=os.fork()
            if pid==0: os._exit(0)
            waited,raw=os.waitpid(pid,0)
            self.assertEqual((waited,raw),(pid,0))
            result=product.export_attempt_evidence(prepared,root/'evidence',
                allowed_paths=['DESIGN.md','test.log'], required_paths=['DESIGN.md','test.log'],
                verify_attempt=lambda binding:attempt_terminal(prepared,raw,0))
            self.assertEqual(result['status'],'evidence_exported')
            self.assertEqual(result['required_presence'],{'DESIGN.md':False,'test.log':False})
            self.assertEqual(result['required_sha256'],{'DESIGN.md':None,'test.log':None})
            self.assertEqual(result['files'],{})
            self.assertFalse(result['task_acceptance'])
            self.assertNotIn('validity',result)
            self.assertEqual(json.loads((root/'evidence/attempt-evidence.json').read_text()),result)

    def test_real_nonzero_child_leaves_exportable_partial_committed_and_untracked_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prepared=fixture(root); actor=root/'actor'
            (actor/'README.md').write_text('committed disposable edit\n')
            git(actor,'add','README.md'); git(actor,'-c','user.name=fixture','-c','user.email=fixture@example.invalid','commit','-qm','edit')
            pid=os.fork()
            if pid==0:
                try:
                    (actor/'DESIGN.md').write_text('partial disposable design\n')
                except BaseException:
                    os._exit(19)
                os._exit(17)
            waited,raw=os.waitpid(pid,0)
            self.assertEqual(waited,pid)
            self.assertEqual(raw,17<<8)
            proof=attempt_terminal(prepared,raw,17)
            result=self.export(prepared,root/'evidence',proof)
            self.assertEqual(result['terminal']['actual_returncode'],17)
            self.assertEqual(result['required_presence'],{'DESIGN.md':True,'test.log':False})
            self.assertEqual(result['required_sha256']['DESIGN.md'],result['files']['DESIGN.md']['sha256'])
            self.assertIsNone(result['required_sha256']['test.log'])
            self.assertEqual((root/'evidence/files/DESIGN.md').read_text(),'partial disposable design\n')
            self.assertIn('committed disposable edit',(root/'evidence/committed.patch').read_text())
            self.assertEqual(result['terminal'],proof)

    def test_real_signalled_child_and_owner_deadline_are_observations_not_validity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prepared=fixture(root); actor=root/'actor'
            ready,notify=os.pipe()
            pid=os.fork()
            if pid==0:
                try:
                    os.close(ready)
                    (actor/'test.log').write_text('disposable check began; no completion claimed\n')
                    os.write(notify,b'R'); os.close(notify)
                    signal.pause()
                finally:
                    os._exit(18)
            os.close(notify)
            try:
                self.assertEqual(os.read(ready,1),b'R')
                os.kill(pid,signal.SIGTERM)
            finally:
                os.close(ready)
                waited,raw=os.waitpid(pid,0)
            self.assertEqual(waited,pid)
            self.assertEqual(raw,signal.SIGTERM)
            proof=attempt_terminal(prepared,raw,-signal.SIGTERM,'wall_deadline')
            proof['observations']=['terminal_result_missing','frame_reader_refused']
            result=self.export(prepared,root/'evidence',proof)
            self.assertEqual(result['terminal'],proof)
            self.assertEqual(result['required_presence'],{'DESIGN.md':False,'test.log':True})
            self.assertFalse(result['task_acceptance'])
            self.assertNotIn('validity',result)

    def test_unknown_or_inconsistent_wait_closure_and_wrong_binding_refuse_without_output(self):
        mutations=[('actual_wait_status',None,'attempt_wait_unknown'),
            ('actual_returncode',None,'attempt_wait_unknown'),
            ('actual_wait_status',True,'attempt_wait_unknown'),
            ('actual_wait_status',signal.SIGSTOP<<8|127,'attempt_wait_nonterminal'),
            ('actual_returncode',17,'attempt_wait_inconsistent'),
            ('containment_status','unknown','attempt_closure_unknown'),
            ('failure_evidence_status','unknown','attempt_closure_unknown'),
            ('workspace_sha256','d'*64,'attempt_terminal_binding')]
        for field,value,code in mutations:
            with self.subTest(field=field,value=value),tempfile.TemporaryDirectory() as directory:
                root=Path(directory); prepared=fixture(root); proof=attempt_terminal(prepared); proof[field]=value
                with self.assertRaisesRegex(product.Refusal,code):
                    self.export(prepared,root/'evidence',proof)
                self.assertFalse((root/'evidence').exists())

    def test_json_selected_verifier_callback_fault_and_foreign_request_refuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prepared=fixture(root)
            with self.assertRaisesRegex(product.Refusal,'trusted_attempt_adapter_required'):
                product.export_attempt_evidence(prepared,root/'evidence',allowed_paths=[],required_paths=[],
                    verify_attempt={'module':'actor'})
            def unavailable(binding):
                raise RuntimeError('PRIVATE_CALLBACK_SENTINEL')
            with self.assertRaisesRegex(product.Refusal,'^attempt_terminal_unavailable$'):
                product.export_attempt_evidence(prepared,root/'evidence',allowed_paths=[],required_paths=[],verify_attempt=unavailable)
            proof=attempt_terminal(prepared); proof['binding']['request_sha256']='d'*64
            with self.assertRaisesRegex(product.Refusal,'attempt_terminal_binding'):
                self.export(prepared,root/'evidence',proof)
            self.assertFalse((root/'evidence').exists())

    def test_success_export_still_refuses_missing_required_files_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prepared=fixture(root)
            with self.assertRaisesRegex(product.Refusal,'required_work_product_missing'):
                product.export_work_product(prepared,root/'success',allowed_paths=['DESIGN.md','test.log'],test_log='test.log',
                    verify_terminal=lambda binding:terminal(prepared))
            proof=terminal(prepared); proof['actual_exit']=17
            with self.assertRaisesRegex(product.Refusal,'terminal_unknown_or_failed'):
                product.export_work_product(prepared,root/'success',allowed_paths=['DESIGN.md','test.log'],test_log='test.log',
                    verify_terminal=lambda binding:proof)
            self.assertFalse((root/'success').exists())

    def test_inspection_open_and_scan_faults_never_publish_missing_file_evidence(self):
        for fault in ['open','scan']:
            with self.subTest(fault=fault),tempfile.TemporaryDirectory() as directory:
                root=Path(directory); prepared=fixture(root); actor=root/'actor'
                if fault=='scan':
                    hidden=actor/'unreadable'; hidden.mkdir(); (hidden/'secret').write_text('disposable test file')
                    hidden.chmod(0)
                    try:
                        with self.assertRaisesRegex(product.Refusal,'filesystem_scan_failed'):
                            self.export(prepared,root/'evidence',attempt_terminal(prepared))
                    finally:
                        hidden.chmod(0o700)
                else:
                    real_open=os.open
                    def read_fault(path,*args,**kwargs):
                        if Path(path)==actor/'README.md':
                            raise OSError('PRIVATE_IO_SENTINEL')
                        return real_open(path,*args,**kwargs)
                    with patch.object(os,'open',side_effect=read_fault):
                        with self.assertRaisesRegex(product.Refusal,'^attempt_export_io$'):
                            self.export(prepared,root/'evidence',attempt_terminal(prepared))
                self.assertFalse((root/'evidence').exists())

    def test_fsync_failure_leaves_consumed_partial_target_without_returned_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prepared=fixture(root)
            with patch.object(os,'fsync',side_effect=OSError('PRIVATE_FSYNC_SENTINEL')):
                with self.assertRaisesRegex(product.Refusal,'^attempt_export_io$'):
                    self.export(prepared,root/'evidence',attempt_terminal(prepared))
            self.assertTrue((root/'evidence').is_dir())
            self.assertFalse((root/'evidence/attempt-evidence.json').exists())
            with self.assertRaisesRegex(product.Refusal,'export_target'):
                self.export(prepared,root/'evidence',attempt_terminal(prepared))

    def test_partial_export_keeps_git_path_special_file_and_size_guards(self):
        for mutation,code in [('symlink','workspace_special_file'),('alternates','extra_git_state'),
                              ('config','git_config_changed'),('unselected','unexpected_task_path'),
                              ('oversized','file_limit'),('count','workspace_limit')]:
            with self.subTest(mutation=mutation),tempfile.TemporaryDirectory() as directory:
                root=Path(directory); prepared=fixture(root); actor=root/'actor'
                if mutation=='symlink': (actor/'DESIGN.md').symlink_to(root/'source/README.md')
                if mutation=='alternates': (actor/'.git/objects/info/alternates').write_text(str(root/'source/.git/objects'))
                if mutation=='config':
                    with (actor/'.git/config').open('a') as config: config.write('\n[include]\npath=/private/fixture\n')
                if mutation=='unselected': (actor/'unexpected.txt').write_text('unselected change')
                if mutation=='oversized':
                    with (actor/'DESIGN.md').open('wb') as file: file.truncate(8*1024*1024+1)
                if mutation=='count':
                    for index in range(4096): (actor/('empty-'+str(index))).touch()
                with self.assertRaisesRegex(product.Refusal,code):
                    self.export(prepared,root/'evidence',attempt_terminal(prepared))
                self.assertFalse((root/'evidence').exists())

    def test_closed_terminal_and_selected_required_paths_refuse_invalid_declarations(self):
        for field,value,code in [('schema','unreviewed','attempt_terminal_binding'),
            ('stop_cause','VALID','attempt_stop_cause'),('observations',['PRIVATE_FREE_TEXT'],'attempt_observations'),
            ('observations',['capture_fault']*2,'attempt_observations'),('proof_sha256','bad','attempt_terminal_proof')]:
            with self.subTest(field=field),tempfile.TemporaryDirectory() as directory:
                root=Path(directory); prepared=fixture(root); proof=attempt_terminal(prepared); proof[field]=value
                with self.assertRaisesRegex(product.Refusal,code): self.export(prepared,root/'evidence',proof)
                self.assertFalse((root/'evidence').exists())
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); prepared=fixture(root)
            proof=attempt_terminal(prepared); del proof['actual_wait_status']
            with self.assertRaisesRegex(product.Refusal,'attempt_terminal_shape'): self.export(prepared,root/'evidence',proof)
            for required,code in [(['not-selected'],'required_paths_unselected'),(['../escape'],'export_path'),
                                  (['DESIGN.md','DESIGN.md'],'required_paths')]:
                with self.subTest(required=required),self.assertRaisesRegex(product.Refusal,code):
                    product.export_attempt_evidence(prepared,root/'evidence',allowed_paths=['DESIGN.md'],
                        required_paths=required,verify_attempt=lambda binding:attempt_terminal(prepared))
            self.assertFalse((root/'evidence').exists())


if __name__=='__main__':
    unittest.main()
