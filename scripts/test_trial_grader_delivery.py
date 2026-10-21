"""Grader delivery over durable Cairn events, exercised only against an in-memory fake fabric."""
import copy
import datetime as dt
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import trial_paired_request as paired
import trial_grader_delivery as grader
import trial_grader_delivery_plan as delivery_plan
from test_trial_grader_delivery_plan import Fixture, build
from test_trial_grading_custody import seal

NOW = dt.datetime(2026, 10, 15, 12, tzinfo=dt.timezone.utc)
PRIMARY = '11111111-1111-4111-8111-111111111111'
SECONDARY = '22222222-2222-4222-8222-222222222222'
PUBLISHER = dict(agent_id='33333333-3333-4333-8333-333333333333',
                 execution_id='44444444-4444-4444-8444-444444444444')
OWNER = 'agent/55555555-5555-4555-8555-555555555555'
MODELS = dict(primary='claude-opus-5-5', secondary='gpt-6-astra')


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class FakeFabric:
    """Mimics the store contracts used by the adapter: idempotent request IDs,
    one delivery per direct event, store-assigned writer and body hashes."""

    def __init__(self):
        self.records, self.remembered, self.events, self.published = {}, {}, {}, {}
        self.calls = []
        self.fail_publish = 0

    def remember(self, *, request_id, repo, task, run, body, shareable):
        self.calls.append(('remember', request_id))
        intent = (repo, task, run, body, shareable)
        if request_id in self.remembered:
            prior, ref = self.remembered[request_id]
            if prior != intent: raise paired.Refusal('IDEMPOTENCY_CONFLICT')
            return dict(ref)
        ref = dict(record_id=str(uuid.uuid4()), version=1)
        self.records[(ref['record_id'], 1)] = dict(body=body, writer='coordinator-principal', kind='note')
        self.remembered[request_id] = (intent, ref)
        return dict(ref)

    def publish(self, *, request_id, repo, destination, kind, record_id, version, correlation_id):
        self.calls.append(('publish', request_id))
        if self.fail_publish:
            self.fail_publish -= 1
            raise paired.Refusal('grader_fabric_uncertain')
        intent = (repo, destination, kind, record_id, version, correlation_id)
        if request_id in self.published:
            prior, event_id = self.published[request_id]
            if prior != intent: raise paired.Refusal('IDEMPOTENCY_CONFLICT')
            return dict(event_id=event_id)
        event_id = str(uuid.uuid4())
        self.events[event_id] = dict(event=dict(event_id=event_id, kind=kind, ref=dict(record_id=record_id, version=version),
            destination=dict(type='agent', name=destination), correlation_id=correlation_id),
            deliveries=[dict(delivery_id=str(uuid.uuid4()), consumer=destination, state='pending', attempts=0)],
            more=False)
        self.published[request_id] = (intent, event_id)
        return dict(event_id=event_id)

    def event_status(self, event_id):
        self.calls.append(('event_status', event_id))
        return copy.deepcopy(self.events[event_id])

    def history(self, *, record_id, version):
        self.calls.append(('history', record_id))
        row = self.records[(record_id, version)]
        body = row['body']
        return dict(record_id=record_id, versions=[dict(version=version, kind=row['kind'], payload_available=True,
            body=body, body_sha256=row.get('sha', sha(body)), body_bytes=len(body.encode()),
            observed_writer=row['writer'], witness='store', written_at='2026-10-15T12:30:00Z')])

    # Grader-side simulation of `cairn complete --stdin --shareable`.
    def complete(self, event_id, body, *, state='handled', consumer=None, result=True):
        delivery = self.events[event_id]['deliveries'][0]
        delivery.update(state=state, attempts=1, completed_at='2026-10-15T12:30:00Z')
        if consumer: delivery['consumer'] = consumer
        if result:
            record_id = str(uuid.uuid4())
            self.records[(record_id, 1)] = dict(body=body, writer='grader-principal', kind='note')
            delivery['result'] = dict(record_id=record_id, version=1)
        return delivery

    def request_of(self, event_id):
        ref = self.events[event_id]['event']['ref']
        return json.loads(self.records[(ref['record_id'], ref['version'])]['body'])


def addressable(f):
    """Bind the fixture's grader slots to registered-session UUIDs."""
    for slot, agent in (('primary', PRIMARY), ('secondary', SECONDARY)):
        f.bundle['policy']['graders'][slot] = dict(agent_id=agent, session_id=agent)
    seal(f.bundle)
    f.sessions['binding_sha256'] = paired.canonical_sha(f.bundle['binding'])
    for row in f.sessions['slots']:
        row.update(f.bundle['policy']['graders'][row['slot']])


def config(**changes):
    value = dict(schema='cairn.grader-delivery-config/1', repo='/home/halbritt/git/cairn',
                 owner_destination=OWNER, publisher=dict(PUBLISHER))
    value.update(changes)
    return value


class Case(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(); self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.serial = 0

    def authority(self, **changes):
        """Synthetic stand-in for a separately held release authority; never produced by the adapter."""
        receipt = (self.root/'plan/receipt.json').read_bytes()
        plan = json.loads((self.root/'plan/plan.json').read_bytes())
        doc = dict(schema='cairn.grader-release-authority/1', plan_receipt_sha256=hashlib.sha256(receipt).hexdigest(),
            binding_sha256=plan['binding_sha256'],
            issuer=dict(role='owner', agent_id='owner-agent', session_id='owner-session'),
            observed_at='2026-10-15T11:00:00Z', valid_until='2026-10-15T13:00:00Z',
            recipients=[dict(slot=s['slot'], agent_id=s['agent_id'], session_id=s['session_id'],
                             execution_id=s['execution_id'], model=MODELS[s['slot']]) for s in plan['sessions']['slots']],
            packets=[dict(pair_id=p['pair_id'], slot=d['slot'], packet_sha256=d['packet_sha256'])
                     for p in plan['pairs'] for d in p['deliveries']],
            max_request_bytes=65536)
        doc.update(changes)
        self.serial += 1
        path = self.root/('authority-%d.json' % self.serial); content = json.dumps(doc).encode()
        path.write_bytes(content)
        return dict(path=str(path), bytes=len(content), sha256=hashlib.sha256(content).hexdigest())

    def rewrite_plan(self, mutate):
        plan_path = self.root/'plan/plan.json'; receipt_path = self.root/'plan/receipt.json'
        plan = json.loads(plan_path.read_bytes()); mutate(plan)
        content = json.dumps(plan).encode()
        for path in (plan_path, receipt_path): path.chmod(0o600)
        plan_path.write_bytes(content)
        receipt = json.loads(receipt_path.read_bytes())
        receipt['files']['plan.json'].update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
        receipt_path.write_bytes(json.dumps(receipt).encode())

    def plan(self, *, full=False, uuids=True):
        f = Fixture(self.root)
        if full: f.full_occurrence()
        if uuids: addressable(f)
        build(f, self.root/'plan')
        return f

    def dispatch(self, fabric, run='run', **kwargs):
        authority = kwargs.pop('authority', None) or self.authority()
        return grader.dispatch(self.root/'plan', self.root/run, fabric=fabric, config=kwargs.pop('cfg', config()),
                               authority=authority, now=kwargs.pop('now', NOW), **kwargs)

    def assert_refused_before_effects(self, code, fabric, call):
        with self.assertRaises(paired.Refusal) as caught:
            call()
        self.assertEqual(str(caught.exception), code)
        self.assertRegex(str(caught.exception), r'^[a-z_]+$')
        self.assertEqual(fabric.calls, [])
        self.assertFalse((self.root/'run').exists())

    def answer(self, fabric, receipt, verdicts):
        for row in receipt['deliveries']:
            request = fabric.request_of(row['event_id'])
            labels = verdicts[row['slot']]
            body = json.dumps(dict(schema='cairn.grader-response/1', request_id=request['request_id'],
                pair_id=request['pair_id'], slot=request['slot'], packet_sha256=request['packet_sha256'],
                binding_sha256=request['binding_sha256'], state='determinate',
                verdicts=dict(zip(request['response']['samples'], labels)), reason='synthetic reason'))
            fabric.complete(row['event_id'], body)


class UnconfiguredTests(Case):
    def test_missing_fabric_or_config_refuses_before_any_private_read(self):
        self.plan(); held = self.authority()
        with patch.object(paired, 'read_bytes', side_effect=AssertionError('private read')):
            for kwargs in (dict(fabric=None, config=config(), authority=held),
                           dict(fabric=FakeFabric(), config=None, authority=held),
                           dict(fabric=FakeFabric(), config=config(), authority=None)):
                with self.assertRaisesRegex(paired.Refusal, 'grader_delivery_unavailable'):
                    grader.dispatch(self.root/'plan', self.root/'run', now=NOW, **kwargs)
                with self.assertRaisesRegex(paired.Refusal, 'grader_delivery_unavailable'):
                    delivery_plan.deliver(self.root/'plan', self.root/'run', now=NOW, **kwargs)
                kwargs.pop('authority')
                if None in kwargs.values():
                    with self.assertRaisesRegex(paired.Refusal, 'grader_delivery_unavailable'):
                        grader.collect(self.root/'plan', self.root/'run', now=NOW, **kwargs)
        self.assertFalse((self.root/'run').exists())

    def test_plan_module_deliver_reaches_the_adapter_only_when_fully_configured(self):
        self.plan(); fabric = FakeFabric()
        receipt = delivery_plan.deliver(self.root/'plan', self.root/'run', fabric=fabric, config=config(),
                                        authority=self.authority(), now=NOW)
        self.assertEqual(len(receipt['deliveries']), 2)

    def test_cli_without_configuration_prints_fixed_refusal(self):
        for argv in ([], ['dispatch'], ['dispatch', '--plan', '/nonexistent', '--run', '/x']):
            with redirect_stdout(io.StringIO()) as output:
                self.assertEqual(grader.main(argv), 2)
            self.assertEqual(json.loads(output.getvalue()), dict(status='refused', code='grader_delivery_unavailable'))

    def test_malformed_config_refuses(self):
        self.plan()
        for bad in (config(schema='other'), config(extra=1), config(publisher=dict(agent_id='x', execution_id='y')),
                    config(owner_destination=''), config(publisher=dict(agent_id=PRIMARY, execution_id=PUBLISHER['execution_id']))):
            with self.assertRaises(paired.Refusal):
                self.dispatch(FakeFabric(), cfg=bad)
        self.assertFalse((self.root/'run').exists())

    def test_unaddressable_slot_identity_refuses_before_publication(self):
        self.plan(uuids=False); fabric = FakeFabric()
        with self.assertRaisesRegex(paired.Refusal, 'grader_slot_not_addressable'):
            self.dispatch(fabric)
        self.assertEqual(fabric.calls, [])

    def test_stale_sessions_refuse(self):
        self.plan(); fabric = FakeFabric()
        with self.assertRaisesRegex(paired.Refusal, 'grader_sessions_stale'):
            self.dispatch(fabric, now=NOW + dt.timedelta(hours=2))
        self.assertEqual(fabric.calls, [])

    def test_tampered_plan_file_refuses(self):
        self.plan()
        path = next((self.root/'plan/comparisons').iterdir())
        path.chmod(0o600); path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(paired.Refusal, 'grader_plan_pin_mismatch'):
            self.dispatch(FakeFabric())


class DispatchTests(Case):
    def test_each_pair_and_slot_gets_one_blind_request_to_its_exact_session(self):
        f = self.plan(); fabric = FakeFabric()
        receipt = self.dispatch(fabric)
        plan = json.loads((self.root/'plan/plan.json').read_bytes())
        self.assertEqual([(r['pair_id'], r['slot']) for r in receipt['deliveries']], [('pair-A', 'primary'), ('pair-A', 'secondary')])
        for row, agent in zip(receipt['deliveries'], (PRIMARY, SECONDARY)):
            status = fabric.events[row['event_id']]
            self.assertEqual(status['event']['destination'], dict(type='agent', name='agent/'+agent))
            self.assertEqual(status['event']['kind'], 'request')
            self.assertEqual(status['event']['correlation_id'], receipt['run_id'])
            ref = status['event']['ref']; body = fabric.records[(ref['record_id'], 1)]['body']
            self.assertLessEqual(len(body.encode()), 65536)
            self.assertNotIn('"CURRENT"', body); self.assertNotIn('"CAND"', body)
            self.assertNotIn('run-0', body.replace('run-0-sample', ''))
            request = json.loads(body)
            delivery = next(d for d in plan['pairs'][0]['deliveries'] if d['slot'] == row['slot'])
            self.assertEqual(request['comparison'].encode(), Path(delivery['comparison']['path']).read_bytes())
            self.assertEqual(hashlib.sha256(request['comparison'].encode()).hexdigest(), request['packet_sha256'])
            self.assertEqual(request['response']['samples'], plan['pairs'][0]['samples'])
            self.assertEqual(request['response']['verdict_labels'], f.bundle['policy']['verdict_labels'])
            self.assertEqual(request['request_id'], row['publication_id'])
            self.assertEqual(row['request']['sha256'], sha(body))
        intent, _ = fabric.remembered[receipt['deliveries'][0]['source_request_id']]
        self.assertEqual(intent[1], 'coordination/'+receipt['deliveries'][0]['publication_id'])
        self.assertIs(intent[4], True)
        self.assertEqual(receipt['transport'], 'synthetic_or_unverified')
        self.assertIs(receipt['runtime_admitted'], False)
        self.assertEqual(receipt['qualification'], 'unqualified')

    def test_arm_tokens_in_rendered_request_refuse(self):
        with self.assertRaisesRegex(paired.Refusal, 'grader_request_not_blind'):
            grader._check_blind('{"x":"CAND"}')
        grader._check_blind('{"x":"CANDID"}')
        self.plan()
        self.rewrite_plan(lambda plan: plan['pairs'][0]['samples'].__setitem__(0, 'CAND'))
        fabric = FakeFabric()
        self.assert_refused_before_effects('grader_request_not_blind', fabric, lambda: self.dispatch(fabric))

    def test_request_over_note_bound_refuses_before_publication(self):
        self.plan(); fabric = FakeFabric()
        held = self.authority(max_request_bytes=1000)
        with patch.object(grader, 'NOTE_LIMIT', 1000):
            with self.assertRaisesRegex(paired.Refusal, 'grader_request_exceeds_note_bound'):
                self.dispatch(fabric, authority=held)
        self.assertEqual(fabric.calls, [])

    def test_resume_after_uncertain_publication_reuses_identical_request_ids(self):
        self.plan(); fabric = FakeFabric(); fabric.fail_publish = 1
        with self.assertRaisesRegex(paired.Refusal, 'grader_fabric_uncertain'):
            self.dispatch(fabric)
        first = list(fabric.calls)
        receipt = self.dispatch(fabric)
        again = self.dispatch(fabric)
        self.assertEqual(receipt, again)
        self.assertEqual(len(fabric.events), 2); self.assertEqual(len(fabric.remembered), 2)
        self.assertEqual(first[1], fabric.calls[len(first)+1])  # same publication UUID retried

    def test_existing_run_for_another_plan_or_config_refuses(self):
        self.plan(); fabric = FakeFabric(); self.dispatch(fabric)
        with self.assertRaisesRegex(paired.Refusal, 'grader_run_mismatch'):
            self.dispatch(fabric, cfg=config(owner_destination='agent/'+PUBLISHER['agent_id']))

    def test_full_occurrence_dispatches_126_requests(self):
        self.plan(full=True); fabric = FakeFabric()
        receipt = self.dispatch(fabric)
        self.assertEqual(len(receipt['deliveries']), 126)
        self.assertEqual({fabric.events[r['event_id']]['event']['destination']['name'] for r in receipt['deliveries']},
                         {'agent/'+PRIMARY, 'agent/'+SECONDARY})


class AuthorityTests(Case):
    def test_stale_or_mismatched_authority_refuses_before_effects(self):
        self.plan()
        plan = json.loads((self.root/'plan/plan.json').read_bytes())
        recipients = [dict(slot=s['slot'], agent_id=s['agent_id'], session_id=s['session_id'],
                           execution_id=s['execution_id'], model='m') for s in plan['sessions']['slots']]
        swapped = [dict(r, slot=o['slot']) for r, o in zip(recipients, reversed(recipients))]
        cases = [
            ('grader_authority_stale', dict(valid_until='2026-10-15T11:30:00Z')),
            ('grader_authority_stale', dict(observed_at='2026-10-15T12:30:00Z')),
            ('grader_authority_mismatch', dict(plan_receipt_sha256='0'*64)),
            ('grader_authority_mismatch', dict(binding_sha256='0'*64)),
            ('grader_authority_mismatch', dict(schema='cairn.grader-release-authority/0')),
            ('grader_authority_mismatch', dict(recipients=swapped)),
            ('grader_authority_mismatch', dict(recipients=[dict(recipients[0], execution_id='other'), recipients[1]])),
            ('grader_authority_mismatch', dict(recipients=[dict(recipients[0], model=''), recipients[1]])),
            ('grader_authority_mismatch', dict(packets=[])),
            ('grader_authority_mismatch', dict(packets=[dict(pair_id='pair-A', slot=slot, packet_sha256='0'*64)
                                                        for slot in ('primary', 'secondary')])),
            ('grader_authority_mismatch', dict(issuer=dict(role='engineer', agent_id='x', session_id='y'))),
            ('grader_recipient_excluded', dict(issuer=dict(role='owner', agent_id=PRIMARY, session_id='z'))),
            ('grader_authority_mismatch', dict(max_request_bytes=65537)),
        ]
        for code, changes in cases:
            with self.subTest(code=code, changes=sorted(changes)):
                fabric = FakeFabric()
                self.assert_refused_before_effects(code, fabric,
                    lambda: self.dispatch(fabric, authority=self.authority(**changes)))
        fabric = FakeFabric(); held = self.authority()
        path = Path(held['path']); path.write_bytes(path.read_bytes() + b' ')
        self.assert_refused_before_effects('grader_authority_mismatch', fabric, lambda: self.dispatch(fabric, authority=held))

    def test_excluded_engineer_reviewer_and_coordinator_identities_never_receive_packets(self):
        self.plan()
        original = (self.root/'plan/plan.json').read_bytes(), (self.root/'plan/receipt.json').read_bytes()
        for role, field in (('actor_engineer', 'execution_id'), ('reviewer', 'agent_id'), ('coordinator', 'session_id')):
            with self.subTest(role=role):
                for path, content in zip(('plan/plan.json', 'plan/receipt.json'), original):
                    (self.root/path).chmod(0o600); (self.root/path).write_bytes(content)
                def mutate(plan):
                    row = dict(role=role, agent_id='x', session_id='y', execution_id='z')
                    row[field] = plan['sessions']['slots'][1][field]
                    plan['sessions']['exclusions'].append(row)
                self.rewrite_plan(mutate)
                fabric = FakeFabric()
                self.assert_refused_before_effects('grader_recipient_excluded', fabric, lambda: self.dispatch(fabric))

    def test_rendered_envelope_is_fully_accounted_and_bounded_before_effects(self):
        self.plan(); fabric = FakeFabric()
        receipt = self.dispatch(fabric)
        total = 0
        for row in receipt['deliveries']:
            ref = fabric.events[row['event_id']]['event']['ref']
            body = fabric.records[(ref['record_id'], 1)]['body'].encode()
            self.assertEqual(row['envelope']['rendered_request_bytes'], len(body))
            self.assertEqual(row['request']['bytes'], len(body))
            self.assertEqual(row['envelope']['slot_fit'], 'unknown')  # token envelope: never shown to fit
            request = json.loads(body)
            self.assertEqual(request['recipient'], dict(agent_id=row['agent_id'], session_id=row['session_id'],
                                                        execution_id=row['execution_id'], model=MODELS[row['slot']]))
            total += len(body)
        self.assertEqual(receipt['rendered_request_bytes'], total)
        smallest = min(r['envelope']['rendered_request_bytes'] for r in receipt['deliveries'])
        (self.root/'run').rename(self.root/'old-run')
        fabric = FakeFabric()
        self.assert_refused_before_effects('grader_envelope_over_budget', fabric,
            lambda: self.dispatch(fabric, authority=self.authority(max_request_bytes=smallest - 1)))
        def byte_limit(plan):
            for d in plan['pairs'][0]['deliveries']:
                d['budget'].update(unit='bytes', limit=smallest - 1)
        self.rewrite_plan(byte_limit)
        self.assert_refused_before_effects('grader_envelope_over_budget', fabric, lambda: self.dispatch(fabric))


class CollectTests(Case):
    def collect(self, fabric, **kwargs):
        return grader.collect(self.root/'plan', self.root/'run', fabric=fabric, config=config(), now=NOW, **kwargs)

    def test_agreeing_grades_bind_custody_and_report_agreement(self):
        self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
        self.answer(fabric, receipt, dict(primary=['pass', 'fail'], secondary=['pass', 'fail']))
        report = self.collect(fabric)
        self.assertEqual(report['agreement'], dict(numerator=1, denominator=1, rate=1.0))
        self.assertEqual(report['join']['aggregate']['declared_raw_agreement'], dict(numerator=1, denominator=1, rate=1.0))
        self.assertEqual(report['disagreements'], [])
        self.assertEqual(report['coverage'], dict(planned=2, dispatched=2, graded=2, pending=0, failed=0, invalid=0))
        for row, sent in zip(report['grades'], receipt['deliveries']):
            status = fabric.events[sent['event_id']]
            self.assertEqual(row['delivery_id'], status['deliveries'][0]['delivery_id'])
            self.assertEqual(row['consumer'], status['event']['destination']['name'])
            self.assertEqual(row['request'], sent['request'])
            body = fabric.records[(row['result']['record_id'], 1)]['body']
            self.assertEqual(row['result']['body_sha256'], sha(body))
            self.assertEqual(row['result']['observed_writer'], 'grader-principal')
            self.assertEqual(row['custody'], 'store_delivery_bound')
            self.assertEqual(row['execution_custody'], 'unverified')
        self.assertTrue((self.root/'run/grades/pair-A-primary.json').exists())

    def test_disagreement_is_routed_to_owner_and_never_resolved(self):
        self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
        self.answer(fabric, receipt, dict(primary=['pass', 'fail'], secondary=['fail', 'fail']))
        report = self.collect(fabric)
        self.assertEqual(report['agreement'], dict(numerator=0, denominator=1, rate=0.0))
        self.assertEqual([d['pair_id'] for d in report['disagreements']], ['pair-A'])
        joined = next(p for p in report['join']['pairs'] if p.get('declared_comparison') == 'disagreement')
        self.assertEqual((joined['resolution'], joined['disposition_state']), ('unresolved', 'missing'))
        self.assertEqual(report['disagreements'][0]['resolution'], 'unresolved')
        routed = grader.route_disagreements(self.root/'plan', self.root/'run', fabric=fabric, config=config(),
                                            now=NOW)
        self.assertEqual(len(routed['disputes']), 1)
        event = fabric.events[routed['disputes'][0]['event_id']]
        self.assertEqual(event['event']['destination']['name'], OWNER)
        dispute = fabric.request_of(event['event']['event_id'])
        self.assertEqual(dispute['schema'], 'cairn.grader-dispute/1')
        self.assertEqual(dispute['resolution'], 'owner_decision_required')
        self.assertEqual(set(dispute['grades']), {'primary', 'secondary'})
        self.assertNotIn('"CURRENT"', json.dumps(dispute)); self.assertNotIn('"CAND"', json.dumps(dispute))
        self.assertEqual(routed['resolution'], 'unresolved')
        again = grader.route_disagreements(self.root/'plan', self.root/'run', fabric=fabric, config=config(),
                                           now=NOW)
        self.assertEqual(again, routed); self.assertEqual(len(fabric.events), 3)

    def test_pending_failed_and_invalid_grades_are_never_republished(self):
        self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
        report = self.collect(fabric)
        self.assertEqual(report['coverage']['pending'], 2)
        self.assertEqual(report['agreement'], dict(numerator=0, denominator=0, rate=None))
        primary, secondary = receipt['deliveries']
        fabric.complete(primary['event_id'], '', state='failed', result=False)
        fabric.complete(secondary['event_id'], json.dumps(dict(schema='cairn.grader-response/1')))
        published = sum(1 for c in fabric.calls if c[0] == 'publish')
        report = self.collect(fabric)
        self.assertEqual((report['coverage']['failed'], report['coverage']['invalid']), (1, 1))
        self.assertEqual(sum(1 for c in fabric.calls if c[0] == 'publish'), published)
        self.assertEqual(report['agreement']['denominator'], 0)

    def test_echoed_identity_mismatch_is_invalid_not_a_grade(self):
        self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
        self.answer(fabric, receipt, dict(primary=['pass', 'pass'], secondary=['pass', 'pass']))
        ref = fabric.events[receipt['deliveries'][1]['event_id']]['deliveries'][0]['result']
        row = fabric.records[(ref['record_id'], 1)]
        body = json.loads(row['body']); body['slot'] = 'primary'; row['body'] = json.dumps(body)
        report = self.collect(fabric)
        self.assertEqual(report['coverage']['invalid'], 1)
        self.assertEqual(report['agreement']['denominator'], 0)

    def test_wrong_consumer_or_store_hash_mismatch_refuses_custody(self):
        self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
        self.answer(fabric, receipt, dict(primary=['pass', 'pass'], secondary=['pass', 'pass']))
        delivery = fabric.events[receipt['deliveries'][0]['event_id']]['deliveries'][0]
        delivery['consumer'] = 'agent/'+SECONDARY
        with self.assertRaisesRegex(paired.Refusal, 'grade_custody_mismatch'):
            self.collect(fabric)
        delivery['consumer'] = 'agent/'+PRIMARY
        fabric.records[(delivery['result']['record_id'], 1)]['sha'] = '0'*64
        with self.assertRaisesRegex(paired.Refusal, 'grade_custody_mismatch'):
            self.collect(fabric)

    def test_terminal_custody_is_immutable_across_collections(self):
        self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
        self.answer(fabric, receipt, dict(primary=['pass', 'pass'], secondary=['pass', 'pass']))
        first = self.collect(fabric)
        self.assertEqual(self.collect(fabric), first)
        delivery = fabric.events[receipt['deliveries'][0]['event_id']]['deliveries'][0]
        fabric.records[(delivery['result']['record_id'], 1)]['writer'] = 'someone-else'
        with self.assertRaisesRegex(paired.Refusal, 'grade_custody_changed'):
            self.collect(fabric)

    def test_delivery_is_one_shot_across_run_directories_and_duplicates_refuse(self):
        self.plan(); fabric = FakeFabric()
        first = self.dispatch(fabric)
        second = self.dispatch(fabric, run='other-run')
        self.assertEqual([r['event_id'] for r in first['deliveries']], [r['event_id'] for r in second['deliveries']])
        self.assertEqual(len(fabric.events), 2)
        status = fabric.events[first['deliveries'][0]['event_id']]
        status['deliveries'].append(dict(status['deliveries'][0], delivery_id=str(uuid.uuid4())))
        with self.assertRaisesRegex(paired.Refusal, 'grade_duplicate_delivery'):
            self.collect(fabric)

    def test_malformed_returns_are_retained_as_invalid_and_never_count(self):
        bodies = ['not json', '{"a":1,"a":2}', '[]', json.dumps(dict(schema='cairn.grader-response/1')),
                  'NaN']
        for body in bodies:
            with self.subTest(body=body):
                self.setUp(); self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
                self.answer(fabric, receipt, dict(primary=['pass', 'pass'], secondary=['pass', 'pass']))
                ref = fabric.events[receipt['deliveries'][0]['event_id']]['deliveries'][0]['result']
                fabric.records[(ref['record_id'], 1)]['body'] = body
                report = self.collect(fabric)
                self.assertEqual(report['coverage']['invalid'], 1)
                self.assertEqual(report['agreement']['denominator'], 0)
                self.assertEqual(report['grades'][0]['result']['body_sha256'], sha(body))
        self.setUp(); self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
        request = fabric.request_of(receipt['deliveries'][0]['event_id'])
        for change in (dict(verdicts={'opaque-A': 'pass'}), dict(verdicts={'opaque-A': 'maybe', 'opaque-B': 'pass'}),
                       dict(state='undetermined'), dict(reason='x'*16385), dict(extra=1),
                       dict(reason='\ud800'), dict(state=[]), dict(pair_id={}), dict(verdicts=[])):
            body = dict(schema='cairn.grader-response/1', request_id=request['request_id'], pair_id='pair-A',
                        slot='primary', packet_sha256=request['packet_sha256'], binding_sha256=request['binding_sha256'],
                        state='determinate', verdicts={'opaque-A': 'pass', 'opaque-B': 'pass'}, reason='r')
            body.update(change)
            self.assertIsNone(grader._response(json.dumps(body), dict(publication_id=request['request_id']),
                dict(pair_id='pair-A', samples=['opaque-B', 'opaque-A']),
                dict(slot='primary', packet_sha256=request['packet_sha256']),
                dict(binding_sha256=request['binding_sha256']), {'pass', 'fail'}))

    def test_unhashable_or_deeply_nested_returns_are_invalid_without_refusing_other_grades(self):
        def journal():
            return {str(p.relative_to(self.root/'run')): p.read_bytes()
                    for p in sorted((self.root/'run').rglob('*')) if p.is_file()}
        malformed = {'list': lambda body: body['verdicts'].__setitem__('opaque-A', []),
                     'object': lambda body: body['verdicts'].__setitem__('opaque-B', {}),
                     'nested': None}
        for name, change in malformed.items():
            with self.subTest(verdict=name):
                self.setUp(); self.plan(); fabric = FakeFabric(); receipt = self.dispatch(fabric)
                self.answer(fabric, receipt, dict(primary=['pass', 'pass'], secondary=['pass', 'pass']))
                ref = fabric.events[receipt['deliveries'][0]['event_id']]['deliveries'][0]['result']
                row = fabric.records[(ref['record_id'], 1)]
                if change is None:
                    row['body'] = '[' * 30000 + ']' * 30000  # fits one note, exceeds the JSON nesting limit
                else:
                    body = json.loads(row['body']); change(body); row['body'] = json.dumps(body)
                published = [c for c in fabric.calls if c[0] in ('remember', 'publish')]
                report = self.collect(fabric)
                self.assertEqual([g['state'] for g in report['grades']], ['invalid', 'graded'])
                self.assertEqual(report['grades'][0]['result']['body_sha256'], sha(row['body']))
                self.assertEqual(report['grades'][1]['verdicts'], {'opaque-A': 'pass', 'opaque-B': 'pass'})
                self.assertEqual((report['coverage']['invalid'], report['coverage']['graded']), (1, 1))
                self.assertEqual(report['agreement']['denominator'], 0)
                before = journal()
                self.assertEqual(self.collect(fabric), report)
                self.assertEqual(journal(), before)
                self.assertEqual([c for c in fabric.calls if c[0] in ('remember', 'publish')], published)
                routed = grader.route_disagreements(self.root/'plan', self.root/'run', fabric=fabric, config=config(), now=NOW)
                self.assertEqual(routed['disputes'], [])
                self.assertEqual([c for c in fabric.calls if c[0] in ('remember', 'publish')], published)

    def test_collect_before_dispatch_refuses(self):
        self.plan()
        with self.assertRaisesRegex(paired.Refusal, 'grader_run_missing'):
            self.collect(FakeFabric())


class CliFabricTests(unittest.TestCase):
    def fabric(self, responses):
        calls = []
        def runner(argv, *, input, capture_output, timeout, check):
            calls.append((argv, input))
            data = responses.pop(0)
            return subprocess.CompletedProcess(argv, 0 if data.get('ok') else 1, json.dumps(data).encode(), b'')
        return grader.CliFabric(cairn='/usr/bin/cairn', token_file='/t/token', socket=None,
                                publisher=PUBLISHER, runner=runner), calls

    def test_operations_use_existing_agent_cli_contracts(self):
        ok = lambda data: dict(schema='cairn.response/1', ok=True, status='OK', data=data)
        fabric, calls = self.fabric([ok(dict(record_id='r', version=1)), ok(dict(event_id='e')),
                                     ok(dict(event={}, deliveries=[])), ok(dict(versions=[]))])
        base = ['/usr/bin/cairn', 'agent', '--token-file', '/t/token', '--agent-id', PUBLISHER['agent_id'],
                '--execution-id', PUBLISHER['execution_id']]
        self.assertEqual(fabric.remember(request_id='q', repo='R', task='T', run='*', body='B', shareable=True),
                         dict(record_id='r', version=1))
        self.assertEqual(calls[-1], (base + ['remember', '--request-id', 'q', '--repo', 'R', '--task', 'T', '--run', '*',
                                             '--kind', 'note', '--shareable', '--stdin'], b'B'))
        fabric.publish(request_id='p', repo='R', destination='agent/x', kind='request', record_id='r', version=1,
                       correlation_id='c')
        self.assertEqual(calls[-1][0], base + ['publish', '--request-id', 'p', '--repo', 'R', '--to', 'agent/x', '--kind',
                                               'request', '--version', '1', '--correlation-id', 'c', 'r'])
        fabric.event_status('e')
        self.assertEqual(calls[-1][0], base + ['event-status', 'e'])
        fabric.history(record_id='r', version=1)
        self.assertEqual(calls[-1], (base + ['history'], b'{"record_id":"r","version":1}'))

    def test_refused_or_unparseable_responses_refuse(self):
        fabric, _ = self.fabric([dict(schema='cairn.response/1', ok=False, status='INVALID_REQUEST')])
        with self.assertRaisesRegex(paired.Refusal, 'grader_fabric_refused'):
            fabric.event_status('e')
        def timeout(argv, **kwargs): raise subprocess.TimeoutExpired(argv, 1)
        fabric = grader.CliFabric(cairn='/usr/bin/cairn', token_file='/t', socket=None, publisher=PUBLISHER, runner=timeout)
        with self.assertRaisesRegex(paired.Refusal, 'grader_fabric_uncertain'):
            fabric.event_status('e')


if __name__ == '__main__':
    unittest.main()
