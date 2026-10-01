"""Claude admitted-channel boundary; local fixture CLI, no native/provider run."""
import hashlib
import json
import unittest
from unittest.mock import patch
import test_inbox_recall_bridge as fixture
from test_inbox_recall_bridge import memory, coord, SESSION, TURN, AGENT, EXEC, DELIVERY


class ClaudeInboxRecallTests(unittest.TestCase):
    freeze_base = fixture.InboxRecallBridgeTests.freeze
    calls = fixture.InboxRecallBridgeTests.calls
    invoke = fixture.InboxRecallBridgeTests.invoke
    memory_main = fixture.InboxRecallBridgeTests.memory_main
    compact_event = fixture.InboxRecallBridgeTests.compact_event

    def setUp(self):
        fixture.InboxRecallBridgeTests.setUp(self)
        self.mc['harness'] = self.cc['harness'] = 'claude'
        self.state['agent']['metadata']['harness'] = 'claude'
        self.event.pop('turn_id')
        self.event['prompt_id'] = 'host-prompt-one'
        self.native = 'claude-channel:host-prompt-one'
        self.state['inbox_intent']['native_turn_id'] = self.native
        self.state['inbox_attempt']['native_turn_id'] = self.native
        self.freeze()

    def freeze(self):
        self.freeze_base()
        if self.mc['harness'] == 'claude':
            self.event['prompt'] = (f'<channel source="cairn-events" native_session_id="{SESSION}" '
                f'agent_id="{AGENT}" execution_id="{EXEC}" delivery_id="{DELIVERY}">\n'
                + self.event['prompt'] + '\n</channel>')

    def ledger(self):
        return json.loads((self.root/'memory'/f'{SESSION}.inbox-recall.json').read_text())

    def test_ordinary_prompt_reports_returned_utf8_text_bytes_not_envelope(self):
        required = 'Current required guard: 日本語 🛡.'
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
        self.freeze()
        event = dict(self.event, prompt='Inspect the fixture migration safeguards.')
        state_path = self.root / 'memory' / f'{SESSION}.json'

        def reported(result):
            self.assertEqual(result['code'], 0, result)
            text = json.loads(result['stdout']).get('hookSpecificOutput', {}).get('additionalContext', '')
            status = json.loads(state_path.read_text())['last_recall']
            self.assertEqual(status['bytes'], len(text.encode('utf-8')))
            return text, status

        first = self.memory_main(event)
        self.assertEqual(first['code'], 0, first)
        self.assertIn('candidate_bodies', first['stdout'])
        text, status = reported(first)
        self.assertEqual(status['outcome'], 'delegated')
        self.assertIn(required, text)
        self.assertGreater(status['bytes'], len(text))
        self.assertLess(status['bytes'], len(first['stdout'].encode('utf-8')))
        before = self.ledger()
        grant = before['grants'][before['active']]
        self.assertEqual(grant['output_bytes'], len(first['stdout'].encode('utf-8')))

        refreshed = self.memory_main(event)
        text, status = reported(refreshed)
        self.assertEqual(status['outcome'], 'required_only')
        self.assertIn(required, text)
        self.assertNotIn('candidate_bodies', text)
        self.assertEqual(self.ledger()['grants'][before['active']]['output_bytes'], grant['output_bytes'])

        self.fixture['search']['selected'] = []
        self.freeze()
        text, status = reported(self.memory_main(event))
        self.assertEqual(text, '')
        self.assertEqual(status['outcome'], 'empty')

        self.fixture['fail_search'] = 'API_UNAVAILABLE'
        self.freeze()
        failed = self.memory_main(dict(event, prompt_id='next-prompt'))
        self.assertNotEqual(failed['code'], 0)
        self.assertEqual(failed['stdout'], '')
        status = json.loads(state_path.read_text())['last_recall']
        self.assertEqual(status['outcome'], 'failed')
        self.assertEqual(status['bytes'], 0)

    def test_admitted_channel_delivers_source_and_memory_once_through_public_main(self):
        original = dict(self.event)
        self.assertEqual(self.memory_main(self.event)['code'], 0)
        out = self.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        context = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
        self.assertIn(self.body, context)
        self.assertIn(self.fixture['pull']['selection']['record']['body'], context)
        self.assertEqual(self.event, original)
        ledger = self.ledger()
        grant = ledger['grants'][ledger['active']]
        self.assertEqual(grant['identity']['native_owner'], self.native)
        self.assertEqual(grant['identity']['boundary'], 'claude_prompt_delivery')
        self.assertEqual(grant['output_bytes'], len(out['stdout'].encode()))
        self.assertLessEqual(grant['output_bytes'] + grant['native_allowance_bytes'], 9500)
        calls = self.calls()
        self.assertEqual(self.invoke(main=True)['code'], 2)
        self.assertEqual(self.calls(), calls)
        self.assertEqual(self.ledger(), ledger)

    def test_compact_restores_required_without_optional_credit_and_counts_every_emission(self):
        required = 'Whole current Claude instruction. 日本語.'
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
        self.freeze()
        initial = self.invoke(main=True)
        self.assertEqual(initial['code'], 0)
        before = self.ledger()
        grant = before['grants'][before['active']]
        calls = len(self.calls())
        out = self.memory_main(self.compact_event())
        self.assertEqual(out['code'], 0, out)
        self.assertIn(required, json.loads(out['stdout'])['hookSpecificOutput']['additionalContext'])
        self.assertEqual([c['op'] for c in self.calls()[calls:]], ['search'])
        after = self.ledger()['grants'][before['active']]
        for key in ('output_bytes', 'native_allowance_bytes', 'pull_calls'):
            self.assertEqual(after[key], grant[key])
        self.assertEqual(after['required_refresh_bytes'], len(out['stdout'].encode()))
        self.assertLessEqual(len(out['stdout'].encode()), 9500)

    def test_missing_and_changed_prompt_owner_refuse_before_source(self):
        for owner in (None, 'claude-channel:another-prompt'):
            with self.subTest(owner=owner):
                self.state['inbox_attempt']['native_turn_id'] = owner
                self.freeze()
                self.assertEqual(self.invoke(main=True)['code'], 2)
                self.assertEqual(self.calls(), [])

    def test_startup_charge_is_carried_once_and_optional_outage_does_not_block(self):
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Startup required text.'))]
        self.freeze()
        startup = self.memory_main(dict(self.event, hook_event_name='SessionStart', source='startup', prompt=''))
        self.assertEqual(startup['code'], 0, startup)
        self.assertEqual(self.ledger()['pending_bytes'], len(startup['stdout'].encode()))
        initial = self.invoke(main=True)
        self.assertEqual(initial['code'], 0, initial)
        grant = self.ledger()['grants'][self.ledger()['active']]
        self.assertEqual(grant['prior_hook_charge_bytes'], len(startup['stdout'].encode()))
        self.assertLessEqual(grant['prior_hook_charge_bytes'] + grant['output_bytes'] + grant['native_allowance_bytes'], 9500)

    def test_required_failure_is_pending_and_next_prompt_refuses_but_resume_does_not_claim_stop(self):
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Required applicability guard.'))]
        self.freeze()
        self.assertEqual(self.invoke(main=True)['code'], 0)
        self.fixture['fail_search'] = 'API_UNAVAILABLE'; self.freeze()
        failed = self.memory_main(self.compact_event())
        self.assertEqual(failed['code'], 1)
        self.assertEqual(failed['stdout'], '')
        self.assertTrue(self.ledger()['grants'][self.ledger()['active']]['required']['pending'])
        owner_join = dict(self.event, prompt='Continue the already active task.')
        self.assertEqual(self.memory_main(owner_join)['code'], 2)
        self.assertEqual(self.memory_main(dict(owner_join, prompt_id='host-next-prompt'))['code'], 2)
        self.fixture.pop('fail_search'); self.fixture['search']['selected'] = []; self.freeze()
        recovered = self.memory_main(owner_join)
        self.assertEqual(recovered['code'], 0)
        self.assertEqual(self.ledger()['grants'][self.ledger()['active']]['required']['count'], 0)
        self.assertFalse(self.ledger()['grants'][self.ledger()['active']]['required']['pending'])

    def test_no_known_required_outage_and_first_save_failure_remain_optional(self):
        self.fixture['fail_search'] = 'API_UNAVAILABLE'; self.freeze()
        failed = self.memory_main(self.compact_event())
        self.assertEqual(failed['code'], 1)
        self.assertEqual(failed['stdout'], '')
        self.fixture.pop('fail_search'); self.freeze()
        with patch.object(memory, 'save_codex_budget', side_effect=OSError('fixture disk failure')):
            failed = self.memory_main(self.compact_event())
        self.assertEqual(failed['code'], 1)
        self.assertEqual(failed['stdout'], '')
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='First checked required instruction.'))]
        self.freeze()
        with patch.object(memory, 'save_codex_budget', side_effect=OSError('fixture disk failure')):
            failed = self.memory_main(dict(self.event, prompt='A normal owner task.'))
        self.assertEqual(failed['code'], 2)
        self.assertEqual(failed['stdout'], '')

    def test_new_owner_task_has_fresh_prompt_grant_without_replaying_inbox_task(self):
        self.assertEqual(self.invoke(main=True)['code'], 0)
        before = self.ledger()
        next_event = dict(self.event, prompt_id='host-prompt-two', prompt='Inspect the current fixture implementation.')
        out = self.memory_main(next_event)
        self.assertEqual(out['code'], 0, out)
        self.assertNotIn(self.body, out['stdout'])
        after = self.ledger()
        self.assertEqual(len(after['grants']), 2)
        self.assertEqual(after['grants'][before['active']], before['grants'][before['active']])
        current = after['grants'][after['active']]
        self.assertLessEqual(current['output_bytes'] + current['native_allowance_bytes'], 9500)
        calls = len(self.calls())
        again = self.memory_main(next_event)
        self.assertEqual(again['code'], 0)
        self.assertEqual([x['op'] for x in self.calls()[calls:]], ['search'])
        self.assertEqual(self.ledger()['grants'][after['active']]['native_allowance_bytes'], current['native_allowance_bytes'])

    def test_both_hook_orders_hold_activation_decision_across_manifest_flip(self):
        for enabled in (False, True):
            for first in ('memory', 'coordination'):
                with self.subTest(enabled=enabled, first=first):
                    case = ClaudeInboxRecallTests(); case.setUp(); self.addCleanup(case.doCleanups)
                    required = 'Required context survives the activation boundary.'
                    case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
                    case.freeze()
                    manifest = json.loads(case.binding.read_text()); manifest['enabled'] = enabled
                    case.binding.write_text(json.dumps(manifest))
                    invoke = {'memory': lambda: case.memory_main(case.event), 'coordination': lambda: case.invoke(main=True)}
                    a = invoke[first]()
                    manifest['enabled'] = not enabled; case.binding.write_text(json.dumps(manifest))
                    b = invoke['coordination' if first == 'memory' else 'memory']()
                    self.assertEqual((a['code'], b['code']), (0, 0), (a,b))
                    outputs = a['stdout'] + b['stdout']
                    self.assertIn(required, outputs)
                    self.assertEqual(case.body in outputs, enabled)
                    ledger = case.ledger()
                    self.assertEqual(ledger['inbox_activation'][case.native], enabled)
                    self.assertEqual(len(ledger['grants']), int(enabled))

    def test_required_envelope_refusal_and_no_memory_opt_out(self):
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='必須条件' * 3000))]
        self.freeze()
        out = self.invoke(main=True)
        self.assertEqual(out['code'], 2)
        self.assertEqual(out['stdout'], '')
        self.assertEqual(self.ledger()['grants'], {})
        (self.root/'.cairn-no-memory').touch()
        self.assertEqual(memory.handle(self.mc, self.event), {})
        count = len(self.calls())
        out = self.invoke(main=True)
        self.assertEqual(out['code'], 0)
        self.assertEqual(len(self.calls()), count)
        self.assertNotIn(self.body, out['stdout'])

    def test_refused_source_and_lease_never_emit_assignment(self):
        self.assertEqual(self.invoke(main=True, lease_failure=True)['code'], 2)
        self.fixture['fail_history'] = 'AUTHORITY_DENIED'; self.freeze()
        out = self.invoke(main=True)
        self.assertEqual(out['code'], 2)
        self.assertEqual(out['stdout'], '')
        self.assertEqual(self.ledger()['grants'], {})

    def test_pin_drift_after_enabled_latch_refuses_without_source_reads(self):
        self.assertEqual(self.memory_main(self.event)['code'], 0)
        (self.root/'memory.json').write_text('{}')
        out = self.invoke(main=True)
        self.assertEqual(out['code'], 2)
        self.assertEqual(self.calls(), [])

    def test_pending_required_status_survives_config_drift_and_missing_prompt_identity(self):
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Current required instruction.'))]
        self.freeze()
        self.assertEqual(self.invoke(main=True)['code'], 0)
        self.fixture['fail_search'] = 'API_UNAVAILABLE'; self.freeze()
        self.assertEqual(self.memory_main(self.compact_event())['code'], 1)
        ordinary = dict(self.event, prompt='Continue current work.')
        ordinary.pop('prompt_id')
        self.assertEqual(self.memory_main(ordinary)['code'], 2)
        # Engine drift prevents optional work; status classification does not
        # depend on successfully importing the changed engine/config.
        (self.root/'memory.py').write_text('changed engine')
        ordinary['prompt_id'] = 'new-host-prompt'
        self.assertEqual(self.memory_main(ordinary)['code'], 2)

    def test_capture_keeps_original_host_input_and_ignores_bridge_dependency(self):
        event = dict(self.event, hook_event_name='PreCompact', transcript_path='original-owner-transcript')
        self.binding.unlink()
        observed = []
        def capture(mem, actual, state):
            observed.append(actual)
            return {}
        with patch.object(memory, 'capture', side_effect=capture):
            self.assertEqual(memory.handle(self.mc, event), {})
        self.assertEqual(observed, [event])
        self.assertEqual(self.calls(), [])

    def test_dependency_and_final_state_failure_persist_required_restoration_obligation(self):
        for failure in ('engine_drift', 'final_state_save'):
            with self.subTest(failure=failure):
                case = ClaudeInboxRecallTests(); case.setUp(); self.addCleanup(case.doCleanups)
                case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Known whole requirement.'))]
                case.freeze()
                self.assertEqual(case.invoke(main=True)['code'], 0)
                if failure == 'engine_drift':
                    (case.root/'memory.py').write_text('changed pinned dependency')
                    failed = case.memory_main(case.compact_event())
                else:
                    with patch.object(memory, 'save_state', side_effect=OSError('ordinary state save failed')):
                        failed = case.memory_main(case.compact_event())
                self.assertEqual(failed['code'], 1)
                self.assertEqual(failed['stdout'], '')
                self.assertTrue(case.ledger()['grants'][case.ledger()['active']]['required']['pending'])
                if failure != 'engine_drift':
                    case.fixture['fail_search'] = 'API_UNAVAILABLE'; case.freeze()
                next_prompt = dict(case.event, prompt_id='next-native-prompt', prompt='Start the next owner request.')
                self.assertEqual(case.memory_main(next_prompt)['code'], 2)

    def test_failed_pending_write_cannot_erase_known_required_across_new_prompt(self):
        for selected in ([], [dict(mandatory=True, record=dict(body='Known required context.'))]):
            with self.subTest(required=bool(selected)):
                case = ClaudeInboxRecallTests(); case.setUp(); self.addCleanup(case.doCleanups)
                case.fixture['search']['selected'] = selected; case.freeze()
                self.assertEqual(case.invoke(main=True)['code'], 0)
                (case.root/'memory.py').write_text('changed dependency')
                with patch.object(memory, 'save_codex_budget', side_effect=OSError('pending status write failed')):
                    self.assertEqual(case.memory_main(case.compact_event())['code'], 1)
                active = case.ledger()['grants'][case.ledger()['active']]
                self.assertFalse(active['required']['pending'])
                event = dict(case.event, prompt_id='next-actual-prompt', prompt='Next ordinary task.')
                self.assertEqual(case.memory_main(event)['code'], 2 if selected else 1)
                # A fresh authenticated check can legitimately clear the old obligation.
                case.fixture['search']['selected'] = []; case.freeze()
                self.assertEqual(case.memory_main(event)['code'], 0)
                self.assertEqual(case.ledger()['grants'][case.ledger()['active']]['required']['count'], 0)
