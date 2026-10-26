"""Focused unit tests for unix_peer_transport and wake_transport."""
import json
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock

try:
    from integrations.lifecycle import unix_peer_transport as upt
    from integrations.lifecycle import wake_transport as wt
except ImportError:
    import unix_peer_transport as upt
    import wake_transport as wt


class UnixPeerTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ref = upt.process_reference(os.getpid())

    def test_proc_stat_and_process_liveness(self):
        parsed = upt.parse_proc_stat(os.getpid())
        self.assertEqual(parsed["pid"], os.getpid())
        self.assertEqual(parsed["start"], self.ref["start"])
        self.assertEqual(upt.process_identity_tuple(os.getpid()), (self.ref["start"], self.ref["boot"]))
        self.assertTrue(upt.process_alive(self.ref))
        self.assertFalse(upt.process_alive(dict(self.ref, start=self.ref["start"] + 1)))
        self.assertFalse(upt.process_alive(dict(self.ref, boot="00000000-0000-0000-0000-000000000000")))

    def test_stale_socket_cleanup_and_private_bind(self):
        sock_path = self.root / "bridge.sock"
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(str(sock_path))
        stale.close()
        self.assertTrue(sock_path.exists())

        server = upt.bind_private_socket(sock_path, backlog=2, accept_timeout=0.5)
        self.assertIsNotNone(server)
        self.addCleanup(server.close)
        mode = stat.S_IMODE(sock_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

        # Live listener must not be unlinked or rebound.
        second = upt.bind_private_socket(sock_path, backlog=2, accept_timeout=0.5)
        self.assertIsNone(second)

    def test_open_verified_socket_and_json_line_exchange(self):
        sock_path = self.root / "rpc.sock"
        server = upt.bind_private_socket(sock_path, backlog=2, accept_timeout=2.0)
        self.addCleanup(server.close)

        def serve_once():
            conn, _ = server.accept()
            with conn:
                req = upt.recv_json_line(conn, timeout=2.0)
                upt.send_json_line(conn, dict(id=req["id"], result=dict(ok=True)), compact=True)

        worker = threading.Thread(target=serve_once, daemon=True)
        worker.start()
        client, ident = upt.open_verified_socket(
            str(sock_path), self.ref, owner="process", return_identity=True
        )
        with client:
            self.assertEqual(ident["pid"], os.getpid())
            reply = upt.exchange_json_line(client, dict(id=7, method="ping"))
            self.assertEqual(reply, dict(id=7, result=dict(ok=True)))
        worker.join(timeout=2)

    def test_open_verified_socket_rejects_mismatched_peer(self):
        sock_path = self.root / "mismatch.sock"
        server = upt.bind_private_socket(sock_path, backlog=2, accept_timeout=2.0)
        self.addCleanup(server.close)

        class Unavailable(Exception):
            pass

        with self.assertRaisesRegex(Unavailable, "different process"):
            upt.open_verified_socket(
                str(sock_path),
                dict(self.ref, pid=os.getpid() + 1),
                unavailable_cls=Unavailable,
            )
        with self.assertRaisesRegex(Unavailable, "identity has changed"):
            upt.open_verified_socket(
                str(sock_path),
                dict(self.ref, start=self.ref["start"] + 1),
                unavailable_cls=Unavailable,
            )

    def test_bounded_peer_socket_and_line_limits(self):
        left, right = socket.socketpair(socket.AF_UNIX)
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        bounded = upt.BoundedPeerSocket(left, max_bytes=8, error_cls=ValueError)
        right.sendall(b"123456789")
        with self.assertRaisesRegex(ValueError, "exceeded byte limit"):
            bounded.recv(64)
        with self.assertRaisesRegex(ValueError, "unbounded socket read"):
            _ = bounded.makefile

        with self.assertRaisesRegex(ValueError, "exceeds the line limit"):
            upt.format_json_line({"x": "a" * 50}, max_bytes=16)

    def test_wake_transport_registry_and_dispatch(self):
        for harness, name, expected_sub in (
            ("codex", "codex-queue", "codex"),
            ("claude", "claude-channel", "channel bridge"),
            ("opencode", "opencode-queue", "opencode"),
            ("hermes", "hermes-queue", "hermes"),
        ):
            adapter = wt.get_harness_wake_transport(harness)
            self.assertIsNotNone(adapter)
            self.assertEqual(adapter.name, name)
            self.assertIs(wt.get_wake_transport(name), adapter)
            self.assertIn(expected_sub, wt.unavailable_wake_reason(harness).lower())
        self.assertIs(wt.get_wake_transport("unknown"), wt.TERMINAL_WAKE_TRANSPORT)


if __name__ == "__main__":
    unittest.main()
