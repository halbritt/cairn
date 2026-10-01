"""Per-prompt recall observations reported by the lifecycle hook (use-report input).

The hook meters its own invocation: wall time by phase, selector calls with
model, token usage and provider-reported cost, status and injected bytes. A
value it did not observe is omitted, never reported as zero, and reporting never
changes what the hook returns or whether it fails.
"""
import itertools
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_receipt_accounting import hook

RECEIPT = '3b241101-e2bb-4255-8caf-38c3f4bd2dc9'
SECOND_RECEIPT = '0b7b3d5c-7a51-4bf8-9f44-2a0c4c6ac5a1'
ALLOWED_KEYS = {'request_id', 'repo', 'receipt_ids', 'harness', 'hook_event', 'method', 'status', 'error_class',
                'elapsed_ms', 'search_ms', 'selector_ms', 'pulls_ms', 'search_calls', 'pull_calls',
                'selector_calls', 'injected_bytes'}
ALLOWED_CALL_KEYS = {'stage', 'outcome', 'elapsed_ms', 'model', 'model_source', 'input_tokens', 'output_tokens',
                     'cache_creation_input_tokens', 'cache_read_input_tokens', 'cost_usd'}
SESSIONS = dict(claude='caed9473-b01a-41e7-95ce-c3c1f28d66b3', codex='caed9473-b01a-41e7-95ce-c3c1f28d66b3',
                opencode='ses_0123456789abcdefABCDEF', hermes='opaque-hermes-session')


class Clock:
    """Deterministic monotonic time that only the fake transport and selector advance."""
    def __init__(self):
        self.now = 5000.0

    def __call__(self):
        return self.now


class Transport:
    def __init__(self, clock, durations=None):
        self.clock = clock
        self.durations = dict(search=0.25, pull=0.5) | (durations or {})
        self.failures = {}
        self.commands = []
        self.reports = []
        self.report_timeouts = []
        self.semantic_receipt = False
        self.body = 'fixture: use concise field names'

    def __call__(self, command, *, body, timeout=None, **kwargs):
        operation = command[6]
        self.commands.append(operation)
        self.clock.now += self.durations.get(operation, 0)
        failure = self.failures.get(operation)
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(command, timeout)
        if failure == 'exit':
            return subprocess.CompletedProcess(command, 2, '{"ok":false,"status":"INTERNAL"}', 'private stderr')
        if failure == 'garbage':
            return subprocess.CompletedProcess(command, 0, 'not json', '')
        if failure == 'raise':
            raise OSError('transport unavailable')
        if operation == 'recall-observation':
            self.reports.append(json.loads(body))
            self.report_timeouts.append(timeout)
            data = dict(observation_id='observation')
        elif operation == 'search':
            semantic = '--semantic' in command
            receipt = SECOND_RECEIPT if semantic and self.semantic_receipt else RECEIPT
            entry = dict(record_id='optional', version=1, kind='preference', summary=self.body,
                         pull_arguments=dict(receipt_id=receipt, handle='optional', request_id='pull'))
            data = dict(status='READY', destination=dict(name='hosted'), receipt_id=receipt, credits_remaining=4,
                        selected=[dict(mandatory=True, record=dict(record_id='required', version=1,
                                                                     body='Required instruction'))],
                        index=[entry])
        else:
            assert operation == 'pull', command
            data = dict(selection=dict(record=dict(record_id='optional', version=1, body=self.body)),
                        credits_remaining=3)
        return subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=data)), '')


class RecallObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'fixture'
        (self.root / '.git').mkdir(parents=True)
        self.clock = Clock()
        self.states = itertools.count()
        self.transport = Transport(self.clock)
        patcher = patch.object(hook.time, 'monotonic', self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, harness='claude', **extra):
        config = dict(cairn='fixture', socket='fixture', token_file='fixture', repo='fixture:repo',
                      state_dir=str(self.root / ('state-%s-%d' % (harness, next(self.states)))), context_bytes=9500, **extra)
        if harness != 'claude':
            config['harness'] = harness
        return config

    def event(self, harness='claude', **extra):
        return dict(hook_event_name='UserPromptSubmit', cwd=str(self.root), session_id=SESSIONS[harness],
                    prompt='Use concise field names', **extra)

    def handle(self, config, event, selector=None):
        with patch.object(hook, 'bounded_command', side_effect=self.transport):
            if selector is None:
                return hook.handle(config, event)
            with patch.object(hook, 'select_json', side_effect=selector):
                return hook.handle(config, event)

    def test_each_harness_reports_phases_receipts_and_injected_bytes_once(self):
        for harness in ('claude', 'codex', 'opencode', 'hermes'):
            with self.subTest(harness=harness):
                self.transport.reports.clear()
                result = self.handle(self.config(harness), self.event(harness))
                context = result['hookSpecificOutput']['additionalContext']
                self.assertIn(self.transport.body, context)
                self.assertEqual(len(self.transport.reports), 1)
                report = self.transport.reports[0]
                self.assertEqual(set(report), ALLOWED_KEYS - {'error_class'})
                self.assertEqual(report['harness'], harness)
                self.assertEqual(report['repo'], 'fixture:repo')
                self.assertEqual(report['hook_event'], 'UserPromptSubmit')
                self.assertEqual(report['method'], hook.RECALL_METER_METHOD)
                self.assertEqual(report['status'], 'completed')
                self.assertEqual(report['receipt_ids'], [RECEIPT])
                self.assertEqual((report['search_ms'], report['pulls_ms'], report['elapsed_ms']), (250, 500, 750))
                self.assertEqual((report['search_calls'], report['pull_calls']), (1, 1))
                # No selector ran: that is an observed empty list, not an unreported field.
                self.assertEqual((report['selector_calls'], report['selector_ms']), ([], 0))
                self.assertEqual(report['injected_bytes'], len(context.encode()))
                self.assertEqual(self.transport.commands[-1], 'recall-observation')

    def test_session_start_and_empty_recall_are_observed_with_zero_injection(self):
        self.transport.durations['search'] = 0.1
        config = self.config()
        with patch.object(hook, 'bounded_command', side_effect=self.transport):
            empty = lambda command, **kwargs: subprocess.CompletedProcess(command, 0, json.dumps(dict(ok=True, data=dict(
                status='READY', destination=dict(name='hosted'), selected=[], index=[], receipt_id=RECEIPT))), '')
            real = self.transport.__call__
            def transport(command, **kwargs):
                return real(command, **kwargs) if command[6] == 'recall-observation' else empty(command, **kwargs)
            with patch.object(hook, 'bounded_command', side_effect=transport):
                result = hook.handle(config, dict(self.event(), hook_event_name='SessionStart', source='startup', prompt='Use concise field names'))
        self.assertEqual(result, {})
        report = self.transport.reports[0]
        self.assertEqual((report['hook_event'], report['status'], report['injected_bytes']), ('SessionStart', 'completed', 0))
        self.assertEqual((report['search_calls'], report['pull_calls'], report['pulls_ms']), (1, 0, 0))

    def test_selector_calls_report_model_usage_and_provider_cost_without_inventing_any(self):
        self.transport.semantic_receipt = True
        verdicts = [
            dict(structured_output=dict(index=0), total_cost_usd=0.0123, modelUsage={'provider-model-7': {}},
                 usage=dict(input_tokens=2100, output_tokens=40, cache_creation_input_tokens=0, cache_read_input_tokens=900)),
            dict(structured_output=dict(index=0)),  # a provider that reports no usage, cost or model
        ]
        for verdict in verdicts:
            with self.subTest(verdict=sorted(verdict)):
                self.transport.reports.clear()

                def selector(config, schema, prompt, request, timeout, stage='capture', observation=None):
                    self.clock.now += 1.2
                    observation['requested_model'] = 'configured-alias'
                    return verdict

                self.handle(self.config(semantic_fallback=True), self.event(), selector)
                report = self.transport.reports[0]
                self.assertEqual((report['status'], report['selector_ms'], report['elapsed_ms']), ('completed', 1200, 2200))
                self.assertEqual(report['receipt_ids'], [RECEIPT, SECOND_RECEIPT])
                (call,) = report['selector_calls']
                self.assertEqual((call['stage'], call['outcome'], call['elapsed_ms']), ('recall', 'completed', 1200))
                self.assertLessEqual(set(call), ALLOWED_CALL_KEYS)
                if 'total_cost_usd' in verdict:
                    self.assertEqual((call['model'], call['model_source']), ('provider-model-7', 'reported'))
                    self.assertEqual((call['input_tokens'], call['output_tokens']), (2100, 40))
                    self.assertEqual((call['cache_creation_input_tokens'], call['cache_read_input_tokens']), (0, 900))
                    self.assertEqual(call['cost_usd'], 0.0123)
                else:
                    self.assertEqual((call['model'], call['model_source']), ('configured-alias', 'requested'))
                    for unknown in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens',
                                    'cache_read_input_tokens', 'cost_usd'):
                        self.assertNotIn(unknown, call)

    def test_malformed_provider_figures_are_ignored_not_zeroed(self):
        verdict = dict(structured_output=dict(index=0), total_cost_usd=float('nan'), usage=dict(
            input_tokens=-1, output_tokens=True, cache_creation_input_tokens=1.5, cache_read_input_tokens='9'),
            modelUsage={'a': {}, 'b': {}})
        self.handle(self.config(semantic_fallback=True), self.event(),
                    lambda *args, observation=None, **kwargs: verdict)
        (call,) = self.transport.reports[0]['selector_calls']
        # Two reported models cannot be assigned to one call, and no requested model exists.
        self.assertEqual(set(call), {'stage', 'outcome', 'elapsed_ms'})

    def test_selector_failure_is_a_timeout_or_error_but_a_call_that_never_started_is_not_counted(self):
        def timed_out(config, schema, prompt, request, timeout, stage='capture', observation=None):
            observation['requested_model'] = 'configured-alias'
            self.clock.now += 8
            raise hook.HookError('command timed out; memory operation not confirmed', code='TIMEOUT')

        def reported_error(config, schema, prompt, request, timeout, stage='capture', observation=None):
            observation['requested_model'] = None
            return dict(is_error=True, total_cost_usd=0.002, usage=dict(input_tokens=10))

        def never_started(config, schema, prompt, request, timeout, stage='capture', observation=None):
            raise hook.HookError('invalid lifecycle recall_model: expected a nonempty model name without whitespace or controls')

        for selector, expected in ((timed_out, [('timeout', 'configured-alias')]), (reported_error, [('error', None)]),
                                   (never_started, [])):
            with self.subTest(selector=selector.__name__):
                self.transport.reports.clear()
                self.handle(self.config(semantic_fallback=True), self.event(), selector)
                report = self.transport.reports[0]
                self.assertEqual(report['status'], 'completed')  # the hook still delivered its required context
                self.assertEqual([(c['outcome'], c.get('model')) for c in report['selector_calls']], expected)
        # An errored call that returned usage and cost still reports them; they are the provider's, not an estimate.
        self.transport.reports.clear()
        self.handle(self.config(semantic_fallback=True), self.event(), reported_error)
        self.assertEqual(self.transport.reports[0]['selector_calls'][0]['cost_usd'], 0.002)

    def test_hook_failures_report_timeout_or_error_and_still_raise(self):
        for failure, status in (('timeout', 'timeout'), ('exit', 'error'), ('garbage', 'error')):
            with self.subTest(failure=failure):
                transport = Transport(self.clock)
                transport.failures['search'] = failure
                with patch.object(hook, 'bounded_command', side_effect=transport), \
                     self.assertRaises(hook.HookError):
                    hook.handle(self.config(), self.event())
                (report,) = transport.reports
                self.assertEqual((report['status'], report['error_class'], report['injected_bytes']), (status, 'HookError', 0))
                self.assertEqual((report['search_calls'], report['search_ms'], report['pull_calls']), (1, 250, 0))
                self.assertEqual(report['elapsed_ms'], 250)  # the failed attempt's own time, not zero
                self.assertNotIn('receipt_ids', report)  # no receipt was produced

    def test_report_failure_never_changes_hook_output_or_exit(self):
        baseline = self.handle(self.config(recall_observations=False), self.event())
        self.assertEqual(self.transport.reports, [])
        for failure in ('timeout', 'exit', 'garbage', 'raise'):
            with self.subTest(failure=failure):
                transport = Transport(self.clock)
                transport.failures['recall-observation'] = failure
                with patch.object(hook, 'bounded_command', side_effect=transport):
                    result = hook.handle(self.config(), self.event())
                self.assertEqual(result, baseline)
                self.assertEqual(transport.commands[-1], 'recall-observation')

    def test_opt_out_and_unknown_harness_send_nothing(self):
        for config in (self.config(recall_observations=False), self.config(harness='other-agent')):
            with self.subTest(config=config.get('harness'), off=config.get('recall_observations')):
                event = self.event()
                if config.get('harness') == 'other-agent':
                    SESSIONS['other-agent'] = SESSIONS['claude']
                    self.addCleanup(SESSIONS.pop, 'other-agent')
                    event = self.event('other-agent')
                self.handle(config, event)
                self.assertNotIn('recall-observation', self.transport.commands)

    def test_report_time_is_bounded_and_not_part_of_elapsed(self):
        self.transport.durations['recall-observation'] = 5
        config = self.config()
        result = self.handle(config, self.event())
        self.assertIn('Required instruction', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.transport.reports[0]['elapsed_ms'], 750)
        self.assertLessEqual(self.transport.report_timeouts[0], hook.RECALL_REPORT_SECONDS)
        state = json.loads((Path(config['state_dir']) / (SESSIONS['claude'] + '.json')).read_text())
        self.assertEqual(state['last_recall']['duration_ms'], 750.0)

    def test_no_report_is_attempted_when_it_would_overrun_the_host_timeout(self):
        # The recall deadline (11 s) passed, so optional pulls stop, but required context is delivered.
        self.transport.durations['search'] = 12.5
        result = self.handle(self.config(), self.event())
        self.assertIn('Required instruction', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.transport.reports, [])

    def test_report_carries_metrics_only(self):
        self.transport.body = 'SECRET-NOTE-BODY: use concise field names'
        self.handle(self.config(semantic_fallback=True), dict(self.event(), prompt='SECRET-PROMPT use concise field names'),
                    lambda *args, observation=None, **kwargs: dict(structured_output=dict(index=0), result='SECRET-MODEL-TEXT'))
        encoded = json.dumps(self.transport.reports[0])
        for secret in ('SECRET-PROMPT', 'SECRET-NOTE-BODY', 'SECRET-MODEL-TEXT', str(self.root), 'private owner'):
            self.assertNotIn(secret, encoded)
        self.assertLessEqual(set(self.transport.reports[0]), ALLOWED_KEYS)

    def test_receipts_are_canonical_unique_and_bounded(self):
        meter = hook.RecallMeter()
        for value in (RECEIPT, RECEIPT, 'receipt', None, 7, '3B241101-E2BB-4255-8CAF-38C3F4BD2DC9',
                      '00000000-0000-0000-0000-000000000000'):
            meter.receipt(value)
        for index in range(20):
            meter.receipt('00000000-0000-4000-8000-%012d' % (index + 1))
        self.assertEqual(meter.receipts[0], RECEIPT)
        self.assertEqual(len(meter.receipts), hook.RECALL_METER_LIMIT)
        self.assertEqual(len(set(meter.receipts)), len(meter.receipts))


if __name__ == '__main__':
    unittest.main()
