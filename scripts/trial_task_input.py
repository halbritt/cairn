"""Explicit prospective inputs for the existing native task executor.

Inputs and setup/grader commands are trusted operator code, never agent output.
No provider, service, fixture overlay or historical trial is loaded here.
"""
import hashlib
import json
import os
from pathlib import Path
import re

from trial_task_arms import validate_arms, checked_file

SCHEMA = 'cairn.task-eval.input/1'
SLUG = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}')


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'), object_pairs_hook=_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def _relative(value):
    if not isinstance(value, str) or not value or Path(value).is_absolute() or '..' in Path(value).parts:
        raise ValueError('input paths must be relative and contained')
    return value


def manifest(root):
    root = Path(root).resolve(strict=True)
    files = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError('prospective input symlinks are unsupported')
        if path.is_file() and path != root/'FROZEN.json':
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_file() and not path.is_dir():
            raise ValueError('prospective input must contain regular files only')
    document, corpus = _read(root/'input.json'), _read(root/'corpus.json')
    if document.get('schema') != SCHEMA or set(document)-{'runtime'} != {'schema','baseline_commit','corpus_policy','native','arms','memory_arms','cases'}:
        raise ValueError('unsupported prospective input schema or fields')
    if not isinstance(document['corpus_policy'],dict) or not isinstance(document['corpus_policy'].get('description'),str) or not document['corpus_policy']['description']:
        raise ValueError('explicit corpus selection policy description required')
    if not re.fullmatch(r'[0-9a-f]{40}', document['baseline_commit']):
        raise ValueError('baseline_commit must be an explicit full revision')
    if set(corpus) != {'notes'} or not isinstance(corpus['notes'], list):
        raise ValueError('corpus must contain notes')
    validate_arms(document)
    if 'runtime' in document:
        from trial_task_runtime import validate_runtime
        validate_runtime(document['runtime'])
    native = document['native']
    if (not isinstance(native,dict) or set(native) != {'binary','model','effort'}
            or not isinstance(native['model'],str) or not native['model']
            or native['effort'] not in ('low','medium','high','xhigh','max')):
        raise ValueError('explicit native model/effort/binary required')
    checked_file(native['binary'])
    seen = set()
    for note in corpus['notes']:
        if (not isinstance(note, dict) or not {'id','body','kind'} <= set(note)
                or set(note)-{'id','body','kind','shareable','repo','supersede_with','provenance'}):
            raise ValueError('unsupported corpus note; do not flatten source contracts')
        if not isinstance(note['id'],str) or not SLUG.fullmatch(note['id']) or note['id'] in seen:
            raise ValueError('invalid or duplicate note id')
        seen.add(note['id'])
        if not isinstance(note['body'],str) or not note['body'] or not isinstance(note['kind'],str):
            raise ValueError('invalid note body/kind')
        if 'shareable' in note and type(note['shareable']) is not bool:
            raise ValueError('shareable must be a boolean')
    ids = set()
    cases = document['cases']
    if not isinstance(cases,list) or not cases:
        raise ValueError('prospective input needs cases')
    for case in cases:
        if not isinstance(case,dict) or not isinstance(case.get('id'),str) or not SLUG.fullmatch(case['id']) or case['id'] in ids:
            raise ValueError('invalid or duplicate case id')
        ids.add(case['id'])
        permitted = {'id','workspace','copy_to','cwd','workspace_commit','category','primary','provenance','expected',
                     'public_validation_commands','relevance_labels_complete','acceptable','must_not_deliver','over_applied','wordings','preflight','correct','mistake','review','stratum'}
        if set(case)-permitted:
            raise ValueError('unsupported prospective case fields; combine known corrections in exact task wording')
        if 'relevance_labels_complete' in case and type(case['relevance_labels_complete']) is not bool:
            raise ValueError('relevance_labels_complete must be an explicit boolean')
        for key in ('workspace','copy_to','cwd'):
            _relative(case[key])
        if not (root/'workspaces'/case['workspace']/'setup.sh').is_file():
            raise ValueError('workspace requires reviewed setup.sh')
        if (set(case['wordings']) != {'task'} or not isinstance(case['wordings']['task'],str)
                or not case['wordings']['task']):
            raise ValueError('prospective input requires one exact task wording')
        labels = case.get('public_validation_commands', {})
        if (not isinstance(labels, dict) or len(labels) > 32
                or any(not isinstance(label, str) or not re.fullmatch('[a-z][a-z0-9_-]{0,47}', label)
                       or not isinstance(command, str) or not command or len(command.encode()) > 4096
                       or '\0' in command or command not in case['wordings']['task']
                       for label, command in labels.items())
                or len(set(labels.values())) != len(labels)):
            raise ValueError('public validation labels require bounded exact commands in the common task')
        commands = case.get('preflight')
        if (not isinstance(commands,list) or not commands
                or any(not isinstance(cmd,list) or not cmd or any(not isinstance(s,str) or not s or '\0' in s for s in cmd) for cmd in commands)):
            raise ValueError('each prospective case needs explicit prerequisite argv checks')
    frozen = dict(schema=SCHEMA, labels_sha256=hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
                  files=files, cases_sha256=files['input.json'], corpus_sha256=files['corpus.json'],
                  baseline_commit=document['baseline_commit'], label_version=1)
    return frozen, document, corpus


def load_input(root, *, frozen=True):
    root = Path(root).resolve(strict=True)
    current, document, corpus = manifest(root)
    if frozen and _read(root/'FROZEN.json') != current:
        raise ValueError('prospective input changed after freezing')
    cases = [dict(case, _prospective_workspace=str(root/'workspaces'/case['workspace'])) for case in document['cases']]
    return dict(frozen=current, cases=cases, notes=corpus['notes'], root=root, corpus_policy=document['corpus_policy'], document=document)


def freeze_input(root):
    bundle = load_input(root, frozen=False)
    with (bundle['root']/'FROZEN.json').open('x', encoding='utf-8') as target:
        json.dump(bundle['frozen'],target,indent=2)
        target.write('\n')
        target.flush()
        os.fsync(target.fileno())
    return bundle
