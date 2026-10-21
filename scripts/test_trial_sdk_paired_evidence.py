"""Synthetic checked-selection boundary; real publication/SDK move/owned files.

No actual weekly cases/oracles/evaluator import or child execution. The existing
weekly _checked validator is substituted with an explicitly synthetic bundle;
these tests do not claim it validates the original private weekly input.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

import test_trial_sdk_fixed_run as rf
import test_trial_sdk_stream_retention as rt
from test_trial_sdk_callback_phases import interrupt_line
from trial_paired_request import canonical_sha, selected_bytes
from trial_sdk_paired_evidence import WeeklyPreparationOwner, EvidencePublication
from trial_sdk_fixed_run import FixedRunOwner
import trial_weekly_request as weekly


class PairedEvidenceTests(unittest.TestCase):
    setUp=rf.RunTests.setUp
    setup_binding=rf.RunTests.setup_binding
    config=rf.RunTests.config
    running=rf.RunTests.running
    read_once=rt.RetentionTests.read_once

    def fixture(self, suffix=''):
        output=self.root/('weekly'+suffix);registry=self.root/('registry'+suffix);registry.mkdir(mode=0o700)
        def pin(name, value):
            path=self.root/(name+suffix+'.json');body=selected_bytes(value);path.write_bytes(body);path.chmod(0o600)
            return dict(path=str(path),sha256=hashlib.sha256(body).hexdigest())
        source=dict(resolved_corpus_sha256=canonical_sha(dict(notes=[dict(id='N',body='original')])) )
        schedule=dict(wording='task',runs=[dict(case='T1',arm='CAND',seed=17,position=0)],sha256='e'*64)
        common=pin('control',{'synthetic':True})
        request=dict(schema=weekly.SCHEMA,request_id=self.document['binding']['request_id'],
            occurrence_id=self.document['binding']['occurrence_id'],campaign_id='00000000-0000-4000-8000-000000000004',
            output=str(output),weekly_input=dict(execution=common),schedule=pin('schedule',schedule),
            supervisor_source=common,permission_profile=dict(source=common,settings=common),
            grading={k:common for k in ('rubric','changed_pair_definition','blinding_policy','review_budget')},
            dispositions={})
        bundle=dict(root=self.source,workspace_paths={},execution={'native':dict(model='fixture-model',effort='high')},source=source,plan={'sha256':'d'*64},schedule=schedule,
            frozen={'corpus_sha256':'f'*64},notes=[dict(id='N',body='original')],
            distractor_notes=[dict(id='D',body='synthetic distractor')],
            cases=[dict(id='T1',wordings={'task':self.prompt})])
        self.document['binding']['request_sha256']=canonical_sha(request)
        self.document['binding']['session_id']=str(uuid.uuid5(uuid.UUID(request['occurrence_id']),canonical_sha(schedule)+':0'))
        self.document['mcp_binding'].update(task='task-eval/T1.CAND.s17',run='T1.CAND.s17')
        mcp_path=self.source/'mcp.json';mcp_path.chmod(0o600)
        from trial_paired_request import mcp_arguments
        raw=json.dumps(dict(mcpServers=dict(cairn=dict(type='stdio',command='/native/cairn',
            args=mcp_arguments(self.document['mcp_binding']))))).encode()
        mcp_path.write_bytes(raw);mcp_path.chmod(0o400)
        for role,ref in self.document['sources'].items():
            if ref['path']==str(mcp_path):ref['sha256']=hashlib.sha256(raw).hexdigest()
        self.document['binding']['source_set_sha256']=canonical_sha(
            {role:ref['sha256'] for role,ref in self.document['sources'].items()})
        self.output=str(self.root/('prepared'+suffix))
        preparation,binding=self.setup_binding()
        selected=WeeklyPreparationOwner(preparation)
        # Retain independently even if subsequent parent publication interrupts.
        self.addCleanup(selected.close)
        with patch.object(weekly,'_checked',return_value=(request,bundle,None,{})) as check:
            selected.prepare(request,registry=registry,now=dt.datetime.now(dt.timezone.utc),companion={},review={})
        self.assertEqual(check.call_count,1)
        return selected,preparation,binding,request

    def bind(self, selected, preparation, binding, suffix=''):
        run=FixedRunOwner(binding.resources,clock=lambda:1);self.addCleanup(run.cleanup_step)
        config=self.config();config['policy'].update(self.document['mcp_binding'])
        with patch('time.monotonic_ns',return_value=1):
            selected.bind_run(run,preparation,binding,0,config,str(self.root/('fixed'+suffix)),
                str(self.root/'callbacks'),rf.AGGREGATE,rf.OP_QUOTE,stream_output=str(self.root/('streams'+suffix)))
        return run

    def test_real_prepare_binding_move_and_failure_evidence_publication(self):
        selected,preparation,binding,request=self.fixture();allocation=preparation._allocation
        run=self.bind(selected,preparation,binding)
        self.assertIs(run._session._custody._allocation,allocation)
        self.assertIsNone(preparation._allocation)
        held=dict(binding.resources._memory)
        self.assertEqual(run.cleanup_step(),'closed')
        output=str(self.root/'evidence');run.publish_evidence(output)
        receipt=json.loads((Path(output)/'evidence.json').read_bytes())
        self.assertEqual(run._publication.state,'closed')
        self.assertEqual(receipt['join']['request_sha256'],canonical_sha(request))
        self.assertEqual(receipt['join']['seed'],17)
        self.assertEqual(receipt['join']['frozen_corpus_sha256'],'f'*64)
        self.assertEqual(receipt['selected_overlay']['sha256'],hashlib.sha256(
            (Path(request['output'])/'owned-selection/selected-overlay.json').read_bytes()).hexdigest())
        self.assertIsNone(receipt['observed_corpus_sha256'])
        self.assertEqual(receipt['containment'],'unavailable')
        self.assertEqual(receipt['streams']['state'],'unknown')
        self.assertEqual(receipt['grade'],'ungraded')
        self.assertEqual(receipt['selected_input']['status'],'unknown')
        self.assertEqual(binding.resources._memory,held)
        with self.assertRaisesRegex(ValueError,'run_publication_consumed'):run.publish_evidence(str(self.root/'retry'))
        self.assertFalse((self.root/'retry').exists())

    def test_closed_stream_success_uses_actual_file_readback_and_no_terminal_grade(self):
        selected,preparation,binding,_=self.fixture();run=self.bind(selected,preparation,binding)
        actual_readv=os.readv
        with self.running(run._session):
            self.read_once(run,b'private evidence')
            self.read_once(run,b'',name='stdout');self.read_once(run,b'',name='stderr')
            native=run._session.native_owner;native.close_stdin()
            with patch('os.waitpid',return_value=(123,0)):native.observe_wait()
            native.release()
            with patch('os.readv',side_effect=actual_readv):run._evidence.finish(run._session._pump)
            run.cleanup_step();run.publish_evidence(str(self.root/'evidence'))
            receipt=json.loads((self.root/'evidence/evidence.json').read_bytes())
            self.assertEqual(receipt['streams']['state'],'closed')
            self.assertEqual(receipt['streams']['written']['stderr'],16)
            self.assertEqual(receipt['native_owners'][0]['wait']['returncode'],0)
            self.assertEqual(receipt['work_product_export'],'unavailable')
            self.assertEqual((self.root/'evidence/stderr.bin').read_bytes(),b'private evidence')
            self.assertEqual((self.root/'evidence/stream.jsonl').read_bytes(),b'')
            self.assertEqual(receipt['failure_evidence'],'local_bytes_closed')

    def test_failed_export_retains_partial_consumes_output_and_tickets(self):
        selected,preparation,binding,_=self.fixture();run=self.bind(selected,preparation,binding)
        run.cleanup_step();held=dict(binding.resources._memory);actual=os.write
        def write(fd,body):
            actual(fd,body[:3]);raise KeyboardInterrupt('after partial output')
        with patch('os.write',side_effect=write):
            with self.assertRaises(KeyboardInterrupt):run.publish_evidence(str(self.root/'evidence'))
        self.assertEqual(run._publication.state,'unknown')
        self.assertEqual((self.root/'evidence/evidence.json').stat().st_size,3)
        self.assertTrue(run._publication.files.closed)
        self.assertEqual(binding.resources._memory,held)
        self.assertTrue(binding.resources.snapshot()['unknown'])
        with self.assertRaisesRegex(ValueError,'run_publication_consumed'):run.publish_evidence(str(self.root/'retry'))

    def test_row_claim_before_interruption_is_consumed_with_original_owner_reachable(self):
        selected,preparation,binding,_=self.fixture()
        run=FixedRunOwner(binding.resources,clock=lambda:1);self.addCleanup(run.cleanup_step)
        config=self.config();config['policy'].update(self.document['mcp_binding'])
        with self.assertRaises(KeyboardInterrupt):
            interrupt_line(WeeklyPreparationOwner.bind_run,'run._paired = self',lambda:selected.bind_run(
                run,preparation,binding,0,config,str(self.root/'fixed'),str(self.root/'callbacks'),
                rf.AGGREGATE,rf.OP_QUOTE,stream_output=str(self.root/'streams')))
        self.assertIn(0,selected.claimed);self.assertIn(run,selected.runs)
        self.assertEqual(selected.state,'unknown')
        self.assertTrue(binding.resources.snapshot()['unknown'])
        self.assertIsNotNone(preparation._snapshot)
        run.cleanup_step();selected.close()
        self.assertTrue(preparation._snapshot.closed)
        self.assertTrue(preparation._control.ledger()['closed'])

    def test_binding_tamper_refuses_before_run_file_effects(self):
        selected,preparation,binding,_=self.fixture()
        preparation._binding['request_sha256']='0'*64
        with self.assertRaises(ValueError):self.bind(selected,preparation,binding)
        self.assertFalse((self.root/'fixed').exists());self.assertFalse((self.root/'streams').exists())

    def test_selected_file_drift_refuses_before_run(self):
        selected,preparation,binding,request=self.fixture()
        path=Path(request['output'])/'owned-selection/join.json';path.chmod(0o600);path.write_bytes(b'{}')
        with self.assertRaises(ValueError):self.bind(selected,preparation,binding)
        self.assertFalse((self.root/'fixed').exists())

    def test_cleanup_pending_cannot_export_or_replay(self):
        selected,preparation,binding,_=self.fixture();run=self.bind(selected,preparation,binding)
        with self.assertRaisesRegex(ValueError,'evidence_cleanup_pending'):run.publish_evidence(str(self.root/'evidence'))
        self.assertFalse((self.root/'evidence').exists())
        self.assertEqual(run._publication.state,'unknown')
        self.assertIn(binding.allocation,binding.resources._memory)

    def test_actual_schema_rejection_does_not_import_legacy_evaluator(self):
        import sys
        import subprocess
        if os.environ.get('CAIRN_TEST_IMPORT_BOUNDARY') != '1':
            environment = dict(os.environ, CAIRN_TEST_IMPORT_BOUNDARY='1')
            completed = subprocess.run([sys.executable, '-B', '-m', 'unittest',
                'test_trial_sdk_paired_evidence.PairedEvidenceTests.'
                'test_actual_schema_rejection_does_not_import_legacy_evaluator'],
                cwd=Path(__file__).parent, env=environment, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return
        preparation,binding=self.setup_binding();selected=WeeklyPreparationOwner(preparation)
        self.addCleanup(selected.close)
        self.assertNotIn('trial_task_eval',sys.modules)
        with self.assertRaisesRegex(ValueError,'weekly_request_shape'):
            selected.prepare({'schema':'cairn.paired-eval.request/2'},registry=self.root,
                now=dt.datetime.now(dt.timezone.utc),companion={},review={})
        self.assertNotIn('trial_task_eval',sys.modules)
        self.assertEqual(selected.state,'unknown')
        self.assertTrue(binding.resources.snapshot()['unknown'])

    def test_mcp_run_scope_mismatch_refuses_before_run(self):
        selected,preparation,binding,_=self.fixture()
        run=FixedRunOwner(binding.resources,clock=lambda:1);self.addCleanup(run.cleanup_step)
        config=self.config();config['policy'].update(self.document['mcp_binding'])
        config['policy']['repo']='/different'
        with self.assertRaisesRegex(ValueError,'paired_mcp_binding'):
            selected.bind_run(run,preparation,binding,0,config,str(self.root/'fixed'),
                str(self.root/'callbacks'),rf.AGGREGATE,rf.OP_QUOTE,stream_output=str(self.root/'streams'))
        self.assertFalse((self.root/'fixed').exists())

    def test_mutated_selected_seed_refuses_without_consuming_another_row(self):
        selected,preparation,binding,_=self.fixture();selected.rows[0]['seed']=18
        with self.assertRaisesRegex(ValueError,'paired_selection_changed'):self.bind(selected,preparation,binding)
        self.assertEqual(selected.claimed,set())
        self.assertFalse((self.root/'fixed').exists())

    def test_direct_exporter_cannot_bypass_run_one_shot(self):
        selected,preparation,binding,_=self.fixture();run=self.bind(selected,preparation,binding)
        run.cleanup_step();foreign=EvidencePublication(run)
        with self.assertRaisesRegex(ValueError,'evidence_publication_owner'):
            foreign.publish(str(self.root/'foreign'))
        self.assertFalse((self.root/'foreign').exists())

    def test_final_publication_interrupt_keeps_closed_files_and_unknown_consumed_owner(self):
        selected,preparation,binding,_=self.fixture();run=self.bind(selected,preparation,binding)
        run.cleanup_step();held=dict(binding.resources._memory)
        with self.assertRaises(KeyboardInterrupt):
            interrupt_line(EvidencePublication.publish,"self.state='closed'",
                lambda:run.publish_evidence(str(self.root/'evidence')))
        self.assertEqual(run._publication.state,'unknown')
        self.assertTrue(run._publication.files.closed)
        self.assertTrue((self.root/'evidence/evidence.json').is_file())
        self.assertEqual(binding.resources._memory,held)

    def test_replaced_original_stream_refuses_even_with_identical_bytes(self):
        selected,preparation,binding,_=self.fixture();run=self.bind(selected,preparation,binding)
        actual_readv=os.readv
        with self.running(run._session):
            self.read_once(run,b'private evidence');self.read_once(run,b'',name='stdout');self.read_once(run,b'',name='stderr')
            native=run._session.native_owner;native.close_stdin()
            with patch('os.waitpid',return_value=(123,0)):native.observe_wait()
            native.release()
            with patch('os.readv',side_effect=actual_readv):run._evidence.finish(run._session._pump)
            run.cleanup_step()
            source=self.root/'streams/stderr.bin';source.rename(source.with_suffix('.old'))
            source.write_bytes(b'private evidence');source.chmod(0o600)
            with self.assertRaisesRegex(ValueError,'evidence_stream_replaced'):
                run.publish_evidence(str(self.root/'evidence'))
            self.assertEqual(run._publication.state,'unknown')
            self.assertFalse((self.root/'evidence/evidence.json').exists())

    def test_post_move_join_mutation_refuses_before_output(self):
        selected,preparation,binding,_=self.fixture();run=self.bind(selected,preparation,binding)
        run.cleanup_step();run._paired_join['seed']=18
        with self.assertRaisesRegex(ValueError,'paired_join_changed'):
            run.publish_evidence(str(self.root/'evidence'))
        self.assertFalse((self.root/'evidence').exists())

    def test_selection_source_closed_before_export_is_not_a_receipt_substitute(self):
        selected,preparation,binding,_=self.fixture();run=self.bind(selected,preparation,binding)
        run.cleanup_step();selected.close()
        with self.assertRaisesRegex(ValueError,'snapshot_consumed'):
            run.publish_evidence(str(self.root/'evidence'))
        self.assertFalse((self.root/'evidence').exists())

class EmptyStreamTests(unittest.TestCase):
    def test_missing_empty_stream_is_not_zero_deliveries(self):
        import tempfile
        from trial_sdk_paired_evidence import EvidenceSnapshot
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);out=EvidenceSnapshot(str(root/'out'))
            try:
                with self.assertRaises(OSError):
                    out.copy_empty_stream('stream.jsonl',str(root/'missing'),(0,0),hashlib.sha256(b'').hexdigest())
                self.assertNotIn('stream.jsonl',out.files)
            finally:out.close()

    def test_zero_original_identity_and_nonzero_refuse_before_output(self):
        import tempfile
        from trial_sdk_paired_evidence import EvidenceSnapshot
        for body,expected in ((b'',(0,0)),(b'x',None)):
            with self.subTest(body=body),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);source=root/'source';source.write_bytes(body);source.chmod(0o600)
                meta=source.stat();out=EvidenceSnapshot(str(root/'out'))
                try:
                    with self.assertRaises(ValueError):
                        out.copy_empty_stream('stream.jsonl',str(source),expected or (meta.st_dev,meta.st_ino),hashlib.sha256(b'').hexdigest())
                    self.assertFalse((root/'out/stream.jsonl').exists())
                finally:out.close()

if __name__=='__main__':unittest.main()
