"""Real retained Python client -> Go Claude bridge consumer checks for F01.

No provider, credentials, store API, lifecycle profile override or activation.
The expected-UID negative changes an expectation for the same owned peer; it
does not manipulate another user's processes or files.
"""
import importlib.util
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import tempfile
import time
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "f01_legacy_channel", ROOT / "integrations/lifecycle/claude_channel.py")
channel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(channel)
peer = channel._transport


class RetainedBridgeProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="f01-cli-", dir="/tmp")
        cls.addClassCleanup(cls.build.cleanup)
        cls.binary = Path(cls.build.name) / "cairn"
        subprocess.run(["go", "build", "-o", str(cls.binary), "./cmd/cairn"],
                       cwd=ROOT, check=True, capture_output=True, timeout=90)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="f01-peer-", dir="/tmp")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.process = subprocess.Popen(
            [str(self.binary), "claude-channel", "--directory", str(self.directory)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            bufsize=0, start_new_session=True)
        self.assertGreater(self.process.pid, 1)
        self.addCleanup(self.stop)
        self.buffer = b""

    def stop(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.assertGreater(self.process.pid, 1)
                self.process.kill()
                self.process.wait(timeout=3)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()

    def send_mcp(self, value):
        self.process.stdin.write(json.dumps(value).encode() + b"\n")
        self.process.stdin.flush()

    def read_mcp(self, timeout=5):
        deadline = time.monotonic() + timeout
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, "MCP output deadline")
            ready, _, _ = select.select([self.process.stdout], [], [], remaining)
            self.assertTrue(ready, "no MCP output")
            raw = os.read(self.process.stdout.fileno(), 65536)
            self.assertTrue(raw, "bridge closed stdout")
            self.buffer += raw
        line, self.buffer = self.buffer.split(b"\n", 1)
        return json.loads(line)

    def no_notification(self):
        self.assertEqual(self.buffer, b"")
        self.assertFalse(select.select([self.process.stdout], [], [], .1)[0],
                         "refused input emitted an MCP message")

    def initialize(self):
        self.send_mcp(dict(jsonrpc="2.0", id=1, method="server/discover", params={}))
        refusal = self.read_mcp()
        self.assertEqual(refusal["error"]["code"], -32601)
        self.registry = self.directory / (str(os.getpid()) + ".json")
        self.assertFalse(self.registry.exists())
        self.send_mcp(dict(jsonrpc="2.0", id=2, method="initialize", params=dict(
            protocolVersion="2025-11-25", capabilities={},
            clientInfo=dict(name="f01-process-fixture", version="1"))))
        result = self.read_mcp()["result"]
        self.assertIn("claude/channel", result["capabilities"]["experimental"])
        self.send_mcp(dict(jsonrpc="2.0", method="notifications/initialized"))
        deadline = time.monotonic() + 5
        while not self.registry.exists():
            self.assertLess(time.monotonic(), deadline, "registry deadline")
            time.sleep(.01)
        self.record = json.loads(self.registry.read_bytes())
        self.assertEqual(self.record["process"]["pid"], self.process.pid)
        self.assertEqual(self.record["parent"]["pid"], os.getpid())
        self.assertEqual(self.record["schema"], "cairn.claude-channel/1")

    def meta(self):
        return {key: str(uuid.uuid4()) for key in (
            "native_session_id", "agent_id", "execution_id", "delivery_id")}

    def connection(self):
        connection = socket.socket(socket.AF_UNIX)
        connection.settimeout(5)
        connection.connect(self.record["socket"])
        return connection

    def test_legacy_handshake_verified_client_and_meta_order(self):
        self.initialize()
        content, meta = "é <>& \u2028 retained client", self.meta()
        channel.write(self.record["socket"], self.record["process"],
                      self.record["parent"], content, meta)
        message = self.read_mcp()
        self.assertEqual(message["method"], "notifications/claude/channel")
        self.assertEqual(message["params"]["content"], content)
        self.assertEqual(message["params"]["meta"], meta)
        self.assertEqual(list(message["params"]["meta"]), list(meta))

    def test_owned_peer_and_synthetic_expected_uid_and_identity_mismatch(self):
        self.initialize()
        with self.connection() as connection:
            pid, uid, _ = peer.peer_credentials(connection)
            self.assertEqual((pid, uid), (self.process.pid, os.getuid()))
            peer.verify_peer_credentials(connection, self.record["process"],
                                         expected_uid=os.getuid())
            with self.assertRaisesRegex(RuntimeError, "another user"):
                peer.verify_peer_credentials(connection, self.record["process"],
                                             expected_uid=os.getuid() + 1)
        for field, changed in (("pid", os.getpid()), ("start", self.record["process"]["start"] + 1),
                               ("boot", "synthetic-other-boot")):
            with self.subTest(field=field), self.connection() as connection:
                reference = dict(self.record["process"], **{field: changed})
                with self.assertRaises(RuntimeError):
                    peer.verify_peer_credentials(connection, reference)
        self.no_notification()

    def test_frame_limit_fragmentation_eof_and_actual_read_deadline(self):
        self.initialize()
        request = dict(parent=self.record["parent"], content="", meta=self.meta())
        overhead = len(json.dumps(request, separators=(",", ":")).encode())
        request["content"] = "x" * (8192 - overhead)
        raw = json.dumps(request, separators=(",", ":")).encode()
        self.assertEqual(len(raw), 8192)
        with self.connection() as connection:
            for part in (raw[:17], raw[17:], b"\n"):
                connection.sendall(part)
            self.assertEqual(peer.recv_json_line(connection), {"status": "written"})
        self.assertEqual(self.read_mcp()["params"]["content"], request["content"])
        for payload, half_close, delayed in (
                (raw + b"x\n", False, False), (b"not JSON\n", False, False),
                (b"{", True, False), (b"{", False, True)):
            with self.subTest(size=len(payload), eof=half_close, timeout=delayed), self.connection() as connection:
                started = time.monotonic()
                connection.sendall(payload)
                if half_close:
                    connection.shutdown(socket.SHUT_WR)
                self.assertEqual(peer.recv_json_line(connection), {"status": "unavailable"})
                if delayed:
                    self.assertGreater(time.monotonic() - started, 1)
                    self.assertLess(time.monotonic() - started, 5)
        self.no_notification()

    def test_killed_owned_bridge_is_not_a_verified_peer(self):
        self.initialize()
        self.assertGreater(self.process.pid, 1)
        self.process.kill()
        self.process.wait(timeout=3)
        with self.assertRaises(channel.ChannelUnavailable):
            channel.write(self.record["socket"], self.record["process"],
                          self.record["parent"], "after owned exit", self.meta())


if __name__ == "__main__":
    unittest.main()
