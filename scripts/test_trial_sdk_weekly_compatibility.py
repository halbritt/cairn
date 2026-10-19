"""Real weekly validation on synthetic inputs; no native/store/grading effects."""
import hashlib
import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

import test_trial_sdk_paired_evidence as parent_tests
import test_trial_weekly_request as request_tests
import trial_task_eval as legacy
import trial_weekly_request as weekly
from trial_paired_request import canonical_sha, mcp_arguments
from trial_sdk_paired_evidence import WeeklyPreparationOwner, seal_resolved_corpus
from trial_bootstrap_snapshot import Snapshot


class CompatibilityTests(unittest.TestCase):
    setUp = parent_tests.PairedEvidenceTests.setUp
    setup_binding = parent_tests.PairedEvidenceTests.setup_binding
    config = parent_tests.PairedEvidenceTests.config
    bind = parent_tests.PairedEvidenceTests.bind

    def selected(self, launch_change=None):
        fixture_root=self.root/'weekly-input';fixture_root.mkdir()
        f=request_tests.Fixture(fixture_root)
        # A future overlay must not silently change the selected version-one input.
        (f.source/'revisions').mkdir()
        (f.source/'revisions/v2.json').write_text(json.dumps(dict(cases=[],notes=[
            dict(id='current',body='unselected future value') ])))
        bundle=f.bundle;row=bundle['schedule']['runs'][0]
        case=next(c for c in bundle['cases'] if c['id']==row['case'])
        self.prompt=case['wordings']['task']
        prompt_path=Path(self.document['sources']['prompt']['path'])
        prompt_path.chmod(0o600);prompt_path.write_text(self.prompt);prompt_path.chmod(0o400)
        self.document['sources']['prompt']['sha256']=hashlib.sha256(self.prompt.encode()).hexdigest()
        session=str(uuid.uuid5(uuid.UUID(f.request['occurrence_id']),canonical_sha(bundle['schedule'])+':0'))
        run=f"{row['case']}.{row['arm']}.s{row['seed']}"
        self.document['binding'].update(request_id=f.request['request_id'],occurrence_id=f.request['occurrence_id'],
            request_sha256=canonical_sha(f.request),case_id=row['case'],arm=row['arm'],session_id=session)
        self.document['mcp_binding'].update(task='task-eval/'+run,run=run)
        mcp_path=self.source/'mcp.json';mcp_path.chmod(0o600)
        raw=json.dumps(dict(mcpServers=dict(cairn=dict(type='stdio',command='/native/cairn',
            args=mcp_arguments(self.document['mcp_binding']))))).encode()
        mcp_path.write_bytes(raw);mcp_path.chmod(0o400)
        for ref in self.document['sources'].values():
            if ref['path']==str(mcp_path):ref['sha256']=hashlib.sha256(raw).hexdigest()
        self.document['binding']['source_set_sha256']=canonical_sha(
            {role:ref['sha256'] for role,ref in self.document['sources'].items()})
        if launch_change:self.document['launch'].update(launch_change)
        preparation,binding=self.setup_binding();selected=WeeklyPreparationOwner(preparation)
        self.selected_owner=selected
        self.addCleanup(selected.close)
        selected.prepare(f.request,registry=f.root/'registry',now=request_tests.NOW,**f.references)
        return f,selected,preparation,binding

    def test_real_checked_21_case_schedule_to_owned_run_and_publication(self):
        f,selected,preparation,binding=self.selected()
        self.assertEqual(len(f.bundle['cases']),21)
        self.assertEqual(len(f.bundle['distractor_notes']),1900)
        self.assertEqual(len(selected.rows),126)
        for original,owned in zip(f.bundle['schedule']['runs'],selected.rows):
            self.assertEqual(owned['run_id'],f"{original['case']}.{original['arm']}.s{original['seed']}")
            self.assertNotEqual(owned['run_id'],owned['session_id'])
            self.assertEqual({k:owned[k] for k in original},original)
        self.assertEqual(len({r['session_id'] for r in selected.rows}),126)
        run=self.bind(selected,preparation,binding)
        self.assertEqual(run._paired_join['run_id'],self.document['mcp_binding']['run'])
        self.assertEqual(run._paired_join['session_id'],self.document['binding']['session_id'])
        self.assertEqual(run.cleanup_step(),'closed')
        run.publish_evidence(str(self.root/'publication'))
        receipt=json.loads((self.root/'publication/evidence.json').read_bytes())
        self.assertIsNone(receipt['observed_corpus_sha256'])
        self.assertEqual(receipt['selected_resolved_corpus'],selected.resolved_corpus)
        self.assertEqual(receipt['join'],run._paired_join)

    def test_exact_legacy_resolved_bytes_separate_from_overlay_and_future(self):
        f,selected,_,_=self.selected()
        actual=(Path(f.request['output'])/'owned-selection/selected-resolved-corpus.json').read_bytes()
        expected=(json.dumps(dict(notes=legacy.load_corpus(1,root=f.source)),ensure_ascii=False,indent=2)+'\n').encode()
        self.assertEqual(actual,expected)
        self.assertTrue(actual.endswith(b'\n'))
        self.assertNotIn(b'unselected future value',actual)
        self.assertEqual(selected.resolved_corpus['sha256'],hashlib.sha256(expected).hexdigest())
        self.assertNotEqual(selected.resolved_corpus['sha256'],selected.overlay['sha256'])
        self.assertNotEqual(selected.resolved_corpus['sha256'],selected.identity['resolved_corpus_sha256'])
        notes=json.loads(actual)['notes']
        self.assertEqual(next(n for n in notes if n['id']=='old')['supersede_with'],'current')
        self.assertEqual(len(json.loads((Path(f.request['output'])/'owned-selection/selected-overlay.json').read_bytes())['notes']),1904)

    def test_legacy_scope_substitution_refuses_before_run_move(self):
        f,selected,preparation,binding=self.selected()
        self.document['mcp_binding']['task']=selected.rows[0]['case']
        with self.assertRaisesRegex(ValueError,'paired_service_binding'):
            self.bind(selected,preparation,binding)
        self.assertIsNotNone(preparation._allocation)
        self.assertEqual(selected.state,'unknown')

    def test_legacy_serializer_unicode_escapes_long_token_and_late_refusal(self):
        owner=Snapshot(str(self.root/'serializer'));self.addCleanup(owner.close)
        notes=[dict(id='N',body='é😀\n\\"'+('x'*70000),entities=['a','b']),dict(id='B',body='tail')]
        seal_resolved_corpus(owner,notes)
        expected=(json.dumps(dict(notes=notes),ensure_ascii=False,indent=2)+'\n').encode()
        self.assertEqual((self.root/'serializer/selected-resolved-corpus.json').read_bytes(),expected)
        bad=Snapshot(str(self.root/'invalid'));self.addCleanup(bad.close)
        notes[-1]['body']='\ud800'
        with self.assertRaises(ValueError):seal_resolved_corpus(bad,notes)
        self.assertFalse((self.root/'invalid/selected-resolved-corpus.json').exists())

    def test_actual_compiled_model_and_effort_must_match_selection(self):
        # One owned attempt per case: refusal consumes its selected custody.
        f,selected,preparation,binding=self.selected(dict(model='other-model'))
        with self.assertRaisesRegex(ValueError,'paired_launch_binding'):
            self.bind(selected,preparation,binding)
        self.assertIsNotNone(preparation._allocation)
        self.assertEqual(selected.state,'unknown')

    def test_actual_compiled_effort_must_match_selection(self):
        f,selected,preparation,binding=self.selected(dict(effort='low'))
        with self.assertRaisesRegex(ValueError,'paired_launch_binding'):
            self.bind(selected,preparation,binding)
        self.assertIsNotNone(preparation._allocation)
        self.assertEqual(selected.state,'unknown')

    def test_sdk_case_syntax_does_not_change_supplemental_schema(self):
        import trial_native_bootstrap as bootstrap
        document=json.loads(json.dumps(self.document))
        document['binding']['case_id']='synthetic-20'
        bootstrap._declaration(document,schema=bootstrap.SDK_SCHEMA)
        document['schema']=bootstrap.SCHEMA
        with self.assertRaisesRegex(ValueError,'bootstrap_case'):
            bootstrap._declaration(document)
        document['schema']=bootstrap.SDK_SCHEMA
        for case in ('../T1','', 'x'*97, 'bad.name'):
            document['binding']['case_id']=case
            with self.assertRaisesRegex(ValueError,'bootstrap_case'):
                bootstrap._declaration(document,schema=bootstrap.SDK_SCHEMA)

    def test_interrupted_resolved_corpus_write_keeps_original_failure_custody(self):
        import os
        import trial_sdk_paired_evidence as evidence
        original=evidence.seal_resolved_corpus
        def fail(snapshot,notes):
            def partial(fd,raw):
                os.write(fd,raw[:1])
                raise KeyboardInterrupt('after real partial corpus write')
            with patch.object(evidence,'write_all',side_effect=partial):
                return original(snapshot,notes)
        with patch.object(evidence,'seal_resolved_corpus',side_effect=fail):
            with self.assertRaises(KeyboardInterrupt):self.selected()
        selected=self.selected_owner
        self.assertEqual(selected.state,'unknown')
        self.assertIn(selected.allocation.ticket,selected.allocation.resources._memory)
        self.assertTrue(selected.allocation.resources.snapshot()['unknown'])
        partial=self.root/'weekly-input/output/owned-selection/selected-resolved-corpus.json'
        self.assertEqual(partial.stat().st_size,1)
        self.assertFalse(selected.snapshot_owner.closed)
        selected.close()
        self.assertTrue(selected.snapshot_owner.closed)
