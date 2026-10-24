"""Offline weekly bundle. Trusted quiescent source; no execution or admission.

Original weekly labels and controls are private evaluator data, never prospective
input/1. File checks bound/refuse source reads but do not establish concurrent
filesystem custody. Execution and workload pins remain caller declarations.
"""
import hashlib
import os
from pathlib import Path
import random
import re
import stat

import trial_task_eval as weekly
from trial_paired_request import (Refusal, canonical_sha, need, pin, plain_path,
                                  read_bytes, read_json, read_json_pin, shape)
from trial_task_input import _relative

REFERENCE_SCHEMA = 'cairn.weekly-input-reference/1'
EXECUTION_SCHEMA = 'cairn.weekly-execution/1'
CODEX_EXECUTION_SCHEMA = 'cairn.weekly-execution/2'
SCHEDULE_SCHEMA = 'cairn.weekly-schedule/1'
SOURCE_BYTES = 64 * 1024 * 1024
SOURCE_ENTRIES = 4096
DISTRACTORS = 1900
GENERATOR_SEED = 20260928
EMBEDDING = ('embedding_worker', 'embedding_config', 'embedding_model_identity')
MANIFEST_FIELDS = ('schema', 'labels_sha256', 'files', 'cases_sha256', 'corpus_sha256',
                   'baseline_commit', 'label_version')


def _revision(value):
    need(type(value) is str and re.fullmatch('[0-9a-f]{40}', value), 'weekly_revision')


def _source_tree(root):
    """Check all entries without reading future-version file bodies."""
    need(root.is_dir(), 'weekly_source_root')
    total = count = 0
    for directory, names, files in os.walk(root, followlinks=False,
                                          onerror=lambda _:need(False, 'weekly_source_scan')):
        for name in names + files:
            path = plain_path(str(Path(directory)/name))
            need(len(str(path).encode('utf8')) <= 4096, 'weekly_path_bound')
            mode = path.lstat()
            count += 1
            need(stat.S_ISDIR(mode.st_mode) or stat.S_ISREG(mode.st_mode), 'weekly_source_regular')
            if stat.S_ISREG(mode.st_mode):
                need(mode.st_size <= 8*1024*1024, 'weekly_source_file_bound')
                total += mode.st_size
            need(count <= SOURCE_ENTRIES and total <= SOURCE_BYTES, 'weekly_source_bounds')


def generated_distractors():
    """Pure fixed generator; independent from the frozen original corpus hash."""
    notes = [weekly.distractor(i, seed=GENERATOR_SEED) for i in range(DISTRACTORS)]
    return dict(count=DISTRACTORS, seed=GENERATOR_SEED,
                sha256=canonical_sha(dict(notes=notes)), notes=notes)


def _execution(reference):
    execution = read_json_pin(reference['execution'])
    shape(execution, 'schema weekly_source actor_api_base native arms memory_arms seeds wording workload distractors',
          'weekly_execution_shape') if execution.get('schema') == EXECUTION_SCHEMA else \
        shape(execution, 'schema weekly_source actor_api_base provider arms memory_arms seeds wording workload distractors',
          'weekly_execution_shape')
    need(execution['schema'] in (EXECUTION_SCHEMA,CODEX_EXECUTION_SCHEMA), 'weekly_execution_schema')
    need(canonical_sha(execution['weekly_source']) == canonical_sha(
         {k:reference[k] for k in ('root','label_version','frozen_sha256')}),
         'weekly_source_binding')
    _revision(execution['actor_api_base'])
    actor = execution['provider'] if execution['schema'] == CODEX_EXECUTION_SCHEMA else execution['native']
    if execution['schema'] == CODEX_EXECUTION_SCHEMA:
        shape(actor, 'harness binary model effort', 'weekly_provider_shape')
        need(actor['harness'] == 'codex', 'weekly_provider_unsupported')
    else:
        shape(actor, 'binary model effort', 'weekly_native_shape')
    pin(actor['binary'])
    need(type(actor['model']) is str and 0 < len(actor['model'].encode('utf8')) <= 256
         and '\0' not in actor['model'] and actor['effort'] in ('low','medium','high','xhigh','max'),
         'weekly_native_declaration')
    need(execution['arms'] == ['CURRENT','CAND'] and execution['wording'] == 'task', 'weekly_plan_policy')
    seeds = execution['seeds']
    need(type(seeds) is list and len(seeds) == 3 and all(type(s) is int and 0 <= s <= 2**31-1 for s in seeds)
         and len(set(seeds)) == 3, 'weekly_seeds')
    shape(execution['memory_arms'], 'CURRENT CAND', 'weekly_arms_shape')
    for name, arm in execution['memory_arms'].items():
        shape(arm, 'binary hook bridge api_revision recall_revision recall_mode semantic_fallback '
              'embedding_worker embedding_config embedding_model_identity', 'weekly_arm_shape')
        _revision(arm['api_revision'])
        need(type(arm['recall_revision']) is str and 0 < len(arm['recall_revision']) <= 256
             and '\0' not in arm['recall_revision'],
             'weekly_recall_revision')
        need(arm['recall_mode'] in ('ambient','agent_tools')
             and type(arm['semantic_fallback']) is bool, 'weekly_recall_declaration')
        for field in ('binary','hook','bridge',*EMBEDDING):
            pin(arm[field])
    for field in EMBEDDING:
        need(execution['memory_arms']['CURRENT'][field] == execution['memory_arms']['CAND'][field],
             'weekly_embedding_mismatch')
    # Legacy weekly storage uses the common embedding backend independently of
    # the hook's semantic_fallback switch. Prospective route admission rules do
    # not belong here; retain literal declarations for the later dispatcher.
    pin(execution['workload'])  # Semantic demand/capacity validation belongs to the companion.
    growth = execution['distractors']
    shape(growth, 'count seed generator_source generated_sha256', 'weekly_growth_shape')
    need(type(growth['count']) is int and growth['count'] == DISTRACTORS
         and type(growth['seed']) is int and growth['seed'] == GENERATOR_SEED, 'weekly_growth_policy')
    pin(growth['generator_source'])
    need(plain_path(growth['generator_source']['path']) == Path(weekly.__file__).resolve(),
         'weekly_generator_source')
    return execution


def actor(execution):
    """Return a typed provider declaration without flattening Codex into the Claude native shape."""
    return execution['provider'] if execution.get('schema')==CODEX_EXECUTION_SCHEMA else execution['native']


def _cases(cases, notes, root, version):
    need(type(cases) is list and len(cases) == 21 and all(type(c) is dict for c in cases), 'weekly_case_count')
    need(type(notes) is list and all(type(n) is dict for n in notes), 'weekly_corpus_shape')
    for note in notes:
        need(all(type(note.get(k)) is str and note[k] for k in ('id','body','kind')), 'weekly_note_shape')
        if 'shareable' in note:
            need(type(note['shareable']) is bool, 'weekly_note_shape')
    workspaces = {}
    for case in cases:
        need(type(case.get('id')) is str and re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}',case['id']),
             'weekly_case_id')
        need('_prospective_workspace' not in case, 'weekly_workspace_override')
        shape(case['wordings'], 'task paraphrase direct', 'weekly_wordings')
        need(all(type(text) is str and text and '\0' not in text for text in case['wordings'].values()),
             'weekly_wordings')
        for key in ('workspace','copy_to','cwd'):
            _relative(case[key])
            need('\0' not in case[key], 'weekly_workspace_path')
        name = case['workspace']
        if name.startswith('revisions/'):
            parts = Path(name).parts
            need(len(parts) >= 3 and re.fullmatch('v[1-9][0-9]*',parts[1])
                 and int(parts[1][1:]) <= version
                 and (root/'revisions'/(parts[1]+'.json')).is_file(), 'weekly_workspace_version')
        workspace = plain_path(str(weekly.workspace_dir(case, root=root)))
        need(workspace.is_relative_to(root) and workspace.is_dir()
             and (workspace/'setup.sh').is_file(), 'weekly_workspace_source')
        workspaces[case['id']] = str(workspace)
    need(not weekly.validate(cases, notes, root=root), 'weekly_labels_invalid')
    need(not {n['id'] for n in notes} & {f'D-{i:05d}' for i in range(DISTRACTORS)},
         'weekly_distractor_collision')
    return workspaces


def _load(reference):
    shape(reference, 'schema root label_version frozen_sha256 execution', 'weekly_reference_shape')
    need(reference['schema'] == REFERENCE_SCHEMA, 'weekly_reference_schema')
    version = reference['label_version']
    need(type(version) is int and 0 < version < 2**31, 'weekly_label_version')
    root = plain_path(reference['root'])
    need(str(root) == reference['root'] and len(reference['root'].encode('utf8')) <= 4096, 'weekly_root_path')
    _source_tree(root)
    frozen_path = weekly.frozen_path(version, root=root)
    frozen_bytes = read_bytes(frozen_path)
    need(hashlib.sha256(frozen_bytes).hexdigest() == reference['frozen_sha256'], 'weekly_frozen_pin')
    frozen = read_json(frozen_path)
    need(type(frozen) is dict and set(frozen) in (set(MANIFEST_FIELDS), set(MANIFEST_FIELDS)|{'frozen_at'}),
         'weekly_manifest_shape')
    if 'frozen_at' in frozen:
        need(type(frozen['frozen_at']) is str and len(frozen['frozen_at']) <= 256, 'weekly_frozen_metadata')
    # Strict-parse only the selected JSON sources before using the actual legacy readers.
    for path in (root/'cases.json', root/'corpus.json', *weekly.revision_files(version, root=root)):
        read_json(path)
    current = weekly.label_manifest(version, root=root)
    need(canonical_sha({k:frozen[k] for k in MANIFEST_FIELDS}) == canonical_sha(current),
         'weekly_manifest_identity')
    _revision(frozen['baseline_commit'])
    cases = weekly.load_cases(version, root=root)
    notes = weekly.load_corpus(version, root=root)
    workspaces = _cases(cases, notes, root, version)
    execution = _execution(reference)
    generated = generated_distractors()
    need(generated['sha256'] == execution['distractors']['generated_sha256'], 'weekly_generated_digest')
    runs = []
    for seed in execution['seeds']:
        for case in cases:
            order = list(execution['arms'])
            random.Random(f"{seed}:{case['id']}").shuffle(order)
            runs.extend(dict(case=case['id'], arm=arm, seed=seed, position=position)
                        for position,arm in enumerate(order))
    source = dict(root=str(root), label_version=version, manifest_sha256=reference['frozen_sha256'],
                  frozen_identity=current, resolved_cases_sha256=canonical_sha(cases),
                  resolved_corpus_sha256=canonical_sha(dict(notes=notes)))
    growth = {key:value for key,value in generated.items() if key != 'notes'}
    plan = dict(instrument='weekly-frozen-cases',
                cases=[case['id'] for case in cases], arms=execution['arms'], seeds=execution['seeds'],
                attempts=126, workload=execution['workload'])
    plan['sha256'] = canonical_sha(plan)
    schedule = dict(schema=SCHEDULE_SCHEMA, plan_sha256=plan['sha256'], source=source,
                    execution=dict(reference['execution']), actor_api_base=execution['actor_api_base'],
                    wording='task', distractors=growth, runs=runs)
    schedule['sha256'] = canonical_sha(schedule)
    return dict(schema='cairn.weekly-input-bundle/1', root=root, frozen=frozen, cases=cases, notes=notes,
                workspace_paths=workspaces, execution=execution, source=source, generated=growth,
                distractor_notes=generated['notes'], plan=plan, schedule=schedule, runtime_admitted=False,
                witness_valid=False, admission=False)


def load_weekly_input(reference):
    """Read a selected private weekly bundle; return no launch capability.

    Sources must be trusted and quiescent. Repeated manifest/reader reads are not
    an atomic snapshot, and file pins are declarations rather than provenance proof.
    """
    try:
        return _load(reference)
    except Refusal:
        raise
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise Refusal('weekly_source_or_declaration_invalid') from None
