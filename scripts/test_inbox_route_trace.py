"""Opt-in selected route evidence through the actual bound hook, no provider."""
import json
import hashlib
import unittest

import test_claude_inbox_recall as claude
import test_inbox_recall_bridge as fixtures


class InboxRouteTraceTests(unittest.TestCase):
    def fixture(self, enabled=False, kind=claude.ClaudeInboxRecallTests):
        case = kind()
        case.setUp()
        self.addCleanup(case.doCleanups)
        if enabled:
            case.mc['inbox_recall_trace'] = True
        case.freeze()
        return case

    def traces(self, case):
        return list((case.root / 'memory' / 'inbox-recall-traces').glob('*.json'))

    def test_opt_in_public_hook_records_metadata_without_changing_output_or_calls(self):
        for kind in (claude.ClaudeInboxRecallTests, fixtures.InboxRecallBridgeTests):
            with self.subTest(kind=kind.__name__):
                case = self.fixture(True, kind)
                out = case.invoke(main=True)
                self.assertEqual(out['code'], 0, out)
                paths = self.traces(case)
                self.assertEqual(len(paths), 1)
                trace = json.loads(paths[0].read_text())
                self.assertTrue(trace['complete'])
                self.assertEqual(trace['status'], 'bridge_returned')
                self.assertEqual(trace['identity']['delivery_id'], fixtures.DELIVERY)
                self.assertEqual([x['operation'] for x in trace['rpc']], ['search', 'history', 'search', 'pull'])
                self.assertEqual([x['phase'] for x in trace['rpc']], ['required', 'task_source', 'optional_search', 'expansion'])
                self.assertEqual(trace['final']['bodies'][0]['record_id'], fixtures.AGENT)
                self.assertEqual(trace['final']['bodies'][0]['extent'], 'whole')
                self.assertEqual(trace['returned_context_bytes'], len(json.loads(out['stdout'])['hookSpecificOutput']['additionalContext'].encode()))
                self.assertEqual([x['op'] for x in case.calls()], ['search', 'history', 'search', 'pull'])
                wire = paths[0].read_text()
                for sentinel in (case.body, 'Preserve the fixture scope', 'Fixture guidance', 'agent/sender', str(case.root)):
                    self.assertNotIn(sentinel, wire)
                self.assertEqual(paths[0].stat().st_mode & 0o777, 0o600)

    def test_tracing_preserves_public_output_and_calls(self):
        cases = [self.fixture(value) for value in (False, True)]
        outputs = [case.invoke(main=True) for case in cases]
        def normalized(value, case):
            return json.dumps(value, sort_keys=True).replace(str(case.root), '<ROOT>').replace(case.root.name, '<PROJECT>')
        self.assertEqual(normalized(outputs[0], cases[0]), normalized(outputs[1], cases[1]))
        self.assertEqual(normalized(cases[0].calls(), cases[0]), normalized(cases[1].calls(), cases[1]))

    def test_failed_persistence_records_zero_returned_bytes_and_no_emission(self):
        case = self.fixture(True)
        engine = case.root / 'memory.py'
        engine.write_text(engine.read_text() + """
_original_save_budget = save_codex_budget
def save_codex_budget(path, value):
    if path.name.endswith('.inbox-recall.json'):
        raise OSError('SECRET failure detail')
    return _original_save_budget(path, value)
""")
        manifest = json.loads(case.binding.read_text())
        manifest['engine']['sha256'] = hashlib.sha256(engine.read_bytes()).hexdigest()
        case.binding.write_text(json.dumps(manifest))
        out = case.invoke(main=True)
        self.assertEqual((out['code'], out['stdout']), (2, ''))
        trace = json.loads(self.traces(case)[0].read_text())
        self.assertEqual((trace['status'], trace['returned_context_bytes']), ('error', 0))
        self.assertNotIn('final', trace)
        self.assertNotIn('SECRET', json.dumps(trace))

    def test_unvalidated_profile_and_private_sink_refusal_do_not_change_delivery(self):
        case = self.fixture(True)
        case.fixture['search']['destination'] = dict(name='local', allow_local=True)
        case.freeze()
        self.assertEqual(case.invoke(main=True)['code'], 2)
        self.assertEqual(self.traces(case), [])
        case = self.fixture(True)
        sink = case.root / 'memory' / 'inbox-recall-traces'
        sink.mkdir(parents=True)
        sink.parent.chmod(0o700)
        sink.chmod(0o755)
        self.assertEqual(case.invoke(main=True)['code'], 0)
        self.assertEqual(self.traces(case), [])

    def test_checked_refused_span_and_fitted_emission_remain_distinct(self):
        case = self.fixture(True)
        body = 'PRIVATE OPTIONAL PASSAGE ' + '日本語 ' * 2000
        raw = body.encode()
        entry = case.fixture['search']['index'][0]
        entry.update(**{'class': 'A'}, body_sha256=hashlib.sha256(raw).hexdigest(), summary_span=dict(offset=0, length=20))
        span_body = raw[:1536].decode(errors='ignore')
        span = dict(offset=0, end=len(span_body.encode()), total_bytes=len(raw), body=span_body,
                    sha256=hashlib.sha256(span_body.encode()).hexdigest(), source_sha256=entry['body_sha256'])
        case.fixture['pull'] = dict(selection=dict(record=dict(record_id=fixtures.AGENT, version=1, body='', **{'class':'A'})),
                                    span=span, credits_remaining=3, bytes_remaining=22000)
        case.mc['context_bytes'] = 7000
        case.freeze()
        code = case.cli.read_text().replace("elif op=='pull': result=fixture['pull']", """elif op=='pull':
 if 'span' not in json.loads(body):
  print(json.dumps(dict(ok=False,status='BUDGET_REFUSED')));sys.exit(7)
 result=fixture['pull']""")
        case.cli.write_text(code)
        out = case.invoke(main=True)
        self.assertEqual(out['code'], 0, out)
        trace = json.loads(self.traces(case)[0].read_text())
        reasons = [row['reason'] for row in trace['routes']]
        self.assertIn('whole_pull_budget', reasons)
        self.assertIn('span_context_budget', reasons)
        emitted = trace['final']['bodies'][0]
        self.assertEqual(emitted['extent'], 'span')
        context = json.loads(out['stdout'])['hookSpecificOutput']['additionalContext']
        view = json.loads(context[context.index('{"selected":'):])
        actual = view['candidate_bodies'][0]['response']['span']
        self.assertEqual(emitted['span'], {key: actual[key] for key in ('offset','end','total_bytes','sha256','source_sha256')})
        self.assertLess(actual['end'], span['end'])
        self.assertIn('fit_admitted', reasons)
        checked = [row for row in trace['routes'] if row['reason']=='checked_source_returned']
        self.assertEqual(checked[0]['sources'][0]['span']['sha256'], span['sha256'])
        self.assertEqual(checked[0]['sources'][0]['extent'], 'span')
        pulls = [row for row in trace['rpc'] if row['operation']=='pull']
        self.assertEqual(len(pulls), 2)
        self.assertEqual([row['outcome'] for row in pulls], ['error', 'returned'])
        self.assertEqual(pulls[1]['requested_span'], dict(offset=0, length=1536))
        self.assertNotIn('PRIVATE OPTIONAL PASSAGE', json.dumps(trace))
        self.assertNotIn(span_body, out['stdout'])

    def test_trace_caps_are_explicit_and_do_not_export_sentinels(self):
        case = self.fixture(True)
        bridge = fixtures.module('trace_caps', case.root/'inbox_recall.py')
        trace = bridge.InboxRouteTrace(case.root/'memory', {})
        for _ in range(40):
            trace.route('candidate_start', [dict(record_id='PRIVATE RAW VALUE', version=1, body='SECRET BODY')])
        trace.finish('', False)
        self.assertFalse(trace.data['complete'])
        self.assertEqual(trace.data['dropped'], 8)
        self.assertEqual(len(trace.data['routes']), 32)
        self.assertNotIn('PRIVATE', json.dumps(trace.data))
        self.assertNotIn('SECRET', json.dumps(trace.data))

    def test_trace_flush_runs_after_both_locks_and_old_engine_stays_unobserved(self):
        case = self.fixture(True)
        bridge = case.root / 'inbox_recall.py'
        bridge.write_text(bridge.read_text() + """
_saved_trace_flush = InboxRouteTrace.flush
def checked_flush(self, deadline):
    root = self.directory.parent.parent
    for directory in ('memory', 'coordination'):
        with (root/directory/'11111111-1111-4111-8111-111111111111.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return _saved_trace_flush(self, deadline)
InboxRouteTrace.flush = checked_flush
""")
        manifest = json.loads(case.binding.read_text())
        manifest['bridge']['sha256'] = hashlib.sha256(bridge.read_bytes()).hexdigest()
        case.binding.write_text(json.dumps(manifest))
        self.assertEqual(case.invoke(main=True)['code'], 0)
        self.assertEqual(len(self.traces(case)), 1)
        case = self.fixture(True)
        engine = case.root / 'memory.py'
        engine.write_text(engine.read_text().replace('INBOX_ROUTE_TRACE_VERSION = 1', 'INBOX_ROUTE_TRACE_VERSION = 0'))
        manifest = json.loads(case.binding.read_text())
        manifest['engine']['sha256'] = hashlib.sha256(engine.read_bytes()).hexdigest()
        case.binding.write_text(json.dumps(manifest))
        self.assertEqual(case.invoke(main=True)['code'], 0)
        self.assertEqual(self.traces(case), [])

    def test_expired_flush_and_disk_failure_preserve_output(self):
        case = self.fixture(True)
        bridge = fixtures.module('trace_expired', case.root/'inbox_recall.py')
        trace = bridge.InboxRouteTrace(case.root/'memory', {})
        trace.finish('', False)
        trace.flush(0)
        self.assertEqual(self.traces(case), [])
        path = case.root/'inbox_recall.py'
        path.write_text(path.read_text() + """
def refused_trace(self, deadline):
    raise OSError('PRIVATE disk failure')
InboxRouteTrace.flush = refused_trace
""")
        manifest = json.loads(case.binding.read_text())
        manifest['bridge']['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        case.binding.write_text(json.dumps(manifest))
        out = case.invoke(main=True)
        self.assertEqual(out['code'], 0)
        self.assertIn(case.body, out['stdout'])
        self.assertEqual(self.traces(case), [])
        self.assertNotIn('PRIVATE', json.dumps(out))

    def test_default_off_creates_no_trace(self):
        case = self.fixture()
        self.assertEqual(case.invoke(main=True)['code'], 0)
        self.assertEqual(self.traces(case), [])


if __name__ == '__main__':
    unittest.main()
