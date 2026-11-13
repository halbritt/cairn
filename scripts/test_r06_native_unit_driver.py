"""Driver custody and local provider checks; never starts a native client."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request

from check_r06_native_unit import CAPTURE_BYTES, OwnedMessages, capture_file, install_null_selector
from disposable_host import HostRefusal


class NativeUnitDriverTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix='r06-driver-test-')
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name) / 'unit'
        self.root.mkdir(mode=0o700)
        self.evidence = Path(self.scratch.name) / 'evidence'
        self.evidence.mkdir(mode=0o700)

    def test_actual_null_selector_records_raw_input_and_result(self):
        selector, pin = install_null_selector(self.root, self.evidence)
        raw = json.dumps(dict(text='日本語 🙂', integer=2 ** 100), ensure_ascii=False).encode()
        argv = [str(selector), '--print', '--json-schema', '{}']
        result = subprocess.run(argv, input=raw, capture_output=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), dict(structured_output=dict(checkpoint=None, workstream=None, memories=[])))
        calls = list((self.evidence / 'selector-calls').glob('*.json'))
        self.assertEqual(len(calls), 1)
        record = json.loads(calls[0].read_bytes())
        self.assertEqual(record['argv'], argv)
        self.assertEqual(record['program_sha256'], pin['sha256'])
        self.assertFalse(record['model_account_invoked'])
        self.assertEqual(calls[0].with_suffix('.stdin').read_bytes(), raw)
        self.assertEqual(calls[0].with_suffix('.stdout').read_bytes(), result.stdout)

    def test_copy_preserves_bytes_and_labels_original_identity(self):
        source = self.root / 'memory.json'
        raw = b'\xff\x00' + '日本語 🙂 900719925474099312345'.encode()
        source.write_bytes(raw)
        destination = self.evidence / 'selected-memory.raw'
        record = capture_file(self.root, source, destination)
        self.assertEqual(destination.read_bytes(), raw)
        self.assertEqual(record['source_inode'], source.stat().st_ino)
        self.assertEqual(record['source_device'], source.stat().st_dev)
        self.assertFalse(record['copy_is_original_inode'])
        self.assertFalse(os.path.samefile(source, destination))
        self.assertEqual(record['sha256'], hashlib.sha256(raw).hexdigest())

    def test_capture_refuses_credential_alias_escape_and_oversize(self):
        credential = self.root / 'worker.token'
        credential.write_bytes(b'synthetic-credential-must-not-be-copied')
        alias = self.root / 'state.json'
        os.link(credential, alias)
        outside = Path(self.scratch.name) / 'outside'
        outside.write_bytes(b'outside')
        large = self.root / 'large'
        with large.open('wb') as stream:
            stream.truncate(CAPTURE_BYTES + 1)
        link = self.root / 'symlink'
        link.symlink_to(alias)
        for source in (credential, alias, outside, large, link):
            with self.subTest(source=source.name):
                destination = self.evidence / source.name
                with self.assertRaises(HostRefusal):
                    capture_file(self.root, source, destination, [credential])
                self.assertFalse(destination.exists())

    def test_provider_captures_actual_body_without_authorization_headers(self):
        provider = OwnedMessages(self.evidence / 'provider')
        try:
            raw = json.dumps(dict(model='fixture', messages=[dict(role='user', content='日本語 🙂')],
                                  integer=2 ** 100), ensure_ascii=False).encode()
            request = urllib.request.Request(provider.url + '/v1/messages', data=raw,
                headers={'Content-Type': 'application/json', 'Authorization': 'fixture-header-must-not-be-copied'})
            with urllib.request.urlopen(request, timeout=10) as response:
                self.assertIn(b'R04 local fixture reply.', response.read())
            bodies = list(provider.evidence.glob('*.body'))
            self.assertEqual(len(bodies), 1)
            self.assertEqual(bodies[0].read_bytes(), raw)
            self.assertEqual(provider.requests[0]['integer'], 2 ** 100)
            self.assertNotIn('fixture-header-must-not-be-copied', bodies[0].with_suffix('.json').read_text())
            self.assertEqual(provider.failures, [])
        finally:
            provider.close()
        self.assertFalse(provider.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
