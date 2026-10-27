"""Issue a short-lived, plan-bound grader release after explicit owner selection.

This private operator tool reads the blind plan and current Cairn session
directory. Session model and route fields remain reported metadata. It makes no
provider calls and publishes no events.
"""
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import trial_paired_request as paired
import pinned_document as pinned
import trial_blind_packet_draft as blind
import trial_grader_delivery as delivery

INSTRUCTION_SCHEMA = 'cairn.grader-release-instruction/1'
AUTHORITY_SCHEMA = delivery.AUTHORITY_SCHEMA
MAX_VALIDITY_SECONDS = 300
ROUTE_FIELDS = ('harness', 'project', 'workspace', 'delivery_mode')


def _refuse(condition, code):
    paired.need(condition, code)


def _instruction(value, plan_sha, plan, config, now):
    paired.shape(value, 'schema instruction_id selected_at authorization plan_receipt_sha256 binding_sha256 '
                 'issuer recipients max_request_bytes valid_for_seconds', 'grader_release_instruction_invalid')
    paired.need(value['schema'] == INSTRUCTION_SCHEMA and value['plan_receipt_sha256'] == plan_sha and
                value['binding_sha256'] == plan['binding_sha256'], 'grader_release_instruction_mismatch')
    delivery._uuid(value['instruction_id'], 'grader_release_instruction_invalid')
    paired.need(paired.utc(value['selected_at']) <= now < paired.utc(value['selected_at']) +
                dt.timedelta(seconds=MAX_VALIDITY_SECONDS), 'grader_release_instruction_stale')
    delivery._text(value['authorization'], 'grader_release_instruction_invalid', 512)
    issuer = value['issuer']
    paired.shape(issuer, 'role agent_id session_id execution_id', 'grader_release_instruction_invalid')
    paired.need(issuer['role'] in ('owner', 'coordinator'), 'grader_release_instruction_invalid')
    for field in ('agent_id', 'execution_id'):
        delivery._uuid(issuer[field], 'grader_release_instruction_invalid')
    delivery._text(issuer['session_id'], 'grader_release_instruction_invalid', 256)
    sessions = plan['sessions']
    excluded = {key: {row[key] for row in sessions['exclusions']} for key in
                ('agent_id', 'session_id', 'execution_id')}
    if issuer['role'] == 'coordinator':
        paired.need(issuer['agent_id'] == config['publisher']['agent_id'] and
                    issuer['execution_id'] == config['publisher']['execution_id'] and
                    any(row['role'] == 'coordinator' and all(issuer[k] == row[k] for k in excluded)
                        for row in sessions['exclusions']), 'grader_release_issuer_mismatch')
    else:
        owner = config['owner_destination']
        paired.need(owner.startswith('agent/') and owner[6:] == issuer['agent_id'],
                    'grader_release_issuer_mismatch')
    paired.need(all(issuer[k] not in excluded[k] or issuer['role'] == 'coordinator'
                    for k in ('agent_id', 'session_id', 'execution_id')),
                'grader_release_issuer_mismatch')
    limit, validity = value['max_request_bytes'], value['valid_for_seconds']
    paired.need(type(limit) is int and 0 < limit <= delivery.NOTE_LIMIT and
                type(validity) is int and 1 <= validity <= MAX_VALIDITY_SECONDS,
                'grader_release_instruction_invalid')
    recipients = value['recipients']
    paired.need(type(recipients) is list and len(recipients) == 2, 'grader_release_instruction_invalid')
    expected = plan['sessions']['slots']
    seen = {field: set() for field in ('agent_id', 'session_id', 'execution_id')}
    for want, got in zip(expected, recipients):
        paired.shape(got, 'slot agent_id session_id execution_id model route_sha256 route',
                     'grader_release_instruction_invalid')
        route = got['route']
        paired.shape(route, 'harness project workspace delivery_mode', 'grader_release_instruction_invalid')
        paired.need(got['slot'] == want['slot'] and all(got[k] == want[k] for k in
                    ('agent_id', 'session_id', 'execution_id')), 'grader_release_instruction_mismatch')
        delivery._uuid(got['agent_id'], 'grader_release_instruction_invalid')
        delivery._uuid(got['execution_id'], 'grader_release_instruction_invalid')
        delivery._text(got['session_id'], 'grader_release_instruction_invalid', 256)
        declared_route = pinned.sha256_hex(delivery._read_pinned(want['route']))
        paired.need(got['route_sha256'] == declared_route, 'grader_release_instruction_mismatch')
        delivery._text(got['model'], 'grader_release_instruction_invalid', 128)
        for field in ROUTE_FIELDS:
            delivery._text(route[field], 'grader_release_instruction_invalid', 4096)
        for key in seen:
            paired.need(got[key] not in excluded[key] and got[key] not in seen[key] and
                        got[key] != issuer.get(key), 'grader_recipient_excluded')
            seen[key].add(got[key])
    paired.need(len(recipients) == len(expected), 'grader_release_instruction_mismatch')
    return value


class CairnDirectory:
    """Read the current, exact Cairn session directory without claiming inboxes."""

    def __init__(self, *, cairn, token_file, socket=None, repo, runner=subprocess.run, timeout=15):
        self.cairn, self.token_file, self.socket = cairn, token_file, socket
        self.repo, self.runner, self.timeout = repo, runner, timeout

    def one_live(self, agent_id):
        argv = [self.cairn, 'agents', 'list', '--token-file', self.token_file, '--repo', self.repo,
                '--agent-id', agent_id, '--limit', '2']
        if self.socket:
            argv += ['--socket', self.socket]
        try:
            done = self.runner(argv, capture_output=True, timeout=self.timeout, check=False)
        except (OSError, subprocess.TimeoutExpired):
            raise paired.Refusal('grader_directory_unavailable') from None
        try:
            envelope = json.loads(done.stdout)
        except (ValueError, TypeError):
            raise paired.Refusal('grader_directory_unavailable') from None
        paired.need(done.returncode == 0 and isinstance(envelope, dict) and
                    envelope.get('schema') == 'cairn.response/1' and envelope.get('ok') is True,
                    'grader_directory_unavailable')
        data = envelope.get('data')
        paired.need(isinstance(data, dict) and type(data.get('agents')) is list and
                    data.get('more') is False and len(data['agents']) == 1,
                    'grader_session_not_live')
        return data['agents'][0]


def _check_presence(directory, recipient, repo, now):
    row = directory.one_live(recipient['agent_id'])
    paired.need(row.get('agent_id') == recipient['agent_id'] and
                row.get('execution_id') == recipient['execution_id'] and
                row.get('native_session_id') == recipient['session_id'] and row.get('repo') == repo and
                row.get('online') is True and row.get('stopped') is False and
                row.get('metadata_source') == 'reported', 'grader_session_not_live')
    try:
        # Cairn directory timestamps may use the host's non-UTC offset.
        # Release instructions and plan timestamps retain their UTC-only contract.
        def directory_time(value):
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            paired.need(parsed.tzinfo is not None and parsed.utcoffset() is not None,
                        'grader_session_not_live')
            return parsed.astimezone(dt.timezone.utc)

        last_seen, expires = directory_time(row['last_seen']), directory_time(row['expires_at'])
        paired.need(last_seen <= now and now - last_seen <= dt.timedelta(seconds=90) and now < expires,
                    'grader_session_not_live')
    except (AttributeError, KeyError, TypeError, ValueError):
        raise paired.Refusal('grader_session_not_live') from None
    metadata = row.get('metadata')
    paired.need(isinstance(metadata, dict) and metadata.get('model') == recipient['model'] and
                metadata.get('state') == 'idle' and metadata.get('harness') == recipient['route']['harness'] and
                metadata.get('project') == recipient['route']['project'] and
                metadata.get('workspace') == recipient['route']['workspace'] and
                metadata.get('delivery_mode') == recipient['route']['delivery_mode'],
                'grader_reported_route_mismatch')
    observed = metadata.get('observed_model')
    paired.need(observed in (None, '', recipient['model']), 'grader_reported_route_mismatch')
    return dict(agent_id=row['agent_id'], execution_id=row['execution_id'], session_id=row['native_session_id'],
                model=recipient['model'], route={key: metadata[key] for key in ROUTE_FIELDS},
                metadata_source='reported', model_custody='owner_selected_and_reported',
                observed_at=now.isoformat().replace('+00:00', 'Z'))


def issue(plan_dir, instruction_path, *, config, directory, now):
    """Create an authority from an explicitly supplied owner instruction."""
    delivery._check_config(config)
    now = now.astimezone(dt.timezone.utc)
    plan_dir = paired.plain_path(str(plan_dir))
    receipt_bytes = paired.read_bytes(plan_dir/'receipt.json')
    plan, _, rows, plan_sha, _ = delivery._load_plan(plan_dir, config, now)
    _refuse(pinned.sha256_hex(receipt_bytes) == plan_sha, 'grader_plan_pin_mismatch')
    instruction_bytes = paired.read_bytes(instruction_path)
    instruction = blind._json(instruction_bytes)
    selected = _instruction(instruction, plan_sha, plan, config, now)
    recipients = []
    for row in selected['recipients']:
        recipients.append(_check_presence(directory, row, config['repo'], now))
    authority = dict(schema=AUTHORITY_SCHEMA, plan_receipt_sha256=plan_sha,
        binding_sha256=plan['binding_sha256'], instruction_sha256=pinned.sha256_hex(instruction_bytes),
        issuer={key: selected['issuer'][key] for key in ('role', 'agent_id', 'session_id')},
        observed_at=now.isoformat().replace('+00:00', 'Z'),
        valid_until=(now+dt.timedelta(seconds=selected['valid_for_seconds'])).isoformat().replace('+00:00', 'Z'),
        recipients=[dict(slot=source['slot'], agent_id=live['agent_id'], session_id=live['session_id'],
                         execution_id=live['execution_id'], model=live['model'], route=live['route'],
                         declared_route_sha256=source['route_sha256'],
                         metadata_source=live['metadata_source'], model_custody=live['model_custody'])
                    for source, live in zip(selected['recipients'], recipients)],
        packets=sorted((dict(pair_id=pair['pair_id'], slot=delivery_row['slot'],
                             packet_sha256=delivery_row['packet_sha256']) for pair, delivery_row, _ in rows),
                       key=lambda row: (row['pair_id'], row['slot'])),
        max_request_bytes=selected['max_request_bytes'])
    return authority


def main(argv=None):
    """Private CLI: issue --plan DIR --instruction FILE --config FILE --output FILE."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        paired.need(len(argv) == 9 and argv[0] == 'issue' and argv[1::2] ==
                    ['--plan', '--instruction', '--config', '--output'], 'grader_release_issuer_unavailable')
        plan_dir, instruction_path, config_path, output_path = (Path(argv[index]) for index in (2, 4, 6, 8))
        cfg = paired.read_json(config_path)
        paired.shape(cfg, 'delivery cli', 'grader_release_issuer_unavailable')
        paired.shape(cfg['cli'], 'cairn token_file socket', 'grader_release_issuer_unavailable')
        now = dt.datetime.now(dt.timezone.utc)
        directory = CairnDirectory(repo=cfg['delivery']['repo'], **cfg['cli'])
        authority = issue(plan_dir, instruction_path, config=cfg['delivery'], directory=directory, now=now)
        encoded = blind.canonical_bytes(authority)
        paired.write_selected_bytes(output_path, encoded)
        print(json.dumps(dict(path=str(output_path), bytes=len(encoded), sha256=pinned.sha256_hex(encoded)), sort_keys=True))
    except paired.Refusal as refusal:
        print(json.dumps(dict(status='refused', code=str(refusal))))
        return 2
    except (KeyError, TypeError, OSError, ValueError):
        print(json.dumps(dict(status='refused', code='grader_release_issuer_unavailable')))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
