"""Synthetic manifest verifier checks; no original case or grading material."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_task_eval as task


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8') + b'\n'


class CaseMaterialsPinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        case = dict(id='synthetic', prompt='synthetic only', correct=[dict(type='file', path='x')])
        cases_raw = canonical(dict(cases=[case]))
        (self.root / 'cases.json').write_bytes(cases_raw)
        frozen_raw = b'{"synthetic frozen labels"}\n'
        (self.root / 'FROZEN-v12.json').write_bytes(frozen_raw)
        labels = 'a' * 64
        self.frozen = dict(labels_sha256=labels)
        self.case = case
        self.document = dict(
            schema=task.CASE_MATERIALS_SCHEMA, label_version=12,
            frozen_sha256=hashlib.sha256(frozen_raw).hexdigest(), labels_sha256=labels,
            cases_sha256=hashlib.sha256(cases_raw).hexdigest(),
            evaluator_sha256=hashlib.sha256(Path(task.__file__).read_bytes()).hexdigest(),
            files_sha256=labels, evaluator_observation_contract=dict(schema='synthetic-contract/1'),
            cases=[dict(case='synthetic', channels=['task_statement', 'grading_instructions', 'final_answer',
                                                       'artifact_bundle'], unavailable_channels=[],
                        contract=self.material('contract'),
                        task_statement=self.material('task'), grading_instructions=self.material('instructions'),
                        source_case_sha256=hashlib.sha256(canonical(case)).hexdigest())])
        self.manifest = self.root / 'materials.json'
        self.write_manifest()

    @staticmethod
    def material(label):
        return dict(path=label+'.json', bytes=5, sha256=hashlib.sha256(label.encode()).hexdigest())

    def write_manifest(self):
        raw = canonical(self.document)
        self.manifest.write_bytes(raw)
        self.manifest.chmod(0o600)
        self.sha = hashlib.sha256(raw).hexdigest()

    def verify(self):
        with patch.object(task, 'verify_frozen', return_value=self.frozen):
            return task.case_materials_pin(str(self.manifest), self.sha, [self.case], root=self.root)

    def test_valid_manifest_returns_only_schema_and_digest(self):
        self.assertEqual(self.verify(), dict(schema=task.CASE_MATERIALS_SCHEMA, sha256=self.sha))

    def test_refuses_changed_manifest_digest_and_evaluator_source(self):
        with self.assertRaisesRegex(ValueError, 'digest_mismatch'):
            task.case_materials_pin(str(self.manifest), '0'*64, [self.case], root=self.root)
        self.document['evaluator_sha256'] = '0'*64
        self.write_manifest()
        with patch.object(task, 'verify_frozen', return_value=self.frozen):
            with self.assertRaisesRegex(ValueError, 'source_mismatch'):
                task.case_materials_pin(str(self.manifest), self.sha, [self.case], root=self.root)

    def test_refuses_material_source_mismatch_and_noncanonical_bytes(self):
        self.document['cases'][0]['source_case_sha256'] = '0'*64
        self.write_manifest()
        with patch.object(task, 'verify_frozen', return_value=self.frozen):
            with self.assertRaisesRegex(ValueError, 'case_source_mismatch'):
                task.case_materials_pin(str(self.manifest), self.sha, [self.case], root=self.root)
        self.manifest.write_bytes(self.manifest.read_bytes().rstrip(b'\n'))
        self.manifest.chmod(0o600)
        digest = hashlib.sha256(self.manifest.read_bytes()).hexdigest()
        with patch.object(task, 'verify_frozen', return_value=self.frozen):
            with self.assertRaisesRegex(ValueError, 'not_canonical'):
                task.case_materials_pin(str(self.manifest), digest, [self.case], root=self.root)

    def test_requires_explicit_unavailable_action_channel_for_tool_output_checks(self):
        self.case['correct'].append(dict(type='tool_output', pattern='expected'))
        self.document['cases'][0]['channels'] = ['task_statement', 'grading_instructions',
                                                  'final_answer', 'artifact_bundle']
        self.document['cases'][0]['unavailable_channels'] = [
            dict(name='action_trace', reason='raw_tool_output_unavailable')]
        self.document['cases'][0]['source_case_sha256'] = hashlib.sha256(canonical(self.case)).hexdigest()
        self.write_manifest()
        self.assertEqual(self.verify()['sha256'], self.sha)
        self.document['cases'][0]['unavailable_channels'] = []
        self.write_manifest()
        with patch.object(task, 'verify_frozen', return_value=self.frozen):
            with self.assertRaisesRegex(ValueError, 'channel_contract_mismatch'):
                task.case_materials_pin(str(self.manifest), self.sha, [self.case], root=self.root)


if __name__ == '__main__':
    unittest.main()
