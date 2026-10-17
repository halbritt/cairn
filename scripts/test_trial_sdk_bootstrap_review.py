"""Real disposable source files; no SDK/native binary or account is acquired."""
import copy
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import trial_bootstrap_snapshot as custody
import trial_native_bootstrap as bootstrap
from trial_coding_launch import SDKLaunch, compile_launch, compile_sdk_launch
from trial_paired_request import (IMPLEMENTATION_SETTINGS_SHA, ORIGINAL_LIMITS,
                                 canonical_sha, mcp_arguments)


class SDKReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'sources'
        self.source.mkdir(mode=0o700)
        self.output = str(self.root / 'prepared')
        self.prompt, self.instruction = 'Selected fixture prompt.', 'Inspect source: café.'
        binding = dict(request_id='00000000-0000-4000-8000-000000000001',
                       occurrence_id='00000000-0000-4000-8000-000000000002',
                       session_id='00000000-0000-4000-8000-000000000003',
                       case_id='T1', arm='CAND', workspace_sha256='a'*64,
                       request_sha256='b'*64, source_set_sha256='c'*64)
        mcp = dict(socket='/unavailable-review/memory.sock',
                   token_file='/unavailable-review/no-token', repo='/unavailable-review/repo',
                   task='fixture-task', run='fixture-run')
        coding = compile_launch(session_id=binding['session_id'], model='fixture-model',
                                effort='high', prompt=self.prompt,
                                memory_instruction=self.instruction)
        self.user = coding.input_message
        contents = {
            'claude': b'inert selected CLI fixture; never executed',
            'cairn': b'inert selected MCP fixture; never executed',
            'trial_task_permissions.py': (Path(__file__).parent / 'trial_task_permissions.py').read_bytes(),
            'prompt.txt': self.prompt.encode(), 'instruction.txt': self.instruction.encode(),
            'settings.json': json.dumps(dict(permissions=json.loads(coding.permission_json),
                hooks={}, autoMemoryEnabled=False, claudeMdExcludes=['**'])).encode(),
            'mcp.json': json.dumps(dict(mcpServers=dict(cairn=dict(type='stdio',
                command='/native/cairn', args=mcp_arguments(mcp))))).encode(),
        }
        self.document = dict(schema='cairn.sdk-native-bootstrap-review/1', binding=binding,
            launch=dict(model='fixture-model', effort='high'), sources={}, mcp_binding=mcp,
            limits=dict(ORIGINAL_LIMITS), permission_sha256=IMPLEMENTATION_SETTINGS_SHA,
            hook_interface=None)
        for role, (name, _, executable) in bootstrap.SOURCE_ROLES.items():
            path = self.source / name
            path.write_bytes(contents[name])
            path.chmod(0o500 if executable else 0o400)
            self.document['sources'][role] = dict(path=str(path),
                sha256=hashlib.sha256(contents[name]).hexdigest())
        self.document['binding']['source_set_sha256'] = canonical_sha(
            {role: ref['sha256'] for role, ref in self.document['sources'].items()})

    def prepare(self, output=None):
        owner = bootstrap.prepare_sdk_review(self.document, output or self.output)
        self.addCleanup(owner.close)
        return owner

    def test_compiles_control_owned_review_without_duplicate_user_file(self):
        baseline = set(os.listdir('/proc/self/fd'))
        with self.prepare() as owner:
            self.assertIs(type(owner.launch), SDKLaunch)
            review = owner.review_bindings()
            first = owner.launch.control.pristine_binding()
            self.assertEqual(owner.review_bindings(), review)
            self.assertEqual(owner.launch.control.pristine_binding(), first)
            self.assertEqual(review['schema'], self.document['schema'])
            self.assertEqual(review['status'], 'source_prepared_for_review')
            self.assertFalse(review['complete_bootstrap'])
            self.assertFalse(review['runtime_attested'])
            self.assertEqual(review['input_mode'], 'controller_owned')
            self.assertEqual(review['user_message_bytes'], len(self.user))
            self.assertEqual(review['user_message_sha256'], hashlib.sha256(self.user).hexdigest())
            self.assertEqual(first['user_message_sha256'], review['user_message_sha256'])
            initialize = owner.launch.control.pending_write()
            self.assertEqual(first['initialize_sha256'], hashlib.sha256(initialize).hexdigest())
            self.assertEqual(review['control'], first)
            self.assertEqual(review['guaranteed_context_bytes'], owner.launch.guaranteed_context_bytes)
            self.assertEqual(set(review['snapshots']),
                {entry[0] for entry in bootstrap.SOURCE_ROLES.values()} | {'sdk-control.json'})
            self.assertEqual(json.loads(Path(self.output, 'sdk-control.json').read_bytes()), first)
            self.assertFalse(Path(self.output, 'input.jsonl').exists())
            self.assertFalse(hasattr(owner.launch, 'input_message'))
            with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^hook_interface_unavailable$'):
                owner.require_complete()
        self.assertTrue(owner.launch.control.ledger()['closed'])
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)

    def test_consumed_or_closed_controller_refuses_a_new_review(self):
        for action in ('partial_initialize', 'closed'):
            with self.subTest(action=action):
                owner = self.prepare(str(self.root / action))
                if action == 'closed':
                    owner.launch.control.close()
                else:
                    # A pure caller-reported count, not an actual native write.
                    owner.launch.control.written(1)
                with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
                    owner.review_bindings()
                self.assertTrue(owner.launch.control.ledger()['closed'])
                self.assertTrue(owner._snapshot.closed)

    def test_hidden_user_drift_is_not_hidden_by_unchanged_public_ledger(self):
        owner = self.prepare()
        control = owner.launch.control
        initial_ledger, initial_ids = control.ledger(), control.bindings
        control._user = b'x' * owner.launch.user_message_bytes
        self.assertEqual(control.ledger(), initial_ledger)
        self.assertEqual(control.bindings, initial_ids)
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            owner.review_bindings()
        self.assertTrue(control.ledger()['closed'])

    def test_replaced_launch_control_closes_original_not_foreign_controller(self):
        owner = self.prepare()
        original = owner.launch.control
        foreign = compile_sdk_launch(session_id=self.document['binding']['session_id'],
            model='fixture-model', effort='high', prompt=self.prompt,
            memory_instruction=self.instruction).control
        self.addCleanup(foreign.close)
        owner._launch = dataclasses.replace(owner.launch, control=foreign)
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            owner.review_bindings()
        self.assertTrue(original.ledger()['closed'])
        self.assertFalse(foreign.ledger()['closed'])
        self.assertTrue(owner._snapshot.closed)

    def test_changed_compiled_user_binding_or_held_source_refuses_review(self):
        for changed in ('compiled', 'source'):
            with self.subTest(changed=changed):
                owner = self.prepare(str(self.root / changed))
                original = owner.launch.control
                if changed == 'compiled':
                    owner._launch = dataclasses.replace(owner.launch, user_message_sha256='f'*64)
                else:
                    path = self.source / 'prompt.txt'
                    path.chmod(0o600)
                    path.write_bytes(b'Changed fixture source')
                with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
                    owner.review_bindings()
                self.assertTrue(original.ledger()['closed'])

    def test_legacy_and_sdk_schemas_cannot_select_each_other(self):
        original = copy.deepcopy(self.document)
        cases = ((bootstrap.prepare_review, self.document),
                 (bootstrap.prepare_sdk_review, {**original, 'schema': bootstrap.SCHEMA}))
        for index, (prepare, document) in enumerate(cases):
            with self.subTest(index=index):
                output = str(self.root / ('wrong-schema-' + str(index)))
                with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^bootstrap_schema$'):
                    prepare(document, output)
                self.assertEqual(list(Path(output).iterdir()), [])

    def test_sdk_declaration_write_failure_preserves_consumption_and_closes_fds(self):
        baseline = set(os.listdir('/proc/self/fd'))
        actual = os.write
        def refuse_metadata(fd, body):
            if b'initialize_sha256' in bytes(body):
                raise OSError('constructed private storage fault')
            return actual(fd, body)
        with patch.object(custody.os, 'write', side_effect=refuse_metadata):
            with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^preparation_io_or_shape_unknown$'):
                self.prepare()
        self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
        self.assertFalse(Path(self.output, 'input.jsonl').exists())
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^preparation_consumed$'):
            self.prepare()

    def test_early_acquisition_failure_keeps_constructor_cleanup_uncertainty(self):
        for index, primary in enumerate((OSError('private acquisition fault'),
                                         KeyboardInterrupt('private interruption'))):
            with self.subTest(primary=type(primary).__name__):
                output = str(self.root / ('early-failure-' + str(index)))
                baseline = set(os.listdir('/proc/self/fd'))
                actual_open, actual_close, owned = os.open, os.close, []
                def acquisition(path, *args, **kwargs):
                    if path == Path(output).name:
                        raise primary
                    fd = actual_open(path, *args, **kwargs)
                    owned.append(fd)
                    return fd
                def uncertain_close(fd):
                    actual_close(fd)
                    raise OSError('private cleanup uncertainty')
                expected = bootstrap.PreparationRefusal if isinstance(primary, OSError) else KeyboardInterrupt
                with patch.object(custody.os, 'open', side_effect=acquisition), \
                        patch.object(custody.os, 'close', side_effect=uncertain_close):
                    with self.assertRaises(expected) as caught:
                        self.prepare(output)
                if isinstance(primary, OSError):
                    self.assertEqual(str(caught.exception), 'preparation_io_or_shape_unknown')
                else:
                    self.assertIs(caught.exception, primary)
                self.assertEqual(caught.exception.cleanup_faults, ('descriptor_cleanup_unknown',))
                self.assertTrue(Path(output).is_dir())
                self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
                for fd in owned:
                    with self.assertRaises(OSError):
                        os.fstat(fd)

    def test_snapshot_close_uncertainty_does_not_skip_control_close_or_retry(self):
        owner = self.prepare()
        control = owner.launch.control
        target = owner._snapshot.files['prompt.txt'][0].fd
        actual, attempts = os.close, []
        def uncertain(fd):
            attempts.append(fd)
            actual(fd)
            if fd == target:
                raise OSError('constructed private close fault')
        with patch.object(custody.os, 'close', side_effect=uncertain):
            self.assertEqual(owner.close(), ('descriptor_cleanup_unknown',))
            self.assertEqual(owner.close(), ('descriptor_cleanup_unknown',))
        self.assertEqual(attempts.count(target), 1)
        self.assertTrue(control.ledger()['closed'])
        self.assertTrue(all(lease.fd is None for lease in owner._snapshot.tree.leases))

    def test_custom_primary_cleanup_metadata_cannot_skip_owned_cleanup(self):
        class GetterFailure(BaseException):
            @property
            def cleanup_faults(self):
                raise RuntimeError('private diagnostic getter fault')
        class MalformedAnnotation(BaseException):
            cleanup_faults = 'not a fixed cleanup tuple'
        for index, primary in enumerate((GetterFailure(), MalformedAnnotation())):
            with self.subTest(primary=type(primary).__name__):
                baseline = set(os.listdir('/proc/self/fd'))
                actual_open, actual_write, actual_close = os.open, os.write, os.close
                owned = {}
                def acquisition(*args, **kwargs):
                    fd = actual_open(*args, **kwargs)
                    observed = os.fstat(fd)
                    owned[fd] = observed.st_dev, observed.st_ino, observed.st_mode
                    return fd
                def refuse_metadata(fd, body):
                    if b'initialize_sha256' in bytes(body):
                        raise primary
                    return actual_write(fd, body)
                try:
                    with patch.object(custody.os, 'open', side_effect=acquisition), \
                            patch.object(custody.os, 'write', side_effect=refuse_metadata):
                        with self.assertRaises(BaseException) as caught:
                            self.prepare(str(self.root / ('custom-primary-' + str(index))))
                    self.assertIs(caught.exception, primary)
                    self.assertEqual(set(os.listdir('/proc/self/fd')), baseline)
                    if isinstance(primary, MalformedAnnotation):
                        self.assertEqual(primary.cleanup_faults, ('cleanup_annotation_unknown',))
                finally:
                    # Test-owned rescue of real tracked descriptors on a RED;
                    # never close a reused number with a different identity.
                    for fd, identity in owned.items():
                        try:
                            observed = os.fstat(fd)
                        except OSError:
                            continue
                        if (observed.st_dev, observed.st_ino, observed.st_mode) == identity:
                            actual_close(fd)

    def test_immutable_body_failure_survives_uncertain_snapshot_cleanup(self):
        @dataclasses.dataclass(frozen=True)
        class Primary(Exception):
            reason: str
        owner = self.prepare()
        target = owner._snapshot.files['prompt.txt'][0].fd
        actual = os.close
        def uncertain(fd):
            actual(fd)
            if fd == target:
                raise OSError('constructed private close fault')
        primary = Primary('constructed immutable primary')
        with patch.object(custody.os, 'close', side_effect=uncertain):
            with self.assertRaises(Primary) as caught:
                with owner:
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(owner.close(), ('descriptor_cleanup_unknown',))
        self.assertTrue(owner.launch.control.ledger()['closed'])


if __name__ == '__main__':
    unittest.main()
