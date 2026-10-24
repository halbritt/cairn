"""Disposable-store preflight for CAIRN-223. No model calls.

Run through scripts/trial-task-eval.sh -- after building a Cairn binary. The
wrapper owns the PostgreSQL cluster. This preflight exposes actual search and
hook delivery gaps before any provider admission.
"""

import argparse
import json
from pathlib import Path
import tempfile
import uuid

from check_source_fit_panel import PANEL, source_body, validate
from check_note_transport import operator
from trial_task_eval import TrialStore, TRIAL_REPO, load_hook


def run_preflight(binary, provider_callback=None):
    panel = json.loads(PANEL.read_text())
    manifest = validate(panel)
    with tempfile.TemporaryDirectory(prefix='source-fit-preflight-') as temporary:
        root = Path(temporary)
        store = TrialStore(root / 'store', binary, 'source_fit')
        try:
            notes = [dict(id=key, kind='lesson', body=source_body(record),
                          shareable=record['eligibility'] != 'local_only_inaccessible_to_hosted')
                     for key, record in panel['records'].items()
                     if key not in ('relay_current_extract', 'conflict_a', 'conflict_b')]
            store.seed(notes, workers=1)
            if 'relay_current_extract' in store.ids:
                raise RuntimeError('author split contaminated the current arm')
            store.start()
            hook = load_hook(Path(__file__).resolve().parents[1] / 'integrations/lifecycle/memory.py')
            report = []
            contexts = {}

            def inspect(case, arm, active_store, prompt):
                state = root / 'states' / case['id'] / arm
                state.mkdir(parents=True)
                config = dict(cairn=str(active_store.binary), socket=str(active_store.socket),
                              token_file=str(active_store.token_file), repo=TRIAL_REPO,
                              state_dir=str(state), context_bytes=9500,
                              harness='claude', recall_mode='agent_tools',
                              semantic_fallback=True)
                event = dict(hook_event_name='UserPromptSubmit', cwd=str(root),
                             session_id=str(uuid.uuid4()), turn_id=str(uuid.uuid4()),
                             prompt=prompt)
                try:
                    output = hook.handle(config, event)
                except hook.HookError as error:
                    contexts[(case['id'], arm)] = ''
                    return dict(case=case['id'], arm=arm, error=str(error),
                                qualification='hook refusal; no source delivered')
                text = output.get('hookSpecificOutput', {}).get('additionalContext', '')
                contexts[(case['id'], arm)] = text
                view = json.loads(text[text.index('{"selected":'):]) if text else {}
                indexed = [active_store.names.get(entry['record_id'], 'unknown')
                           for entry in view.get('index', [])]
                bodies = []
                for candidate in view.get('candidate_bodies', []):
                    response = candidate['response']
                    for selected in [response['selection'], *response.get('competing', [])]:
                        bodies.append(dict(source=active_store.names.get(selected['record']['record_id'], 'unknown'),
                                           extent=response.get('source_extent', 'whole'),
                                           body_bytes=len(selected['record'].get('body', '').encode()),
                                           span_bytes=len(response.get('span', {}).get('body', '').encode())))
                row = dict(case=case['id'], arm=arm, indexed=indexed, bodies=bodies,
                           selected=len(view.get('selected', [])),
                           inspection=view.get('candidate_inspection', {}),
                           context_bytes=len(text.encode()))
                if 'local_only' in indexed or any(body['source'] == 'local_only' for body in bodies):
                    raise RuntimeError('inaccessible local source reached hosted hook view')
                return row

            for case in panel['cases']:
                if case['id'] == 'required_conflict':
                    continue
                report.append(inspect(case, 'current', store, case['task']))
                if 'hints' in case['arms']:
                    report.append(inspect(case, 'hints', store,
                                          case['task'] + '\n' + case['hint_suffix']))
            split = TrialStore(root / 'split-store', binary, 'source_fit_split')
            try:
                split.seed([*notes, dict(id='relay_current_extract', kind='lesson',
                                        body=source_body(panel['records']['relay_current_extract']),
                                        shareable=True)], workers=1)
                split.supersede('relay_mixed', 'relay_current_extract')
                original = operator(str(split.binary), split.env, 'get',
                                    record_id=split.ids['relay_mixed'])
                if original.get('body') != source_body(panel['records']['relay_mixed']):
                    raise RuntimeError('author split changed the original source body')
                split.start()
                case = next(case for case in panel['cases'] if 'author_split' in case['arms'])
                report.append(inspect(case, 'author_split', split, case['task']))
                conflict = TrialStore(root / 'conflict-store', binary, 'source_fit_conflict')
                try:
                    root_grant = operator(str(conflict.binary), conflict.env, 'bootstrap',
                                          dict(request_id=str(uuid.uuid4()), reason='Synthetic source-fit root'))
                    if 'grant_id' not in root_grant:
                        raise RuntimeError('bootstrap did not return a grant ID')
                    for key in ('conflict_a', 'conflict_b'):
                        issued = operator(str(conflict.binary), conflict.env, 'issue', dict(
                            request_id=str(uuid.uuid4()), grant_id=root_grant['grant_id'], mandatory=True,
                            policy_key='source-fit-' + key, reason='Synthetic required conflict control',
                            draft=dict(kind='instruction', body=source_body(panel['records'][key]),
                                       scope=dict(repo=TRIAL_REPO, task_id='*', run_id='*'),
                                       claim_type='self', sensitivity='shareable')))
                        conflict.ids[key] = issued['record_id']
                        conflict.names[issued['record_id']] = key
                    operator(str(conflict.binary), conflict.env, 'dispute', dict(
                        request_id=str(uuid.uuid4()),
                        record_ids=[conflict.ids['conflict_a'], conflict.ids['conflict_b']],
                        reason='Synthetic unreconciled required thresholds'))
                    conflict.start()
                    case = next(case for case in panel['cases'] if case['id'] == 'required_conflict')
                    report.append(inspect(case, 'current', conflict, case['task']))
                    if provider_callback is not None:
                        provider_callback(panel, manifest, contexts,
                                          dict(current=store, author_split=split, conflict=conflict), report)
                    return dict(schema='cairn.source-fit-preflight/1',
                                panel_sha256=manifest['panel_sha256'],
                                qualification='disposable-store search/hook only; no model/task outcome',
                                cases=report)
                finally:
                    conflict.stop()
            finally:
                split.stop()
        finally:
            store.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    args = parser.parse_args()
    print(json.dumps(run_preflight(args.binary), sort_keys=True))


if __name__ == '__main__':
    main()
