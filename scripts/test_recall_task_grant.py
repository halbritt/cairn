"""Launcher task grants, using the public hook and local fixture CLI only."""
import unittest
import test_claude_inbox_recall as fixture

TASK='12345678-1234-4234-8234-123456789abc'

class RecallTaskGrantTests(unittest.TestCase):
    freeze_base=fixture.ClaudeInboxRecallTests.freeze_base
    freeze=fixture.ClaudeInboxRecallTests.freeze
    calls=fixture.ClaudeInboxRecallTests.calls
    memory_main=fixture.ClaudeInboxRecallTests.memory_main
    ledger=fixture.ClaudeInboxRecallTests.ledger
    def setUp(self):
        fixture.ClaudeInboxRecallTests.setUp(self)
        self.mc['recall_task_key']=TASK
        self.freeze()
    def ordinary(self,prompt_id='first'):
        return dict(self.event,prompt='Inspect the fixture implementation.',prompt_id=prompt_id)
    def test_distinct_native_prompts_in_one_launcher_task_do_not_renew_optional_grant(self):
        first=self.memory_main(self.ordinary())
        self.assertEqual(first['code'],0,first)
        self.assertIn('candidate_bodies',first['stdout'])
        before=self.ledger();calls=len(self.calls())
        for prompt_id in ('second','second','third','first'):
            result=self.memory_main(self.ordinary(prompt_id))
            self.assertEqual(result['code'],0,result)
            self.assertNotIn('candidate_bodies',result['stdout'])
        self.assertEqual([x['op'] for x in self.calls()[calls:]],['search']*4)
        after=self.ledger();self.assertEqual(len(after['grants']),1)
        self.assertEqual(after['grants'][before['active']]['native_allowance_bytes'],before['grants'][before['active']]['native_allowance_bytes'])

    def test_new_launcher_task_and_distinct_session_get_independent_grants(self):
        self.assertEqual(self.memory_main(self.ordinary())['code'],0)
        self.mc['recall_task_key']='22345678-1234-4234-8234-123456789abc';self.freeze()
        second=self.memory_main(self.ordinary('second'))
        self.assertIn('candidate_bodies',second['stdout'])
        self.assertEqual(len(self.ledger()['grants']),2)
        self.mc['recall_task_key']=TASK;self.freeze()
        self.assertNotIn('candidate_bodies',self.memory_main(self.ordinary('third'))['stdout'])
        other=self.memory_main(dict(self.ordinary(),session_id='32345678-1234-4234-8234-123456789abc'))
        self.assertIn('candidate_bodies',other['stdout'])

    def test_startup_resume_and_required_failure_do_not_refund_task_allowance(self):
        required='Required current guard. 日本語.'
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body=required))];self.freeze()
        startup=self.memory_main(dict(self.ordinary(),hook_event_name='SessionStart',source='startup',prompt=''))
        self.assertNotIn('candidate_bodies',startup['stdout'])
        initial=self.memory_main(self.ordinary())
        before=self.ledger();grant=before['grants'][before['active']]
        self.assertEqual(grant['output_bytes'],len(startup['stdout'].encode())+len(initial['stdout'].encode()))
        refresh_bytes=0
        for source in ('compact','resume'):
            refreshed=self.memory_main(dict(self.ordinary(),hook_event_name='SessionStart',source=source,prompt=''))
            refresh_bytes += len(refreshed['stdout'].encode())
            self.assertIn(required,refreshed['stdout'])
            self.assertNotIn('candidate_bodies',refreshed['stdout'])
        self.fixture['fail_search']='API_UNAVAILABLE';self.freeze()
        self.assertEqual(self.memory_main(self.ordinary('second'))['code'],2)
        self.fixture.pop('fail_search');self.freeze()
        final=self.memory_main(self.ordinary('third'))
        self.assertIn(required,final['stdout']);self.assertNotIn('candidate_bodies',final['stdout'])
        after=self.ledger()['grants'][before['active']]
        self.assertEqual(after['native_allowance_bytes'],grant['native_allowance_bytes'])
        self.assertEqual(after['output_bytes'],grant['output_bytes'])
        self.assertEqual(len(self.ledger()['grants']),1)
        self.assertEqual(after['required_refresh_bytes'],refresh_bytes+len(final['stdout'].encode()))

    def test_invalid_key_preserves_known_required_refusal_without_new_grant(self):
        self.fixture['search']['selected']=[dict(mandatory=True,record=dict(body='Required guard.'))]
        self.freeze()
        self.assertEqual(self.memory_main(self.ordinary())['code'],0)
        before=self.ledger();calls=len(self.calls())
        self.mc['recall_task_key']='malformed';self.freeze()
        refused=self.memory_main(self.ordinary('second'))
        self.assertEqual(refused['code'],2,refused)
        after=self.ledger()
        self.assertEqual(len(self.calls()),calls)
        self.assertEqual(set(after['grants']),set(before['grants']))
        self.assertEqual(after['active'],before['active'])
        self.assertTrue(after['grants'][after['active']]['required']['pending'])
        self.mc['recall_task_key']=TASK;self.freeze()
        recovered=self.memory_main(self.ordinary('third'))
        self.assertEqual(recovered['code'],0,recovered)
        self.assertIn('Required guard.',recovered['stdout'])
        self.assertNotIn('candidate_bodies',recovered['stdout'])

    def test_explicit_invalid_key_fails_before_search_and_absence_keeps_prompt_default(self):
        for value in (None,False,{},'not-a-uuid'):
            self.mc['recall_task_key']=value;self.freeze();before=len(self.calls())
            result=self.memory_main(self.ordinary())
            self.assertNotEqual(result['code'],0)
            self.assertEqual(len(self.calls()),before)
        self.mc.pop('recall_task_key');self.freeze()
        for prompt_id in ('one','two'):
            self.assertIn('candidate_bodies',self.memory_main(self.ordinary(prompt_id))['stdout'])
        self.assertEqual(len(self.ledger()['grants']),2)
