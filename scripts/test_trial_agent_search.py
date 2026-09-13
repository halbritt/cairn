import json
import unittest

from trial_agent_search import answer_checks, tool_observations


class NativeAgentSearchTest(unittest.TestCase):
    def test_answer_requires_types_and_source_contact(self):
        expected = dict(allowed=False, runs=1)
        correct = dict(allowed=False, runs=1, sources=['pulled-source'])
        self.assertTrue(all(answer_checks(correct, expected, ['pulled-source'], 'pulled-source').values()))
        for wrong in [None, dict(correct, allowed=0), dict(correct, runs=True),
                      dict(correct, sources='pulled-source'), dict(correct, sources=[{}]),
                      dict(correct, sources=['guessed-source'])]:
            self.assertFalse(all(answer_checks(wrong, expected, [], 'pulled-source').values()))

    def test_only_completed_native_tool_results_establish_contact(self):
        prefix = '/opt/cairn agent --socket /opt/api.sock --token-file /opt/agent.token '
        def event(call_id, command, data, status='completed'):
            return dict(type='tool_use', part=dict(tool='bash', callID=call_id, state=dict(status=status,
                        input=dict(command=command), output=json.dumps(dict(schema='cairn.response/1', ok=True, data=data)))))
        search = event('s', prefix+'search --task task --run run query',
                       dict(schema='cairn.agent-search/1', receipt_id='retrieval'))
        pull = event('p', prefix+'pull --request-id retry retrieval handle',
                     dict(selection=dict(record=dict(record_id='source', version=2))))
        text = dict(type='text', part=dict(text=json.dumps(search)))
        events = [event('s', '', {}, status='running'), text, search, search, pull,
                  event('bad', prefix+'pull retrieval handle', {}, status='error')]
        searches, pulls, failures = tool_observations('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(list(searches), ['retrieval'])
        self.assertEqual(list(pulls), [('retrieval', 'source', 2)])
        self.assertEqual(failures, [dict(call_id='bad', status='error')])


if __name__ == '__main__':
    unittest.main()
