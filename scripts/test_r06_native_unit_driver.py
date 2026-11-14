"""Driver custody and local provider checks; never starts a native client."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request

from check_r06_native_unit import (
    CAPTURE_BYTES, NativeUnit, OwnedMessages, capture_file, checked_images, copy_image, digest,
    install_null_selector, owned_command, selected_pin, write,
)
import check_r06_unit_census as census
from disposable_host import HostRefusal, OwnedCluster, pg_bindir


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


class DormantControlFixture(NativeUnit):
    """Use real disabled installations without declaring a native session."""

    def prepare(self):
        self.native = None
        python = copy_image(Path(sys.executable).resolve(), self.root / 'python-image/python')
        self.legacy = self.stage_legacy(python=python)
        self.current = self.legacy
        self.config_root = self.root / 'selected-configs'
        self.active = self.config_root / 'unit'
        self.active.mkdir(mode=0o700, parents=True)
        self.callbacks = self.root / 'callbacks'
        self.callbacks.mkdir(mode=0o700)
        selected = self.active / 'coordination.json'
        shutil.copyfile(self.legacy['config'], selected)
        selected.chmod(0o400)
        self.pins = {selected.name: digest(selected)}

    def catalog(self, readback):
        selection = dict(Directory=str(self.active), Pins=dict(self.pins))
        catalog = dict(Schema='cairn.watch-unit-catalog/1', Scope='disposable-fixture',
            Root=str(self.root), ConfigRoot=str(self.config_root), CallbackRoot=str(self.callbacks),
            Selections=[selection], Sessions=[], Callbacks=[], HookSelections=[],
            CredentialPaths=[str(self.host.token('worker'))],
            Bindings=[selected_pin(self.current['manifest'])], Clients=[selected_pin(self.forwarder)])
        return catalog, dict(Bindings=[selection], Sessions=[], Callbacks=[])


@unittest.skipUnless(os.environ.get('CAIRN_R06_UNIT_IMAGES'), 'requires explicit pinned HOST image manifest')
class NativeUnitControlTests(unittest.TestCase):
    def setUp(self):
        self.images, _ = checked_images(os.environ['CAIRN_R06_UNIT_IMAGES'])
        self.evidence = Path(os.environ['CAIRN_R06_CONTROL_EVIDENCE'])
        self.evidence.mkdir(mode=0o700)
        self.runtime = Path(tempfile.mkdtemp(prefix='r06-control-proof-', dir=Path.home() / '.cache'))
        write(self.evidence / 'scratch.json', dict(path=str(self.runtime)))
        self.previous_mask = os.umask(0o077)
        self.cluster = self.unit = None
        self.passed = False
        self.addCleanup(self.cleanup)
        self.cluster = OwnedCluster.start(self.runtime / 'pg', pg_bin=pg_bindir())
        unit_evidence = self.evidence / 'unit'
        unit_evidence.mkdir(mode=0o700)
        self.unit = DormantControlFixture(self.runtime / 'unit', unit_evidence, self.images, self.cluster)
        (self.unit.active / '.watch.lock').touch(mode=0o600)

    def cleanup(self):
        try:
            try:
                if self.unit is not None:
                    self.unit.close()
            finally:
                if self.cluster is not None:
                    pidfile = self.runtime / 'pg/data/postmaster.pid'
                    pid = int(pidfile.read_text().splitlines()[0]) if pidfile.exists() else None
                    self.assertTrue(pid is None or pid > 1)
                    stopped = self.cluster.stop()
                    absent = pid is None or not Path('/proc', str(pid)).exists()
                    write(self.evidence / 'pg-cleanup.json', dict(pid=pid, stopped=stopped, pid_absent=absent))
                    self.assertTrue(stopped and absent)
            if self.passed:
                shutil.rmtree(self.runtime)
        finally:
            os.umask(self.previous_mask)

    def refuse_original_install_directory(self):
        unit = self.unit
        unit.control('inspect', unit.legacy, None, 'wrong-install-source')
        wrong = unit.evidence / 'wrong-install-source.plan.json'
        before = census.snapshot(unit.root)
        refused = owned_command([self.images['control'], 'unit-control', 'reverse', '--plan', wrong,
            '--sha256', digest(wrong)], unit.evidence / 'wrong-install-reverse', env=unit.host.env, expected=1)
        refusal = json.loads(refused.stdout)
        self.assertEqual(refusal['error'], 'watch config-dir membership changed')
        self.assertFalse(refusal['inspected'])
        self.assertFalse(refusal['exchange']['Published'])
        self.assertEqual(census.snapshot(unit.root), before)
        return refusal

    def test_real_switch_reverse_inspect_uses_retained_watch_directory(self):
        unit = self.unit
        original = unit.legacy['config'].read_bytes()
        candidate = unit.go['config'].read_bytes()
        switched = unit.control('switch', unit.go, None, 'to-go3')
        self.assertTrue(switched['exchange']['Published'])
        unit.current = unit.go
        self.assertEqual((unit.active / 'coordination.json').read_bytes(), candidate)
        refusal = self.refuse_original_install_directory()
        reversed_ = unit.control('reverse', unit.legacy, None, 'to-python2')
        self.assertTrue(reversed_['exchange']['Published'])
        unit.current = unit.legacy
        self.assertEqual((unit.active / 'coordination.json').read_bytes(), original)
        expected = [('retained-to-go3', original), ('retained-to-python2', candidate)]
        for retained, (name, raw) in zip(unit.retained, expected, strict=True):
            directory = Path(retained['Directory'])
            self.assertEqual(directory, unit.config_root / name)
            self.assertEqual({p.name for p in directory.iterdir()}, {'coordination.json', '.watch.lock'})
            self.assertEqual((directory / 'coordination.json').read_bytes(), raw)
            self.assertEqual(retained['Pins'], {'coordination.json': digest(directory / 'coordination.json')})
            self.assertTrue(os.path.samefile(directory / '.watch.lock', unit.active / '.watch.lock'))
        final = unit.control('inspect', unit.go, None, 'restored-inspect')
        self.assertTrue(final['inspected'])
        self.assertFalse(final['exchange']['Published'])
        self.assertIsNone(unit.native)
        self.assertEqual(unit.native_clients, [])
        write(self.evidence / 'result.json', dict(success=True, native_client_executed=False,
            native_qualification=False, switch=switched, reverse=reversed_, inspect=final,
            wrong_install_refusal=refusal, retained=unit.retained,
            lock_inode=(unit.active / '.watch.lock').stat().st_ino))
        self.passed = True


if __name__ == '__main__':
    unittest.main()
