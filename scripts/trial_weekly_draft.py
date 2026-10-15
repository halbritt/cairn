"""Offline original-weekly draft producer; no reviews, holds or execution."""
import argparse
import datetime as dt
import hashlib
import json

import trial_paired_request as paired
import trial_weekly_request as dispatch
from trial_weekly_input import load_weekly_input

SCHEMA = 'cairn.weekly-eval.proposal/1'
FIELDS = ('schema request_id campaign_id occurrence_id scheduled_at expires_at '
          'weekly_input capacity grading output reply supervisor_source permission_profile')


def draft(proposal, draft_dir, *, now):
    """Write selected draft files from a trusted quiescent evaluator-side source."""
    try:
        return _draft(proposal, draft_dir, now=now)
    except paired.Refusal:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        raise paired.Refusal('weekly_draft_invalid_or_unavailable') from None


def _draft(proposal, draft_dir, *, now):
    paired.shape(proposal, FIELDS, 'weekly_proposal_shape')
    paired.need(proposal['schema'] == SCHEMA, 'weekly_proposal_schema')
    encoded = paired.selected_bytes(proposal)
    paired.need(len(encoded) <= paired.FILE_LIMIT, 'weekly_retention_bound')
    proposal = paired.decode_json(encoded)
    destination = paired.plain_path(str(draft_dir))
    output = paired.plain_path(proposal['output'])
    paired.need(not destination.exists() and not output.exists(), 'target_exists')
    for field in ('request_id', 'campaign_id', 'occurrence_id'):
        paired.identifier(proposal[field])
    start, end = paired.utc(proposal['scheduled_at']), paired.utc(proposal['expires_at'])
    now = paired.utc(now.isoformat())
    paired.need(start < end and now < end, 'request_expired')
    paired.shape(proposal['reply'], 'event_id recipient_id correlation_id', 'reply_shape')
    for value in proposal['reply'].values():
        paired.identifier(value)
    bundle = load_weekly_input(proposal['weekly_input'])
    paired.pin(proposal['supervisor_source'])
    paired.permission_profile_check(proposal['permission_profile'])
    paired.grading_check(proposal['grading'], set())  # Reviewers are not selected yet.
    models = dict(CURRENT=bundle['execution']['native']['model'], CAND=bundle['execution']['native']['model'],
                  **{role: proposal['grading'][role]['model'] for role in ('primary', 'secondary')})
    paired.capacity_check(proposal['capacity'], bundle['plan'], now, models)
    capacity = paired.read_json_pin(proposal['capacity'])
    paired.need(paired.utc(capacity['observed_at']) <= start and end <= paired.utc(capacity['valid_until']),
                'draft_capacity_interval')
    # Only contract-selected structured controls are traversed. Opaque grading,
    # supervisor and basis artifacts are pins, not recursively loaded manifests.
    controls = {'capacity.json': paired.selected_bytes(capacity),
                'workload.json': paired.read_pin_bytes(bundle['plan']['workload'])}
    for field in ('account_binding', 'route_binding', 'reservation'):
        controls[field+'.json'] = paired.read_pin_bytes(capacity[field])
    for field in ('source', 'settings'):
        controls['permission-'+field+'.json'] = paired.read_pin_bytes(proposal['permission_profile'][field])
    # Reuse the two-destination guard only. No preparation registry is created.
    # Protect the proposal before adding the generated schedule pin inside draft_dir.
    dispatch._protect(proposal, bundle, controls, None, None, registry=destination)
    schedule_bytes = paired.selected_bytes(bundle['schedule'])
    core = dict(proposal, schema=dispatch.SCHEMA, plan=bundle['plan'],
                schedule=dict(path=str(destination/'schedule.json'),
                              sha256=hashlib.sha256(schedule_bytes).hexdigest()),
                limits=dict(paired.ORIGINAL_LIMITS))
    subjects = dict(schema='cairn.weekly-review-subjects/1',
                    request_core_sha256=paired.canonical_sha(core),
                    subjects=dispatch.review_subjects(core, bundle))
    files = {'schedule.json': schedule_bytes,
             'request-core.json': paired.selected_bytes(core),
             'review-subjects.json': paired.selected_bytes(subjects)}
    receipt = dict(schema='cairn.weekly-eval.draft/1', status='drafted_for_review',
        proposal_sha256=paired.canonical_sha(proposal), request_core_sha256=paired.canonical_sha(core),
        files={name: dict(sha256=hashlib.sha256(content).hexdigest(), bytes=len(content))
               for name, content in files.items()},
        runtime_admitted=False, witness_valid=False, admission=False)
    paired.need(sum(map(len, files.values())) + len(paired.selected_bytes(receipt)) <= paired.FILE_LIMIT,
                'weekly_retention_bound')
    destination.mkdir(mode=0o700)
    for name, content in files.items():
        paired.write_selected_bytes(destination/name, content)
    return receipt


def main(argv=None):
    class PublicParser(argparse.ArgumentParser):
        def error(self, message):
            raise paired.Refusal('invalid_arguments')

    parser = PublicParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('draft')
    command.add_argument('proposal')
    command.add_argument('--draft-dir', required=True)
    try:
        args = parser.parse_args(argv)
        source = paired.plain_path(args.proposal)
        proposal = paired.read_json(source)
        for raw in (args.draft_dir, proposal['output']):
            control = paired.plain_path(raw)
            paired.need(source != control and source not in control.parents and control not in source.parents,
                        'control_path_overlap')
        result = draft(proposal, args.draft_dir, now=dt.datetime.now(dt.timezone.utc))
    except paired.Refusal as error:
        result = dict(status='refused', code=str(error))
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        result = dict(status='refused', code='weekly_draft_invalid_or_unavailable')
    print(json.dumps(result, sort_keys=True))
    return 2 if result['status'] == 'refused' else 0


if __name__ == '__main__':
    raise SystemExit(main())
