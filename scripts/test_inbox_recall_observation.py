"""Public bound-coordinator metering, without a provider or persistent store."""
import hashlib
import json
from unittest.mock import patch
import unittest

import test_claude_inbox_recall as claude
import test_inbox_recall_bridge as fixtures


class InboxRecallObservationTests(unittest.TestCase):
    def fixture(self, kind=claude.ClaudeInboxRecallTests):
        case = kind()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.mc['recall_observations'] = True
        case.fixture['search']['receipt_id'] = fixtures.EXEC
        case.freeze()
        source = case.cli.read_text().replace("if op=='search':", """if op=='recall-observation':
 if fixture.get('fail_report'):sys.exit(7)
 import fcntl
 locks=[]
 for name in ('memory','coordination'):
  with (root/name/'11111111-1111-4111-8111-111111111111.lock').open('a') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   locks.append(name)
 with (root/'reports.jsonl').open('a') as f:f.write(json.dumps(dict(request=json.loads(body),locks_released=locks))+'\\n')
 result=dict(observation_id='fixture')
elif op=='search':""")
        case.cli.write_text(source)
        return case

    def reports(self, case):
        path = case.root / 'reports.jsonl'
        return [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []

    def test_public_bound_hooks_report_full_returned_context_after_both_locks(self):
        for kind in (claude.ClaudeInboxRecallTests, fixtures.InboxRecallBridgeTests):
            with self.subTest(harness=kind.__name__):
                case = self.fixture(kind)
                out = case.invoke(main=True)
                self.assertEqual(out['code'], 0, out)
                text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
                self.assertIn(case.body, text)
                reports = self.reports(case)
                self.assertEqual(len(reports), 1)
                report = reports[0]['request']
                self.assertEqual(reports[0]['locks_released'], ['memory', 'coordination'])
                self.assertEqual(report['method'], 'cairn-lifecycle/inbox-recall-meter/1')
                self.assertEqual(report['status'], 'completed')
                self.assertEqual(report['injected_bytes'], len(text.encode()))
                self.assertLess(report['injected_bytes'], len(out['stdout'].encode()))
                self.assertEqual((report['search_calls'], report['pull_calls']), (2, 1))
                self.assertEqual(report['receipt_ids'], [fixtures.EXEC])
                self.assertEqual((report['selector_calls'], report['selector_ms']), ([], 0))
                self.assertGreaterEqual(report['elapsed_ms'], report['search_ms'] + report['pulls_ms'])
                self.assertNotIn(case.body, json.dumps(report))
                self.assertEqual(case.calls()[-1]['op'], 'recall-observation')

    def update_fixture(self, case):
        (case.root / 'fixture.json').write_text(json.dumps(case.fixture))

    def test_unvalidated_destination_or_failed_initial_search_stays_unobserved(self):
        for value in ('TIMEOUT', 'AUTHORITY_DENIED', None, {'name': 'local', 'allow_local': True}):
            with self.subTest(value=value):
                case = self.fixture()
                if isinstance(value, str):
                    case.fixture['fail_search'] = value
                elif value is None:
                    case.fixture['search'].pop('destination')
                else:
                    case.fixture['search']['destination'] = value
                self.update_fixture(case)
                out = case.invoke(main=True)
                self.assertEqual(out['code'], 2, out)
                self.assertEqual(out['stdout'], '')
                self.assertEqual(self.reports(case), [])
                self.assertEqual([call['op'] for call in case.calls()], ['search'])
                self.assertEqual(case.ledger()['grants'], {})

    def test_validated_profile_source_failure_reports_refusal_without_body(self):
        case = self.fixture()
        case.fixture['fail_history'] = 'AUTHORITY_DENIED'
        self.update_fixture(case)
        out = case.invoke(main=True)
        self.assertEqual((out['code'], out['stdout']), (2, ''))
        row = self.reports(case)[0]
        self.assertEqual(row['locks_released'], ['memory', 'coordination'])
        report = row['request']
        self.assertEqual((report['status'], report['error_class']), ('error', 'HookError'))
        self.assertEqual((report['injected_bytes'], report['search_calls'], report['pull_calls']), (0, 1, 0))
        self.assertEqual(case.ledger()['grants'], {})

    def test_final_ledger_save_failure_cannot_report_successful_injection(self):
        case = self.fixture()
        engine = case.root / 'memory.py'
        engine.write_text(engine.read_text() + """
_original_save_budget = save_codex_budget
def save_codex_budget(path, value):
    if path.name.endswith('.inbox-recall.json'):
        raise OSError('private fixture detail must not enter report')
    return _original_save_budget(path, value)
""")
        manifest = json.loads(case.binding.read_text())
        manifest['engine']['sha256'] = hashlib.sha256(engine.read_bytes()).hexdigest()
        case.binding.write_text(json.dumps(manifest))
        out = case.invoke(main=True)
        self.assertEqual(out['code'], 2, out)
        self.assertEqual(out['stdout'], '')
        report = self.reports(case)[0]['request']
        self.assertEqual((report['status'], report['error_class'], report['injected_bytes']), ('error', 'OSError', 0))
        self.assertEqual((report['search_calls'], report['pull_calls']), (2, 1))
        self.assertNotIn('private fixture', json.dumps(report))
        self.assertEqual(case.ledger()['grants'], {})

    def test_report_failure_does_not_change_success_or_refusal(self):
        for fail_recall in (False, True):
            with self.subTest(fail_recall=fail_recall):
                case = self.fixture()
                case.fixture['fail_report'] = True
                if fail_recall:
                    case.fixture['fail_history'] = 'AUTHORITY_DENIED'
                self.update_fixture(case)
                out = case.invoke(main=True)
                self.assertEqual(out['code'], 2 if fail_recall else 0, out)
                if not fail_recall:
                    text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
                    self.assertIn(case.body, text)
                    self.assertIn(case.fixture['pull']['selection']['record']['body'], text)
                    self.assertEqual(case.ledger()['grants'][case.ledger()['active']]['output_bytes'], len(out['stdout'].encode()))
                self.assertEqual(self.reports(case), [])  # Unavailable observation, never fabricated zero.
                self.assertEqual(case.calls()[-1]['op'], 'recall-observation')

    def test_optional_failure_reports_actual_calls_and_reduced_returned_context(self):
        case = self.fixture()
        case.fixture['fail_task_search'] = 'API_CONNECTION_FAILED'
        self.update_fixture(case)
        out = case.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        text = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
        self.assertIn(case.body, text)
        self.assertNotIn(case.fixture['pull']['selection']['record']['body'], text)
        report = self.reports(case)[0]['request']
        self.assertEqual(report['status'], 'completed')
        self.assertEqual((report['search_calls'], report['pull_calls']), (2, 0))
        self.assertEqual(report['injected_bytes'], len(text.encode()))

    def test_reporting_is_clipped_to_coordinator_deadline_and_margin(self):
        for remaining, expected in [(1.1, .35), (.8, None)]:
            with self.subTest(remaining=remaining):
                case = self.fixture()
                load = fixtures.coord.load_inbox_recall
                captured = []
                def instrument(config, **kwargs):
                    bridge = load(config, **kwargs)
                    binding = bridge.binding
                    def observed_binding(value):
                        engine, mc = binding(value)
                        report = engine.report_recall
                        def observed_report(**kwargs):
                            self.assertIsNotNone(kwargs['deadline'])
                            with patch.object(engine.time, 'monotonic', return_value=kwargs['deadline'] - remaining), \
                                 patch.object(engine, 'run_json', side_effect=lambda command, **kw: captured.append(kw)):
                                report(**kwargs)
                        engine.report_recall = observed_report
                        return engine, mc
                    bridge.binding = observed_binding
                    return bridge
                with patch.object(fixtures.coord, 'load_inbox_recall', side_effect=instrument):
                    out = case.invoke(main=True)
                self.assertEqual(out['code'], 0, out)
                self.assertEqual(len(captured), 0 if expected is None else 1)
                if captured:
                    self.assertAlmostEqual(captured[0]['timeout'], expected, places=5)

    def test_replay_and_invalid_binding_do_not_create_fallback_observations(self):
        case = self.fixture()
        self.assertEqual(case.invoke(main=True)['code'], 0)
        before = case.calls()
        self.assertEqual(case.invoke(main=True)['code'], 2)
        self.assertEqual(case.calls(), before)
        self.assertEqual(len(self.reports(case)), 1)
        fresh = self.fixture()
        (fresh.root / 'memory.py').write_text('invalid pinned engine')
        self.assertEqual(fresh.invoke(main=True)['code'], 2)
        self.assertEqual(fresh.calls(), [])
        self.assertEqual(self.reports(fresh), [])

    def test_older_bridge_and_engine_remain_unobserved_without_new_failure(self):
        for old in ('bridge', 'engine', 'engine_signature'):
            with self.subTest(old=old):
                case = self.fixture()
                load = fixtures.coord.load_inbox_recall
                def legacy(config, **kwargs):
                    bridge = load(config, **kwargs)
                    if old == 'bridge':
                        del bridge.RECALL_OBSERVATION_VERSION
                        output = bridge.output
                        def old_output(*args, revalidate, outer_deadline):
                            return output(*args, revalidate=revalidate, outer_deadline=outer_deadline)
                        bridge.output = old_output
                    else:
                        binding = bridge.binding
                        def old_binding(value):
                            engine, mc = binding(value)
                            if old == 'engine':
                                del engine.report_recall
                            else:
                                def old_report(memory, event, started, elapsed, status, injected, error_class=None):
                                    raise AssertionError('incompatible reporter must not run')
                                engine.report_recall = old_report
                            return engine, mc
                        bridge.binding = old_binding
                    return bridge
                with patch.object(fixtures.coord, 'load_inbox_recall', side_effect=legacy):
                    out = case.invoke(main=True)
                self.assertEqual(out['code'], 0, out)
                self.assertIn(case.body, json.loads(out['stdout'])['hookSpecificOutput']['additionalContext'])
                self.assertEqual(self.reports(case), [])


if __name__ == '__main__':
    unittest.main()
