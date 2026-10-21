"""Deliver frozen blind grader plans over durable Cairn events; fail closed unless configured.

The transport is the existing event fabric: a shareable source note plus a direct
`request` event to each slot's registered session inbox. Returned grades are the
store-linked Class A results of those exact deliveries. A separately held release
authority must name the exact plan, packets, recipients and models; this module
never creates one. No model, provider or campaign is launched here, and
disagreements are only routed to the owner.
"""
import base64
import datetime as dt
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import trial_paired_request as paired
import trial_blind_packet_draft as blind
import trial_grading_custody as custody

C = blind.canonical_bytes
SLOTS = ('primary', 'secondary')
NOTE_LIMIT = 65536
REASON_LIMIT = 16384
CONFIG_SCHEMA = 'cairn.grader-delivery-config/1'
AUTHORITY_SCHEMA = 'cairn.grader-release-authority/1'
REQUEST_SCHEMA = 'cairn.grader-request/1'
RESPONSE_SCHEMA = 'cairn.grader-response/1'
DISPUTE_SCHEMA = 'cairn.grader-dispute/1'
ARM_TOKENS = ('"CURRENT"', '"CAND"')
UNAVAILABLE = 'grader_delivery_unavailable'
# Fixed namespace: request UUIDs derive from the plan, so a plan is delivered once.
NAMESPACE = uuid.UUID('6f1d7c62-3a4e-5b0c-9d8e-0c1a2b3c4d5e')
INSTRUCTIONS = (
    'Grade the two blind samples in `comparison` under the control in `control`. '
    'Reply only by completing this delivery with `cairn complete --stdin --shareable` and a JSON body '
    'matching `response`: echo request_id, pair_id, slot, packet_sha256 and binding_sha256; set state to '
    'determinate (one verdict per listed sample) or undetermined (verdicts null); give a short reason. '
    'Do not consult other graders, the arm mapping, or prior grades.')


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _text(value, code, limit=256):
    paired.need(type(value) is str and value.strip() != '' and len(value.encode()) <= limit and '\0' not in value, code)


def _uuid(value, code):
    try:
        paired.need(type(value) is str and str(uuid.UUID(value)) == value, code)
    except ValueError:
        raise paired.Refusal(code) from None


def _derived(*parts):
    return str(uuid.uuid5(NAMESPACE, '/'.join(parts)))


def _check_config(config):
    paired.shape(config, 'schema repo owner_destination publisher', 'grader_config_invalid')
    paired.need(config['schema'] == CONFIG_SCHEMA, 'grader_config_invalid')
    _text(config['repo'], 'grader_config_invalid'); _text(config['owner_destination'], 'grader_config_invalid')
    paired.shape(config['publisher'], 'agent_id execution_id', 'grader_config_invalid')
    for key in ('agent_id', 'execution_id'):
        _uuid(config['publisher'][key], 'grader_config_invalid')


def _read_pinned(reference, code='grader_plan_pin_mismatch'):
    paired.need(isinstance(reference, dict) and {'path', 'bytes', 'sha256'} <= set(reference), code)
    content = paired.read_bytes(reference['path'])
    paired.need(len(content) == reference['bytes'] and _sha(content) == reference['sha256'], code)
    return content


def _load_plan(plan_dir, config, now):
    plan_dir = paired.plain_path(str(plan_dir))
    receipt_bytes = paired.read_bytes(plan_dir/'receipt.json')
    receipt = blind._json(receipt_bytes)
    paired.need(receipt.get('schema') == 'cairn.grader-delivery-plan-receipt/1' and
                type(receipt.get('files')) is dict and 'plan.json' in receipt['files'], 'grader_plan_invalid')
    for name, reference in receipt['files'].items():
        paired.need(reference.get('path') == str(plan_dir/name), 'grader_plan_invalid')
        _read_pinned(reference)
    plan = blind._json(_read_pinned(receipt['files']['plan.json']))
    paired.need(plan.get('schema') == 'cairn.grader-delivery-plan/1', 'grader_plan_invalid')
    bundle = blind._json(_read_pinned(plan['selection']['grading_bundle']))
    paired.need(paired.canonical_sha(bundle['binding']) == plan['binding_sha256'], 'grader_plan_invalid')
    sessions = plan['sessions']
    paired.need(paired.utc(sessions['observed_at']) <= now < paired.utc(sessions['valid_until']), 'grader_sessions_stale')
    excluded = {key: {row[key] for row in sessions['exclusions']} for key in ('agent_id', 'session_id', 'execution_id')}
    excluded['agent_id'] |= set(bundle['policy']['excluded_agents'])
    excluded['session_id'] |= set(bundle['policy']['excluded_sessions'])
    destinations = {}
    for slot in sessions['slots']:
        _uuid(slot['agent_id'], 'grader_slot_not_addressable')
        # Engineer, reviewer, coordinator and earlier-exposed identities never grade.
        paired.need(all(slot[k] not in excluded[k] for k in excluded), 'grader_recipient_excluded')
        destinations[slot['slot']] = 'agent/'+slot['agent_id']
    paired.need(len(set(destinations.values())) == 2 and
                config['publisher']['agent_id'] not in {s['agent_id'] for s in sessions['slots']} and
                config['owner_destination'] not in destinations.values(), 'grader_config_invalid')
    rows = []
    for pair in plan['pairs']:
        for delivery in pair['deliveries']:
            paired.need(delivery['slot'] in SLOTS, 'grader_plan_invalid')
            rows.append((pair, delivery, destinations[delivery['slot']]))
    return plan, bundle, rows, _sha(receipt_bytes), excluded


def _check_authority(authority_pin, plan, rows, plan_sha, now):
    """Separately held release authority; matching declarations, not authentication."""
    content = _read_pinned(authority_pin, 'grader_authority_mismatch')
    authority = blind._json(content)
    paired.shape(authority, 'schema plan_receipt_sha256 binding_sha256 issuer observed_at valid_until recipients '
                 'packets max_request_bytes', 'grader_authority_mismatch')
    paired.need(authority['schema'] == AUTHORITY_SCHEMA and authority['plan_receipt_sha256'] == plan_sha and
                authority['binding_sha256'] == plan['binding_sha256'], 'grader_authority_mismatch')
    paired.need(paired.utc(authority['observed_at']) <= now < paired.utc(authority['valid_until']), 'grader_authority_stale')
    issuer = authority['issuer']
    paired.shape(issuer, 'role agent_id session_id', 'grader_authority_mismatch')
    paired.need(issuer['role'] in ('owner', 'coordinator'), 'grader_authority_mismatch')
    expected = [dict(slot=s['slot'], agent_id=s['agent_id'], session_id=s['session_id'],
                     execution_id=s['execution_id']) for s in plan['sessions']['slots']]
    recipients = authority['recipients']
    paired.need(type(recipients) is list and len(recipients) == 2, 'grader_authority_mismatch')
    models = {}
    for want, got in zip(expected, recipients):
        paired.shape(got, 'slot agent_id session_id execution_id model', 'grader_authority_mismatch')
        paired.need({k: got[k] for k in want} == want, 'grader_authority_mismatch')
        _text(got['model'], 'grader_authority_mismatch', 128)
        paired.need(issuer['agent_id'] != got['agent_id'] and issuer['session_id'] != got['session_id'],
                    'grader_recipient_excluded')
        models[got['slot']] = got['model']
    packets = sorted((p['pair_id'], d['slot'], d['packet_sha256']) for p, d, _ in rows)
    declared = authority['packets']
    paired.need(type(declared) is list and all(isinstance(r, dict) and set(r) == {'pair_id', 'slot', 'packet_sha256'}
                for r in declared), 'grader_authority_mismatch')
    paired.need(sorted((r['pair_id'], r['slot'], r['packet_sha256']) for r in declared) == packets and
                len(declared) == len(packets), 'grader_authority_mismatch')
    limit = authority['max_request_bytes']
    paired.need(type(limit) is int and 0 < limit <= NOTE_LIMIT, 'grader_authority_mismatch')
    return dict(sha256=_sha(content), models=models, max_request_bytes=limit)


def _check_blind(body):
    paired.need(not any(token in body for token in ARM_TOKENS), 'grader_request_not_blind')


def _render(plan, bundle, pair, delivery, publication_id, model):
    comparison = _read_pinned(delivery['comparison'])
    paired.need(_sha(comparison) == delivery['packet_sha256'], 'grader_plan_pin_mismatch')
    control = _read_pinned(delivery['control'])
    request = dict(schema=REQUEST_SCHEMA, request_id=publication_id, pair_id=pair['pair_id'], slot=delivery['slot'],
        recipient=dict(agent_id=delivery['agent_id'], session_id=delivery['session_id'],
                       execution_id=delivery['execution_id'], model=model),
        binding_sha256=plan['binding_sha256'], rubric_sha256=plan['binding']['rubric_sha256'],
        packet_sha256=delivery['packet_sha256'], comparison=comparison.decode('utf-8'),
        control=dict(encoding='base64', bytes=len(control), sha256=_sha(control),
                     data=base64.b64encode(control).decode('ascii')),
        response=dict(schema=RESPONSE_SCHEMA, samples=list(pair['samples']),
                      verdict_labels=list(bundle['policy']['verdict_labels']),
                      states=['determinate', 'undetermined'], reason_limit_bytes=REASON_LIMIT),
        instructions=INSTRUCTIONS)
    body = C(request).decode('utf-8')
    _check_blind(body)
    return body


def _envelope(body, delivery, authority):
    """Account the whole rendered request; only a bytes envelope can be shown to fit."""
    rendered = len(body.encode())
    paired.need(rendered <= NOTE_LIMIT, 'grader_request_exceeds_note_bound')
    paired.need(rendered <= authority['max_request_bytes'], 'grader_envelope_over_budget')
    paired.need(delivery['budget']['state'] != 'over_budget', 'grader_envelope_over_budget')
    unit, limit = delivery['budget']['unit'], delivery['budget']['limit']
    if unit == 'bytes' and limit is not None:
        paired.need(rendered <= limit, 'grader_envelope_over_budget')
    return dict(rendered_request_bytes=rendered, authority_limit_bytes=authority['max_request_bytes'],
                note_limit_bytes=NOTE_LIMIT, slot_unit=unit, slot_limit=limit,
                slot_fit='fits' if unit == 'bytes' and limit is not None else 'unknown')


class _Run:
    """Append-only private journal: each file is written once and re-reads must match."""

    def __init__(self, path, identity, *, create):
        self.path = paired.plain_path(str(path))
        head = self.path/'run.json'
        if not self.path.exists():
            paired.need(create, 'grader_run_missing')
            self.path.mkdir(mode=0o700)
            paired.write_selected_bytes(head, C(dict(schema='cairn.grader-delivery-run/1', **identity)))
        run = blind._json(paired.read_bytes(head))
        paired.need(all(run.get(k) == v for k, v in identity.items()), 'grader_run_mismatch')
        self.run_id = run['run_id']
        self.identity = run

    def read(self, kind, key):
        path = self.path/kind/(key+'.json')
        return blind._json(paired.read_bytes(path)) if path.exists() else None

    def record(self, kind, key, document, code='grader_journal_conflict'):
        directory = self.path/kind
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory/(key+'.json'); content = C(document)
        if path.exists():
            paired.need(paired.read_bytes(path) == content, code)
        else:
            paired.write_selected_bytes(path, content)
        return document


def _ref(value, code='grader_fabric_invalid'):
    paired.need(isinstance(value, dict) and type(value.get('record_id')) is str and value['record_id'] != '' and
                type(value.get('version')) is int and value['version'] >= 1, code)
    return dict(record_id=value['record_id'], version=value['version'])


def _send(run, fabric, config, kind, key, destination, body, ids, extra=None):
    """Save then publish one exact source under derived request UUIDs (store idempotency)."""
    content = body.encode()
    task = 'coordination/'+ids[1]
    intent = run.record(kind+'-intent', key, dict(source_request_id=ids[0], publication_id=ids[1],
        destination=destination, task=task, sha256=_sha(content), bytes=len(content), **(extra or {})),
        'grader_request_changed')
    source = _ref(fabric.remember(request_id=ids[0], repo=config['repo'], task=task, run='*', body=body, shareable=True))
    run.record(kind+'-source', key, source)
    published = fabric.publish(request_id=ids[1], repo=config['repo'], destination=destination, kind='request',
                               record_id=source['record_id'], version=source['version'], correlation_id=run.run_id)
    paired.need(isinstance(published, dict) and type(published.get('event_id')) is str and published['event_id'] != '',
                'grader_fabric_invalid')
    run.record(kind+'-event', key, dict(event_id=published['event_id']))
    return dict(destination=destination, source_request_id=ids[0], publication_id=ids[1],
                request=dict(**source, sha256=intent['sha256'], bytes=intent['bytes']), event_id=published['event_id'])


def _key(pair, delivery):
    return pair['pair_id']+'-'+delivery['slot']


def _identity(plan, plan_sha, config):
    return dict(run_id=_derived('run', plan_sha, plan['binding_sha256']), plan_receipt_sha256=plan_sha,
                config_sha256=paired.canonical_sha(config))


def _dispatch(plan_dir, run_dir, fabric, config, authority_pin, now):
    plan, bundle, rows, plan_sha, _ = _load_plan(plan_dir, config, now)
    authority = _check_authority(authority_pin, plan, rows, plan_sha, now)
    identity = _identity(plan, plan_sha, config)
    run_id = identity['run_id']
    # Render and account every request before the first durable effect.
    prepared = []
    for pair, delivery, destination in rows:
        key = _key(pair, delivery)
        ids = (_derived(run_id, 'request', key, 'source'), _derived(run_id, 'request', key, 'publication'))
        body = _render(plan, bundle, pair, delivery, ids[1], authority['models'][delivery['slot']])
        prepared.append((pair, delivery, destination, key, ids, body, _envelope(body, delivery, authority)))
    run = _Run(run_dir, dict(identity, authority_sha256=authority['sha256']), create=True)
    deliveries = []
    for pair, delivery, destination, key, ids, body, envelope in prepared:
        sent = _send(run, fabric, config, 'request', key, destination, body, ids,
                     dict(model=authority['models'][delivery['slot']]))
        deliveries.append(dict(pair_id=pair['pair_id'], slot=delivery['slot'], agent_id=delivery['agent_id'],
                               session_id=delivery['session_id'], execution_id=delivery['execution_id'],
                               model=authority['models'][delivery['slot']], envelope=envelope, **sent))
    return dict(schema='cairn.grader-delivery-dispatch/1', run_id=run.run_id, authority_sha256=authority['sha256'],
                deliveries=deliveries,
                rendered_request_bytes=sum(d['envelope']['rendered_request_bytes'] for d in deliveries),
                transport='synthetic_or_unverified', qualification='unqualified', runtime_admitted=False)


def _response(body, intent, pair, delivery, plan, labels):
    """Parse a grader body; any mismatch yields None (retained as invalid, never retried)."""
    try:
        value = blind._json(body.encode('utf-8'))
    except (paired.Refusal, ValueError, UnicodeError):
        return None
    expected = dict(schema=RESPONSE_SCHEMA, request_id=intent['publication_id'], pair_id=pair['pair_id'],
                    slot=delivery['slot'], packet_sha256=delivery['packet_sha256'], binding_sha256=plan['binding_sha256'])
    if not isinstance(value, dict) or set(value) != set(expected) | {'state', 'verdicts', 'reason'}:
        return None
    if any(value[k] != v for k, v in expected.items()):
        return None
    if type(value['reason']) is not str or len(value['reason'].encode()) > REASON_LIMIT:
        return None
    if value['state'] == 'undetermined':
        return value if value['verdicts'] is None else None
    if value['state'] != 'determinate' or not isinstance(value['verdicts'], dict):
        return None
    if set(value['verdicts']) != set(pair['samples']) or any(v not in labels for v in value['verdicts'].values()):
        return None
    return value


def _grade(run, fabric, plan, pair, delivery, destination, labels):
    key = _key(pair, delivery)
    intent, source, event = (run.read(kind, key) for kind in ('request-intent', 'request-source', 'request-event'))
    base = dict(pair_id=pair['pair_id'], slot=delivery['slot'], agent_id=delivery['agent_id'],
                session_id=delivery['session_id'], execution_id=delivery['execution_id'], consumer=destination)
    if event is None or source is None:
        return dict(base, state='undispatched')
    base['model'] = intent['model']
    status = fabric.event_status(event['event_id'])
    ev = status.get('event', {}) if isinstance(status, dict) else {}
    paired.need(ev.get('event_id') == event['event_id'] and ev.get('kind') == 'request' and
                _ref(ev.get('ref'), 'grade_custody_mismatch') == source and
                ev.get('destination') == dict(type='agent', name=destination) and
                ev.get('correlation_id') == run.run_id, 'grade_custody_mismatch')
    deliveries = status.get('deliveries')
    # Exactly one delivery to the exact recipient; a duplicate is never a second grade.
    paired.need(type(deliveries) is list and len(deliveries) == 1 and status.get('more') is False,
                'grade_duplicate_delivery' if type(deliveries) is list and len(deliveries) > 1 else 'grade_custody_mismatch')
    found = deliveries[0]
    paired.need(found.get('consumer') == destination, 'grade_custody_mismatch')
    base.update(event_id=event['event_id'], delivery_id=found.get('delivery_id'),
                request=dict(**source, sha256=intent['sha256'], bytes=intent['bytes']))
    state = found.get('state')
    if state in ('pending', 'leased'):
        return dict(base, state='pending')
    paired.need(state in ('handled', 'ignored', 'failed'), 'grade_custody_mismatch')
    if state != 'handled' or not found.get('result'):
        row = dict(base, state='failed', delivery_state=state, code=found.get('code', ''), custody='store_delivery_bound')
        return run.record('grades', key, row, 'grade_custody_changed')
    result = _ref(found['result'], 'grade_custody_mismatch')
    history = fabric.history(**result)
    versions = history.get('versions') if isinstance(history, dict) else None
    paired.need(type(versions) is list and len(versions) == 1, 'grade_custody_mismatch')
    version = versions[0]
    body = version.get('body')
    paired.need(version.get('version') == result['version'] and version.get('payload_available') is True and
                type(body) is str, 'grade_custody_mismatch')
    content = body.encode('utf-8')
    paired.need(_sha(content) == version.get('body_sha256') and len(content) == version.get('body_bytes'),
                'grade_custody_mismatch')
    retained = dict(result, body_sha256=version['body_sha256'], body_bytes=version['body_bytes'],
                    kind=version.get('kind'), observed_writer=version.get('observed_writer'),
                    witness=version.get('witness'), written_at=version.get('written_at'))
    parsed = _response(body, intent, pair, delivery, plan, labels)
    row = dict(base, result=retained, attempts=found.get('attempts'), late=bool(found.get('late')),
               custody='store_delivery_bound', execution_custody='unverified', model_custody='declared')
    if parsed is None:
        row.update(state='invalid')
    else:
        row.update(state='graded', grade_state=parsed['state'], verdicts=parsed['verdicts'],
                   reason_sha256=_sha(parsed['reason'].encode()))
    return run.record('grades', key, row, 'grade_custody_changed')


def _collect(plan_dir, run_dir, fabric, config, now):
    plan, bundle, rows, plan_sha, _ = _load_plan(plan_dir, config, now)
    run = _Run(run_dir, _identity(plan, plan_sha, config), create=False)
    labels = set(bundle['policy']['verdict_labels'])
    grades = [_grade(run, fabric, plan, pair, delivery, destination, labels) for pair, delivery, destination in rows]
    packets = {(p['pair_id'], d['slot']): d['packet_sha256'] for p in plan['pairs'] for d in p['deliveries']}
    submissions = [dict(submission_id=g['delivery_id'], binding_sha256=plan['binding_sha256'], pair_id=g['pair_id'],
        packet_sha256=packets[(g['pair_id'], g['slot'])], rubric_sha256=plan['binding']['rubric_sha256'],
        slot=g['slot'], agent_id=g['agent_id'], session_id=g['session_id'], state=g['grade_state'],
        verdicts=g['verdicts'], reason_sha256=g['reason_sha256']) for g in grades if g['state'] == 'graded']
    # The unchanged join applies the original agreement and disagreement rules.
    joined = custody.join_results(bundle, submissions, [])
    by_pair = {}
    for g in grades:
        by_pair.setdefault(g['pair_id'], {})[g['slot']] = g
    disagreements = []
    for pair in joined['pairs']:
        if pair.get('declared_comparison') != 'disagreement':
            continue
        pair_id = pair['packet']['pair_id']; both = by_pair[pair_id]
        disagreements.append(dict(pair_id=pair_id, packet_sha256=packets[(pair_id, 'primary')],
            grades={s: {k: both[s][k] for k in ('event_id', 'delivery_id', 'result', 'verdicts', 'reason_sha256')}
                    for s in SLOTS}, resolution=pair['resolution']))
    count = lambda state: sum(1 for g in grades if g['state'] == state)
    return run, plan, dict(schema='cairn.grader-delivery-collection/1', run_id=run.run_id, grades=grades,
        coverage=dict(planned=len(grades), dispatched=len(grades) - count('undispatched'), graded=count('graded'),
                      pending=count('pending'), failed=count('failed'), invalid=count('invalid')),
        agreement=joined['aggregate']['declared_raw_agreement'], disagreements=disagreements, join=joined,
        transport='synthetic_or_unverified', qualification='unqualified', runtime_admitted=False)


def _route(plan_dir, run_dir, fabric, config, now):
    run, plan, report = _collect(plan_dir, run_dir, fabric, config, now)
    disputes = []
    for item in report['disagreements']:
        ids = (_derived(run.run_id, 'dispute', item['pair_id'], 'source'),
               _derived(run.run_id, 'dispute', item['pair_id'], 'publication'))
        body = C(dict(schema=DISPUTE_SCHEMA, request_id=ids[1], run_id=run.run_id, pair_id=item['pair_id'],
            binding_sha256=plan['binding_sha256'], packet_sha256=item['packet_sha256'], grades=item['grades'],
            resolution='owner_decision_required',
            note='Two independent blind grades disagree. Only the owner decides; no script resolves this.')).decode('utf-8')
        _check_blind(body)
        paired.need(len(body.encode()) <= NOTE_LIMIT, 'grader_request_exceeds_note_bound')
        sent = _send(run, fabric, config, 'dispute', item['pair_id'], config['owner_destination'], body, ids)
        disputes.append(dict(pair_id=item['pair_id'], **sent))
    return dict(schema='cairn.grader-dispute-routing/1', run_id=run.run_id, disputes=disputes, resolution='unresolved')


def _guarded(function, *required):
    if any(value is None for value in required):
        raise paired.Refusal(UNAVAILABLE)
    try:
        _check_config(required[1])
        return function()
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError, StopIteration):
        raise paired.Refusal('grader_delivery_invalid_or_unavailable') from None


def dispatch(plan_dir, run_dir, *, fabric=None, config=None, authority=None, now):
    """Publish one blind request per pair and slot under a held release authority pin."""
    return _guarded(lambda: _dispatch(plan_dir, run_dir, fabric, config, authority, now), fabric, config, authority)


def collect(plan_dir, run_dir, *, fabric=None, config=None, now):
    """Read delivery-linked results, retain custody once, and apply the unchanged join."""
    return _guarded(lambda: _collect(plan_dir, run_dir, fabric, config, now)[2], fabric, config)


def route_disagreements(plan_dir, run_dir, *, fabric=None, config=None, now):
    """Publish each determinate disagreement to the owner; never records a disposition."""
    return _guarded(lambda: _route(plan_dir, run_dir, fabric, config, now), fabric, config)


class CliFabric:
    """Existing `cairn agent` operations; every call is one bounded subprocess."""

    def __init__(self, *, cairn, token_file, socket, publisher, runner=subprocess.run, timeout=60):
        self.base = [cairn, 'agent', '--token-file', token_file] + (['--socket', socket] if socket else [])
        self.base += ['--agent-id', publisher['agent_id'], '--execution-id', publisher['execution_id']]
        self.runner, self.timeout = runner, timeout

    def _call(self, args, stdin=b''):
        try:
            done = self.runner(self.base + args, input=stdin, capture_output=True, timeout=self.timeout, check=False)
        except subprocess.TimeoutExpired:
            raise paired.Refusal('grader_fabric_uncertain') from None
        except OSError:
            raise paired.Refusal('grader_fabric_unavailable') from None
        try:
            envelope = json.loads(done.stdout)
        except (ValueError, TypeError):
            raise paired.Refusal('grader_fabric_uncertain') from None
        paired.need(isinstance(envelope, dict) and envelope.get('schema') == 'cairn.response/1' and
                    envelope.get('ok') is True and done.returncode == 0, 'grader_fabric_refused')
        return envelope.get('data')

    def remember(self, *, request_id, repo, task, run, body, shareable):
        flags = ['--shareable'] if shareable else []
        return self._call(['remember', '--request-id', request_id, '--repo', repo, '--task', task, '--run', run,
                           '--kind', 'note', *flags, '--stdin'], body.encode('utf-8'))

    def publish(self, *, request_id, repo, destination, kind, record_id, version, correlation_id):
        return self._call(['publish', '--request-id', request_id, '--repo', repo, '--to', destination, '--kind', kind,
                           '--version', str(version), '--correlation-id', correlation_id, record_id])

    def event_status(self, event_id):
        return self._call(['event-status', event_id])

    def history(self, *, record_id, version):
        return self._call(['history'], json.dumps(dict(record_id=record_id, version=version), separators=(',', ':')).encode())


def main(argv=None):
    """`dispatch|collect|route --plan DIR --run DIR --config FILE [--authority PIN_JSON]`; else fixed refusal."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        command = argv[0] if argv else None
        size = 9 if command == 'dispatch' else 7
        flags = ['--plan', '--run', '--config'] + (['--authority'] if command == 'dispatch' else [])
        paired.need(command in ('dispatch', 'collect', 'route') and len(argv) == size and argv[1::2] == flags, UNAVAILABLE)
        try:
            document = paired.read_json(argv[6])
            authority = paired.read_json(argv[8]) if command == 'dispatch' else None
        except (paired.Refusal, OSError):
            raise paired.Refusal(UNAVAILABLE) from None
        paired.shape(document, 'delivery cli', UNAVAILABLE)
        paired.shape(document['cli'], 'cairn token_file socket', UNAVAILABLE)
        config = document['delivery']
        paired.need(isinstance(config, dict) and isinstance(config.get('publisher'), dict), UNAVAILABLE)
        fabric = CliFabric(publisher=config['publisher'], **document['cli'])
        now = dt.datetime.now(dt.timezone.utc)
        kwargs = dict(fabric=fabric, config=config, now=now)
        if command == 'dispatch':
            result = dispatch(Path(argv[2]), Path(argv[4]), authority=authority, **kwargs)
        else:
            result = dict(collect=collect, route=route_disagreements)[command](Path(argv[2]), Path(argv[4]), **kwargs)
    except paired.Refusal as refusal:
        print(json.dumps(dict(status='refused', code=str(refusal))))
        return 2
    except (KeyError, TypeError):
        print(json.dumps(dict(status='refused', code=UNAVAILABLE)))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
