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
                               ('API_CONNECTION_FAILED', 2), ('AUTHORITY_DENIED', 1),
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
                self.assertFalse((case.root/'memory'/f'{fixtures.SESSION}.inbox-recall.json').exists())
                if count == 2:
                    self.assertEqual(case.calls()[0]['args'], case.calls()[1]['args'])

    def test_transient_required_lookup_preserves_whole_notice_context(self):
        for harness, failure in itertools.product(
                (fixtures.InboxRecallBridgeTests, claude.ClaudeInboxRecallTests),
                ('TIMEOUT', 'API_UNAVAILABLE', 'API_CONNECTION_FAILED', 'process_timeout')):
            with self.subTest(harness=harness.__name__, failure=failure):
                case = harness()
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.delivery['event']['kind'] = 'notice'
                required = 'Required whole instruction: retain source and ownership checks.'
                case.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body=required))]
                case.freeze()
                fail = ("import time;time.sleep(3)" if failure == 'process_timeout' else
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


if __name__ == '__main__':
    unittest.main()
