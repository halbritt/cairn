"""Private selected-material issuer tests; frozen source only, no actors/grading."""
import base64
import json
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trial_legacy_case_materials as materials
import trial_legacy_blind_selection as blind
import trial_paired_request as paired
import trial_task_eval as task_eval
import trial_legacy_pregrade as pregrade
from trial_attempt_custody import AttemptBaseline


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
        self.assertIn('action_trace', cases['db-coverage']['channels'])
        self.assertEqual(cases['db-coverage']['unavailable_channels'], [])
        self.assertEqual(cases['db-coverage']['action_trace_projection'],
                         task_eval.DB_COVERAGE_ACTION_PROJECTION)
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

    def test_changed_db_coverage_selects_only_its_bound_safe_action_channel(self):
        pin = materials.issue(self.output)
        manifest_pin = {key: pin[key] for key in ('schema', 'path', 'bytes', 'sha256')}
        selected = blind._materials(dict(schema=blind.SELECTED_REQUEST_SCHEMA,
            case_materials=manifest_pin), [('db-coverage', 0)],
            dict(evaluator_sha256=hashlib.sha256(Path(task_eval.__file__).read_bytes()).hexdigest()))
        row = selected['db-coverage']
        self.assertIn('action_trace', row['channels'])
        self.assertEqual(row['unavailable_channels'], [])
        self.assertEqual(row['action_trace_projection'], task_eval.DB_COVERAGE_ACTION_PROJECTION)

    def test_projection_selection_is_limited_to_frozen_db_coverage_source(self):
        cases = {case['id']: case for case in task_eval.load_cases(12)}
        self.assertEqual(materials._action_trace_projection(cases['db-coverage']),
                         task_eval.DB_COVERAGE_ACTION_PROJECTION)
        self.assertTrue(all(materials._action_trace_projection(case) is None
                            for case_id, case in cases.items() if case_id != 'db-coverage'))

    def test_manifest_refuses_a_changed_projection_declaration(self):
        pin = materials.issue(self.output)
        manifest_path = Path(pin['path'])
        manifest = json.loads(manifest_path.read_bytes())
        db = next(row for row in manifest['cases'] if row['case'] == 'db-coverage')
        db['action_trace_projection'] = 'cairn.legacy-action-trace/1'
        encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode() + b'\n'
        manifest_path.write_bytes(encoded)
        reference = dict(schema=pin['schema'], path=pin['path'], bytes=len(encoded),
                         sha256=hashlib.sha256(encoded).hexdigest())
        with self.assertRaisesRegex(paired.Refusal, '^legacy_material_manifest_case_mismatch$'):
            materials.read_manifest(reference)

    def test_genuine_pregrade_pin_projects_only_blind_workspace_and_git_artifacts(self):
        out = Path(self.temp.name) / 'result'
        base = out / 'runs' / 'synthetic.none.s0'
        cwd = base / 'work' / 'repo'
        cwd.mkdir(parents=True)
        (cwd / 'result.txt').write_text('actor result\n')
        env = dict(os.environ, GIT_AUTHOR_NAME='Synthetic', GIT_AUTHOR_EMAIL='synthetic@example.invalid',
                   GIT_COMMITTER_NAME='Synthetic', GIT_COMMITTER_EMAIL='synthetic@example.invalid')
        for command in (['git', 'init', '-q'], ['git', 'add', '.'], ['git', 'commit', '-qm', 'initial'],
                        ['git', 'tag', 'eval-base']):
            subprocess.run(command, cwd=cwd, env=env, check=True, capture_output=True)
        (base / 'work' / '.eval-snapshot.json').write_text(json.dumps({
            'result.txt': hashlib.sha256(b'actor result\n').hexdigest()}))
        case = dict(id='synthetic', cwd='repo')
        baseline = AttemptBaseline.capture(base, case, 'none', 0)
        reviewed = dict(schema='cairn.writer-reviewed-identity/2',
            kernel=dict(release='kernel', version='build'), bwrap=dict(path='/usr/bin/bwrap', sha256='a'*64),
            source_scope=dict(kernel='reviewed', bwrap='reviewed'), execution='sealed_memfd')
        writer = dict(schema='cairn.legacy-writer-release/1',
            attempt=dict(run_id='synthetic.none.s0', workspace=str(cwd)), state='released', release='released',
            bootstrap=dict(state='member'), close_errors=[], complete_descendant_custody=False,
            writer_quiescence=dict(state='namespace_init_exited',
                basis='reviewed_identity_source_zap_before_pidfd_notify', scope=reviewed['source_scope']),
            init=dict(state='bound', pid_namespace=dict(inode=123, dev=1)), init_exit='exited',
            launch_evidence=dict(namespaces=dict(pid=123)),
            runtime_identity=dict(kernel=reviewed['kernel'], bwrap=reviewed['bwrap']),
            reviewed_identity=reviewed, execution_binding=dict(mode='sealed_memfd', sha256='a'*64))
        stream = b'{"type":"result","result":"done"}\n'
        prompt = 'Synthetic task.'
        pin = pregrade.capture(base=base, case=case, arm='none', seed=0, baseline=baseline,
                               writer_record=writer, stream_bytes=stream, prompt=prompt)
        writer['final_artifact_custody'] = pin
        (base / 'writer-release.json').write_text(json.dumps(writer))
        stream_path = base / 'stream.jsonl'
        stream_path.write_bytes(stream)
        record = dict(run_id='synthetic.none.s0', case='synthetic', arm='none', seed=0,
            baseline_custody=baseline.pin, final_artifact_custody=pin, writer_release=writer,
            prompt_bytes=len(prompt.encode()), prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            stream_sha256=hashlib.sha256(stream).hexdigest())
        _raw, snapshot, public, observed_pin = blind._pregrade_snapshot(out, record, case, stream)
        self.assertEqual(observed_pin, pin)
        self.assertIn(b'actor result', base64.b64decode(snapshot['workspace']['entries'][0]['content_b64']))
        self.assertNotIn(str(cwd).encode(), public)
        self.assertNotIn(b'synthetic.none.s0', public)
        self.assertNotIn(b'final_artifact_custody', public)


if __name__ == '__main__':
    unittest.main()
