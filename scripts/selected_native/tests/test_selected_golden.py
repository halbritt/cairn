"""Protection written for the repository port (not part of the reviewed originals).

(1) The default memory-only reader still reproduces the evidence of the accepted pilot reader on a golden stream and still
refuses the coding surface with the same fixed codes. (2) Package boundaries: standard library and sibling imports only,
no host paths, no generic top-level module names, the stated retention limitation present in the documentation."""
import ast
import json
import subprocess
import sys
import unittest
from pathlib import Path

from .. import frames
from ..evidence import PULL, SEARCH
from . import test_selected_coding as t

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
SCRIPTS = PACKAGE.parent
ROOT = SCRIPTS.parent
GOLDEN = json.loads((HERE / 'testdata' / 'default-memory-only-evidence.golden.json').read_text())


class DefaultBehaviourGolden(unittest.TestCase):
    def test_default_reader_reproduces_the_accepted_reader_evidence(self):
        reader = frames.Frames(t.S, t.M, t.V)
        for event in [t.init([SEARCH, PULL])] + t.memory_calls() + [t.final()]:
            reader.feed(t.line(event))
        done = reader.finish(0)
        self.assertEqual(done['evidence'], GOLDEN['evidence'])
        self.assertEqual(done['init']['tools'], GOLDEN['init_tools'])
        self.assertEqual(done['final']['work_product'], GOLDEN['work_product'])

    def test_default_reader_refuses_the_coding_surface_with_fixed_codes(self):
        for events, code in [([t.init()], 'tool_allowlist'),
                             ([t.init([SEARCH, PULL]), t.message('assistant', [t.use_block('x', 'Read', {'file_path': 'a'})])], 'tool_not_allowed'),
                             ([t.init([SEARCH, PULL], 'default')], 'init_identity')]:
            with self.subTest(code=code), self.assertRaises(frames.FrameError) as caught:
                t.feed_all(frames.Frames(t.S, t.M, t.V), events)
            self.assertEqual(str(caught.exception), code)

    def test_default_reader_refuses_the_slash_2_declaration(self):
        with self.assertRaises(frames.FrameError) as caught:
            t.feed_all(frames.Frames(t.S, t.M, t.V), [t.init([SEARCH, PULL]), t.message('assistant', [t.use_block(
                's', SEARCH, {'request_id': '40000000-0000-4000-8000-000000000001', 'query': 'q', 'memory_budget_bytes': 9500,
                              'inspection_policy': 'first-fitting-whole/2'})])])
        self.assertEqual(str(caught.exception), 'inspection_policy_shape')


class PackageBoundaries(unittest.TestCase):
    MODULES = ('evidence', 'frames', 'terminal_seam', 'retention')

    def test_runtime_modules_import_only_the_standard_library_and_siblings(self):
        forbidden = {'socket', 'subprocess', 'http', 'urllib', 'ssl', 'ftplib', 'smtplib', 'ctypes', 'multiprocessing', 'asyncio', 'requests'}
        for name in self.MODULES:
            tree = ast.parse((PACKAGE / (name + '.py')).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    top = None if node.level else node.module.split('.')[0]
                    self.assertTrue(node.level == 1 or top in sys.stdlib_module_names, (name, node.module))
                    self.assertNotIn(top, forbidden, name)
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split('.')[0]
                        self.assertIn(top, sys.stdlib_module_names, (name, alias.name))
                        self.assertNotIn(top, forbidden, name)

    def test_no_host_or_operational_paths_in_package_or_tests(self):
        for path in list(PACKAGE.glob('*.py')) + list(HERE.glob('*.py')) + list((HERE / 'testdata').glob('*.json')):
            if path.name == Path(__file__).name:
                continue                          # this file spells the needles
            text = path.read_text()
            for needle in ('/home/', '.local/', 'agent340', 'native-resume-pilot', '/tmp/cairn'):
                self.assertNotIn(needle, text, '%s contains %s' % (path.name, needle))

    def test_importing_the_package_registers_no_generic_top_level_modules(self):
        code = ('import sys; sys.path.insert(0, %r); import selected_native.frames, selected_native.evidence, '
                'selected_native.retention, selected_native.terminal_seam; '
                'bad = [m for m in ("frames", "evidence", "retention", "terminal_seam") if m in sys.modules]; '
                'sys.exit(1 if bad else 0)' % str(SCRIPTS))
        done = subprocess.run([sys.executable, '-B', '-I', '-c', code], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_documented_limits_are_present(self):
        text = (ROOT / 'docs' / 'selected-native-reader.md').read_text()
        for sentence in ('does not establish authenticated exit', 'no provider', 'not runtime admission',
                         'caller_supplied_not_independently_attested'):
            self.assertIn(sentence, text)

    def test_retention_still_labels_exit_and_return_as_unattested(self):
        from ..retention import PreparedReceipt
        receipt = PreparedReceipt('r', 'a' * 64, 1, 'b' * 64)
        self.assertEqual((receipt.exit_basis, receipt.return_receipt), ('caller_supplied_not_independently_attested', False))


if __name__ == '__main__':
    unittest.main()
