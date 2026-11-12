"""Residual evidence must keep exact actors, guard boundaries and scope claims."""
import copy
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import check_semantic_reference as reference
import semantic_refusal_function_evidence as function_evidence
import semantic_refusal_functions as functions
import semantic_refusal_native_residuals as native
import semantic_refusal_residual_fixture as fixture
import semantic_refusal_source_residuals as source


class ResidualEvidenceTest(unittest.TestCase):
    def test_only_exact_reviewed_baseline_can_supply_old_producer_bindings(self):
        value=reference.inventory();baseline=reference.accepted_baseline(value)
        accepted=reference.producer_binding(value,'early',baseline['reports']['early']['sha256'])
        current=reference.producer_binding(value,'early','0'*64)
        self.assertEqual(accepted['ledger'],baseline['ledger_sha256'])
        self.assertEqual(current['ledger'],value['failure_ledger']['sha256'])
        self.assertNotEqual(accepted['origin'],current['origin'])
        for mutation in ('actor-omitted','actor-changed','acceptance-added'):
            altered=copy.deepcopy(baseline)
            if mutation=='actor-omitted':altered['actors'].pop('semantic/stream.go')
            elif mutation=='actor-changed':altered['actors']['semantic/stream.go']='0'*64
            else:altered['accepted']=True
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);path=root/'baseline.json';reference.save(path,altered)
                changed=dict(value,failure_baseline=dict(relative=path.name,sha256=reference.digest(path)))
                with patch.object(reference,'PROJECT',root),self.assertRaisesRegex(ValueError,'reviewed bytes'):
                    reference.accepted_baseline(changed)

    def test_baseline_still_checks_current_actor_bytes(self):
        value=reference.inventory();real=reference.pinned
        def changed(root,relative,sha):
            if relative=='semantic/stream.go':raise ValueError('artifact hash mismatch: changed actor')
            return real(root,relative,sha)
        with patch.object(reference,'pinned',side_effect=changed),self.assertRaisesRegex(ValueError,'changed actor'):
            reference.accepted_baseline(value)

    def test_function_producer_binding_is_specific_to_executed_fixture_and_inputs(self):
        value=reference.inventory();expected=function_evidence.producer_spec(value)
        with patch.object(function_evidence,'DRIVER',function_evidence.DRIVER+'\n# changed bootstrap'):
            self.assertNotEqual(function_evidence.producer_spec(value),expected)
        real=reference.digest
        catalogue=reference.failure_inventory(value)
        with patch.object(reference,'failure_inventory',return_value=catalogue),\
             patch.object(reference,'digest',side_effect=lambda p:'0'*64 if Path(p).name=='semantic_refusal_functions.py' else real(p)):
            self.assertNotEqual(function_evidence.producer_spec(value),expected)
        catalogue=reference.failure_inventory(value)
        row=next(s for r in catalogue['cases'] for s in r['subcases'] if s['id'] in function_evidence.RANK_ERRORS)
        row['input']['variant']='different'
        with patch.object(reference,'failure_inventory',return_value=catalogue):
            self.assertNotEqual(function_evidence.producer_spec(value),expected)

    def test_function_refusals_reject_model_claim_or_partial_cache_even_when_resealed(self):
        rows=functions.rank_and_retreat()
        with patch.object(function_evidence,'IDENTITY_IDS',set()):
            checked=function_evidence.validate_rows({},Path('/unused'),rows,None,None)
            self.assertEqual(len(checked),6)
            for mutation in ('model','inference','cache','exception','counter','input'):
                changed=copy.deepcopy(rows)
                if mutation=='model':changed[0]['model_initialized']=True
                elif mutation=='inference':changed[0]['real_model_inference']=True
                elif mutation=='cache':changed[0]['cache_after']={}
                elif mutation=='exception':changed[0]['exception']['message']='different guard'
                elif mutation=='counter':changed[0]['model_double_calls']=1
                else:changed[0]['input']['decoded_overflow']=False
                with self.subTest(mutation=mutation),self.assertRaises(ValueError):
                    function_evidence.validate_rows({},Path('/unused'),changed,None,None)

    def test_actual_stream_serializer_accepts_exact_bound_and_refuses_next_byte(self):
        for kind in ('ordinary','combined'):
            for edge in ('boundary','over'):
                with self.subTest(kind=kind,edge=edge),tempfile.TemporaryDirectory() as directory:
                    output=io.BytesIO();sidecar=Path(directory)/'double.json'
                    with patch.object(fixture.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(source.cap_input()))),\
                         patch.object(fixture.sys,'stdout',SimpleNamespace(buffer=output)):
                        if edge=='over':
                            message='worker response exceeds limit' if kind=='ordinary' else 'worker response and cache exceed limit'
                            with self.assertRaisesRegex(ValueError,message):fixture.caps(kind,edge,sidecar)
                        else:fixture.caps(kind,edge,sidecar)
                    observation=reference.load(sidecar)
                    self.assertFalse(observation['model_initialized']);self.assertFalse(observation['real_model_inference'])
                    if edge=='over':self.assertEqual(output.getvalue(),b'')
                    else:self.assertEqual(len(output.getvalue()),65536 if kind=='ordinary' else 614400+65536)

    def test_maximum_success_is_absent_from_new_execution_selectors(self):
        ids={r['id'] for r in source.inputs()}|source.CAP_IDS|native.STORE_IDS|{native.IDLE_ID}
        self.assertEqual(len(ids),22)
        self.assertFalse(any('65536-byte-document' in name for name in ids))
        for case in source.inputs():self.assertLess(len(case['input']),1024)

    def test_native_and_source_readers_refuse_extra_acceptance_or_model_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            for report,reader in [(dict(schema='cairn.semantic-native-residuals/1',accepted=True),native.verify),
                                  (dict(schema='cairn.semantic-source-residuals/1',accepted=True),source.verify)]:
                reference.save(path,report)
                with self.assertRaises(ValueError):
                    if reader is native.verify:reader({},path,reference.digest(path))
                    else:reader({},path.parent,path,reference.digest(path))


if __name__=='__main__':unittest.main()
