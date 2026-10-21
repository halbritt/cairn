"""Native controller -> closed command protocol -> child policy parity."""
import json
import unittest

from test_trial_sdk_control import controller, user_ready
from trial_sdk_command_policy import FixedCommandPolicy, CommandPolicyError
from trial_sdk_native_request import command_request, SEARCH_FIELDS
from trial_sdk_incoming import canonical_size_sha256

POLICY = FixedCommandPolicy('/fixed/cairn','/fixed/socket','/fixed/token','repo','task','run',())


def boundary(arguments):
    control = controller(); control.enable_native_memory(); user_ready(control)
    frame = dict(type='control_request',request_id='native-1',request=dict(
        subtype='mcp_message',server_name='cairn',message=dict(jsonrpc='2.0',id=1,
        method='tools/call',params=dict(name='cairn_search',arguments=arguments))))
    event = control.receive(frame)
    request = command_request('mcp__cairn__cairn_search',event.message['params']['arguments'],POLICY)
    return POLICY.plan(request.operation,request.args,request.body)


class NativeRequestTests(unittest.TestCase):
    def test_rejects_wrong_types_before_falsey_normalization(self):
        for key in ('entities','kinds','request_id','context','available_tokens','offset',
                    'memory_budget_bytes','min_pull_bytes','query','semantic_query'):
            for value in (False, {}, []):
                if value == [] and type(value) is list and key in ('entities','kinds'): continue
                if type(value) is dict and not value and key == 'context': continue
                with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                    boundary(dict(query='q',**{key:value}) if key!='query' else {'query':value})
        for key in ('entities','kinds'):
            with self.assertRaises(ValueError):boundary({'query':'q',key:''})

    def test_supported_nulls_and_large_original_entity_request(self):
        plan = boundary(dict(query='q',entities=None,kinds=None,context=None,
                             available_tokens=None,offset=None,memory_budget_bytes=None))
        self.assertIn('--tokens',plan.argv)
        args={'query':'q'*4096,'entities':[{'kind':'file','name':str(i)+'/'+'a'*250+'/'+'b'*250} for i in range(16)]}
        self.assertEqual(canonical_size_sha256(args)[0],12591)
        plan=boundary(args)
        self.assertEqual(plan.argv[-1],args['query'])
        self.assertEqual(plan.argv.count('--entity-file'),16)

    def test_all_advertised_search_fields_have_closed_semantics(self):
        args=dict(query='q',available_tokens=32000,memory_budget_bytes=8000,min_pull_bytes=1000,
            semantic=True,semantic_query='semantic text',browse=False,offset=3,advisory_conflicts=True,
            entities=[{'kind':'symbol','name':'pkg.Symbol'}],kinds=['lesson'],
            request_id='10000000-0000-4000-8000-000000000001',inspection_policy='',
            error_signature_sha256='a'*64,context={'task_phase':'review'})
        self.assertEqual(set(args),SEARCH_FIELDS)
        plan=boundary(args)
        self.assertEqual(plan.body,'semantic text')
        for flag in ('--semantic','--semantic-query-stdin','--offset','--advisory-conflicts',
                     '--entity-symbol','--kind','--request-id','--memory-budget-bytes',
                     '--min-pull-bytes','--error-signature-sha256','--task-phase'):
            self.assertIn(flag,plan.argv)
        plan=boundary({'browse':True,'memory_budget_bytes':8000,'inspection_policy':'first-fitting-whole/2'})
        self.assertIn('--browse',plan.argv);self.assertIn('--inspection-policy',plan.argv)
        with self.assertRaises(CommandPolicyError):boundary({'query':'q','unknown':True})


if __name__=='__main__':unittest.main()
