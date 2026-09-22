"""Model-free checks in the same verified namespace as any following probe."""
import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import socket
import struct
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
import urllib.request

import isolate
from verify import verify_current, credentials

HERE = Path(__file__).resolve().parent


class SetupRefusalTests(unittest.TestCase):
    def test_setup_error_never_falls_back_to_workload(self):
        marker = Path(os.environ['TMPDIR']) / 'must-not-run'
        command = ['/bin/sh', '-c', 'touch "$1"', 'fixture', str(marker)]
        with patch.object(isolate.subprocess, 'run', return_value=subprocess.CompletedProcess([], 77)) as run:
            self.assertEqual(isolate.launch(command), 77)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][0], '/usr/bin/sudo')
        self.assertFalse(marker.exists())

    def test_unavailable_setup_never_falls_back(self):
        with patch.object(isolate.subprocess, 'run', side_effect=FileNotFoundError('fixture missing sudo')) as run:
            with self.assertRaises(FileNotFoundError):
                isolate.launch(['/bin/false'])
        self.assertEqual(run.call_count, 1)

    def test_every_privilege_dimension_fails_closed(self):
        metadata = dict(uid=1000, gid=1000, groups=[10, 1000])
        valid = dict(Uid='1000 1000 1000 1000', Gid='1000 1000 1000 1000', Groups='10 1000',
                     CapInh='0', CapPrm='0', CapEff='0', CapBnd='0', CapAmb='0', NoNewPrivs='1')
        credentials(valid, metadata)
        for key, value in [('Uid', '0 1000 1000 1000'), ('Gid', '0 1000 1000 1000'), ('Groups', '0 10 1000'),
                           ('NoNewPrivs', '0'), *[(k, '1') for k in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')]]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                credentials(dict(valid, **{key: value}), metadata)


def checks():
    evidence = verify_current()
    root = Path(os.environ['HARNESS_FIXTURE_ROOT'])
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    libc.connect.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    libc.connect.restype = ctypes.c_int
    architecture = platform.machine()
    # These are kernel syscall numbers, not calls through libc.connect's PLT.
    numbers = {'x86_64': (42, 44), 'aarch64': (203, 206)}
    if architecture not in numbers:
        raise RuntimeError('direct-syscall negative checks unsupported on this architecture')
    connect_number, sendto_number = numbers[architecture]
    address = ctypes.create_string_buffer(struct.pack('=H', socket.AF_INET) + struct.pack('!H', 443)
                                           + socket.inet_aton('1.1.1.1') + b'\0' * 8)
    refused = []
    for mode in ('libc-connect', 'kernel-connect', 'kernel-sendto'):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if mode.endswith('sendto') else socket.SOCK_STREAM) as client:
            ctypes.set_errno(0)
            if mode == 'libc-connect':
                result = libc.connect(client.fileno(), address, 16)
            elif mode == 'kernel-connect':
                result = libc.syscall(ctypes.c_long(connect_number), ctypes.c_long(client.fileno()),
                                      ctypes.byref(address), ctypes.c_long(16))
            else:
                data = ctypes.create_string_buffer(b'fixture')
                result = libc.syscall(ctypes.c_long(sendto_number), ctypes.c_long(client.fileno()),
                                      ctypes.byref(data), ctypes.c_long(7), ctypes.c_long(0),
                                      ctypes.byref(address), ctypes.c_long(16))
            error = ctypes.get_errno()
            if result != -1 or error not in (errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN):
                raise RuntimeError(f'{mode} did not fail with a kernel routing refusal: {result}/{error}')
            refused.append(dict(operation=mode, errno=error))
    # No capabilities and NNP must prevent re-entering the original namespace.
    metadata = json.loads((root / 'launch.json').read_text())
    with open(f"/proc/{metadata['parent_pid']}/ns/net", 'rb') as host:
        ctypes.set_errno(0)
        if libc.setns(host.fileno(), 0x40000000) != -1 or ctypes.get_errno() != errno.EPERM:
            raise RuntimeError('could re-enter host namespace')
    ctypes.set_errno(0)
    if libc.setuid(0) != -1 or ctypes.get_errno() != errno.EPERM:
        raise RuntimeError('could regain root')
    with (root / 'mock.stderr').open('w') as log:
        mock = subprocess.Popen(['/usr/bin/python3', str(HERE / 'mock_provider.py'), '18131', '0'], stderr=log)
        try:
            url = 'http://127.0.0.1:18131/v1/chat/completions'
            for _ in range(50):
                if mock.poll() is not None:
                    raise RuntimeError('loopback mock exited before readiness')
                try:
                    with urllib.request.urlopen('http://127.0.0.1:18131/v1/models', timeout=1) as reply:
                        if json.load(reply)['data'][0]['id'] == 'mock-model':
                            break
                except OSError:
                    time.sleep(.02)
            else:
                raise RuntimeError('loopback mock never became ready')
            request = urllib.request.Request(url, data=json.dumps(dict(model='mock-model', stream=True,
                messages=[dict(role='user', content='isolation-only')])).encode(), headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=3) as reply:
                body = reply.read().decode()
            if 'MOCK-CANARY-REPLY :: isolation-only' not in body or 'data: [DONE]' not in body:
                raise RuntimeError('loopback mock response was missing its expected canary or completion')
        finally:
            mock.terminate()
            try:
                mock.wait(timeout=3)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait()
    result = unittest.TextTestRunner().run(unittest.defaultTestLoader.loadTestsFromTestCase(SetupRefusalTests))
    if not result.wasSuccessful():
        raise RuntimeError('setup/refusal tests failed')
    evidence.update(external_refusals=refused, host_setns='EPERM', root_setuid='EPERM',
                    loopback_mock='canary-completed', model_calls=0, setup_checks=result.testsRun)
    (root / 'isolation-checks.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps(dict(isolation='verified', root=str(root), **evidence)), flush=True)
    return evidence


if __name__ == '__main__':
    checks()
