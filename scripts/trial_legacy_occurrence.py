"""Post-attempt orchestration of one completed legacy weekly occurrence. CAIRN-107.

Runs, in order and only through the existing functions:

    conversion   trial_legacy_blind_selection.convert
    draft        trial_blind_packet_draft.approval_subject / draft      (changed pairs only)
    bundle       trial_legacy_grading_bundle.build
    plan         trial_grader_delivery_plan.build_plan                  (changed pairs only)
    dispatch     trial_grader_delivery.dispatch                         (changed pairs only)
    collect      trial_grader_delivery.collect / route_disagreements    (changed pairs only)
    report       trial_legacy_grading_bundle.report_join, trial_run_report.build_report,
                 render_markdown, note_draft
    publication  the configured publisher (trial_run_report.publish_note); terminal
                 occurrences only, pending note_publication_unconfigured without one

Each completed phase is recorded once under <out>/phases/<phase>.json with its
exact inputs and output pins. A repeat invocation verifies both and continues;
a changed input refuses occurrence_<phase>_input_changed and a changed output
refuses occurrence_<phase>_output_changed, so nothing downstream is silently
reused. Dispatch and dispute routing reuse the delivery journal and its
derived request IDs, so a retry never publishes twice. Missing prerequisites
are reported as pending states, never fabricated. No actor is run, nothing is
admitted, and every declared value stays declared. Every run also persists its
exact state as an immutable, content-addressed snapshot under <out>/states/,
bound to the input and the report, join, collection and routing documents. See
docs/legacy-occurrence.md.
"""
import argparse
import subprocess
import hashlib
import json
from pathlib import Path
import sys
import uuid

import trial_blind_packet_draft as blind
import trial_grading_custody as custody
import trial_grader_delivery as grader
import trial_grader_delivery_plan as delivery_plan
import trial_legacy_blind_selection as legacy
import trial_legacy_grading_bundle as adapter
import trial_paired_request as paired
import trial_run_report as report

INPUT_SCHEMA = 'cairn.legacy-occurrence-input/1'
PHASE_SCHEMA = 'cairn.legacy-occurrence-phase/1'
STATE_SCHEMA = 'cairn.legacy-occurrence-state/1'
SNAPSHOT_SCHEMA = 'cairn.legacy-occurrence-state-snapshot/1'
INPUT_FIELDS = 'schema conversion_request grading_policy draft_approval grader_sessions release_authority run_date'
OPTIONAL = ('grading_policy', 'draft_approval', 'grader_sessions', 'release_authority')


def C(document):
    """Canonical JSON bytes; unlike blind.canonical_bytes it admits the join's float rates."""
    return (json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            + '\n').encode('utf-8')
need = paired.need


class Pending(Exception):
    """A prerequisite is not available yet; carries a fixed code."""


def _digest(content):
    return dict(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())


def _existing(path):
    return dict(path=str(path), **_digest(legacy._read_path(path)))


def _verify(reference, code):
    need(isinstance(reference, dict) and set(reference) == {'path', 'bytes', 'sha256'}, code)
    try:
        content = legacy._read_path(reference['path'])
    except OSError:
        raise paired.Refusal(code) from None
    need(_digest(content) == {'bytes': reference['bytes'], 'sha256': reference['sha256']}, code)
    return content


def _write_once(path, content, code):
    """Create once; an identical rerun is a no-op, different content refuses."""
    if path.exists():
        need(path.read_bytes() == content, code)
    else:
        paired.write_selected_bytes(path, content)
    return dict(path=str(path), **_digest(content))


class _Phases:
    def __init__(self, out):
        self.out = out
        self.dir = out/'phases'
        self.dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.states = []

    def recorded(self, phase, inputs):
        """The verified outputs of a completed phase, or None if it has not completed."""
        path = self.dir/(phase+'.json')
        if not path.exists():
            return None
        record = blind._json(legacy._read_path(path))
        need(isinstance(record, dict) and record.get('schema') == PHASE_SCHEMA and record.get('phase') == phase
             and isinstance(record.get('outputs'), dict), f'occurrence_{phase}_record_invalid')
        need(record.get('inputs') == inputs, f'occurrence_{phase}_input_changed')
        need(record.get('phase_id') == paired.canonical_sha(inputs), f'occurrence_{phase}_record_invalid')
        for reference in record['outputs'].values():
            if reference is not None:
                _verify(reference, f'occurrence_{phase}_output_changed')
        return record

    def complete(self, phase, inputs, outputs, **detail):
        record = dict(schema=PHASE_SCHEMA, phase=phase, phase_id=paired.canonical_sha(inputs), inputs=inputs,
                      outputs=outputs, **detail)
        _write_once(self.dir/(phase+'.json'), C(record), f'occurrence_{phase}_input_changed')
        return record

    def note(self, phase, state, **detail):
        self.states.append(dict(phase=phase, state=state, **detail))


def _pin(document, name):
    reference = document[name]
    if reference is None:
        return None
    _verify(reference, 'occurrence_input_pin_mismatch')
    return reference


def _conversion(phases, request):
    inputs = dict(conversion_request=request)
    record = phases.recorded('conversion', inputs)
    if record is None:
        try:
            legacy.convert(request, phases.out/'conversion')
        except paired.Refusal as refusal:
            if str(refusal) in ('legacy_materials_missing', 'legacy_materials'):
                raise Pending('case_material_missing') from None
            raise
        record = phases.complete('conversion', inputs, dict(receipt=_existing(phases.out/'conversion/receipt.json')))
    receipt = blind._json(_verify(record['outputs']['receipt'], 'occurrence_conversion_output_changed'))
    phases.note('conversion', 'complete', changed_pair_grading=receipt['changed_pair_grading'])
    return record['outputs']['receipt'], receipt


def _draft(phases, receipt_pin, receipt, approval, now):
    if receipt['selection'] is None:
        phases.note('draft', 'not_required')
        return None
    inputs = dict(conversion_receipt=receipt_pin, approval=approval)
    record = phases.recorded('draft', inputs)
    if record is None:
        # The approver needs the exact subject; it is private and never an approval itself.
        subject = blind.approval_subject(receipt['selection'])
        subject_pin = _write_once(phases.out/'draft-subject.json', C(subject), 'occurrence_draft_output_changed')
        if approval is None:
            raise Pending('draft_approval_missing')
        target = phases.out/'draft'
        if target.exists():
            # Interrupted after the draft was written: adopt it only if it is this exact draft.
            prior = blind._json(legacy._read_path(target/'private/receipt.json'))
            need(prior.get('selection_pin') == receipt['selection'] and prior.get('approval_pin') == approval,
                 'occurrence_draft_output_changed')
        else:
            blind.draft(receipt['selection'], approval, target, now=now)
        record = phases.complete('draft', inputs, dict(
            subject=subject_pin, receipt=_existing(phases.out/'draft/private/receipt.json'),
            crosswalk=_existing(phases.out/'draft/private/crosswalk.json')))
    phases.note('draft', 'complete')
    return record['outputs']


def _bundle(phases, receipt_pin, draft, policy_pin):
    if policy_pin is None:
        raise Pending('grading_policy_missing')
    inputs = dict(conversion_receipt=receipt_pin, draft_receipt=draft and draft['receipt'], grading_policy=policy_pin)
    record = phases.recorded('bundle', inputs)
    policy = blind._json(_verify(policy_pin, 'occurrence_input_pin_mismatch'))
    bundle, result = adapter.build(receipt_pin, policy, draft_receipt=draft and draft['receipt'])
    if record is None:
        directory = phases.out/'bundle'
        directory.mkdir(mode=0o700, exist_ok=True)
        record = phases.complete('bundle', inputs, dict(
            bundle=_write_once(directory/'bundle.json', C(bundle), 'occurrence_bundle_output_changed'),
            result=_write_once(directory/'result.json', C(result), 'occurrence_bundle_output_changed')))
    # The rebuilt bundle must be byte-identical to the recorded one.
    need(_digest(C(bundle)) == {k: record['outputs']['bundle'][k] for k in ('bytes', 'sha256')} and
         _digest(C(result)) == {k: record['outputs']['result'][k] for k in ('bytes', 'sha256')},
         'occurrence_bundle_output_changed')
    phases.note('bundle', 'complete', status=result['status'], binding_sha256=result['binding_sha256'])
    return record['outputs']['bundle'], bundle, result


def _plan(phases, receipt, draft, approval, bundle_pin, result, sessions, now):
    if sessions is None:
        raise Pending('grader_sessions_missing')
    request = blind._json(_verify(receipt['request'], 'occurrence_conversion_output_changed'))
    inputs = dict(bundle=bundle_pin, draft_receipt=draft['receipt'], approval=approval, grader_sessions=sessions)
    record = phases.recorded('plan', inputs)
    if record is None:
        selection = dict(schema='cairn.grader-delivery-selection/1', crosswalk=draft['crosswalk'],
                         draft_receipt=draft['receipt'], approval=approval, policy=request['policy'],
                         grading_bundle=bundle_pin, sessions=sessions, pairs=result['delivery_pairs'])
        selection_pin = _write_once(phases.out/'delivery-selection.json', C(selection), 'occurrence_plan_output_changed')
        target = phases.out/'delivery-plan'
        if target.exists():
            prior = blind._json(legacy._read_path(target/'receipt.json'))
            need(prior.get('selection_pin') == selection_pin, 'occurrence_plan_output_changed')
        else:
            delivery_plan.build_plan(selection_pin, target, now=now)
        record = phases.complete('plan', inputs, dict(selection=selection_pin,
                                                      receipt=_existing(phases.out/'delivery-plan/receipt.json')))
    phases.note('plan', 'complete')
    return record['outputs']['receipt']


def _dispatch(phases, plan_receipt, authority, fabric, config, now):
    if authority is None:
        raise Pending('release_authority_missing')
    if fabric is None or config is None:
        raise Pending('grader_fabric_unavailable')
    inputs = dict(plan_receipt=plan_receipt, release_authority=authority,
                  delivery_config_sha256=paired.canonical_sha(config))
    record = phases.recorded('dispatch', inputs)
    if record is None:
        try:
            # Derived request IDs and the journal make a retry reuse, never republish.
            receipt = grader.dispatch(phases.out/'delivery-plan', phases.out/'delivery-run', fabric=fabric,
                                      config=config, authority=authority, now=now)
        except paired.Refusal as refusal:
            if str(refusal) == 'grader_fabric_uncertain':
                raise Pending('dispatch_uncertain_retry') from None
            raise
        record = phases.complete('dispatch', inputs, dict(
            receipt=_write_once(phases.out/'dispatch.json', C(receipt), 'occurrence_dispatch_output_changed')))
    phases.note('dispatch', 'complete')
    return record


def _collect(phases, fabric, config, now):
    """Returns (collection, pending code or None, routing receipt or None). Grades are only read."""
    try:
        collection = grader.collect(phases.out/'delivery-plan', phases.out/'delivery-run', fabric=fabric,
                                    config=config, now=now)
    except paired.Refusal as refusal:
        if str(refusal) == 'grader_fabric_uncertain':
            return None, 'collect_uncertain_retry', None
        raise
    coverage = collection['coverage']
    if coverage['pending'] or coverage['dispatched'] < coverage['planned']:
        phases.note('collect', 'awaiting_grades', coverage=coverage)
        return collection, 'awaiting_grades', None
    disputes = routed = None
    if collection['disagreements']:
        try:
            routed = grader.route_disagreements(phases.out/'delivery-plan', phases.out/'delivery-run', fabric=fabric,
                                                config=config, now=now)
        except paired.Refusal as refusal:
            if str(refusal) == 'grader_fabric_uncertain':
                return collection, 'dispute_routing_uncertain_retry', None
            raise
        disputes = dict(count=len(routed['disputes']), destination='owner_only', resolution=routed['resolution'])
    phases.note('collect', 'complete', coverage=coverage, disputes=disputes)
    return collection, None, routed


def _join(bundle, result, collection):
    """The report join, always recomputed by the existing join; never relabelled."""
    if result['status'] == 'no_grading_needed':
        return adapter.report_join(bundle, result), 'no_grading_needed'
    if collection is not None:
        return adapter.report_join(bundle, result, collection), 'collected_grades'
    # Evidence for an occurrence that cannot (yet) be graded: the join with no grades.
    return custody.join_results(bundle, [], []), 'no_grades_incomplete'


def _report(phases, receipt, bundle_pin, bundle, result, collection, run_date):
    """Write one versioned snapshot per exact join input; earlier snapshots are kept."""
    join, basis = _join(bundle, result, collection)
    agent = json.loads(_verify(receipt['sources']['agent'], 'occurrence_conversion_output_changed'))
    built = report.build_report(agent, join, run_date)
    if basis == 'no_grades_incomplete':
        need(built['status'] == 'incomplete', 'occurrence_report_without_grades')
    join_sha = paired.canonical_sha(join)
    snapshot = join_sha[:20]
    phase = 'report-' + snapshot
    # Keyed by the exact join input; how the join was reached (basis) does not change the report.
    inputs = dict(agent=receipt['sources']['agent'], bundle=bundle_pin, join_sha256=join_sha, run_date=run_date)
    record = phases.recorded(phase, inputs)  # a changed input refuses before any write
    directory = phases.out/'report'/snapshot
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    code = f'occurrence_{phase}_output_changed'
    outputs = dict(join=_write_once(directory/'join.json', C(join), code),
                   report=_write_once(directory/'report.json', C(built), code),
                   markdown=_write_once(directory/'report.md', report.render_markdown(built).encode(), code),
                   note_draft=_write_once(directory/'note-draft.json', C(report.note_draft(built)), code))
    if record is None:
        phases.complete(phase, inputs, outputs)
    phases.note('report', 'snapshot_written', snapshot=snapshot, basis=basis, report_status=built['status'],
                report_sha256=built['report_sha256'])
    return snapshot, built, join, outputs


def _publish(phases, snapshot, built, publisher):
    """Publish the terminal snapshot's note once, through the configured existing publisher."""
    phase = 'publication-' + snapshot
    draft = report.note_draft(built)
    inputs = dict(report_sha256=built['report_sha256'], request_id=draft['request_id'])
    path = phases.dir/(phase+'.json')
    if path.exists():
        record = phases.recorded(phase, inputs)
        # A recorded receipt is re-validated in full; empty or tampered details refuse.
        need(set(record) == {'schema', 'phase', 'phase_id', 'inputs', 'outputs', 'publication'}
             and record['outputs'] == {}, 'occurrence_publication_record_invalid')
        _receipt(record['publication'], draft['request_id'], built['report_sha256'],
                 'occurrence_publication_record_invalid')
        phases.note('publication', 'published', snapshot=snapshot, request_id=draft['request_id'],
                    record_id=record['publication']['record_id'], version=record['publication']['version'])
        return record, None
    if publisher is None:
        phases.note('publication', 'pending', snapshot=snapshot, request_id=draft['request_id'])
        return None, 'note_publication_unconfigured'
    try:
        # The request ID is derived from the exact report, so a replay is idempotent in the store.
        published = publisher(built)
    except (subprocess.TimeoutExpired, OSError):
        published = None
    except paired.Refusal as refusal:
        if str(refusal) != 'note_response':
            raise
        published = None
    if published is None:
        phases.note('publication', 'uncertain', snapshot=snapshot, request_id=draft['request_id'])
        return None, 'note_publication_uncertain_retry'
    need(isinstance(published, dict) and published.get('request_id') == draft['request_id'],
         'occurrence_publication_mismatch')
    receipt = dict(request_id=published['request_id'], record_id=published.get('record_id'),
                   version=published.get('version'), report_sha256=built['report_sha256'])
    # Only a usable store reference publishes; anything else refuses and nothing is recorded.
    _receipt(receipt, draft['request_id'], built['report_sha256'], 'occurrence_publication_invalid')
    record = phases.complete(phase, inputs, {}, publication=receipt)
    phases.note('publication', 'published', snapshot=snapshot, request_id=draft['request_id'],
                record_id=receipt['record_id'], version=receipt['version'])
    return record, None


def _receipt(receipt, request_id, report_sha256, code):
    """An exact store reference: canonical record UUID, positive version, the expected request."""
    need(isinstance(receipt, dict) and set(receipt) == {'request_id', 'record_id', 'version', 'report_sha256'}
         and receipt['request_id'] == request_id and receipt['report_sha256'] == report_sha256
         and type(receipt['version']) is int and receipt['version'] >= 1 and type(receipt['record_id']) is str, code)
    try:
        need(str(uuid.UUID(receipt['record_id'])) == receipt['record_id'] and receipt['record_id'] != str(uuid.UUID(int=0)),
             code)
    except ValueError:
        raise paired.Refusal(code) from None


def _grading(join, collection, routing):
    """Observed grading status beside the unchanged report, so pending owner
    adjudication is distinguishable from missing grades. Figures come from the
    existing join; disputes are never resolved here."""
    aggregate = join['aggregate']
    coverage = aggregate['changed_pair_coverage']
    disagreements = aggregate['disagreements']
    disputes = [] if routing is None else routing['disputes']
    return dict(changed_pairs=coverage['denominator'],
                all_graded=coverage['determinate_dual'] == coverage['denominator'],
                agreement=aggregate['declared_raw_agreement'], disagreements=disagreements,
                disagreements_routed=disagreements == 0 or len(disputes) == disagreements,
                routing=None if routing is None else dict(destination='owner_only', resolution=routing['resolution'],
                                                          disputes=disputes),
                delivery_coverage=None if collection is None else collection['coverage'],
                resolution='owner_pending' if disagreements else 'not_applicable')


def _snapshots(out):
    directory = out/'report'
    return sorted(p.name for p in directory.iterdir()) if directory.is_dir() else []


def _run(input_pin, out, fabric, config, publisher, now):
    document = blind._json(_verify(input_pin, 'occurrence_input_pin_mismatch'))
    paired.shape(document, INPUT_FIELDS, 'occurrence_input_shape')
    need(document['schema'] == INPUT_SCHEMA and type(document['run_date']) is str, 'occurrence_input_shape')
    request = _pin(document, 'conversion_request')
    need(request is not None, 'occurrence_input_shape')
    pins = {name: _pin(document, name) for name in OPTIONAL}
    out = paired.plain_path(str(out))
    out.mkdir(mode=0o700, exist_ok=True)
    phases = _Phases(out)
    pending, snapshot, built, published, grading, routing = None, None, None, False, None, None
    collection, outputs = None, None
    try:
        receipt_pin, receipt = _conversion(phases, request)
        draft = _draft(phases, receipt_pin, receipt, pins['draft_approval'], now)
        bundle_pin, bundle, result = _bundle(phases, receipt_pin, draft, pins['grading_policy'])
    except Pending as missing:
        pending = str(missing)
        phases.note('next', 'pending', code=pending)
    else:
        if result['status'] == 'awaiting_grades':
            try:
                plan_receipt = _plan(phases, receipt, draft, pins['draft_approval'], bundle_pin, result,
                                     pins['grader_sessions'], now)
                _dispatch(phases, plan_receipt, pins['release_authority'], fabric, config, now)
                collection, pending, routing = _collect(phases, fabric, config, now)
            except Pending as missing:
                pending = str(missing)
                phases.note('next', 'pending', code=pending)
        else:
            for phase in ('plan', 'dispatch', 'collect'):
                phases.note(phase, 'not_required')
        # Report evidence is kept for every occurrence that reached a bundle.
        snapshot, built, join, outputs = _report(phases, receipt, bundle_pin, bundle, result, collection,
                                                 document['run_date'])
        grading = _grading(join, collection, routing)
        if pending is None:
            # Only a terminal occurrence is published; an advancing one keeps its snapshot unpublished.
            record, pending = _publish(phases, snapshot, built, publisher)
            published = record is not None
        else:
            phases.note('publication', 'not_terminal', snapshot=snapshot)
    state = dict(schema=STATE_SCHEMA, status='pending' if pending else 'finished', pending=pending,
                 phases=phases.states, snapshot=snapshot, snapshots=_snapshots(out),
                 report_status=built and built['status'], grading=grading, note_published=published,
                 complete=bool(not pending and published and built and built['status'] == 'declared_complete'),
                 runtime_admitted=False, grades_created=False, custody='declared_only')
    return state, _persist_state(out, input_pin, state, outputs, collection, routing)


def _addressed(out, directory, document):
    """Write a document once under its own content hash; an identical rerun is a no-op."""
    content = C(document)
    folder = out/directory
    folder.mkdir(mode=0o700, exist_ok=True)
    return _write_once(folder/(hashlib.sha256(content).hexdigest()[:20] + '.json'), content,
                       'occurrence_state_snapshot_changed')


def _persist_state(out, input_pin, state, outputs, collection, routing):
    """Persist this exact state, bound to the input and the documents it was derived from.

    Snapshots are content-addressed and immutable; a different state or binding
    is a new snapshot and earlier ones are kept. The input is bound by content
    (bytes, sha256), since a rerun may pin the same input at another path.
    """
    snapshot = dict(schema=SNAPSHOT_SCHEMA, input=dict(bytes=input_pin['bytes'], sha256=input_pin['sha256']),
                    state=state, report=outputs and outputs['report'], join=outputs and outputs['join'],
                    collection=None if collection is None else _addressed(out, 'collections', collection),
                    routing=None if routing is None else _addressed(out, 'routings', routing))
    return _addressed(out, 'states', snapshot)


def run_occurrence(input_pin, out, *, fabric=None, config=None, publisher=None, now):
    """Run or resume one occurrence; returns its phase states. Never runs actors.

    publisher(report) -> {request_id, record_id, version}, e.g. a partial of
    trial_run_report.publish_note. Without one, a terminal occurrence stays
    pending note_publication_unconfigured.
    """
    return run_occurrence_snapshot(input_pin, out, fabric=fabric, config=config, publisher=publisher, now=now)[0]


def run_occurrence_snapshot(input_pin, out, *, fabric=None, config=None, publisher=None, now):
    """As run_occurrence, and also return the pin of the persisted state snapshot
    (cairn.legacy-occurrence-state-snapshot/1 under <out>/states/)."""
    try:
        return _run(input_pin, out, fabric, config, publisher, now)
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
        raise paired.Refusal('occurrence_invalid_or_unavailable') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--input', required=True, help='occurrence input JSON path')
    parser.add_argument('--input-sha256', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--now', required=True, help='UTC YYYY-MM-DDTHH:MM:SSZ')
    parser.add_argument('--delivery-config', help='trial_grader_delivery config file ({"delivery", "cli"})')
    parser.add_argument('--note-config', help='note publication config file ({"binary", "token_file", "repo"})')
    parser.add_argument('--snapshot-pin-out', help='write the persisted state snapshot pin to this new file')
    args = parser.parse_args(argv)
    try:
        content = legacy._read_path(args.input)
        pin = dict(path=args.input, bytes=len(content), sha256=args.input_sha256)
        fabric = config = None
        if args.delivery_config:
            document = paired.read_json(args.delivery_config)
            paired.shape(document, 'delivery cli', 'occurrence_delivery_config')
            config = document['delivery']
            fabric = grader.CliFabric(publisher=config['publisher'], **document['cli'])
        publisher = None
        if args.note_config:
            note = paired.read_json(args.note_config)
            paired.shape(note, 'binary token_file repo', 'occurrence_note_config')
            publisher = lambda built: report.publish_note(built, **note)  # noqa: E731
        state, snapshot = run_occurrence_snapshot(pin, Path(args.out), fabric=fabric, config=config,
                                                  publisher=publisher, now=paired.utc(args.now))
        if args.snapshot_pin_out:
            paired.write_selected(paired.plain_path(args.snapshot_pin_out), snapshot)
    except paired.Refusal as refusal:
        print(json.dumps(dict(status='refused', code=str(refusal))))
        return 2
    except (OSError, KeyError, TypeError):
        print(json.dumps(dict(status='refused', code='occurrence_invalid_or_unavailable')))
        return 2
    print(json.dumps(state, sort_keys=True))
    return 0 if state['status'] == 'finished' else 3


if __name__ == '__main__':
    sys.exit(main())
