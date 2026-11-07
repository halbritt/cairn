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
import py_compile
import ast
import types
import __future__
import contextlib
import shutil
import socket
import copy
import pwd
from unittest import mock
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


def manufactured_command_contract():
    environment = {'PATH': '/usr/bin:/bin', 'HF_HUB_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false', 'PWD': '/'}
    return {'schema': 'cairn.semantic-command-environment/1', 'profile': 'default', 'cwd': '/',
        'environment': environment, 'cpython_environment': dict(environment, LC_CTYPE='C.UTF-8'),
        'runtime_additions': {'LC_CTYPE': {'value': 'C.UTF-8', 'reason': 'manufactured CPython coercion declaration'}}}


def manufactured_case():
    notes = []
    for index, body in enumerate(('red blue', 'red blue', 'green blue')):
        notes.append({'record_id': f'00000000-0000-4000-8000-{index:012d}', 'version': 1,
                      'body': body, 'body_sha256': hashlib.sha256(body.encode()).hexdigest()})
    return {'id': 'manufactured-shared-notes', 'query': 'red', 'notes': notes}


def fake_onnx_worker(output, mode='rank', corruption=None):
    """Prepared NumPy arithmetic with frozen code; no ORT/tokenizer/model import."""
    class NoModel:
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split('.')[0] in {'fastembed', 'onnxruntime', 'tokenizers'}:
                raise AssertionError('real model dependency imported in manufactured trace')
    sys.meta_path.insert(0, NoModel())
    import numpy as np
    rank, embed = capture._load_frozen_sources(Path(capture.__file__).parent)
    packages = Path(np.__file__).resolve().parent.parent
    normalize_path = packages / 'fastembed/common/utils.py'
    onnx_path = packages / 'fastembed/text/onnx_text_model.py'
    normalize_source = normalize_path.read_bytes()

    def function(path, name):
        tree = ast.parse(path.read_bytes())
        node = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)
        namespace = {'np': np, 'OnnxOutputContext': types.SimpleNamespace}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec',
                     flags=__future__.annotations.compiler_flag), namespace)
        return namespace[name]

    normalize = function(normalize_path, 'normalize')

    class ManufacturedTokenizer:
        words = {}

        def encode(self, text, add_special_tokens=True):
            import re
            matches = list(re.finditer(r'\S+', text))
            ids = [self.words.setdefault(match.group(), len(self.words) + 1000) for match in matches]
            offsets = [match.span() for match in matches]
            if add_special_tokens:
                ids, offsets = [101] + ids + [102], [(0, 0)] + offsets + [(0, 0)]
            return types.SimpleNamespace(ids=ids, attention_mask=[1] * len(ids), offsets=offsets)

        def decode(self, ids, skip_special_tokens=False):
            reverse = {value: key for key, value in self.words.items()}
            return ' '.join(reverse[value] for value in ids)

    tokenizer = ManufacturedTokenizer()

    class FakeSession:
        calls = 0

        def get_inputs(self):
            return [types.SimpleNamespace(name=name) for name in ('input_ids', 'attention_mask', 'token_type_ids')]

        def run(self, names, inputs):
            self.calls += 1
            if corruption == 'exception':
                raise RuntimeError('manufactured ONNX execution error')
            raw = np.arange(1, 385, dtype=np.float32) + np.float32(inputs['input_ids'].sum() / 1024)
            hidden = np.broadcast_to(raw, (1, inputs['input_ids'].shape[1], 384)).copy()
            if corruption == 'nonfinite':
                hidden[0, 0, 0] = np.nan
            if corruption == 'shape':
                hidden = hidden[:, :, :383]
            return [hidden]

    class FakeOnnx:
        onnx_embed = function(onnx_path, 'onnx_embed')
        ONNX_OUTPUT_NAMES = None
        model = FakeSession()

        def tokenize(self, documents, **kwargs):
            return [tokenizer.encode(text) for text in documents]

        def _preprocess_onnx_input(self, inputs, **kwargs):
            if corruption == 'mask':
                inputs['attention_mask'][:] = 0
            return inputs

    class FakeEmbedding:
        model = FakeOnnx()

        def embed(self, texts, batch_size):
            assert batch_size == 1
            for text in texts:
                response = self.model.onnx_embed([text])
                yield normalize(response.model_output[:, 0])[0]

    # __init__ would construct a real session; it is deliberately never called.
    scorer = rank.Scorer.__new__(rank.Scorer)
    scorer.np, scorer.tokenizer, scorer.model = np, tokenizer, FakeEmbedding()
    scorer.model_hash, scorer.chunk_vectors = capture.RANK_IDENTITY, {}
    case = manufactured_case()
    request = {key: case[key] for key in ('query', 'notes')}
    embeddings = None
    if mode != 'rank':
        request = {'id': 'manufactured-' + mode, 'operation': mode, 'text': 'red blue'}
        case = dict(request, expected='accepted')
        embeddings = embed.Embeddings.__new__(embed.Embeddings)
        embeddings.scorer = scorer
        embeddings.identity = {'model_sha256': capture.EMBED_IDENTITY,
                               'algorithm': 'bge-original-passages/1', 'dimensions': 384}
    result, trace, arrays, raw = capture._trace_reference(
        scorer=scorer, semantic_rank=rank, embedding_worker=embed, normalize=normalize,
        normalize_source=normalize_source, request=request, mode='rank' if mode == 'rank' else 'embed',
        case_id=case['id'], embeddings=embeddings)
    output.mkdir()
    for name, contents in raw.items():
        (output / (name + '.bin')).write_bytes(contents)
    np.savez(output / 'arrays.npz', **arrays)
    (output / 'trace.json').write_text(json.dumps(trace))
    (output / 'case.json').write_text(json.dumps(case))
    (output / 'result.json').write_text(json.dumps(result))
    assert not any(name.split('.')[0] in {'fastembed', 'onnxruntime', 'tokenizers'} for name in sys.modules)
    print(json.dumps({'manufactured': True, 'model_calls': scorer.model.model.model.calls,
                      'logical_rows': trace['logical_occurrences'], 'all_replay_bits_match': all(trace['replay'].values())}))


def manufactured_reference(trace_root):
    """Construct adversarial parser data. Patched test pins are not real references."""
    def raw_json(value):
        return json.dumps(value, separators=(',', ':'))
    def digest(value):
        return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()
    files = {
        'reference/tooling/capture_semantic_fixtures.py': Path(capture.__file__).read_bytes(),
        'reference/tooling/semantic_fixture_cases.py': Path(capture.__file__).with_name('semantic_fixture_cases.py').read_bytes(),
        'reference/tooling/semantic_acceptance_isolation.py': '# manufactured review fixture\n',
    }
    for filename in capture.SOURCE_DIGESTS:
        files['reference/scripts/' + filename] = Path(capture.__file__).with_name(filename).read_bytes()
    assets = {filename: digest('manufactured asset ' + filename) for filename in capture.ASSET_DIGESTS}
    for filename in assets:
        files['assets/' + filename] = 'manufactured asset ' + filename
    native = {marker: str(index + 1) * 64 for index, marker in enumerate(capture.NATIVE_DIGESTS)}
    libraries = {'/manufactured/' + marker + '.so': {'inode': index + 100, 'device': '00:01', 'sha256': sha}
                 for index, (marker, sha) in enumerate(native.items())}
    review = {'schema': 'cairn.semantic-isolation-review/1', 'status': 'accepted', 'source_commit': 'a' * 40,
        'module_sha256': digest(files['reference/tooling/semantic_acceptance_isolation.py']),
        'review_record': {'record_id': '00000000-0000-4000-8000-000000000001',
                         'version': 1, 'body_sha256': 'b' * 64}, 'manufactured': True}
    files['reference/tooling/isolation-review.json'] = raw_json(review)
    identity = {'inode': 303, 'device': 1, 'owner': os.getuid(), 'mode': 0o700}
    home = {'backing': '/manufactured/private-backing', 'initial_identity': identity,
            'profile': 'default', 'payload_inspected': False}
    files['isolation/disposable-home.json'] = raw_json(home)
    cleanup = dict(home, backing_absent_observed=True, home_receipt='isolation/disposable-home.json',
                   final_receipt='isolation/cleanup.json')
    contract = manufactured_command_contract()
    proof = {'wrapper_sha256': review['module_sha256'], 'profile': 'default', 'home': '/fixture-home',
        'home_probe_inode': 77, 'environment_contract': contract, 'effective_prefix': ['bwrap', '--chdir', '/']}
    files['isolation/isolation-proof.json'] = raw_json(proof)
    declaration = {'id': 1, 'role': 'python-reference', 'mode': 'rank', 'contract': contract}
    files['isolation/command-0001.json'] = raw_json(declaration)
    invocation = dict(declaration, pid=3101, start_ticks='12345', argv=['manufactured-bwrap', '--', 'manufactured-collector'],
        declaration_sha256=digest(files['isolation/command-0001.json']))
    final = {'all_observed_processes_exited': True, 'backing_removed': True, 'errors': [],
        'invocations': [invocation], 'isolation_proof_sha256': digest(files['isolation/isolation-proof.json'])}
    files['isolation/cleanup.json'] = raw_json(final)
    cleanup['isolation_proof_sha256'] = final['isolation_proof_sha256']
    runtime = {'schema': 'cairn.semantic-python-runtime/1', 'network_namespace': 'child-ns',
        'process_start_ticks': '12346', 'pid_namespace': 'child-pid',
        'passwd_home': '/fixture-home', 'expanduser_home': '/fixture-home', 'home_probe_inode': 77,
        'executable': {'inode': 55, 'sha256': 'e' * 64}, 'mapped_libraries': libraries,
        'effective_environment': contract['cpython_environment'], 'cwd': '/',
        'cpu_kernel_class': capture.cpu_kernel_class({'architecture': 'manufactured', 'byte_order': 'little',
            'pointer_bits': 64, 'effective_isa': ['synthetic'], 'os_vector_state': ['synthetic'],
            'blas_core': 'synthetic', 'blas_config': 'synthetic'})}
    worker = {'executable_inode': 55, 'executable_sha256': 'e' * 64, 'network_namespace': 'child-ns',
        'pid': 3102, 'launcher_pid': 3101, 'launcher_start_ticks': '12345', 'invocation_id': 1,
        'role': 'python-reference', 'mode': 'rank', 'process_start_ticks': '12346', 'pid_namespace': 'child-pid',
        'environment_matches_contract': True, 'exec_environment': contract['environment'],
        'libraries': [dict(path=path, **info) for path, info in libraries.items()]}
    case = json.loads((trace_root / 'case.json').read_text())
    prefix = 'isolation/raw/' + case['id'] + '/'
    artifacts = {'request': prefix + 'request.stdin', 'stdout': prefix + 'wire.stdout', 'stderr': prefix + 'wire.stderr',
        'trace': prefix + 'trace.json', 'arrays': prefix + 'trace-arrays.npz', 'runtime': prefix + 'runtime.json',
        'command_contract': prefix + 'collector-command.json', 'invocation': prefix + 'invocation.json',
        'runtime_binding': prefix + 'runtime-binding.json'}
    files[artifacts['request']] = raw_json({key: case[key] for key in ('query', 'notes')})
    files[artifacts['stdout']] = (trace_root / 'result.json').read_bytes()
    files[artifacts['stderr']] = b''
    files[artifacts['trace']] = (trace_root / 'trace.json').read_bytes()
    files[artifacts['arrays']] = (trace_root / 'arrays.npz').read_bytes()
    files[artifacts['runtime']] = raw_json(runtime)
    files[artifacts['command_contract']] = raw_json(contract)
    files[artifacts['invocation']] = raw_json(invocation)
    files[artifacts['runtime_binding']] = raw_json(worker)
    aggregates = {}
    for name in capture.REVIEWER_RAW_BASELINE:
        raw = (trace_root / (name + '.bin')).read_bytes()
        files[name + '.bin'] = raw
        aggregates[name] = digest(raw)
    observation = {'cell': 'rank/' + case['id'], 'status': 'observed', 'exit_code': 0, 'skipped': 0,
        'logical_occurrences': 4, 'observed_model_executions': 3, 'mapped_duplicate_rows': 1, 'artifacts': artifacts,
        'command': ['manufactured-collector'], 'invocation': invocation}
    provenance = {'kind': 'python-reference', 'manufactured': True, 'capture_scope': 'ordinary-default-reference',
        'conditional_b1_proof': False, 'wrapper_review': review, 'source_commit': 'c' * 40,
        'capture_source_sha256': digest(files['reference/tooling/capture_semantic_fixtures.py']),
        'fixture_source_sha256': digest(files['reference/tooling/semantic_fixture_cases.py']),
        'sources': capture.SOURCE_DIGESTS, 'assets': assets, 'isolation_proof': proof,
        'actual_worker_observation': {'model_workers': [worker], 'errors': []}, 'host_network_namespace': 'host-ns',
        'cleanup_evidence': cleanup, 'expanded_row_digests': aggregates,
        'execution_counts': {'logical_occurrences': 4, 'observed_model_executions': 3, 'mapped_duplicate_rows': 1}}
    files['capture-manifest.json'] = raw_json({'schema': 'cairn.semantic-reference-capture/1',
        'selected_phase': 'rank', 'provenance': provenance, 'observations': [observation], 'failure': None})
    return files, assets, native, aggregates, case


class CaptureSemanticFixturesTest(unittest.TestCase):
    def test_profile_adapter_uses_keyword_factory_and_transfers_cleanup_ownership(self):
        """Mocked API plumbing only; exported-module tests check real allocation."""
        calls = []
        backing = mock.Mock()
        task = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.cache/cairn-hybrid-execution-1249/semantic-S-B1/manufactured-task'

        class IsolationError(RuntimeError):
            def __init__(self, message, *, code='configuration', evidence=None):
                super().__init__(message)
                self.code, self.evidence = code, evidence or {}

        class Boundary:
            def __init__(self, **kwargs):
                calls.append(('default', kwargs))

            @classmethod
            def sealed_oracle(cls, **kwargs):
                calls.append(('derived-W', kwargs))
                return cls.__new__(cls)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                calls.append(('context-cleanup', None))

        factory = mock.Mock(return_value=backing)
        module = types.SimpleNamespace(SemanticIsolation=Boundary, IsolationError=IsolationError,
                                       DisposableBacking=types.SimpleNamespace(create=factory))
        with capture._collector_isolation(module, profile='default', read_only_paths=[], output_dir=Path('/manufactured/out')):
            pass
        factory.assert_not_called()
        with self.assertRaisesRegex(RuntimeError, 'manufactured body'):
            with capture._collector_isolation(module, profile='derived-W', read_only_paths=[Path('/manufactured/tool')],
                    output_dir=Path('/manufactured/out'), sealed_root=Path('/manufactured/frozen'), task_root=task, name='fresh'):
                raise RuntimeError('manufactured body')
        factory.assert_called_once_with(sealed_root=Path('/manufactured/frozen'), task_root=task, name='fresh')
        self.assertEqual(calls[-2][0], 'derived-W')
        self.assertEqual(calls[-2][1]['task_scratch_roots'], [task])
        backing.remove.assert_not_called()  # The entered context is the sole cleanup owner.

    def test_profile_adapter_retains_setup_and_failed_empty_child_release(self):
        task = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.cache/cairn-hybrid-execution-1249/semantic-S-B1/manufactured-task'
        for identity_changed in (False, True):
            with self.subTest(identity_changed=identity_changed):
                class IsolationError(RuntimeError):
                    def __init__(self, message, *, code='configuration', evidence=None):
                        super().__init__(message)
                        self.code, self.evidence = code, evidence or {}
                backing = mock.Mock()
                if identity_changed:
                    backing.verify.side_effect = IsolationError('manufactured changed identity')
                setup = IsolationError('manufactured construction refusal', evidence={'setup_marker': 'retained'})
                module = types.SimpleNamespace(IsolationError=IsolationError,
                    DisposableBacking=types.SimpleNamespace(create=mock.Mock(return_value=backing)),
                    SemanticIsolation=types.SimpleNamespace(sealed_oracle=mock.Mock(side_effect=setup)))
                with self.assertRaises(IsolationError) as caught:
                    with capture._collector_isolation(module, profile='derived-W', read_only_paths=[],
                            output_dir=Path('/manufactured/out'), sealed_root=Path('/manufactured/frozen'),
                            task_root=task, name='fresh'):
                        self.fail('refused setup entered')
                backing.verify.assert_called_once_with(empty=True)
                if identity_changed:
                    self.assertEqual(caught.exception.code, 'cleanup')
                    self.assertEqual(caught.exception.evidence['setup_marker'], 'retained')
                    self.assertIn('consumer_setup_failure', caught.exception.evidence)
                    self.assertIn('consumer_allocation_cleanup_failure', caught.exception.evidence)
                    backing.remove.assert_not_called()
                else:
                    self.assertIs(caught.exception, setup)
                    backing.remove.assert_called_once_with()

    def test_profile_adapter_refuses_mixed_profiles_and_foreign_task_roots(self):
        module = mock.Mock()
        for profile, kwargs in (('default', {'name': 'unexpected'}), ('derived-W', {}),
                ('derived-W', {'sealed_root': Path('/manufactured/frozen'), 'task_root': Path('/tmp/cairn-semantic-foreign'), 'name': 'fresh'}),
                ('unknown', {})):
            with self.subTest(profile=profile, kwargs=kwargs), self.assertRaises(capture.EvidenceError):
                with capture._collector_isolation(module, profile=profile, read_only_paths=[],
                                                  output_dir=Path('/manufactured/out'), **kwargs):
                    self.fail('invalid profile entered')
        module.DisposableBacking.create.assert_not_called()
        module.SemanticIsolation.assert_not_called()

    def test_command_environment_uses_the_exact_declared_cpython_contract(self):
        contract = manufactured_command_contract()
        capture._validate_declared_environment(contract, contract['cpython_environment'], '/')
        variants = []
        for environment in (contract['environment'], dict(contract['cpython_environment'], EXTRA='refuse')):
            variants.append((contract, environment, '/'))
        variants.append((contract, contract['cpython_environment'], '/wrong-cwd'))
        for field in ('runtime_additions', 'cpython_environment'):
            changed = copy.deepcopy(contract)
            changed[field]['EXTRA'] = 'not a permitted runtime addition'
            variants.append((changed, changed['cpython_environment'], '/'))
        for value in variants:
            with self.subTest(value=value), self.assertRaises(capture.EvidenceError):
                capture._validate_declared_environment(*value)

    def test_canonical_timeout_keeps_partial_bytes_and_merged_context_cleanup(self):
        """Manufactured wrapper protocol only; no process or model is launched."""
        for failure_code, context_failure in (('timeout', False), ('stdout_limit', False),
                                              ('stderr_limit', False), ('timeout', True)):
            with self.subTest(code=failure_code, context_failure=context_failure), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'capture'

                class IsolationError(RuntimeError):
                    def __init__(self, message, *, code, evidence):
                        super().__init__(message)
                        self.code, self.evidence = code, evidence

                class Wrapper:
                    def __init__(self, *, read_only_paths, output_dir):
                        self.output = output_dir
                        self.proof = {'profile': 'default', 'manufactured': True}

                    def __enter__(self):
                        self.output.mkdir(mode=0o700)
                        self.backing = self.output / 'actual-backing'
                        self.backing.mkdir(mode=0o700)
                        self.identity = self.backing.stat()
                        (self.backing / 'private-payload').write_bytes(b'never inspect')
                        (self.output / 'isolation-proof.json').write_text(json.dumps(self.proof))
                        return self

                    def command_contract(self):
                        return manufactured_command_contract()

                    def run(self, command, *, timeout, role, mode, stdout_limit):
                        declaration = {'id': 1, 'role': role, 'mode': mode, 'contract': self.command_contract()}
                        raw = json.dumps(declaration).encode()
                        (self.output / 'command-0001.json').write_bytes(raw)
                        self.invocation = dict(declaration, pid=3101, start_ticks='12345',
                            argv=['manufactured-bwrap', '--', *command], declaration_sha256=hashlib.sha256(raw).hexdigest())
                        raise IsolationError('manufactured ' + failure_code, code=failure_code, evidence={
                            'failure_code': failure_code, 'stdout': b'partial\x00stdout', 'stderr': b'partial\xffstderr',
                            'argv': self.invocation['argv'], 'exit_code': -9, 'timeout_seconds': timeout,
                            'invocation': self.invocation, 'processes_joined': True, 'cleanup_error': None})

                    def __exit__(self, kind, error, tb):
                        if not context_failure:
                            shutil.rmtree(self.backing)
                        (self.output / 'disposable-home.json').write_text(json.dumps({
                            'backing': str(self.backing), 'profile': 'default', 'payload_inspected': False,
                            'initial_identity': {'inode': self.identity.st_ino, 'device': self.identity.st_dev,
                                'owner': self.identity.st_uid, 'mode': 0o700}}))
                        (self.output / 'cleanup.json').write_text(json.dumps({
                            'all_observed_processes_exited': not context_failure,
                            'backing_removed': not context_failure, 'errors': ['manufactured cleanup failure'] if context_failure else [],
                            'isolation_proof_sha256': capture._digest(self.output / 'isolation-proof.json'),
                            'invocations': [self.invocation]}))
                        if context_failure:
                            raise IsolationError('manufactured context cleanup', code='cleanup',
                                evidence=dict(error.evidence, context_cleanup_error='manufactured context cleanup'))

                module = types.SimpleNamespace(SemanticIsolation=Wrapper, IsolationError=IsolationError)
                with self.assertRaisesRegex(capture.EvidenceError, 'retained as failed'):
                    capture._run_reference_capture(module=module, output=output, phase='embed',
                        cases=[{'id': 'manufactured', 'operation': 'query', 'text': 'red'}], python=Path(sys.executable),
                        sources=Path(directory), assets=Path(directory), read_only=[], provenance={'manufactured': True})
                manifest = json.loads((output / 'capture-manifest.json').read_bytes())
                failure = manifest['failure']
                self.assertEqual(failure['code'], 'cleanup' if context_failure else failure_code)
                self.assertEqual(failure['evidence']['failure_code'], failure_code)
                self.assertEqual((output / 'isolation/raw/manufactured/wire.stdout').read_bytes(), b'partial\x00stdout')
                self.assertEqual((output / 'isolation/raw/manufactured/wire.stderr').read_bytes(), b'partial\xffstderr')
                self.assertEqual(manifest['failed_attempt']['status'], failure_code)
                self.assertEqual(manifest['observations'], [])
                if context_failure:
                    self.assertEqual(failure['evidence']['context_cleanup_error'], 'manufactured context cleanup')
                    self.assertEqual(manifest['primary_failure']['code'], 'timeout')
                    self.assertIn('cleanup_failure', manifest)
                seal = capture._digest(output / 'SHA256SUMS')
                entries = capture.verify_sealed_bundle(output, seal)
                self.assertIn('isolation/command-0001.json', entries)
                self.assertFalse(any('private-payload' in path for path in entries))

    def test_cleanup_requires_the_named_proof_digest_and_joined_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            isolation = output / 'isolation'
            isolation.mkdir()
            backing = output / 'actual-backing'
            backing.mkdir(mode=0o700)
            info = backing.stat()
            backing.rmdir()
            proof = {'profile': 'default', 'manufactured': True}
            (isolation / 'isolation-proof.json').write_text(json.dumps(proof))
            home = {'backing': str(backing), 'profile': 'default', 'payload_inspected': False,
                'initial_identity': {'inode': info.st_ino, 'device': info.st_dev, 'owner': info.st_uid, 'mode': 0o700}}
            (isolation / 'disposable-home.json').write_text(json.dumps(home))
            final = {'all_observed_processes_exited': True, 'backing_removed': True, 'errors': [], 'invocations': [],
                'isolation_proof_sha256': capture._digest(isolation / 'isolation-proof.json')}
            (isolation / 'cleanup.json').write_text(json.dumps(final))
            self.assertEqual(capture._cleanup_evidence(output)['isolation_proof_sha256'], final['isolation_proof_sha256'])
            for field, value in (('isolation_proof_sha256', '0' * 64), ('all_observed_processes_exited', False),
                                 ('backing_removed', False), ('errors', ['manufactured failure'])):
                with self.subTest(field=field):
                    (isolation / 'cleanup.json').write_text(json.dumps(dict(final, **{field: value})))
                    with self.assertRaises(capture.EvidenceError):
                        capture._cleanup_evidence(output)
            proof['profile'] = home['profile'] = 'derived-W'
            proof['sealed_oracle'] = {'sha256': 'a' * 64, 'files': 31, 'metadata': {'manufactured': {'inode': 77}}}
            (isolation / 'isolation-proof.json').write_text(json.dumps(proof))
            (isolation / 'disposable-home.json').write_text(json.dumps(home))
            final['isolation_proof_sha256'] = capture._digest(isolation / 'isolation-proof.json')
            final['sealed_oracle_after'] = proof['sealed_oracle']
            (isolation / 'cleanup.json').write_text(json.dumps(final))
            self.assertTrue(capture._cleanup_evidence(output)['sealed_oracle_unchanged'])
            changed = copy.deepcopy(final)
            changed['sealed_oracle_after']['metadata']['manufactured']['inode'] += 1
            (isolation / 'cleanup.json').write_text(json.dumps(changed))
            with self.assertRaisesRegex(capture.EvidenceError, 'sealed-profile metadata'):
                capture._cleanup_evidence(output)

    def test_runtime_binding_refuses_another_invocation_or_changed_child_facts(self):
        """Manufactured metadata; these files and libraries do not exist."""
        contract = manufactured_command_contract()
        invocation = {'id': 7, 'role': 'python-reference', 'mode': 'rank', 'pid': 3101,
            'start_ticks': '12345', 'contract': contract}
        libraries = {'/manufactured/' + marker + '.so': {'inode': index + 100, 'device': '00:01', 'sha256': sha}
            for index, (marker, sha) in enumerate(capture.NATIVE_DIGESTS.items())}
        runtime = {'effective_environment': contract['cpython_environment'], 'cwd': '/',
            'executable': {'inode': 55, 'sha256': 'e' * 64}, 'mapped_libraries': libraries,
            'network_namespace': 'child-net', 'pid_namespace': 'child-pid', 'process_start_ticks': '12346'}
        worker = {'pid': 3102, 'launcher_pid': 3101, 'launcher_start_ticks': '12345', 'invocation_id': 7,
            'role': 'python-reference', 'mode': 'rank', 'process_start_ticks': '12346', 'pid_namespace': 'child-pid',
            'network_namespace': 'child-net', 'executable_inode': 55, 'executable_sha256': 'e' * 64,
            'environment_matches_contract': True, 'exec_environment': contract['environment'],
            'libraries': [dict(path=path, **info) for path, info in libraries.items()]}
        self.assertEqual(len(capture._validate_runtime_binding(invocation, worker, runtime, {'profile': 'default'}, 'host-net')), 3)
        for field, value in (('launcher_pid', 3103), ('launcher_start_ticks', '12347'), ('invocation_id', 8),
                             ('role', 'another-role'), ('mode', 'embed'), ('process_start_ticks', '12347'),
                             ('pid_namespace', 'another-pid'), ('executable_inode', 56),
                             ('network_namespace', 'host-net'), ('environment_matches_contract', False),
                             ('exec_environment', {}), ('libraries', [])):
            with self.subTest(field=field), self.assertRaises(capture.EvidenceError):
                capture._validate_runtime_binding(invocation, dict(worker, **{field: value}), runtime,
                                                  {'profile': 'default'}, 'host-net')

    def test_manufactured_python_reference_stays_unqualified_and_every_gate_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_root = root / 'trace'
            result = subprocess.run([sys.executable, '-B', __file__, '--fake-onnx-worker', str(trace_root), 'rank'],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            files, assets, native, aggregates, case = manufactured_reference(trace_root)
            artifacts = json.loads(files['capture-manifest.json'])['observations'][0]['artifacts']
            variants = ['valid', 'scope', 'profile', 'netns', 'home', 'expanduser', 'home-inode', 'environment',
                        'replay', 'mapping', 'ordinal', 'owner', 'count', 'mask', 'aggregate', 'aggregate-bytes',
                        'cleanup', 'cleanup-identity', 'backing-observation', 'outer-child',
                        'missing-executable-hash', 'skipped', 'zero-observations', 'missing-runtime',
                        'command-contract', 'invocation', 'declaration-hash', 'cleanup-proof',
                        'borrowed-worker', 'cleanup-invocation']
            variants += ['native-' + name for name in native]
            variants += ['missing-native-' + name for name in native]
            for index, variant in enumerate(variants):
                with self.subTest(variant=variant):
                    changed = copy.deepcopy(files)
                    manifest = json.loads(changed['capture-manifest.json'])
                    provenance = manifest['provenance']
                    runtime = json.loads(changed[artifacts['runtime']])
                    trace = json.loads(changed[artifacts['trace']])
                    if variant == 'scope':
                        provenance['conditional_b1_proof'] = True
                    elif variant == 'profile':
                        provenance['isolation_proof']['profile'] = 'derived-W'
                    elif variant == 'netns':
                        runtime['network_namespace'] = 'host-ns'
                    elif variant == 'home':
                        runtime['passwd_home'] = '/wrong'
                    elif variant == 'expanduser':
                        runtime['expanduser_home'] = '/wrong'
                    elif variant == 'home-inode':
                        runtime['home_probe_inode'] += 1
                    elif variant == 'environment':
                        runtime['effective_environment']['UNDECLARED'] = 'bad'
                    elif variant == 'replay':
                        trace['replay'][next(iter(trace['replay']))] = False
                    elif variant == 'mapping':
                        trace['row_order'][2]['inference'] = 99
                    elif variant == 'ordinal':
                        trace['row_order'][2]['row'] = 99
                    elif variant == 'owner':
                        trace['row_order'][2]['record_id'] = 'wrong'
                    elif variant == 'count':
                        trace['observed_model_executions'] = 294
                    elif variant == 'mask':
                        trace['inferences'][0]['inputs']['attention_mask'] = trace['inferences'][0]['inputs']['token_type_ids']
                    elif variant == 'aggregate':
                        provenance['expanded_row_digests']['ids_i64le'] = '0' * 64
                    elif variant == 'aggregate-bytes':
                        changed['cls_f32le.bin'] = b'changed'
                    elif variant == 'cleanup':
                        changed['isolation/cleanup.json'] = json.dumps({'backing_removed': False})
                    elif variant == 'cleanup-identity':
                        provenance['cleanup_evidence']['initial_identity']['inode'] += 1
                    elif variant == 'backing-observation':
                        provenance['cleanup_evidence']['backing_absent_observed'] = False
                    elif variant == 'outer-child':
                        provenance['actual_worker_observation']['model_workers'][0]['executable_inode'] += 1
                    elif variant == 'missing-executable-hash':
                        runtime['executable'].pop('sha256')
                    elif variant == 'skipped':
                        manifest['observations'][0]['skipped'] = 1
                    elif variant == 'zero-observations':
                        manifest['observations'] = []
                    elif variant == 'missing-runtime':
                        provenance.pop('actual_worker_observation')
                    elif variant == 'command-contract':
                        contract = json.loads(changed[artifacts['command_contract']])
                        contract['cpython_environment']['UNDECLARED'] = 'bad'
                        changed[artifacts['command_contract']] = json.dumps(contract)
                    elif variant == 'invocation':
                        manifest['observations'][0]['invocation']['id'] += 1
                    elif variant == 'declaration-hash':
                        changed['isolation/command-0001.json'] += ' '
                    elif variant == 'cleanup-proof':
                        final = json.loads(changed['isolation/cleanup.json'])
                        final['isolation_proof_sha256'] = '0' * 64
                        changed['isolation/cleanup.json'] = json.dumps(final)
                    elif variant == 'borrowed-worker':
                        worker = json.loads(changed[artifacts['runtime_binding']])
                        worker['launcher_pid'] += 1
                        changed[artifacts['runtime_binding']] = json.dumps(worker)
                    elif variant == 'cleanup-invocation':
                        final = json.loads(changed['isolation/cleanup.json'])
                        final['invocations'] = []
                        changed['isolation/cleanup.json'] = json.dumps(final)
                    elif variant.startswith('missing-native-'):
                        marker = variant[len('missing-native-'):]
                        path = next(path for path in runtime['mapped_libraries'] if marker in path)
                        runtime['mapped_libraries'].pop(path)
                    elif variant.startswith('native-'):
                        marker = variant[len('native-'):]
                        library = next(info for path, info in runtime['mapped_libraries'].items() if marker in path)
                        library['sha256'] = '0' * 64
                    changed['capture-manifest.json'] = json.dumps(manifest)
                    changed[artifacts['runtime']] = json.dumps(runtime)
                    changed[artifacts['trace']] = json.dumps(trace)
                    bundle = root / ('bundle-' + str(index))
                    seal = sealed_bundle(bundle, changed)
                    with mock.patch.object(capture, 'ASSET_DIGESTS', assets), \
                            mock.patch.object(capture, 'NATIVE_DIGESTS', native), \
                            mock.patch.object(capture, 'REVIEWER_RAW_BASELINE', aggregates), \
                            mock.patch.object(capture, 'ranking_cases', return_value=[case]):
                        if variant == 'valid':
                            catalog = capture.import_bundles([(bundle, seal)], root / ('catalog-' + str(index)))
                            self.assertEqual(catalog['cells'][0]['status'], 'unqualified')
                            self.assertFalse(catalog['model_acceptance'])
                            self.assertEqual(catalog['phase_status'], 'incomplete')
                        else:
                            with self.assertRaises(capture.EvidenceError):
                                capture.import_bundles([(bundle, seal)], root / ('catalog-' + str(index)))
    def test_missing_runtime_interface_seals_failure_and_checks_declared_backing(self):
        """Fake wrapper only; no subprocess/model call. Failure is never a pass."""
        for leave_backing, failure_type in ((False, AttributeError), (True, AttributeError),
                                           (False, KeyError), (False, TypeError), (False, RuntimeError)):
            with self.subTest(leave_backing=leave_backing, failure=failure_type.__name__), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'capture'

                class MissingRuntimeWrapper:
                    def __init__(self, *, read_only_paths, output_dir):
                        self.output = output_dir
                        self.proof = {'profile': 'default'}

                    def __enter__(self):
                        self.output.mkdir(mode=0o700)
                        self.backing = self.output / 'actual-private-backing'
                        self.backing.mkdir(mode=0o700)
                        self.identity = self.backing.stat()
                        (self.backing / 'private-payload').write_text('never copy or inspect this fixture payload')
                        (self.output / 'isolation-proof.json').write_text(json.dumps(self.proof))
                        return self

                    def command_contract(self):
                        return manufactured_command_contract()

                    def run(self, command, *, role, mode, **kwargs):
                        target = Path(command[command.index('--output') + 1])
                        (target / 'trace.json').write_text(json.dumps({
                            'logical_occurrences': 1, 'observed_model_executions': 1, 'mapped_duplicate_rows': 0}))
                        libraries = {'/manufactured/' + marker + '.so': {'inode': index + 100, 'device': '00:01', 'sha256': sha}
                            for index, (marker, sha) in enumerate(capture.NATIVE_DIGESTS.items())}
                        (target / 'runtime.json').write_text(json.dumps({'mapped_libraries': libraries}))
                        declaration = {'id': 1, 'role': role, 'mode': mode, 'contract': self.command_contract()}
                        raw = json.dumps(declaration).encode()
                        (self.output / 'command-0001.json').write_bytes(raw)
                        self.invocation = dict(declaration, pid=3101, start_ticks='12345',
                            argv=['manufactured-bwrap', '--', *command], declaration_sha256=hashlib.sha256(raw).hexdigest())
                        result = subprocess.CompletedProcess(command, 0, b'{}', b'')
                        result.invocation = self.invocation
                        return result

                    def __exit__(self, *exception):
                        if not leave_backing:
                            shutil.rmtree(self.backing)
                        (self.output / 'disposable-home.json').write_text(json.dumps({
                            'profile': 'default', 'payload_inspected': False,
                            'backing': str(self.backing), 'initial_identity': {
                                'inode': self.identity.st_ino, 'device': self.identity.st_dev,
                                'owner': self.identity.st_uid, 'mode': 0o700}}))
                        (self.output / 'cleanup.json').write_text(json.dumps({
                            'backing_removed': not leave_backing, 'all_observed_processes_exited': True, 'errors': [],
                            'isolation_proof_sha256': capture._digest(self.output / 'isolation-proof.json'),
                            'invocations': [self.invocation]}))

                def run_fake(**kwargs):
                    return capture._run_reference_capture(module=types.SimpleNamespace(
                        SemanticIsolation=MissingRuntimeWrapper, IsolationError=type('IsolationError', (RuntimeError,), {})),
                        output=output, phase='embed',
                        cases=[{'id': 'manufactured', 'operation': 'query', 'text': 'red', 'expected': 'accepted'}],
                        python=Path(sys.executable), sources=Path(directory), assets=Path(directory),
                        read_only=[], provenance={'kind': 'mechanics', 'manufactured': True})

                if failure_type is not AttributeError:
                    def failed_runtime(self, *args, **kwargs):
                        raise failure_type('manufactured runtime interface error')
                    MissingRuntimeWrapper.invocation_runtime = failed_runtime

                with mock.patch.object(capture, 'capture_reference', side_effect=run_fake), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = capture.main(['capture', '--phase', 'embed', '--output', str(output),
                        '--python', sys.executable, '--sources', directory, '--assets', directory,
                        '--isolation-module', '/absent', '--isolation-review', '/absent',
                        '--isolation-review-sha256', '0' * 64, '--source-commit', '0' * 40])
                self.assertEqual(code, 2)
                manifest = json.loads((output / 'capture-manifest.json').read_bytes())
                self.assertEqual(manifest['failure']['type'], failure_type.__name__)
                if failure_type is AttributeError:
                    self.assertIn('invocation_runtime', manifest['failure']['message'])
                self.assertEqual(output.stat().st_mode & 0o777, 0o700)
                entries = capture.verify_sealed_bundle(output, hashlib.sha256((output / 'SHA256SUMS').read_bytes()).hexdigest())
                self.assertIn('failure.json', entries)
                self.assertFalse(any('private-payload' in path for path in entries))
                if leave_backing:
                    self.assertIn('cleanup_failure', manifest)
                    self.assertNotIn('cleanup_evidence', manifest['provenance'])
                else:
                    self.assertTrue(manifest['provenance']['cleanup_evidence']['backing_absent_observed'])
                    self.assertEqual(manifest['provenance']['cleanup_evidence']['initial_identity']['inode'],
                                     json.loads((output / 'isolation/disposable-home.json').read_text())['initial_identity']['inode'])

    def test_actual_capture_refuses_before_wrapper_import_or_session_construction(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'absent'
            with mock.patch.object(capture, '_reviewed_isolation', side_effect=AssertionError('wrapper imported')):
                with self.assertRaisesRegex(capture.EvidenceError, 'pretoken'):
                    capture.capture_reference(output=output, phase='rank', python=Path(sys.executable),
                        sources=Path(directory), assets=Path(directory), isolation_module=Path('/absent'),
                        isolation_review=Path('/absent'), isolation_review_sha256='0' * 64)
            self.assertFalse(output.exists())
        expected = dict(text_count=294, token_count=33395,
            **{key: capture.REVIEWER_RAW_BASELINE[key] for key in ('ids_i64le', 'lengths_i64le')})
        capture.validate_pretoken_digests(expected)  # mechanics only; never an admission stamp
        for field in expected:
            changed = dict(expected, **{field: 0})
            with self.assertRaisesRegex(capture.EvidenceError, 'pre-inference'):
                capture.validate_pretoken_digests(changed)

    def test_overflow_reply_shape_and_f32_representability_refuse(self):
        with self.assertRaises(capture.EvidenceError):
            capture._strict_json(b'{"unrelated":1e400}')
        request = {'id': 'manufactured', 'operation': 'query', 'text': 'red'}
        reply = {'id': request['id'], 'identity': {'model_sha256': capture.EMBED_IDENTITY,
            'algorithm': 'bge-original-passages/1', 'dimensions': 384}, 'vector': [1.0] * 384,
            'query_projection': {'method': 'original-prefix/1', 'truncated': False,
                'original_tokens': 3, 'embedded_tokens': 3, 'prefix_bytes': 3,
                'prefix_sha256': hashlib.sha256(b'red').hexdigest()}}
        capture._embedding_reply(request, reply)
        for change in ('extra', 'truncated', 'original', 'float64'):
            altered = json.loads(json.dumps(reply))
            if change == 'extra':
                altered['extra'] = True
            elif change == 'truncated':
                altered['query_projection']['truncated'] = True
            elif change == 'original':
                altered['query_projection']['original_tokens'] = 4
            else:
                altered['vector'][0] = 0.1
            with self.assertRaises(capture.EvidenceError):
                capture._embedding_reply(request, altered)

    def test_seal_refuses_fifo_and_socket_instead_of_silently_omitting_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.mkfifo(root / 'fifo')
            with self.assertRaisesRegex(capture.EvidenceError, 'non-regular'):
                capture._seal_output(root)
            (root / 'fifo').unlink()
            with socket.socket(socket.AF_UNIX) as endpoint:
                endpoint.bind(str(root / 'socket'))
                with self.assertRaisesRegex(capture.EvidenceError, 'non-regular'):
                    capture._seal_output(root)
    def test_frozen_trace_counts_executions_mapping_and_bit_replay_without_a_model(self):
        for mode in ('rank', 'query', 'document'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / 'trace'
                result = subprocess.run([sys.executable, '-B', __file__, '--fake-onnx-worker', str(output), mode],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                summary = json.loads(result.stdout)
                trace = json.loads((output / 'trace.json').read_text())
                self.assertTrue(summary['manufactured'])
                self.assertTrue(summary['all_replay_bits_match'])
                self.assertEqual(summary['model_calls'], 3 if mode == 'rank' else 1)
                self.assertEqual(trace['observed_model_executions'], summary['model_calls'])
                self.assertFalse(trace['conditional_b1_proof'])
                if mode == 'rank':
                    self.assertEqual([row['inference'] for row in trace['row_order']], [0, 1, 1, 2])
                    self.assertEqual(trace['mapped_duplicate_rows'], 1)
                    self.assertEqual(len((output / 'cls_f32le.bin').read_bytes()), 4 * 384 * 4)
                    self.assertEqual(len((output / 'lengths_i64le.bin').read_bytes()), 4 * 8)

    def test_frozen_trace_refuses_bad_fake_onnx_tensors(self):
        for corruption in ('mask', 'nonfinite', 'shape', 'exception'):
            with self.subTest(corruption=corruption), tempfile.TemporaryDirectory() as directory:
                result = subprocess.run([sys.executable, '-B', __file__, '--fake-onnx-worker',
                    str(Path(directory) / 'trace'), 'rank', corruption], capture_output=True, text=True, timeout=20)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('manufactured ONNX execution error' if corruption == 'exception' else 'EvidenceError', result.stderr)
                self.assertNotIn('real model dependency imported', result.stderr)
    def test_shared_stream_ledger_has_only_one_observed_cold_cell(self):
        """Manufactured ledger mechanics, never model observations."""
        cases = capture.ranking_cases()[:17]
        requests, replies = [], []
        for index, case in enumerate(cases):
            for repeat in range(2):
                identifier = f'frame-{index * 2 + repeat}'
                request = {key: case[key] for key in ('query', 'notes')}
                requests.append(json.dumps({'id': identifier, 'request': request}))
                scores = [{key: note[key] for key in ('record_id', 'version', 'body_sha256')}
                          | {'score': 0} for note in case['notes']]
                replies.append(json.dumps({'id': identifier, 'result': {
                    'model_sha256': capture.RANK_IDENTITY, 'algorithm': 'bge-max-chunk/1', 'scores': scores}}))
        stdin, stdout = ('\n'.join(requests) + '\n').encode(), ('\n'.join(replies) + '\n').encode()
        record = {'name': 'retrieval-quality-stream', 'input_sha256': hashlib.sha256(stdin).hexdigest(),
                  'stdout_sha256': hashlib.sha256(stdout).hexdigest(), 'stderr_sha256': hashlib.sha256(b'').hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'
            seal = sealed_bundle(root, {'inventory/frozen-manifest.json': json.dumps({'baseline': 'manufactured'}),
                'captures.json': json.dumps({'records': [record]}), 'additional-captures.json': '{"records":[]}',
                'inputs/retrieval-quality-stream.stdin': stdin, 'raw/retrieval-quality-stream.stdout': stdout,
                'raw/retrieval-quality-stream.stderr': b''})
            with mock.patch.object(capture, '_fixed_digests'):
                catalog = capture.import_bundles([(root, seal)], Path(directory) / 'catalog')
            cold = [cell for cell in catalog['cells'] if cell['id'].startswith('stream/') and cell['id'].endswith('/cold')]
            self.assertEqual(sum(cell['status'] == 'unqualified' for cell in cold), 1)
            self.assertEqual(sum(cell['status'] == 'missing' for cell in cold), 18)  # 16 legacy + 2 never captured
            warm_observations = [entry for cell in catalog['cells'] if cell['id'].endswith('/warm') for entry in cell['observations']]
            self.assertEqual(len(warm_observations), 33)

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
                capture._reviewed_isolation(module, receipt, '0' * 64)
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
            contract = Path(directory) / 'command.json'
            contract.write_text(json.dumps(manufactured_command_contract()))
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
                '--isolation-proof', str(proof), '--command-contract', str(contract),
                '--parent-net-namespace', os.readlink('/proc/self/ns/net')],
                capture_output=True, text=True, timeout=10,
                env=dict(os.environ, PYTHONPATH=str(Path(capture.__file__).parent)))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('network namespace', result.stderr)
            self.assertNotIn('model imported', result.stderr)

    def test_real_bwrap_guard_accepts_declared_environment_and_refuses_an_extra(self):
        """Real namespace, manufactured home/proof; all model imports blocked."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / 'private-home'
            home.mkdir(mode=0o700)
            probe = home / '.semantic-isolation-probe'
            probe.write_text('manufactured guard fixture')
            passwd = root / 'passwd'
            passwd.write_text(f'fixture:x:{os.getuid()}:{os.getgid()}:fixture:/fixture-home:/bin/sh\n')
            group = root / 'group'
            group.write_text(f'fixture:x:{os.getgid()}:\n')
            environment = {'PATH': '/usr/bin:/bin', 'HF_HUB_OFFLINE': '1',
                'HF_HUB_DISABLE_TELEMETRY': '1', 'TOKENIZERS_PARALLELISM': 'false',
                'CAIRN_DECLARED_TEST_VALUE': 'declared'}
            prefix = ['bwrap', '--unshare-all', '--die-with-parent', '--new-session',
                '--clearenv', '--ro-bind', '/usr', '/usr']
            for name in ('bin', 'lib', 'lib64', 'sbin'):
                path = Path('/') / name
                if path.is_symlink():
                    prefix += ['--symlink', os.readlink(path), '/' + name]
                elif path.exists():
                    prefix += ['--ro-bind', str(path), str(path)]
            prefix += ['--proc', '/proc', '--dev', '/dev', '--dir', '/etc',
                '--ro-bind', str(passwd), '/etc/passwd', '--ro-bind', str(group), '/etc/group',
                '--bind', str(home), '/fixture-home',
                '--ro-bind', str(Path(capture.__file__).parent), str(Path(capture.__file__).parent)]
            proof = root / 'proof.json'
            contract_path = root / 'command.json'
            prefix += ['--ro-bind', str(proof), '/fixture-proof.json', '--ro-bind', str(contract_path), '/fixture-command.json',
                '--chdir', '/', '--remount-ro', '/']
            proof.write_text(json.dumps({'profile': 'default', 'home': '/fixture-home',
                'home_probe_inode': probe.stat().st_ino, 'effective_prefix': prefix}))
            contract = manufactured_command_contract()
            contract['environment'] = dict(environment, PWD='/')
            contract['cpython_environment'] = dict(contract['environment'], LC_CTYPE='C.UTF-8')
            contract_path.write_text(json.dumps(contract))
            command = [
                'import sys',
                'class NoModel:',
                ' def find_spec(self, fullname, path=None, target=None):',
                "  if fullname.split('.')[0] in {'numpy','fastembed','onnxruntime','tokenizers'}:",
                "   raise AssertionError('model import during preflight')",
                'sys.meta_path.insert(0,NoModel())',
                f'sys.path.insert(0,{str(Path(capture.__file__).parent)!r})',
                'import capture_semantic_fixtures as capture',
                "raise SystemExit(capture.main(['_collect','--preflight-only','--mode','rank','--case-id','manufactured',"
                "'--request','/absent','--sources','/absent','--assets','/absent','--output','/absent',"
                "'--isolation-proof','/fixture-proof.json','--command-contract','/fixture-command.json','--parent-net-namespace',"
                + repr(os.readlink('/proc/self/ns/net')) + ']))']
            for extra in (False, True):
                env_args = [part for key, value in environment.items() for part in ('--setenv', key, value)]
                if extra:
                    env_args += ['--setenv', 'CAIRN_UNDECLARED_TEST_VALUE', 'refuse']
                result = subprocess.run(prefix + env_args + ['--', '/usr/bin/python3', '-I', '-B', '-S', '-c',
                    '\n'.join(command)], capture_output=True, text=True, timeout=15)
                if extra:
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertIn('environment', result.stderr)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertFalse(json.loads(result.stdout)['model_imported'])
                self.assertNotIn('model import during preflight', result.stderr)

    def test_frozen_sources_execute_verified_bytes_despite_adjacent_pyc(self):
        """Only temporary source copies/pyc; never touch installed bytecode."""
        source_root = Path(capture.__file__).parent
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(sys.modules):
            root = Path(directory)
            for filename in capture.SOURCE_DIGESTS:
                raw = (source_root / filename).read_bytes()
                malicious = b"raise AssertionError('adjacent pyc executed')\n"
                path = root / filename
                path.write_bytes(malicious + b' ' * (len(raw) - len(malicious)))
                os.utime(path, (1700000000, 1700000000))
                py_compile.compile(str(path), doraise=True)
                path.write_bytes(raw)
                os.utime(path, (1700000000, 1700000000))
            rank, embed = capture._load_frozen_sources(root)
            self.assertEqual(rank.ALGORITHM, 'bge-max-chunk/1')
            self.assertEqual(embed.ALGORITHM, 'bge-original-passages/1')
            self.assertIs(embed.semantic_rank, rank)
            self.assertTrue((root / '__pycache__').is_dir())
            (root / 'semantic_rank.py').write_text('changed source')
            with self.assertRaisesRegex(capture.EvidenceError, 'source mismatch'):
                capture._load_frozen_sources(root)

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
    if len(sys.argv) > 1 and sys.argv[1] == '--fake-onnx-worker':
        fake_onnx_worker(Path(sys.argv[2]), sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    else:
        unittest.main()
