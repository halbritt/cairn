"""Frozen synthetic SDK tuple, independent of mapping implementation constants."""
import hashlib
import json
import unittest
from trial_sdk_launch import LaunchMapper, MappingError

SESSION = '11111111-2222-4333-8444-555555555555'
DENIED = 'mcp__cairn__cairn_assess,mcp__cairn__cairn_assessments,mcp__cairn__cairn_client_info,mcp__cairn__cairn_edit,mcp__cairn__cairn_history,mcp__cairn__cairn_prepare_note,mcp__cairn__cairn_pull_evidence,mcp__cairn__cairn_remember'

def fixture():
    return {'command':'/unavailable/claude','cwd':'/work','env':{
        'HOME':'/tmp/home','CLAUDE_CONFIG_DIR':'/tmp/home/.claude','PATH':'/usr/bin:/bin',
        'LANG':'C.UTF-8','TMPDIR':'/tmp','CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1',
        'ANTHROPIC_BASE_URL':'http://127.0.0.1:18767','ANTHROPIC_API_KEY':'synthetic-no-account-no-value',
        'CLAUDE_CODE_ENTRYPOINT':'sdk-ts','CLAUDE_AGENT_SDK_VERSION':'0.3.278'},
        'args':['--output-format','stream-json','--verbose','--input-format','stream-json',
            '--max-turns','8','--tools','Bash,Edit,Glob,Grep,Read,Write','--setting-sources=',
            '--permission-mode','dontAsk','--session-id='+SESSION,'--await-initialize',
            '--model','sonnet','--effort','high','--replay-user-messages','--include-hook-events',
            '--permission-prompts','none','--disallowedTools',DENIED,'--no-session-persistence',
            '--strict-mcp-config','--mcp-config','/config/mcp.json','--settings','/config/settings.json']}

def digest(command,args,cwd,env):
    wire=json.dumps([command,args,cwd,sorted(env.items())],ensure_ascii=False,separators=(',',':')).encode()
    return len(wire),hashlib.sha256(wire).hexdigest()

class Credit:
    def __init__(self):self.live=0;self.peak=0;self.grants=[];self.returns=[];self.available=512
    def reserve(self,n):
        if n>self.available-self.live:return False
        self.live+=n;self.peak=max(self.peak,self.live);self.grants.append(n);return True
    def release(self,n):self.live-=n;self.returns.append(n)

def mapper():
    credit=Credit()
    return LaunchMapper(SESSION,credit.reserve,credit.release),credit

class MappingTests(unittest.TestCase):
    def test_selected_tuple_maps_only_executable_and_one_print_flag(self):
        raw=fixture();before=json.dumps(raw,sort_keys=True)
        owner,credit=mapper();plan=owner.consume(raw)
        self.assertEqual(plan.argv,('/native/claude','-p',*raw['args']))
        self.assertEqual(plan.cwd,raw['cwd']);self.assertEqual(dict(plan.env),raw['env'])
        self.assertTrue(plan.requires_initialize_instruction)
        for summary,command,args in [(plan.raw,raw['command'],raw['args']),
            (plan.with_print,raw['command'],['-p',*raw['args']]),
            (plan.native,'/native/claude',['-p',*raw['args']])]:
            self.assertEqual((summary.bytes,summary.sha256),digest(command,args,raw['cwd'],raw['env']))
        self.assertEqual((plan.raw.bytes,plan.with_print.bytes,plan.native.bytes),(1128,1133,1128))
        self.assertEqual(json.dumps(raw,sort_keys=True),before)
        self.assertEqual(credit.live,0);self.assertEqual(credit.grants,credit.returns);self.assertLessEqual(credit.peak,512)

    def test_refused_and_successful_attempts_cannot_be_reused(self):
        for bad in (False,True):
            with self.subTest(bad=bad):
                owner,credit=mapper();raw=fixture()
                if bad:
                    raw['args'].append('-p')
                    with self.assertRaisesRegex(MappingError,'^launch_arguments$'):owner.consume(raw)
                else:owner.consume(raw)
                with self.assertRaisesRegex(MappingError,'^launch_mapping_consumed$'):owner.consume(fixture())
                self.assertEqual(credit.live,0)

    def test_flag_forms_and_order_are_preserved_with_optional_argv_instruction(self):
        instruction='Synthetic offline diagnostic. No memory calls. Preserve the fixed six tools and eight-turn limit.'
        for append in (False,True):
            raw=fixture();args=raw['args']
            # Change one combined value to separated and one separated to combined.
            at=args.index('--setting-sources=');args[at:at+1]=['--setting-sources','']
            at=args.index('--model');args[at:at+2]=['--model=sonnet']
            args.insert(0,args.pop(args.index('--verbose')))
            if append:args+=['--append-system-prompt',instruction]
            owner,credit=mapper();plan=owner.consume(raw)
            self.assertEqual(plan.argv[2:],tuple(args));self.assertEqual(plan.requires_initialize_instruction,not append)
            self.assertEqual((plan.raw.bytes,plan.raw.sha256),digest(raw['command'],args,raw['cwd'],raw['env']))
            self.assertEqual(credit.live,0)

    def test_each_changed_or_extra_flag_refuses_before_hash_scratch(self):
        cases=[['-p'],['--print'],['--print=true'],['-p=value'],['--unknown'],['--verbose='],['--model','sonnet'],['--session-id='+SESSION],['--system-prompt','replace']]
        for extra in cases:
            with self.subTest(extra=extra):
                owner,credit=mapper();raw=fixture();raw['args']+=extra
                with self.assertRaisesRegex(MappingError,'^launch_arguments$'):owner.consume(raw)
                self.assertEqual(credit.grants,[])
        for key,bad in [('--model','other'),('--tools','Read'),('--settings','/elsewhere'),('--max-turns','9')]:
            with self.subTest(key=key):
                owner,credit=mapper();raw=fixture();raw['args'][raw['args'].index(key)+1]=bad
                with self.assertRaisesRegex(MappingError,'^launch_arguments$'):owner.consume(raw)
                self.assertEqual(credit.grants,[])

    def test_missing_boolean_or_value_flags_and_foreign_session_refuse(self):
        for token in ('--verbose','--await-initialize','--strict-mcp-config','--session-id='+SESSION):
            raw=fixture();raw['args'].remove(token);owner,credit=mapper()
            with self.assertRaisesRegex(MappingError,'^launch_arguments$'):owner.consume(raw)
            self.assertEqual(credit.grants,[])
        raw=fixture();raw['args'][raw['args'].index('--session-id='+SESSION)]='--session-id=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
        owner,_=mapper()
        with self.assertRaisesRegex(MappingError,'^launch_arguments$'):owner.consume(raw)

    def test_tuple_environment_and_text_bounds_refuse_before_scratch(self):
        changes=[('command','/native/claude'),('cwd',None),('args',['x']*64),('args',[1]),('args',['x'*4097])]
        for key,value in changes:
            raw=fixture();raw[key]=value;owner,credit=mapper()
            with self.assertRaises(MappingError):owner.consume(raw)
            self.assertEqual(credit.grants,[])
        for key,value in [('ANTHROPIC_BASE_URL','https://example.invalid'),('PATH','/other'),('ANTHROPIC_API_KEY','changed')]:
            raw=fixture();raw['env'][key]=value;owner,credit=mapper()
            with self.assertRaisesRegex(MappingError,'^launch_environment$'):owner.consume(raw)
            self.assertEqual(credit.grants,[])
        raw=fixture();raw['authenticated']=True;owner,credit=mapper()
        with self.assertRaisesRegex(MappingError,'^launch_tuple$'):owner.consume(raw)
        self.assertEqual(credit.grants,[])

    def test_plan_snapshots_mutable_input_containers(self):
        raw=fixture();owner,_=mapper();plan=owner.consume(raw)
        argv,env=plan.argv,plan.env
        raw['args'].clear();raw['env'].clear();raw['command']='changed'
        self.assertEqual(plan.argv,argv);self.assertEqual(len(plan.argv),32)
        self.assertEqual(plan.env,env);self.assertEqual(len(plan.env),10)

    def test_scratch_refusal_or_unknown_credit_never_returns_a_plan(self):
        for reply,code in [(False,'launch_scratch_credit'),(None,'launch_scratch_unknown'),(1,'launch_scratch_unknown')]:
            returns=[];owner=LaunchMapper(SESSION,lambda n:reply,returns.append)
            with self.assertRaisesRegex(MappingError,'^'+code+'$'):owner.consume(fixture())
            self.assertEqual(returns,[])
            with self.assertRaisesRegex(MappingError,'^launch_mapping_consumed$'):owner.consume(fixture())

    def test_release_uncertainty_is_retained_and_not_retried(self):
        calls=[]
        def release(n):calls.append(n);raise OSError('synthetic')
        owner=LaunchMapper(SESSION,lambda n:True,release)
        with self.assertRaisesRegex(MappingError,'^launch_scratch_release$'):owner.consume(fixture())
        self.assertTrue(owner.cleanup_unknown);self.assertEqual(len(calls),1)
        with self.assertRaisesRegex(MappingError,'^launch_mapping_consumed$'):owner.consume(fixture())
        self.assertEqual(len(calls),1)

    def test_session_and_credit_boundaries_are_required_before_consumption(self):
        for session in ('','AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE','11111111-2222-1333-8444-555555555555',None):
            with self.assertRaisesRegex(MappingError,'^launch_session$'):LaunchMapper(session,lambda n:True,lambda n:None)
        with self.assertRaisesRegex(MappingError,'^launch_credit_boundary$'):LaunchMapper(SESSION,None,None)

# Independent review cases exercise credit and tuple boundaries.

def groups():
 args=fixture()['args'];out=[];i=0
 while i<len(args):
  t=args[i]
  if '=' in t:out.append([t]);i+=1
  elif i+1<len(args) and not args[i+1].startswith('--'):out.append(args[i:i+2]);i+=2
  else:out.append([t]);i+=1
 return out
class IndependentMappingTests(unittest.TestCase):
 def test_each_mandatory_group_missing_and_duplicated(self):
  gs=groups()
  for i in range(len(gs)):
   for duplicate in (False,True):
    raw=fixture();raw['args']=[t for j,g in enumerate(gs) if duplicate or j!=i for t in g]
    if duplicate:raw['args']+=gs[i]
    m,c=mapper()
    with self.assertRaises(MappingError):m.consume(raw)
    self.assertEqual(c.grants,[])
    with self.assertRaisesRegex(MappingError,'launch_mapping_consumed'):m.consume(fixture())
 def test_each_rotation_mixed_forms_matches_independent_digest(self):
  gs=groups()
  for i in range(len(gs)):
   raw=fixture();out=[]
   for j,g in enumerate(gs[i:]+gs[:i]):
    if len(g)==2 and j%2:out.append(g[0]+'='+g[1])
    elif '=' in g[0] and j%2:out.extend(g[0].split('=',1))
    else:out.extend(g)
   raw['args']=out;m,c=mapper();p=m.consume(raw)
   self.assertEqual(p.argv,('/native/claude','-p',*out));self.assertEqual((p.raw.bytes,p.raw.sha256),digest(raw['command'],out,raw['cwd'],raw['env']));self.assertEqual(c.live,0)
 def test_refusal_at_every_reservation_keeps_credit_balanced(self):
  m,c=mapper();m.consume(fixture());total=len(c.grants)
  for failat in range(1,total+1):
   c=Credit();calls=0
   def reserve(n):
    nonlocal calls
    calls+=1
    return False if calls==failat else c.reserve(n)
   m=LaunchMapper(SESSION,reserve,c.release)
   with self.assertRaisesRegex(MappingError,'launch_scratch_credit'):m.consume(fixture())
   self.assertEqual(c.live,0);self.assertEqual(c.grants,c.returns)
   with self.assertRaisesRegex(MappingError,'launch_mapping_consumed'):m.consume(fixture())
 def test_input_mutation_during_credit_cannot_change_validated_plan(self):
  raw=fixture();original=fixture();c=Credit()
  def reserve(n):
   raw['args'][:]=['--bad'];raw['env'].clear();raw['command']='bad'
   return c.reserve(n)
  m=LaunchMapper(SESSION,reserve,c.release);p=m.consume(raw)
  self.assertEqual(p.argv,('/native/claude','-p',*original['args']));self.assertEqual(dict(p.env),original['env']);self.assertEqual(c.live,0)
 def test_credit_reentry_refuses_without_stealing_outer_attempt(self):
  c=Credit();calls=0;m=None
  def reserve(n):
   nonlocal calls
   calls+=1
   with self.assertRaisesRegex(MappingError,'launch_mapping_consumed'):m.consume(fixture())
   return c.reserve(n)
  m=LaunchMapper(SESSION,reserve,c.release);p=m.consume(fixture());self.assertTrue(calls>1);self.assertEqual(c.live,0);self.assertEqual(p.argv[0],'/native/claude')
 def test_same_length_changed_environment_and_wrong_plain_types(self):
  for key in fixture()['env']:
   raw=fixture();del raw['env'][key];raw['env']['unexpected']='x';m,c=mapper()
   with self.assertRaises(MappingError):m.consume(raw)
   self.assertEqual(c.grants,[])
  for key,value in [('args',tuple(fixture()['args'])),('env',list(fixture()['env'].items())),('command',b'/unavailable/claude'),('cwd',b'/work')]:
   raw=fixture();raw[key]=value;m,c=mapper()
   with self.assertRaises(MappingError):m.consume(raw)
   self.assertEqual(c.grants,[])
 def test_release_fault_positions_across_all_three_hashes(self):
  m,c=mapper();m.consume(fixture());total=len(c.grants)
  for failat in (1,total//3,total//3+1,2*total//3,total):
   c=Credit();calls=0
   def release(n):
    nonlocal calls
    calls+=1
    if calls==failat:raise OSError('synthetic')
    c.release(n)
   m=LaunchMapper(SESSION,c.reserve,release)
   with self.assertRaisesRegex(MappingError,'launch_scratch_release'):m.consume(fixture())
   self.assertTrue(m.cleanup_unknown);self.assertEqual(calls,failat)
   with self.assertRaisesRegex(MappingError,'launch_mapping_consumed'):m.consume(fixture())
   self.assertEqual(calls,failat)

if __name__=='__main__':unittest.main()
