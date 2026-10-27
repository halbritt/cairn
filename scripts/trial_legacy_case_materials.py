"""Issue private case materials and channel contracts from frozen legacy v12.

The package is intended to be created by the weekly runner before actor
admission. It contains only each case's selected task wording, authored review
question, and a channel contract derived from the frozen evaluator's check
types. The check definitions, outcomes, note IDs, and memory material remain
inside the pinned evaluator source and are never copied into the package.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

import trial_blind_packet_draft as packet
import trial_paired_request as paired
import pinned_document as pinned
import trial_task_eval as task_eval

SCHEMA = 'cairn.legacy-v12-case-material-manifest/2'
EVALUATOR_CONTRACT_SCHEMA = 'cairn.legacy-v12-evaluator-observation-contract/1'
VERSION = 12
EXPECTED_CASES_SHA256 = '383f70fb16dc72ce7a02941eafb65747a007c47da08de27f3ed3dffeedbaf446'
EXPECTED_LABELS_SHA256 = '3e5518dfbd41c4d6b28a1476d7a3d96d454fea9e7946c9fb4e1ac2897ce0e709'
MAX_CASES = 21
MAX_FILES = 128
MAX_FILE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
ARTIFACT_NAME = 'pregrade-snapshot.json'
ARTIFACT_INVENTORY = [dict(name=ARTIFACT_NAME, kind='file', representation='bytes',
                           media_type='application/json')]


def _need(condition, code):
    paired.need(condition, code)


def _canonical(value):
    return packet.canonical_bytes(value)


def _checks(checks):
    for check in checks:
        yield check
        if check.get('type') in ('all', 'any'):
            yield from _checks(check.get('checks', []))


def selected_checks(case):
    checks = [*_checks(case.get('correct', [])), *_checks(case.get('mistake', []))]
    checks.extend(case.get('review_checks', {}).values())
    checks.extend(case.get('descriptive', {}).values())
    return checks


def shell_checks(case):
    """Private source-order shell checks; callers may project exits, never names."""
    return [check for check in selected_checks(case) if check.get('type') == 'shell']


def _channels(case):
    """Select evidence channels from evaluator access patterns, without exporting checks."""
    checks = selected_checks(case)
    kinds = {check.get('type') for check in checks}
    channels = ['task_statement', 'grading_instructions', 'final_answer']
    unavailable = []
    if kinds & {'file', 'exists', 'added', 'unchanged', 'shell', 'commit_files', 'staged'}:
        channels.append('artifact_bundle')
    if 'shell' in kinds:
        channels.append('evaluator_observation')
    if kinds & {'command', 'tool_output'}:
        if _action_trace_projection(case) is None:
            unavailable.append(dict(name='action_trace', reason='raw_tool_output_unavailable'))
        else:
            channels.append('action_trace')
    return channels, unavailable


def _action_trace_projection(case):
    """Only db-coverage has a frozen, exact allowlisted safe action projection."""
    kinds = {check.get('type') for check in selected_checks(case)}
    if case.get('id') == 'db-coverage' and kinds == {
            'all', 'tool_output', 'file', 'added', 'answer'}:
        return task_eval.DB_COVERAGE_ACTION_PROJECTION
    return None


def _evaluator_contract(evaluator_sha256, assets_sha256):
    rules = [
        'mistake if any mistake check is true',
        'otherwise undetermined if a correct or mistake check is undetermined',
        'otherwise correct if all correct checks are true',
        'otherwise incomplete',
        'a raised review flag changes any non-mistake result to needs_review',
    ]
    return dict(schema=EVALUATOR_CONTRACT_SCHEMA,
                evaluator_source_sha256=evaluator_sha256,
                selected_assets_sha256=assets_sha256,
                input_fields=['task_statement_sha256', 'final_answer_sha256', 'stream_sha256',
                              'baseline_sha256', 'pregrade_snapshot_sha256',
                              'selected_action_trace_sha256', 'selected_case_sha256'],
                execution=dict(boundary='in_process_grade', completed=True,
                               exit_observations='ordered shell-check process exit codes only',
                               aggregate_evaluator_exit='not_a_process'),
                precedence=dict(version='cairn.task-eval.grade-precedence/v12', rules=rules),
                redactions=['check names', 'check definitions', 'check verdicts', 'check logs',
                            'arm labels', 'memory records', 'memory deliveries', 'answers'])


def _check_source(root):
    root = paired.plain_path(str(root))
    _need(root.is_dir(), 'legacy_material_source_unavailable')
    try:
        frozen = task_eval.verify_frozen(VERSION, root=root)
        current = task_eval.label_manifest(VERSION, root=root)
        cases = task_eval.load_cases(VERSION, root=root)
        frozen_bytes = task_eval.frozen_path(VERSION, root=root).read_bytes()
        cases_bytes = (root / 'cases.json').read_bytes()
        evaluator = root.parent.parent / 'scripts' / 'trial_task_eval.py'
        evaluator_bytes = paired.read_bytes(evaluator)
    except (OSError, SystemExit, ValueError, TypeError, KeyError):
        raise paired.Refusal('legacy_material_source_unavailable') from None
    _need(frozen.get('label_version') == VERSION and frozen.get('cases_sha256') == EXPECTED_CASES_SHA256 and
          frozen.get('labels_sha256') == EXPECTED_LABELS_SHA256 and
          current.get('cases_sha256') == EXPECTED_CASES_SHA256 and
          current.get('labels_sha256') == EXPECTED_LABELS_SHA256 and
          len(cases) == MAX_CASES, 'legacy_material_source_mismatch')
    case_ids = [case.get('id') for case in cases]
    _need(all(type(case_id) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}', case_id)
              for case_id in case_ids) and len(set(case_ids)) == MAX_CASES,
          'legacy_material_case_inventory')
    return frozen, cases, dict(frozen=pinned.digest(frozen_bytes), cases=pinned.digest(cases_bytes),
                               evaluator=pinned.digest(evaluator_bytes), labels_sha256=current['labels_sha256'])


def case_source(case_id, *, root=None):
    """Return one verified frozen v12 case for private evaluator projection."""
    try:
        _, cases, _ = _check_source(Path(root) if root is not None else task_eval.TRIAL)
        matches = [case for case in cases if case.get('id') == case_id]
        _need(len(matches) == 1, 'legacy_material_case_unavailable')
        return matches[0]
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise paired.Refusal('legacy_material_case_unavailable') from None


def _write_package(root, files):
    root.mkdir(mode=0o700)
    for name, content in sorted(files.items()):
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        paired.write_selected_bytes(path, content)


def _existing(root, files):
    info = root.lstat()
    _need(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700,
          'legacy_material_output_policy')
    present = {path.relative_to(root).as_posix(): path for path in root.rglob('*')
               if path.is_file() or path.is_symlink()}
    _need(set(present) == set(files), 'legacy_material_output_conflict')
    for name, path in present.items():
        _need(not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) == 0o600 and
              paired.read_bytes(path) == files[name], 'legacy_material_output_conflict')


def issue(output, *, root=None):
    """Write an owner-only, content-addressed v12 package; never emits case material."""
    try:
        source_root = Path(root) if root is not None else task_eval.TRIAL
        frozen, cases, source = _check_source(source_root)
        output = paired.plain_path(str(output))
        _need(not output.exists(), 'legacy_material_output_conflict')
        files = {}
        rows = []
        total = 0
        for case in sorted(cases, key=lambda item: item['id']):
            case_id = case['id']
            prompt = (case.get('wordings') or {}).get('task')
            rubric = case.get('review')
            _need(type(prompt) is str and prompt.strip() and type(rubric) is str and rubric.strip(),
                  'legacy_material_case_unlabelled')
            prompt_bytes = prompt.encode('utf-8', errors='strict')
            instruction = ('Assess the supplied attempt against the case review question. Use only the '
                           'selected evidence channels. Do not infer what memory was available or which arm '
                           'produced the attempt. Do not reproduce this instruction as a result.\n\n'
                           'Review question: ' + rubric.strip() + '\n').encode('utf-8')
            _need(len(prompt_bytes) <= MAX_FILE_BYTES and len(instruction) <= MAX_FILE_BYTES,
                  'legacy_material_file_bound')
            channels, unavailable = _channels(case)
            contract = dict(schema='cairn.blind-case-contract/2', case_key=case_id,
                            case_class='legacy-v12', required_channels=channels,
                            artifact_inventory=(list(ARTIFACT_INVENTORY)
                                                if 'artifact_bundle' in channels else []))
            prefix = 'cases/' + case_id + '/'
            source_case = _canonical(case)
            docs = {
                prefix + 'contract.json': _canonical(contract),
                prefix + 'task.txt': prompt_bytes,
                prefix + 'grading.txt': instruction,
            }
            pins = {}
            for name, content in docs.items():
                total += len(content)
                _need(len(content) <= MAX_FILE_BYTES and total <= MAX_TOTAL_BYTES,
                      'legacy_material_total_bound')
                files[name] = content
                pins[name.rsplit('/', 1)[-1].split('.', 1)[0]] = dict(path=str(output/name), **pinned.digest(content))
            rows.append(dict(case=case_id, channels=contract['required_channels'],
                             unavailable_channels=unavailable,
                             action_trace_projection=_action_trace_projection(case),
                             contract=pins['contract'], task_statement=pins['task'],
                             grading_instructions=pins['grading'],
                             source_case_sha256=hashlib.sha256(source_case).hexdigest()))
        _need(len(rows) == MAX_CASES and len(files) <= MAX_FILES, 'legacy_material_inventory')
        manifest = dict(schema=SCHEMA, label_version=VERSION,
                        frozen_sha256=source['frozen']['sha256'], labels_sha256=frozen['labels_sha256'],
                        cases_sha256=frozen['cases_sha256'], evaluator_sha256=source['evaluator']['sha256'],
                        files_sha256=frozen['labels_sha256'],
                        evaluator_observation_contract=_evaluator_contract(
                            source['evaluator']['sha256'], frozen['labels_sha256']), cases=rows)
        manifest_bytes = _canonical(manifest)
        total += len(manifest_bytes)
        _need(total <= MAX_TOTAL_BYTES, 'legacy_material_total_bound')
        files['manifest.json'] = manifest_bytes
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _write_package(output, files)
        return dict(schema=SCHEMA, path=str(output/'manifest.json'), bytes=len(manifest_bytes),
                    sha256=hashlib.sha256(manifest_bytes).hexdigest(), cases=len(rows),
                    total_bytes=total, storage='private_owner_only')
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError,
            RecursionError):
        raise paired.Refusal('legacy_material_issue_invalid_or_unavailable') from None


def read_manifest(reference, *, root=None, evaluator_sha256=None):
    """Verify an issued manifest against the frozen v12 source and its private files."""
    try:
        _need(isinstance(reference, dict) and set(reference) == {'schema', 'path', 'bytes', 'sha256'} and
              reference['schema'] == SCHEMA, 'legacy_material_manifest_pin')
        content = paired.read_bytes(reference['path'])
        _need(pinned.digest(content) == {k: reference[k] for k in ('bytes', 'sha256')},
              'legacy_material_manifest_mismatch')
        manifest = paired.decode_json(content)
        _need(type(manifest) is dict and set(manifest) == {'schema', 'label_version', 'frozen_sha256',
              'labels_sha256', 'cases_sha256', 'evaluator_sha256', 'files_sha256',
              'evaluator_observation_contract', 'cases'},
              'legacy_material_manifest_shape')
        _need(manifest['schema'] == SCHEMA, 'legacy_material_manifest_shape')
        frozen, cases, source = _check_source(Path(root) if root is not None else task_eval.TRIAL)
        _need(manifest['label_version'] == VERSION and manifest['frozen_sha256'] == source['frozen']['sha256'] and
              manifest['labels_sha256'] == frozen['labels_sha256'] and
              manifest['cases_sha256'] == frozen['cases_sha256'] and
              type(manifest['evaluator_sha256']) is str and
              re.fullmatch('[0-9a-f]{64}', manifest['evaluator_sha256']) and
              (manifest['evaluator_sha256'] == (evaluator_sha256 or source['evaluator']['sha256'])) and
              manifest['files_sha256'] == frozen['labels_sha256'], 'legacy_material_manifest_source_mismatch')
        _need(manifest['evaluator_observation_contract'] == _evaluator_contract(
              manifest['evaluator_sha256'], frozen['labels_sha256']), 'legacy_material_evaluator_contract')
        _need(type(manifest['cases']) is list and len(manifest['cases']) == MAX_CASES,
              'legacy_material_manifest_inventory')
        ids = [case['id'] for case in cases]
        _need([row.get('case') for row in manifest['cases']] == sorted(ids),
              'legacy_material_manifest_inventory')
        by_id = {case['id']: case for case in cases}
        for row in manifest['cases']:
            _need(type(row) is dict and set(row) == {'case', 'channels', 'unavailable_channels',
                  'action_trace_projection', 'contract', 'task_statement',
                  'grading_instructions', 'source_case_sha256'}, 'legacy_material_manifest_case')
            case = by_id[row['case']]
            expected_channels, expected_unavailable = _channels(case)
            _need(row['channels'] == expected_channels and row['unavailable_channels'] == expected_unavailable and
                  row['action_trace_projection'] == _action_trace_projection(case) and
                  row['source_case_sha256'] == hashlib.sha256(_canonical(case)).hexdigest(),
                  'legacy_material_manifest_case_mismatch')
            for field in ('contract', 'task_statement', 'grading_instructions'):
                pin = row[field]
                _need(type(pin) is dict and set(pin) == {'path', 'bytes', 'sha256'} and
                      type(pin['bytes']) is int and 0 <= pin['bytes'] <= MAX_FILE_BYTES,
                      'legacy_material_manifest_pin')
                expected_name = {'contract': 'contract.json', 'task_statement': 'task.txt',
                                 'grading_instructions': 'grading.txt'}[field]
                _need(Path(pin['path']) == Path(reference['path']).parent / 'cases' / row['case'] / expected_name,
                      'legacy_material_manifest_path')
                material = paired.read_bytes(pin['path'])
                _need(pinned.digest(material) == {k: pin[k] for k in ('bytes', 'sha256')},
                      'legacy_material_manifest_pin_mismatch')
            contract = packet._json(paired.read_bytes(row['contract']['path']))
            task = paired.read_bytes(row['task_statement']['path'])
            grading = paired.read_bytes(row['grading_instructions']['path'])
            _need(contract == dict(schema='cairn.blind-case-contract/2', case_key=row['case'],
                  case_class='legacy-v12', required_channels=row['channels'],
                  artifact_inventory=(list(ARTIFACT_INVENTORY)
                                      if 'artifact_bundle' in row['channels'] else [])) and
                  task.decode('utf-8') == case['wordings']['task'] and
                  b'Review question: ' + case['review'].strip().encode('utf-8') + b'\n' in grading,
                  'legacy_material_manifest_content_mismatch')
        return manifest
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError,
            RecursionError):
        raise paired.Refusal('legacy_material_manifest_invalid_or_unavailable') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--root', help='pinned task-evaluation source root')
    parser.add_argument('--output', required=True, help='new private package directory')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(issue(args.output, root=args.root), sort_keys=True))
    except paired.Refusal as refusal:
        print(json.dumps(dict(status='refused', code=str(refusal))))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
