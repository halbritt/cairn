#!/usr/bin/env python3
"""Capture/import semantic references; integrity never implies model qualification."""

import argparse
import ast
import contextlib
import ctypes
import errno
import hashlib
import importlib.util
import io
import json
import math
import os
import pwd
import platform
import re
import resource
import shutil
import socket
import stat
import struct
import subprocess
import sys
import time
import traceback
import zipfile
from pathlib import Path

from semantic_fixture_cases import embedding_cases, embedding_failure_cases, ranking_cases

SOURCE_DIGESTS = {
    'semantic_rank.py': '4f70d19c1442bebe812cf0fab667a214992575d68737b981189783211916de95',
    'embedding_worker.py': 'f8b134f92523eb53ab21f7d4878283f5a6dc401ecb605c7e8d7e2df2aa673ae8',
}
ASSET_DIGESTS = {
    'model_optimized.onnx': '51f1bd0addd6e859e42c2c8021a5e5461385bb676a649f4b269aa445449f2431',
    'config.json': '13582bcf2effc85b7bf3d3f5532e686bc1c9ce86bb009d10f0ec33cbe92299dd',
    'tokenizer.json': 'd241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66',
    'special_tokens_map.json': '5d5b662e421ea9fac075174bb0688ee0d9431699900b90662acd44b2a350503a',
    'tokenizer_config.json': '0b29c7bfc889e53b36d9dd3e686dd4300f6525110eaa98c76a5dafceb2029f53',
}
RANK_IDENTITY = '057e65f9a513a73360112a43676db6aff5faf047e6822d1302ffc2c6de6b4a3b'
EMBED_IDENTITY = '042ef8d9cc096cd8416e230599b919c7c834a9c29c174c0b2ee072f02332d45b'
REVIEWER_RAW_BASELINE = {
    'ids_i64le': '1762a94e5b785ffcfa300f404252994026b18a71f763086f297f89a9809394a9',
    'lengths_i64le': 'a81f2c9f819d10049549d14dcdb38983febf0a41cbfe314421e4f70bba8ded33',
    'cls_f32le': '1f7ba8c969c2053385ce6e89528d2decea78b627e9fb6ef40cfc322fd760b623',
}
NATIVE_DIGESTS = {
    'onnxruntime_pybind11_state': 'a434baf53b7eba95c0c819eb67505099d9c8027e1fce4f3e25a0a368bc01045b',
    '_multiarray_umath': '1535fbed1e9ac6b717e72ad9bd5c20b9f0536a21084fcadc40c00255a8f35d1d',
    'openblas': '05c9f9eb89ee68a4b9d673184fa91c99587e736392c0c2d49180a8aa5303d080',
}


class EvidenceError(ValueError):
    pass


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


@contextlib.contextmanager
def _sealed_file(root, relative):
    if (not isinstance(relative, str) or not relative or relative.startswith('/') or '\\' in relative
            or any(part in ('', '.', '..') for part in relative.split('/'))):
        raise EvidenceError('unsafe relative path in evidence: ' + str(relative))
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        parts = relative.split('/')
        for component in parts[:-1]:
            directory = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise EvidenceError('sealed evidence must be a regular file: ' + relative)
            yield stream
    except OSError as error:
        raise EvidenceError('missing/unsafe sealed file or symlink: ' + relative) from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def verify_sealed_bundle(root, expected_sha256):
    """Verify a caller-pinned seal and every listed file without following links."""
    root = Path(root).resolve(strict=True)
    if not isinstance(expected_sha256, str) or not re.fullmatch('[0-9a-f]{64}', expected_sha256):
        raise EvidenceError('seal SHA-256 must contain 64 lowercase hex digits')
    with _sealed_file(root, 'SHA256SUMS') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise EvidenceError('seal digest mismatch')
    if len(raw) > 1024 * 1024:
        raise EvidenceError('seal exceeds 1 MiB')
    entries = {}
    for line in raw.decode('utf-8').splitlines():
        match = re.fullmatch('([0-9a-f]{64})  (.+)', line)
        if not match:
            raise EvidenceError('invalid SHA256SUMS line')
        digest, relative = match.groups()
        if relative == 'SHA256SUMS' or relative in entries:
            raise EvidenceError('duplicate or self-referential sealed file: ' + relative)
        with _sealed_file(root, relative) as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
                raise EvidenceError('file digest mismatch: ' + relative)
        entries[relative] = digest
    if not entries:
        raise EvidenceError('empty evidence seal')
    return entries


def _strict_json(raw):
    def reject(value):
        raise EvidenceError('nonfinite JSON constant: ' + value)
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise EvidenceError('nonfinite JSON number: ' + value)
        return result
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    try:
        return json.loads(raw, parse_constant=reject, parse_float=finite_float, object_pairs_hook=object_pairs)
    except (ValueError, UnicodeDecodeError) as error:
        raise EvidenceError('invalid evidence JSON: ' + str(error)) from error


def _same_json(left, right):
    return (json.dumps(left, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
            == json.dumps(right, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False))


class _Bundle:
    def __init__(self, root, seal):
        self.root = Path(root).resolve(strict=True)
        self.seal = seal
        self.files = verify_sealed_bundle(self.root, seal)

    def raw(self, relative):
        if not isinstance(relative, str) or relative not in self.files:
            raise EvidenceError('unsealed required file: ' + relative)
        with _sealed_file(self.root, relative) as stream:
            raw = stream.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise EvidenceError('structured evidence exceeds 32 MiB: ' + relative)
        if hashlib.sha256(raw).hexdigest() != self.files[relative]:
            raise EvidenceError('file digest mismatch after verification: ' + relative)
        return raw

    def json(self, relative):
        value = _strict_json(self.raw(relative))
        if not isinstance(value, dict):
            raise EvidenceError('expected a JSON object: ' + relative)
        return value


def reference_inventory():
    rank = ranking_cases()
    embed = embedding_cases()
    cells = ['rank/' + case['id'] for case in rank]
    cells += ['stream/' + case['id'] + '/' + temperature for case in rank for temperature in ('cold', 'warm')]
    cells += ['stream/idle-restore']
    cells += ['embed/' + case['id'] for case in embed]
    cells += ['arithmetic/raw-294', 'tests/prepared-23', 'oracle/punctuation-numerical', 'failures/s01b']
    return {'schema': 'cairn.semantic-reference-inventory/1', 'contract': 'semantic/4',
            'rank_requests': 19, 'rank_scores': 269, 'stream_replies': 38, 'idle_restore_cases': 1,
            'positive_queries': 9, 'positive_documents': 6, 'prepared_tests': 23, 'required_cells': cells}


def cpu_kernel_class(facts):
    """Hash effective capability/getter inputs; marketing names cannot fill gaps."""
    names = ('architecture', 'byte_order', 'pointer_bits', 'effective_isa', 'os_vector_state', 'blas_core', 'blas_config')
    if not isinstance(facts, dict) or any(name not in facts for name in names):
        raise EvidenceError('missing observed CPU/kernel class inputs')
    canonical = {name: facts[name] for name in names}
    for name in ('architecture', 'blas_core', 'blas_config'):
        if (not isinstance(canonical[name], str) or not canonical[name].strip()
                or canonical[name].strip().lower() in ('unknown', 'unobserved', 'default', 'none')):
            raise EvidenceError('unknown CPU/kernel input: ' + name)
    if canonical['byte_order'] not in ('little', 'big') or canonical['pointer_bits'] not in (32, 64):
        raise EvidenceError('unknown CPU/kernel ABI')
    for name in ('effective_isa', 'os_vector_state'):
        values = canonical[name]
        if not isinstance(values, list) or not values or not all(isinstance(value, str) and value for value in values):
            raise EvidenceError('unknown CPU/kernel capabilities: ' + name)
        canonical[name] = sorted(set(values))
    raw = json.dumps(canonical, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return {'inputs': canonical, 'sha256': hashlib.sha256(raw).hexdigest()}


def _capture_manifest(bundle):
    document = bundle.json('capture-manifest.json')
    if document.get('schema') != 'cairn.semantic-reference-capture/1':
        raise EvidenceError('unsupported reference capture schema')
    provenance = document.get('provenance')
    if not isinstance(provenance, dict) or provenance.get('kind') not in ('mechanics', 'python-reference'):
        raise EvidenceError('missing or invalid capture provenance')
    observations = document.get('observations')
    if not isinstance(observations, list):
        raise EvidenceError('capture observations must be a list')
    if provenance['kind'] == 'python-reference':
        return _python_capture(bundle, document)
    result = []
    for observation in observations:
        if not isinstance(observation, dict) or not isinstance(observation.get('cell'), str):
            raise EvidenceError('invalid observation cell')
        artifacts = observation.get('artifacts')
        if not isinstance(artifacts, dict) or not artifacts:
            raise EvidenceError('missing observation artifacts')
        for relative in artifacts.values():
            bundle.raw(relative)
        result.append(dict(observation, status='unqualified', reason='manufactured mechanics fixture; no model evidence'))
    return {'kind': 'capture', 'provenance': provenance, 'observations': result}


def _array_archive(bundle, relative, descriptions):
    """Check .npy headers/bits using stdlib only, without executing pickle."""
    if not isinstance(descriptions, dict) or not descriptions:
        raise EvidenceError('missing typed tensor descriptions')
    tensors = {}
    try:
        with zipfile.ZipFile(io.BytesIO(bundle.raw(relative))) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != {name + '.npy' for name in descriptions}:
                raise EvidenceError('tensor archive inventory mismatch')
            if sum(info.file_size for info in archive.infolist()) > 32 * 1024 * 1024:
                raise EvidenceError('tensor archive expands above 32 MiB')
            for name, description in descriptions.items():
                raw = archive.read(name + '.npy')
                if raw[:6] != b'\x93NUMPY' or raw[6:8] not in (b'\x01\x00', b'\x02\x00'):
                    raise EvidenceError('unsupported typed tensor header')
                size_bytes = 2 if raw[6] == 1 else 4
                start = 8 + size_bytes
                header_size = int.from_bytes(raw[8:start], 'little')
                if not 0 < header_size < 65536:
                    raise EvidenceError('invalid tensor header length')
                header = ast.literal_eval(raw[start:start + header_size].decode('ascii'))
                dtype, shape = header.get('descr'), header.get('shape')
                if (dtype not in ('<f4', '<i8') or header.get('fortran_order') is not False
                        or not isinstance(shape, tuple) or not shape
                        or not all(type(dimension) is int and dimension > 0 for dimension in shape)):
                    raise EvidenceError('unsupported tensor dtype, shape or layout')
                data = raw[start + header_size:]
                if (len(data) != math.prod(shape) * (4 if dtype == '<f4' else 8)
                        or description.get('dtype') != dtype or description.get('shape') != list(shape)
                        or description.get('bytes_sha256') != hashlib.sha256(data).hexdigest()):
                    raise EvidenceError('tensor bytes/metadata mismatch')
                if dtype == '<f4' and not all(math.isfinite(value[0]) for value in struct.iter_unpack('<f', data)):
                    raise EvidenceError('nonfinite raw arithmetic tensor')
                tensors[name] = {'dtype': dtype, 'shape': shape, 'data': data}
    except (zipfile.BadZipFile, SyntaxError, KeyError, struct.error) as error:
        raise EvidenceError('invalid tensor archive: ' + str(error)) from error
    return tensors


def _review_receipt(review):
    record = review.get('review_record', {}) if isinstance(review, dict) else {}
    def matches(pattern, value):
        return isinstance(value, str) and bool(re.fullmatch(pattern, value))
    return (isinstance(review, dict) and review.get('schema') == 'cairn.semantic-isolation-review/1'
        and review.get('status') == 'accepted'
        and matches('[0-9a-f]{40}', review.get('source_commit'))
        and isinstance(record, dict)
        and matches('[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', record.get('record_id'))
        and type(record.get('version')) is int and record['version'] > 0
        and matches('[0-9a-f]{64}', record.get('body_sha256'))
        and matches('[0-9a-f]{64}', review.get('module_sha256')))


def _python_capture(bundle, document):
    provenance = document['provenance']
    review = provenance.get('wrapper_review')
    if not _review_receipt(review):
        raise EvidenceError('missing accepted isolation review provenance')
    for field, relative in (('capture_source_sha256', 'reference/tooling/capture_semantic_fixtures.py'),
                            ('fixture_source_sha256', 'reference/tooling/semantic_fixture_cases.py')):
        bundle.raw(relative)
        if bundle.files[relative] != provenance.get(field):
            raise EvidenceError('capture tooling provenance mismatch')
    if (provenance.get('sources') != SOURCE_DIGESTS or provenance.get('assets') != ASSET_DIGESTS
            or not re.fullmatch('[0-9a-f]{40}', provenance.get('source_commit', ''))):
        raise EvidenceError('missing or changed source/asset provenance')
    for directory, digests in (('reference/scripts/', SOURCE_DIGESTS), ('assets/', ASSET_DIGESTS)):
        for filename, digest in digests.items():
            if bundle.files.get(directory + filename) != digest:
                raise EvidenceError('missing or changed frozen input: ' + filename)
    bundle.raw('reference/tooling/isolation-review.json')
    if (bundle.json('reference/tooling/isolation-review.json') != review
            or bundle.files.get('reference/tooling/semantic_acceptance_isolation.py') != review['module_sha256']):
        raise EvidenceError('reviewed wrapper provenance mismatch')
    observations = document['observations']
    phase = document.get('selected_phase')
    if phase not in ('rank', 'embed'):
        raise EvidenceError('invalid selected capture phase')
    expected_cases = ranking_cases() if phase == 'rank' else embedding_cases()
    failed = document.get('failure')
    if failed:
        # Failed attempts retain files and commands but never fill an acceptance cell.
        return {'kind': 'failed-capture', 'provenance': provenance, 'failure': failed, 'observations': []}
    if [entry.get('cell') for entry in observations] != [phase + '/' + case['id'] for case in expected_cases]:
        raise EvidenceError('missing, zero, duplicate or reordered positive observations')
    external = provenance.get('actual_worker_observation', {})
    workers = external.get('model_workers', [])
    if not workers or external.get('errors') != []:
        raise EvidenceError('missing actual worker observation')
    if provenance.get('capture_scope') != 'ordinary-default-reference' or provenance.get('conditional_b1_proof') is not False:
        raise EvidenceError('ordinary capture is not the conditional B1 proof')
    cleanup = provenance.get('cleanup_evidence', {})
    home_receipt = bundle.json(cleanup.get('home_receipt', ''))
    final_receipt = bundle.json(cleanup.get('final_receipt', ''))
    identity = home_receipt.get('initial_identity')
    if (cleanup.get('backing_absent_observed') is not True or cleanup.get('backing') != home_receipt.get('backing')
            or not isinstance(cleanup.get('backing'), str) or not Path(cleanup['backing']).is_absolute()
            or cleanup.get('initial_identity') != identity or not isinstance(identity, dict)
            or set(identity) != {'inode', 'device', 'owner', 'mode'}
            or not all(type(value) is int for value in identity.values())
            or identity['inode'] <= 0 or identity['device'] < 0 or identity['owner'] < 0 or identity['mode'] != 0o700
            or final_receipt.get('backing_removed') is not True
            or final_receipt.get('all_observed_processes_exited') is not True or final_receipt.get('errors') != []):
        raise EvidenceError('missing/mismatched declared backing identity or joined cleanup receipt')
    proof = provenance.get('isolation_proof', {})
    if (proof.get('wrapper_sha256') != review['module_sha256'] or not proof.get('home_probe_inode')
            or proof.get('profile') != 'default' or bundle.json('isolation/isolation-proof.json') != proof
            or final_receipt.get('isolation_proof_sha256') != bundle.files.get('isolation/isolation-proof.json')
            or cleanup.get('isolation_proof_sha256') != final_receipt['isolation_proof_sha256']
            or home_receipt.get('profile') != proof['profile'] or home_receipt.get('payload_inspected') is not False
            or not isinstance(final_receipt.get('invocations'), list)):
        raise EvidenceError('missing reviewed-wrapper confinement preflight')
    result = []
    aggregate = {name: bytearray() for name in REVIEWER_RAW_BASELINE}
    counts = dict(logical_occurrences=0, observed_model_executions=0, mapped_duplicate_rows=0)
    for observation, case in zip(observations, expected_cases):
        if observation.get('exit_code') != 0 or observation.get('status') != 'observed' or observation.get('skipped', 0) != 0:
            raise EvidenceError('failed/skipped positive reference observation')
        artifacts = observation.get('artifacts', {})
        if not all(key in artifacts for key in ('request', 'stdout', 'stderr', 'trace', 'arrays', 'runtime',
                                               'command_contract', 'invocation', 'runtime_binding')):
            raise EvidenceError('missing positive reference artifacts')
        for relative in artifacts.values():
            bundle.raw(relative)
        request = bundle.json(artifacts['request'])
        expected = ({key: case[key] for key in ('query', 'notes')} if phase == 'rank' else
                    {key: case[key] for key in ('id', 'operation', 'text')})
        if not _same_json(request, expected):
            raise EvidenceError('captured fixture input drift')
        reply = bundle.json(artifacts['stdout'])
        (_rank_reply if phase == 'rank' else _embedding_reply)(request, reply)
        runtime = bundle.json(artifacts['runtime'])
        if (runtime.get('schema') != 'cairn.semantic-python-runtime/1'
                or cpu_kernel_class(runtime.get('cpu_kernel_class', {}).get('inputs')) != runtime.get('cpu_kernel_class')):
            raise EvidenceError('missing or mismatched canonical CPU/kernel class')
        if (not runtime.get('network_namespace') or
                runtime['network_namespace'] == provenance.get('host_network_namespace') or
                runtime.get('passwd_home') != proof.get('home') or
                runtime.get('expanduser_home') != proof.get('home') or
                runtime.get('home_probe_inode') != proof['home_probe_inode']):
            raise EvidenceError('model child did not retain the network/OS-home boundary')
        invocation = bundle.json(artifacts['invocation'])
        declaration_path, declaration = _invocation_receipt(invocation)
        if (invocation != observation.get('invocation') or invocation not in final_receipt['invocations']
                or invocation['mode'] != phase
                or invocation['contract'] != bundle.json(artifacts['command_contract'])
                or not observation.get('command')
                or invocation['argv'][-len(observation['command']):] != observation['command']
                or bundle.json(declaration_path) != declaration
                or bundle.files.get(declaration_path) != invocation['declaration_sha256']):
            raise EvidenceError('missing/mismatched command-specific invocation declaration')
        worker = bundle.json(artifacts['runtime_binding'])
        matching = [item for item in workers if item.get('launcher_pid') == invocation['pid']
                    and item.get('launcher_start_ticks') == invocation['start_ticks']]
        if matching != [worker]:
            raise EvidenceError('missing/ambiguous per-invocation actual worker observation')
        _validate_runtime_binding(invocation, worker, runtime, proof, provenance.get('host_network_namespace'))
        trace = bundle.json(artifacts['trace'])
        if (trace.get('case_id') != case['id'] or trace.get('mode') != phase
                or trace.get('scope') != 'ordinary-default-reference' or trace.get('conditional_b1_proof') is not False
                or not trace.get('replay') or not all(value is True for value in trace['replay'].values())):
            raise EvidenceError('missing/mismatched bit-exact arithmetic trace')
        tensors = _array_archive(bundle, artifacts['arrays'], trace.get('arrays'))
        inferences, rows = trace.get('inferences', []), trace.get('row_order', [])
        expected_counts = dict(logical_occurrences=len(rows), observed_model_executions=len(inferences),
                               mapped_duplicate_rows=len(rows) - len(inferences))
        if (not inferences or not rows or len(rows) < len(inferences)
                or trace.get('unique_inferences') != len(inferences)
                or any(type(trace.get(key)) is not int or trace[key] != value for key, value in expected_counts.items())
                or any(observation.get(key) != value for key, value in expected_counts.items())):
            raise EvidenceError('missing actual inference or occurrence mapping')
        for key, value in expected_counts.items():
            counts[key] += value
        if len(trace.get('first_stage', [])) != len(inferences):
            raise EvidenceError('missing first normalization stage')
        for index, inference in enumerate(inferences):
            if inference.get('index') != index:
                raise EvidenceError('reordered actual inference indices')
            inputs = inference.get('inputs', {})
            if set(inputs) != {'input_ids', 'attention_mask', 'token_type_ids'}:
                raise EvidenceError('missing actual int64 input tensors')
            shape = tensors[inputs['input_ids']]['shape']
            for name in inputs.values():
                if tensors[name]['dtype'] != '<i8' or tensors[name]['shape'] != shape or shape[0] != 1:
                    raise EvidenceError('actual input tensor shape/dtype mismatch')
            if (tensors[inference['raw_cls']]['shape'] != (1, 384) or tensors[inference['raw_cls']]['dtype'] != '<f4'
                    or inference.get('hidden_shape') != [1, shape[1], 384]):
                raise EvidenceError('raw CLS shape mismatch')
            for field, expected in (('attention_mask', 1), ('token_type_ids', 0)):
                if any(value[0] != expected for value in struct.iter_unpack('<q', tensors[inputs[field]]['data'])):
                    raise EvidenceError('actual mask/type tensor mismatch')
            stage = trace['first_stage'][index]
            if stage.get('p') != 2 or stage.get('dim') != 1 or stage.get('eps') != 1e-12:
                raise EvidenceError('normalization settings mismatch')
            for field in ('pooled', 'norm_unclipped', 'norm', 'normalized'):
                tensor = tensors[stage[field]]
                if tensor['dtype'] != '<f4' or tensor['shape'] != ((1, 384) if field in ('pooled', 'normalized') else (1, 1)):
                    raise EvidenceError('normalization tensor shape/dtype mismatch')
        if phase == 'rank':
            texts = trace.get('inference_texts', [])
            if len(texts) != len(inferences) or texts[0] != 'Represent this sentence for searching relevant passages: ' + request['query']:
                raise EvidenceError('missing inference text mapping')
            owners, keys = [], []
            for ordinal, row in enumerate(rows):
                index = row.get('inference')
                if type(index) is not int or not 0 <= index < len(inferences) or row.get('row') != ordinal:
                    raise EvidenceError('invalid occurrence-to-inference mapping')
                if ordinal == 0:
                    if index != 0 or row.get('role') != 'prefixed_query':
                        raise EvidenceError('query is not the first logical row')
                else:
                    owner = row.get('note_index')
                    key = row.get('passage_sha256')
                    if (type(owner) is not int or not 0 <= owner < len(request['notes']) or index == 0
                            or row.get('record_id') != request['notes'][owner]['record_id']
                            or key != hashlib.sha256(texts[index].encode()).hexdigest()
                            or trace.get('missing', {}).get(key) != index):
                        raise EvidenceError('note owner/passage/inference mapping mismatch')
                    owners.append(owner)
                    keys.append(key)
                inference = inferences[index]
                tensor = tensors[inference['inputs']['input_ids']]
                aggregate['ids_i64le'].extend(tensor['data'])
                aggregate['lengths_i64le'].extend(struct.pack('<q', tensor['shape'][1]))
                aggregate['cls_f32le'].extend(tensors[inference['raw_cls']]['data'])
            if trace.get('owners') != owners or trace.get('passage_keys') != keys or set(owners) != set(range(len(request['notes']))):
                raise EvidenceError('missing or reordered note ownership')
        reason = 'validated isolated traced Python reference; actual Go-supervisor evidence remains with S-01b'
        if phase == 'embed':
            reason += '; wrapper raw-output invariance for all query/document inputs remains unproved'
        result.append(dict(observation, status='unqualified', reason=reason))
    if phase == 'rank':
        digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in aggregate.items()}
        if digests != REVIEWER_RAW_BASELINE or provenance.get('expanded_row_digests') != digests:
            raise EvidenceError('ordinary expanded-row digest mismatch; no B1 proof')
        for name, raw in aggregate.items():
            if bundle.raw(name + '.bin') != raw:
                raise EvidenceError('aggregate raw tensor bytes mismatch')
    if provenance.get('execution_counts') != counts:
        raise EvidenceError('observed-execution/logical-mapping totals mismatch')
    return {'kind': 'isolated-python-reference', 'provenance': provenance, 'observations': result}


def _fixed_digests(bundle, source_entries, asset_entries, *, asset_copies=False):
    for filename, digest in SOURCE_DIGESTS.items():
        relative = 'reference/scripts/' + filename
        if source_entries.get('scripts/' + filename, source_entries.get(filename, {})).get('sha256') != digest:
            raise EvidenceError('missing or changed source provenance: ' + filename)
        bundle.raw(relative)
        if bundle.files[relative] != digest:
            raise EvidenceError('frozen worker source mismatch: ' + filename)
    for filename, digest in ASSET_DIGESTS.items():
        if asset_entries.get(filename, {}).get('sha256') != digest:
            raise EvidenceError('missing or changed asset provenance: ' + filename)
        if asset_copies and bundle.files.get('assets/' + filename) != digest:
            raise EvidenceError('missing or changed sealed asset: ' + filename)


def _observation(cell, artifacts, *, reason, **details):
    return dict(cell=cell, status='unqualified', reason=reason, artifacts=artifacts, **details)


def _embedding_reply(request, reply):
    if not isinstance(reply, dict):
        raise EvidenceError('embedding reply must be an object')
    identity = {'model_sha256': EMBED_IDENTITY, 'algorithm': 'bge-original-passages/1', 'dimensions': 384}
    if reply.get('id') != request.get('id') or reply.get('identity') != identity:
        raise EvidenceError('embedding reply identity mismatch')
    if request['operation'] == 'query':
        if set(reply) != {'id', 'identity', 'query_projection', 'vector'}:
            raise EvidenceError('query reply keys differ from the wire contract')
        vectors = [reply.get('vector')]
        projection = reply.get('query_projection')
        if (not isinstance(projection, dict) or set(projection) != {'method', 'truncated', 'original_tokens',
                'embedded_tokens', 'prefix_bytes', 'prefix_sha256'} or projection.get('method') != 'original-prefix/1'):
            raise EvidenceError('missing query projection')
        count = projection.get('prefix_bytes')
        if type(count) is not int or not 0 < count <= len(request['text'].encode('utf-8')):
            raise EvidenceError('invalid original prefix length')
        prefix = _utf8(request['text'].encode('utf-8')[:count])
        if hashlib.sha256(prefix.encode()).hexdigest() != projection.get('prefix_sha256'):
            raise EvidenceError('original prefix hash mismatch')
        if type(projection.get('embedded_tokens')) is not int or not 0 < projection['embedded_tokens'] <= 512:
            raise EvidenceError('invalid embedded token count')
        original = projection['original_tokens']
        truncated = projection['truncated']
        if (type(original) is not int or original < projection['embedded_tokens']
                or type(truncated) is not bool or truncated != (count < len(request['text'].encode('utf-8')))
                or (truncated and original <= 512) or (not truncated and original != projection['embedded_tokens'])):
            raise EvidenceError('inconsistent original-prefix projection')
    else:
        if set(reply) != {'id', 'identity', 'passages'}:
            raise EvidenceError('document reply keys differ from the wire contract')
        passages = reply.get('passages')
        if not isinstance(passages, list) or not 1 <= len(passages) <= 256:
            raise EvidenceError('invalid passage count')
        body = request['text'].encode('utf-8')
        vectors = []
        previous = -1
        for passage in passages:
            if (not isinstance(passage, dict) or set(passage) != {'span', 'vector'}
                    or not isinstance(passage.get('span'), dict) or set(passage['span']) != {'offset', 'length'}):
                raise EvidenceError('invalid source passage')
            span = passage.get('span', {})
            offset, length = span.get('offset'), span.get('length')
            if (type(offset) is not int or type(length) is not int or not 0 <= offset < len(body)
                    or not 0 < length <= len(body) - offset or offset < previous):
                raise EvidenceError('invalid source span')
            _utf8(body[:offset])
            _utf8(body[offset:offset + length])
            previous = offset
            vectors.append(passage.get('vector'))
    for vector in vectors:
        if (not isinstance(vector, list) or len(vector) != 384
                or not all(_finite_f32(component) for component in vector)
                or not any(vector)):
            raise EvidenceError('invalid finite/nonzero 384-component vector')
    return len(vectors)


def _utf8(raw):
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError as error:
        raise EvidenceError('source span/projection splits UTF-8') from error


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _finite_f32(value):
    if not _finite(value):
        return False
    try:
        return struct.unpack('<f', struct.pack('<f', value))[0] == value
    except (OverflowError, struct.error):
        return False


def _rank_reply(request, reply):
    if not isinstance(reply, dict) or reply.get('model_sha256') != RANK_IDENTITY or reply.get('algorithm') != 'bge-max-chunk/1':
        raise EvidenceError('rank model/algorithm mismatch')
    if set(reply) != {'model_sha256', 'algorithm', 'scores'}:
        raise EvidenceError('rank reply keys differ from the wire contract')
    scores = reply.get('scores')
    if not isinstance(scores, list) or len(scores) != len(request['notes']):
        raise EvidenceError('rank score count mismatch')
    for note, score in zip(request['notes'], scores):
        if not isinstance(score, dict) or set(score) != {'record_id', 'version', 'body_sha256', 'score'}:
            raise EvidenceError('rank score must be an object')
        if (type(score.get('version')) is not int
                or any(score.get(key) != note[key] for key in ('record_id', 'version', 'body_sha256'))):
            raise EvidenceError('rank source metadata/order mismatch')
        if type(score.get('score')) is not int or not -1000000 <= score['score'] <= 1000000:
            raise EvidenceError('invalid discrete rank score')


def _characterization(bundle):
    frozen = bundle.json('inventory/frozen-manifest.json')
    _fixed_digests(bundle, frozen.get('source_files', {}), frozen.get('assets', {}), asset_copies=True)
    ledger = bundle.json('captures.json')['records'] + bundle.json('additional-captures.json')['records']
    observations = []
    auxiliary = []
    for record in ledger:
        name = record.get('name', '')
        if not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9-]{0,79}', name):
            raise EvidenceError('invalid captured record name')
        paths = {'request': 'inputs/' + name + '.stdin', 'stdout': 'raw/' + name + '.stdout',
                 'stderr': 'raw/' + name + '.stderr'}
        request_raw = bundle.raw(paths['request'])
        stdout = bundle.raw(paths['stdout'])
        bundle.raw(paths['stderr'])
        for field, relative in (('input_sha256', paths['request']), ('stdout_sha256', paths['stdout']), ('stderr_sha256', paths['stderr'])):
            if record.get(field) != bundle.files[relative]:
                raise EvidenceError('capture ledger/artifact hash mismatch: ' + name)
        if name == 'embed-dense-max-ascii':
            if (record.get('status') != 'timeout' or record.get('timeout_seconds') != 90 or stdout
                    or _strict_json(request_raw).get('text') != '!' * 65536):
                raise EvidenceError('changed direct-supervisor punctuation90s observation')
            auxiliary.append(dict(id='direct-supervisor/punctuation-90s', status='observed',
                observed_direct_supervisor_disposition='timeout', real_go_disposition='unmeasured',
                elapsed_seconds=record['elapsed_seconds'], artifacts=paths))
        elif name == 'existing-tests':
            log = bundle.raw(paths['stderr']).decode('utf-8')
            if record.get('exit_code') != 0 or 'Ran 23 tests' not in log or '\nOK\n' not in log or 'skipped' in log.lower():
                raise EvidenceError('missing/failed/skipped prepared23 test evidence')
            observations.append(_observation('tests/prepared-23', paths, reason='retained mechanics/tokenizer tests, not fresh isolated acceptance', test_count=23, skipped=0))
        elif name.startswith('embed-') and record.get('status') == 'observed' and record.get('exit_code') == 0:
            request = _strict_json(request_raw)
            if request['operation'] == 'identity':
                continue
            reply = _strict_json(stdout)
            count = _embedding_reply(request, reply)
            for case in embedding_cases():
                if (case['operation'], case['text']) == (request['operation'], request['text']):
                    observations.append(_observation('embed/' + case['id'], paths,
                        reason='legacy model observation; T1 wrapper/runtime proof absent, request ID mapping explicit',
                        original_request_id=request['id'], fixture_request_id=case['id'], vectors=count))
        elif name == 'retrieval-quality-stream':
            requests = [_strict_json(line) for line in request_raw.splitlines()]
            replies = [_strict_json(line) for line in stdout.splitlines()]
            if len(requests) != 34 or len(replies) != 34:
                raise EvidenceError('legacy stream count mismatch')
            rank = ranking_cases()[:17]
            for index, (envelope, reply) in enumerate(zip(requests, replies)):
                _rank_reply(envelope['request'], reply['result'])
                if envelope['id'] != reply.get('id'):
                    raise EvidenceError('stream request ID mismatch')
                case = rank[index // 2]
                temperature = 'cold' if index == 0 else 'warm'
                observations.append(_observation('stream/' + case['id'] + '/' + temperature, paths,
                    reason='legacy stream frames; only first frame is cold and no T1/runtime qualification',
                    original_request_id=envelope['id'], frame_index=index))
    return {'kind': 'legacy-characterization', 'observations': observations, 'auxiliary': auxiliary,
            'provenance': {'kind': 'legacy-unqualified', 'source_baseline': frozen['baseline'],
                           'source_digests': SOURCE_DIGESTS, 'asset_digests': ASSET_DIGESTS}}


def _arithmetic(bundle):
    manifest = bundle.json('inventory/input-manifest.json')
    _fixed_digests(bundle, manifest.get('files', {}), manifest.get('external_model_assets', {}))
    cases = ranking_cases()
    recorded = manifest.get('cases')
    if not isinstance(recorded, list) or [case.get('id') for case in recorded] != [case['id'] for case in cases]:
        raise EvidenceError('missing or reordered rank inventory')
    ledger = bundle.json('trace-commands.json')['records']
    if len(ledger) != 19 or any(record.get('exit_code') != 0 for record in ledger):
        raise EvidenceError('missing/failed rank trace invocation')
    observations = []
    for case, entry in zip(cases, recorded):
        input_path = 'cases/' + case['id'] + '.json'
        if entry.get('sha256') != bundle.files.get(input_path) or not _same_json(bundle.json(input_path), case):
            raise EvidenceError('frozen ranking input mismatch: ' + case['id'])
        prefix = 'raw/' + case['id'] + '/'
        result_path = prefix + 'reference-result.json'
        _rank_reply(case, bundle.json(result_path))
        trace = bundle.json(prefix + 'trace.json')
        if not trace.get('first_normalized_equals_embedded_bits') or any(
                entry.get('dtype') != '<f4' for entry in trace.get('arrays', {}).values()):
            raise EvidenceError('missing float32 arithmetic trace')
        bundle.raw(prefix + 'trace-arrays.npz')
        comparison = bundle.json(prefix + 'arithmetic-comparison.json')
        if not comparison.get('bit_exact_replay_steps') or not all(comparison['bit_exact_replay_steps'].values()):
            raise EvidenceError('arithmetic replay mismatch')
        observations.append(_observation('rank/' + case['id'], {'request': input_path, 'stdout': result_path,
            'trace': prefix + 'trace.json', 'arrays': prefix + 'trace-arrays.npz'},
            reason='frozen legacy arithmetic observation; wrapper invariance/int64 snapshot/runtime inode qualification missing', scores=len(case['notes'])))
    summary = bundle.json('arithmetic-summary.json')
    if summary.get('cases') != 19 or summary.get('scores') != 269 or summary.get('chunk_similarities') != 275:
        raise EvidenceError('arithmetic inventory count mismatch')
    bundle.json('inventory/runtime/fingerprint.json')
    bundle.json('inventory/math-fingerprint.json')
    ties = _strict_json(bundle.raw('isolated-ties.json'))
    numerators = (1, 3, 5, -1, -3, -5)
    expected_scores = (7812, 23438, 39062, -7812, -23438, -39062)
    if not isinstance(ties, list) or len(ties) != 6 or any(
            entry.get('kind') != 'isolated arithmetic fixture; not a model tie'
            or entry.get('numerator') != numerator or entry.get('denominator') != 128
            or entry.get('similarity_f32') != numerator / 128
            or entry.get('ties_to_even_score') != score
            for entry, numerator, score in zip(ties, numerators, expected_scores)):
        raise EvidenceError('missing or mislabeled isolated arithmetic controls')
    variants = summary.get('variants', {})
    expected_changes = {'f64_normalize_and_matvec': 14, 'f32_per_row_dot': 3, 'f32_single_normalization': 7}
    if any(variants.get(name, {}).get('changed_scores') != count for name, count in expected_changes.items()):
        raise EvidenceError('missing frozen alternative-arithmetic negative controls')
    return {'kind': 'legacy-arithmetic', 'observations': observations,
            'auxiliary': {'negative_arithmetic_controls': {name: variants[name] for name in expected_changes},
                          'isolated_ties': ties, 'actual_model_exact_ties': summary.get('actual_model_exact_scaled_ties')},
            'provenance': {'kind': 'legacy-unqualified', 'source_baseline': manifest['source_baseline'],
                           'source_digests': SOURCE_DIGESTS, 'asset_digests': ASSET_DIGESTS}}


def _unqualified_oracle(bundle):
    manifest = bundle.json('inventory/experiment-manifest.json')
    sources = manifest.get('sources', {})
    for filename, digest in SOURCE_DIGESTS.items():
        if sources.get(filename, {}).get('sha256') != digest or bundle.files.get('reference/' + filename) != digest:
            raise EvidenceError('missing oracle source provenance')
    for filename, digest in ASSET_DIGESTS.items():
        if manifest.get('assets', {}).get(filename, {}).get('sha256') != digest:
            raise EvidenceError('missing oracle asset provenance')
    attempt = bundle.json('oracle-attempt.json')
    request_raw = bundle.raw('inputs/request.stdin')
    if hashlib.sha256(request_raw).hexdigest() != '2da1f7d4cc8ba638f216251a1e87c3cea57aa2f0d9a672a40ff5d16a23132d23':
        raise EvidenceError('consumed oracle input mismatch')
    request = _strict_json(request_raw)
    raw = bundle.raw('raw/oracle.stdout')
    reply = _strict_json(raw)
    if (_embedding_reply(request, reply) != 256 or attempt.get('exit_code') != 0
            or attempt.get('external_deadline_seconds') != 180 or attempt.get('model_attempt_number') != 1
            or attempt.get('elapsed_seconds', 181) > 180 or attempt.get('stdout_sha256') != bundle.files['raw/oracle.stdout']):
        raise EvidenceError('invalid retained oracle outcome')
    for index, passage in enumerate(reply['passages']):
        if passage['span'] != {'offset': index * 256, 'length': min(288, 65536 - index * 256)}:
            raise EvidenceError('retained oracle source-span mismatch')
    bundle.json('preflight/full-isolation.stdout')
    return {'kind': 'consumed-unqualified-oracle', 'provenance': {'kind': 'unqualified-oracle',
                'source_digests': SOURCE_DIGESTS, 'asset_digests': ASSET_DIGESTS},
            'observations': [_observation('oracle/punctuation-numerical',
                {'request': 'inputs/request.stdin', 'stdout': 'raw/oracle.stdout', 'attempt': 'oracle-attempt.json'},
                reason='one180s attempt consumed/unqualified; production launcher/env/Go bytes, correct sampler and wrapper invariance not proved',
                vectors=256, elapsed_seconds=attempt['elapsed_seconds'])]}


def _read_bundle(bundle):
    markers = [name for name in ('capture-manifest.json', 'inventory/frozen-manifest.json',
               'inventory/input-manifest.json', 'inventory/experiment-manifest.json') if name in bundle.files]
    if len(markers) != 1:
        raise EvidenceError('missing or ambiguous bundle provenance manifest')
    handlers = {'capture-manifest.json': _capture_manifest, 'inventory/frozen-manifest.json': _characterization,
                'inventory/input-manifest.json': _arithmetic, 'inventory/experiment-manifest.json': _unqualified_oracle}
    try:
        return handlers[markers[0]](bundle)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as error:
        raise EvidenceError('malformed evidence schema: ' + type(error).__name__ + ': ' + str(error)) from error


def _outside_repository(output):
    output = Path(output).resolve()
    repository = Path(__file__).resolve().parents[1]
    worktrees = subprocess.run(['git', '-C', str(repository), 'worktree', 'list', '--porcelain', '-z'],
                              check=True, capture_output=True).stdout.split(b'\0')
    roots = [Path(os.fsdecode(line[len(b'worktree '):])).resolve()
             for line in worktrees if line.startswith(b'worktree ')]
    if any(output == root or root in output.parents for root in [repository, *roots]):
        raise EvidenceError('raw evidence output must stay outside the repository')
    if output.exists():
        raise EvidenceError('output already exists; preserve the existing evidence')
    return output


def import_bundles(specifications, output):
    """Import caller-pinned archives without upgrading integrity to qualification."""
    if not specifications:
        raise EvidenceError('at least one pinned bundle is required')
    output = _outside_repository(output)
    imported = []
    for path, seal in specifications:
        bundle = _Bundle(path, seal)
        parsed = _read_bundle(bundle)
        imported.append(dict(parsed, root=str(bundle.root), seal_sha256=seal))
    inventory = reference_inventory()
    observed = {cell: [] for cell in inventory['required_cells']}
    for bundle in imported:
        for observation in bundle['observations']:
            if observation['cell'] not in observed:
                raise EvidenceError('unknown reference inventory cell: ' + observation['cell'])
            observed[observation['cell']].append(observation)
    cells = [{'id': cell, 'status': 'unqualified' if observations else 'missing', 'observations': observations}
             for cell, observations in observed.items()]
    catalog = {'schema': 'cairn.semantic-reference-catalog/1', 'inventory': inventory,
               'phase': 'python-reference', 'phase_status': 'incomplete', 'model_acceptance': False,
               'bundles': imported, 'cells': cells,
               'missing_cells': [cell['id'] for cell in cells if cell['status'] == 'missing'],
               'unqualified_cells': [cell['id'] for cell in cells if cell['status'] == 'unqualified']}
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    (output / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return catalog


def _reviewed_isolation(module_path, review_path, expected_review_sha256):
    if module_path is None or review_path is None or expected_review_sha256 is None:
        raise EvidenceError('reviewed isolation module and pinned review receipt are required; no model was run')
    module_path, review_path = Path(module_path), Path(review_path)
    raw = review_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_review_sha256:
        raise EvidenceError('isolation review receipt digest mismatch')
    review = _strict_json(raw)
    if (not _review_receipt(review)
            or module_path.name != 'semantic_acceptance_isolation.py'
            or not module_path.is_file()):
        raise EvidenceError('missing or mismatched reviewed isolation receipt')
    module_raw = module_path.read_bytes()
    if hashlib.sha256(module_raw).hexdigest() != review['module_sha256']:
        raise EvidenceError('missing or mismatched reviewed isolation receipt')
    spec = importlib.util.spec_from_file_location('cairn_semantic_capture_isolation', module_path)
    module = importlib.util.module_from_spec(spec)
    # Execute the bytes actually hashed above, ignoring any neighboring .pyc.
    exec(compile(module_raw, str(module_path), 'exec'), module.__dict__)
    if not hasattr(module, 'SemanticIsolation'):
        raise EvidenceError('reviewed isolation module has no SemanticIsolation interface')
    return module, review


def _copy_reference(output, sources, assets, isolation_module, isolation_review):
    for directory in ('reference/scripts', 'assets'):
        (output / directory).mkdir(parents=True, exist_ok=True)
    for filename, digest in SOURCE_DIGESTS.items():
        source = sources / filename
        if _digest(source) != digest:
            raise EvidenceError('frozen source mismatch: ' + filename)
        shutil.copyfile(source, output / 'reference/scripts' / filename)
    for filename, digest in ASSET_DIGESTS.items():
        if _digest(assets / filename) != digest:
            raise EvidenceError('frozen asset mismatch: ' + filename)
        shutil.copyfile(assets / filename, output / 'assets' / filename)
    tooling = output / 'reference/tooling'
    tooling.mkdir(parents=True)
    script = Path(__file__).resolve()
    for source, filename in ((script, script.name), (script.with_name('semantic_fixture_cases.py'), 'semantic_fixture_cases.py'),
            (script.parents[1] / 'core/testdata/retrieval-quality.json', 'retrieval-quality.json'),
            (Path(isolation_module), 'semantic_acceptance_isolation.py'), (Path(isolation_review), 'isolation-review.json')):
        shutil.copyfile(source, tooling / filename)


def _seal_output(output, relatives=None):
    rows = []
    paths = sorted(output.rglob('*')) if relatives is None else [output / name for name in sorted(set(relatives))]
    sealed = []
    for path in paths:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise EvidenceError('symlink in new evidence: ' + str(path))
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise EvidenceError('non-regular file in new evidence: ' + str(path))
        if path != output / 'SHA256SUMS':
            relative = path.relative_to(output).as_posix()
            with _sealed_file(output, relative) as stream:
                rows.append(hashlib.file_digest(stream, 'sha256').hexdigest() + '  ' + relative + '\n')
            sealed.append(path)
    (output / 'SHA256SUMS').write_text(''.join(rows), encoding='utf-8')
    for path in [*sealed, output / 'SHA256SUMS']:
        path.chmod(0o444)


def _invocation_receipt(invocation):
    """The wrapper owns this declaration name and its pre-launch bytes."""
    if (not isinstance(invocation, dict) or type(invocation.get('id')) is not int or invocation['id'] <= 0
            or type(invocation.get('pid')) is not int or invocation['pid'] <= 1
            or not isinstance(invocation.get('start_ticks'), str) or not invocation['start_ticks'].isdigit()
            or not isinstance(invocation.get('argv'), list) or not invocation['argv']
            or not all(isinstance(value, str) for value in invocation['argv'])
            or not re.fullmatch('[0-9a-f]{64}', invocation.get('declaration_sha256', ''))):
        raise EvidenceError('missing exact wrapper invocation identity/declaration')
    declaration = {key: invocation[key] for key in ('id', 'role', 'mode', 'contract')}
    return f"isolation/command-{invocation['id']:04d}.json", declaration


def _validate_runtime_binding(invocation, worker, runtime, proof, host_namespace):
    """Join one command declaration to its actual model child's two observations."""
    contract = invocation['contract']
    if contract['profile'] != proof.get('profile') or invocation['role'] != 'python-reference':
        raise EvidenceError('reference invocation profile/role mismatch')
    _validate_declared_environment(contract, runtime.get('effective_environment'), runtime.get('cwd'))
    native = _native_images(runtime.get('mapped_libraries', {}))
    own_exe = runtime.get('executable', {})
    if (type(own_exe.get('inode')) is not int or own_exe['inode'] <= 0
            or not isinstance(own_exe.get('sha256'), str) or not re.fullmatch('[0-9a-f]{64}', own_exe['sha256'])
            or type(worker.get('pid')) is not int or worker['pid'] <= 1 or worker['pid'] == invocation['pid']
            or worker.get('launcher_pid') != invocation['pid']
            or worker.get('launcher_start_ticks') != invocation['start_ticks']
            or worker.get('invocation_id') != invocation['id']
            or worker.get('role') != invocation['role'] or worker.get('mode') != invocation['mode']
            or not runtime.get('process_start_ticks')
            or worker.get('process_start_ticks') != runtime['process_start_ticks']
            or not runtime.get('pid_namespace') or worker.get('pid_namespace') != runtime['pid_namespace']
            or worker.get('executable_inode') != own_exe['inode']
            or worker.get('executable_sha256') != own_exe['sha256']
            or not runtime.get('network_namespace') or runtime['network_namespace'] == host_namespace
            or worker.get('network_namespace') != runtime['network_namespace']
            or worker.get('environment_matches_contract') is not True
            or worker.get('exec_environment') not in (contract['environment'], contract['cpython_environment'])
            or any(not any(lib.get('path') == path and lib.get('inode') == info['inode']
                          and lib.get('sha256') == info['sha256'] and lib.get('device') == info['device']
                          for lib in worker.get('libraries', [])) for path, info in native.values())):
        raise EvidenceError('runtime observation does not identify this invocation\'s actual model child')
    return [{'path': path, 'sha256': info['sha256']} for path, info in native.values()]


def _cleanup_evidence(output, invocations=()):
    """Consume the shared wrapper's named backing identity and final receipt."""
    def read(relative):
        with _sealed_file(output, relative) as stream:
            value = _strict_json(stream.read(1024 * 1024 + 1))
        if not isinstance(value, dict):
            raise EvidenceError('cleanup receipt must be an object')
        return value
    home = read('isolation/disposable-home.json')
    final = read('isolation/cleanup.json')
    proof = read('isolation/isolation-proof.json')
    with _sealed_file(output, 'isolation/isolation-proof.json') as stream:
        proof_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    identity, backing = home.get('initial_identity'), home.get('backing')
    if (not isinstance(backing, str) or not Path(backing).is_absolute()
            or home.get('profile') != proof.get('profile') or home.get('payload_inspected') is not False
            or not isinstance(identity, dict) or set(identity) != {'inode', 'device', 'owner', 'mode'}
            or not all(type(value) is int for value in identity.values())
            or identity['inode'] <= 0 or identity['device'] < 0
            or identity['owner'] != os.getuid() or identity['mode'] != 0o700):
        raise EvidenceError('missing declared backing identity')
    try:
        current = Path(backing).lstat()
    except FileNotFoundError:
        pass
    else:
        same = (current.st_ino, current.st_dev) == (identity['inode'], identity['device'])
        raise EvidenceError('declared backing still present' if same else 'declared backing identity changed')
    if (final.get('backing_removed') is not True or final.get('all_observed_processes_exited') is not True
            or final.get('errors') != [] or final.get('isolation_proof_sha256') != proof_digest
            or not isinstance(final.get('invocations'), list)
            or any(invocation not in final['invocations'] for invocation in invocations)):
        raise EvidenceError('shared wrapper did not prove joined cleanup')
    result = {'backing': backing, 'initial_identity': identity, 'backing_absent_observed': True,
            'isolation_proof_sha256': proof_digest,
            'home_receipt': 'isolation/disposable-home.json', 'final_receipt': 'isolation/cleanup.json'}
    if proof.get('profile') == 'derived-W':
        before = proof.get('sealed_oracle')
        if (not isinstance(before, dict) or before.get('files') != 31 or not before.get('metadata')
                or not re.fullmatch('[0-9a-f]{64}', before.get('sha256', ''))
                or final.get('sealed_oracle_after') != before):
            raise EvidenceError('derived-W cleanup did not preserve the exact sealed-profile metadata')
        result['sealed_oracle_unchanged'] = True
    return result


def _capture_file_inventory(output, cases):
    # Explicit public artifacts only. Never walk a failed wrapper's private backing.
    paths = ['capture-manifest.json', 'failure.json', 'cleanup-failure.json']
    paths += [name + '.bin' for name in REVIEWER_RAW_BASELINE]
    paths += ['reference/scripts/' + name for name in SOURCE_DIGESTS]
    paths += ['assets/' + name for name in ASSET_DIGESTS]
    paths += ['reference/tooling/' + name for name in ('capture_semantic_fixtures.py', 'semantic_fixture_cases.py',
              'retrieval-quality.json', 'semantic_acceptance_isolation.py', 'isolation-review.json')]
    paths += ['isolation/' + name for name in ('isolation-proof.json', 'runtime-observations.json',
              'disposable-home.json', 'cleanup.json', 'read-only-probe')]
    # Only the wrapper's documented flat declaration namespace, never its backing.
    paths += [path.relative_to(output).as_posix() for path in (output / 'isolation').glob('command-*.json')
              if re.fullmatch(r'command-[0-9]{4,}\.json', path.name)]
    paths += ['failure.stdout', 'failure.stderr']
    for case in cases:
        paths += ['isolation/raw/' + case['id'] + '/' + name for name in (
            'request.stdin', 'wire.stdout', 'wire.stderr', 'trace.json', 'trace-arrays.npz', 'runtime.json',
            'collector-command.json', 'invocation.json', 'runtime-binding.json',
            'ids_i64le.bin', 'lengths_i64le.bin', 'cls_f32le.bin')]
    return paths


def _exception_record(error):
    return {'type': type(error).__name__, 'message': str(error),
            'traceback': ''.join(traceback.format_exception(type(error), error, error.__traceback__))}


@contextlib.contextmanager
def _collector_isolation(module, *, profile, read_only_paths, output_dir,
                         sealed_root=None, task_root=None, name=None):
    """Own only pre-entry allocation; an entered shared context owns all cleanup."""
    if profile == 'default':
        if any(value is not None for value in (sealed_root, task_root, name)):
            raise EvidenceError('default collector cannot consume sealed-profile backing inputs')
        with module.SemanticIsolation(read_only_paths=read_only_paths, output_dir=output_dir) as isolation:
            yield isolation
        return
    if profile != 'derived-W' or any(value is None for value in (sealed_root, task_root, name)):
        raise EvidenceError('derived-W collector requires explicit sealed root, task root and fresh name')
    task_root = Path(task_root).absolute()
    owner = Path(pwd.getpwuid(os.getuid()).pw_dir)
    task_base = owner / '.cache/cairn-hybrid-execution-1249/semantic-S-B1'
    if task_base not in task_root.parents:
        raise EvidenceError('B1 collector task root must be a dedicated semantic-S-B1 child')
    # The one published factory validates the profile, roots and fresh basename
    # before mkdir. Never create a directory or normalize an alias on its behalf.
    backing = module.DisposableBacking.create(sealed_root=sealed_root, task_root=task_root, name=name)
    entered = False
    try:
        manager = module.SemanticIsolation.sealed_oracle(sealed_root=sealed_root, backing=backing,
            task_scratch_roots=[task_root], read_only_paths=read_only_paths, output_dir=output_dir)
        with manager as isolation:
            entered = True
            yield isolation
    except BaseException as error:
        if not entered:
            try:
                # verify checks the root identity before looking up the child.
                # A context that already removed its child reports FileNotFound.
                try:
                    backing.verify(empty=True)
                except FileNotFoundError:
                    pass
                else:
                    backing.remove()
            except BaseException as cleanup_error:
                evidence = dict(error.evidence) if isinstance(error, module.IsolationError) else {}
                evidence.update(consumer_setup_failure=_exception_record(error),
                                consumer_allocation_cleanup_failure=_exception_record(cleanup_error))
                raise module.IsolationError('collector setup failed and owned empty-child release refused',
                                            code='cleanup', evidence=evidence) from error
        raise


def _capture_failure(error, isolation_error, output, case_output):
    record = _exception_record(error)
    if isolation_error is None or not isinstance(error, isolation_error):
        return record
    record['code'] = error.code
    try:
        evidence = dict(error.evidence)
        for name, limit in (('stdout', 4 * 1024 * 1024), ('stderr', 65536)):
            if name not in evidence:
                continue
            raw = evidence[name]
            if not isinstance(raw, bytes) or len(raw) > limit:
                raise EvidenceError('wrapper failure ' + name + ' violates the bounded bytes contract')
            path = case_output / ('wire.' + name) if case_output else output / ('failure.' + name)
            path.write_bytes(raw)
            evidence[name] = {'artifact': path.relative_to(output).as_posix(), 'bytes': len(raw),
                              'sha256': hashlib.sha256(raw).hexdigest()}
        # Other canonical fields, including invocation and both cleanup errors,
        # stay structured. Invalid API evidence is itself an explicit failure.
        record['evidence'] = _strict_json(json.dumps(evidence, allow_nan=False))
    except (TypeError, ValueError, OSError) as retention_error:
        record['evidence_retention_failure'] = _exception_record(retention_error)
    return record


@contextlib.contextmanager
def _capture_context(manager, manifest, record_failure):
    with manager as isolation:
        try:
            yield isolation
        except BaseException as error:
            manifest['primary_failure'] = record_failure(error)
            raise


def _validate_declared_environment(contract, environment, actual_cwd):
    if (not isinstance(contract, dict) or set(contract) != {
            'schema', 'profile', 'cwd', 'environment', 'cpython_environment', 'runtime_additions'}
            or contract.get('schema') != 'cairn.semantic-command-environment/1'
            or contract.get('profile') not in ('default', 'derived-W')):
        raise EvidenceError('missing exact per-command environment contract')
    declared, cpython, additions = (contract[name] for name in (
        'environment', 'cpython_environment', 'runtime_additions'))
    cwd = contract['cwd']
    if (not isinstance(declared, dict) or not declared or not isinstance(cpython, dict)
            or not isinstance(additions, dict) or not isinstance(cwd, str) or not cwd.startswith('/')
            or '\0' in cwd or declared.get('PWD') != cwd
            or not all(isinstance(key, str) and key and '=' not in key and isinstance(value, str)
                       and '\0' not in key + value for key, value in declared.items())):
        raise EvidenceError('invalid declared exec environment/cwd')
    expected = dict(declared)
    if not any(declared.get(key) for key in ('LANG', 'LC_ALL', 'LC_CTYPE')):
        addition = additions.get('LC_CTYPE')
        if (set(additions) != {'LC_CTYPE'} or not isinstance(addition, dict)
                or set(addition) != {'value', 'reason'} or addition['value'] != 'C.UTF-8'
                or not isinstance(addition['reason'], str) or not addition['reason']):
            raise EvidenceError('missing exact justified CPython locale addition')
        expected['LC_CTYPE'] = 'C.UTF-8'
    elif additions:
        raise EvidenceError('undeclared CPython runtime additions')
    if cpython != expected or environment != cpython or actual_cwd != cwd:
        raise EvidenceError('collector environment/cwd differs from its exact CPython declaration')


def _isolation_guard(proof, contract, parent_namespace, *, profile='default'):
    """Refuse before any model import; a caller flag alone is not confinement."""
    namespace = os.readlink('/proc/self/ns/net')
    if namespace == parent_namespace:
        raise EvidenceError('collector requires a distinct, preflighted network namespace')
    if any(name != 'lo' for _, name in socket.if_nameindex()):
        raise EvidenceError('collector namespace has an external network interface')
    with socket.socket() as connection:
        connection.settimeout(0.2)
        try:
            connection.connect(('192.0.2.1', 9))
        except OSError as error:
            if error.errno not in (errno.ENETUNREACH, errno.EHOSTUNREACH):
                raise EvidenceError('collector network isolation probe failed') from error
        else:
            raise EvidenceError('collector namespace has an external network route')
    if profile not in ('default', 'derived-W') or proof.get('profile') != profile:
        raise EvidenceError('collector requires its explicitly selected profile; ordinary capture remains default only')
    if contract.get('profile') != proof['profile']:
        raise EvidenceError('collector command profile differs from isolation proof')
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    if ((profile == 'default' and 'HOME' in os.environ)
            or (profile == 'derived-W' and contract['environment'].get('HOME') != proof.get('home'))
            or home.as_posix() != proof.get('home')
            or os.path.expanduser('~') != home.as_posix() or
            (home / '.semantic-isolation-probe').stat().st_ino != proof.get('home_probe_inode')):
        raise EvidenceError('collector OS home lookup is not the preflighted disposable home')
    _validate_declared_environment(contract, dict(os.environ), os.getcwd())
    return dict(proof, collector_network_namespace=namespace,
                collector_getpwuid_home=str(home), collector_expanduser_home=os.path.expanduser('~'),
                collector_cwd=os.getcwd(), collector_environment=dict(os.environ))


def _native_images(mapped):
    result = {}
    for marker, digest in NATIVE_DIGESTS.items():
        matches = [(path, info) for path, info in mapped.items() if marker in path]
        if (len(matches) != 1 or matches[0][1].get('sha256') != digest
                or type(matches[0][1].get('inode')) is not int or matches[0][1]['inode'] <= 0
                or not isinstance(matches[0][1].get('device'), str)
                or not re.fullmatch('[0-9a-fA-F]+:[0-9a-fA-F]+', matches[0][1]['device'])):
            raise EvidenceError('missing/changed actual native image: ' + marker)
        result[marker] = matches[0]
    return result


def _mapped_runtime(scorer):
    """Observe this Python image after inference, never the bwrap supervisor."""
    import importlib.metadata
    import onnxruntime as ort
    np = scorer.np
    mapped = {}
    for line in Path('/proc/self/maps').read_text().splitlines():
        fields = line.split(None, 5)
        if len(fields) != 6 or not fields[5].startswith('/') or '.so' not in fields[5]:
            continue
        filename = fields[5]
        if filename in mapped:
            continue
        with open(filename, 'rb') as stream:
            identity = os.fstat(stream.fileno())
            device = tuple(int(part, 16) for part in fields[3].split(':'))
            if (identity.st_ino != int(fields[4]) or
                    device != (os.major(identity.st_dev), os.minor(identity.st_dev))):
                raise EvidenceError('actual loaded library inode/device mismatch: ' + filename)
            mapped[filename] = {'inode': identity.st_ino, 'device': fields[3],
                'sha256': hashlib.file_digest(stream, 'sha256').hexdigest(), 'bytes': identity.st_size}
    _native_images(mapped)
    blas_paths = [path for path in mapped if '/numpy.libs/' in path and 'openblas' in path]
    if len(blas_paths) != 1:
        raise EvidenceError('expected exactly one observed NumPy OpenBLAS image')
    library = ctypes.CDLL(blas_paths[0], mode=os.RTLD_NOLOAD | os.RTLD_LOCAL)
    getters = {}
    for name, return_type in (
            ('scipy_openblas_get_config64_', ctypes.c_char_p),
            ('scipy_openblas_get_corename64_', ctypes.c_char_p),
            ('scipy_openblas_get_num_threads64_', ctypes.c_int),
            ('scipy_openblas_get_num_procs64_', ctypes.c_int),
            ('scipy_openblas_get_parallel64_', ctypes.c_int)):
        function = getattr(library, name)
        function.restype, function.argtypes = return_type, []
        value = function()
        getters[name] = value.decode('ascii') if isinstance(value, bytes) else value
    features = np._core._multiarray_umath.__cpu_features__
    if platform.machine() != 'x86_64' or not isinstance(features, dict) or not features.get('SSE2'):
        raise EvidenceError('unobserved CPU/OS capability class; no shared default')
    libc = ctypes.CDLL(None)
    libc.getauxval.argtypes, libc.getauxval.restype = [ctypes.c_ulong], ctypes.c_ulong
    # NumPy's runtime detector includes CPUID plus OS vector-state checks.
    # Keep its enabled vector features and Linux capability words, not a CPU name.
    os_features = ['AT_HWCAP=' + hex(libc.getauxval(16)), 'AT_HWCAP2=' + hex(libc.getauxval(26))]
    os_features += ['numpy-os-enabled:' + key for key, enabled in features.items()
                    if enabled and (key.startswith('AVX') or key.startswith('X86_V'))]
    facts = dict(architecture=platform.machine(), byte_order=sys.byteorder, pointer_bits=struct.calcsize('P') * 8,
        effective_isa=[key for key, enabled in features.items() if enabled], os_vector_state=os_features,
        blas_core=getters['scipy_openblas_get_corename64_'], blas_config=getters['scipy_openblas_get_config64_'])
    session = scorer.model.model.model
    options = session.get_session_options()
    settings = {name: str(getattr(options, name)) for name in ('intra_op_num_threads', 'inter_op_num_threads',
        'execution_mode', 'graph_optimization_level', 'use_deterministic_compute')}
    packages = {name: importlib.metadata.version(name) for name in ('fastembed', 'onnxruntime', 'numpy', 'tokenizers')}
    if (sys.version_info[:3] != (3, 11, 15)
            or packages != {'fastembed': '0.8.0', 'onnxruntime': '1.29.0', 'numpy': '2.4.6', 'tokenizers': '0.23.2'}):
        raise EvidenceError('prepared package versions differ from the frozen reference')
    if (session.get_providers() != ['CPUExecutionProvider'] or settings['intra_op_num_threads'] != '2'
            or settings['inter_op_num_threads'] != '2'):
        raise EvidenceError('reference provider/session settings changed')
    with open('/proc/self/exe', 'rb') as stream:
        executable = {'path': os.readlink('/proc/self/exe'), 'inode': os.fstat(stream.fileno()).st_ino,
                      'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}
    native_sources = {}
    for name, module in sorted(sys.modules.copy().items()):
        filename = getattr(module, '__file__', None)
        if filename and name.split('.')[0] in ('fastembed', 'numpy', 'onnxruntime', 'tokenizers'):
            path = Path(filename)
            if path.is_file() and path.suffix in ('.py', '.so'):
                native_sources[name] = {'path': str(path), 'sha256': _digest(path)}
    config = io.StringIO()
    with contextlib.redirect_stdout(config):
        np.show_config()
    usage = resource.getrusage(resource.RUSAGE_SELF)
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return {'schema': 'cairn.semantic-python-runtime/1', 'pid': os.getpid(), 'executable': executable,
        'process_start_ticks': Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19],
        'pid_namespace': os.readlink('/proc/self/ns/pid'),
        'network_namespace': os.readlink('/proc/self/ns/net'),
        'passwd_home': str(home), 'expanduser_home': os.path.expanduser('~'),
        'home_probe_inode': (home / '.semantic-isolation-probe').stat().st_ino,
        'resource_usage': {'max_rss_kib': usage.ru_maxrss, 'user_seconds': usage.ru_utime, 'system_seconds': usage.ru_stime},
        'python': sys.version, 'compiler': platform.python_compiler(), 'abi': sys.implementation.cache_tag,
        'packages': packages, 'effective_environment': dict(os.environ), 'cwd': os.getcwd(),
        'cpu_kernel_class': cpu_kernel_class(facts),
        'cpu_capability_method': 'NumPy runtime CPUID/OS vector-state detector and Linux getauxval',
        'numpy_cpu_features': features, 'numpy_config': config.getvalue(), 'openblas_getters': getters,
        'mapped_libraries': mapped, 'imported_code': native_sources, 'ort_version': ort.__version__,
        'ort_build': ort.get_build_info(), 'providers': session.get_providers(), 'session': settings,
        'os_threads': len(list(Path('/proc/self/task').iterdir())),
        'cpu_affinity': sorted(os.sched_getaffinity(0)), 'batch_size': 1,
        'unknowns': ['No instruction-level per-call microkernel trace.', 'Not run through the actual Go supervisor.']}


def _load_frozen_sources(sources):
    """Execute only the .py bytes verified here; Python must not consult pyc."""
    raw_sources = {}
    for filename, digest in SOURCE_DIGESTS.items():
        raw = (sources / filename).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise EvidenceError('frozen source mismatch: ' + filename)
        raw_sources[filename] = raw
    previous = {name: sys.modules.get(name) for name in ('semantic_rank', 'embedding_worker')}
    loaded = {}
    try:
        for filename, raw in raw_sources.items():
            name = filename[:-3]
            spec = importlib.util.spec_from_file_location(name, sources / filename)
            module = importlib.util.module_from_spec(spec)
            module.__cairn_source_bytes__ = raw
            sys.modules[name] = module
            exec(compile(raw, str(sources / filename), 'exec'), module.__dict__)
            loaded[name] = module
    except BaseException:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        raise
    return loaded['semantic_rank'], loaded['embedding_worker']


def _collect_reference(args):
    """Instrument frozen call frames without editing or replacing the oracle."""
    _capture_admission()
    # main has already checked the actual namespace, network and passwd home.
    for filename, digest in SOURCE_DIGESTS.items():
        if _digest(args.sources / filename) != digest:
            raise EvidenceError('frozen source mismatch: ' + filename)
    for filename, digest in ASSET_DIGESTS.items():
        if _digest(args.assets / filename) != digest:
            raise EvidenceError('frozen asset mismatch: ' + filename)
    cases = ranking_cases() if args.mode == 'rank' else embedding_cases()
    selected = next((case for case in cases if case['id'] == args.case_id), None)
    if selected is None:
        raise EvidenceError('collector input is outside the committed positive inventory')
    request = _strict_json(args.request.read_bytes())
    expected = ({key: selected[key] for key in ('query', 'notes')} if args.mode == 'rank' else
                {key: selected[key] for key in ('id', 'operation', 'text')})
    if not _same_json(request, expected):
        raise EvidenceError('collector request differs from the committed fixture')
    import numpy as np
    import fastembed.common.utils as fastembed_utils
    semantic_rank, embedding_worker = _load_frozen_sources(args.sources)
    if (Path(semantic_rank.__file__).resolve() != (args.sources / 'semantic_rank.py').resolve() or
            Path(embedding_worker.__file__).resolve() != (args.sources / 'embedding_worker.py').resolve()):
        raise EvidenceError('oracle import shadowed by another source')
    embeddings = embedding_worker.Embeddings(args.assets) if args.mode == 'embed' else None
    scorer = embeddings.scorer if embeddings else semantic_rank.Scorer(args.assets)
    result, metadata, arrays, raw_rows = _trace_reference(
        scorer=scorer, semantic_rank=semantic_rank, embedding_worker=embedding_worker,
        normalize=fastembed_utils.normalize, normalize_source=Path(fastembed_utils.__file__).read_bytes(),
        request=request, mode=args.mode, case_id=args.case_id, embeddings=embeddings)
    for name, raw in raw_rows.items():
        (args.output / (name + '.bin')).write_bytes(raw)
    np.savez(args.output / 'trace-arrays.npz', **arrays)
    (args.output / 'trace.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
    (args.output / 'runtime.json').write_text(json.dumps(_mapped_runtime(scorer), indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False))


def _trace_reference(*, scorer, semantic_rank, embedding_worker, normalize, normalize_source,
                     request, mode, case_id, embeddings=None):
    """Observe one ordinary frozen score call; backend execution is injectable in tests."""
    np = scorer.np
    arrays, stages, pending_norms = {}, [], {}
    raw_rows = {}
    metadata = {'schema': 'cairn.semantic-operation-trace/1', 'case_id': case_id, 'mode': mode,
                'scope': 'ordinary-default-reference', 'conditional_b1_proof': False,
                'arrays': {}, 'inferences': [], 'row_order': [], 'replay': {}, 'logical_occurrences': 0}

    def store(name, value):
        if value.dtype.str not in ('<f4', '<i8') or not np.isfinite(value).all():
            raise EvidenceError('unexpected tensor dtype or nonfinite tensor: ' + name)
        metadata['arrays'][name] = dict(dtype=value.dtype.str, shape=list(value.shape), strides=list(value.strides),
            c_contiguous=bool(value.flags.c_contiguous), f_contiguous=bool(value.flags.f_contiguous),
            address_mod_64=value.ctypes.data % 64, bytes_sha256=hashlib.sha256(value.tobytes(order='C')).hexdigest())
        arrays[name] = value.copy()

    score_code = semantic_rank.Scorer.score.__code__
    normalize_code = normalize.__code__
    model = scorer.model.model
    onnx_code = model.onnx_embed.__func__.__code__
    line_events = {}
    for node in ast.walk(ast.parse(semantic_rank.__cairn_source_bytes__)):
        if isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name) and node.target.id == 'vectors':
            line_events['divide'] = node.lineno
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'similarities' for t in node.targets):
            line_events['matvec'] = node.lineno
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'scores' for t in node.targets):
            line_events['maxima'] = node.lineno
    before_clip = next(node.lineno for node in ast.walk(ast.parse(normalize_source))
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == 'maximum')
    if set(line_events) != {'divide', 'matvec', 'maxima'}:
        raise EvidenceError('frozen score trace locations changed')

    def trace(frame, event, argument):
        if event == 'call':
            return trace if frame.f_code in (score_code, normalize_code, onnx_code) else None
        if event == 'return' and argument is None:
            return trace  # An exception is unwinding; preserve the original error.
        local = frame.f_locals
        if frame.f_code == onnx_code and event == 'return':
            inputs = local['onnx_input']
            if set(inputs) != {'input_ids', 'attention_mask', 'token_type_ids'}:
                raise EvidenceError('missing actual int64 ONNX input tensor')
            index = len(metadata['inferences'])
            names = {key: f'inference_{index}_{key}' for key in inputs}
            for key, value in inputs.items():
                if value.dtype != np.int64 or value.ndim != 2 or value.shape[0] != 1:
                    raise EvidenceError('unexpected actual ONNX tensor shape/dtype')
                store(names[key], value)
            if not (np.all(inputs['attention_mask'] == 1) and np.all(inputs['token_type_ids'] == 0)):
                raise EvidenceError('reference mask/token types differ from reviewer baseline')
            hidden = argument.model_output
            if hidden.ndim != 3 or hidden.shape != (1, inputs['input_ids'].shape[1], 384):
                raise EvidenceError('unexpected actual last_hidden_state shape')
            cls_name = f'inference_{index}_cls'
            store(cls_name, hidden[:, 0])
            metadata['inferences'].append({'index': index, 'inputs': names, 'raw_cls': cls_name,
                'hidden_shape': list(hidden.shape), 'session': 'runtime.json#/session'})
        elif frame.f_code == normalize_code:
            if event == 'line' and frame.f_lineno == before_clip:
                pending_norms[id(frame)] = local['norm'].copy()
            if event == 'return':
                index = len(stages)
                stage = {}
                for key, value in (('pooled', local['input_array']), ('norm_unclipped', pending_norms.pop(id(frame))),
                                   ('norm', local['norm']), ('normalized', argument)):
                    stage[key] = f'first_{index}_{key}'
                    store(stage[key], value)
                stage.update(p=local['p'], dim=local['dim'], eps=local['eps'])
                stages.append(stage)
        elif frame.f_code == score_code:
            if event == 'line' and frame.f_lineno == line_events['divide']:
                for name, value in (('embedded_matrix', local['embedded']), ('stack_before_second_norm', local['vectors']),
                                    ('second_norms', local['norms'])):
                    store(name, value)
            elif event == 'line' and frame.f_lineno == line_events['matvec']:
                store('stack_after_second_norm', local['vectors'])
                metadata['matvec_operands'] = {
                    label: dict(dtype=value.dtype.str, shape=list(value.shape), strides=list(value.strides),
                                c_contiguous=bool(value.flags.c_contiguous), address_mod_64=value.ctypes.data % 64)
                    for label, value in (('matrix', local['vectors'][1:]), ('query', local['vectors'][0]))}
            elif event == 'line' and frame.f_lineno == line_events['maxima']:
                store('similarities', local['similarities'])
            elif event == 'return':
                metadata['owners'], metadata['passage_keys'], metadata['missing'] = local['owners'], local['keys'], local['missing']
                metadata['inference_texts'] = [local['question']] + local['passages']
                metadata['row_order'] = [{'row': 0, 'inference': 0, 'role': 'prefixed_query'}] + [
                    dict(row=i + 1, inference=local['missing'][key], note_index=owner,
                         record_id=request['notes'][owner]['record_id'], passage_sha256=key)
                    for i, (owner, key) in enumerate(zip(local['owners'], local['keys']))]
                metadata['note_maxima_f64'] = [{'value': value, 'hex': value.hex()} for value in local['scores']]
        return trace

    started = time.monotonic()
    previous_trace = sys.gettrace()
    sys.settrace(trace)
    try:
        if embeddings:
            result = embeddings.respond(request)
        else:
            semantic_rank.validate_request(request)
            result = scorer.score(request)
    finally:
        sys.settrace(previous_trace)
    if not stages or len(stages) != len(metadata['inferences']):
        raise EvidenceError('missing actual inference/normalization stages')
    metadata['first_stage'] = stages
    for inference, stage in zip(metadata['inferences'], stages):
        pooled = arrays[stage['pooled']]
        unclipped = np.linalg.norm(pooled, ord=stage['p'], axis=stage['dim'], keepdims=True)
        norms = np.maximum(unclipped, stage['eps'])
        for label, observed, replayed in (
                ('raw-cls', arrays[inference['raw_cls']], pooled),
                ('first-norm', arrays[stage['norm_unclipped']], unclipped),
                ('first-clip', arrays[stage['norm']], norms),
                ('first-divide', arrays[stage['normalized']], pooled / norms)):
            key = str(inference['index']) + '/' + label
            metadata['replay'][key] = observed.dtype == replayed.dtype and observed.tobytes() == replayed.tobytes()
    if embeddings:
        _embedding_reply(request, result)
        metadata['row_order'] = [dict(row=i, inference=i, role=request['operation'])
                                 for i in range(len(metadata['inferences']))]
        if request['operation'] == 'query':
            prefix = request['text'].encode()[:result['query_projection']['prefix_bytes']].decode()
            metadata['inference_texts'] = [semantic_rank.PREFIX + prefix]
        else:
            metadata['inference_texts'] = [text for text, _ in embedding_worker.source_passages(scorer.tokenizer, request['text'])]
        expected_vectors = ([result['vector']] if request['operation'] == 'query' else [p['vector'] for p in result['passages']])
        if len(expected_vectors) != len(stages):
            raise EvidenceError('embedding output/inference count mismatch')
        metadata['replay']['output-vectors'] = all(
            np.asarray(vector, dtype='<f4').tobytes() == arrays[stage['normalized']].tobytes()
            for vector, stage in zip(expected_vectors, stages))
    else:
        _rank_reply(request, result)
        normalized = np.concatenate([arrays[stage['normalized']] for stage in stages], axis=0)
        mapping = [row['inference'] for row in metadata['row_order']]
        stacked = np.stack([normalized[index] for index in mapping])
        norms = np.linalg.norm(stacked, axis=1, keepdims=True)
        divided = stacked / norms
        similarities = divided[1:] @ divided[0]
        for label, value in (('embedded_matrix', normalized), ('stack_before_second_norm', stacked),
                             ('second_norms', norms), ('stack_after_second_norm', divided), ('similarities', similarities)):
            metadata['replay'][label] = arrays[label].tobytes() == value.tobytes() and arrays[label].dtype == value.dtype
        metadata['similarities_unrounded'] = [float(value) for value in arrays['similarities']]
        metadata['similarity_bits_u32_hex'] = [f'{value:08x}' for value in arrays['similarities'].view(np.uint32)]
        # Logical occurrence order expands real repeated-input reuse; it is not
        # a claim that the model executed a duplicate inference.
        for name, raw in (
                ('ids_i64le', b''.join(arrays[metadata['inferences'][index]['inputs']['input_ids']].astype('<i8', copy=False).tobytes() for index in mapping)),
                ('lengths_i64le', b''.join(struct.pack('<q', arrays[metadata['inferences'][index]['inputs']['input_ids']].shape[1]) for index in mapping)),
                ('cls_f32le', b''.join(arrays[metadata['inferences'][index]['raw_cls']].astype('<f4', copy=False).tobytes() for index in mapping))):
            raw_rows[name] = raw
    if not all(metadata['replay'].values()):
        raise EvidenceError('float32 operation trace is not bit-exact on replay')
    metadata['logical_occurrences'] = len(metadata['row_order'])
    metadata['unique_inferences'] = len(metadata['inferences'])
    metadata['observed_model_executions'] = len(metadata['inferences'])
    metadata['mapped_duplicate_rows'] = len(metadata['row_order']) - len(metadata['inferences'])
    metadata['elapsed_scoring_seconds'] = time.monotonic() - started
    metadata['line_events'] = line_events
    return result, metadata, arrays, raw_rows


def _capture_admission():
    # No caller boolean/review pointer can substitute for that missing proof.
    raise EvidenceError('actual capture unavailable: accepted pretoken gate and B1 proof vehicle pending')


def validate_pretoken_digests(observed):
    """Input-only identity check, never a permission or numerical qualification."""
    if (not isinstance(observed, dict) or type(observed.get('text_count')) is not int or observed['text_count'] != 294
            or type(observed.get('token_count')) is not int or observed['token_count'] != 33395
            or any(observed.get(name) != REVIEWER_RAW_BASELINE[name]
                for name in ('ids_i64le', 'lengths_i64le'))):
        raise EvidenceError('pre-inference input-ID/length/count mismatch; no session may be built')


def capture_reference(*, output, phase, python, sources, assets, isolation_module, isolation_review,
                      isolation_review_sha256=None, source_commit=None):
    """Run only committed positive inputs through S-B2's reviewed boundary."""
    if isolation_module is None or isolation_review is None or isolation_review_sha256 is None:
        raise EvidenceError('reviewed isolation module and pinned review receipt are required; no model was run')
    _capture_admission()
    module, review = _reviewed_isolation(isolation_module, isolation_review, isolation_review_sha256)
    if phase not in ('rank', 'embed'):
        raise EvidenceError('capture phase must be rank or embed; consumed punctuation is import-only')
    output = _outside_repository(output)
    python, sources, assets = Path(python).absolute(), Path(sources).absolute(), Path(assets).absolute()
    venv = python.parent.parent
    if not (venv / 'pyvenv.cfg').is_file():
        raise EvidenceError('a pinned prepared Python venv is required')
    for filename, digest in SOURCE_DIGESTS.items():
        if _digest(sources / filename) != digest:
            raise EvidenceError('frozen source mismatch: ' + filename)
    for filename, digest in ASSET_DIGESTS.items():
        if _digest(assets / filename) != digest:
            raise EvidenceError('frozen asset mismatch: ' + filename)
    script = Path(__file__).resolve()
    fixture_module = script.with_name('semantic_fixture_cases.py')
    workload = script.parents[1] / 'core/testdata/retrieval-quality.json'
    runtime_paths = [venv, python.resolve().parent.parent]
    for line in (venv / 'pyvenv.cfg').read_text().splitlines():
        if line.startswith('home = '):
            runtime_paths.append(Path(line.split(' = ', 1)[1]).parent)
    read_only = [script, fixture_module, workload, sources, assets, *runtime_paths]
    if not isinstance(source_commit, str) or not re.fullmatch('[0-9a-f]{40}', source_commit):
        raise EvidenceError('the committed capture-tool source revision is required')
    for path in (script, fixture_module, workload):
        committed = subprocess.run(['git', '-C', str(script.parents[1]), 'show',
            source_commit + ':' + path.relative_to(script.parents[1]).as_posix()],
            capture_output=True, check=True).stdout
        if hashlib.sha256(committed).hexdigest() != _digest(path):
            raise EvidenceError('capture tool/input differs from the specified commit')
    cases = ranking_cases() if phase == 'rank' else embedding_cases()
    provenance = {'kind': 'python-reference', 'wrapper_review': review,
                    'capture_source_sha256': _digest(script), 'fixture_source_sha256': _digest(fixture_module),
                    'source_commit': source_commit, 'host_network_namespace': os.readlink('/proc/self/ns/net'),
                    'sources': SOURCE_DIGESTS, 'assets': ASSET_DIGESTS,
                    'request_encoding': 'capture compact UTF-8 JSON; not claimed Go-generated',
                    'reviewer_raw_baseline': REVIEWER_RAW_BASELINE}
    def prepare():
        _copy_reference(output, sources, assets, isolation_module, isolation_review)
        if (_digest(output / 'reference/tooling/semantic_acceptance_isolation.py') != review['module_sha256']
                or _digest(output / 'reference/tooling/isolation-review.json') != isolation_review_sha256):
            raise EvidenceError('reviewed isolation inputs changed while copying; no model was run')
    return _run_reference_capture(module=module, output=output, phase=phase, cases=cases, python=python,
        sources=sources, assets=assets, read_only=read_only, provenance=provenance, prepare=prepare)


def _run_reference_capture(*, module, output, phase, cases, python, sources, assets, read_only,
                           provenance, prepare=None):
    """Orchestration below admission; model-free tests inject only a fake wrapper."""
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    script = Path(__file__).resolve()
    manifest = {'schema': 'cairn.semantic-reference-capture/1', 'selected_phase': phase,
                'provenance': dict(provenance, capture_scope='ordinary-default-reference', conditional_b1_proof=False),
                'observations': [], 'failure': None}
    isolation_output = output / 'isolation'
    raw_gate = {name: bytearray() for name in REVIEWER_RAW_BASELINE}
    isolation_error = None
    case_output = None
    invocations = []
    def record_failure(error):
        return _capture_failure(error, isolation_error, output, case_output)
    try:
        if prepare is not None:
            prepare()
        isolation_error = module.IsolationError
        if not isinstance(isolation_error, type) or not issubclass(isolation_error, Exception):
            raise EvidenceError('shared wrapper has no canonical IsolationError class')
        with _capture_context(_collector_isolation(module, profile='default',
                              read_only_paths=read_only, output_dir=isolation_output),
                              manifest, record_failure) as isolation:
            if isolation.proof.get('profile') != 'default':
                raise EvidenceError('ordinary capture cannot consume a derived-W construction or claim B1 proof')
            manifest['provenance']['isolation_proof'] = isolation.proof
            for case in cases:
                mode = 'rank' if phase == 'rank' else 'embed'
                case_output = isolation_output / 'raw' / case['id']
                case_output.mkdir(parents=True)
                request = {'query': case['query'], 'notes': case['notes']} if mode == 'rank' else {
                    key: case[key] for key in ('id', 'operation', 'text')}
                if request.get('operation') == 'document' and request.get('text') == '!' * 65536:
                    raise EvidenceError('punctuation180s attempt is consumed; no recapture')
                request_raw = json.dumps(request, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
                if mode == 'embed':
                    request_raw += b'\n'
                (case_output / 'request.stdin').write_bytes(request_raw)
                contract = isolation.command_contract()  # The same no-override environment used by run().
                _validate_declared_environment(contract, contract['cpython_environment'], contract['cwd'])
                if contract['profile'] != 'default':
                    raise EvidenceError('ordinary collector command requires the default profile')
                contract_path = case_output / 'collector-command.json'
                contract_path.write_text(json.dumps(contract, indent=2) + '\n')
                command = [str(python), '-B', str(script), '_collect', '--mode', mode,
                    '--request', str(case_output / 'request.stdin'), '--case-id', case['id'],
                    '--sources', str(sources), '--assets', str(assets), '--output', str(case_output),
                    '--isolation-proof', str(isolation_output / 'isolation-proof.json'),
                    '--command-contract', str(contract_path),
                    '--parent-net-namespace', os.readlink('/proc/self/ns/net')]
                timeout = 90 if mode == 'embed' and request['operation'] == 'document' else 25
                started = time.monotonic()
                try:
                    completed = isolation.run(command, timeout=timeout, stdout_limit=4 * 1024 * 1024,
                                              role='python-reference', mode=mode)
                except isolation_error as error:
                    failure_code = error.code
                    if error.code == 'cleanup':
                        failure_code = error.evidence.get('failure_code', 'cleanup')
                    manifest['failed_attempt'] = dict(case_id=case['id'], command=command, timeout_seconds=timeout,
                        elapsed_seconds=time.monotonic() - started, status=failure_code,
                        wrapper_code=error.code)
                    raise
                (case_output / 'wire.stdout').write_bytes(completed.stdout)
                (case_output / 'wire.stderr').write_bytes(completed.stderr)
                invocation = completed.invocation
                declaration_path, declaration = _invocation_receipt(invocation)
                with _sealed_file(output, declaration_path) as stream:
                    declaration_raw = stream.read(1024 * 1024 + 1)
                if (invocation['contract'] != contract or invocation['mode'] != mode
                        or invocation['role'] != 'python-reference'
                        or invocation['argv'][-len(command):] != command
                        or hashlib.sha256(declaration_raw).hexdigest() != invocation['declaration_sha256']
                        or _strict_json(declaration_raw) != declaration):
                    raise EvidenceError('returned invocation differs from the collector command/declaration')
                invocations.append(invocation)
                (case_output / 'invocation.json').write_text(json.dumps(invocation, indent=2) + '\n')
                cell = mode + '/' + case['id']
                observation = {'cell': cell, 'status': 'observed' if completed.returncode == 0 else 'failed',
                    'command': command, 'timeout_seconds': timeout, 'elapsed_seconds': time.monotonic() - started,
                    'exit_code': completed.returncode, 'invocation': invocation,
                    'artifacts': {key: (case_output / filename).relative_to(output).as_posix() for key, filename in (
                        ('request', 'request.stdin'), ('stdout', 'wire.stdout'), ('stderr', 'wire.stderr'),
                        ('trace', 'trace.json'), ('arrays', 'trace-arrays.npz'), ('runtime', 'runtime.json'),
                        ('command_contract', 'collector-command.json'), ('invocation', 'invocation.json'),
                        ('runtime_binding', 'runtime-binding.json'))}}
                manifest['observations'].append(observation)
                (output / 'capture-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
                if completed.returncode != 0:
                    raise EvidenceError('positive reference capture failed: ' + case['id'])
                trace = _strict_json((case_output / 'trace.json').read_bytes())
                if mode == 'rank':
                    for name in raw_gate:
                        raw_gate[name].extend((case_output / (name + '.bin')).read_bytes())
                observation['logical_occurrences'] = trace['logical_occurrences']
                observation['observed_model_executions'] = trace['observed_model_executions']
                observation['mapped_duplicate_rows'] = trace['mapped_duplicate_rows']
                runtime = _strict_json((case_output / 'runtime.json').read_bytes())
                pins = [{'path': path, 'sha256': info['sha256']}
                        for path, info in _native_images(runtime.get('mapped_libraries', {})).values()]
                worker = isolation.invocation_runtime(invocation, pins)
                _validate_runtime_binding(invocation, worker, runtime, isolation.proof,
                                          provenance['host_network_namespace'])
                (case_output / 'runtime-binding.json').write_text(json.dumps(worker, indent=2) + '\n')
            manifest['provenance']['actual_worker_observation'] = isolation.runtime_evidence(require_ort=True)
        if phase == 'rank':
            digests = {name: hashlib.sha256(raw).hexdigest() for name, raw in raw_gate.items()}
            for name, raw in raw_gate.items():
                (output / (name + '.bin')).write_bytes(raw)
            manifest['provenance']['expanded_row_digests'] = digests
            if digests != REVIEWER_RAW_BASELINE or len(raw_gate['cls_f32le']) != 294 * 384 * 4:
                raise EvidenceError('ordinary expanded-row digest mismatch; no conditional B1 proof')
        manifest['provenance']['execution_counts'] = {
            key: sum(entry[key] for entry in manifest['observations'])
            for key in ('logical_occurrences', 'observed_model_executions', 'mapped_duplicate_rows')}
        manifest['provenance']['wrapper_invariance_scope'] = (
            'ordinary default construction only; observed executions and mapped duplicate rows are separate; '
            'expanded digest equality establishes neither executed duplicate rows nor the semantic/4 B1 proof')
    except BaseException as error:
        manifest['failure'] = record_failure(error)
    try:
        manifest['provenance']['cleanup_evidence'] = _cleanup_evidence(output, invocations)
    except BaseException as error:
        cleanup = _exception_record(error)
        manifest['cleanup_failure'] = cleanup
        (output / 'cleanup-failure.json').write_text(json.dumps(cleanup, indent=2) + '\n')
        if manifest['failure'] is None:
            manifest['failure'] = cleanup
    if manifest['failure'] is not None:
        (output / 'failure.json').write_text(json.dumps(manifest['failure'], indent=2) + '\n')
    (output / 'capture-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    _seal_output(output, _capture_file_inventory(output, cases))
    if manifest['failure']:
        raise EvidenceError('capture retained as failed/incomplete: ' + manifest['failure']['type']
                            + ': ' + manifest['failure']['message'])
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('inputs', help='print synthetic input-only inventory; no inference')
    importer = commands.add_parser('import', help='validate pinned seals and import evidence outside git')
    importer.add_argument('--bundle', action='append', nargs=2, metavar=('DIRECTORY', 'SEAL_SHA256'), required=True)
    importer.add_argument('--output', type=Path, required=True)
    capturer = commands.add_parser('capture', help='unavailable until an accepted pretoken gate/proof vehicle exists')
    capturer.add_argument('--phase', choices=('rank', 'embed'), required=True)
    for option in ('output', 'python', 'sources', 'assets'):
        capturer.add_argument('--' + option, type=Path, required=True)
    capturer.add_argument('--isolation-module', type=Path, required=True)
    capturer.add_argument('--isolation-review', type=Path, required=True)
    capturer.add_argument('--isolation-review-sha256', required=True)
    capturer.add_argument('--source-commit', required=True)
    collector = commands.add_parser('_collect', help=argparse.SUPPRESS)
    collector.add_argument('--mode', choices=('rank', 'embed'), required=True)
    collector.add_argument('--case-id', required=True)
    collector.add_argument('--preflight-only', action='store_true', help='validate declared facts without model imports')
    for option in ('request', 'sources', 'assets', 'output', 'isolation-proof', 'command-contract'):
        collector.add_argument('--' + option, type=Path, required=True)
    collector.add_argument('--parent-net-namespace', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == '_collect':
            # Ordinary capture always selects default; the explicit derived-W
            # guard is exercised only by separate model-free profile callers.
            proof = _isolation_guard(_strict_json(args.isolation_proof.read_bytes()),
                                     _strict_json(args.command_contract.read_bytes()), args.parent_net_namespace)
            if args.preflight_only:
                print(json.dumps({'status': 'ordinary-preflight-observed', 'model_imported': False,
                                  'network_namespace': proof['collector_network_namespace']}))
                return 0
            _capture_admission()
            _collect_reference(args)
            return 0
        if args.command == 'inputs':
            print(json.dumps({'inventory': reference_inventory(), 'ranking': ranking_cases(), 'embedding': embedding_cases(),
                              'embedding_failures': embedding_failure_cases()}, ensure_ascii=False))
            return 0
        if args.command == 'capture':
            result = capture_reference(output=args.output, phase=args.phase, python=args.python, sources=args.sources,
                assets=args.assets, isolation_module=args.isolation_module, isolation_review=args.isolation_review,
                isolation_review_sha256=args.isolation_review_sha256, source_commit=args.source_commit)
            print(json.dumps({'status': 'reference-observations-captured', 'phase': result['selected_phase'],
                              'observations': len(result['observations']), 'model_acceptance': False}))
            return 0
        catalog = import_bundles(args.bundle, args.output)
        print(json.dumps({'schema': catalog['schema'], 'phase_status': catalog['phase_status'],
                          'missing_cells': len(catalog['missing_cells']), 'unqualified_cells': len(catalog['unqualified_cells']),
                          'catalog': str(args.output / 'catalog.json')}))
        return 3
    except (Exception, KeyboardInterrupt) as error:
        print(json.dumps({'status': 'refused', 'error': str(error)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
