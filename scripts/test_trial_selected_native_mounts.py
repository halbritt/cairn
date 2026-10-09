"""Constructed disposable packages only; no uncommitted port source is copied."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from trial_selected_native_mounts import PACKAGE_MODULES, MODULE_LIMIT, PackageRefusal, reviewed_package
from trial_task_eval import runtime_sandbox


class PackageMountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)/'selected_native'
        self.root.mkdir()
        # Import semantics fixtures, not a stand-in for a production reader.
        self.modules = {
            '__init__.py':'"""Constructed package import fixture."""\n',
            'evidence.py':'class Ledger:\n    pass\n',
            'frames.py':'from .evidence import Ledger\nclass Frames:\n    def __init__(self):\n        self.ledger=Ledger()\n',
            'retention.py':'from .frames import Frames\ndef accepts(value):\n    return type(value) is Frames\n',
        }
        for name,data in self.modules.items():
            (self.root/name).write_text(data)
        self.pins = {name:hashlib.sha256((self.root/name).read_bytes()).hexdigest() for name in PACKAGE_MODULES}

    def tearDown(self):
        self.temp.cleanup()

    def test_exact_runtime_mounts_exclude_tests_terminal_and_flat_modules(self):
        (self.root/'terminal_seam.py').write_text('raise RuntimeError("not admitted")\n')
        (self.root/'tests').mkdir()
        (self.root/'tests/private_fixture.py').write_text('PRIVATE_TEST_SENTINEL')
        mounts = reviewed_package(self.root,self.pins)
        self.assertEqual(set(mounts.manifest),{'selected_native/'+name for name in PACKAGE_MODULES})
        self.assertEqual(mounts.sources,tuple((self.root/name,'selected_native/'+name) for name in PACKAGE_MODULES))

    def test_real_sandbox_relative_imports_have_one_class_identity(self):
        mounts=reviewed_package(self.root,self.pins)
        impostor=Path(self.temp.name)/'frames.py'
        impostor.write_text('class Frames:\n    pass\n')
        command=runtime_sandbox()+['--unshare-net','--unshare-user','--disable-userns',
            '--dir','/sources','--dir','/sources/selected_native','--setenv','PYTHONPATH','/sources']
        for source,target in mounts.sources:
            command+=['--ro-bind',str(source),'/sources/'+target]
        command+=['--ro-bind',str(impostor),'/sources/frames.py','/usr/bin/python3','-B','-c',
            'import json,os; from selected_native.frames import Frames; from selected_native.retention import accepts; '
            'from selected_native import retention; import frames; '
            'print(json.dumps(dict(same_class=retention.Frames is Frames, accepts=accepts(Frames()), '
            'flat_rejected=not accepts(frames.Frames()), package_files=sorted(os.listdir("/sources/selected_native")))))']
        result=subprocess.run(command,capture_output=True,timeout=2,check=True)
        self.assertEqual(json.loads(result.stdout),dict(same_class=True,accepts=True,flat_rejected=True,package_files=sorted(PACKAGE_MODULES)))

    def test_missing_changed_symlink_and_nonregular_modules_refuse(self):
        path=self.root/'frames.py'
        original=path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(PackageRefusal,'package_source_unavailable'):
            reviewed_package(self.root,self.pins)
        path.write_bytes(original+b'\n')
        with self.assertRaisesRegex(PackageRefusal,'package_pin_mismatch'):
            reviewed_package(self.root,self.pins)
        path.unlink()
        target=Path(self.temp.name)/'real-frames.py'
        target.write_bytes(original)
        path.symlink_to(target)
        with self.assertRaisesRegex(PackageRefusal,'package_source_unavailable'):
            reviewed_package(self.root,self.pins)
        path.unlink()
        os.mkfifo(path)
        with self.assertRaisesRegex(PackageRefusal,'package_module_type'):
            reviewed_package(self.root,self.pins)

    def test_package_root_symlink_refuses(self):
        alias=Path(self.temp.name)/'alias'
        alias.symlink_to(self.root,target_is_directory=True)
        with self.assertRaisesRegex(PackageRefusal,'package_source_unavailable'):
            reviewed_package(alias,self.pins)

    def test_oversized_module_and_incomplete_or_extra_pin_set_refuse(self):
        (self.root/'frames.py').write_bytes(b'x'*(MODULE_LIMIT+1))
        with self.assertRaisesRegex(PackageRefusal,'package_module_bounds'):
            reviewed_package(self.root,self.pins)
        for pins in [{k:v for k,v in self.pins.items() if k!='__init__.py'},
                     {**self.pins,'terminal_seam.py':'0'*64}]:
            with self.assertRaisesRegex(PackageRefusal,'package_pin_set'):
                reviewed_package(self.root,pins)


if __name__=='__main__':
    unittest.main()
