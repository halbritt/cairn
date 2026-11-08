"""Closed refusal diagnostics through temporary pinned hooks and a fake CLI."""
import json
import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_inbox_recall_bridge as fixture


class NativeRefusalDiagnosticsTests(unittest.TestCase):
    setUp = fixture.InboxRecallBridgeTests.setUp
    freeze = fixture.InboxRecallBridgeTests.freeze
    calls = fixture.InboxRecallBridgeTests.calls
    invoke = fixture.InboxRecallBridgeTests.invoke

    def changed_engine(self, source):
        engine = self.root / 'memory.py'
        engine.write_text(engine.read_text() + '\n' + source)
        manifest = json.loads(self.binding.read_text())
        fixture.repin_runtime(manifest)
        self.binding.write_text(json.dumps(manifest))

    def refused(self, stage, reason, *, inspect=True, **options):
        result = self.invoke(main=True, **options)
        self.assertEqual((result['code'], result['stdout']), (2, ''), result)
        state = json.loads(fixture.coord.state_path(self.cc, fixture.SESSION).read_text())
        diagnostic = state['wake_refusal_diagnostic']
        self.assertEqual((diagnostic.get('refusal_stage'), diagnostic.get('refusal_reason')), (stage, reason))
        self.assertEqual(diagnostic['settlement'], 'held')
        self.assertNotIn('finished_at', state['inbox_attempt'])
        self.assertEqual(state['inbox_attempt']['attempt_id'], fixture.ATTEMPT)
        for call in self.calls():
            self.assertIn(call['op'], ('search', 'history', 'pull'))
        journal = fixture.coord.intent_path(self.cc['state_dir'], fixture.ATTEMPT)
        if journal.exists():
            saved = json.loads(journal.read_text())
            self.assertFalse(saved.get('completion') or saved.get('response'))
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(diagnostic) + result['stderr'])
        with patch.object(fixture.coord, 'process_alive', return_value=True):
            observed = fixture.coord.held_attempt_refusal_observation(self.cc, state)
        if inspect:
            self.assertEqual((observed['refusal_stage'], observed['refusal_reason']), (stage, reason))
        else:
            self.assertIsNone(observed, 'mismatched session must not become a current-attempt observation')
        return diagnostic

    def test_postclaim_member_hash_refusal_keeps_the_bootstrap_guard_and_names_it(self):
        member = self.root / 'memory.py'
        original = member.read_bytes()
        def changed_member():
            member.write_bytes(original + b'\n# PRIVATE_SENTINEL\n')
        diagnostic = self.refused('loader', 'member_hash', on_renew=changed_member)
        self.assertEqual((diagnostic['error_class'], diagnostic['cause_class']), ('ValueError', None))
        self.assertEqual(self.calls(), [])

    def test_attempt_session_mismatch_is_an_identity_refusal(self):
        self.state['inbox_attempt']['session'] = dict(agent_id=fixture.AGENT, execution_id=fixture.RECORD)
        self.freeze()
        self.refused('identity', 'attempt_session', inspect=False)

    def test_invalid_inbox_accounting_names_its_guard(self):
        directory = self.root / 'memory'
        directory.mkdir(mode=0o700)
        (directory / (fixture.SESSION + '.inbox-recall.json')).write_text(json.dumps(dict(
            schema='cairn.inbox-recall-grants/1', session=fixture.SESSION, grants={}, pending_bytes=True)))
        self.refused('inbox_ledger', 'pending_charge')

    def test_optional_nonhosted_result_is_distinct_from_source_validation(self):
        self.fixture['task_search'] = copy.deepcopy(self.fixture['search'])
        self.fixture['task_search']['destination'] = dict(name='hosted', allow_local=True)
        self.freeze()
        self.refused('optional_search', 'hosted_destination')

    def test_required_context_too_large_records_only_bounded_decision_counters(self):
        self.fixture['search']['selected'] = [dict(mandatory=True, record=dict(body='PRIVATE_SENTINEL' * 1000))]
        self.freeze()
        diagnostic = self.refused('required_render', 'required_context')
        self.assertEqual({k: diagnostic[k] for k in ('configured_budget_bytes', 'budget_bytes', 'prior_charge_bytes')},
                         dict(configured_budget_bytes=9500, budget_bytes=9500, prior_charge_bytes=0))

    def test_optional_required_context_has_its_own_budget_guard(self):
        self.fixture['task_search'] = copy.deepcopy(self.fixture['search'])
        self.fixture['task_search']['selected'] = [dict(mandatory=True, record=dict(body='PRIVATE_SENTINEL' * 1000))]
        self.freeze()
        self.refused('optional_render', 'task_context')

    def test_prior_native_charge_reports_actual_remaining_budget_without_signed_or_coerced_fields(self):
        for prior, legacy, charged, expected in ((9000, False, 9000, dict(budget_bytes=500)),
                                               (10000, False, 10000, {}), (65536, True, 131151, {})):
            with self.subTest(prior=prior):
                case = type(self)()
                case.setUp()
                self.addCleanup(case.doCleanups)
                directory = case.root / 'memory'
                directory.mkdir(mode=0o700)
                ledger = dict(schema='cairn.codex-memory-grants/1', session_id=fixture.SESSION,
                    active_turn_id=None, pending=dict(limit_bytes=max(12000, prior), emitted_hook_bytes=prior,
                        reserved_native_bytes=0, granted=False), turns={})
                if not legacy:
                    ledger['pending']['serialized_hook_bytes'] = prior
                (directory / (fixture.SESSION + '.memory-budget.json')).write_text(json.dumps(ledger))
                diagnostic = case.refused('required_render', 'required_context')
                self.assertEqual(diagnostic['prior_charge_bytes'], charged)
                self.assertEqual({k: diagnostic[k] for k in ('budget_bytes',) if k in diagnostic}, expected)

    def test_unexpected_encoding_exception_stays_unknown_at_the_source_stage(self):
        self.fixture['history']['versions'][0]['body'] = '\ud800 PRIVATE_SENTINEL'
        self.freeze()
        diagnostic = self.refused('task_source', 'unknown')
        self.assertEqual(diagnostic['cause_class'], None)

    def test_required_lookup_keeps_chained_cause_and_existing_recall_metadata(self):
        self.fixture['fail_search'] = 'AUTHORITY_DENIED'
        self.freeze()
        diagnostic = self.refused('required_search', 'unknown')
        self.assertEqual(diagnostic['cause_class'], 'HookError')
        self.assertEqual(diagnostic['cause_code'], 'AUTHORITY_DENIED')
        self.assertEqual((diagnostic['recall_phase'], diagnostic['recall_operation']), ('required_search', 'search'))

    def test_reporting_failure_cannot_replace_or_succeed_a_source_refusal(self):
        self.fixture['history']['versions'][0]['body_sha256'] = '0' * 64
        self.freeze()
        self.changed_engine("def report_recall(**kwargs):\n"
                            "    Path(__file__).with_name('report-attempt').write_text('attempted')\n"
                            "    raise ValueError('PRIVATE_SENTINEL')\n")
        self.refused('task_source', 'source_version')
        self.assertEqual((self.root / 'report-attempt').read_text(), 'attempted')

    def test_final_bridge_checks_refuse_invalid_results_from_the_independently_pinned_engine(self):
        for text, allowance, reason in (('PRIVATE_SENTINEL' * 1000, 0, 'output_budget'),
                                        ('PRIVATE_SENTINEL', 9500, 'allowance_budget')):
            with self.subTest(reason=reason):
                case = type(self)()
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.changed_engine("def eager_agent_candidates(memory, found, budget, status, deadline, **kwargs):\n"
                                    f"    status.update(outcome='delegated', native_allowance_bytes={allowance})\n"
                                    f"    return {text!r}\n")
                diagnostic = case.refused('final_render', reason)
                if allowance:
                    self.assertEqual(diagnostic['native_allowance_bytes'], allowance)

    def test_final_lease_revalidation_refusal_retains_its_stage(self):
        renewals = []
        def renew():
            renewals.append(True)
            if len(renewals) == 2:
                raise fixture.coord.CoordinationError('STALE_LEASE', 'PRIVATE_SENTINEL')
        self.refused('lease_revalidation', 'unknown', on_renew=renew)
        self.assertEqual(len(renewals), 2)

    def test_failed_grant_write_retains_uncertain_persisted_accounting_and_refuses(self):
        self.changed_engine("_save = save_codex_budget\n"
                            "def save_codex_budget(path, value):\n"
                            "    if path.name.endswith('.inbox-recall.json') and value.get('grants'):\n"
                            "        raise OSError('PRIVATE_SENTINEL')\n"
                            "    return _save(path, value)\n")
        self.refused('grant_persistence', 'unknown')
        turn_ledger = json.loads((self.root / 'memory' / (fixture.SESSION + '.memory-budget.json')).read_text())
        self.assertTrue(turn_ledger['turns'][fixture.TURN]['granted'])
        self.assertFalse((self.root / 'memory' / (fixture.SESSION + '.inbox-recall.json')).exists())

    def test_diagnostic_write_failure_cannot_turn_refusal_into_success_or_replay(self):
        self.fixture['history']['versions'][0]['body_sha256'] = '0' * 64
        self.freeze()
        replace = os.replace
        state_path = fixture.coord.state_path(self.cc, fixture.SESSION)
        rejected = []
        def refuse_diagnostic_write(source, target, *args, **kwargs):
            if Path(target) == state_path and 'wake_refusal_diagnostic' in json.loads(Path(source).read_text()):
                rejected.append(True)
                raise OSError('PRIVATE_SENTINEL')
            return replace(source, target, *args, **kwargs)
        with patch.object(os, 'replace', side_effect=refuse_diagnostic_write):
            result = self.invoke(main=True)
        self.assertTrue(rejected, 'the durable diagnostic write was not reached')
        self.assertEqual((result['code'], result['stdout']), (2, ''))
        saved = json.loads(state_path.read_text())
        self.assertNotIn('wake_refusal_diagnostic', saved)
        self.assertNotIn('finished_at', saved['inbox_attempt'])
        self.assertNotIn('PRIVATE_SENTINEL', result['stderr'])

    def test_untrusted_diagnostic_attributes_are_sanitized_at_the_durable_boundary(self):
        cases = [
            (dict(refusal_stage='PRIVATE_SENTINEL', refusal_reason='source_identity'), 'identity', 'unknown', {}),
            (dict(refusal_stage='identity', refusal_reason='PRIVATE_SENTINEL',
                  configured_budget_bytes=True, budget_bytes='100', prior_charge_bytes=1.5,
                  native_allowance_bytes=-1, raw_error='PRIVATE_SENTINEL'), 'identity', 'unknown', {}),
            (dict(refusal_stage='required_render', refusal_reason='required_context',
                  configured_budget_bytes=True, budget_bytes='100', prior_charge_bytes=1.5,
                  native_allowance_bytes=-1), 'required_render', 'required_context', {}),
            (dict(refusal_stage='required_render', refusal_reason='required_context', configured_budget_bytes=9501,
                  budget_bytes=9500, prior_charge_bytes=131152, native_allowance_bytes=9501),
             'required_render', 'required_context', dict(budget_bytes=9500)),
        ]
        for attributes, stage, reason, expected_numbers in cases:
            with self.subTest(attributes=attributes):
                case = type(self)()
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.changed_engine("def project_for(cwd):\n    error = ValueError('PRIVATE_SENTINEL')\n"
                                    f"    error.cairn_inbox_refusal = {attributes!r}\n    raise error\n")
                diagnostic = case.refused(stage, reason)
                keys = ('configured_budget_bytes', 'budget_bytes', 'prior_charge_bytes', 'native_allowance_bytes')
                self.assertEqual({k: diagnostic[k] for k in keys if k in diagnostic}, expected_numbers)

    def test_source_digest_refusal_names_the_guard_without_delivering_or_replaying(self):
        self.fixture['history']['versions'][0]['body_sha256'] = '0' * 64
        self.freeze()
        diagnostic = self.refused('task_source', 'source_version')
        self.assertEqual((diagnostic['error_class'], diagnostic['cause_class']), ('ValueError', None))


if __name__ == '__main__':
    unittest.main()
