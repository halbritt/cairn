"""Local collector tests use only an owned non-agent dummy process."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import r06_agy_current_observation as collector


class CurrentObservationCollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.root.chmod(0o700)

    def test_actual_owned_process_generation_and_numeric_cleanup(self):
        executable=self.root/'owned-non-agent-sleep'
        shutil.copyfile(shutil.which('sleep'),executable)
        executable.chmod(0o500)
        child=subprocess.Popen([str(executable),'15'])
        self.assertGreater(child.pid,1)
        try:
            before=collector.capture_owned_generation(child.pid)
            after=collector.capture_owned_generation(child.pid)
            self.assertEqual(before,after)
            self.assertEqual(before['Client']['SHA256'],collector.sha(executable))
            self.assertEqual(before['ClientArgv'],[str(executable),'15'])
            self.assertEqual(before['Process']['PID'],child.pid)
            pin=collector.write_generation_pair(self.root/'generation.json',before,after)
            self.assertEqual(pin['SHA256'],collector.sha(pin['Path']))
            self.assertEqual(json.loads(Path(pin['Path']).read_text())['After'],after)
        finally:
            child.terminate()
            code=child.wait(timeout=5)
        self.assertIsInstance(code,int)
        self.assertFalse(Path(f'/proc/{child.pid}').exists())
        evidence=os.environ.get('CAIRN_AGY_CURRENT_TEST_EVIDENCE')
        if evidence:
            Path(evidence).write_text(json.dumps(dict(pid=child.pid,exit=code,joined=True,
                absent=True,pid_greater_than_one=child.pid>1,group_signals=False,
                child_kind='owned non-agent dummy; no native qualification'))+'\n')

    def test_foreign_self_and_unsafe_pids_refuse(self):
        for pid in [0,1,-1,True,os.getpid(),os.getppid()]:
            with self.subTest(pid=pid),self.assertRaises((ValueError,FileNotFoundError,ProcessLookupError)):
                collector.capture_owned_generation(pid)

    def test_trace_offsets_pin_full_unmodified_bytes(self):
        path=self.root/'raw.trace'
        raw=b'200 write(1, "unit record", 11) = 11\n'
        path.write_bytes(raw)
        path.chmod(0o600)
        value=collector.trace_interval(path,0,len(raw),[])
        self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(value['Trace']['SHA256'],collector.sha(path))
        for start,end in [(1,len(raw)),(0,len(raw)-1),(0,len(raw)+1),(0,0)]:
            with self.subTest(start=start,end=end),self.assertRaises(ValueError):
                collector.trace_interval(path,start,end,[])

    def test_alias_and_uncontrolled_files_refuse(self):
        path=self.root/'input'
        path.write_bytes(b'synthetic unit bytes')
        alias=self.root/'alias';alias.symlink_to(path)
        with self.assertRaises(ValueError):collector.pin(alias)
        path.chmod(0o666)
        with self.assertRaises(ValueError):collector.pin(path)

    def test_oversize_trace_refuses_before_reading_sparse_payload(self):
        path=self.root/'oversize.trace'
        with path.open('wb') as output:output.truncate(256*1024*1024+1)
        path.chmod(0o600)
        with self.assertRaisesRegex(ValueError,'256 MiB'):
            collector.trace_interval(path,0,path.stat().st_size,[])

    def test_generation_record_is_exclusive_and_private(self):
        target=self.root/'generation.json'
        collector.write_generation_pair(target,{'unit':'before'},{'unit':'after'})
        with self.assertRaises(FileExistsError):collector.write_generation_pair(target,{}, {})
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):collector.write_generation_pair(self.root/'foreign.json',{}, {})


if __name__=='__main__':
    unittest.main()
