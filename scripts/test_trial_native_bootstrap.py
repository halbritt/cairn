"""Disposable files only; constructed source declarations are not authorization."""
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
from trial_coding_launch import compile_launch
from trial_paired_request import (IMPLEMENTATION_SETTINGS_SHA, ORIGINAL_LIMITS,
                                 Refusal, canonical_sha, mcp_arguments)


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'sources'
        self.source.mkdir(mode=0o700)
        self.output = str(self.root / 'prepared')
        binding = dict(request_id='00000000-0000-4000-8000-000000000001',
                       occurrence_id='00000000-0000-4000-8000-000000000002',
                       session_id='00000000-0000-4000-8000-000000000003',
                       case_id='T1', arm='CAND', workspace_sha256='a'*64,
                       request_sha256='b'*64, source_set_sha256='c'*64)
        mcp_binding = dict(socket='/fixture-runtime/memory.sock',
                           token_file='/fixture-runtime/not-a-real-token',
                           repo='/fixture-runtime/repository', task='fixture-task',
                           run='fixture-run')
        launch = compile_launch(session_id=binding['session_id'], model='fixture-model',
                                effort='high', prompt='Fixed fixture prompt.',
                                memory_instruction='Fixed instruction: café.')
        contents = {
            'claude': b'inert CLI fixture bytes; never executed',
            'cairn': b'inert MCP fixture bytes; never executed',
            'trial_task_permissions.py': (Path(__file__).parent / 'trial_task_permissions.py').read_bytes(),
            'prompt.txt': b'Fixed fixture prompt.',
            'instruction.txt': 'Fixed instruction: café.'.encode(),
            'settings.json': json.dumps(dict(permissions=json.loads(launch.permission_json),
                                             hooks={}, autoMemoryEnabled=False,
                                             claudeMdExcludes=['**'])).encode(),
            'mcp.json': json.dumps(dict(mcpServers=dict(cairn=dict(type='stdio',
                command='/native/cairn', args=mcp_arguments(mcp_binding))))).encode(),
        }
        self.document = dict(schema=bootstrap.SCHEMA, binding=binding,
                             launch=dict(model='fixture-model', effort='high'),
                             sources={}, mcp_binding=mcp_binding,
                             limits=dict(ORIGINAL_LIMITS),
                             permission_sha256=IMPLEMENTATION_SETTINGS_SHA,
                             hook_interface=None)
        for role, (name, _, executable) in bootstrap.SOURCE_ROLES.items():
            target = self.source / name
            target.write_bytes(contents[name])
            target.chmod(0o500 if executable else 0o400)
            self.document['sources'][role] = dict(path=str(target),
                                                 sha256=hashlib.sha256(contents[name]).hexdigest())
        self.repin()

    def repin(self, role=None):
        if role:
            ref = self.document['sources'][role]
            ref['sha256'] = hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest()
        self.document['binding']['source_set_sha256'] = canonical_sha(
            {role: ref['sha256'] for role, ref in self.document['sources'].items()})

    def rewrite(self, role, content):
        path = Path(self.document['sources'][role]['path'])
        path.chmod(0o600)
        path.write_bytes(content)
        path.chmod(0o400)
        self.repin(role)

    def prepare(self):
        owner = bootstrap.prepare_review(self.document, self.output)
        self.addCleanup(owner.close)
        return owner

    def refused(self, code):
        with self.assertRaises(bootstrap.PreparationRefusal) as caught:
            self.prepare()
        self.assertEqual(str(caught.exception), code)
        self.assertTrue(Path(self.output).is_dir())
        with self.assertRaises(bootstrap.PreparationRefusal) as retry:
            bootstrap.prepare_review(self.document, self.output)
        self.assertEqual(str(retry.exception), 'preparation_consumed')

    def test_compiles_exact_single_message_and_rechecks_held_review(self):
        owner = self.prepare()
        review = owner.review_bindings()
        self.assertEqual(review['status'], 'source_prepared_for_review')
        self.assertFalse(review['complete_bootstrap'])
        self.assertFalse(review['runtime_attested'])
        self.assertIn('hook_interface_unavailable', review['missing_gates'])
        self.assertEqual(review['binding'], self.document['binding'])
        self.assertEqual(review['permissions_sha256'], IMPLEMENTATION_SETTINGS_SHA)
        self.assertEqual(len(owner.launch.input_message.splitlines()), 1)
        self.assertEqual(json.loads(owner.launch.input_message)['message']['role'], 'user')
        self.assertEqual(review['input_sha256'], hashlib.sha256(owner.launch.input_message).hexdigest())
        self.assertEqual(review['input_bytes'] + len('Fixed instruction: café.'.encode())
                         + 512 + review['hook_context_bytes'], 9500)
        self.assertEqual(owner.launch.argv[owner.launch.argv.index('--max-turns') + 1], '8')
        self.assertEqual(set(review['snapshots']), {entry[0] for entry in bootstrap.SOURCE_ROLES.values()} | {'input.jsonl'})
        for name, observed in review['snapshots'].items():
            self.assertEqual(observed['nlink'], 1)
            self.assertEqual(observed['uid'], os.geteuid())
            self.assertEqual(Path(self.output, name).stat().st_mode & 0o777,
                             0o500 if name in ('claude', 'cairn') else 0o400)
            if name == 'input.jsonl':
                self.assertIsNone(observed['source_identity'])
            else:
                origin = (self.source / name).stat()
                self.assertEqual(observed['source_identity']['ino'], origin.st_ino)
                self.assertEqual(observed['source_identity']['bytes'], origin.st_size)
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^hook_interface_unavailable$'):
            owner.require_complete()

    def test_never_opens_mcp_socket_token_repository_or_executes_cli(self):
        opened = []
        actual = os.open
        def observe(path, *args, **kwargs):
            opened.append(str(path))
            return actual(path, *args, **kwargs)
        with patch.object(custody.os, 'open', side_effect=observe):
            self.prepare()
        self.assertFalse(any('/fixture-runtime' in path for path in opened))

    def test_leaf_symlink_refuses_without_reading_target(self):
        path = self.source / 'claude'
        path.unlink()
        path.symlink_to(self.source / 'cairn')
        self.refused('preparation_io_or_shape_unknown')

    def test_symlink_ancestor_refuses(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.source, target_is_directory=True)
        self.document['sources']['cli']['path'] = str(alias / 'claude')
        self.refused('preparation_io_or_shape_unknown')

    def test_replaced_ancestor_is_not_reopened_for_review(self):
        owner = self.prepare()
        self.source.rename(self.root / 'original')
        self.source.mkdir()
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            owner.review_bindings()
        self.assertTrue(owner._snapshot.closed)

    def test_replaced_snapshot_directory_is_not_followed(self):
        owner = self.prepare()
        Path(self.output).rename(self.root / 'original-prepared')
        Path(self.output).mkdir()
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            owner.review_bindings()

    def test_hardlink_source_refuses(self):
        os.link(self.source / 'claude', self.root / 'another-link')
        self.refused('source_file_policy')

    def test_source_group_write_or_wrong_executable_policy_refuses(self):
        (self.source / 'claude').chmod(0o520)
        self.refused('source_file_policy')

    def test_fifo_source_refuses_without_blocking(self):
        path = self.source / 'claude'
        path.unlink()
        os.mkfifo(path, 0o600)
        self.refused('source_file_policy')

    def test_source_changes_after_copy_refuses_held_review(self):
        owner = self.prepare()
        path = self.source / 'prompt.txt'
        path.chmod(0o600)
        path.write_bytes(b'Changed prompt content')
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            owner.review_bindings()

    def test_snapshot_same_length_drift_refuses(self):
        owner = self.prepare()
        path = Path(self.output, 'prompt.txt')
        path.chmod(0o600)
        original = path.read_bytes()
        path.write_bytes(b'X' + original[1:])
        path.chmod(0o400)
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            owner.review_bindings()

    def test_reused_held_fd_never_closes_unrelated_file(self):
        owner = self.prepare()
        lease = owner._snapshot.files['prompt.txt'][0]
        lost = lease.fd
        os.close(lost)
        unrelated = self.root / 'unrelated'
        unrelated.write_bytes(b'foreign retained fd')
        foreign = os.open(unrelated, os.O_RDONLY)
        self.assertEqual(foreign, lost)
        try:
            with self.assertRaises(bootstrap.PreparationRefusal) as caught:
                owner.review_bindings()
            self.assertEqual(str(caught.exception), 'review_custody_unknown')
            self.assertEqual(caught.exception.cleanup_faults, ('descriptor_cleanup_unknown',))
            self.assertEqual(os.read(foreign, 100), b'foreign retained fd')
            self.assertEqual(owner.close(), ('descriptor_cleanup_unknown',))
            os.fstat(foreign)
        finally:
            os.close(foreign)

    def test_close_fault_attempts_all_others_and_does_not_retry(self):
        owner = self.prepare()
        target = owner._snapshot.files['prompt.txt'][0].fd
        actual = os.close
        closed = []
        def fault(fd):
            closed.append(fd)
            actual(fd)
            if fd == target:
                raise OSError('private injected text')
        with patch.object(custody.os, 'close', side_effect=fault):
            self.assertEqual(owner.close(), ('descriptor_cleanup_unknown',))
            self.assertEqual(owner.close(), ('descriptor_cleanup_unknown',))
        self.assertEqual(closed.count(target), 1)
        self.assertTrue(all(lease.fd is None for lease in owner._snapshot.tree.leases))

    def test_acquisition_and_cleanup_fault_both_remain_fixed(self):
        actual_open, actual_close = os.open, os.close
        owned = []
        def acquisition(path, *args, **kwargs):
            if path == 'prepared':
                raise OSError('private acquisition details')
            fd = actual_open(path, *args, **kwargs)
            owned.append(fd)
            return fd
        def cleanup(fd):
            actual_close(fd)
            raise OSError('private cleanup details')
        with patch.object(custody.os, 'open', side_effect=acquisition), \
                patch.object(custody.os, 'close', side_effect=cleanup):
            with self.assertRaises(bootstrap.PreparationRefusal) as caught:
                self.prepare()
        self.assertEqual(str(caught.exception), 'preparation_io_or_shape_unknown')
        self.assertEqual(caught.exception.cleanup_faults, ('descriptor_cleanup_unknown',))
        self.assertTrue(Path(self.output).is_dir())
        for fd in owned:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_fsync_failure_is_not_a_prepared_manifest_and_stays_consumed(self):
        with patch.object(custody.os, 'fsync', side_effect=OSError('private storage details')):
            self.refused('preparation_io_or_shape_unknown')

    def test_wrong_source_uid_refuses(self):
        actual = custody.regular
        def wrong_uid(observed, limit):
            # Exercise the actual policy on stat facts from a disposable regular file.
            with patch.object(custody.os, 'geteuid', return_value=observed.st_uid + 1):
                actual(observed, limit)
        with patch.object(custody, 'regular', side_effect=wrong_uid):
            self.refused('source_file_policy')

    def test_nonexecutable_binary_refuses(self):
        (self.source / 'claude').chmod(0o400)
        self.refused('source_executable_policy')

    def test_positive_partial_writes_complete_without_duplicate_bytes(self):
        actual = os.write
        def short(fd, content):
            return actual(fd, content[:3])
        with patch.object(custody.os, 'write', side_effect=short):
            owner = self.prepare()
        self.assertEqual(Path(self.output, 'input.jsonl').read_bytes(), owner.launch.input_message)

    def test_zero_write_consumes_target_and_returns_fixed_refusal(self):
        with patch.object(custody.os, 'write', return_value=0):
            self.refused('snapshot_write_unknown')

    def test_copy_growing_beyond_fixed_bound_refuses(self):
        source = self.root / 'small'
        source.write_bytes(b'12345678')
        source.chmod(0o400)
        owner = custody.Snapshot(str(self.root / 'bounded-copy'))
        self.addCleanup(owner.close)
        actual = os.read
        grown = False
        def growing(fd, count):
            nonlocal grown
            block = actual(fd, count)
            if block and not grown:
                grown = True
                source.chmod(0o600)
                with source.open('ab') as writer:
                    writer.write(b'9')
                source.chmod(0o400)
            return block
        with patch.object(custody.os, 'read', side_effect=growing):
            with self.assertRaisesRegex(Refusal, '^snapshot_byte_limit$'):
                owner.copy('copy', dict(path=str(source), sha256=hashlib.sha256(b'12345678').hexdigest()), 8)
        self.assertEqual(Path(self.root, 'bounded-copy', 'copy').read_bytes(), b'12345678')

    def test_source_digest_mismatch_never_exposes_ready_bindings(self):
        self.document['sources']['cli']['sha256'] = 'f'*64
        self.repin()
        self.refused('source_pin_mismatch')

    def test_settings_permission_agreement_and_no_hook_invention(self):
        settings = json.loads((self.source / 'settings.json').read_bytes())
        settings['permissions']['allow'].append('Bash(*)')
        self.rewrite('settings', json.dumps(settings).encode())
        self.refused('permission_settings_mismatch')

    def test_declared_gateway_cannot_grant_complete_interface(self):
        self.document['hook_interface'] = dict(complete=True, authenticated=True,
                                                command='/made-up/gateway')
        self.refused('hook_interface_unavailable')

    def test_settings_hooks_are_not_accepted_as_reviewed_gateway(self):
        settings = json.loads((self.source / 'settings.json').read_bytes())
        settings['hooks'] = dict(SessionStart=[dict(command='invented gateway')])
        self.rewrite('settings', json.dumps(settings).encode())
        self.refused('bootstrap_settings')

    def test_mcp_ceiling_or_binary_command_override_refuses(self):
        mcp = json.loads((self.source / 'mcp.json').read_bytes())
        mcp['mcpServers']['cairn']['args'][-1] = '64000'
        self.rewrite('mcp', json.dumps(mcp).encode())
        self.refused('bootstrap_mcp')

    def test_mcp_command_override_refuses(self):
        mcp = json.loads((self.source / 'mcp.json').read_bytes())
        mcp['mcpServers']['cairn']['command'] = '/bin/sh'
        self.rewrite('mcp', json.dumps(mcp).encode())
        self.refused('bootstrap_mcp')

    def test_duplicate_or_deep_json_is_fixed_malformed(self):
        self.rewrite('settings', b'{"permissions":{},"permissions":{}}')
        self.refused('bootstrap_json')

    def test_deep_json_is_fixed_malformed(self):
        self.rewrite('settings', b'['*2000 + b'0' + b']'*2000)
        with self.assertRaises(bootstrap.PreparationRefusal) as caught:
            self.prepare()
        # Supported JSON decoders differ in whether nesting triggers recursion
        # refusal; either outcome must be a fixed refusal, never a prepared file.
        self.assertIn(str(caught.exception), ('bootstrap_json', 'bootstrap_settings'))
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^preparation_consumed$'):
            self.prepare()

    def test_parser_recursion_fault_remains_fixed_and_consumed(self):
        self.rewrite('settings', b'['*2000 + b'0' + b']'*2000)
        actual = json.loads
        def recursive(content, *args, **kwargs):
            if content.startswith('['):
                raise RecursionError('private parser text')
            return actual(content, *args, **kwargs)
        with patch.object(bootstrap.json, 'loads', side_effect=recursive):
            self.refused('bootstrap_json')

    def test_instruction_and_single_input_shared_budget_refuses(self):
        self.rewrite('instruction', b'x'*8500)
        self.refused('coding_input_budget')

    def test_malformed_utf8_input_is_fixed(self):
        self.rewrite('instruction', b'\xff')
        self.refused('bootstrap_text')

    def test_nul_instruction_cannot_compile_an_unusable_argument(self):
        self.rewrite('instruction', b'not\0an argument')
        self.refused('bootstrap_instruction')

    def test_limits_and_source_set_bindings_refuse_changes(self):
        self.document['limits']['native_searches'] = 3
        self.refused('limits_changed')

    def test_wrong_source_set_hash_refuses(self):
        self.document['binding']['source_set_sha256'] = 'd'*64
        self.refused('bootstrap_source_set')

    def test_unknown_source_role_or_argv_extension_refuses(self):
        self.document['launch']['argv'] = ['/bin/sh']
        self.refused('bootstrap_launch')

    def test_control_or_grading_source_role_refuses(self):
        self.document['sources']['oracle'] = self.document['sources']['prompt']
        self.refused('bootstrap_sources')

    def test_permission_source_cannot_be_substituted(self):
        self.document['sources']['permission_source']['sha256'] = 'e'*64
        self.repin()
        self.refused('permission_source_mismatch')

    def test_closed_owner_cannot_produce_new_review_bindings(self):
        owner = self.prepare()
        self.assertEqual(owner.close(), ())
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            owner.review_bindings()

    def test_closed_snapshot_cannot_copy_or_generate_more_files(self):
        owner = self.prepare()
        owner.close()
        with self.assertRaisesRegex(Refusal, '^snapshot_consumed$'):
            owner._snapshot.generated('after-close', b'x')
        self.assertFalse(Path(self.output, 'after-close').exists())

    def test_caller_mutation_cannot_change_frozen_binding(self):
        owner = self.prepare()
        original = copy.deepcopy(self.document['binding'])
        self.document['binding']['arm'] = 'CURRENT'
        self.document['limits']['native_searches'] = 100
        self.assertEqual(owner.review_bindings()['binding'], original)
        self.assertEqual(owner.review_bindings()['limits'], ORIGINAL_LIMITS)

    def test_public_compiled_launch_cannot_be_replaced(self):
        owner = self.prepare()
        with self.assertRaises(AttributeError):
            owner.launch = dataclasses.replace(owner.launch, input_message=b'changed input\n')
        review = owner.review_bindings()
        self.assertEqual(review['input_sha256'], review['snapshots']['input.jsonl']['sha256'])

    def test_damaged_internal_compilation_refuses_held_review(self):
        owner = self.prepare()
        owner._launch = dataclasses.replace(owner.launch, input_message=b'changed input\n')
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^review_custody_unknown$'):
            owner.review_bindings()

    def test_freezes_before_validator_and_derives_same_frozen_mcp(self):
        original = copy.deepcopy(self.document)
        actual = bootstrap._declaration
        def external_mutation(frozen):
            compiled = actual(frozen)
            self.document['binding']['arm'] = 'UNVALIDATED'
            self.document['mcp_binding']['run'] = 'unvalidated-run'
            return compiled
        with patch.object(bootstrap, '_declaration', side_effect=external_mutation):
            owner = self.prepare()
        self.assertEqual(owner.review_bindings()['binding'], original['binding'])
        mcp = json.loads(Path(self.output, 'mcp.json').read_bytes())
        self.assertEqual(mcp['mcpServers']['cairn']['args'], mcp_arguments(original['mcp_binding']))

    def test_oversized_or_nonplain_graph_refuses_before_source_copy(self):
        self.document['launch']['model'] = 'x'*65537
        self.refused('bootstrap_graph_limit')
        self.assertEqual(list(Path(self.output).iterdir()), [])

    def test_graph_depth_is_bounded_before_validation(self):
        nested = {}
        for _ in range(10):
            nested = {'deeper': nested}
        self.document['unexpected'] = nested
        self.refused('bootstrap_graph_limit')

    def test_keyboard_interrupt_during_write_closes_all_and_propagates_primary(self):
        before = set(os.listdir('/proc/self/fd'))
        interrupted = KeyboardInterrupt('test primary interruption')
        with patch.object(custody.os, 'write', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt) as caught:
                self.prepare()
        self.assertIs(caught.exception, interrupted)
        self.assertEqual(caught.exception.cleanup_faults, ())
        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
        with self.assertRaisesRegex(bootstrap.PreparationRefusal, '^preparation_consumed$'):
            self.prepare()

    def test_context_cleanup_cannot_mask_primary_body_exception(self):
        owner = self.prepare()
        actual = os.close
        target = owner._snapshot.files['prompt.txt'][0].fd
        primary = ValueError('test primary body failure')
        def failed_close(fd):
            actual(fd)
            if fd == target:
                raise OSError('private close details')
        with patch.object(custody.os, 'close', side_effect=failed_close):
            with self.assertRaises(ValueError) as caught:
                with owner:
                    raise primary
        self.assertIs(caught.exception, primary)
        self.assertEqual(caught.exception.cleanup_faults, ('descriptor_cleanup_unknown',))

    def test_unanchored_setup_refuses_close_and_preserves_primary_unknown(self):
        tree = custody.HeldTree()
        source = self.source / 'prompt.txt'
        owned = os.open(source, os.O_RDONLY)
        primary = OSError('private setup details')
        try:
            with patch.object(custody.os, 'fstat', side_effect=primary), \
                    patch.object(custody.os, 'close') as close:
                with self.assertRaises(OSError) as caught:
                    tree.own(owned)
                close.assert_not_called()
            self.assertIs(caught.exception, primary)
            self.assertEqual(tree.close(), ('descriptor_cleanup_unknown',))
            os.fstat(owned)
        finally:
            # Test-owned direct handle; not a fallback by the preparer.
            os.close(owned)

    def test_initial_anchor_failure_does_not_close_reused_foreign_fd(self):
        tree = custody.HeldTree()
        owned = os.open(self.source / 'prompt.txt', os.O_RDONLY)
        foreign_path = self.root / 'foreign'
        foreign_path.write_bytes(b'foreign file')
        actual_close, actual_open = os.close, os.open
        foreign = None
        def anchor_lost(fd):
            nonlocal foreign
            actual_close(fd)
            foreign = actual_open(foreign_path, os.O_RDONLY)
            self.assertEqual(foreign, fd)
            raise OSError('private setup details')
        try:
            with patch.object(custody.os, 'fstat', side_effect=anchor_lost):
                with self.assertRaises(OSError):
                    tree.own(owned)
            self.assertEqual(tree.close(), ('descriptor_cleanup_unknown',))
            self.assertEqual(os.read(foreign, 100), b'foreign file')
        finally:
            if foreign is not None:
                actual_close(foreign)

    def test_aggregate_source_copy_limit_refuses_second_file(self):
        owner = custody.Snapshot(str(self.root / 'aggregate'))
        self.addCleanup(owner.close)
        first, second = self.source / 'prompt.txt', self.source / 'instruction.txt'
        cap = first.stat().st_size + second.stat().st_size - 1
        with patch.object(custody, 'PIN_LIMIT', cap):
            owner.copy('first', dict(path=str(first), sha256=hashlib.sha256(first.read_bytes()).hexdigest()), 9500)
            with self.assertRaisesRegex(Refusal, '^snapshot_total_byte_limit$'):
                owner.copy('second', dict(path=str(second), sha256=hashlib.sha256(second.read_bytes()).hexdigest()), 9500)
        self.assertFalse(Path(self.root, 'aggregate', 'second').exists())


if __name__ == '__main__':
    unittest.main()
