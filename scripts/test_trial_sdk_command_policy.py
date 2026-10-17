"""Pure command planning, with actual engine-generated calls and no dispatch."""
import dataclasses
import json
import unittest
import uuid

from test_trial_sdk_engine_adapter import engine_module
from trial_sdk_command_policy import FixedCommandPolicy, CommandPolicyError

A='00000000-0000-4000-8000-000000000001'
B='00000000-0000-4000-8000-000000000002'
C='00000000-0000-4000-8000-000000000003'


class PolicyTests(unittest.TestCase):
    def policy(self, context=()):
        return FixedCommandPolicy('/fixed/cairn','/fixed/api.sock','/fixed/token',
                                  '/repo','task','run',context)

    def args(self, *middle, query='query'):
        return ('--repo','/repo','--task','task','--run','run','--tokens','16000',
                *middle,'--',query)

    def pull(self, **extra):
        return dict(request_id=A,receipt_id=B,handle=C,**extra)

    def test_engine_generated_search_and_json_pulls_use_fixed_route(self):
        engine=engine_module()
        context=dict(revision='a'*40,workspace_sha256='a'*64,task_class='coding',
                     task_phase='review',binding='bound',capability='read')
        config=dict(cairn='/fixed/cairn',socket='/fixed/api.sock',token_file='/fixed/token',
                    repo='/repo',task_id='task',run_id='run',harness='claude',context=context)
        policy=self.policy(tuple(context.items()))
        memory=engine.Memory(config,'session')
        plans=[]
        def capture(command,*,body=None,**unused):
            self.assertEqual(tuple(command[:6]),('/fixed/cairn','agent','--socket',
                             '/fixed/api.sock','--token-file','/fixed/token'))
            plans.append(policy.plan(command[6],tuple(command[7:]),body))
            return {'ok':True,'data':{'status':'READY','destination':{'name':'hosted'},'receipt_id':A,'index':[]}}
        engine.run_json=capture
        memory.search('--socket remains query text',16000,entities=['scripts/main.py'],
                      kinds=['decision'],semantic=True,offset=0)
        self.assertEqual(plans[-1].argv[-2:],('--','--socket remains query text'))
        self.assertIn('--semantic',plans[-1].argv)
        self.assertIn('--revision',plans[-1].argv)
        for request_id in (A,str(uuid.uuid5(uuid.NAMESPACE_URL,'fixed-call'))):
            memory.call('pull',payload=dict(request_id=request_id,receipt_id=B,handle=C,
                             span={'offset':0,'length':65536},opening_bytes=768))
            self.assertEqual(json.loads(plans[-1].body)['request_id'],request_id)
        self.assertEqual(plans[-1].argv,('/fixed/cairn','agent','--socket',
                         '/fixed/api.sock','--token-file','/fixed/token','pull'))

    def test_actual_engine_omits_empty_context_strings_and_policy_matches(self):
        engine=engine_module()
        context=dict(revision='',workspace_sha256='',task_class='coding',
                     task_phase='',binding='',capability='')
        config=dict(cairn='/fixed/cairn',socket='/fixed/api.sock',token_file='/fixed/token',
                    repo='/repo',task_id='task',run_id='run',harness='claude',context=context)
        policy=self.policy(tuple(context.items()))
        self.assertEqual(policy.context,(('task_class','coding'),))
        captured=[]
        def capture(command,*,body=None,**unused):
            plan=policy.plan(command[6],tuple(command[7:]),body)
            self.assertEqual(plan.argv,tuple(command))
            captured.append(plan)
            return {'ok':True,'data':{'status':'READY','destination':{'name':'hosted'},
                                     'receipt_id':A,'index':[]}}
        engine.run_json=capture
        engine.Memory(config,'session').search('query',16000)
        self.assertEqual(len(captured),1)
        self.assertNotIn('--revision',captured[0].argv)
        for context in ((('revision',''),('revision','a'*40)),(('unknown',''),),
                        (('revision',False),),(('revision',0),),(('revision',None),),
                        (('revision',[]),),(('revision','invalid'),)):
            with self.subTest(context=context),self.assertRaises(CommandPolicyError):
                self.policy(context)

    def test_search_rebuilds_and_preserves_empty_entity_query(self):
        plan=self.policy().plan('search',self.args('--entity-file','a.py','--offset','10000',
                                                   '--kind','instruction',query=''),None)
        self.assertIsNone(plan.body)
        self.assertEqual(plan.argv[-2:],('--',''))
        with self.assertRaises(dataclasses.FrozenInstanceError):plan.body='{}'
        with self.assertRaises(dataclasses.FrozenInstanceError):self.policy().repo='/other'
        self.assertFalse(hasattr(plan,'execute'))

    def test_route_scope_config_and_file_attacks_refuse(self):
        base=self.args()
        attacks=[base[:-2]+('--semantic-query-file','/private','--','query'),
                 base[:-2]+('--socket','/other','--','query'),
                 base[:-2]+('--repo','/other','--','query'),
                 base[:-2]+('--request-id',A,'--','query'),
                 base[:-2]+('--browse','--','query'),
                 base[:-2]+('--revision','unbound','--','query'),
                 tuple('/other' if x=='/repo' else x for x in base),
                 tuple('other' if x=='task' else x for x in base),
                 base+('extra',),base[:-2],base[:-2]+('--','a\0b'),
                 ('--repo=/repo',)+base[2:]]
        for args in attacks:
            with self.subTest(args=args),self.assertRaises(CommandPolicyError):
                self.policy().plan('search',args,None)
        for op in ('create','expand','selector','recall-observation'):
            with self.assertRaises(CommandPolicyError):self.policy().plan(op,(),None)
        with self.assertRaises(CommandPolicyError):self.policy().plan('search',base,'{}')

    def test_context_is_exact_snapshot_not_mutable_config(self):
        context=(('revision','a'*40),('task_phase','review'))
        policy=self.policy(context)
        good=self.args('--revision','a'*40,'--task-phase','review')
        self.assertIn('a'*40,policy.plan('search',good,None).argv)
        for bad in (self.args(),self.args('--revision','other','--task-phase','review'),
                    good[:-2]+('--revision','a'*40,'--','query')):
            with self.assertRaises(CommandPolicyError):policy.plan('search',bad,None)

    def test_source_search_limits_and_canonical_entities(self):
        for tokens in (256,1000000):
            args=tuple(str(tokens) if x=='16000' else x for x in self.args(query='é'*2048))
            self.policy().plan('search',args,None)
        for middle in (('--offset','10001'),('--offset','-1'),('--entity-file','../private'),
                       ('--entity-file','a//b'),('--entity-file','/file'),
                       ('--entity-file','a\\b'),('--entity-file',' a'),
                       ('--kind','unknown'),('--semantic','--semantic')):
            with self.subTest(middle=middle),self.assertRaises(CommandPolicyError):
                self.policy().plan('search',self.args(*middle),None)
        for tokens in ('255','1000001','True','01','1e4'):
            with self.subTest(tokens=tokens),self.assertRaises(CommandPolicyError):
                self.policy().plan('search',tuple(tokens if x=='16000' else x for x in self.args()),None)
        with self.assertRaises(CommandPolicyError):
            self.policy().plan('search',self.args(query='é'*2049),None)
        for n,flag in ((17,'--entity-file'),(9,'--kind')):
            middle=tuple(x for i in range(n) for x in (flag,f'f{i}' if flag=='--entity-file' else 'note'))
            with self.assertRaises(CommandPolicyError):self.policy().plan('search',self.args(*middle),None)

    def test_duplicate_entity_hints_rebuild_without_exceeding_owner_argv_bound(self):
        middle=('--entity-file','a.py')*100
        plan=self.policy().plan('search',self.args(*middle),None)
        self.assertEqual(plan.argv.count('--entity-file'),1)
        self.assertLessEqual(len(plan.argv),128)

    def test_pull_evidence_and_history_public_json_dialects(self):
        evidence=dict(self.pull(),evidence_id=A,expected_sha256='ab'*32,
                      span={'offset':1048575,'length':1048576})
        self.assertEqual(json.loads(self.policy().plan('pull-evidence',(),json.dumps(evidence)).body),evidence)
        cases=[dict(record_id=A),dict(record_id=A,limit=0,before_version=0,version=0),
               dict(record_id=A,limit=100,before_version=2147483647),
               dict(record_id=A,version=2147483647,limit=0,span={'offset':65535,'length':65536})]
        for body in cases:
            result=json.loads(self.policy().plan('history',(),json.dumps(body)).body)
            self.assertEqual(result,dict(body,repo='/repo'))
        self.policy().plan('pull',(),json.dumps(self.pull(opening_bytes=0)))
        self.policy().plan('pull',(),json.dumps(self.pull(span=None)))

    def test_json_ambiguity_shape_scope_and_selector_attacks_refuse(self):
        cases=[('pull','{"request_id":"'+A+'","request_id":"'+A+'","receipt_id":"'+B+'","handle":"'+C+'"}'),
               ('pull',json.dumps(self.pull(repo='/other'))),
               ('pull',json.dumps(self.pull(scope={'repo':'/other'}))),
               ('pull',json.dumps(self.pull(opening_bytes=1))),
               ('pull',json.dumps(self.pull(span={'offset':True,'length':1}))),
               ('pull',json.dumps(self.pull(span={'offset':65536,'length':1}))),
               ('pull',json.dumps(self.pull(span={'offset':0,'length':0}))),
               ('pull',json.dumps(self.pull(span={'offset':0,'length':1,'extra':0}))),
               ('pull','{"request_id":NaN}'),('pull','{} {}'),('pull','[]'),
               ('pull-evidence',json.dumps(dict(self.pull(),evidence_id=A,expected_sha256='x'*64))),
               ('history',json.dumps(dict(record_id=A,repo='/other'))),
               ('history',json.dumps(dict(record_id=A,version=1,limit=1))),
               ('history',json.dumps(dict(record_id=A,span={'offset':0,'length':1}))),
               ('history',json.dumps(dict(record_id=A,version=True))),
               ('history',json.dumps(dict(record_id=A,limit=101))),
               ('history',json.dumps(dict(record_id=A,before_version=2147483648)))]
        for op,body in cases:
            with self.subTest(op=op,body=body),self.assertRaises(CommandPolicyError):
                self.policy().plan(op,(),body)
        for op in ('pull','pull-evidence','history'):
            with self.assertRaises(CommandPolicyError):self.policy().plan(op,(A,B),'{}')
        for value in ('00000000-0000-0000-0000-000000000000',A.upper().replace('000000000001','ABCDEFABCDEF'),A.replace('-',''),True):
            body=self.pull();body['request_id']=value
            with self.assertRaises(CommandPolicyError):self.policy().plan('pull',(),json.dumps(body))

    def test_config_context_uses_public_pin_bounds_and_preserves_digest_case(self):
        for key,value in (('revision','rev'),('revision','A'*40),
                          ('workspace_sha256','not-a-digest'),('task_class','*'),
                          ('binding','é'*129)):
            with self.subTest(key=key,value=value),self.assertRaises(CommandPolicyError):
                self.policy(((key,value),))
        policy=self.policy((('revision','a'*64),('workspace_sha256','AB'*32),
                            ('task_class','é'*128)))
        args=self.args('--revision','a'*64,'--workspace-sha256','AB'*32,
                       '--task-class','é'*128)
        self.assertIn('AB'*32,policy.plan('search',args,None).argv)
        body=dict(self.pull(),evidence_id=A,expected_sha256='AB'*32)
        self.assertEqual(json.loads(self.policy().plan('pull-evidence',(),json.dumps(body)).body)
                         ['expected_sha256'],'AB'*32)

    def test_plan_never_claims_receipt_membership_or_read_authorization(self):
        plan=self.policy().plan('pull',(),json.dumps(self.pull()))
        self.assertEqual(plan.operation,'pull')
        self.assertFalse(hasattr(plan,'authorized'))
        self.assertFalse(hasattr(plan,'permit'))
        self.assertEqual(json.loads(plan.body),self.pull())
        with self.assertRaises(CommandPolicyError):
            self.policy().plan('pull',(),'{'+(' '*(128*1024))+'}')
        with self.assertRaises(CommandPolicyError):self.policy().plan('pull',[],json.dumps(self.pull()))


if __name__=='__main__':unittest.main()
