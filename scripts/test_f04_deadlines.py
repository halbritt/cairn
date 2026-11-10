"""Trusted installed hook budgets, whole process timings, and prelaunch expiry."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import unittest

from test_f04_activation import ROOT, Fixture, compat, encoded, sha, write


ROWS={
    'codex':{'memory':{'SessionStart':13,'UserPromptSubmit':13,'PreCompact':150,'Stop':150},
             'coordination':{'SessionStart':15,'UserPromptSubmit':15,'Stop':15,'SessionEnd':2,'Interrupt':2,'PostToolUse':2}},
    'claude':{'memory':{'SessionStart':13,'UserPromptSubmit':13,'PreCompact':150,'SessionEnd':150,'PostToolUse':13,'PostToolUseFailure':13},
              'coordination':{'SessionStart':15,'UserPromptSubmit':15,'Stop':15,'SessionEnd':2,'PostToolUse':2}},
}


class F04DeadlineTests(unittest.TestCase):
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

    def traced(self,f,role,budget):
        trace=f.root/'trace'
        before=time.monotonic()
        result=subprocess.run(['/usr/bin/strace','-f','-qq','-ttt','-s','256','-o',str(trace),
                               '-e','trace=execve,exit_group',str(f.launcher(role,budget))],
                              input=f.raw,capture_output=True,cwd=f.root,env=f.env(),timeout=18)
        return result,time.monotonic()-before,trace.read_text().splitlines()

    def test_every_installed_bound_event_uses_its_own_exact_vector_and_budget(self):
        for harness,roles in ROWS.items():
            for role,events in roles.items():
                for event,budget in events.items():
                    with self.subTest(harness=harness,role=role,event=event):
                        f=self.fixture(harness+role+event,harness=harness)
                        # Empty source inputs prevent a capture selector or a
                        # provider call; a source capture refusal is recorded.
                        f.raw=encoded(dict(f.event,hook_event_name=event,prompt='',host_pid=os.getpid()))
                        result,elapsed,trace=self.traced(f,role,budget)
                        self.assertNotIn(b'compatibility_preflight',result.stderr)
                        self.assertIn(result.returncode,(0,1),result.stderr)
                        self.assertLess(elapsed,budget)
                        go=[(i,row) for i,row in enumerate(trace) if 'execve("/proc/self/fd/' in row]
                        self.assertEqual(len(go),1,'\n'.join(trace))
                        index,row=go[0]
                        stamp=lambda line:float(re.match(r'\s*\d+\s+(\d+\.\d+)\s',line).group(1))
                        began=stamp(trace[0]);launched=stamp(row)
                        ended=next(stamp(r) for r in trace[index+1:] if 'exit_group(' in r)
                        finished=max(stamp(r) for r in trace if 'exit_group(' in r)
                        print('F04_HOST_ROW',json.dumps(dict(harness=harness,role=role,event=event,H=budget,
                            total_seconds=elapsed,interpreter_receipt_hash_spawn_seconds=launched-began,
                            activation_seconds=ended-launched,retained_seconds=finished-ended,
                            exit=result.returncode,stdout_bytes=len(result.stdout),stderr_bytes=len(result.stderr))),flush=True)
                        # No absent/unsupported row is added to the qualification.
                        for row in trace:
                            if 'execve(' in row: self.assertNotRegex(row,r'execve\("[^\"]*/(?:codex|claude)"')

    def test_missing_invalid_or_wrong_row_timeout_refuses_before_activation(self):
        for role,budget in (('memory',12),('coordination',13),('memory',150),('coordination',2)):
            f=self.fixture(role+str(budget))
            # UPS has trusted rows 13/15, even if event text claims another H.
            f.raw=encoded(dict(f.event,timeout=150,deadline=time.monotonic()+10000))
            result,elapsed,trace=self.traced(f,role,budget)
            self.assertEqual((result.returncode,result.stdout),(1,b''))
            self.assertIn(b'compatibility_preflight',result.stderr)
            self.assertFalse(any('execve("/proc/self/fd/' in r for r in trace))
            self.assertFalse(Path(f.memory['state_dir']).exists())
            self.assertEqual(f.api_calls(),[])
        f=self.fixture('missing-timeout')
        vector=compat.launch_vector('/usr/bin/python3',f.receipt_path,f.receipt_sha,'memory','memory-hook.py',f.memory_path,13,f.launch_root)
        index=vector.index('--hook-timeout');del vector[index:index+2]
        result=subprocess.run(vector,input=f.raw,capture_output=True,cwd=f.root,env=f.env(),timeout=5)
        self.assertEqual((result.returncode,result.stdout),(1,b''))
        self.assertIn(b'compatibility_preflight',result.stderr)
        self.assertFalse(Path(f.memory['state_dir']).exists())

    def test_expiration_during_checked_bootstrap_prevents_any_child_launch(self):
        for role,budget,event in (('memory',13,'UserPromptSubmit'),('coordination',2,'PostToolUse')):
            f=self.fixture('prelaunch-'+role)
            f.raw=encoded(dict(f.event,hook_event_name=event,prompt='ordinary prompt'))
            root=Path(f.receipt['snapshot_root'])
            sources={name:(root/name).read_bytes() for name in f.receipt['members']}
            # A separately pinned TEST closure stalls before run_checked. The
            # production verifier's actual t0 must already be running.
            sources['lifecycle_compatibility.py']+=('\nimport time as _fixture_clock\n_fixture_clock.sleep('+str(budget+0.05)+')\n').encode()
            hashes={name:sha(raw) for name,raw in sources.items()}
            identity=sha(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode())
            snapshot=f.root/identity;snapshot.mkdir(mode=0o700)
            for name,raw in sources.items(): write(snapshot/name,raw)
            f.receipt.update(snapshot_root=str(snapshot),source_revision='TEST pinned prelaunch delay',
                             members={name:dict(sha256=hashes[name],bytes=len(raw)) for name,raw in sources.items()})
            write(f.receipt_path,encoded(f.receipt));f.receipt_sha=sha(f.receipt_path.read_bytes())
            result,elapsed,trace=self.traced(f,role,budget)
            self.assertEqual((result.returncode,result.stdout),(1,b''))
            self.assertIn(b'OSError' if role=='memory' else b'TimeoutError',result.stderr)
            self.assertGreaterEqual(elapsed,budget)
            self.assertLess(elapsed,budget+2)
            self.assertFalse(any('execve("/proc/self/fd/' in r for r in trace))
            self.assertEqual(f.api_calls(),[])
            self.assertFalse(Path(f.memory['state_dir']).exists())


if __name__=='__main__': unittest.main()
