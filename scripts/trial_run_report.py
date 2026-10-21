"""Dated per-run report and short Cairn note for a paired task-evaluation run.

Inputs are an existing `cairn.task-eval.agent/1` document and a
`cairn.grading-join-report/1` produced by `trial_grading_custody.join_results`.
Only allowlisted counts, identifiers and outcome tokens are read; answers,
traces, errors, memory deliveries, verdicts, reasons and grader identities
never enter the outputs. The report fails closed: any missing, failed,
unstarted or inconsistent evidence yields `status: "incomplete"`. Even complete
declared evidence keeps `custody: "unknown"` and never claims campaign success.
"""
import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import uuid
from pathlib import Path

from trial_paired_request import Refusal, canonical_sha, need

SCHEMA = 'cairn.task-eval-run-report/1'
ARMS = ('CURRENT', 'CAND')
OUTCOMES = ('correct', 'mistake', 'incomplete', 'undetermined', 'harness_error',
            'provider_error', 'not_regradable')
FAILED_OUTCOMES = ('harness_error', 'provider_error', 'not_regradable')
COMPARISONS = ('agreement', 'disagreement', 'ineligible', 'not_selected')
SELECTIONS = ('changed', 'unchanged', 'undetermined')
DISPOSITIONS = ('missing', 'declared', 'conflict')
ORIGINAL_ATTEMPTS = 126
ORIGINAL_PAIRS = 63
NOTE_LIMIT = 2048
# Fixed namespace so a report's note request id is reproducible across hosts.
NOTE_NAMESPACE = uuid.UUID('6d2b7c0e-5f0a-4f43-9a52-1b07c4e1a107')
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}')


def _token(value):
    need(type(value) is str and TOKEN.fullmatch(value) is not None, 'report_identifier')
    return value


def _seed(value):
    need(type(value) is int, 'report_identifier')
    return value


def _count(value, code):
    need(type(value) is int and value >= 0, code)
    return value


def _date(text):
    need(type(text) is str and re.fullmatch(r'\d{4}-\d{2}-\d{2}', text) is not None, 'report_date')
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        raise Refusal('report_date') from None
    return text


def _outcome(value):
    return value if value in OUTCOMES else 'other'


def _agent(agent):
    need(isinstance(agent, dict) and agent.get('schema') == 'cairn.task-eval.agent/1', 'report_agent_schema')
    admission = agent.get('admission')
    need(isinstance(admission, dict) and isinstance(admission.get('not_started'), list)
         and type(admission.get('state')) is str, 'report_agent_admission')
    planned = _count(admission.get('planned'), 'report_agent_admission')
    admitted = _count(admission.get('admitted'), 'report_agent_admission')
    stop = admission.get('stop')
    need(stop is None or isinstance(stop, dict), 'report_agent_admission')
    records = agent.get('records')
    need(isinstance(records, list), 'report_agent_records')
    rows = []
    for record in records:
        need(isinstance(record, dict), 'report_agent_records')
        trace = record.get('trace')
        failure = trace.get('admission_failure') if isinstance(trace, dict) else None
        need(failure is None or isinstance(failure, dict), 'report_agent_records')
        rows.append(dict(run_id=_token(record.get('run_id')), case=_token(record.get('case')),
                         seed=_seed(record.get('seed')), arm=_token(record.get('arm')),
                         outcome=_outcome(record.get('outcome')),
                         provider=None if failure is None else failure.get('reason')))
    measurement = agent.get('measurement_failures') or []
    need(isinstance(measurement, list), 'report_agent_records')
    return dict(state=admission['state'], planned=planned, admitted=admitted,
                not_started=len(admission['not_started']),
                stopped_reason=None if stop is None else stop.get('reason'),
                records=rows, measurement_failures=len(measurement))


def _join(join):
    need(isinstance(join, dict) and join.get('schema') == 'cairn.grading-join-report/1', 'report_join_schema')
    binding, aggregate, pairs = join.get('binding'), join.get('aggregate'), join.get('pairs')
    need(isinstance(binding, dict) and isinstance(aggregate, dict), 'report_join_shape')
    need(isinstance(pairs, list) and len(pairs) == ORIGINAL_PAIRS, 'report_join_pairs')
    rows, keys = [], set()
    for pair in pairs:
        need(isinstance(pair, dict) and isinstance(pair.get('attempts'), dict), 'report_join_pairs')
        key = _token(pair.get('case')), _seed(pair.get('seed'))
        need(key not in keys and pair.get('selection') in SELECTIONS
             and pair.get('declared_comparison') in COMPARISONS
             and pair.get('disposition_state', 'missing') in DISPOSITIONS, 'report_join_pairs')
        keys.add(key)
        attempts = {}
        for arm in ARMS:
            attempt = pair['attempts'].get(arm)
            need(isinstance(attempt, dict) and attempt.get('state') in ('missing', 'completed', 'failed'),
                 'report_join_pairs')
            attempts[arm] = None if attempt['state'] == 'missing' else _token(attempt.get('run_id'))
        rows.append(dict(case=key[0], seed=key[1], selection=pair['selection'],
                         comparison=pair['declared_comparison'],
                         disposition=pair.get('disposition_state', 'missing'), runs=attempts,
                         issues=sorted(i for i in pair.get('issues', []) if type(i) is str and TOKEN.fullmatch(i))))
    attempts = aggregate.get('attempts')
    coverage = aggregate.get('changed_pair_coverage')
    issues = aggregate.get('changed_pair_issues')
    need(isinstance(attempts, dict) and isinstance(coverage, dict) and isinstance(issues, dict), 'report_join_shape')
    return dict(
        campaign_id=_token(binding.get('campaign_id')), occurrence_id=_token(binding.get('occurrence_id')),
        request_id=_token(binding.get('request_id')), pairs=rows,
        missing=_count(attempts.get('missing'), 'report_join_shape'),
        failed=_count(attempts.get('failed'), 'report_join_shape'),
        changed=_count(coverage.get('denominator'), 'report_join_shape'),
        secondary=_count(coverage.get('secondary_present'), 'report_join_shape'),
        dual=_count(coverage.get('determinate_dual'), 'report_join_shape'),
        conflicts=sum(_count(issues.get(k, 0), 'report_join_shape')
                      for k in ('primary_conflict', 'secondary_conflict')),
        undetermined=aggregate.get('selection_completeness') != 'declared')


def report_digest(report):
    return canonical_sha({k: v for k, v in report.items() if k != 'report_sha256'})


def build_report(agent, join, run_date):
    """Return a deterministic body-free report; performs no I/O."""
    run_date = _date(run_date)
    a, j = _agent(agent), _join(join)
    by_key, mismatch = {}, False
    for row in a['records']:
        key = (row['case'], row['seed'], row['arm'])
        mismatch |= key in by_key or row['arm'] not in ARMS
        by_key[key] = row
    expected = {(p['case'], p['seed'], arm): p['runs'][arm] for p in j['pairs'] for arm in ARMS}
    mismatch |= not set(by_key) <= set(expected)
    for key, run_id in expected.items():
        row = by_key.get(key)
        mismatch |= (row['run_id'] if row else None) != run_id

    failed = [r for r in a['records'] if r['outcome'] in FAILED_OUTCOMES or r['provider']]
    capped = sum(r['provider'] == 'capacity_exhausted' for r in a['records'])
    auth = sum(r['provider'] == 'authentication_failed' for r in a['records'])
    provider_stop = a['state'] == 'stopped_provider_failure' and a['stopped_reason'] == 'capacity_exhausted'
    limit_losses = capped + (a['not_started'] if provider_stop else 0)

    pairs, pending = [], []
    for p in sorted(j['pairs'], key=lambda p: (p['case'], p['seed'])):
        outcome = {arm: by_key[p['case'], p['seed'], arm]['outcome'] if (p['case'], p['seed'], arm) in by_key
                   else 'not_run' for arm in ARMS}
        pairs.append(dict(case=p['case'], seed=p['seed'], current=outcome['CURRENT'], candidate=outcome['CAND'],
                          selection=p['selection'], grading=p['comparison']))
        if p['comparison'] == 'disagreement':
            pending.append(dict(case=p['case'], seed=p['seed'], disposition=p['disposition'],
                                resolution='pending_owner'))

    agreement_n = sum(p['comparison'] == 'agreement' for p in j['pairs'])
    agreement_d = agreement_n + len(pending)
    reasons = {
        'admission_not_complete': a['state'] != 'complete',
        'planned_not_original': a['planned'] != ORIGINAL_ATTEMPTS,
        'attempts_not_started': a['not_started'] > 0 or a['admitted'] < a['planned'],
        'attempts_unfinished': len(a['records']) < a['planned'],
        'attempt_failures': bool(failed),
        'provider_limit_losses': limit_losses > 0,
        'provider_auth_failures': auth > 0,
        'measurement_failures': a['measurement_failures'] > 0,
        'evidence_mismatch': mismatch,
        'grading_attempts_unavailable': j['missing'] > 0 or j['failed'] > 0,
        'selection_undetermined': j['undetermined'],
        'changed_pairs_not_dual_graded': j['dual'] < j['changed'],
        'grade_conflicts': j['conflicts'] > 0,
        'disagreements_pending_owner': bool(pending),
    }
    incomplete = sorted(k for k, v in reasons.items() if v)
    report = dict(
        schema=SCHEMA, run_date=run_date,
        status='incomplete' if incomplete else 'declared_complete', incomplete_reasons=incomplete,
        campaign=dict(request_id=j['request_id'], campaign_id=j['campaign_id'], occurrence_id=j['occurrence_id']),
        inputs=dict(agent_sha256=canonical_sha(agent), join_sha256=canonical_sha(join)),
        attempts=dict(planned=a['planned'], admitted=a['admitted'], finished=len(a['records']) - len(failed),
                      not_started=a['not_started'], failed=len(failed),
                      provider_limit_losses=limit_losses, provider_auth_failures=auth),
        pairs=pairs,
        grading=dict(changed_pairs=j['changed'], second_grader_reviewed=j['secondary'],
                     dual_determinate=j['dual'],
                     agreement=dict(numerator=agreement_n, denominator=agreement_d,
                                    rate=agreement_n / agreement_d if agreement_d else None)),
        disagreements_pending_owner=pending,
        # Never derived from inputs: no verified custody exists in this stage.
        custody='unknown', actual_campaign_success=False)
    report['report_sha256'] = report_digest(report)
    return report


def _rate(agreement):
    if agreement['rate'] is None:
        return f"n/a ({agreement['numerator']}/{agreement['denominator']})"
    return f"{agreement['rate'] * 100:.1f}% ({agreement['numerator']}/{agreement['denominator']})"


def render_markdown(report):
    att, grading, camp = report['attempts'], report['grading'], report['campaign']
    status = ('INCOMPLETE: ' + ', '.join(report['incomplete_reasons'])) if report['incomplete_reasons'] \
        else 'All planned attempts and changed-pair grades are declared present.'
    lines = [
        f"# Paired task-evaluation run report {report['run_date']}", '',
        f"Campaign `{camp['campaign_id']}`, occurrence `{camp['occurrence_id']}`, request `{camp['request_id']}`.", '',
        f"**Status:** {status}", '',
        f"Grading custody is `{report['custody']}`. This report does not establish actual campaign "
        'success, grader independence or blinding. It contains no transcripts, model output, '
        'case bodies, grader verdicts or oracle answers.', '',
        '## Attempts', '',
        '| Planned | Admitted | Finished | Failed | Not started | Provider-limit losses | Provider auth failures |',
        '| ---: | ---: | ---: | ---: | ---: | ---: | ---: |',
        f"| {att['planned']} | {att['admitted']} | {att['finished']} | {att['failed']} | {att['not_started']} "
        f"| {att['provider_limit_losses']} | {att['provider_auth_failures']} |", '',
        '## Second grader', '',
        f"- Changed pairs: {grading['changed_pairs']}",
        f"- Reviewed by the second grader: {grading['second_grader_reviewed']}",
        f"- Both grades determinate: {grading['dual_determinate']}",
        f"- Declared agreement rate: {_rate(grading['agreement'])}", '',
        '## Disagreements pending owner', '',
    ]
    if report['disagreements_pending_owner']:
        lines += ['| Case | Seed | Declared disposition | Resolution |', '| --- | ---: | --- | --- |']
        lines += [f"| {d['case']} | {d['seed']} | {d['disposition']} | {d['resolution']} |"
                  for d in report['disagreements_pending_owner']]
    else:
        lines.append('None.')
    lines += ['', '## Pair outcomes', '', '| Case | Seed | CURRENT | CAND | Selection | Grading |',
              '| --- | ---: | --- | --- | --- | --- |']
    lines += [f"| {p['case']} | {p['seed']} | {p['current']} | {p['candidate']} | {p['selection']} | {p['grading']} |"
              for p in report['pairs']]
    lines += ['', f"Report sha256 `{report['report_sha256']}`.", '']
    return '\n'.join(lines)


def note_draft(report):
    """Short shareable counts-only note; no case, seed or run identifiers."""
    att, grading, camp = report['attempts'], report['grading'], report['campaign']
    status = 'incomplete (' + ', '.join(report['incomplete_reasons']) + ')' if report['incomplete_reasons'] \
        else 'declared complete (custody unverified)'
    body = '\n'.join([
        f"Paired task-eval run report {report['run_date']} (CAIRN-107): campaign {camp['campaign_id']}, "
        f"occurrence {camp['occurrence_id']}.",
        f"Status: {status}.",
        f"Attempts: {att['finished']} finished of {att['planned']} planned; {att['not_started']} not started; "
        f"{att['failed']} failed; provider-limit losses {att['provider_limit_losses']}.",
        f"Second grader: {grading['second_grader_reviewed']} of {grading['changed_pairs']} changed pairs; "
        f"declared agreement {_rate(grading['agreement'])}.",
        f"Disagreements pending owner: {len(report['disagreements_pending_owner'])}.",
        f"Grading custody {report['custody']}; this note does not claim campaign success or task benefit.",
        f"Report sha256 {report['report_sha256']}.", ''])
    need(len(body.encode()) <= NOTE_LIMIT, 'note_size')
    request_id = str(uuid.uuid5(NOTE_NAMESPACE, f"{report['run_date']}:{report['report_sha256']}"))
    return dict(request_id=request_id, kind='note', shareable=True, body=body)


def publish_note(report, *, binary, token_file, repo, runner=subprocess.run):
    """Save the report's note through `cairn agent remember`; replays are idempotent."""
    need(isinstance(report, dict) and report.get('schema') == SCHEMA, 'report_schema')
    need(report.get('report_sha256') == report_digest(report), 'report_digest')
    need(all(type(v) is str and v for v in (binary, token_file, repo)), 'note_destination')
    draft = note_draft(report)
    argv = [binary, 'agent', '--token-file', token_file, 'remember', '--repo', repo,
            '--kind', draft['kind'], '--request-id', draft['request_id'], '--shareable', '--stdin']
    result = runner(argv, input=draft['body'], capture_output=True, text=True, timeout=30)
    try:
        response = json.loads(result.stdout)
    except (TypeError, ValueError):
        raise Refusal('note_response') from None
    need(isinstance(response, dict), 'note_response')
    need(response.get('status') != 'IDEMPOTENCY_CONFLICT', 'note_request_conflict')
    data = response.get('data')
    need(response.get('status') == 'OK' and isinstance(data, dict) and data.get('body') == draft['body'],
         'note_response')
    return dict(request_id=draft['request_id'], record_id=data.get('record_id'), version=data.get('version'))


def _load(path):
    def pairs(items):
        keys = [k for k, _ in items]
        need(len(keys) == len(set(keys)), 'report_duplicate_key')
        return dict(items)
    return json.loads(Path(path).read_bytes(), object_pairs_hook=pairs)


def _write(path, text):
    """Create once; an identical rerun is a no-op, different content refuses."""
    data = text.encode()
    try:
        with open(path, 'xb') as handle:
            handle.write(data)
    except FileExistsError:
        need(path.read_bytes() == data, 'report_exists')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    render = sub.add_parser('render', help='write the dated report and note draft')
    render.add_argument('--agent', required=True)
    render.add_argument('--join', required=True)
    render.add_argument('--date', required=True)
    render.add_argument('--out', required=True)
    publish = sub.add_parser('publish', help='save the note of a rendered report')
    publish.add_argument('--report', required=True)
    publish.add_argument('--token-file', required=True)
    publish.add_argument('--repo', required=True)
    publish.add_argument('--binary', default='cairn')
    args = parser.parse_args(argv)
    try:
        if args.command == 'render':
            report = build_report(_load(args.agent), _load(args.join), args.date)
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            stem = f"task-eval-run-{report['run_date']}"
            _write(out / f'{stem}.json', json.dumps(report, indent=2, sort_keys=True) + '\n')
            _write(out / f'{stem}.md', render_markdown(report))
            _write(out / f'{stem}.note.json', json.dumps(note_draft(report), indent=2, sort_keys=True) + '\n')
            print(json.dumps(dict(status=report['status'], report_sha256=report['report_sha256'])))
        else:
            print(json.dumps(publish_note(_load(args.report), binary=args.binary,
                                          token_file=args.token_file, repo=args.repo)))
    except Refusal as refusal:
        print(json.dumps(dict(refusal=str(refusal))))
        return 2
    except (OSError, ValueError):
        print(json.dumps(dict(refusal='report_input')))
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
