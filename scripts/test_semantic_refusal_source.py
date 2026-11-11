"""Refusal readers reject incomplete captures and mismatched process scope."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import check_semantic_reference as reference
import semantic_refusal_source as source


class SourceObservationTest(unittest.TestCase):
    def fixture(self, folder):
        case=dict(id='F-EMBED/blank-query/empty',mode='embed',cache=True,
                  input=b'{"id":"query","operation":"query","text":""}\n',
                  error='ValueError: query exceeds input bounds')
        invocation=dict(pid=10,start_ticks='100',role='S01b-source-model-refusal',mode=case['id'])
        reference.save(folder/'result.json',dict(exit_code=1,invocation=invocation))
        (folder/'worker.stdout').write_bytes(b'')
        (folder/'worker.stderr').write_text(case['error']+'\n')
        worker=dict(pid=11,launcher_pid=10,launcher_start_ticks='100',role=invocation['role'],
                    mode=case['id'],environment_matches_contract=True)
        reference.save(folder/'runtime-observations.json',dict(model_workers=[worker],errors=[]))
        return case

    def test_direct_source_scope_has_no_go_or_discovery_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);case=self.fixture(folder)
            actual=source.outcome(folder,case)
            self.assertEqual(actual['disposition']['process_exit_kill'],dict(state='observed',exit_code=1,killed=False))
            self.assertEqual(actual['disposition']['stdout_prefix']['hex'],'')
            self.assertEqual(actual['disposition']['go_error_or_result']['state'],'not-exercised')
            self.assertEqual(actual['disposition']['discovery_coverage']['state'],'not-exercised')
            self.assertEqual(actual['disposition']['restarts']['count'],0)

    def test_another_invocation_cannot_supply_model_evidence(self):
        for change in [dict(launcher_pid=20),dict(launcher_start_ticks='200'),
                       dict(mode='other'),dict(role='other'),dict(environment_matches_contract=False)]:
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                folder=Path(directory);case=self.fixture(folder)
                runtime=reference.load(folder/'runtime-observations.json')
                runtime['model_workers'][0].update(change)
                reference.save(folder/'runtime-observations.json',runtime)
                with self.assertRaisesRegex(ValueError,'this invocation'):source.outcome(folder,case)

    def test_partial_reply_or_wrong_exception_cannot_be_a_refusal(self):
        for stdout,stderr,exit_code in [(b'{}\n','ValueError: query exceeds input bounds\n',1),
                                      (b'','ValueError: another guard\n',1),
                                      (b'','ValueError: query exceeds input bounds\n',0)]:
            with tempfile.TemporaryDirectory() as directory:
                folder=Path(directory);case=self.fixture(folder)
                (folder/'worker.stdout').write_bytes(stdout);(folder/'worker.stderr').write_text(stderr)
                result=reference.load(folder/'result.json');result['exit_code']=exit_code
                reference.save(folder/'result.json',result)
                with self.assertRaises(ValueError):source.outcome(folder,case)

    def test_interrupted_producer_provenance_cannot_be_relabelled_as_fresh(self):
        value=reference.inventory()
        manifest=dict(schema='cairn.semantic-interrupted-source-capture/1',root='/unused',
            actor_sources={p:value['source_sha256'][p] for p in source.ACTOR_SOURCES},
            python_sha256=value['python_sha256'],runtime_binding_sha256=value['retained']['runtime-binding']['sha256'],
            assets_sha256=value['assets_sha256'],parameters={},
            producer_tool_provenance='not retained by interrupted collector; exact raw captures sealed after interruption',cases=[])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'manifest.json';reference.save(path,manifest)
            self.assertEqual(source.reuse_manifest(value,path,reference.digest(path),{}, {},set()),({},[]))
            for change in [dict(producer_tool_provenance='fresh actual-model proof'),dict(accepted=True),
                           dict(python_sha256='wrong')]:
                reference.save(path,dict(manifest,**change))
                with self.assertRaises(ValueError):source.reuse_manifest(value,path,reference.digest(path),{}, {},set())

    def test_file_seal_cannot_omit_an_input_used_by_decoder(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'case';folder.mkdir()
            for name in ('result.json','worker.stdout'):(folder/name).write_text('{}')
            hashes={str(p.relative_to(root)):reference.digest(p) for p in folder.iterdir()}
            self.assertEqual(reference.pinned_case_files(root,'case',hashes,{'result.json'}),folder)
            omitted=copy.deepcopy(hashes);del omitted['case/worker.stdout']
            with self.assertRaisesRegex(ValueError,'unsealed'):reference.pinned_case_files(root,'case',omitted,{'result.json'})
            (folder/'worker.stdout').write_text('changed')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):reference.pinned_case_files(root,'case',hashes,{'result.json'})

    def test_model_or_acceptance_claim_cannot_be_added_to_source_report(self):
        value=reference.inventory()
        report=dict(schema='cairn.semantic-source-refusals/1',actor_sources={},producer_ledger_sha256='',
            producer_tool_sha256={},python_sha256='',runtime_binding_sha256='',assets_sha256={},cases=[],
            failed=[],model_execution=True,parameters={})
        for change in [dict(model_execution=False),dict(accepted=True),dict(scope='Go-supervisor')]:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'report.json';reference.save(path,dict(report,**change))
                with self.assertRaisesRegex(ValueError,'unsupported source'):source.verify(value,path.parent,path,reference.digest(path))


if __name__=='__main__':
    unittest.main()
