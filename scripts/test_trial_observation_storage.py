"""Streaming accounting parity and bounded correlation state."""
import hashlib
import json
import unittest
import weakref
from trial_native_observation import LineMeasurement

class Graph(dict):
    pass

class StorageTests(unittest.TestCase):
    def test_parsed_document_matches_original_envelope_charge_and_ends_borrow(self):
        observer = LineMeasurement(input_bytes=500, expects_hooks=False, model='fixture', prompt='task')
        events = [dict(type='system', subtype='init', model='fixture'),
                  dict(type='user', message=dict(content='task')),
                  dict(type='assistant', message=dict(content=[dict(type='tool_use', id='call', name='mcp__cairn__cairn_pull', input={})]))]
        result = dict(type='tool_result', tool_use_id='call', is_error=True, content='é'*2300)
        events += [dict(type='user', message=dict(content=[result]))]*2
        events += [dict(type='result', permission_denials=[])]
        for i, event in enumerate(events):
            graph = Graph(event)
            ref = weakref.ref(graph)
            raw = json.dumps(event, ensure_ascii=False) + '\n'
            observer.consume_document(graph, len(raw.encode()), {'received_seconds':31 if i==4 else 1})
            del graph
            self.assertIsNone(ref())
        report = observer.finish(())
        self.assertEqual(report['native_memory_result_bytes'], 2*len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode()))
        self.assertIn('selected_input_exceeded_9500_bytes', report['failures'])
        self.assertIn('last_memory_exceeded_30_seconds', report['failures'])

    def test_correlation_overflow_is_unknown_and_storage_stays_bounded(self):
        observer = LineMeasurement(input_bytes=500, expects_hooks=False, model='fixture', prompt='task')
        for i in range(400):
            observer.consume_document(dict(type='assistant', message=dict(content=[dict(
                type='tool_use', id=str(i), name='mcp__cairn__cairn_search', input={})])), 150,
                {'received_seconds': 1})
        report = observer.finish(())
        self.assertEqual(report['status'], 'unknown')
        self.assertIn('memory_correlation_limit', report['unknown'])
        self.assertLessEqual(report['memory_calls'], 256)
        self.assertEqual(report['native_searches'], 400)

if __name__ == '__main__': unittest.main()
