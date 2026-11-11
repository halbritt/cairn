"""Actual child-process failure protocol, durable-before-failure and cleanup."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

from test_f04_activation import ROOT, SESSION, TURN, Fixture, encoded, module, oracle, sha, write


CHILD = r'''package main
import("bytes";"encoding/json";"fmt";"io";"os";"path/filepath";"strings";"syscall";"time";"github.com/halbritt/cairn/internal/lifecycle/activationipc";"golang.org/x/sys/unix")
// The installed producer deliberately uses argv[0]=cairn. Tests select a
// separate pinned fault image with -X instead of changing that launch ABI.
var testMode string
func main(){
 unix.Prctl(unix.PR_SET_PDEATHSIG,uintptr(syscall.SIGKILL),0,0,0)
 mode:=testMode;if mode=="" {mode=filepath.Base(os.Args[0])}
 frame:=func(class string,n string){fmt.Fprintf(os.Stderr,"{\"error\":{\"class\":%q,\"reason\":\"activation\",\"errno\":%s}}\n",class,n)}
 switch mode {
 case "panic": panic("fixture child output must not escape")
 case "signal": syscall.Kill(os.Getpid(),syscall.SIGTERM); time.Sleep(time.Second)
 case "empty": os.Exit(1)
 case "stdout-failure": fmt.Print("do-not-echo");os.Exit(1)
 case "success-stderr": fmt.Print("{\"enabled\":true}\n");fmt.Fprint(os.Stderr,"do-not-echo")
 case "extra-success": fmt.Print("{\"enabled\":true}\n{}\n")
 case "false-exit": fmt.Print("{\"enabled\":true}\n");os.Exit(7)
 case "oversized": fmt.Print(strings.Repeat("x",8192))
 case "invalid-utf8": os.Stderr.Write([]byte{255});os.Exit(1)
 case "unknown-class": frame("UnknownError","null");os.Exit(1)
 case "unknown-reason": fmt.Fprint(os.Stderr,"{\"error\":{\"class\":\"ValueError\",\"reason\":\"unknown\",\"errno\":null}}\n");os.Exit(1)
 case "boolean-errno": frame("OSError","true");os.Exit(1)
 case "extra-error": frame("ValueError","null");fmt.Fprint(os.Stderr,"\n");os.Exit(1)
 case "missing": frame("FileNotFoundError","2");os.Exit(1)
 case "permission": frame("PermissionError","13");os.Exit(1)
 case "blocking": frame("BlockingIOError","11");os.Exit(1)
 case "timeout-frame": frame("TimeoutError","110");os.Exit(1)
 case "sleep":
  fmt.Fprint(os.Stderr,"");time.Sleep(30*time.Second)
 case "deadline-cue":
  raw,e:=io.ReadAll(os.Stdin);if e!=nil {panic(e)}
  var out,err bytes.Buffer
  code:=activationipc.Run(os.Args[1:],bytes.NewReader(raw),&out,&err)
  var event map[string]interface{};json.Unmarshal(raw,&event)
  if code==0 && event["hook_event_name"]=="PostToolUse" {
   config:="";deadline:=""
   for i,arg:=range os.Args {if arg=="--config" {config=os.Args[i+1]};if arg=="--deadline" {deadline=os.Args[i+1]}}
   var settings map[string]interface{};body,e:=os.ReadFile(config);if e!=nil {panic(e)}
   if e=json.Unmarshal(body,&settings);e!=nil {panic(e)}
   path:=filepath.Join(filepath.Dir(settings["state_dir"].(string)),"go-deadline")
   if e=os.WriteFile(path,[]byte(deadline),0600);e!=nil {panic(e)}
  }
  os.Stdout.Write(out.Bytes());os.Stderr.Write(err.Bytes());os.Exit(code)
 case "post-persist", "changed-target":
  var out,err bytes.Buffer
  code:=activationipc.Run(os.Args[1:],os.Stdin,&out,&err)
  if code!=0 {os.Stdout.Write(out.Bytes());os.Stderr.Write(err.Bytes());os.Exit(code)}
  if mode=="changed-target" {
   config:="";role:=""
   for i,arg:=range os.Args {if arg=="--config" {config=os.Args[i+1]};if arg=="--entrypoint" {role=os.Args[i+1]}}
   var c,mapBinding map[string]interface{}
   raw,_:=os.ReadFile(config);json.Unmarshal(raw,&c)
   raw,_=os.ReadFile(c["inbox_recall_binding"].(string));json.Unmarshal(raw,&mapBinding)
   target:=mapBinding["memory_config"].(map[string]interface{})["path"].(string)
   if role=="memory" {target=mapBinding["coordination_configs"].([]interface{})[0].(map[string]interface{})["path"].(string)}
   if e:=os.Chmod(target,0600);e!=nil {panic(e)}
   file,e:=os.OpenFile(target,os.O_APPEND|os.O_WRONLY,0);if e!=nil {panic(e)};file.Write([]byte(" "));file.Close()
   os.Stdout.Write(out.Bytes());return
  }
  os.Exit(2)
 default: os.Exit(3)
 }
}'''


DEADLINE_PROBE = b'''
# Test-only observer in this fixture's explicitly repinned source closure.
_fixture_original_tool_cue = tool_cue
def tool_cue(config, event):
    if event.get('hook_event_name') != 'PostToolUse':
        return _fixture_original_tool_cue(config, event)
    context = _BUNDLE.compatibility_context
    root = Path(config['state_dir']).parent
    go_deadline = float((root / 'go-deadline').read_text())
    audit = dict(go_deadline=go_deadline, retained_deadline=context.deadline, timeouts=[])
    namespace = context.remaining.__func__.__globals__
    original_time, original_run = namespace['time'], subprocess.run
    class Clock:
        def monotonic(self): return context.deadline - 0.125
        def __getattr__(self, name): return getattr(original_time, name)
    def observed(command, *args, **kwargs):
        if 'session-inbox-pending' in command:
            audit['timeouts'].append(kwargs['timeout'])
        return original_run(command, *args, **kwargs)
    # The real Go activation has already run with the original absolute
    # deadline. Only this retained phase sees a fixed eighth-second remainder;
    # subprocess and the public parent's actual timer keep their real clocks.
    namespace['time'], subprocess.run = Clock(), observed
    try:
        return _fixture_original_tool_cue(config, event)
    finally:
        namespace['time'], subprocess.run = original_time, original_run
        (root / 'deadline-probe.json').write_text(json.dumps(audit))
'''


def install_deadline_probe(f):
    if hasattr(f, 'sources'):
        path = f.sources / 'coordination.py'
        path.write_bytes(path.read_bytes() + DEADLINE_PROBE)
        f.publish_configuration()  # Exercise the actual immutable R-02 producer.
        return
    sources = {name: (Path(f.receipt['snapshot_root']) / name).read_bytes()
               for name in f.receipt['members']}
    sources['coordination.py'] += DEADLINE_PROBE
    sources['coordination-hook.py'] = sources['coordination.py']
    # This is the same explicit snapshot/pin operation as the other F-04
    # test-only source observers; no production source is changed.
    hashes = {name: sha(raw) for name, raw in sources.items()}
    identity = sha(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode())
    snapshot = f.root / identity
    snapshot.mkdir(mode=0o700)
    for name, raw in sources.items(): write(snapshot / name, raw)
    f.receipt.update(snapshot_root=str(snapshot), source_revision='TEST same-deadline observer',
                     members={name: dict(sha256=hashes[name], bytes=len(raw)) for name, raw in sources.items()})
    write(f.receipt_path, encoded(f.receipt))
    f.receipt_sha = sha(f.receipt_path.read_bytes())


class F04FailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory()
        directory=Path(cls.build.name)
        for name in ('go.mod','go.sum'): shutil.copyfile(ROOT/name,directory/name)
        for name in ('internal','core','localapi'): shutil.copytree(ROOT/name,directory/name)
        package=directory/'cmd'/'f04-failure'
        package.mkdir(parents=True)
        (package/'main.go').write_text(CHILD)
        cls.binary=directory/'failure'
        subprocess.run(['go','build','-buildvcs=false','-o',str(cls.binary),'./cmd/f04-failure'],cwd=directory,check=True)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)

    def fixture(self,name,mode,**kwargs):
        return Fixture(self.root/name,self.binary,binary_name=mode,**kwargs)

    def test_protocol_and_process_failures_never_escape_as_host_status_two_or_false(self):
        modes=('panic','signal','empty','stdout-failure','success-stderr','extra-success','false-exit','oversized',
               'invalid-utf8','unknown-class','unknown-reason','boolean-errno','extra-error')
        for role in ('memory','coordination'):
            for wake in (False,True):
                for mode in modes:
                    with self.subTest(role=role,wake=wake,mode=mode):
                        f=self.fixture(role+str(wake)+mode,mode)
                        if not wake: f.raw=encoded(dict(f.event,prompt='ordinary fixture prompt'))
                        result=f.public(role)
                        expected=(b'Cairn lifecycle diagnostic: phase=activation error_class=UnexpectedError\n' if role=='memory'
                                  else b'Cairn coordination: SubprocessError; session presence unavailable\n')
                        self.assertEqual((result.returncode,result.stdout,result.stderr),(1,b'',expected))
                        self.assertFalse(Path(f.memory['state_dir']).exists())
                        self.assertEqual(f.api_calls(),[])

    def test_unavailable_child_wait_status_refuses_before_spawn_instead_of_assuming_zero(self):
        for role in ('memory','coordination'):
            f=self.fixture('sigchld-'+role,'false-exit')
            result=subprocess.run([str(f.launcher(role))],input=f.raw,capture_output=True,cwd=f.root,env=f.env(),
                                  preexec_fn=lambda:signal.signal(signal.SIGCHLD,signal.SIG_IGN),timeout=18)
            self.assertEqual((result.returncode,result.stdout),(1,b''))
            self.assertIn(b'UnexpectedError' if role=='memory' else b'SubprocessError',result.stderr)
            self.assertFalse(Path(f.memory['state_dir']).exists())
            self.assertEqual(f.api_calls(),[])

    def test_valid_os_error_frames_keep_source_category_and_wake_policy(self):
        for role in ('memory','coordination'):
            for wake in (False,True):
                for mode,label in (('missing','FileNotFoundError'),('permission','PermissionError'),('blocking','BlockingIOError'),('timeout-frame','TimeoutError')):
                    with self.subTest(role=role,wake=wake,mode=mode):
                        f=self.fixture(role+str(wake)+mode,mode)
                        if not wake: f.raw=encoded(dict(f.event,prompt='ordinary fixture prompt'))
                        result=f.public(role)
                        self.assertEqual(result.stdout,b'')
                        if role=='memory':
                            category='BlockingIOError' if label=='BlockingIOError' else 'OSError'
                            expected=(f'Cairn lifecycle diagnostic: phase=activation error_class={category}\n'
                                      'Cairn lifecycle: invalid lifecycle input or unavailable local file; use native tools or an explicit handoff.\n').encode()
                            self.assertEqual((result.returncode,result.stderr),(1,expected))
                        elif wake:
                            category='OtherError' if label=='TimeoutError' else label
                            expected=(f'Cairn coordination diagnostic: phase=activation error_class={category}\n'
                                      'Cairn coordination: NATIVE_PROMPT_REFUSED: inbox activation decision unavailable; channel text rejected, owner prompt continues\n').encode()
                            self.assertEqual((result.returncode,result.stderr),(2,expected))
                        else:
                            expected=f'Cairn coordination: {label}; session presence unavailable\n'.encode()
                            self.assertEqual((result.returncode,result.stderr),(1,expected))
                        self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_failure_after_real_activation_persistence_does_not_fallback(self):
        for role in ('memory','coordination'):
            with self.subTest(role=role):
                f=self.fixture(role,'post-persist')
                result=f.public(role)
                self.assertEqual((result.returncode,result.stdout),(1,b''))
                self.assertIn(b'UnexpectedError' if role=='memory' else b'SubprocessError',result.stderr)
                path=Path(f.memory['state_dir'])/(SESSION+'.memory-budget.json')
                self.assertEqual(json.loads(path.read_text())['inbox_activation'],{TURN:True})
                self.assertEqual(f.api_calls(),[])

    def test_change_before_retained_continuation_refuses_and_keeps_real_decision(self):
        for role in ('memory','coordination'):
            f=self.fixture('changed-'+role,'changed-target')
            result=f.public(role)
            self.assertEqual((result.returncode,result.stdout),(1 if role=='memory' else 2,b''))
            self.assertIn(b'phase=activation error_class=ValueError',result.stderr)
            path=Path(f.memory['state_dir'])/(SESSION+'.memory-budget.json')
            self.assertEqual(json.loads(path.read_text())['inbox_activation'],{TURN:True})
            self.assertEqual(f.api_calls(),[])

    def seed_required(self,f):
        directory=Path(f.memory['state_dir']);directory.mkdir(mode=0o700)
        required=dict(count=1,selection_sha256='a'*64,pending=True)
        if f.memory['harness']=='claude':
            state=dict(schema='cairn.inbox-recall-grants/1',session=SESSION,grants={},required=required)
            path=directory/(SESSION+'.inbox-recall.json')
            oracle.save_claude_ledger(path,state)
        else:
            path=directory/(SESSION+'.memory-budget.json')
            state=oracle.codex_budget_ledger(path,SESSION,9500)
            state['pending']['required']=required
            oracle.save_codex_ledger(path,state)
        return path

    def test_required_restoration_os_policy_and_transport_failure_are_separate(self):
        for harness in ('codex','claude'):
            for event in ('UserPromptSubmit','SessionStart'):
                for mode in ('permission','panic'):
                    with self.subTest(harness=harness,event=event,mode=mode):
                        f=self.fixture(harness+event+mode,mode,harness=harness)
                        f.raw=encoded(dict(f.event,hook_event_name=event,prompt='ordinary required restoration'))
                        path=self.seed_required(f)
                        result=f.public('memory')
                        if mode=='panic':
                            self.assertEqual((result.returncode,result.stdout,result.stderr),(1,b'',
                                b'Cairn lifecycle diagnostic: phase=activation error_class=UnexpectedError\n'))
                        elif event=='UserPromptSubmit':
                            self.assertEqual((result.returncode,result.stdout),(2,b''))
                            self.assertEqual(result.stderr,b'Cairn lifecycle diagnostic: phase=activation error_class=OSError\nCairn: current required context could not be restored.\n')
                        elif harness=='codex':
                            self.assertEqual((result.returncode,result.stdout),(0,b'{"continue":false,"stopReason":"Bound inbox recall cannot restore required context."}\n'))
                            self.assertEqual(result.stderr,b'Cairn lifecycle diagnostic: phase=activation error_class=OSError\n')
                        else:
                            self.assertEqual((result.returncode,result.stdout),(1,b''))
                            self.assertIn(b'phase=activation error_class=OSError',result.stderr)
                            self.assertIn(b'current required Claude context could not be restored',result.stderr)
                        state=json.loads(path.read_text())
                        self.assertNotIn('inbox_activation',state)
                        self.assertEqual(f.api_calls(),[])

    def test_post_dispatch_recheck_preserves_a_proven_required_result(self):
        for event in ('UserPromptSubmit','SessionStart'):
            with self.subTest(event=event):
                f=self.fixture('required-recheck-'+event,'changed-target')
                f.raw=encoded(dict(f.event,hook_event_name=event,prompt='ordinary required restoration'))
                path=self.seed_required(f)
                result=f.public('memory')
                diagnostic=b'Cairn lifecycle diagnostic: phase=activation error_class=ValueError\n'
                expected=(2,b'',diagnostic+b'Cairn: current required context could not be restored.\n') if event=='UserPromptSubmit' else (0,b'{"continue":false,"stopReason":"Bound inbox recall cannot restore required context."}\n',diagnostic)
                self.assertEqual((result.returncode,result.stdout,result.stderr),expected)
                ledger=json.loads(path.read_text())
                self.assertTrue(ledger['pending']['required']['pending'])
                self.assertFalse(ledger['pending']['granted'])

    def test_claude_activation_refusal_record_success_and_failure_keep_native_veto(self):
        coord=module('f04_refusal_source',ROOT/'integrations/lifecycle/coordination.py')
        for bad_record in (False,True):
            f=self.fixture('record'+str(bad_record),'permission',harness='claude')
            f.event['host_pid']=os.getpid()
            process=coord.process_reference(os.getpid())
            session=dict(agent_id='33333333-3333-4333-8333-333333333333',execution_id='44444444-4444-4444-8444-444444444444')
            wake=dict(transport='claude-channel',status='submitted',native_id=SESSION,session=session,parent=process,
                      delivery_id='55555555-5555-4555-8555-555555555555')
            state=dict(agent=dict(session,native_session_id=SESSION),process=process,idle_wake=wake)
            path=coord.state_path(f.coord,SESSION);path.parent.mkdir(mode=0o700)
            write(path,encoded(state))
            if bad_record: write(path.parent/'wake-refusals',b'occupied path')
            f.event['prompt']=(f'<channel source="cairn-events" native_session_id="{SESSION}" '
                f'agent_id="{session["agent_id"]}" execution_id="{session["execution_id"]}" delivery_id="{wake["delivery_id"]}">\n'
                +coord.wake_message(wake)+'\n</channel>')
            f.raw=encoded(f.event)
            result=f.public('coordination')
            self.assertEqual((result.returncode,result.stdout),(2,b''))
            self.assertIn(b'phase=activation error_class=PermissionError',result.stderr)
            self.assertIn(b'NATIVE_PROMPT_REFUSED',result.stderr)
            if bad_record:
                self.assertIn(b'phase=refusal_record error_class=OtherError',result.stderr)
                self.assertNotIn('wake_rejection',json.loads(path.read_text()))
            else:
                self.assertNotIn(b'phase=refusal_record',result.stderr)
                receipts=list((path.parent/'wake-refusals').glob('*.json'))
                self.assertEqual(len(receipts),1)
                receipt=json.loads(receipts[0].read_text())
                self.assertEqual((receipt['phase'],receipt['error_class']),('activation','PermissionError'))
                self.assertEqual(json.loads(path.read_text())['wake_rejection'],receipts[0].stem)
            self.assertEqual(f.api_calls(),[])
            self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_actual_parent_timer_uses_the_whole_trusted_hook_budget(self):
        for role,budget in (('memory',13),('coordination',15)):
            with self.subTest(role=role):
                f=self.fixture(role,'sleep')
                started=time.monotonic()
                result=f.public(role)
                elapsed=time.monotonic()-started
                print('ACTUAL_HOOK_DEADLINE',role,budget,elapsed,result.returncode,repr(result.stderr),flush=True)
                self.assertGreaterEqual(elapsed,budget-0.05)
                self.assertLess(elapsed,budget+2)
                self.assertEqual(result.returncode,1 if role=='memory' else 2)
                self.assertEqual(result.stdout,b'')
                self.assertIn(b'OSError' if role=='memory' else b'OtherError',result.stderr)
                self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_retained_cue_uses_the_remaining_same_deadline_after_real_go_activation(self):
        f=self.fixture('remaining-cue','deadline-cue')
        install_deadline_probe(f)
        f.event.update(hook_event_name='UserPromptSubmit',prompt='ordinary active owner task',host_pid=os.getpid())
        f.raw=encoded(f.event)
        self.assertEqual(f.public('coordination').returncode,0)
        f.replies({'session-inbox-pending':[dict(ok=True,status='OK',data=dict(requests=0,notices=1,responses=0),delay=2)]})
        f.raw=encoded(dict(f.event,hook_event_name='PostToolUse'))
        result=f.public('coordination',timeout=2)
        self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{}\n',b''))
        audit=json.loads((f.root/'deadline-probe.json').read_text())
        self.assertEqual(audit['go_deadline'],audit['retained_deadline'],
                         'retained phase restarted the enclosing hook deadline')
        self.assertEqual(audit['timeouts'],[0.125],
                         'pending lookup did not use the remaining enclosing budget')

    def test_parent_signal_is_preserved_and_the_owned_child_is_reaped(self):
        for role in ('memory','coordination'):
            for number in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):
                with self.subTest(role=role,signal=number):
                    f=self.fixture(role+str(number),'sleep')
                    parent=subprocess.Popen([str(f.launcher(role))],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                            cwd=f.root,env=f.env())
                    try:
                        parent.stdin.write(f.raw);parent.stdin.close();parent.stdin=None
                        # Observe this exact parent's child through procfs; no elapsed
                        # sleep is used as evidence that activation has started.
                        deadline=time.monotonic()+5
                        child=None
                        while time.monotonic()<deadline:
                            children=Path(f'/proc/{parent.pid}/task/{parent.pid}/children').read_text().split()
                            if children:
                                child=int(children[0]);break
                            time.sleep(0.005)
                        self.assertIsNotNone(child,'activation did not spawn its owned child')
                        parent.send_signal(number)
                        out,err=parent.communicate(timeout=5)
                        self.assertEqual(parent.returncode,-number,(out,err))
                        self.assertFalse(Path(f'/proc/{child}').exists(),'parent did not reap its child')
                    finally:
                        if parent.poll() is None: parent.kill()
                        parent.communicate()


if __name__=='__main__': unittest.main()
