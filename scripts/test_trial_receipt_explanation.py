"""Metadata-only receipt export; integration cases require the owned PG wrapper."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import trial_task_eval as te
from trial_receipt_explanation import collect_receipts, retain_hook_explanations, export_receipts


class ReceiptObservationTests(unittest.TestCase):
    def test_only_exact_observed_hook_receipts_with_digests(self):
        ident=str(uuid.uuid4())
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'observations.jsonl'
            rows=[dict(schema='cairn.task-hook-observation/1',search_receipts=[dict(state='observed',receipt_id=ident,query_sha256='a'*64)],search_receipts_omitted=0),
                  dict(schema='cairn.task-hook-observation/1',search_receipts=[dict(state='unknown')],search_receipts_omitted=0)]
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            found=collect_receipts(path)
            self.assertEqual(found['receipts'],[dict(receipt_id=ident,query_sha256='a'*64)])
            self.assertFalse(found['complete'])
            self.assertIn('unobserved_search_receipt',found['unknown'])

    def test_store_export_requires_the_original_disposable_socket(self):
        store=object.__new__(te.TrialStore)
        store.pg_socket='/tmp/cairn-task-eval-pg.original/socket'
        with patch.object(te,'disposable_pg',return_value='/tmp/cairn-task-eval-pg.other/socket'):
            with self.assertRaisesRegex(ValueError,'owned database routing changed'):
                store.receipt_explanations(dict(receipts=[],complete=True,unknown=[]))

    def test_missing_malformed_conflicting_and_receipt_cap_are_explicit(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'observations.jsonl'
            self.assertFalse(collect_receipts(path)['complete'])
            ident=str(uuid.uuid4())
            entries=[dict(state='observed',receipt_id=str(uuid.uuid4()),query_sha256='a'*64) for _ in range(17)]
            entries += [dict(state='observed',receipt_id=ident,query_sha256='b'*64),
                        dict(state='observed',receipt_id=ident,query_sha256='c'*64),
                        dict(state='observed',receipt_id='PRIVATE',query_sha256='PRIVATE')]
            path.write_text('[]\n'+json.dumps(dict(schema='cairn.task-hook-observation/1',search_receipts=entries,search_receipts_omitted=1)))
            value=collect_receipts(path)
            self.assertEqual(len(value['receipts']),16)
            self.assertIn('receipt_limit',value['unknown'])
            self.assertIn('receipt_digest_conflict',value['unknown'])
            self.assertIn('malformed_hook_observation',value['unknown'])
            self.assertNotIn('PRIVATE',json.dumps(value))

    def test_missing_owned_route_is_unknown_without_catching_interrupt(self):
        store=object.__new__(te.TrialStore)
        with tempfile.TemporaryDirectory() as root:
            with patch.object(te,'disposable_pg',side_effect=SystemExit('PRIVATE route')):
                info=retain_hook_explanations(store,Path(root))
            self.assertFalse(info['complete'])
            self.assertNotIn('PRIVATE',(Path(root)/info['artifact']).read_text())
            with patch.object(te,'disposable_pg',side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):retain_hook_explanations(store,Path(root))

    def test_nested_metadata_does_not_hide_later_valid_receipt(self):
        ident=str(uuid.uuid4())
        row=dict(schema='cairn.task-hook-observation/1',search_receipts=[dict(state='observed',receipt_id=ident,query_sha256='a'*64)],search_receipts_omitted=0)
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'observations.jsonl'
            path.write_text('['*2000+'0'+']'*2000+'\n'+json.dumps(row))
            observed=collect_receipts(path)
            self.assertEqual(observed['receipts'][0]['receipt_id'],ident)
            self.assertFalse(observed['complete'])

    def test_output_cap_retains_prefix_with_exact_sidecar_encoding(self):
        from types import SimpleNamespace
        observed=dict(complete=True,unknown=[],receipts=[dict(receipt_id=str(uuid.uuid4()),query_sha256='a'*64) for _ in range(8)])
        def run(*args,**kwargs):
            return SimpleNamespace(stdout=b'{"owned":false}')
        with patch('trial_receipt_explanation.MAX_OUTPUT_BYTES',1200):
            result=export_receipts('psql','socket','database',{},run,'agent:task-eval-baseline','trial:task-eval',observed)
            self.assertGreater(len(result['receipts']),0)
            self.assertLess(len(result['receipts']),len(observed['receipts']))
            self.assertIn('output_byte_limit',result['unknown'])
            class Store:
                def receipt_explanations(self, observed):return result
            with tempfile.TemporaryDirectory() as root:
                info=retain_hook_explanations(Store(),Path(root))
                self.assertIsNotNone(info['artifact'])
                self.assertLessEqual(info['bytes'],1200)
                self.assertEqual(json.loads((Path(root)/info['artifact']).read_bytes()),result)

    def test_export_and_retention_failure_do_not_prevent_cleanup(self):
        class BrokenStore:
            def receipt_explanations(self, observed):
                raise RuntimeError('PRIVATE exception /secret/path')
        cleaned=[]
        with tempfile.TemporaryDirectory() as root:
            with ExitStack() as lifetime:
                lifetime.callback(cleaned.append,True)
                result=retain_hook_explanations(BrokenStore(),Path(root))
                self.assertFalse(result['complete'])
                self.assertNotIn('PRIVATE',json.dumps(result))
            self.assertEqual(cleaned,[True])
            class Store:
                def receipt_explanations(self, observed):return dict(complete=True,unknown=[])
            (Path(root)/'receipt-explanations.json').unlink()
            (Path(root)/'receipt-explanations.json').mkdir()
            self.assertIsNone(retain_hook_explanations(Store(),Path(root))['artifact'])


@unittest.skipUnless(os.environ.get('CAIRN_TASK_EVAL_PG') and os.environ.get('CAIRN_TASK_EVAL_BINARY'),
                     'requires owned wrapper and explicit test binary')
class ReceiptStoreTests(unittest.TestCase):
    def setUp(self):
        self.resources=ExitStack();self.addCleanup(self.resources.close)
        directory=self.resources.enter_context(tempfile.TemporaryDirectory(prefix='cairn-receipt-export-test-'))
        self.store=te.TrialStore(Path(directory)/'store',os.environ['CAIRN_TASK_EVAL_BINARY'],'explanation_'+uuid.uuid4().hex[:8])
        self.resources.callback(self.store.stop)
        self.store.remember(dict(id='one',kind='lesson',body='cobalt restore PRIVATE_BODY first',shareable=True))
        self.store.remember(dict(id='two',kind='lesson',body='cobalt PRIVATE_BODY second',shareable=True))
        self.local=self.store.remember(dict(id='local',kind='lesson',body='cobalt PRIVATE_LOCAL',shareable=False))
        self.other=self.store.remember(dict(id='other',kind='lesson',body='cobalt PRIVATE_OTHER',shareable=True,repo=te.OTHER_REPO))
        self.store.start()

    def search(self):
        return self.store.agent('search','--repo',te.TRIAL_REPO,'--task','fixture','--run','export','--tokens','32000','cobalt restore')

    def observed(self, found):
        return dict(complete=True,unknown=[],receipts=[dict(receipt_id=found['receipt_id'],query_sha256=hashlib.sha256(b'cobalt restore').hexdigest())])

    def sql(self, query):
        return te.run([self.store.pg_bin/'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-h',self.store.pg_socket,'-d',self.store.database,'-c',query],env=self.store.env).stdout

    def test_original_rank_projection_and_no_database_mutation(self):
        found=self.search();before=self.store.fingerprint()
        counts=self.sql('SELECT count(*) FROM cairn.retrieval_receipt; SELECT count(*) FROM cairn.record_use; SELECT count(*) FROM cairn.retrieval_candidate;')
        result=self.store.receipt_explanations(self.observed(found));self.assertTrue(result['complete'],result)
        row=result['receipts'][0];self.assertEqual(row['status'],'observed');self.assertEqual(row['candidate_count'],2)
        self.assertEqual([c['record_id'] for c in row['candidates']],[e['record_id'] for e in found['index']])
        self.assertEqual([c['rank'] for c in row['candidates']],[1,2])
        self.assertTrue(all(c['reason']=='INDEXED' for c in row['candidates']))
        text=json.dumps(result);self.assertNotIn('PRIVATE',text);self.assertNotIn('facts',text)
        self.assertNotIn(self.local['record_id'],text);self.assertNotIn(self.other['record_id'],text)
        self.assertEqual(before,self.store.fingerprint())
        self.assertEqual(counts,self.sql('SELECT count(*) FROM cairn.retrieval_receipt; SELECT count(*) FROM cairn.record_use; SELECT count(*) FROM cairn.retrieval_candidate;'))
        # Retention is independent of current-note state; no new retrieval required.
        # Use a legitimate new hosted-owned version, not a historical row rewrite.
        draft=dict(kind='lesson',body='cobalt restore version one',scope=dict(repo=te.TRIAL_REPO,task_id='*',run_id='*'),sensitivity='shareable',claim_type='self')
        created=self.store.agent('create',payload=dict(request_id=str(uuid.uuid4()),draft=draft))
        original=self.search();retained=self.store.receipt_explanations(self.observed(original))
        edited=self.store.agent('edit',payload=dict(request_id=str(uuid.uuid4()),record_id=created['record_id'],expected_version=1,draft=dict(draft,body='cobalt restore version two')))
        self.assertEqual(edited['version'],2)
        self.assertEqual(retained,self.store.receipt_explanations(self.observed(original)))
        self.store.agent('edit',payload=dict(request_id=str(uuid.uuid4()),record_id=created['record_id'],expected_version=2,draft=dict(draft,body='unrelated cleanup fixture')))


    def test_missing_wrong_owner_wrong_repo_and_legacy_never_leak(self):
        found=self.search();observed=self.observed(found)
        missing=self.observed(dict(receipt_id=str(uuid.uuid4())))
        self.assertEqual(self.store.receipt_explanations(missing)['receipts'][0]['reason'],'missing_or_not_owned')
        for column, value in [('caller','agent:task-eval-OTHER'),('scope',json.dumps(dict(repo=te.OTHER_REPO)))]:
            old=self.sql(f"SELECT {column} FROM cairn.retrieval_receipt WHERE receipt_id='{found['receipt_id']}'").decode().strip()
            self.sql(f"UPDATE cairn.retrieval_receipt SET {column}='{value}' WHERE receipt_id='{found['receipt_id']}'")
            result=self.store.receipt_explanations(observed)
            self.assertEqual(result['receipts'][0]['candidates'],[])
            self.assertEqual(result['receipts'][0]['reason'],'missing_or_not_owned')
            self.sql(f"UPDATE cairn.retrieval_receipt SET {column}='{old}' WHERE receipt_id='{found['receipt_id']}'")
        self.sql(f"UPDATE cairn.retrieval_receipt SET explanation_version=0 WHERE receipt_id='{found['receipt_id']}'")
        self.assertEqual(self.store.receipt_explanations(observed)['receipts'][0]['reason'],'unsupported_or_legacy_explanation')

    def test_poison_metadata_and_candidate_cap_stay_explicit_and_bounded(self):
        found=self.search();ident=found['receipt_id']
        self.sql(f"UPDATE cairn.retrieval_candidate SET reason='PRIVATE_REASON',detail=detail || '{{\"rank\":true,\"idf_score\":\"PRIVATE_SCORE\",\"facts\":{{\"evidence\":\"PRIVATE_EVIDENCE\"}},\"body\":\"PRIVATE_BODY\"}}'::jsonb WHERE receipt_id='{ident}'")
        result=self.store.receipt_explanations(self.observed(found))
        self.assertFalse(result['complete']);self.assertNotIn('PRIVATE',json.dumps(result))
        self.assertTrue(all(c['rank'] is None for c in result['receipts'][0]['candidates']))
        # Smaller patched test cap exercises the actual SQL LIMIT/explicit count path.
        with patch('trial_receipt_explanation.MAX_CANDIDATES',1):
            result=self.store.receipt_explanations(self.observed(self.search()))
        row=result['receipts'][0];self.assertTrue(row['truncated']);self.assertEqual(row['candidate_count'],2)
        self.assertEqual(len(row['candidates']),1);self.assertFalse(result['complete'])

    def test_sidecar_persists_before_cleanup_without_private_text(self):
        found=self.search()
        with tempfile.TemporaryDirectory() as root:
            base=Path(root);(base/'hook-state').mkdir()
            (base/'hook-state/observations.jsonl').write_text(json.dumps(dict(schema='cairn.task-hook-observation/1',search_receipts=[dict(state='observed',**self.observed(found)['receipts'][0])],search_receipts_omitted=0))+'\n')
            info=retain_hook_explanations(self.store,base)
            self.assertTrue(info['complete']);self.assertNotIn('PRIVATE',(base/info['artifact']).read_text())

    def test_actual_cmd_agent_grading_and_store_cleanup_survive_export_failure(self):
        import contextlib
        import io
        import sys
        from test_trial_task_prospective import fixture
        for grade_failure in (False,True):
            with self.subTest(grade_failure=grade_failure), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);inp=fixture(root/'input')
                with contextlib.redirect_stdout(io.StringIO()):te.main(['freeze-input','--input',str(inp)])
                child=root/'native.py'
                child.write_text("import json,sys\nprint(json.dumps({'type':'system','subtype':'init','model':'fixture-model'}))\nprint(sys.stdin.readline().strip())\nprint(json.dumps({'type':'result','is_error':False,'subtype':'success','result':'inspected','num_turns':1,'permission_denials':[]}))\n")
                def sandbox(work,cwd,binds,env,argv,harness='claude',**kwargs):
                    return [sys.executable,str(child)] if argv[0]=='claude' else argv
                stopped=[];original_stop=te.TrialStore.stop
                def stop(store):
                    server=store.server;sock=store.sockdir
                    original_stop(store)
                    te.run([store.pg_bin/'dropdb','-h',store.pg_socket,store.database],env=store.env)
                    stopped.append(server is not None and server.poll() is not None and not sock.exists())
                original_grade=te.grade
                def grade(case,ctx):
                    if grade_failure:raise RuntimeError('synthetic grader failure')
                    return original_grade(case,ctx)
                conf=dict(binary=os.environ['CAIRN_TASK_EVAL_BINARY'],hook=str(te.ROOT/'integrations/lifecycle/memory.py'),recall_mode='ambient',semantic_fallback=False)
                with patch.object(te,'copy_arms',return_value=(['none','baseline'],{'baseline':conf})), \
                     patch.object(te,'sandbox_command',side_effect=sandbox), \
                     patch.object(te.random.Random,'shuffle',lambda _,order: order.reverse()), \
                     patch.object(te.TrialStore,'receipt_explanations',side_effect=ValueError('PRIVATE EXPORT FAILURE')), \
                     patch.object(te.TrialStore,'stop',stop),patch.object(te,'grade',side_effect=grade), \
                     contextlib.redirect_stdout(io.StringIO()):
                    te.main(['agent','--prospective-input',str(inp),'--output',str(root/'out'),'--model','fixture-model','--reasoning-effort','high','--parallel','2','--distractors','0'])
                report=json.loads((root/'out/agent.json').read_text())
                record=next(r for r in report['records'] if r['arm']=='baseline')
                none=next(r for r in report['records'] if r['arm']=='none')
                self.assertIsNone(none.get('receipt_explanation_export'))
                self.assertEqual(record['outcome'],'harness_error' if grade_failure else 'correct')
                artifact=record['receipt_explanation_export'];self.assertFalse(artifact['complete'])
                data=(root/'out/runs/new-work.baseline.s0'/artifact['artifact']).read_bytes()
                self.assertEqual(artifact['sha256'],hashlib.sha256(data).hexdigest())
                self.assertNotIn(b'PRIVATE',data);self.assertEqual(stopped,[True])
                self.assertFalse((root/'out/stores/baseline/agent.token').exists())
