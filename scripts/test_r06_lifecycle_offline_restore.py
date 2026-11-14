"""Offline payload integrity and exact selected destination, without fake effects."""
import copy
import io
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest

import check_r06_lifecycle_offline_restore as fixture


class OfflinePayloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.payload = self.root / 'selected'
        self.payload.mkdir(mode=0o700)
        (self.payload / 'code').mkdir(mode=0o700)
        (self.payload / 'code/member').write_bytes(b'original selected bytes\n')
        (self.payload / 'code/member').chmod(0o400)
        self.archive = self.root / 'offline.tar.gz'
        self.manifest = fixture.seal(self.payload, self.archive)

    def test_initial_absence_and_exact_cold_restoration(self):
        shutil.rmtree(self.payload)
        self.assertFalse(self.payload.exists())
        fixture.restore(self.payload, self.archive, self.manifest)
        self.assertEqual((self.payload/'code/member').read_bytes(), b'original selected bytes\n')
        self.assertEqual((self.payload/'code/member').stat().st_mode & 0o777, 0o400)
        fixture.verify(self.payload, self.manifest)

    def test_missing_tampered_extra_and_unsafe_members_refuse(self):
        member = self.payload/'code/member'
        member.chmod(0o600)
        member.write_bytes(b'changed')
        member.chmod(0o400)
        with self.assertRaises(fixture.ClosureRefusal): fixture.verify(self.payload, self.manifest)
        member.unlink()
        with self.assertRaises(fixture.ClosureRefusal): fixture.verify(self.payload, self.manifest)
        member.write_bytes(b'original selected bytes\n')
        member.chmod(0o400)
        (self.payload/'extra').write_bytes(b'not selected')
        with self.assertRaises(fixture.ClosureRefusal): fixture.verify(self.payload, self.manifest)
        (self.payload/'extra').unlink()
        member.chmod(0o666)
        with self.assertRaises(fixture.ClosureRefusal): fixture.verify(self.payload, self.manifest)

    def test_foreign_declared_owner_and_different_destination_refuse(self):
        shutil.rmtree(self.payload)
        foreign = copy.deepcopy(self.manifest)
        foreign['uid'] = os.getuid() + 1
        with self.assertRaises(fixture.ClosureRefusal): fixture.restore(self.payload, self.archive, foreign)
        with self.assertRaises(fixture.ClosureRefusal):
            fixture.restore(self.root/'other', self.archive, self.manifest)
        self.assertFalse(self.payload.exists())
        self.assertFalse((self.root/'other').exists())

    def test_archive_corruption_refuses_before_creating_destination(self):
        shutil.rmtree(self.payload)
        with tarfile.open(self.archive, 'w:gz') as out:
            for name, expected in self.manifest['members'].items():
                member = tarfile.TarInfo(name)
                member.uid = os.getuid()
                member.mode = expected['mode']
                if expected['kind'] == 'directory': member.type = tarfile.DIRTYPE
                else: member.size = 7
                out.addfile(member, None if member.isdir() else io.BytesIO(b'changed'))
        with self.assertRaises(fixture.ClosureRefusal): fixture.restore(self.payload, self.archive, self.manifest)
        self.assertFalse(self.payload.exists())

    def test_traversal_absolute_duplicate_and_symlink_paths_refuse(self):
        for name in ['../escape', '/absolute', 'a/../escape', './relative']:
            with self.subTest(name=name), self.assertRaises(fixture.ClosureRefusal): fixture.safe_name(name)
        target = self.payload/'link'
        target.symlink_to(self.payload/'code/member')
        with self.assertRaises(fixture.ClosureRefusal): fixture.inventory(self.payload)
        self.assertFalse((self.root.parent/'escape').exists())

    def test_root_permissions_and_credential_members_are_not_payload(self):
        self.payload.chmod(0o777)
        with self.assertRaises(fixture.ClosureRefusal): fixture.verify(self.payload,self.manifest)
        self.payload.chmod(0o700)
        (self.payload/'worker.token').write_bytes(b'synthetic excluded credential')
        with self.assertRaises(fixture.ClosureRefusal): fixture.inventory(self.payload)

    def test_duplicate_archive_member_is_rejected_before_destination_exists(self):
        shutil.rmtree(self.payload)
        with tarfile.open(self.archive,'w:gz') as out:
            for _ in range(2):
                member=tarfile.TarInfo('code')
                member.uid=os.getuid()
                member.mode=0o700
                member.type=tarfile.DIRTYPE
                out.addfile(member)
        with self.assertRaises(fixture.ClosureRefusal): fixture.restore(self.payload,self.archive,self.manifest)
        self.assertFalse(self.payload.exists())

    def test_bytes_from_another_selected_member_do_not_satisfy_its_pin(self):
        other=self.payload/'code/other'
        other.write_bytes(b'different selected bytes\n');other.chmod(0o400)
        manifest=fixture.seal(self.payload,self.archive)
        original=(self.payload/'code/member').read_bytes()
        changed=other.read_bytes()
        shutil.rmtree(self.payload)
        with tarfile.open(self.archive,'w:gz') as out:
            for name,row in manifest['members'].items():
                item=tarfile.TarInfo(name);item.uid=os.getuid();item.mode=row['mode']
                if row['kind']=='directory':item.type=tarfile.DIRTYPE;out.addfile(item)
                else:
                    raw=changed if name=='code/member' else original
                    item.size=len(raw);out.addfile(item,io.BytesIO(raw))
        with self.assertRaises(fixture.ClosureRefusal): fixture.restore(self.payload,self.archive,manifest)
        self.assertFalse(self.payload.exists())


if __name__ == '__main__':
    unittest.main()
