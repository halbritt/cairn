"""CAIRN-186: the bound bridge compacts its per-session grant ledger without losing conflict or recovery evidence.

Local fixtures only: synthetic ledgers in temporary directories, a fixture CLI,
a mocked store. No live session, ledger or production database is touched.
"""
import hashlib
import importlib.util
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import test_claude_inbox_recall as claude_fixture
import test_postclaim_refusal_settlement as postclaim
from test_codex_refusal_recovery import CodexBase

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('compaction_bridge', ROOT / 'integrations/lifecycle/inbox_recall.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def key_of(label):
    return hashlib.sha256(label.encode()).hexdigest()


def ident(label):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'cairn186-fixture:' + label))


def ordinary(n):
    return dict(identity=dict(native_owner=f'claude-channel:old-prompt-{n}', boundary='ordinary_prompt'),
                limit_bytes=9500, output_bytes=400, native_allowance_bytes=9000,
                required=dict(count=0, selection_sha256='', pending=False), required_refresh_bytes=0)


def delivered(n):
    return dict(identity=dict(attempt_id=ident(f'attempt-{n}'), delivery_id=ident(f'delivery-{n}'),
                              event_id=ident(f'event-{n}'), session=dict(agent_id=ident('agent'), execution_id=ident('exec')),
                              source=dict(record_id=ident(f'record-{n}'), version=1),
                              native_owner=f'claude-channel:old-wake-{n}', boundary='claude_prompt_delivery'),
                wake_sha256=key_of(f'wake-{n}'), limit_bytes=9500, output_bytes=2400, coordination_bytes=760,
                native_allowance_bytes=7000, pull_calls=0, prior_hook_charge_bytes=0, status='required_only',
                at=1792354702.0 + n, required=dict(count=0, selection_sha256='', pending=False))


def history(session, count, *, decisions=0):
    """A long-lived session: mostly ordinary prompts, an inbox delivery every fourth grant."""
    grants = {}
    for n in range(count):
        grants[key_of(f'grant-{n}')] = delivered(n) if n % 4 == 3 else ordinary(n)
    ledger = dict(schema='cairn.inbox-recall-grants/1', session=session, grants=grants,
                  active=key_of(f'grant-{count - 1}'), pending_bytes=0)
    if decisions:
        ledger['inbox_activation'] = {f'claude-channel:old-decision-{n}': bool(n % 2) for n in range(decisions)}
    return ledger


class CompactionUnitTests(unittest.TestCase):
    def test_folds_old_grants_keeping_recent_window_active_task_and_pending_required(self):
        ledger = history('s', 300)
        keys = list(ledger['grants'])
        task_key, pending_key, named_key = keys[5], keys[6], keys[7]
        ledger['grants'][task_key]['identity'].update(recall_task_key=ident('task'), boundary='launcher_task')
        ledger['grants'][pending_key]['required'] = dict(count=1, selection_sha256='c' * 64, pending=True)
        ledger['grants'][keys[11]] = delivered(11)  # an older delivery named by the live state
        named = ledger['grants'][keys[11]]['identity']
        ledger['active'] = keys[-1]
        folded = bridge.compact_grants(ledger, owners={named['native_owner']}, deliveries={named['delivery_id']})
        retained = list(ledger['grants'])
        window = keys[-bridge.LEDGER_RECENT_GRANTS:]
        self.assertEqual(retained, [task_key, pending_key, keys[11], *window], 'order and pins preserved')
        self.assertEqual(folded, 300 - len(retained))
        self.assertEqual(ledger['folded']['count'], folded)
        for key in window:
            self.assertEqual(ledger['grants'][key], history('s', 300)['grants'][key], 'window grants are kept whole')
        self.assertEqual(ledger['active'], keys[-1])
        reloaded = self.roundtrip(ledger)
        self.assertEqual(reloaded['folded'], ledger['folded'])

    def test_folded_tokens_preserve_every_bridge_conflict_predicate(self):
        original = history('s', 200)
        ledger = json.loads(json.dumps(original))
        bridge.compact_grants(ledger)
        gone = [k for k in original['grants'] if k not in ledger['grants']]
        self.assertTrue(gone)
        for key in gone:
            grant = original['grants'][key]
            owner = grant['identity']['native_owner']
            self.assertTrue(bridge.folded_conflict(ledger, key=key, wake_sha256=None, owner=None), key)
            self.assertTrue(bridge.folded_conflict(ledger, key='0' * 64, wake_sha256=None, owner=owner), owner)
            if 'wake_sha256' in grant:
                self.assertTrue(bridge.folded_conflict(ledger, key='0' * 64, wake_sha256=grant['wake_sha256'], owner=None))
        self.assertFalse(bridge.folded_conflict(ledger, key='0' * 64, wake_sha256='1' * 64, owner='claude-channel:fresh'))
        # Codex checks no native owner; a missing section is no conflict.
        self.assertFalse(bridge.folded_conflict(dict(grants={}), key=gone[0], wake_sha256=None, owner=None))

    def test_compaction_is_cumulative_and_caps_tokens_oldest_first(self):
        ledger = history('s', 200)
        bridge.compact_grants(ledger)
        first = list(ledger['folded']['tokens'])
        for n in range(200, 260):
            ledger['grants'][key_of(f'grant-{n}')] = ordinary(n)
            ledger['active'] = key_of(f'grant-{n}')
        bridge.compact_grants(ledger)
        self.assertEqual(ledger['folded']['tokens'][:len(first)], first, 'earlier folds are kept, in order')
        self.assertEqual(ledger['folded']['count'], 260 - bridge.LEDGER_RECENT_GRANTS)
        combined = list(ledger['folded']['tokens'])
        for n in range(260, 270):
            ledger['grants'][key_of(f'grant-{n}')] = ordinary(n)
            ledger['active'] = key_of(f'grant-{n}')
        uncapped = json.loads(json.dumps(ledger))
        bridge.compact_grants(uncapped)
        self.assertEqual(uncapped['folded']['tokens'][:len(combined)], combined)
        with patch.object(bridge, 'LEDGER_FOLDED_TOKENS', 50):
            bridge.compact_grants(ledger)
        self.assertEqual(ledger['folded']['tokens'], uncapped['folded']['tokens'][-50:], 'the oldest tokens expire first')
        self.assertEqual(ledger['folded']['count'], 270 - bridge.LEDGER_RECENT_GRANTS)

    def test_activation_decisions_keep_recent_window_and_current_turn(self):
        ledger = history('s', 10, decisions=500)
        current = 'claude-channel:old-decision-3'
        bridge.compact_grants(ledger, turns={current})
        decisions = list(ledger['inbox_activation'])
        self.assertEqual(decisions[0], current)
        self.assertEqual(decisions[1:], [f'claude-channel:old-decision-{n}'
                                         for n in range(500 - bridge.LEDGER_RECENT_DECISIONS, 500)])
        self.assertIs(ledger['inbox_activation'][current], True)

    def test_small_ledger_is_left_byte_identical(self):
        ledger = history('s', bridge.LEDGER_RECENT_GRANTS, decisions=3)
        before = json.dumps(ledger)
        self.assertEqual(bridge.compact_grants(ledger), 0)
        self.assertEqual(json.dumps(ledger), before)
        self.assertNotIn('folded', ledger)

    def test_load_grants_rejects_a_corrupt_fold(self):
        ledger = history('s', 200)
        bridge.compact_grants(ledger)
        for name, change in (('not a dict', lambda f: f.clear() or f.update(x=1)),
                             ('negative count', lambda f: f.update(count=-1)),
                             ('bool count', lambda f: f.update(count=True)),
                             ('bad token', lambda f: f['tokens'].append('XYZ')),
                             ('long token', lambda f: f['tokens'].append('0' * 64)),
                             ('tokens not list', lambda f: f.update(tokens='abc'))):
            with self.subTest(case=name):
                broken = json.loads(json.dumps(ledger))
                change(broken['folded'])
                with self.assertRaises(ValueError):
                    self.roundtrip(broken)

    def roundtrip(self, ledger):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (ledger['session'] + '.inbox-recall.json')
            path.write_text(json.dumps(ledger))
            return bridge.load_grants(path, ledger['session'])


class BridgeCompactionTests(unittest.TestCase):
    """The actual coordination and memory hooks against an oversized or already compacted Claude ledger."""
    setUp = claude_fixture.ClaudeInboxRecallTests.setUp
    freeze = claude_fixture.ClaudeInboxRecallTests.freeze
    freeze_base = claude_fixture.ClaudeInboxRecallTests.freeze_base
    calls = claude_fixture.ClaudeInboxRecallTests.calls
    invoke = claude_fixture.ClaudeInboxRecallTests.invoke
    memory_main = claude_fixture.ClaudeInboxRecallTests.memory_main
    ledger = claude_fixture.ClaudeInboxRecallTests.ledger

    def seed(self, ledger):
        directory = self.root / 'memory'
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / (claude_fixture.SESSION + '.inbox-recall.json')
        path.write_text(json.dumps(ledger, separators=(',', ':')))
        path.with_suffix('.initialized').write_text(json.dumps(dict(
            schema='cairn.inbox-recall-initialized/1', session=claude_fixture.SESSION)))
        return path

    def test_admitted_delivery_compacts_an_oversized_ledger(self):
        original = history(claude_fixture.SESSION, 400, decisions=300)
        path = self.seed(original)
        size = path.stat().st_size
        out = self.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        self.assertIn(self.body, out['stdout'])
        after = self.ledger()
        self.assertLess(path.stat().st_size, size / 3)
        self.assertEqual(len(after['grants']), bridge.LEDGER_RECENT_GRANTS)
        self.assertEqual(after['folded']['count'], 401 - bridge.LEDGER_RECENT_GRANTS)
        current = after['grants'][after['active']]
        self.assertEqual(current['identity']['native_owner'], self.native)
        self.assertEqual(current['identity']['delivery_id'], claude_fixture.DELIVERY)
        self.assertEqual(list(after['grants'])[-1], after['active'])
        self.assertIn(self.native, after['inbox_activation'])
        self.assertLessEqual(len(after['inbox_activation']), bridge.LEDGER_RECENT_DECISIONS + 1)

    def test_compacted_ledger_then_receives_new_grants_from_bridge_and_engine(self):
        compacted = history(claude_fixture.SESSION, 300)
        bridge.compact_grants(compacted)
        tokens = list(compacted['folded']['tokens'])
        self.seed(compacted)
        out = self.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        after = self.ledger()
        self.assertEqual(after['folded']['tokens'][:len(tokens)], tokens)
        self.assertEqual(after['grants'][after['active']]['identity']['native_owner'], self.native)
        # The pinned engine's own save of an ordinary prompt grant keeps the fold.
        folded = after['folded']
        nxt = dict(self.event, prompt_id='host-prompt-two', prompt='Inspect the current fixture implementation.')
        result = self.memory_main(nxt)
        self.assertEqual(result['code'], 0, result)
        again = self.ledger()
        self.assertEqual(again['folded'], folded)
        self.assertEqual(again['grants'][again['active']]['identity'],
                         dict(native_owner='claude-channel:host-prompt-two', boundary='ordinary_prompt'))

    def test_folded_owner_or_wake_still_refuses_a_repeated_grant(self):
        wake = hashlib.sha256(self.event['prompt'].encode()).hexdigest()
        for name, extra in (('native owner', dict(native_owner=self.native)), ('wake', dict(wake_sha256=wake))):
            with self.subTest(folded=name):
                self.setUp()
                original = history(claude_fixture.SESSION, 200)
                old = list(original['grants'])[3]
                grant = original['grants'][old]
                if 'native_owner' in extra:
                    grant['identity']['native_owner'] = extra['native_owner']
                else:
                    grant['wake_sha256'] = extra['wake_sha256']
                bridge.compact_grants(original)
                self.assertNotIn(old, original['grants'])
                self.seed(original)
                calls = len(self.calls())
                out = self.invoke(main=True)
                self.assertEqual(out['code'], 2, out)
                self.assertNotIn(self.body, out['stdout'])
                after = self.ledger()
                # Only the shared activation decision for this prompt is recorded; no grant, no fold change.
                self.assertEqual(after.pop('inbox_activation'), {self.native: True})
                self.assertEqual(after, original, 'refusal changed the compacted grants')
                self.assertEqual(len(self.calls()), calls, 'refusal read memory or source')

    def test_unadmitted_wake_never_compacts(self):
        for key in ('inbox_intent', 'inbox_attempt'):
            self.state.pop(key)
        self.freeze()
        original = history(claude_fixture.SESSION, 200)
        self.seed(original)
        with patch.object(claude_fixture.coord, 'recover_inbox'):
            out = self.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        after = self.ledger()
        self.assertNotIn('folded', after)
        self.assertEqual(len(after['grants']), 201)
        self.assertEqual(after['grants'][after['active']]['identity']['boundary'], 'unadmitted_wake')


class RecoveryAgainstCompactedLedgerTests(postclaim.Base):
    def write_compacted(self, **retained_identity):
        ledger = history(postclaim.SESSION, 300)
        # The attempt under recovery is long folded away only if never named; place a named grant in the window.
        if retained_identity:
            last = list(ledger['grants'])[-1]
            ledger['grants'][last]['identity'].update(retained_identity)
        ledger['inbox_activation'] = {postclaim.TURN: True}
        bridge.compact_grants(ledger)
        self.assertIn('folded', ledger)
        self.ledger.write_text(json.dumps(ledger))
        return ledger

    def test_refused_wake_without_a_grant_settles_against_a_compacted_ledger(self):
        self.write_compacted()
        diagnostic, _ = self.settle()
        self.assertEqual((diagnostic['settlement'], diagnostic['grant']), ('turn_ended', 'absent'))
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()],
                         [(postclaim.ATTEMPT, 'turn_ended')])

    def test_retained_grant_for_the_attempt_delivery_or_prompt_keeps_the_hold(self):
        for identity in (dict(attempt_id=postclaim.ATTEMPT), dict(delivery_id=postclaim.DELIVERY),
                         dict(native_owner=postclaim.TURN)):
            with self.subTest(identity=identity):
                self.setUp()
                self.write_compacted(**identity)
                diagnostic, _ = self.settle()
                self.assertEqual((diagnostic['settlement'], diagnostic['grant']), ('held', 'present'))
                self.assertEqual(self.reconciles(), [])

    def test_explicit_claude_recovery_reads_a_compacted_ledger(self):
        self.write_compacted()
        self.state['wake_refusal_diagnostic'] = dict(phase='bridge_after_claim', origin='hook', settlement='held',
            attempt_id=postclaim.ATTEMPT, delivery_id=postclaim.DELIVERY, native_turn_id=postclaim.TURN, at=1.0,
            grant='unknown')
        self.state.pop('active_prompt')
        self.save()
        with self.patched(), patch.object(postclaim.coord, 'process_alive', return_value=True):
            result = postclaim.coord.recover_refused_wake(self.cc, postclaim.SESSION, postclaim.ATTEMPT, postclaim.TURN)
        self.assertEqual(result['grant'], 'absent')
        self.assertEqual([(r['attempt_id'], r['reason']) for r in self.reconciles()],
                         [(postclaim.ATTEMPT, 'turn_ended')])


class CodexRecoveryAgainstCompactedLedgerTests(CodexBase):
    def test_codex_refused_wake_recovery_settles_and_a_retained_grant_refuses(self):
        ledger = history(postclaim.SESSION, 300)
        bridge.compact_grants(ledger)
        self.ledger.write_text(json.dumps(ledger))
        result = self.recover(check_only=True)
        self.assertEqual(result['status'], 'eligible')
        last = list(ledger['grants'])[-1]
        ledger['grants'][last]['identity']['attempt_id'] = postclaim.ATTEMPT
        self.ledger.write_text(json.dumps(ledger))
        self.assert_refused_unchanged()


if __name__ == '__main__':
    unittest.main()
