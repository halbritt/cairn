"""Capture/import mechanics only; manufactured archives are not model evidence."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import io
import struct
import zipfile
from pathlib import Path

import capture_semantic_fixtures as capture


def sealed_bundle(root, files):
    root.mkdir()
    rows = []
    for relative, contents in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = contents.encode() if isinstance(contents, str) else contents
        path.write_bytes(raw)
        rows.append(hashlib.sha256(raw).hexdigest() + '  ' + relative + '\n')
    raw = ''.join(rows).encode()
    (root / 'SHA256SUMS').write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


class CaptureSemanticFixturesTest(unittest.TestCase):
    def test_seal_validation_rejects_changed_file_and_changed_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'
            expected = sealed_bundle(root, {'raw/output.json': '{"synthetic":true}\n'})
            verified = capture.verify_sealed_bundle(root, expected)
            self.assertEqual(set(verified), {'raw/output.json'})
            (root / 'raw/output.json').write_text('{"changed":true}\n')
            with self.assertRaisesRegex(capture.EvidenceError, 'digest mismatch'):
                capture.verify_sealed_bundle(root, expected)
            (root / 'SHA256SUMS').write_text('')
            with self.assertRaisesRegex(capture.EvidenceError, 'seal digest mismatch'):
                capture.verify_sealed_bundle(root, expected)

    def test_seal_cannot_reach_outside_paths_or_follow_links(self):
        with tempfile.TemporaryDirectory() as directory:
            outside = Path(directory) / 'host-only'
            outside.write_text('synthetic guard')
            for relative in ('../host-only', '/host-only', 'raw/../host-only', 'raw\\host-only'):
                with self.subTest(path=relative):
                    root = Path(directory) / 'bundle'
                    root.mkdir(exist_ok=True)
                    raw = (hashlib.sha256(outside.read_bytes()).hexdigest() + '  ' + relative + '\n').encode()
                    (root / 'SHA256SUMS').write_bytes(raw)
                    with self.assertRaisesRegex(capture.EvidenceError, 'unsafe relative path'):
                        capture.verify_sealed_bundle(root, hashlib.sha256(raw).hexdigest())
            (root / 'linked').symlink_to(outside)
            raw = (hashlib.sha256(outside.read_bytes()).hexdigest() + '  linked\n').encode()
            (root / 'SHA256SUMS').write_bytes(raw)
            with self.assertRaisesRegex(capture.EvidenceError, 'symlink'):
                capture.verify_sealed_bundle(root, hashlib.sha256(raw).hexdigest())

    def test_import_refuses_missing_provenance_even_with_a_valid_seal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'
            expected = sealed_bundle(root, {'capture-manifest.json': json.dumps({
                'schema': 'cairn.semantic-reference-capture/1', 'observations': []})})
            output = Path(directory) / 'imported'
            with self.assertRaisesRegex(capture.EvidenceError, 'provenance'):
                capture.import_bundles([(root, expected)], output)
            self.assertFalse(output.exists())

    def test_cli_import_retains_manufactured_data_as_incomplete_without_a_pass_stamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'
            expected = sealed_bundle(root, {'capture-manifest.json': json.dumps({
                'schema': 'cairn.semantic-reference-capture/1',
                'provenance': {'kind': 'mechanics', 'description': 'manufactured import fixture'},
                'observations': []})})
            output = Path(directory) / 'catalog'
            result = subprocess.run([sys.executable, '-B', '-S', capture.__file__, 'import',
                '--bundle', str(root), expected, '--output', str(output)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 3, result.stderr)
            catalog = json.loads((output / 'catalog.json').read_text())
            self.assertEqual(catalog['phase_status'], 'incomplete')
            self.assertFalse(catalog['model_acceptance'])
            self.assertTrue(catalog['missing_cells'])

    def test_import_refuses_an_unsealed_output_instead_of_counting_the_claimed_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'
            expected = sealed_bundle(root, {'capture-manifest.json': json.dumps({
                'schema': 'cairn.semantic-reference-capture/1', 'provenance': {'kind': 'mechanics'},
                'observations': [{'cell': 'rank/storage-topic', 'status': 'passed',
                                  'artifacts': {'stdout': 'raw/not-sealed.json'}}]})})
            with self.assertRaisesRegex(capture.EvidenceError, 'unsealed required file'):
                capture.import_bundles([(root, expected)], Path(directory) / 'catalog')

    def test_cpu_class_normalizes_sets_and_refuses_an_unobserved_math_core(self):
        facts = {'architecture': 'x86_64', 'byte_order': 'little', 'pointer_bits': 64,
                 'effective_isa': ['AVX2', 'SSE2'], 'os_vector_state': ['X86_V3', 'AVX'],
                 'blas_core': 'observed-test-core', 'blas_config': 'observed-test-config'}
        first = capture.cpu_kernel_class(facts)
        reordered = dict(facts, effective_isa=['SSE2', 'AVX2', 'SSE2'], os_vector_state=['AVX', 'X86_V3'])
        self.assertEqual(first, capture.cpu_kernel_class(reordered))
        with self.assertRaisesRegex(capture.EvidenceError, 'CPU/kernel'):
            capture.cpu_kernel_class(dict(facts, blas_core=None))

    def test_claimed_mechanics_pass_is_still_unqualified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'
            cell = capture.reference_inventory()['required_cells'][0]
            expected = sealed_bundle(root, {
                'capture-manifest.json': json.dumps({'schema': 'cairn.semantic-reference-capture/1',
                    'provenance': {'kind': 'mechanics'}, 'observations': [
                        {'cell': cell, 'status': 'passed', 'artifacts': {'stdout': 'raw/result'}}]}),
                'raw/result': 'manufactured data; not a model result'})
            result = capture.import_bundles([(root, expected)], Path(directory) / 'catalog')
            self.assertEqual(result['cells'][0]['status'], 'unqualified')
            self.assertEqual(result['phase_status'], 'incomplete')
            self.assertFalse(result['model_acceptance'])

    def test_duplicate_json_keys_cannot_override_capture_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'
            expected = sealed_bundle(root, {'capture-manifest.json':
                '{"schema":"cairn.semantic-reference-capture/1","provenance":null,'
                '"provenance":{"kind":"mechanics"},"observations":[]}'})
            with self.assertRaisesRegex(capture.EvidenceError, 'duplicate JSON key'):
                capture.import_bundles([(root, expected)], Path(directory) / 'catalog')

    def test_review_digest_mismatch_refuses_before_loading_external_code(self):
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / 'semantic_acceptance_isolation.py'
            module.write_text("raise AssertionError('unreviewed code executed')\n")
            receipt = Path(directory) / 'review.json'
            receipt.write_text('{}')
            with self.assertRaisesRegex(capture.EvidenceError, 'receipt digest mismatch'):
                capture.capture_reference(output=Path(directory) / 'output', phase='rank',
                    python=Path(sys.executable), sources=Path(directory), assets=Path(directory),
                    isolation_module=module, isolation_review=receipt, isolation_review_sha256='0' * 64)
            self.assertFalse((Path(directory) / 'output').exists())

    def test_typed_archive_refuses_tampered_bits_nonfinite_data_and_object_dtype(self):
        # Manufactured .npy bytes test parsing only; no NumPy/model import.
        for dtype, values, description_sha in (
                ('<f4', struct.pack('<2f', 1.0, 2.0), None),
                ('<f4', struct.pack('<2f', 1.0, 3.0), hashlib.sha256(struct.pack('<2f', 1.0, 2.0)).hexdigest()),
                ('<f4', struct.pack('<2f', 1.0, float('nan')), None),
                ('|O', b'not-a-pickle', None)):
            with self.subTest(dtype=dtype, tampered=description_sha is not None):
                header = repr({'descr': dtype, 'fortran_order': False, 'shape': (1, 2)}).encode() + b'\n'
                raw = b'\x93NUMPY\x01\x00' + len(header).to_bytes(2, 'little') + header + values
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, 'w') as stream:
                    stream.writestr('tensor.npy', raw)
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory) / 'bundle'
                    expected = sealed_bundle(root, {'arrays.npz': archive.getvalue()})
                    bundle = capture._Bundle(root, expected)
                    descriptions = {'tensor': {'dtype': dtype, 'shape': [1, 2],
                        'bytes_sha256': description_sha or hashlib.sha256(values).hexdigest()}}
                    if dtype == '<f4' and values == struct.pack('<2f', 1.0, 2.0) and description_sha is None:
                        result = capture._array_archive(bundle, 'arrays.npz', descriptions)
                        self.assertEqual(result['tensor']['data'], values)
                    else:
                        with self.assertRaises(capture.EvidenceError):
                            capture._array_archive(bundle, 'arrays.npz', descriptions)

    def test_capture_cannot_start_without_reviewed_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'must-not-exist'
            with self.assertRaisesRegex(capture.EvidenceError, 'reviewed isolation'):
                capture.capture_reference(output=output, phase='rank', python=Path(sys.executable),
                    sources=Path(directory), assets=Path(directory), isolation_module=None, isolation_review=None)
            self.assertFalse(output.exists())

    def test_private_collector_refuses_host_execution_before_importing_a_model(self):
        with tempfile.TemporaryDirectory() as directory:
            proof = Path(directory) / 'proof.json'
            proof.write_text(json.dumps({'network_namespace': os.readlink('/proc/self/ns/net'),
                                         'home_probe_inode': 0}))
            script = """
import sys
class NoModel:
 def find_spec(self, fullname, path=None, target=None):
  if fullname.split('.')[0] in {'numpy','fastembed','onnxruntime','tokenizers'}:
   raise AssertionError('model imported before isolation refusal')
sys.meta_path.insert(0,NoModel())
import capture_semantic_fixtures
raise SystemExit(capture_semantic_fixtures.main(sys.argv[1:]))
"""
            result = subprocess.run([sys.executable, '-B', '-S', '-c', script, '_collect',
                '--mode', 'rank', '--case-id', 'storage-location', '--request', directory,
                '--sources', directory, '--assets', directory, '--output', directory,
                '--isolation-proof', str(proof), '--parent-net-namespace', os.readlink('/proc/self/ns/net')],
                capture_output=True, text=True, timeout=10,
                env=dict(os.environ, PYTHONPATH=str(Path(capture.__file__).parent)))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('network namespace', result.stderr)
            self.assertNotIn('model imported', result.stderr)

    def test_reply_checks_refuse_zero_nonfinite_dimensions_and_invalid_utf8_spans(self):
        request = {'id': 'manufactured-document', 'operation': 'document', 'text': 'é🙂'}
        identity = {'model_sha256': capture.EMBED_IDENTITY, 'algorithm': 'bge-original-passages/1', 'dimensions': 384}
        for vector in ([0.0] * 384, [float('nan')] * 384, [1.0] * 383, [10 ** 1000] * 384):
            with self.subTest(vector_type=type(vector[0]).__name__, size=len(vector)):
                with self.assertRaises(capture.EvidenceError):
                    capture._embedding_reply(request, {'id': request['id'], 'identity': identity,
                        'passages': [{'span': {'offset': 0, 'length': 6}, 'vector': vector}]})
        with self.assertRaises(capture.EvidenceError):
            capture._embedding_reply(request, {'id': request['id'], 'identity': identity,
                'passages': [{'span': {'offset': 1, 'length': 5}, 'vector': [1.0] * 384}]})

    def test_rank_reply_requires_discrete_scores_and_original_note_order(self):
        request = capture.ranking_cases()[0]
        scores = [{key: note[key] for key in ('record_id', 'version', 'body_sha256')}
                  | {'score': 123456} for note in request['notes']]
        reply = {'model_sha256': capture.RANK_IDENTITY, 'algorithm': 'bge-max-chunk/1', 'scores': scores}
        capture._rank_reply(request, reply)
        with self.assertRaisesRegex(capture.EvidenceError, 'order'):
            capture._rank_reply(request, dict(reply, scores=list(reversed(scores))))
        for field, value in (('version', True), ('score', True), ('score', 123456.0), ('score', 1000001)):
            with self.subTest(field=field, value=value):
                changed = [dict(score) for score in scores]
                changed[0][field] = value
                with self.assertRaises(capture.EvidenceError):
                    capture._rank_reply(request, dict(reply, scores=changed))


if __name__ == '__main__':
    unittest.main()
