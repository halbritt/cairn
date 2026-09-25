"""Claude channel client: verified registry selection and bounded bridge writes."""
import importlib.util
import json
import os
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('claude_channel', ROOT/'integrations/lifecycle/claude_channel.py')
channel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(channel)
engine_spec = importlib.util.spec_from_file_location('coordination_channels', ROOT/'integrations/lifecycle/coordination.py')
engine = importlib.util.module_from_spec(engine_spec)
engine_spec.loader.exec_module(engine)

META = dict(native_session_id='f9ac2f4e-1f19-4d0d-b3a4-2b2a8f3c1f01', agent_id='0e0dbe0e-1c0e-4d5e-9d7a-3f2f9f3c2f02',
            execution_id='c40cc88c-7d62-4c88-8fa8-1f1f8f3c3f03', delivery_id='79dbd04d-d2bf-4b48-9b0f-4f4f7f3c4f04')


class Bridge(threading.Thread):
    """A stand-in channel bridge serving one line per connection."""

    def __init__(self, root, status='written'):
        super().__init__(daemon=True)
        self.path = root/'bridge.sock'
        self.control = root/'control.json'
        self.control.write_text(json.dumps({'status': status}))
        self.log = root/'requests.jsonl'
        self.connections = 0
        self.listener = socket.socket(socket.AF_UNIX)
        self.listener.bind(str(self.path))
        self.path.chmod(0o600)
        self.listener.listen(4)
        self.listener.settimeout(10)

    def run(self):
        while True:
            try:
                conn, _ = self.listener.accept()
            except OSError:
                return
            self.connections += 1
            with conn:
                conn.settimeout(4)
                mode = json.loads(self.control.read_text()).get('status', 'written')
                data = b''
                while b'\n' not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if data:
                    with self.log.open('a') as file:
                        file.write(data.decode())
                    if mode == 'drop':
                        continue
                    if mode == 'garbage':
                        conn.sendall(b'not json at all\n'); continue
                    if mode == 'array':
                        conn.sendall(b'[1,2,3]\n'); continue
                    if mode == 'nostatus':
                        conn.sendall(b'{"other": 1}\n'); continue
                    if mode == 'interrupt':
                        try:
                            conn.recv(1)
                        except OSError:
                            pass  # The client may fail before any byte lands.
                        continue  # Close mid-write: the send may have been partial.
                    if mode == 'drip':
                        for _ in range(20):
                            try:
                                conn.sendall(b' ')
                            except OSError:
                                return
                            time.sleep(0.05)
                        continue
                    conn.sendall(json.dumps({'status': mode}).encode()+b'\n')

    def requests(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []


class ChannelClient(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.reference = engine.process_reference(os.getpid())
        self.bridge_reference = engine.process_reference(os.getpid())
        self.bridge = Bridge(self.root)
        self.addCleanup(lambda: self.bridge.join(timeout=2))
        self.addCleanup(self.bridge.listener.close)
        self.bridge.start()

    def write(self, status=None):
        if status is not None:
            self.bridge.control.write_text(json.dumps({'status': status}))
        return channel.write(str(self.bridge.path), self.bridge_reference, self.reference,
                             'wake content', META)

    def test_written_round_trips_one_verified_line(self):
        self.write('written')
        request = self.bridge.requests()[0]
        self.assertEqual(request, dict(parent=self.reference, content='wake content', meta=META))

    def test_bridge_refusal_is_retryable(self):
        with self.assertRaisesRegex(channel.ChannelUnavailable, 'refused'):
            self.write('unavailable')
        self.assertEqual(len(self.bridge.requests()), 1)

    def test_uncertain_reply_and_lost_reply_are_never_resent(self):
        for status in ('uncertain', 'drop'):
            with self.subTest(status=status):
                with self.assertRaises(channel.ChannelError) as caught:
                    self.write(status)
                self.assertNotIsInstance(caught.exception, channel.ChannelUnavailable)
        self.assertEqual(len(self.bridge.requests()), 2)

    def test_drip_reply_has_one_overall_deadline(self):
        with mock.patch.object(channel, 'REPLY_TIMEOUT_SECONDS', 0.2, create=True):
            with self.assertRaisesRegex(channel.ChannelError, 'reply timed out') as caught:
                self.write('drip')
        self.assertNotIsInstance(caught.exception, channel.ChannelUnavailable)
        self.assertEqual(len(self.bridge.requests()), 1)

    def test_wrong_bridge_socket_is_refused_before_sending(self):
        with self.assertRaisesRegex(channel.ChannelUnavailable, 'different process'):
            channel.write(str(self.bridge.path), dict(self.bridge_reference, pid=os.getpid()+1),
                          self.reference, 'wake content', META)
        self.assertEqual(self.bridge.requests(), [])

    def test_changed_bridge_identity_is_refused_before_sending(self):
        with self.assertRaisesRegex(channel.ChannelUnavailable, 'identity'):
            channel.write(str(self.bridge.path), dict(self.bridge_reference, start=self.bridge_reference['start']+1),
                          self.reference, 'wake content', META)
        self.assertEqual(self.bridge.requests(), [])

    def test_absent_endpoint_is_retryable_not_uncertain(self):
        absent = self.root/'absent.sock'
        with self.assertRaises(channel.ChannelUnavailable):
            channel.write(str(absent), self.bridge_reference, self.reference, 'wake content', META)

    def test_registry_selection_requires_the_exact_native_process(self):
        directory = self.root/'channels'
        directory.mkdir()
        registry = directory/f"{self.reference['pid']}.json"
        registry.write_text(json.dumps(dict(schema='cairn.claude-channel/1', parent=self.reference,
            process=self.bridge_reference, socket=str(self.bridge.path))))
        config = dict(harness='claude', claude_channel_dir=str(directory))
        self.assertEqual(engine.claude_channel_endpoint(config, self.reference),
                         dict(socket=str(self.bridge.path), process=self.bridge_reference,
                              parent=self.reference))
        # A registry for one process never selects for another, live or reused.
        self.assertIsNone(engine.claude_channel_endpoint(config, dict(self.reference, pid=os.getpid()+1)))
        self.assertIsNone(engine.claude_channel_endpoint(config, dict(self.reference, start=self.reference['start']+1)))
        # A dead bridge or a foreign record is never selected.
        registry.write_text(json.dumps(dict(schema='cairn.claude-channel/1', parent=self.reference,
            process=dict(self.bridge_reference, start=1), socket=str(self.bridge.path))))
        self.assertIsNone(engine.claude_channel_endpoint(config, self.reference))
        registry.write_text(json.dumps(dict(schema='foreign', parent=self.reference,
            process=self.bridge_reference, socket=str(self.bridge.path))))
        self.assertIsNone(engine.claude_channel_endpoint(config, self.reference))
        self.assertIsNone(engine.claude_channel_endpoint(
            dict(harness='codex', claude_channel_dir=str(directory)), self.reference))


    def test_interrupted_write_is_uncertain_and_never_resent(self):
        # A peer that closes mid-write may already have received part of the
        # line: the failure is uncertain, never a retryable pre-send refusal.
        with self.assertRaises(channel.ChannelError) as caught:
            self.write('interrupt')
        self.assertNotIsInstance(caught.exception, channel.ChannelUnavailable)
        self.assertEqual(self.bridge.connections, 1)

    def test_malformed_replies_are_uncertain_not_crashes(self):
        for mode in ('garbage', 'array', 'nostatus'):
            with self.subTest(mode=mode):
                with self.assertRaises(channel.ChannelError) as caught:
                    self.write(mode)
                self.assertNotIsInstance(caught.exception, channel.ChannelUnavailable)


def build_cairn():
    binary = Path('/tmp') / f'cairn-channel-test-{os.getuid()}' / 'cairn'
    sources = list((ROOT / 'internal/claudechannel').glob('*.go')) + [ROOT / 'go.mod']
    if not binary.exists() or any(source.stat().st_mtime > binary.stat().st_mtime for source in sources):
        if shutil.which('go') is None:
            return None
        binary.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, GOFLAGS=(os.environ.get('GOFLAGS', '') + ' -buildvcs=false').strip())
        subprocess.run(['go', 'build', '-o', str(binary), './cmd/cairn'], cwd=ROOT, env=env,
                       check=True, capture_output=True, timeout=300)
    return binary


class GoGeneratedRegistry(unittest.TestCase):
    def test_actual_go_registry_selects_for_a_real_python_reference(self):
        binary = build_cairn()
        if binary is None:
            self.skipTest('go toolchain unavailable')
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            bridge = subprocess.Popen([str(binary), 'claude-channel', '--directory', str(directory)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                bridge.stdin.write(json.dumps(dict(jsonrpc='2.0', id=1, method='initialize', params=dict(
                    protocolVersion='2025-11-25', capabilities={},
                    clientInfo=dict(name='python-test', version='1')))) + '\n')
                bridge.stdin.flush()
                deadline = time.time() + 10
                while True:
                    line = bridge.stdout.readline()
                    assert line or time.time() > deadline, 'no initialize response'
                    if json.loads(line).get('id') == 1:
                        break
                bridge.stdin.write(json.dumps(dict(jsonrpc='2.0', method='notifications/initialized')) + '\n')
                bridge.stdin.flush()
                registry = directory / f"{os.getpid()}.json"
                while not registry.exists() and time.time() < deadline:
                    time.sleep(0.05)
                record = json.loads(registry.read_text())
                # The engine's real five-field process reference must select
                # the Go-generated registry through the stable projection.
                selected = engine.claude_channel_endpoint(
                    dict(harness='claude', claude_channel_dir=str(directory)),
                    engine.process_reference(os.getpid()))
                self.assertEqual(selected, dict(socket=record['socket'],
                    process=record['process'], parent=record['parent']))
                # The full cross-language write round trip through the Go socket.
                channel.write(record['socket'], record['process'], record['parent'],
                              'cross-language probe', META)
            finally:
                bridge.stdin.close()
                bridge.wait(timeout=10)
            self.assertFalse(registry.exists(), 'registry survived bridge exit')


if __name__ == '__main__':
    unittest.main()
