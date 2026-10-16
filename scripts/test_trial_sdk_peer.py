"""Pure representation checks; actual endpoint checks are explicitly selected."""
import socket
import sys
import unittest
from unittest.mock import patch

import trial_sdk_peer as peer


class Credentials(unittest.TestCase):
    def instance(self, uid=1000, gid=2000):
        result = peer.Peer.__new__(peer.Peer)
        result._pid, result._uid, result._gid = 123, uid, gid
        return result

    def record(self, pid=123, uid=1000, gid=2000):
        # Independent integer bytes, including unsigned high-bit identities.
        body = (pid.to_bytes(4, sys.byteorder, signed=True)
                + uid.to_bytes(4, sys.byteorder) + gid.to_bytes(4, sys.byteorder))
        return socket.SOL_SOCKET, socket.SCM_CREDENTIALS, body

    def test_full_unsigned_identity_range(self):
        for uid, gid in ((0, 0), (1000, 2000), (2**31, 1),
                         (1, 2**31), (2**32-1, 2**32-1)):
            with self.subTest(uid=uid, gid=gid):
                self.assertEqual(self.instance(uid, gid)._ancillary(
                    [self.record(uid=uid, gid=gid)]), (False, False))

    def test_each_identity_mismatch_refuses(self):
        for args in ({'pid':124}, {'uid':1001}, {'gid':2001}):
            with self.subTest(args=args):
                self.assertEqual(self.instance()._ancillary([self.record(**args)]),
                                 (True, False))

    def test_missing_duplicate_and_wrong_width_refuse(self):
        item = self.record()
        for records in ([], [item, item], [(item[0], item[1], b'')],
                        [(item[0], item[1], item[2][:-1])],
                        [(item[0], item[1], item[2]+b'x')]):
            with self.subTest(records=records):
                self.assertEqual(self.instance()._ancillary(records), (True, False))

    def test_unsupported_representation_refuses(self):
        actual = peer.ctypes.sizeof
        with patch.object(peer.ctypes, 'sizeof',
                          side_effect=lambda value: 8 if value is peer.ctypes.c_uint else actual(value)):
            with self.assertRaisesRegex(peer.PeerError, '^peer_credential_abi$'):
                peer._check_credential_abi()


if __name__ == '__main__':
    unittest.main()
