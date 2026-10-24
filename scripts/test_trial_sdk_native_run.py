"""Real inert children across preparation, launch, socket service and one writer.

No vendor executable, API, account, original case or oracle is opened. This
checks source mechanics; successful fixture I/O is not native compatibility.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
import unittest
from unittest.mock import patch

import test_trial_sdk_bootstrap_review as bf
import test_trial_sdk_fixed_run as rf
from test_trial_sdk_shared_coordinator import AGGREGATE
from test_trial_sdk_engine_memory_rpc_parent import OP_QUOTE
from trial_native_bootstrap import canonical_sha
from trial_sdk_source_profile import prepare_native_review, PREPARATION_RAW, PREPARATION_WORKING
from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_session_resources import SDKSessionResources
from trial_sdk_stream_credit import StreamCredit
from trial_sdk_fixed_run import FixedRunOwner
import trial_sdk_fixed_bootstrap as fixed_bootstrap
from trial_sdk_fixed_source_pins import SOURCE_PINS

FIXED_SOURCE_REVISION = '43c4b6170f1f7784d70d55ca6669d770ab214ed1'
FIXED_MEMORY_BLOB = 'f1d5638f77b510298d90a4945479dfc2e256d8d9'

NATIVE = r'''#!/usr/bin/python3
import json, sys, os
MODE = 'complete'
session = sys.argv[sys.argv.index('--session-id')+1]
def send(value):
    print(json.dumps(value, separators=(',',':')), flush=True)
initialize = json.loads(sys.stdin.readline())
send({'type':'control_response','response':{'subtype':'success',
    'request_id':initialize['request_id'],'response':{}}})
def call(ordinal, message):
    ident = 'memory-' + str(ordinal)
    send({'type':'control_request','request_id':ident,'request':{
        'subtype':'mcp_message','server_name':'cairn','message':message}})
    while True:
        response=json.loads(sys.stdin.readline())
        if response.get('type')=='control_response' and response['response']['request_id']==ident:
            return response['response']['response']['mcp_response']
call(1, {'jsonrpc':'2.0','id':1,'method':'initialize','params':{
    'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'inert','version':'1'}}})
call(2, {'jsonrpc':'2.0','method':'notifications/initialized'})
listed=call(3, {'jsonrpc':'2.0','id':2,'method':'tools/list'})
assert {t['name'] for t in listed['result']['tools']}=={'cairn_search','cairn_pull'}
byname={t['name']:t for t in listed['result']['tools']}
assert 'Read mandatory context in selected' in byname['cairn_search']['description']
assert 'Read the complete note before replacing its body.' in byname['cairn_pull']['description']
assert 'inspection_policy' in byname['cairn_search']['inputSchema']['properties']
if MODE == 'invalid':
    call(4, {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{
        'name':'cairn_search','arguments':{'query':'q','entities':False}}})
    raise AssertionError('invalid request dispatched')
result=call(4, {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{
    'name':'cairn_search','arguments':{'query':'inert fixture','available_tokens':32000}}})
data=json.loads(result['result']['content'][0]['text'])
assert data['schema']=='cairn.mcp-search/1'
assert 'pull_command' not in data['index'][0]
args=data['index'][0]['pull_arguments']
whole=call(5, {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'cairn_pull','arguments':args}})
whole=json.loads(whole['result']['content'][0]['text'])
assert whole['selection']['record']['body']=='inert body'
assert whole['competing'][0]['record']['body']=='inert competing position'
span=dict(args,request_id='20000000-0000-4000-8000-000000000002',span={'offset':6,'length':4},opening_bytes=3)
part=call(6, {'jsonrpc':'2.0','id':5,'method':'tools/call','params':{'name':'cairn_pull','arguments':span}})
part=json.loads(part['result']['content'][0]['text'])
assert part['span']['body']=='body' and part['source_opening']['body']=='ine'
error=call(7, {'jsonrpc':'2.0','id':6,'method':'tools/call','params':{
    'name':'cairn_search','arguments':{'query':'error','available_tokens':32000}}})
assert error['result']['isError'] is True
for ordinal,event in enumerate(('SessionStart','UserPromptSubmit')):
    ident='hook-'+str(ordinal)
    registration=initialize['request']['hooks'][event][0]['hookCallbackIds'][0]
    send({'type':'control_request','request_id':ident,'request':{
        'subtype':'hook_callback','callback_id':registration,
        'input':{'session_id':session,'hook_event_name':event,'cwd':os.getcwd(),
                 'prompt':'authored inert task'}}})
    while True:
        response=json.loads(sys.stdin.readline())
        if response.get('type')=='control_response' and response['response']['request_id']==ident: break
send({'type':'result','session_id':session,'subtype':'success','is_error':False,'result':'inert ungraded terminal'})
'''
COMMAND = r'''#!/usr/bin/python3
import hashlib, json, sys, time
receipt='10000000-0000-4000-8000-000000000001'
record='30000000-0000-4000-8000-000000000001'
handle='40000000-0000-4000-8000-000000000001'
body='inert body'
digest=hashlib.sha256(body.encode()).hexdigest()
if 'search' in sys.argv:
    if sys.argv[-1]=='error':
        print('authored command refusal',file=sys.stderr); sys.exit(1)
    data={'schema':'cairn.agent-search/1','status':'READY','destination':{'name':'hosted'},'selected':[],
        'index':[{'record_id':record,'version':1,
        'body_sha256':digest,'pull_command':'LOCAL ROUTE MUST NOT LEAK',
        'pull_arguments':{'request_id':'20000000-0000-4000-8000-000000000001',
            'receipt_id':receipt,'handle':handle}}], 'receipt_id':receipt}
    if sys.argv[-1]!='inert fixture': data['index']=[]
else:
    assert 'pull' in sys.argv
    args=json.load(sys.stdin)
    # INTERRUPT_DELAY
    data={'selection':{'record':{'record_id':record,'version':1,'body':body}},
          'competing':[{'record':{'body':'inert competing position'}}]}
    if 'span' in args:
        data['selection']['record']['body']=''
        data['span']={'offset':6,'end':10,'total_bytes':10,'body':'body',
            'sha256':hashlib.sha256(b'body').hexdigest(),'source_sha256':digest}
        data['source_opening']={'offset':0,'end':3,'total_bytes':10,'body':'ine',
            'sha256':hashlib.sha256(b'ine').hexdigest()}
print(json.dumps({'schema':'cairn.response/1','ok':True,'status':'OK','data':data}))
'''


class NativeRunTests(unittest.TestCase):
    def setUp(self):
        bf.SDKReviewTests.setUp(self)
        NativeRunTests._stage_pinned_sources(self)

    def _stage_pinned_sources(self):
        """Give inert native fixtures private, read-only copies of fixed sources.

        Repository checkouts may intentionally be group-writable, and the
        current memory.py may have advanced beyond this frozen trial's pin.
        The runtime still refuses such live sources. Tests use the explicitly
        reviewed historical tree, verify every independent source pin, and
        copy into a private fixture before exercising the unchanged policy.
        """
        staged = self.root / 'fixed-pinned-source'
        staged.mkdir(mode=0o700)
        scripts = staged / 'scripts'
        scripts.mkdir(mode=0o700)
        integrations = staged / 'integrations'
        integrations.mkdir(mode=0o700)
        lifecycle = integrations / 'lifecycle'
        lifecycle.mkdir(mode=0o700)
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(scripts.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(integrations.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(lifecycle.stat().st_mode), 0o700)

        repo = Path(__file__).resolve().parent.parent
        original_modes = {}
        for name, expected in SOURCE_PINS.items():
            relative = ('integrations/lifecycle/memory.py' if name == 'memory.py'
                        else 'scripts/' + name)
            source = repo / relative
            target = lifecycle / name if name == 'memory.py' else scripts / name
            original_modes[str(source)] = stat.S_IMODE(source.stat().st_mode)
            raw = subprocess.check_output(
                ['git', 'show', f'{FIXED_SOURCE_REVISION}:{relative}'], cwd=repo)
            self.assertLessEqual(len(raw), 256 * 1024 if name == 'memory.py' else 65536)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), expected,
                             f'{relative} no longer matches the authored source pin')

            target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                | os.O_NOFOLLOW | os.O_CLOEXEC, 0o400)
            try:
                view = memoryview(raw)
                while view:
                    written = os.write(target_fd, view)
                    self.assertGreater(written, 0)
                    view = view[written:]
                os.fsync(target_fd)
                os.fchmod(target_fd, 0o400)
                sealed_identity = os.fstat(target_fd)
            finally:
                os.close(target_fd)
            target_fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                initial = os.fstat(target_fd)
                self.assertEqual((initial.st_dev, initial.st_ino, initial.st_mode,
                                  initial.st_uid, initial.st_gid, initial.st_nlink,
                                  initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns),
                                 (sealed_identity.st_dev, sealed_identity.st_ino,
                                  sealed_identity.st_mode, sealed_identity.st_uid,
                                  sealed_identity.st_gid, sealed_identity.st_nlink,
                                  sealed_identity.st_size, sealed_identity.st_mtime_ns,
                                  sealed_identity.st_ctime_ns))
                copied = bytearray()
                while len(copied) <= len(raw):
                    block = os.read(target_fd, min(8192, len(raw) + 1 - len(copied)))
                    if not block:
                        break
                    copied.extend(block)
                final = os.fstat(target_fd)
                self.assertEqual((final.st_dev, final.st_ino, final.st_mode,
                                  final.st_uid, final.st_gid, final.st_nlink,
                                  final.st_size, final.st_mtime_ns, final.st_ctime_ns),
                                 (initial.st_dev, initial.st_ino, initial.st_mode,
                                  initial.st_uid, initial.st_gid, initial.st_nlink,
                                  initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns))
                named = os.stat(target, follow_symlinks=False)
                self.assertEqual((named.st_dev, named.st_ino, named.st_mode),
                                 (initial.st_dev, initial.st_ino, initial.st_mode))
                self.assertEqual(hashlib.sha256(copied).hexdigest(), expected)
                self.assertEqual(len(copied), len(raw))
                self.assertEqual(stat.S_IMODE(initial.st_mode), 0o400)
            finally:
                os.close(target_fd)
            raw = None

        self._pinned_source_original_modes = original_modes
        self._pinned_source_fixture = staged
        self._fixed_bootstrap_path_patch = patch.object(
            fixed_bootstrap, '__file__', str(scripts / 'trial_sdk_fixed_bootstrap.py'))
        self._fixed_bootstrap_path_patch.start()
        self.addCleanup(self._fixed_bootstrap_path_patch.stop)

    def test_fixed_trial_source_epoch_is_historical_and_unchanged_by_fixture_setup(self):
        source = Path(__file__).resolve().parent.parent / 'integrations/lifecycle/memory.py'
        staged = self._pinned_source_fixture / 'integrations/lifecycle/memory.py'
        self.assertEqual(stat.S_IMODE(source.stat().st_mode),
                         self._pinned_source_original_modes[str(source)])
        self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(),
                         SOURCE_PINS['memory.py'])
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(staged.parent.stat().st_mode), 0o700)
        self.assertEqual(subprocess.check_output(
            ['git', 'rev-parse', f'{FIXED_SOURCE_REVISION}:integrations/lifecycle/memory.py'],
            cwd=Path(__file__).resolve().parent.parent).decode().strip(),
            FIXED_MEMORY_BLOB)

    config = rf.RunTests.config

    def change(self, role, body):
        path=Path(self.document['sources'][role]['path'])
        path.chmod(0o600); path.write_bytes(body)
        path.chmod(0o500 if role in ('cli','mcp_binary') else 0o400)
        self.document['sources'][role]['sha256']=hashlib.sha256(body).hexdigest()

    def prepare(self, mode='complete'):
        from trial_sdk_peer import _prctl
        previous = _prctl(3)
        self.assertEqual(_prctl(4, 0), 0)
        self.addCleanup(_prctl, 4, previous)
        self.document['hook_interface']={'profile':'sdk-hosted-memory/1'}
        self.change('cli', getattr(self,'native_source',NATIVE).replace("MODE = 'complete'", 'MODE = '+repr(mode)).encode())
        self.change('mcp_binary', getattr(self,'command_source',COMMAND).replace('# INTERRUPT_DELAY',
            'time.sleep(60)' if mode == 'interrupt' else 'pass').encode())
        self.change('mcp', json.dumps({'mcpServers':{'cairn':{'type':'sdk','name':'cairn'}}}).encode())
        self.document['binding']['source_set_sha256']=canonical_sha(
            {role:ref['sha256'] for role,ref in self.document['sources'].items()})
        bank=AttemptResources()
        preparation=prepare_native_review(self.document,self.output,bank)
        self.addCleanup(preparation.close)
        native=NativeProcessOwner(); domain=bank.bind(native)
        allocation=bank.allocate(domain,'operation',SDKSessionResources.MINIMUM_RAW,2**20)
        binding=SDKSessionResources(bank,native,domain,allocation,StreamCredit(bank,domain,1))
        owner=FixedRunOwner(bank)
        origin=time.monotonic_ns()
        config=self.config()
        (self.root/'service').mkdir(mode=0o700)
        config['standing_raw']=5184
        config['deadline_ns']=origin+30_000_000_000
        config['cwd']=str(self.root)
        config['env']={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'}
        config['policy']=dict(self.document['mcp_binding'],context={},executable=str(Path(self.output)/'cairn'))
        owner.prepare(preparation,binding,config,str(self.root/'fixed'),str(self.root/'callbacks'),
                      AGGREGATE,OP_QUOTE,stream_output=str(self.root/'streams'))
        self.addCleanup(self.cleanup,owner)
        owner.bind_native_launch(self.output,str(self.root),config['env'],origin)
        return owner

    def until(self, action, predicate, seconds=25):
        end=time.monotonic()+seconds
        while not predicate():
            self.assertLess(time.monotonic(),end,'inert boundary stalled')
            action()
            time.sleep(.001)

    def cleanup(self, owner):
        self.until(owner.cleanup_step, lambda:owner.snapshot()['state'] in ('closed','closed_unknown'))

    def test_source_ready_then_once_only_launch_dispatch_reply_and_cleanup(self):
        owner=self.prepare()
        self.assertIsNone(owner._session.native_owner.snapshot()['pid'])
        self.assertIsNotNone(owner._selected_input)
        owner.require_complete()  # No circular prior native-observation requirement.
        self.assertEqual(owner.resources.snapshot()['raw_reserved'],PREPARATION_RAW+2566+5184+1024)
        self.assertEqual(owner.resources.snapshot()['working_reserved'],PREPARATION_WORKING+2*2**20+96*2**20)
        owner.start_service()
        self.until(owner.step,lambda:owner.snapshot()['state']=='session')
        owner.start_native()
        self.until(owner.step,lambda:owner.snapshot()['state']=='terminal_waited')
        self.assertEqual(owner._coordinator.gate.snapshot()['native_searches_debited'],2)
        self.assertEqual(owner._coordinator.gate.snapshot()['expansions_debited'],2)
        self.assertEqual(owner._evidence._terminal_graph['result'],'inert ungraded terminal')
        measured=owner._selected_input.evidence()
        self.assertEqual(measured['measurement']['status'],'unknown')
        self.assertIn('rendered_memory_dispatch_uncorrelated',measured['measurement']['unknown'])
        self.assertEqual(measured['stdout_sha256'],owner._evidence.snapshot()['sha256']['stdout'])
        self.assertGreater(measured['validated_controls'],0)
        self.assertGreater(owner.resources.snapshot()['traffic_spent'],0)
        self.assertLessEqual(owner.resources.snapshot()['raw_peak'],131072)
        self.assertFalse(owner.resources.snapshot()['unknown'])
        with self.assertRaisesRegex(ValueError,'run_native_launch_consumed'):
            owner.start_native()
        self.cleanup(owner)
        self.assertTrue(all(o.snapshot()['release']['handles_detached'] for o in owner._original_owners()))

    def test_invalid_native_request_never_starts_a_command(self):
        owner=self.prepare('invalid');owner.start_service()
        self.until(owner.step,lambda:owner._state=='session');owner.start_native()
        with self.assertRaisesRegex(ValueError,'native_request_shape'):
            self.until(owner.step,lambda:False)
        self.assertEqual(owner._coordinator.gate.snapshot()['native_searches_debited'],0)
        self.assertFalse(owner._session._custody.control._memory.rows['memory-4']['reply_queued'])
        self.assertTrue(owner.resources.snapshot()['unknown'])
        self.cleanup(owner)
        self.assertEqual(owner._state,'closed_unknown')

    def test_native_launch_failure_is_consumed_before_any_process_effect(self):
        owner=self.prepare()
        with self.assertRaisesRegex(ValueError,'run_service_not_ready'):
            owner.start_native()
        self.assertFalse(owner._session.native_owner.snapshot()['launch_attempted'])
        with self.assertRaisesRegex(ValueError,'run_native_launch_consumed'):
            owner.start_native()
        self.assertTrue(owner.resources.snapshot()['unknown'])

    def test_cancel_inflight_native_command_waits_then_quarantines_unobserved_eof(self):
        import os
        import select
        owner=self.prepare('interrupt');owner.start_service()
        self.until(owner.step,lambda:owner._state=='session');owner.start_native()
        self.until(owner.step,lambda:owner._memory.operation is not None
            and owner._memory.event.callback.request_id=='memory-5'
            and owner._memory.operation.snapshot()['phase']=='running')
        service_pid=owner._link.owner.snapshot()['pid']
        children=Path('/proc')/str(service_pid)/'task'/str(service_pid)/'children'
        child_pids=children.read_text().split()
        self.assertEqual(len(child_pids),1)
        command_pid=int(child_pids[0])
        pidfd=os.pidfd_open(command_pid,0);self.addCleanup(os.close,pidfd)
        self.assertFalse(select.select([pidfd],[],[],0)[0])
        before=owner.resources.snapshot()['traffic_spent']
        owner.cancel()
        self.until(owner.cleanup_step,lambda:owner._memory.operation._unknown)
        # RELEASED(unknown) is only emitted after the service's actual child
        # wait/cleanup. Independently observe the exact child pidfd terminated
        # and its proc entry reaped while that service parent is still alive.
        self.assertIsNone(owner._link.owner.observe_wait())
        self.assertEqual(select.select([pidfd],[],[],0)[0],[pidfd])
        self.assertNotIn(str(command_pid),children.read_text().split())
        self.assertFalse((Path('/proc')/str(command_pid)).exists())
        self.cleanup(owner)
        # Abandonment does not invent stdout/stderr EOF observations. The
        # existing command owner reports unknown RELEASE and retains its quote.
        self.assertEqual(owner._state,'closed_unknown')
        self.assertGreater(owner.resources.snapshot()['traffic_spent'],before)
        self.assertTrue(owner._memory.operation._unknown)
        self.assertEqual(owner._link.transcript.snapshot()['phase'],'release_unknown')
        self.assertIsNotNone(owner._memory.operation._child)
        self.assertFalse(owner._session._custody.control._memory.rows['memory-5']['reply_queued'])
        self.assertEqual(owner._coordinator.gate.snapshot()['expansions_debited'],1)
        self.assertTrue(all(o.snapshot()['release']['handles_detached'] for o in owner._original_owners()))


if __name__=='__main__':unittest.main()
