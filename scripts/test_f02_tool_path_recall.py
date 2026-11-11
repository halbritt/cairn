"""The same symlink-parent relation through actual checked recall entries."""
import json
import shutil
import unittest

import test_f02_checked_ordinary as fixture


class ToolPathRecallTests(unittest.TestCase):
    setUpClass=classmethod(fixture.CheckedOrdinaryTests.setUpClass.__func__)
    tearDownClass=classmethod(fixture.CheckedOrdinaryTests.tearDownClass.__func__)
    setUp=fixture.CheckedOrdinaryTests.setUp
    close=fixture.CheckedOrdinaryTests.close
    source=fixture.CheckedOrdinaryTests.source
    go=fixture.CheckedOrdinaryTests.go
    go_vector=fixture.CheckedOrdinaryTests.go_vector
    selected_vector=fixture.CheckedOrdinaryTests.selected_vector
    go_launcher=fixture.CheckedOrdinaryTests.go_launcher
    state=fixture.CheckedOrdinaryTests.state
    pair=fixture.CheckedOrdinaryTests.pair

    def test_relative_symlink_parent_recall_entities_match_source(self):
        outside=self.root/'outside'/'dir';outside.mkdir(parents=True)
        (self.f.root/'link').symlink_to(outside,target_is_directory=True)
        (self.f.root/'b.py').write_text('lexical decoy')
        target=self.f.root/'sub'/'dir';target.mkdir(parents=True)
        (target.parent/'b.py').write_text('resolved internal file')
        (self.f.root/'internal').symlink_to(target,target_is_directory=True)
        for path,expected in (('link/../b.py',[]),('internal/../b.py',['sub/b.py']),('sub/../b.py',['b.py'])):
            with self.subTest(path=path):
                shutil.rmtree(self.f.memory['state_dir'],ignore_errors=True);self.calls.clear()
                self.f.event.update(hook_event_name='UserPromptSubmit',prompt='repair '+path)
                self.f.raw=fixture.encoded(self.f.event)
                self.assertEqual(self.pair().returncode,0)
                searches=[body for operation,body,_ in self.calls if operation=='index']
                self.assertEqual(len(searches),1)
                self.assertEqual(searches[0].get('entities',[]),[dict(kind='file',name=name) for name in expected])
                print('RECALL_PATH_PAIR='+json.dumps(dict(path=path,expected_files=expected,request=searches[0])),flush=True)
