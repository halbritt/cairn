"""Required lookup recovery through the native hook, without a model call."""
import json
import hashlib
import itertools
import time
import unittest
from unittest.mock import Mock, patch

import test_claude_inbox_recall as claude
import test_inbox_recall_bridge as fixtures


class RequiredLookupRetryTests(unittest.TestCase):
    def test_original_deadline_prevents_a_retry_after_it_expires(self):
        case = fixtures.InboxRecallBridgeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.fixture['fail_search'] = 'TIMEOUT'
        case.freeze()
        original_load = fixtures.coord.load_inbox_recall

        def load(config, **kwargs):
            bridge = original_load(config, **kwargs)
            original_bind = bridge.binding

            def bind(config):
                engine, settings = original_bind(config)
                failed = False
                clock = Mock(wraps=time)
                clock.monotonic.side_effect = lambda: time.monotonic() + (60 if failed else 0)
                engine.time = clock
                original_call = engine.Memory.call

                def call(instance, *args, **kwargs):
                    nonlocal failed
                    try:
                        return original_call(instance, *args, **kwargs)
                    finally:
                        failed = True

                engine.Memory.call = call
                return engine, settings

            bridge.binding = bind
            return bridge

        with patch.object(fixtures.coord, 'load_inbox_recall', side_effect=load):
            result = case.invoke(main=True)
        self.assertEqual((result['code'], result['stdout']), (2, ''))
        self.assertEqual([c['op'] for c in case.calls()], ['search'])

    def test_repeated_transient_and_permanent_failures_never_grant_context(self):
        for failure, count in [('TIMEOUT', 2), ('API_UNAVAILABLE', 2),
                               ('API_CONNECTION_FAILED', 2), ('VERSION_CONFLICT', 2),
                               ('IDEMPOTENCY_CONFLICT', 1), ('AUTHORITY_DENIED', 1),
                               ('OPEN_CONFLICT', 1), ('BUDGET_REFUSED', 1)]:
            with self.subTest(failure=failure):
                case = fixtures.InboxRecallBridgeTests()
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.fixture['fail_search'] = failure
                case.freeze()
                result = case.invoke(main=True)
                self.assertEqual((result['code'], result['stdout']), (2, ''))
                self.assertEqual([c['op'] for c in case.calls()], ['search'] * count)
                case.assert_no_grant()
                if count == 2:
                    self.assertEqual(case.calls()[0]['args'], case.calls()[1]['args'])

    def test_transient_required_lookup_preserves_whole_notice_context(self):
        for harness, failure in itertools.product(
                (fixtures.InboxRecallBridgeTests, claude.ClaudeInboxRecallTests),
                ('TIMEOUT', 'API_UNAVAILABLE', 'API_CONNECTION_FAILED', 'VERSION_CONFLICT', 'process_timeout')):
            with self.subTest(harness=harness.__name__, failure=failure):
                case = harness()
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.delivery['event']['kind'] = 'notice'
                required = 'Required whole instruction: retain source and ownership checks.'
                case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
                case.freeze()
                # A hung first lookup, not merely a slow one, reaches the client timeout.
                fail = ("import time;time.sleep(30)" if failure == 'process_timeout' else
                        f"print(json.dumps(dict(ok=False,status={failure!r})));sys.exit(7)")
                case.cli.write_text(case.cli.read_text().replace("if op=='search':", """if op=='search':
 if len([x for x in (root/'calls.jsonl').read_text().splitlines() if json.loads(x)['op']=='search'])==1:
  """ + fail))
                result = case.invoke(main=True)
                self.assertEqual(result['code'], 0, result)
                text = json.loads(result['stdout'])['hookSpecificOutput']['additionalContext']
                self.assertIn(required, text)
                self.assertIn('Read its exact selected source', text)
                calls = case.calls()
                self.assertEqual([c['op'] for c in calls], ['search', 'search'])
                self.assertEqual(calls[0]['args'], calls[1]['args'])
                self.assertIn('--request-id', calls[0]['args'])
                ledger = json.loads((case.root/'memory'/f'{fixtures.SESSION}.inbox-recall.json').read_text())
                self.assertEqual(len(ledger['grants']), 1)
                self.assertEqual(next(iter(ledger['grants'].values()))['identity']['attempt_id'], fixtures.ATTEMPT)

    def test_older_pinned_engine_retains_single_attempt_behavior(self):
        case = fixtures.InboxRecallBridgeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.fixture['fail_search'] = 'TIMEOUT'
        case.freeze()
        engine = case.root/'memory.py'
        engine.write_text(engine.read_text().replace('INBOX_REQUIRED_SEARCH_RETRY_VERSION = 1\n', ''))
        binding = json.loads(case.binding.read_text())
        binding['engine']['sha256'] = hashlib.sha256(engine.read_bytes()).hexdigest()
        case.binding.write_text(json.dumps(binding))
        result = case.invoke(main=True)
        self.assertEqual((result['code'], result['stdout']), (2, ''))
        self.assertEqual(len(case.calls()), 1)
        self.assertNotIn('--request-id', case.calls()[0]['args'])


def slow_required_search(case, delays):
    """Delay each successive required lookup in the fake CLI by delays[n] seconds."""
    case.cli.write_text(case.cli.read_text().replace("if op=='search':", f"""if op=='search' and '--kind' in args:
 import time
 n=len([x for x in (root/'calls.jsonl').read_text().splitlines() if json.loads(x)['op']=='search'])-1
 time.sleep({list(delays)!r}[min(n,{len(delays) - 1})])
if op=='search':""", 1))


def record_timeouts(case, seen):
    """Observe the timeout each memory operation receives, without changing it."""
    original_load = fixtures.coord.load_inbox_recall

    def load(config, **kwargs):
        bridge = original_load(config, **kwargs)
        original_bind = bridge.binding

        def bind(config):
            engine, settings = original_bind(config)
            original_call = engine.Memory.call

            def call(instance, operation, *args, **kwargs):
                seen.append((operation, kwargs.get('timeout'), time.monotonic()))
                return original_call(instance, operation, *args, **kwargs)

            engine.Memory.call = call
            return engine, settings

        bridge.binding = bind
        return bridge

    return patch.object(fixtures.coord, 'load_inbox_recall', side_effect=load)


class RequiredLookupBudgetTests(unittest.TestCase):
    """A slow but successful required lookup is not cut off at a fixed two seconds."""

    def case(self):
        case = fixtures.InboxRecallBridgeTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def test_required_lookup_slower_than_two_seconds_delivers_task_in_one_attempt(self):
        case = self.case()
        slow_required_search(case, [2.5])
        case.freeze()
        result = case.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn(case.body, text)
        ops = [c['op'] for c in case.calls()]
        self.assertEqual(ops[:2], ['search', 'history'])
        self.assertEqual(sum('--kind' in c['args'] for c in case.calls()), 1)

    def test_required_lookup_beyond_whole_budget_refuses_with_same_diagnostics(self):
        case = self.case()
        slow_required_search(case, [30])
        case.freeze()
        started = time.monotonic()
        with self.assertRaises(fixtures.coord.NativePromptRefused) as raised:
            case.invoke()
        elapsed = time.monotonic() - started
        meta = raised.exception.__cause__.cairn_recall_failure
        self.assertEqual({k: meta[k] for k in ('recall_phase', 'recall_operation', 'recall_attempts',
                                               'recall_max_attempts')},
                         dict(recall_phase='required_search', recall_operation='search',
                              recall_attempts=2, recall_max_attempts=2))
        self.assertIsInstance(meta['deadline_remaining_ms'], int)
        self.assertLess(meta['deadline_remaining_ms'], 500)
        self.assertEqual([c['op'] for c in case.calls()], ['search', 'search'])
        self.assertEqual(case.calls()[0]['args'], case.calls()[1]['args'])
        # The required-lookup window (eight seconds, inside the bridge deadline) bounds the refusal.
        self.assertLess(elapsed, 10)
        case.assert_no_grant()

    def test_hung_first_lookup_leaves_its_reserve_for_one_retry(self):
        case = self.case()
        slow_required_search(case, [30, 0])
        case.freeze()
        seen = []
        with record_timeouts(case, seen):
            result = case.invoke()
        self.assertIn(case.body, result['hookSpecificOutput']['additionalContext'])
        searches = [c for c in case.calls() if '--kind' in c['args']]
        self.assertEqual(len(searches), 2)
        self.assertEqual(searches[0]['args'], searches[1]['args'])
        first, retry = [t for op, t, _ in seen[:2]]
        self.assertGreater(first, 6.5)
        self.assertLessEqual(first, 7.0)
        self.assertGreater(retry, 0.5)

    def test_slow_successful_required_lookup_leaves_later_phases_their_reserve(self):
        case = self.case()
        slow_required_search(case, [3.0])
        case.freeze()
        seen = []
        with record_timeouts(case, seen):
            result = case.invoke()
        self.assertIn(case.body, result['hookSpecificOutput']['additionalContext'])
        operations = [op for op, _, _ in seen]
        self.assertEqual(operations[:2], ['search', 'history'])
        first = seen[0][1]
        self.assertLessEqual(first, 7.0)
        history = seen[1][1]
        self.assertGreater(history, 0.8)
        self.assertLessEqual(history, 2)


    def test_required_lookup_beyond_five_second_recall_cap_still_delivers(self):
        # CAIRN-187: under host load (load average ~50 on 12 CPUs) one required
        # lookup took over four seconds; the five-second recall cap left a
        # one-second retry that could not finish, refusing agent-510's wake.
        case = self.case()
        case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='Required whole instruction.'))]
        slow_required_search(case, [5.5])
        case.freeze()
        seen = []
        with record_timeouts(case, seen):
            result = case.invoke()
        text = result['hookSpecificOutput']['additionalContext']
        self.assertIn('Required whole instruction.', text)
        # The exact task-source read still gets its own bounded time after the
        # slow lookup, within the required window's post-lookup reserve.
        self.assertIn(case.body, text)
        self.assertEqual(sum('--kind' in c['args'] for c in case.calls()), 1)
        self.assertEqual([op for op, _, _ in seen][:2], ['search', 'history'])
        self.assertGreater(seen[1][1], 1.5)
        self.assertLessEqual(seen[1][1], 2)
        ledger = json.loads((case.root/'memory'/f'{fixtures.SESSION}.inbox-recall.json').read_text())
        self.assertEqual(len(ledger['grants']), 1)


class RequiredLookupWindowTests(unittest.TestCase):
    """The required window reallocates unused bridge time but never its post-lookup reserve."""

    def bridge(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integrations' / 'lifecycle'))
        import inbox_recall
        return inbox_recall

    def test_window_is_eight_seconds_when_the_bridge_has_time(self):
        bridge = self.bridge()
        self.assertEqual(bridge.required_search_deadline(100.0, 112.0), 108.0)

    def test_window_keeps_the_post_lookup_reserve_before_the_bridge_deadline(self):
        bridge = self.bridge()
        # A late start (slow claim, or the CAIRN-184 1.5 s lock wait) shortens
        # the required window instead of the lease renewal and ledger writes.
        self.assertEqual(bridge.required_search_deadline(102.5, 112.0), 109.0)
        for started in (100.0, 101.5, 104.0, 108.0, 111.0, 113.0):
            self.assertLessEqual(bridge.required_search_deadline(started, 112.0),
                                 112.0 - bridge.REQUIRED_SEARCH_OUTER_RESERVE_SECONDS)

    def test_window_never_shorter_than_the_former_recall_cap_when_time_allows(self):
        bridge = self.bridge()
        for started in (100.0, 100.5, 101.0, 101.5, 102.0):
            self.assertGreaterEqual(bridge.required_search_deadline(started, 112.0), min(started + 5, 112.0))


if __name__ == '__main__':
    unittest.main()
