"""Checked unbound H150 consumers against the complete installed Source."""
import base64
import copy
import fcntl
import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path
import shutil
import signal
import time
import unittest

from test_f02_bound_memory import BoundMemoryCase
import test_f03_bound_capture as bound_capture
import test_f03_capture_boundary as capture_fixture
import test_f03_capture_selection as selection_fixture
from test_f04_activation import SESSION, compat, encoded, scalar_tree, sha, write
from test_r04_host_wiring import UnboundFixture


class UnboundSelection:
    def setUp(self):
        super().setUp()
        previous = self.f
        installed = self.root / 'installed'
        installed.mkdir(mode=0o700)
        self.f = UnboundFixture(installed / 'project', self.binary,
                                stager=self.stager, harness=previous.memory['harness'], enabled=False)
        for config in (self.f.memory, self.f.coord):
            for key in ('cairn', 'socket', 'token_file'):
                config[key] = previous.memory[key]
        for key in ('claude', 'model', 'capture_observations', 'recall_observations'):
            if key in previous.memory:
                self.f.memory[key] = previous.memory[key]
        self.f.memory['capture_observations'] = True
        self.f.publish_configuration(enabled=False)
        self.f.event = dict(previous.event, cwd=str(self.f.root))
        self.f.raw = encoded(self.f.event)

    def go_vector(self, timeout=None):
        vector = BoundMemoryCase.go_vector(self, timeout)
        vector[1] = 'memory-capture'
        if getattr(self, 'capture_entrypoint', None):
            vector[vector.index('--entrypoint') + 1] = self.capture_entrypoint
        return vector

    def source_launcher(self, timeout=150):
        vector = compat.launch_vector(sys.executable, self.f.receipt_path, self.f.receipt_sha,
                                      'memory', self.capture_entrypoint, self.f.memory_path,
                                      timeout, self.f.launch_root)
        raw = compat.launcher_bytes(vector)
        directory = self.f.launch_root / sha(raw)
        directory.mkdir(mode=0o700, exist_ok=True)
        launcher = directory / 'inbox'
        if launcher.exists():
            self.assertEqual(launcher.read_bytes(), raw)
        else:
            write(launcher, raw, 0o500)
        return launcher

    def source(self, *, timeout=None):
        print('CHECKED_UNBOUND_SELECTION=' + json.dumps(dict(
            test=self.id(), receipt_sha256=self.f.receipt_sha,
            source_snapshot=Path(self.f.receipt['snapshot_root']).name,
            source_members=self.f.receipt['members'],
            go_sha256=self.f.receipt['binary']['sha256']), sort_keys=True), flush=True)
        if not getattr(self, 'capture_entrypoint', None):
            return super().source(timeout=timeout)
        return subprocess.run([*getattr(self, 'source_prefix', ()),
                               str(self.source_launcher(150 if timeout is None else timeout))],
                              input=self.f.raw, cwd=self.f.root, env=self.f.env(),
                              capture_output=True, timeout=165)


class CheckedCapturePair(BoundMemoryCase):
    # Installed non-.py aliases report unknown Source identity; a repinned .py
    # alias reports its exact checked member digest. Keep both assertions.
    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        seed = {path.name: path.read_bytes() for path in directory.iterdir()} if directory.exists() else {}
        self.calls.clear()
        self.report_locks.clear()
        self.before_run('source')
        source = self.source()
        self.after_run()
        source_state, calls, locks = self.state(), list(self.calls), list(self.report_locks)
        self.reset_state(seed)
        self.calls.clear()
        self.report_locks.clear()
        self.before_run('go')
        go = self.go()
        self.after_run()
        self.assertEqual((go.returncode, go.stdout, go.stderr), (source.returncode, source.stdout, source.stderr))
        actual_state = self.state()
        self.assertEqual(actual_state.keys(), source_state.keys())
        for name in source_state:
            self.assertEqual(scalar_tree(actual_state[name]), scalar_tree(source_state[name]), name)
        self.assertEqual([row[0] for row in self.calls], [row[0] for row in calls])
        self.assertEqual(self.report_locks, locks)
        self.assertTrue(all(all(row.values()) for row in self.report_locks))
        for actual, expected in zip(self.calls, calls):
            def normalized(row):
                body = copy.deepcopy(row[1])
                identifier = body.get('request_id')
                if row[0] not in ('create', 'revise'):
                    body.pop('request_id', None)
                if row[0] != 'version':
                    self.assertEqual(str(uuid.UUID(identifier)), identifier)
                    self.assertNotEqual(uuid.UUID(identifier).int, 0)
                if row[0] in ('capture-observation', 'recall-observation'):
                    for key in ('duration_ms', 'elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms', 'selector_duration_ms'):
                        if key in body:
                            self.assertGreaterEqual(body.pop(key), 0)
                self.assertEqual(row[2]['Authorization'], 'Bearer bound-memory-synthetic-token')
                return body
            self.assertEqual(normalized(actual), normalized(expected))
            for row in (actual, expected):
                declaration = row[2]['Cairn-Client-Diagnostics']
                origin = json.loads(base64.urlsafe_b64decode(declaration + '=' * (-len(declaration) % 4)))['origin']
                self.assertEqual(origin['component'], 'lifecycle-memory')
                source_capture = self.f.event['hook_event_name'] in ('PreCompact', 'Stop', 'SessionEnd')
                if row is actual or not source_capture:
                    self.assertEqual(origin, dict(component='lifecycle-memory', basis='reported',
                                                  implementation_id=self.f.receipt['binary']['sha256']))
                else:
                    expected_origin = dict(component='lifecycle-memory', basis='reported')
                    if self.f.receipt_sha != self.f.installed['receipt']['sha256']:
                        entrypoint = 'memory-hook.py'
                    else:
                        words = shlex.split(self.f.launcher('memory', self.timeout()).read_text())
                        entrypoint = words[words.index('--entrypoint') + 1]
                    if entrypoint.endswith('.py'):
                        expected_origin['implementation_id'] = self.f.receipt['members'][entrypoint]['sha256']
                    self.assertEqual(origin, expected_origin)
        return go


class CheckedUnboundCaptureTests(UnboundSelection, CheckedCapturePair):
    def setUp(self):
        super().setUp()
        self.event('PreCompact')

    session_key = bound_capture.BoundCaptureTests.session_key
    after_run = bound_capture.BoundCaptureTests.after_run
    scheduled = bound_capture.BoundCaptureTests.scheduled

    def before_run(self, side):
        super().before_run(side)
        if side == 'go' and getattr(self, 'before_candidate', None):
            self.before_candidate()
        capture_fixture.CaptureBoundaryTests.start_scheduler(self, side)

    def rollout(self, messages=0, path=None):
        if isinstance(messages, int):
            return bound_capture.BoundCaptureTests.rollout(self, messages)
        return capture_fixture.CaptureBoundaryTests.rollout(self, messages, path)

    configure = capture_fixture.CaptureBoundaryTests.configure
    outcome = capture_fixture.CaptureBoundaryTests.outcome
    courtesy_messages = capture_fixture.CaptureBoundaryTests.courtesy_messages

    test_missing_transcript_commits_pending_without_native_activation = bound_capture.BoundCaptureTests.test_missing_transcript_commits_pending_without_native_activation
    test_empty_courtesy_and_duplicate_reuse_existing_capture_body = bound_capture.BoundCaptureTests.test_empty_courtesy_and_duplicate_reuse_existing_capture_body
    test_short_and_continuation_stop_preserve_source_early_returns = bound_capture.BoundCaptureTests.test_short_and_continuation_stop_preserve_source_early_returns
    test_capture_lock_refuses_without_committing_a_status = bound_capture.BoundCaptureTests.test_capture_lock_refuses_without_committing_a_status
    test_claude_capture_rows_keep_source_eligibility_and_no_activation = bound_capture.BoundCaptureTests.test_claude_capture_rows_keep_source_eligibility_and_no_activation
    test_changed_capture_fields_and_binding_refuse_cas_before_marker = bound_capture.BoundCaptureTests.test_changed_capture_fields_and_binding_refuse_cas_before_marker
    test_concurrent_recall_and_hints_survive_bound_capture_commit = bound_capture.BoundCaptureTests.test_concurrent_recall_and_hints_survive_bound_capture_commit
    test_failed_replace_cannot_advance_marker_and_next_capture_can_retry = bound_capture.BoundCaptureTests.test_failed_replace_cannot_advance_marker_and_next_capture_can_retry
    test_incorrect_capture_budget_refuses_before_body_or_reports = bound_capture.BoundCaptureTests.test_incorrect_capture_budget_refuses_before_body_or_reports
    test_short_stop_and_repeated_append_truncate_keep_exact_offsets = capture_fixture.CaptureBoundaryTests.test_short_stop_and_repeated_append_truncate_keep_exact_offsets
    test_append_after_snapshot_remains_for_later_stop = capture_fixture.CaptureBoundaryTests.test_append_after_snapshot_remains_for_later_stop
    test_bounded_transcript_tail_filters_non_owner_parts_and_keeps_snapshot_offset = capture_fixture.CaptureBoundaryTests.test_bounded_transcript_tail_filters_non_owner_parts_and_keeps_snapshot_offset

    def test_report_failure_keeps_confirmed_capture_state(self):
        self.f.memory['socket'] = str(self.root / 'missing.sock')
        self.configure('claude', 'SessionEnd', messages=[])
        self.assertEqual(self.pair().returncode, 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state()[SESSION + '.json']['last_capture']['outcome'], 'empty')

    def test_account_privacy_and_disable_guards_have_no_body_effects(self):
        original = self.f.env
        for fields in (dict(CAIRN_LIFECYCLE_DISABLED='1'), dict(CAIRN_LIFECYCLE_CHILD='1'),
                       dict(CODEX_HOME=str(self.root / 'different-account'))):
            with self.subTest(environment=list(fields)):
                self.f.env = lambda fields=fields: dict(original(), **fields)
                self.assertEqual(self.pair().stdout, b'{}\n')
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})
        self.f.env = original
        (self.f.root / '.cairn-no-memory').touch()
        self.assertEqual(self.pair().stdout, b'{}\n')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})

    def test_changed_complete_selection_refuses_before_body(self):
        for path in (Path(self.f.receipt['snapshot_root']) / 'memory.py', self.f.memory_path,
                     self.f.coord_path, self.f.binding, self.f.binary):
            with self.subTest(artifact=path.name):
                original = path.read_bytes()
                self.overwrite(path, original + b' ')
                try:
                    result = self.pair()
                    self.assertEqual((result.returncode, result.stdout), (1, b''))
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.state(), {})
                finally:
                    self.overwrite(path, original)

    def test_unavailable_capture_rows_refuse_without_state_api_or_selector(self):
        for harness, event in (('codex', 'SessionEnd'), ('claude', 'Stop'),
                               ('opencode', 'Stop'), ('hermes', 'Stop'),
                               ('agy', 'PreCompact'), ('unknown', 'PreCompact'),
                               ('codex', 'SessionStart'), ('codex', 'PostToolUse')):
            with self.subTest(harness=harness, event=event):
                self.configure(harness, event, messages=[])
                result = self.go(timeout=150)
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertEqual(result.stderr, b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def test_external_authority_flags_cannot_select_vector_deadline_or_descriptor(self):
        for option in ('deadline', 'vector', 'image-fd', 'authenticated', 'binding-enabled'):
            with self.subTest(option=option):
                self.extra_selection = ('--' + option, '1')
                result = self.go()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'phase=compatibility_preflight', result.stderr)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def test_every_one_of_eight_coordination_pins_is_required(self):
        manifest = json.loads(self.f.binding.read_bytes())
        paths = [self.f.coord_path]
        for index in range(1, 8):
            path = self.f.coord_path.with_name('coordination-' + str(index) + '.json')
            write(path, self.f.coord_path.read_bytes())
            paths.append(path)
        descriptors = [dict(path=str(path), sha256=sha(path.read_bytes())) for path in paths]
        manifest['coordination_configs'] = descriptors
        self.overwrite(self.f.binding, encoded(manifest))
        self.f.receipt['coordination_configs'] = descriptors
        self.f.receipt['binding']['sha256'] = sha(self.f.binding.read_bytes())
        self.overwrite(self.f.receipt_path, encoded(self.f.receipt))
        self.f.receipt_sha = sha(self.f.receipt_path.read_bytes())
        self.assertEqual(self.pair().returncode, 0)
        for path in paths:
            with self.subTest(coordinator=path.name):
                self.reset_state({})
                original = path.read_bytes()
                self.overwrite(path, original + b' ')
                try:
                    result = self.pair()
                    self.assertEqual((result.returncode, result.stdout), (1, b''))
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.state(), {})
                finally:
                    self.overwrite(path, original)

    def test_enabled_or_bound_generation_does_not_enter_unbound_capture(self):
        manifest = json.loads(self.f.binding.read_bytes())
        manifest['enabled'] = True
        self.overwrite(self.f.binding, encoded(manifest))
        self.f.receipt['binding']['sha256'] = sha(self.f.binding.read_bytes())
        self.overwrite(self.f.receipt_path, encoded(self.f.receipt))
        self.f.receipt_sha = sha(self.f.receipt_path.read_bytes())
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})

    def test_actual_launcher_and_executing_inode_are_required(self):
        launcher = self.go_launcher()
        raw = launcher.read_bytes()
        self.overwrite(launcher, raw + b'# changed\n')
        result = subprocess.run(self.go_vector() + ['--'], input=self.f.raw,
                                cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=15)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'compatibility_preflight', result.stderr)
        self.overwrite(launcher, raw)
        alternate = self.root / 'same-bytes-other-inode'
        shutil.copyfile(self.f.binary, alternate)
        alternate.chmod(0o500)
        result = subprocess.run(self.go_vector() + ['--'], executable=str(alternate),
                                input=self.f.raw, cwd=self.f.root, env=self.f.env(),
                                capture_output=True, timeout=15)
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertIn(b'compatibility_preflight', result.stderr)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})

    def test_input_limits_and_malformed_events_refuse_without_effects(self):
        for raw in (b'{', b'[]', b'null', b'x' * (1024 * 1024 + 1)):
            with self.subTest(bytes=len(raw)):
                self.f.raw = raw
                result = self.pair()
                self.assertEqual((result.returncode, result.stdout), (1, b''))
                self.assertIn(b'phase=input', result.stderr)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def test_unexpected_transcript_failure_is_redacted_and_retryable(self):
        path = self.root / 'malformed.jsonl'
        path.write_bytes(b'[]\n')
        self.event('PreCompact', transcript_path=str(path))
        source = self.source()
        self.assertEqual((source.returncode, source.stdout), (1, b''))
        self.assertIn(b'AttributeError', source.stderr)
        self.calls.clear()
        candidate = self.go()
        self.assertEqual((candidate.returncode, candidate.stdout, candidate.stderr),
                         (1, b'', b'Cairn lifecycle diagnostic: phase=handle error_class=UnexpectedError\n'))
        self.assertEqual(self.state(), {})

    def test_changed_receipt_refuses_before_body(self):
        self.overwrite(self.f.receipt_path, self.f.receipt_path.read_bytes() + b' ')
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})

    def test_repinned_bound_profile_cannot_downgrade(self):
        config = dict(self.f.coord, inbox_recall_binding=str(self.f.binding))
        self.overwrite(self.f.coord_path, encoded(config))
        self.repin_profiles()
        result = self.pair()
        self.assertEqual((result.returncode, result.stdout), (1, b''))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.state(), {})


class CheckedUnboundBoundaryCoverageTests(UnboundSelection, CheckedCapturePair):
    def setUp(self):
        super().setUp()
        self.configure('opencode', 'PreCompact', messages=[])

    session_key = capture_fixture.CaptureBoundaryTests.session_key
    configure = capture_fixture.CaptureBoundaryTests.configure
    rollout = capture_fixture.CaptureBoundaryTests.rollout
    courtesy_messages = capture_fixture.CaptureBoundaryTests.courtesy_messages
    outcome = capture_fixture.CaptureBoundaryTests.outcome
    scheduled = bound_capture.BoundCaptureTests.scheduled
    after_run = capture_fixture.CaptureBoundaryTests.finish_scheduler

    def before_run(self, side):
        super().before_run(side)
        observed_directory = self.f.root / 'invocation-trace'
        if observed_directory.exists():
            # The unchanged reference method observes its project-relative path.
            # Both real hooks still open the receipt config's actual trace path.
            actual_directory = self.f.memory_path.parent / 'invocation-trace'
            actual_directory.mkdir(mode=0o700, exist_ok=True)
            observed = observed_directory / 'trace.jsonl'
            if not observed.exists():
                write(observed, b'', 0o600)
            actual = actual_directory / 'trace.jsonl'
            if not actual.exists():
                os.link(observed, actual)
            self.assertTrue(os.path.samefile(actual, observed))
        capture_fixture.CaptureBoundaryTests.start_scheduler(self, side)

    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        seed = {path.name: path.read_bytes() for path in directory.iterdir()} if directory.exists() else {}
        self.calls.clear()
        self.report_locks.clear()
        self.before_run('source')
        source = self.source()
        self.after_run()
        source_state, calls, locks = self.state(), list(self.calls), list(self.report_locks)
        self.reset_state(seed)
        self.calls.clear()
        self.report_locks.clear()
        self.before_run('go')
        go = self.go()
        self.after_run()
        self.assertEqual((go.returncode, go.stdout, go.stderr), (source.returncode, source.stdout, source.stderr))
        actual_state = self.state()
        self.assertEqual(actual_state.keys(), source_state.keys())
        for name in source_state:
            self.assertEqual(scalar_tree(actual_state[name]), scalar_tree(source_state[name]), name)
        self.assertEqual([row[0] for row in self.calls], [row[0] for row in calls])
        self.assertEqual(self.report_locks, locks)
        # A legacy writer deliberately holds the shared lock in the timeout
        # case. Reference parity still compares every observed lock exactly;
        # the capture lock owned by each hook must always be released.
        self.assertTrue(all(row['.capture.lock'] for row in self.report_locks))
        print('CHECKED_CAPTURE_LOCKS=' + json.dumps(dict(test=self.id(), source=locks,
              go=self.report_locks), sort_keys=True), flush=True)
        for actual, expected in zip(self.calls, calls):
            def normalized(row):
                body = copy.deepcopy(row[1])
                identifier = body.get('request_id')
                if row[0] not in ('create', 'revise'):
                    body.pop('request_id', None)
                if row[0] != 'version':
                    self.assertEqual(str(uuid.UUID(identifier)), identifier)
                    self.assertNotEqual(uuid.UUID(identifier).int, 0)
                if row[0] in ('capture-observation', 'recall-observation'):
                    for key in ('duration_ms', 'elapsed_ms', 'search_ms', 'pulls_ms', 'selector_ms', 'selector_duration_ms'):
                        if key in body:
                            self.assertGreaterEqual(body.pop(key), 0)
                self.assertEqual(row[2]['Authorization'], 'Bearer bound-memory-synthetic-token')
                return body
            self.assertEqual(normalized(actual), normalized(expected))
            for row in (actual, expected):
                declaration = row[2]['Cairn-Client-Diagnostics']
                origin = json.loads(base64.urlsafe_b64decode(declaration + '=' * (-len(declaration) % 4)))['origin']
                self.assertEqual(origin['component'], 'lifecycle-memory')
                source_capture = self.f.event['hook_event_name'] in ('PreCompact', 'Stop', 'SessionEnd')
                if row is actual or not source_capture:
                    self.assertEqual(origin, dict(component='lifecycle-memory', basis='reported',
                                                  implementation_id=self.f.receipt['binary']['sha256']))
                else:
                    expected_origin = dict(component='lifecycle-memory', basis='reported')
                    if self.f.receipt_sha != self.f.installed['receipt']['sha256']:
                        entrypoint = 'memory-hook.py'
                    else:
                        words = shlex.split(self.f.launcher('memory', self.timeout()).read_text())
                        entrypoint = words[words.index('--entrypoint') + 1]
                    if entrypoint.endswith('.py'):
                        expected_origin['implementation_id'] = self.f.receipt['members'][entrypoint]['sha256']
                    self.assertEqual(origin, expected_origin)
        return go

    test_capture_trace_preserves_phase_stage_outcome_and_zero_context = capture_fixture.CaptureBoundaryTests.test_capture_trace_preserves_phase_stage_outcome_and_zero_context
    test_final_commit_waits_for_short_legacy_writer_and_times_out_without_marker = capture_fixture.CaptureBoundaryTests.test_final_commit_waits_for_short_legacy_writer_and_times_out_without_marker
    test_preparation_failure_does_not_report_or_create_state = capture_fixture.CaptureBoundaryTests.test_preparation_failure_does_not_report_or_create_state
    test_invalid_dialogue_and_missing_transcript_do_not_advance_status = capture_fixture.CaptureBoundaryTests.test_invalid_dialogue_and_missing_transcript_do_not_advance_status
    test_only_ascii_blank_transcript_lines_are_skipped = capture_fixture.CaptureBoundaryTests.test_only_ascii_blank_transcript_lines_are_skipped


class CheckedUnboundProviderCaptureTests(UnboundSelection, bound_capture.BoundProviderCaptureTests, CheckedCapturePair):
    pass


class CheckedUnboundSelectionTests(UnboundSelection, selection_fixture.CaptureSelectionTests):
    # Preserve the reference fixture's independent selector/API/state assertions;
    # only its two process launch boundaries change to the checked R02 selection.
    capture_entrypoint = 'memory.py'
    setUpClass = classmethod(BoundMemoryCase.setUpClass.__func__)
    timeout = BoundMemoryCase.timeout
    go_launcher = BoundMemoryCase.go_launcher
    go = BoundMemoryCase.go

    def test_all_supported_producer_cells_reach_real_selection(self):
        messages = [dict(role='user' if i % 2 == 0 else 'assistant',
                         text='Preserve the postgres retention decision and its operator guide.')
                    for i in range(6)]
        for harness, events in (('codex', ('PreCompact', 'Stop')),
                                ('claude', ('PreCompact', 'SessionEnd')),
                                ('opencode', ('PreCompact', 'SessionEnd')),
                                ('hermes', ('PreCompact', 'SessionEnd'))):
            for event in events:
                with self.subTest(harness=harness, event=event):
                    shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
                    self.records.clear()
                    if harness == 'codex':
                        fields = dict(transcript_path=str(self.rollout(messages)))
                    elif harness == 'claude':
                        path = self.root / 'claude-transcript.jsonl'
                        write(path, b''.join(encoded(dict(type=message['role'], uuid=str(uuid.uuid4()),
                              message=dict(content=[dict(type='text', text=message['text'])]))) + b'\n'
                              for message in messages))
                        fields = dict(transcript_path=str(path))
                    else:
                        fields = dict(messages=messages)
                    self.configure(harness, event, **fields)
                    self.assertEqual(self.pair().returncode, 0)
                    self.assertEqual(len(self.provider_calls), 1)
                    self.assertEqual(self.state()[self.session_key() + '.json']['last_capture']['outcome'], 'nothing_selected')

    def test_crashed_checked_capture_releases_locks_without_a_marker(self):
        self.configure('opencode', 'PreCompact', messages=[
            dict(role='user', text='Preserve the postgres retention policy.')])
        for side in ('source', 'go'):
            with self.subTest(side=side):
                shutil.rmtree(self.f.memory['state_dir'], ignore_errors=True)
                self.calls.clear()
                log = self.root / 'provider.calls'
                log.unlink(missing_ok=True)
                write(self.root / 'provider.json', encoded(dict(delay=60,
                      result=dict(structured_output=self.selection))))
                launcher = self.source_launcher() if side == 'source' else self.go_launcher()
                process = subprocess.Popen([str(launcher)], stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           cwd=self.f.root, env=self.f.env())
                selector = None
                try:
                    process.stdin.write(self.f.raw)
                    process.stdin.close()
                    expires = time.monotonic() + 10
                    while not log.exists() and time.monotonic() < expires:
                        time.sleep(0.01)
                    self.assertTrue(log.exists(), 'checked capture never reached the selector')
                    selector = json.loads(log.read_text().splitlines()[0])['pid']
                    self.assertGreater(process.pid, 1)
                    self.assertGreater(selector, 1)
                    self.assertNotEqual(selector, process.pid)
                    process.kill()
                    process.wait(timeout=5)
                    self.assertEqual(process.returncode, -signal.SIGKILL)
                    self.assertEqual(self.state(), {})
                    self.assertFalse(any(operation in ('create', 'revise') for operation, _, _ in self.calls))
                    for suffix in ('.lock', '.capture.lock'):
                        with (Path(self.f.memory['state_dir']) / (self.session_key() + suffix)).open('a') as stream:
                            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=5)
                    if selector is not None:
                        try:
                            os.kill(selector, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.stdout.close()
                    process.stderr.close()

    def pair(self):
        directory = Path(self.f.memory['state_dir'])
        seed = {p.name: p.read_bytes() for p in directory.iterdir() if p.suffix != '.lock'} if directory.exists() else {}
        records = copy.deepcopy(self.records)
        spec = dict(result=dict(structured_output=self.selection))
        spec.update(self.provider)
        write(self.root/'provider.json', encoded(spec))
        observations = []
        for side in ('source', 'go'):
            self.records = copy.deepcopy(records)
            self.calls.clear()
            self.report_locks.clear()
            self.index_count = self.write_count = 0
            (self.root/'provider.calls').unlink(missing_ok=True)
            if side == 'go':
                if directory.exists():
                    for p in directory.iterdir():
                        if p.suffix != '.lock': p.unlink()
                if seed:
                    directory.mkdir(mode=0o700, exist_ok=True)
                    for name, raw in seed.items(): write(directory/name, raw)
            self.start_scheduler(side)
            if side == 'source':
                result = self.source()
            else:
                result = self.go()
            self.finish_scheduler()
            provider_calls = [json.loads(line) for line in (self.root/'provider.calls').read_text().splitlines()] if (self.root/'provider.calls').exists() else []
            for call in provider_calls:
                # Both frontends reap their direct selector before returning.
                self.assertFalse(Path('/proc', str(call.pop('pid'))).exists())
                self.assertEqual(call.pop('entries'), [])
                cwd = Path(call.pop('cwd'))
                self.assertTrue(cwd.name.startswith('cairn-selection-'))
                self.assertFalse(cwd.exists())
                self.assertEqual(call['child'], '1')
                self.assertIsNone(call['claudecode'])
                call['body'] = json.loads(call['body'])
            observations.append((result, self.state(), copy.deepcopy(self.calls), list(self.report_locks), copy.deepcopy(self.records), provider_calls))
        source, candidate = observations
        def output(raw):
            if self.f.memory['harness'] != 'hermes' or not raw:
                return raw
            body = json.loads(raw)
            last = body.get('cairn_status', {}).get('last_capture')
            if last is not None: self.assertGreaterEqual(last.pop('at'), 0)
            return scalar_tree(body)
        self.assertEqual((candidate[0].returncode, output(candidate[0].stdout), candidate[0].stderr),
                         (source[0].returncode, output(source[0].stdout), source[0].stderr))
        self.assertEqual(scalar_tree(candidate[1]), scalar_tree(source[1]))
        self.assertEqual(candidate[3:], source[3:])
        self.assertTrue(all(all(locks.values()) for locks in self.report_locks))
        self.assertEqual([c[0] for c in candidate[2]], [c[0] for c in source[2]])
        for (operation, actual, headers), (_, expected, source_headers) in zip(candidate[2], source[2]):
            actual, expected = copy.deepcopy(actual), copy.deepcopy(expected)
            for request in (actual, expected):
                if operation not in ('create', 'revise', 'version'):
                    identity = request.pop('request_id')
                    self.assertEqual(str(uuid.UUID(identity)), identity)
                if operation == 'capture-observation':
                    self.assertGreaterEqual(request.pop('duration_ms'), 0)
                    if 'selector_duration_ms' in request: self.assertGreaterEqual(request.pop('selector_duration_ms'), 0)
            self.assertEqual(actual, expected, operation)
            for declared, image in ((headers, sha(self.binary.read_bytes())), (source_headers, self.f.receipt['members']['memory.py']['sha256'])):
                self.assertEqual(declared['Authorization'], 'Bearer f03-synthetic-token')
                raw = declared['Cairn-Client-Diagnostics']
                diagnostic = json.loads(base64.urlsafe_b64decode(raw+'='*(-len(raw)%4)))
                self.assertEqual(diagnostic['origin']['component'], 'lifecycle-memory')
                self.assertEqual(diagnostic['origin']['implementation_id'], image)
        self.provider_calls = candidate[5]
        type(self).source_pairs += 1
        return candidate[0]

    def run_seeded_title(self, side, raw):
        directory = Path(self.f.memory['state_dir'])
        shutil.rmtree(directory, ignore_errors=True)
        directory.mkdir(mode=0o700)
        path = directory/(self.session_key()+'.json')
        write(path, raw)
        self.calls.clear()
        self.report_locks.clear()
        self.records.clear()
        self.index_count = self.write_count = 0
        (self.root/'provider.calls').unlink(missing_ok=True)
        write(self.root/'provider.json', encoded(dict(result=dict(structured_output=self.selection))))
        if side == 'source':
            result = self.source()
        else:
            result = self.go()
        return result, path


if __name__ == '__main__':
    unittest.main()
