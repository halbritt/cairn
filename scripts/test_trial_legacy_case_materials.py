"""Private selected-material issuer tests; frozen source only, no actors/grading."""
import json
import hashlib
from pathlib import Path
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trial_legacy_case_materials as materials
import trial_legacy_blind_selection as blind
import trial_paired_request as paired
import trial_task_eval as task_eval


class CaseMaterialsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name) / 'issued'

    def test_issues_private_v12_materials_and_truthful_per_case_channels(self):
        pin = materials.issue(self.output)
        reference = {key: pin[key] for key in ('schema', 'path', 'bytes', 'sha256')}
        manifest = materials.read_manifest(reference)
        self.assertEqual(manifest['label_version'], 12)
        self.assertEqual(len(manifest['cases']), 21)
        cases = {row['case']: row for row in manifest['cases']}
        self.assertEqual(cases['rhumb-ci-scope']['channels'], [
            'task_statement', 'grading_instructions', 'final_answer'])
        self.assertNotIn('action_trace', cases['db-coverage']['channels'])
        self.assertEqual(cases['db-coverage']['unavailable_channels'], [
            dict(name='action_trace', reason='raw_tool_output_unavailable')])
        self.assertIn('artifact_bundle', cases['db-coverage']['channels'])
        self.assertNotIn('action_trace', cases['deploy-stamp']['channels'])
        self.assertIn('artifact_bundle', cases['deploy-stamp']['channels'])
        self.assertIn('evaluator_observation', cases['deploy-stamp']['channels'])
        self.assertNotIn('evaluator_observation', cases['db-coverage']['channels'])
        eval_contract = manifest['evaluator_observation_contract']
        self.assertEqual(eval_contract['schema'], materials.EVALUATOR_CONTRACT_SCHEMA)
        self.assertEqual(eval_contract['execution']['aggregate_evaluator_exit'], 'not_a_process')
        self.assertIn('check verdicts', eval_contract['redactions'])

        info = self.output.stat()
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
        for row in manifest['cases']:
            contract = json.loads(Path(row['contract']['path']).read_bytes())
            self.assertEqual(contract['required_channels'], row['channels'])
            self.assertNotIn('correct', contract)
            self.assertNotIn('mistake', contract)
            task = Path(row['task_statement']['path']).read_bytes()
            grading = Path(row['grading_instructions']['path']).read_bytes()
            self.assertTrue(task)
            self.assertIn(b'Review question:', grading)
        serialized = Path(reference['path']).read_bytes()
        self.assertNotIn(b'"correct"', serialized)
        self.assertNotIn(b'"mistake"', serialized)
        self.assertEqual(pin['storage'], 'private_owner_only')

    def test_tampered_material_refuses_even_when_manifest_pin_is_unchanged(self):
        pin = materials.issue(self.output)
        reference = {key: pin[key] for key in ('schema', 'path', 'bytes', 'sha256')}
        manifest = json.loads(Path(reference['path']).read_bytes())
        material = Path(manifest['cases'][0]['task_statement']['path'])
        material.write_bytes(material.read_bytes() + b'changed')
        with self.assertRaisesRegex(paired.Refusal, '^legacy_material_manifest_pin_mismatch$'):
            materials.read_manifest(reference)

    def test_issuer_refuses_a_conflicting_existing_package(self):
        materials.issue(self.output)
        with self.assertRaisesRegex(paired.Refusal, '^legacy_material_output_conflict$'):
            materials.issue(self.output)

    def test_changed_db_coverage_selects_an_explicitly_unavailable_action_channel(self):
        pin = materials.issue(self.output)
        manifest_pin = {key: pin[key] for key in ('schema', 'path', 'bytes', 'sha256')}
        selected = blind._materials(dict(schema=blind.SELECTED_REQUEST_SCHEMA,
            case_materials=manifest_pin), [('db-coverage', 0)],
            dict(evaluator_sha256=hashlib.sha256(Path(task_eval.__file__).read_bytes()).hexdigest()))
        row = selected['db-coverage']
        self.assertNotIn('action_trace', row['channels'])
        self.assertEqual(row['unavailable_channels'], [
            dict(name='action_trace', reason='raw_tool_output_unavailable')])


if __name__ == '__main__':
    unittest.main()
