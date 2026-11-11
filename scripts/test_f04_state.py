"""Cross-language lock, actual syscall interruption, and immutable decision state."""
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_f04_activation import ROOT, SESSION, TURN, Fixture, encoded, oracle, scalar_tree, write, overwrite_fixture


PYTHON_WRITER = r'''
import importlib.util,json,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('source_ledger',Path(sys.argv[1])/'integrations/lifecycle/grant_ledger.py')
source=importlib.util.module_from_spec(spec);spec.loader.exec_module(source)
memory=json.loads(sys.argv[2]);harness,session,turn=sys.argv[3:]
original=os.fsync
paused=False
def fsync(fd):
 global paused
 if not paused:
  paused=True
  print('locked',flush=True)
  if sys.stdin.buffer.read(1)!=b'x': raise RuntimeError('missing writer release')
 return original(fd)
os.fsync=fsync
print(json.dumps(source.activation_decision(memory,harness,session,turn,True)),flush=True)
'''


class F04StateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory()
        cls.binary=Path(cls.build.name)/'activation'
        subprocess.run(['go','build','-buildvcs=false','-o',str(cls.binary),'./cmd/cairn-lifecycle'],cwd=ROOT,check=True)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root=Path(self.directory.name)

    def fixture(self,name,**kwargs): return Fixture(self.root/name,self.binary,**kwargs)

    def ledger(self,f):
        suffix='.inbox-recall.json' if f.memory['harness']=='claude' else '.memory-budget.json'
        return Path(f.memory['state_dir'])/(SESSION+suffix)

    def test_python_writer_and_both_go_roles_share_the_existing_lock_inode(self):
        for harness in ('codex','claude'):
            for mode in (0o644,0o664,'symlink'):
                with self.subTest(harness=harness,mode=mode):
                    f=self.fixture(harness+str(mode),harness=harness)
                    directory=Path(f.memory['state_dir']);directory.mkdir(mode=0o700)
                    lock=directory/(SESSION+'.lock')
                    target=lock if mode!='symlink' else directory/'retained-lock'
                    write(target,b'',0o644 if mode=='symlink' else mode)
                    if mode=='symlink': lock.symlink_to(target.name)
                    identity=(target.stat().st_dev,target.stat().st_ino,target.stat().st_mode)
                    turn='claude-channel:fixture-prompt' if harness=='claude' else TURN
                    child=subprocess.Popen([sys.executable,'-I','-S','-c',PYTHON_WRITER,str(ROOT),json.dumps(f.memory),
                                            harness,SESSION,turn],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
                    try:
                        self.assertTrue(select.select([child.stdout],[],[],5)[0])
                        self.assertEqual(child.stdout.readline(),b'locked\n')
                        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                            waiting=[pool.submit(f.activation,role) for role in ('memory','coordination')]
                            # Python owns the exact source lock during the first
                            # fsync; neither real Go caller may publish early.
                            time.sleep(0.12)
                            self.assertTrue(all(not job.done() for job in waiting))
                            child.stdin.write(b'x');child.stdin.flush()
                            for job in waiting:
                                result=job.result(timeout=10)
                                self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
                        out,err=child.communicate(timeout=5)
                        self.assertEqual((child.returncode,out,err),(0,b'true\n',b''))
                        self.assertEqual((target.stat().st_dev,target.stat().st_ino,target.stat().st_mode),identity)
                        self.assertEqual(json.loads(self.ledger(f).read_text())['inbox_activation'],{turn:True})
                    finally:
                        if child.poll() is None: child.kill()
                        child.communicate()

    def test_persistent_python_contention_gets_the_full_source_wait_without_state_effect(self):
        f=self.fixture('contended')
        directory=Path(f.memory['state_dir']);directory.mkdir(mode=0o700)
        lock=directory/(SESSION+'.lock')
        with lock.open('a') as held:
            fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
            started=time.monotonic()
            result=f.activation()
            elapsed=time.monotonic()-started
            self.assertGreaterEqual(elapsed,1.5)
            self.assertLess(elapsed,3)
            self.assertEqual(result.returncode,1)
            self.assertEqual(json.loads(result.stderr)['error'],dict(**{'class':'BlockingIOError'},reason='activation',errno=11))
            self.assertEqual(list(directory.iterdir()),[lock])

    def test_real_marker_first_process_death_refuses_an_initialized_missing_ledger(self):
        f=self.fixture('interrupted')
        # The third real fsync is the ledger temporary file. Kill at that
        # syscall after marker rename and directory fsync, before ledger rename.
        trace=f.root/'syscalls'
        command=['/usr/bin/strace','-qq','-f','-o',str(trace),'-e','trace=fsync',
                 '-e','inject=fsync:signal=SIGKILL:when=3',*f.activation_argv()]
        result=subprocess.run(command,input=f.raw,capture_output=True,cwd=f.root,env=f.env(),timeout=15)
        self.assertNotEqual(result.returncode,0,(result.stdout,result.stderr))
        path=self.ledger(f)
        self.assertTrue(path.with_suffix('.initialized').exists(),trace.read_text())
        self.assertFalse(path.exists(),trace.read_text())
        before={p.name:p.read_bytes() for p in path.parent.iterdir() if p.is_file()}
        result=f.activation('coordination')
        self.assertEqual((result.returncode,result.stdout),(1,b''))
        self.assertEqual(json.loads(result.stderr)['error']['class'],'ValueError')
        self.assertEqual({p.name:p.read_bytes() for p in path.parent.iterdir() if p.is_file()},before)

    @patch.dict(os.environ, GOMAXPROCS='1', GODEBUG='asyncpreemptoff=1')
    def test_real_write_sync_and_rename_failures_preserve_the_publication_boundary(self):
        # strace counts injections per syscall and TID. Keep ordinal writes on
        # publication rather than Go's scheduler eventfd. All errno/trace/state
        # assertions below remain; other concurrency tests use normal scheduling.
        for operation,number,published_marker,published_state in (
                ('write',1,False,False),('write','1+',False,False),('fsync',1,False,False),('fsync',2,True,False),
                ('fsync',3,True,False),('fsync',4,True,True),('renameat',1,False,False),('renameat',2,True,False)):
            with self.subTest(operation=operation,number=number):
                f=self.fixture(operation+str(number))
                trace=f.root/'fault-syscalls'
                result=subprocess.run(['/usr/bin/strace','-qq','-f','-yy','-o',str(trace),'-e','trace='+operation,
                    '-e',f'inject={operation}:error=EIO:when={number}',*f.activation_argv()],input=f.raw,
                    capture_output=True,cwd=f.root,env=f.env(),timeout=15)
                self.assertEqual((result.returncode,result.stdout),(1,b''),trace.read_text())
                if result.stderr:
                    self.assertEqual(json.loads(result.stderr)['error'],{'class':'OSError','reason':'activation','errno':5})
                else:
                    # strace's counter is per Linux task. A migrated Go
                    # goroutine can hit another first-write injection while
                    # reporting the original file error. Only an observed
                    # injected stderr failure permits a missing frame here;
                    # the public parent treats it as transport failure.
                    text=trace.read_text()
                    self.assertEqual(operation,'write',text)
                    self.assertRegex(text,r'write\(2(?:<[^>]*>)?,[^\n]*= -1 EIO[^\n]*INJECTED')
                    self.assertRegex(text,r'write\(\d+<'+re.escape(str(self.ledger(f).parent))+r'/[^\n]*= -1 EIO[^\n]*INJECTED')
                path=self.ledger(f)
                self.assertEqual(path.with_suffix('.initialized').exists(),published_marker,trace.read_text())
                self.assertEqual(path.exists(),published_state,trace.read_text())
                result=f.activation('coordination')
                if published_marker and not published_state:
                    self.assertEqual((result.returncode,result.stdout),(1,b''),'a failed publication may not reset initialized accounting')
                else:
                    self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
                    self.assertEqual(json.loads(path.read_text())['inbox_activation'],{TURN:True})

    def test_go_writer_holds_the_python_lock_and_death_keeps_complete_prior_state(self):
        f=self.fixture('go-holder')
        path=self.ledger(f);path.parent.mkdir(mode=0o700)
        # A writer-produced large opaque value gives the process interruption
        # fixture time to observe marker publication; no production test seam.
        state=dict(schema='cairn.codex-memory-grants/1',session_id=SESSION,active_turn_id=None,turns={},
                   pending=dict(limit_bytes=9500,emitted_hook_bytes=0,reserved_native_bytes=0,granted=False),
                   opaque='x'*(8*1024*1024))
        oracle.save_codex_budget(path,state)
        before=path.read_bytes()
        import ctypes
        libc=ctypes.CDLL(None,use_errno=True)
        watch=libc.inotify_init1(os.O_CLOEXEC|os.O_NONBLOCK)
        self.assertGreaterEqual(watch,0)
        self.addCleanup(os.close,watch)
        self.assertGreaterEqual(libc.inotify_add_watch(watch,os.fsencode(path.parent),0x80),0)
        child=subprocess.Popen(f.activation_argv(),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                               cwd=f.root,env=f.env())
        try:
            child.stdin.write(f.raw);child.stdin.close();child.stdin=None
            deadline=time.monotonic()+10
            observed=False
            while time.monotonic()<deadline and not observed:
                self.assertTrue(select.select([watch],[],[],max(0,deadline-time.monotonic()))[0])
                raw=os.read(watch,65536)
                observed=os.fsencode(path.with_suffix('.initialized').name)+b'\0' in raw
            self.assertTrue(observed)
            child.send_signal(signal.SIGSTOP)
            os.waitpid(child.pid,os.WUNTRACED)
            started=time.monotonic()
            with self.assertRaises(BlockingIOError):
                oracle.activation_decision(f.memory,'codex',SESSION,TURN,False)
            self.assertGreaterEqual(time.monotonic()-started,1.49)
            child.kill();child.communicate(timeout=5)
            self.assertEqual(path.read_bytes(),before,'interrupted writer published a partial or unexpected state')
            # The released kernel lock permits a new writer without resetting
            # any accounting or the existing opaque value.
            result=f.activation('coordination')
            self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
            after=json.loads(path.read_text());after.pop('inbox_activation')
            self.assertEqual(scalar_tree(after),scalar_tree(state))
        finally:
            if child.poll() is None: child.kill()
            child.communicate()

    def test_activation_does_not_compact_64_65_or_4096_folded_boundaries(self):
        for count in (64,65):
            f=self.fixture('retention'+str(count),harness='claude')
            path=self.ledger(f);path.parent.mkdir(mode=0o700)
            grants={format(i,'064x'):dict(identity={'native_owner':'owner'+str(i)},limit_bytes=9500,
                output_bytes=i,native_allowance_bytes=0,opaque=[i,-0.0]) for i in range(count)}
            state=dict(schema='cairn.inbox-recall-grants/1',session=SESSION,grants=grants,
                       inbox_activation={'prior'+str(i):bool(i%2) for i in range(count)},
                       folded=dict(count=100,tokens=[format(i,'016x') for i in range(4096)]))
            write(path,encoded(state))
            source=self.root/('source'+str(count));source.mkdir(mode=0o700)
            write(source/path.name,encoded(state))
            oracle.activation_decision(dict(f.memory,state_dir=str(source)),'claude',SESSION,'claude-channel:fixture-prompt',True)
            result=f.activation()
            self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
            actual=json.loads(path.read_text())
            self.assertEqual(scalar_tree(actual),scalar_tree(json.loads((source/path.name).read_text())))
            self.assertEqual(len(actual['grants']),count)
            self.assertEqual(len(actual['inbox_activation']),count+1)
            self.assertEqual(len(actual['folded']['tokens']),4096)

    def test_disabled_generation_does_not_override_cached_true_or_clear_accounting(self):
        for harness in ('codex','claude'):
            a=self.fixture(harness+'A',harness=harness)
            self.assertEqual(a.activation().returncode,0)
            original=self.ledger(a).read_bytes()
            b=self.fixture(harness+'B',harness=harness,enabled=False)
            b.memory['state_dir']=a.memory['state_dir'];b.publish_configuration()
            for role in ('memory','coordination'):
                result=b.activation(role)
                self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":true}\n',b''))
                self.assertEqual(self.ledger(a).read_bytes(),original)
            # A is still independently selected, and B gets its own L. No
            # publication or cache refresh silently changes A's selected bytes.
            self.assertNotEqual(a.launcher('memory'),b.launcher('memory'))
            self.assertEqual(a.activation().stdout,b'{"enabled":true}\n')
            overwrite_fixture(b.coord_path,b.coord_path.read_bytes()+b' ')
            result=b.activation('memory')
            self.assertEqual((result.returncode,result.stdout),(1,b''),'cached true still requires all final pins')
            self.assertEqual(self.ledger(a).read_bytes(),original)

    def test_cached_false_retains_the_disabled_final_pin_exception(self):
        for harness in ('codex','claude'):
            a=self.fixture(harness+'false',harness=harness,enabled=False)
            self.assertEqual(a.activation().stdout,b'{"enabled":false}\n')
            original=self.ledger(a).read_bytes()
            b=self.fixture(harness+'enabled',harness=harness)
            b.memory['state_dir']=a.memory['state_dir'];b.publish_configuration()
            overwrite_fixture(b.coord_path,b.coord_path.read_bytes()+b' ')
            result=b.activation('memory')
            self.assertEqual((result.returncode,result.stdout,result.stderr),(0,b'{"enabled":false}\n',b''))
            self.assertEqual(self.ledger(a).read_bytes(),original)

    def test_corrupt_schema_session_and_decisions_refuse_without_rewriting_initialized_state(self):
        for harness in ('codex','claude'):
            for defect in ('json','schema','session','decision','directory'):
                with self.subTest(harness=harness,defect=defect):
                    f=self.fixture(harness+defect,harness=harness)
                    self.assertEqual(f.activation().returncode,0)
                    path=self.ledger(f)
                    marker=path.with_suffix('.initialized').read_bytes()
                    state=json.loads(path.read_text())
                    if defect=='directory': path.unlink();path.mkdir(mode=0o700)
                    else:
                        if defect=='schema': state['schema']='unsupported'
                        elif defect=='session': state['session' if harness=='claude' else 'session_id']='different-session'
                        elif defect=='decision': state['inbox_activation']['prior']='true'
                        write(path,b'{broken' if defect=='json' else encoded(state))
                    before=None if defect=='directory' else path.read_bytes()
                    result=f.activation('coordination')
                    self.assertEqual((result.returncode,result.stdout),(1,b''))
                    self.assertEqual(json.loads(result.stderr)['error']['class'],
                                     'IsADirectoryError' if defect=='directory' else 'JSONDecodeError' if defect=='json' else 'ValueError')
                    self.assertEqual(path.with_suffix('.initialized').read_bytes(),marker)
                    if defect=='directory': self.assertTrue(path.is_dir())
                    else: self.assertEqual(path.read_bytes(),before)


if __name__=='__main__': unittest.main()
