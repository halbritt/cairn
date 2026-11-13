"""Guard the evidence boundary of the retained-effects fixture."""
from pathlib import Path
import tempfile
import unittest

from check_r06_retained_effects import go_issued, journal_observation, snapshot


class RetainedEffectsTests(unittest.TestCase):
    def test_executor_mapping_preserves_the_actual_context_and_operation(self):
        legacy = dict(binary=Path('/owned/python'), entry=Path('/owned/generation/coordination.py'))
        issued = ['/owned/python', '/owned/generation/coordination.py', 'inbox',
                  '--context', '/owned/state/inbox/issued.json', 'respond', '--version', '2', 'record']
        before = issued.copy()
        self.assertEqual(go_issued('/selected/go3', legacy, issued),
            ['/selected/go3', 'coordination', *issued[2:]])
        self.assertEqual(issued, before)
        for changed in ([*issued[:1], '/foreign/coordination.py', *issued[2:]],
                        ['/foreign/python', *issued[1:]], issued[:2] + ['hook']):
            with self.subTest(changed=changed), self.assertRaisesRegex(AssertionError, 'selected retained'):
                go_issued('/selected/go3', legacy, changed)

    def test_completed_and_settled_do_not_hide_a_possible_reply(self):
        journal = dict(kind='request', completion=dict(status='committed', result=dict(record_id='result', version=1)))
        self.assertEqual(journal_observation(journal), dict(pending=[], completed=True,
            settled=True, reply_possible=True, replied=False))
        journal['response'] = dict(status='pending')
        self.assertEqual(journal_observation(journal), dict(pending=['response'], completed=True,
            settled=False, reply_possible=False, replied=False))
        journal['response']['status'] = 'committed'
        self.assertEqual(journal_observation(journal), dict(pending=[], completed=True,
            settled=True, reply_possible=False, replied=True))

    def test_preservation_detects_changed_bytes_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'opaque'
            path.write_bytes(b'{"integer":900719925474099312345,"text":"opaque"}\n')
            path.chmod(0o600)
            before = snapshot([path])
            path.chmod(0o400)
            self.assertNotEqual(snapshot([path]), before)
            path.chmod(0o600)
            self.assertEqual(snapshot([path]), before)
            path.write_bytes(b'changed\n')
            self.assertNotEqual(snapshot([path]), before)
            path.unlink()
            path.symlink_to(Path(directory) / 'absent')
            with self.assertRaises(OSError):
                snapshot([path])


if __name__ == '__main__':
    unittest.main()
