"""Real synthetic preparation files and read buffers; no native process or SDK."""
import codecs
import json
import os
import sys
import unittest
from unittest.mock import patch

import test_trial_native_bootstrap as fixtures
import trial_bootstrap_snapshot as snapshot
import trial_native_bootstrap as bootstrap


class PreparationTextTests(unittest.TestCase):
    setUp = fixtures.PreparationTests.setUp
    rewrite = fixtures.PreparationTests.rewrite
    repin = fixtures.PreparationTests.repin

    def prepare(self, sdk):
        self.document['schema'] = bootstrap.SDK_SCHEMA if sdk else bootstrap.SCHEMA
        owner = (bootstrap.prepare_sdk_review if sdk else bootstrap.prepare_review)(
            self.document, self.output + ('-sdk' if sdk else '-native'))
        self.addCleanup(owner.close)
        return owner

    def test_preparation_does_not_retain_collected_raw_chunks(self):
        # Whitespace is accepted JSON and must still fit the existing file limit.
        settings = (self.source / 'settings.json').read_bytes()
        self.rewrite('settings', settings + b' ' * (65536 - len(settings)))
        actual_read = os.read
        retained = []

        def short_read(fd, count):
            frame = sys._getframe(1)
            while frame is not None:
                if frame.f_code.co_filename == snapshot.__file__:
                    # Observe actual backings in the acquisition frame, not a
                    # decoder mock or a list retained by the test itself.
                    raw = {}
                    for value in frame.f_locals.values():
                        if isinstance(value, bytes):
                            raw[id(value)] = len(value)
                        elif isinstance(value, list):
                            for part in value:
                                if isinstance(part, bytes):
                                    raw[id(part)] = len(part)
                    retained.append(sum(raw.values()))
                frame = frame.f_back
            return actual_read(fd, min(count, 4093))

        with patch.object(snapshot.os, 'read', new=short_read):
            for sdk in (False, True):
                owner = self.prepare(sdk)
                self.assertEqual(owner.review_bindings()['snapshots']['settings.json']['bytes'], 65536)
                if sdk:
                    session = owner.consume_for_session()
                    self.addCleanup(session.close)
                    session.held_source_bindings()
        self.assertGreater(len(retained), 30)
        self.assertLessEqual(max(retained), 8192)

    def test_split_utf8_and_decoder_backings_through_preparation_and_custody(self):
        self.rewrite('prompt', 'Read café, € and 🧭.'.encode())
        instruction = 'Inspect café, € and 🧭.'
        self.rewrite('instruction', instruction.encode())
        self.document['mcp_binding']['task'] = '🧭€é'
        mcp = json.dumps(dict(mcpServers=dict(cairn=dict(type='stdio',
            command='/native/cairn', args=bootstrap.mcp_arguments(
                self.document['mcp_binding'])))), ensure_ascii=False).encode()
        # A real short read ends after the FIRST byte of this four-byte scalar.
        mcp = b' ' * (8190 - mcp.index('🧭'.encode())) + mcp
        mcp += b' ' * (65536 - len(mcp))
        self.rewrite('mcp', mcp)
        actual_read = os.read
        observations = []
        decode = codecs.utf_8_decode

        def observe_decode(data, errors='strict', final=False):
            observations.append(len(data))
            return decode(data, errors, final)

        def short_read(fd, count):
            # One-byte prompt/instruction reads exercise all UTF-8 carry widths.
            name = os.readlink('/proc/self/fd/' + str(fd)).rsplit('/', 1)[-1]
            return actual_read(fd, min(count, 1 if name in ('prompt.txt', 'instruction.txt') else 8191))

        with patch.object(snapshot.os, 'read', new=short_read), \
             patch.object(snapshot.codecs, 'utf_8_decode', new=observe_decode):
            for sdk in (False, True):
                owner = self.prepare(sdk)
                self.assertEqual(owner.launch.argv[-1], instruction)
                owner.review_bindings()
                if not sdk:
                    legacy = owner._snapshot.read('mcp.json')
                    self.assertIs(type(legacy), bytes)
                    self.assertEqual(legacy, mcp)
                if sdk:
                    self.assertEqual(owner.launch.control.pristine_binding()['instruction_bytes'],
                                     len(instruction.encode()))
                    session = owner.consume_for_session()
                    self.addCleanup(session.close)
                    session.held_source_bindings()
        # Observe the decoder's actual inputs: borrowed blocks stay bounded and
        # split scalars are decoded through the small carry path.
        self.assertTrue(observations)
        self.assertLessEqual(max(observations), 8191)

    def test_invalid_utf8_and_json_refuse_after_short_reads_without_fd_leaks(self):
        actual_read = os.read
        cases = (
            ('prompt', b'bad\xfftext', 'bootstrap_text'),
            ('instruction', b'truncated\xf0\x9f', 'bootstrap_text'),
            ('settings', b'{"hooks":{}}\xe2\x82', 'bootstrap_json'),
            ('mcp', b'{"mcpServers":{},"mcpServers":{}}', 'bootstrap_json'),
            ('settings', b'{"permissions":{"a":1,"a":2}}', 'bootstrap_json'),
            ('settings', b'{"hooks":NaN}', 'bootstrap_json'),
            ('mcp', b'{} trailing', 'bootstrap_json'),
        )
        original = {role: (self.source / name).read_bytes()
                    for role, (name, _, _) in bootstrap.SOURCE_ROLES.items()}
        for index, (role, body, code) in enumerate(cases):
            for sdk in (False, True):
                with self.subTest(role=role, body=body, sdk=sdk):
                    self.output = str(self.root / ('invalid-' + str(index)))
                    self.rewrite(role, body)
                    baseline = set(os.listdir('/proc/self/fd'))
                    with patch.object(snapshot.os, 'read', new=lambda fd, count:
                                      actual_read(fd, min(count, 2))):
                        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^' + code + '$'):
                            self.prepare(sdk)
                    self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
                    with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^preparation_consumed$'):
                        self.prepare(sdk)
                    self.rewrite(role, original[role])

    def test_mid_decode_file_drift_and_interruption_close_preparation(self):
        settings = (self.source / 'settings.json').read_bytes()
        self.rewrite('settings', settings + b' ' * (65536 - len(settings)))
        actual_read = os.read
        for sdk in (False, True):
            for interrupt in (False, True):
                with self.subTest(sdk=sdk, interrupt=interrupt):
                    self.output = str(self.root / ('drift-' + str(sdk) + '-' + str(interrupt)))
                    touched = []
                    primary = KeyboardInterrupt('synthetic read interruption')

                    def read(fd, count):
                        body = actual_read(fd, min(count, 4093))
                        frame = sys._getframe(1)
                        decoding = False
                        while frame is not None:
                            if frame.f_code.co_filename == snapshot.__file__ and frame.f_locals.get('text') is True:
                                decoding = True
                            frame = frame.f_back
                        path = os.readlink('/proc/self/fd/' + str(fd))
                        if decoding and path.endswith('/settings.json') and body and not touched:
                            touched.append(True)
                            if interrupt:
                                raise primary
                            # Mutate the actual held snapshot inode after its
                            # initial identity check, while decoding short reads.
                            os.chmod(path, 0o600)
                            with open(path, 'r+b') as changed:
                                changed.seek(-1, os.SEEK_END)
                                changed.write(b'\n')
                            os.chmod(path, 0o400)
                        return body

                    baseline = set(os.listdir('/proc/self/fd'))
                    expected = KeyboardInterrupt if interrupt else bootstrap.PreparationRefusal
                    with patch.object(snapshot.os, 'read', new=read):
                        with self.assertRaises(expected) as caught:
                            self.prepare(sdk)
                    self.assertEqual(touched, [True])
                    if interrupt:
                        self.assertIs(caught.exception, primary)
                    else:
                        self.assertEqual(str(caught.exception), 'snapshot_changed')
                    self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_invalid_utf8_does_not_skip_final_snapshot_validation(self):
        self.rewrite('settings', b'\xff' + b' ' * 65535)
        actual_read = os.read
        touched = []

        def read(fd, count):
            body = actual_read(fd, min(count, 4093))
            frame = sys._getframe(1)
            decoding = frame.f_locals.get('text') is True
            path = os.readlink('/proc/self/fd/' + str(fd))
            if decoding and path.endswith('/settings.json') and body and not touched:
                touched.append(True)
                os.chmod(path, 0o600)
                with open(path, 'r+b') as changed:
                    changed.seek(-1, os.SEEK_END)
                    changed.write(b'\n')
                os.chmod(path, 0o400)
            return body

        for sdk in (False, True):
            touched.clear()
            baseline = set(os.listdir('/proc/self/fd'))
            with patch.object(snapshot.os, 'read', new=read):
                with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^snapshot_changed$'):
                    self.prepare(sdk)
            self.assertEqual(touched, [True])
            self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_original_config_size_limit_still_refuses(self):
        settings = (self.source / 'settings.json').read_bytes()
        self.rewrite('settings', settings + b' ' * (65537 - len(settings)))
        for sdk in (False, True):
            baseline = set(os.listdir('/proc/self/fd'))
            with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^source_file_policy$'):
                self.prepare(sdk)
            self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)


if __name__ == '__main__':
    unittest.main()
