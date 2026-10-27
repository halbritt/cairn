"""Synthetic-only checks for explicit, live-session grader release issuance."""
import copy
import datetime as dt
import json
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

import trial_paired_request as paired
import trial_blind_packet_draft as blind
import trial_grader_delivery as delivery
import trial_grader_release_issuer as issuer
import pinned_document as pinned
from test_trial_grader_delivery import Case, MODELS, OWNER, ROUTE, config
from test_trial_grader_delivery_plan import Fixture, build

NOW = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc)
OWNER_ID = OWNER.removeprefix('agent/')


class Directory:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.lookups = []

    def one_live(self, agent_id):
        self.lookups.append(agent_id)
        value = self.rows.get(agent_id)
        if value is None:
            raise paired.Refusal('grader_session_not_live')
        return copy.deepcopy(value)


class CliDirectoryTests(unittest.TestCase):
    def test_directory_query_is_exact_read_only_and_returns_only_one_live_entry(self):
        row = dict(agent_id='11111111-1111-4111-8111-111111111111')
        response = dict(schema='cairn.response/1', ok=True, data=dict(agents=[row], more=False))
        calls = []
        def runner(argv, *, capture_output, timeout, check):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, json.dumps(response).encode(), b'')
        directory = issuer.CairnDirectory(cairn='/bin/cairn', token_file='/private/token', socket='/private/api.sock',
                                           repo='/home/owner/git/cairn', runner=runner)
        self.assertEqual(directory.one_live(row['agent_id']), row)
        self.assertEqual(calls, [['/bin/cairn', 'agents', 'list', '--token-file', '/private/token', '--repo',
            '/home/owner/git/cairn', '--agent-id', row['agent_id'], '--limit', '2', '--socket', '/private/api.sock']])

    def test_empty_or_ambiguous_directory_fails_closed(self):
        for agents, more in (([], False), ([{}, {}], False), ([{}], True)):
            response = dict(schema='cairn.response/1', ok=True, data=dict(agents=agents, more=more))
            directory = issuer.CairnDirectory(cairn='/bin/cairn', token_file='/private/token', repo='R',
                runner=lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0,
                    json.dumps(response).encode(), b''))
            with self.assertRaisesRegex(paired.Refusal, 'grader_session_not_live'):
                directory.one_live('11111111-1111-4111-8111-111111111111')


class ReleaseIssuerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fx = Fixture(self.root)
        self.fx.bundle['policy']['graders']['primary'] = dict(agent_id='11111111-1111-4111-8111-111111111111',
            session_id='native-primary')
        self.fx.bundle['policy']['graders']['secondary'] = dict(agent_id='22222222-2222-4222-8222-222222222222',
            session_id='native-secondary')
        from test_trial_grading_custody import seal
        seal(self.fx.bundle)
        self.fx.sessions['binding_sha256'] = paired.canonical_sha(self.fx.bundle['binding'])
        for row in self.fx.sessions['slots']:
            row.update(self.fx.bundle['policy']['graders'][row['slot']])
            row['execution_id'] = ('33333333-3333-4333-8333-333333333333' if row['slot'] == 'primary'
                                   else '44444444-4444-4444-8444-444444444444')
        self.fx.save()
        build(self.fx, self.root/'plan')
        self.config = config()
        self.receipt_bytes = (self.root/'plan/receipt.json').read_bytes()
        self.plan = json.loads((self.root/'plan/plan.json').read_bytes())
        self.plan_sha = pinned.sha256_hex(self.receipt_bytes)
        self.instruction = dict(schema=issuer.INSTRUCTION_SCHEMA, instruction_id=str(uuid.uuid4()),
            selected_at='2026-10-15T11:59:00Z', authorization='Authorize the exact blind grader release.',
            plan_receipt_sha256=self.plan_sha, binding_sha256=self.plan['binding_sha256'],
            issuer=dict(role='owner', agent_id=OWNER_ID, session_id='owner-native-session',
                        execution_id='55555555-5555-4555-8555-555555555555'),
            recipients=[dict(slot=row['slot'], agent_id=row['agent_id'], session_id=row['session_id'],
                execution_id=row['execution_id'], model=MODELS[row['slot']],
                route_sha256=pinned.sha256_hex(delivery._read_pinned(row['route'])), route=dict(ROUTE))
                for row in self.plan['sessions']['slots']], max_request_bytes=32768, valid_for_seconds=180)
        self.instruction_path = self.root/'instruction.json'
        self.write_instruction()
        self.directory = Directory({row['agent_id']: self.live_row(row) for row in self.plan['sessions']['slots']})

    def write_instruction(self):
        self.instruction_path.write_bytes(blind.canonical_bytes(self.instruction))

    def live_row(self, selected):
        return dict(agent_id=selected['agent_id'], execution_id=selected['execution_id'],
            native_session_id=selected['session_id'], repo=self.config['repo'], online=True, stopped=False,
            metadata_source='reported', last_seen='2026-10-15T11:59:30Z', expires_at='2026-10-15T12:01:00Z',
            metadata=dict(model=MODELS[selected['slot']], observed_model=MODELS[selected['slot']],
                state='idle', **ROUTE))

    def issue(self):
        return issuer.issue(self.root/'plan', self.instruction_path, config=self.config,
            directory=self.directory, now=NOW)

    def test_issues_from_selected_owner_instruction_and_binds_plan_packets_routes(self):
        authority = self.issue()
        self.assertEqual(authority['schema'], delivery.AUTHORITY_SCHEMA)
        self.assertEqual(authority['plan_receipt_sha256'], self.plan_sha)
        self.assertEqual(authority['binding_sha256'], self.plan['binding_sha256'])
        self.assertEqual(authority['instruction_sha256'], pinned.sha256_hex(self.instruction_path.read_bytes()))
        self.assertEqual(authority['valid_until'], '2026-10-15T12:03:00Z')
        self.assertEqual(len(authority['packets']), 2)
        self.assertEqual([row['metadata_source'] for row in authority['recipients']], ['reported', 'reported'])
        self.assertEqual([row['model_custody'] for row in authority['recipients']],
                         ['owner_selected_and_reported']*2)
        self.assertEqual([row['declared_route_sha256'] for row in authority['recipients']],
                         [row['route_sha256'] for row in self.instruction['recipients']])
        self.assertEqual(self.directory.lookups, [row['agent_id'] for row in self.plan['sessions']['slots']])

    def test_directory_accepts_host_offset_timestamps(self):
        self.directory = Directory({row['agent_id']: self.live_row(row) for row in self.plan['sessions']['slots']})
        for live in self.directory.rows.values():
            live['last_seen'] = '2026-10-15T04:59:30-07:00'
            live['expires_at'] = '2026-10-15T05:01:00-07:00'
        self.assertEqual(len(self.issue()['recipients']), 2)

    def test_issued_authority_passes_delivery_preflight_and_uses_exact_sessions(self):
        authority = self.issue()
        authority_path = self.root/'authority.json'
        authority_bytes = blind.canonical_bytes(authority)
        authority_path.write_bytes(authority_bytes)
        from test_trial_grader_delivery import FakeFabric
        fabric = FakeFabric()
        result = delivery.dispatch(self.root/'plan', self.root/'delivery-run', fabric=fabric,
            config=self.config, authority=dict(path=str(authority_path), bytes=len(authority_bytes),
                                               sha256=pinned.sha256_hex(authority_bytes)), now=NOW)
        self.assertEqual(len(result['deliveries']), 2)
        self.assertEqual([row for row in fabric.calls if row[0] == 'resolve'],
                         [('resolve', row['agent_id']) for row in authority['recipients']])

    def test_missing_or_stale_presence_refuses_closed(self):
        del self.directory.rows[self.plan['sessions']['slots'][1]['agent_id']]
        with self.assertRaisesRegex(paired.Refusal, 'grader_session_not_live'):
            self.issue()
        self.directory = Directory({row['agent_id']: self.live_row(row) for row in self.plan['sessions']['slots']})
        self.directory.rows[self.plan['sessions']['slots'][0]['agent_id']]['expires_at'] = '2026-10-15T11:59:59Z'
        with self.assertRaisesRegex(paired.Refusal, 'grader_session_not_live'):
            self.issue()

    def test_rejects_duplicate_or_actor_recipient_and_mismatched_model_route(self):
        original = copy.deepcopy(self.instruction)
        self.instruction['recipients'][1] = copy.deepcopy(self.instruction['recipients'][0])
        self.write_instruction()
        with self.assertRaisesRegex(paired.Refusal, 'grader_release_instruction_mismatch|grader_recipient_excluded'):
            self.issue()
        self.instruction = original
        self.instruction['recipients'][0]['route']['project'] = 'actor-project'
        self.write_instruction()
        with self.assertRaisesRegex(paired.Refusal, 'grader_reported_route_mismatch'):
            self.issue()

    def test_refuses_expired_instruction_and_oversized_or_long_lived_release(self):
        self.instruction['selected_at'] = '2026-10-15T11:54:59Z'
        self.write_instruction()
        with self.assertRaisesRegex(paired.Refusal, 'grader_release_instruction_stale'):
            self.issue()
        self.instruction['selected_at'] = '2026-10-15T11:59:00Z'
        self.instruction['valid_for_seconds'] = issuer.MAX_VALIDITY_SECONDS+1
        self.write_instruction()
        with self.assertRaisesRegex(paired.Refusal, 'grader_release_instruction_invalid'):
            self.issue()


if __name__ == '__main__':
    unittest.main()
