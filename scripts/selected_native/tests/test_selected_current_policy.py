"""first-fitting-whole/2 support in a reviewer-pinned current profile, with fixtures emitted by the current source.

The wire texts come from the Go generator in go-fixture-generator/ (presentSearch + toolResult of the
current mcpapi package and the unexported budget functions of core, run on git-archive snapshots of main
554037b and installed-build b5ed072, offline, no database). Python fixtures marked DERIVED change one
field of such a text. Nothing here starts a native child, provider, database or service.
"""
import copy
from pathlib import Path
import json
import re
import sys
import unittest

sys.dont_write_bytecode = True

from .. import evidence
from .. import frames
from ..evidence import (ACCEPTED_POLICIES, CURRENT_READ_POLICIES, EvidenceError, Ledger, POLICY_1, POLICY_2, PULL, SEARCH,
                      inspection_budget)
from ..frames import FrameError, Frames, PINNED_BUDGETS, Profile
from . import test_selected_coding as t

FIX = str(Path(__file__).resolve().parent / 'testdata') + '/'
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
CORE = json.loads(Path(FIX + 'core-budget.main-554037b.json').read_text())
WIRE = json.loads(Path(FIX + 'mcp-wire.main-554037b.json').read_text())
SEARCH_ID = '40000000-0000-4000-8000-000000000001'
RECEIPT = '10000000-0000-4000-8000-000000000001'
HANDLE = '30000000-0000-4000-8000-000000000001'


def text(name):
    return WIRE[name]['text']


def request(policy=None, budget=9500, **extra):
    args = {'request_id': SEARCH_ID, 'query': 'q', 'semantic': True}
    if budget is not None:
        args['memory_budget_bytes'] = budget
    if policy:
        args['inspection_policy'] = policy
    args.update(extra)
    return args


def searched(policies, name, **req):
    ledger = Ledger(policies)
    ledger.request('s', SEARCH, request(**req))
    ledger.response('s', [{'type': 'text', 'text': text(name)}])
    return ledger


def derived(name, edit):
    value = json.loads(text(name))
    edit(value)
    return json.dumps(value)


def refusal(callable_):
    with unittest.TestCase().assertRaises((EvidenceError, FrameError)) as caught:
        callable_()
    return str(caught.exception)


class SourceFacts(unittest.TestCase):
    def test_guard_both_builds_emit_identical_wire_and_budget_facts(self):
        norm = lambda s: UUID.sub('<uuid>', s)
        for stem in ('core-budget', 'mcp-wire'):
            main = Path(FIX + stem + '.main-554037b.json').read_text()
            installed = Path(FIX + stem + '.b5ed072.json').read_text()
            self.assertEqual(norm(main), norm(installed), stem)

    def test_guard_source_rules_the_ledger_mirror_encodes(self):
        limit = {k: v['expansion_limit'] for k, v in CORE.items() if k != 'expansion_memory_cost_sample'}
        self.assertEqual(limit['policy2_ready_32000_reserve_over_24000'], 32000)    # /2 expands within the declared room
        self.assertEqual(limit['policy1_ready_32000_reserve_over_24000'], 24000)    # /1 keeps the legacy ceiling
        self.assertEqual(CORE['policy2_ready_32000_reserve_over_24000']['validate_error'], '')
        self.assertTrue(CORE['policy1_ready_32000_reserve_over_24000']['validate_error'])
        self.assertTrue(CORE['policy2_declared_under_schema3']['validate_error'])
        self.assertTrue(CORE['policy1_declared_under_schema4']['validate_error'])
        self.assertTrue(CORE['policy2_reserve_over_budget']['validate_error'])

    def test_fix_ledger_mirror_agrees_with_the_go_validator_on_every_scenario(self):
        entries = lambda n: [{}] * n
        for name, row in CORE.items():
            if name == 'expansion_memory_cost_sample':
                continue
            budget = row['memory_budget']
            policy = budget.get('inspection_policy', '')
            count = 1 if policy and budget.get('inspection_status') == 'ready' else (2 if name.startswith('pilot') else 0)
            go_accepts = row['validate_error'] == ''
            try:
                inspection_budget(policy, budget, entries(count))
                mirror_accepts = True
            except EvidenceError:
                mirror_accepts = False
            with self.subTest(scenario=name):
                self.assertEqual(mirror_accepts, go_accepts)


class LedgerPolicy(unittest.TestCase):
    def test_fix_accepted_reader_still_refuses_the_declaration(self):
        self.assertEqual(refusal(lambda: Ledger().request('s', SEARCH, request(POLICY_2))), 'inspection_policy_shape')
        self.assertEqual(refusal(lambda: Ledger(ACCEPTED_POLICIES).request('s', SEARCH, request(POLICY_2))), 'inspection_policy_shape')
        Ledger().request('s', SEARCH, request(POLICY_1))                      # /1 stays accepted

    def test_fix_pinned_ledger_accepts_the_declaration_and_the_current_source_response(self):
        for name, policy in [('search_policy2_ready_9500', POLICY_2), ('search_policy1_ready', POLICY_1),
                             ('search_pilot_budget2', None)]:
            with self.subTest(response=name):
                ledger = searched(CURRENT_READ_POLICIES, name, policy=policy)
                stored = ledger.selected()['responses']['s']
                self.assertEqual(stored['memory_budget'].get('inspection_policy'), policy)
                self.assertEqual(stored['source_schema'], 'cairn.semantic/20')
                self.assertEqual(stored['credits_remaining'], 4)
        for name in ('search_policy2_no_whole_fits', 'search_policy2_no_candidates'):
            ledger = searched(CURRENT_READ_POLICIES, name, policy=POLICY_2)
            self.assertEqual(ledger.selected()['responses']['s']['index'], [])

    def test_fix_declared_room_raises_only_the_remaining_ceiling_to_the_32000_tool_room(self):
        ledger = searched(CURRENT_READ_POLICIES, 'search_policy2_ready_32000', policy=POLICY_2, budget=32000)
        self.assertEqual(ledger.selected()['responses']['s']['bytes_remaining'], 26500)
        over = derived('search_policy1_ready', lambda v: v.update(bytes_remaining=26500) or v['memory_budget'].update(bytes=32000))   # DERIVED
        led = Ledger(CURRENT_READ_POLICIES)
        led.request('s', SEARCH, request(POLICY_1, budget=32000))
        self.assertEqual(refusal(lambda: led.response('s', over)), 'integer_out_of_range')                     # /1 keeps 24000
        too_big = derived('search_policy2_ready_32000', lambda v: v.update(bytes_remaining=32001))             # DERIVED
        led = Ledger(CURRENT_READ_POLICIES)
        led.request('s', SEARCH, request(POLICY_2, budget=32000))
        self.assertEqual(refusal(lambda: led.response('s', too_big)), 'integer_out_of_range')

    def test_fix_pull_ceiling_follows_the_receipts_declared_policy(self):
        pull_args = {'request_id': '40000000-0000-4000-8000-000000000002', 'receipt_id': RECEIPT, 'handle': HANDLE}
        big_pull = derived('pull_whole', lambda v: v.update(bytes_remaining=25000))                            # DERIVED
        led = searched(CURRENT_READ_POLICIES, 'search_policy2_ready_32000', policy=POLICY_2, budget=32000)
        led.request('p', PULL, pull_args)
        led.response('p', [{'type': 'text', 'text': big_pull}])
        self.assertEqual(led.selected()['responses']['p']['bytes_remaining'], 25000)
        led = searched(CURRENT_READ_POLICIES, 'search_policy1_ready', policy=POLICY_1)
        led.request('p', PULL, pull_args)
        self.assertEqual(refusal(lambda: led.response('p', [{'type': 'text', 'text': big_pull}])), 'integer_out_of_range')
        # the whole body of the real pull fixture is still read as before
        led = searched(CURRENT_READ_POLICIES, 'search_policy2_ready_9500', policy=POLICY_2)
        led.request('p', PULL, pull_args)
        led.response('p', [{'type': 'text', 'text': text('pull_whole')}])
        self.assertEqual(led.selected()['responses']['p']['source']['coverage'], 'whole_returned_body')

    def test_fix_response_must_match_the_declared_policy_schema_and_room(self):
        cases = [
            ('request without policy, /2 response', None, 'search_policy2_ready_9500', 9500, None, 'inspection_policy_response_mismatch'),
            ('request /1, /2 response', POLICY_1, 'search_policy2_ready_9500', 9500, None, 'inspection_policy_response_mismatch'),
            ('request /2, pilot response', POLICY_2, 'search_pilot_budget2', 9500, None, 'inspection_policy_response_mismatch'),
            ('/2 policy under schema/3', POLICY_2, 'search_policy2_ready_9500', 9500,
             lambda v: v['memory_budget'].update(schema='cairn.memory-budget/3'), 'memory_budget_policy_schema'),
            ('/2 reserve above the declared room', POLICY_2, 'search_policy2_ready_9500', 9500,
             lambda v: v['memory_budget'].update(min_pull_bytes=9501), 'memory_budget_inspection'),
            ('ready without index entries', POLICY_2, 'search_policy2_ready_9500', 9500,
             lambda v: v.update(index=[]), 'memory_budget_inspection'),
            ('unknown inspection status', POLICY_2, 'search_policy2_ready_9500', 9500,
             lambda v: v['memory_budget'].update(inspection_status='other'), 'memory_budget_inspection'),
            ('response room differs from the request', POLICY_2, 'search_policy2_ready_9500', 9000, None, 'memory_budget_response_mismatch'),
            ('response room above the 32000 tool ceiling', POLICY_2, 'search_policy2_ready_32000', 32000,
             lambda v: v['memory_budget'].update(bytes=32001), 'memory_budget_shape'),
        ]
        for label, policy, name, budget, edit, code in cases:
            led = Ledger(CURRENT_READ_POLICIES)
            led.request('s', SEARCH, request(policy, budget=budget))
            body = text(name) if edit is None else derived(name, edit)                                            # DERIVED when edited
            with self.subTest(case=label):
                self.assertEqual(refusal(lambda: led.response('s', [{'type': 'text', 'text': body}])), code)

    def test_fix_declaration_does_not_generalise_to_unbounded_room_credits_or_new_policies(self):
        self.assertEqual(refusal(lambda: Ledger(CURRENT_READ_POLICIES).request('s', SEARCH, request('first-fitting-whole/3'))),
                         'inspection_policy_shape')
        self.assertEqual(refusal(lambda: Ledger(CURRENT_READ_POLICIES).request('s', SEARCH, request(POLICY_2, budget=32001))),
                         'integer_out_of_range')
        self.assertEqual(refusal(lambda: Ledger(CURRENT_READ_POLICIES).request('s', SEARCH, request(POLICY_2, available_tokens=32001))),
                         'integer_out_of_range')
        credits = derived('search_policy2_ready_9500', lambda v: v.update(credits_remaining=5))               # DERIVED
        led = Ledger(CURRENT_READ_POLICIES)
        led.request('s', SEARCH, request(POLICY_2))
        self.assertEqual(refusal(lambda: led.response('s', [{'type': 'text', 'text': credits}])), 'integer_out_of_range')
        self.assertEqual(refusal(lambda: Ledger({'', 'first-fitting-whole/3'})), 'ledger_policies')
        self.assertEqual(refusal(lambda: Ledger(())), 'ledger_policies')

    def test_guard_original_numeric_limits_are_untouched(self):
        self.assertEqual(PINNED_BUDGETS, {'memory_bytes': 9500, 'final_bytes': 8192, 'tool_room_bytes': 32000,
                                          'native_searches': 2, 'pulls': 4})
        self.assertEqual(evidence.TOOL_ROOM, 32000)
        led = Ledger(CURRENT_READ_POLICIES)
        led.request('s', SEARCH, request(min_pull_bytes=24000))
        self.assertEqual(refusal(lambda: Ledger(CURRENT_READ_POLICIES).request('s', SEARCH, request(min_pull_bytes=24001))), 'integer_out_of_range')


class CurrentProfile(unittest.TestCase):
    def test_fix_memory_on_profile_without_the_current_read_policy_is_refused_before_launch(self):
        self.assertEqual(refusal(lambda: t.profile(hooks='project', inspection_policies=ACCEPTED_POLICIES)), 'profile_current_read_contract')
        t.profile(hooks='project', inspection_policies=CURRENT_READ_POLICIES)
        t.profile(hooks='refuse', inspection_policies=ACCEPTED_POLICIES)               # an arm without hooks may pin either
        self.assertEqual(refusal(lambda: t.profile(inspection_policies=('first-fitting-whole/1',))), 'profile_inspection_policies')
        self.assertEqual(refusal(lambda: t.profile(inspection_policies=('', 'x'))), 'profile_inspection_policies')

    def test_guard_policies_are_part_of_the_profile_hash_and_document(self):
        a, b = t.profile(inspection_policies=ACCEPTED_POLICIES), t.profile(inspection_policies=CURRENT_READ_POLICIES)
        self.assertNotEqual(a.sha256, b.sha256)
        self.assertEqual(b.document['inspection_policies'], sorted(CURRENT_READ_POLICIES))
        self.assertEqual(b.document['budgets'], PINNED_BUDGETS)

    def stream(self, name, policy, budget=9500, final='{"ok":true}'):
        args = request(policy, budget=budget)
        return [t.init(), t.message('assistant', [t.use_block('s1', SEARCH, args)]),
                t.message('user', [t.result_block('s1', [{'type': 'text', 'text': text(name)}])]), t.final(final)]

    def test_fix_composed_stream_with_the_declaration_passes_and_keeps_the_original_budget_verdict(self):
        reader, _ = t.composed(None, prof=t.profile(inspection_policies=CURRENT_READ_POLICIES))
        done = t.run(reader, self.stream('search_policy2_ready_9500', POLICY_2))
        self.assertEqual(done['budget_check']['violations'], [])
        self.assertEqual(done['evidence']['responses']['s1']['memory_budget']['inspection_policy'], POLICY_2)
        reader, _ = t.composed(None, prof=t.profile(inspection_policies=CURRENT_READ_POLICIES))
        done = t.run(reader, self.stream('search_policy2_ready_32000', POLICY_2, budget=32000))
        self.assertEqual(done['budget_check']['violations'], ['search_budget_above_original'])       # evidence kept, verdict stays 9500
        self.assertEqual(done['budget_check']['pinned']['memory_bytes'], 9500)

    def test_fix_server_invalid_policy_request_is_evidence_and_is_flagged(self):
        args = request(POLICY_2, budget=None)
        events = [t.init(), t.message('assistant', [t.use_block('s1', SEARCH, args)]),
                  t.message('user', [t.result_block('s1', 'INVALID_REQUEST: inspection_policy requires first-fitting-whole/1 or /2, '
                                                          'memory_budget_bytes and no min_pull_bytes', True)]), t.final()]
        reader, _ = t.composed(None, prof=t.profile(inspection_policies=CURRENT_READ_POLICIES))
        done = t.run(reader, events)
        self.assertIn('inspection_policy_request_invalid', done['budget_check']['violations'])
        self.assertEqual(done['evidence']['responses']['s1'], {'is_error': True, 'payload_retained': False})

    def test_fix_default_and_non_declaring_readers_refuse_a_slash_2_stream_with_a_fixed_code(self):
        for reader in (Frames(t.S, t.M, t.V), t.composed(None, prof=t.profile(inspection_policies=ACCEPTED_POLICIES))[0]):
            init = t.init([SEARCH, PULL]) if reader.profile is None else t.init()
            with self.assertRaises(FrameError) as caught:
                t.feed_all(reader, [init, t.message('assistant', [t.use_block('s1', SEARCH, request(POLICY_2))])])
            self.assertEqual(str(caught.exception), 'inspection_policy_shape')


if __name__ == '__main__':
    unittest.main()
