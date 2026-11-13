"""Held bound Claude tool producer: Source controls and negative Go probes.

Actual emitted Go failed at the current memoryhost recall/capture admission
guard. The producer therefore remains Python. The existing fixture/comparator
checks retained Source against that actual selected Python command; those are
controls, not Go parity. Separate explicit capability probes retain the exact
unsupported-Go refusal without treating it as producer support.
"""
import base64
import copy
import fcntl
import json
import os
from pathlib import Path
import shlex
import subprocess
import unittest
from unittest.mock import patch

from test_f02_bound_memory import BoundMemoryCase
from test_f04_activation import SESSION, encoded, write, sha
from test_r04_bound_provider_wiring import R04BoundProviderWiringTests


class R04BoundToolWiringTests(R04BoundProviderWiringTests):
    before_run = BoundMemoryCase.before_run
    after_run = BoundMemoryCase.after_run

    @classmethod
    def setUpClass(cls):
        root = os.environ.get('R04_BOUND_EVIDENCE_DIR')
        if root:
            Path(root).mkdir(mode=0o700, exist_ok=True)
            with patch.dict(os.environ, R04_BOUND_EVIDENCE_DIR=str(Path(root) / 'bound-tool')):
                super().setUpClass()
        else:
            super().setUpClass()

    def event(self, name, **fields):
        # Native tool callbacks have tool_use_id, not a fabricated prompt owner.
        self.f.event = dict(hook_event_name=name, session_id=SESSION, cwd=str(self.f.root),
                            tool_use_id='toolu_r04_fixture')
        self.f.event.update(fields)
        self.f.raw = encoded(self.f.event)

    def stage_tool(self, *, enabled=True):
        self.stage_bound('claude', 'agent_tools', enabled=enabled)
        self.assertFalse(self.f.package.exists())
        for name in ('PostToolUse', 'PostToolUseFailure'):
            row = self.rows[name]
            self.assertEqual((row['implementation'], row['hook']['timeout']), ('python', 13))
            self.assertEqual(row['argv'], row['retained_launcher'])
            self.assertEqual(row['reason'], 'bound callback')
        print('R04_BOUND_TOOL_SELECTION ' + json.dumps(dict(
            kind='retained Source producer control; Go unsupported', enabled=enabled,
            tool_rows={name: self.rows[name] for name in ('PostToolUse', 'PostToolUseFailure')})), flush=True)

    def go(self):
        row = self.rows[self.f.event['hook_event_name']]
        self.assertEqual(row['implementation'], 'python')
        # The existing comparator calls its second side "go". This cell is an
        # actual emitted Python producer control and is labelled as such.
        print('R04_BOUND_TOOL_CONTROL retained-python-versus-emitted-python', flush=True)
        return self.invoke_provider_side('go', lambda: subprocess.run(
            ['/bin/sh', '-c', row['hook']['command']], input=self.f.raw,
            cwd=self.f.root, env=self.f.env(), capture_output=True, timeout=20))

    def retain_pair(self):
        if not self.evidence:
            return
        self.case_number += 1
        root = self.evidence / (self._testMethodName + '-' + str(self.case_number))
        root.mkdir(mode=0o700)
        def raw(value):
            if isinstance(value, bytes):
                return dict(base64=base64.b64encode(value).decode())
            raise TypeError(type(value).__name__)
        write(root / 'pair.json', json.dumps(self.last_raw_pair, default=raw).encode())
        availability = {}
        for name, path in (('memory', self.f.memory_path), ('coordination', self.f.coord_path),
                           ('binding', self.f.binding), ('receipt', self.f.receipt_path)):
            availability[name] = dict(path=str(path), exists=path.exists())
            if path.exists():
                data = path.read_bytes()
                write(root / (name + '.json'), data)
                availability[name]['sha256'] = sha(data)
        write(root / 'input-availability.json', encoded(availability))
        write(root / 'rows.json', encoded(self.rows))
        write(root / 'comparison-kind.json', encoded(dict(
            kind='retained Python versus emitted Python; no Go parity claim')))
        write(root / 'event.json', self.f.raw)
        write(root / 'provider-input.json', encoded(dict(sha256=sha(self.provider.read_bytes()),
              responses=self.responses, calls=self.provider_sides)))

    def quiet_result(self, result, *, code=0, stdout=b'{}\n'):
        self.assertEqual((result.returncode, result.stdout), (code, stdout))
        if code == 0:
            self.assertEqual(result.stderr, b'')
        self.assertEqual(self.calls, [])
        self.assertEqual(self.f.api_calls(), [])
        self.assertEqual(self.provider_sides, dict(source=[], go=[]))

    def boundary_pair(self):
        # The existing body comparator requires Source phase=handle. Admission
        # and code refusals instead use the existing bound comparator, then the
        # same complete file/trace/request keyset and normalization checks.
        source, go = self.source, self.go
        before, after = self.before_run, self.after_run
        state = self.state
        rows = {}
        def launch(side, call):
            self.trace.unlink(missing_ok=True)
            result = call()
            rows[side] = dict(exit=result.returncode, stdout=result.stdout, stderr=result.stderr,
                files=self.files(), calls=copy.deepcopy(self.calls), locks=list(self.report_locks),
                trace=[json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else [])
            return result
        self.source, self.go = lambda: launch('source', source), lambda: launch('go', go)
        self.before_run, self.after_run = lambda side: None, lambda: None
        self.state = lambda: BoundMemoryCase.state(self)
        try:
            result = BoundMemoryCase.pair(self)
        finally:
            self.source, self.go, self.before_run, self.after_run, self.state = source, go, before, after, state
        self.last_raw_pair = [rows['source'], rows['go']]
        self.validate_keysets(self.last_raw_pair)
        self.assertEqual(self.normalize(rows['source'], 'source'), self.normalize(rows['go'], 'go'))
        self.retain_pair()
        return result

    def hints(self, enabled):
        self.required('Required context must not turn a tool callback into recall.')
        self.stage_tool(enabled=enabled)
        write(self.f.root / 'current.go', b'fixture retention policy\n')
        for name, fields in (
            ('PostToolUse', dict(tool_name='Read', tool_input=dict(file_path='current.go'))),
            ('PostToolUse', dict(tool_name='Grep', tool_input=dict(pattern='retention|policy', path='current.go'))),
            ('PostToolUse', dict(tool_name='Bash', tool_input=dict(command='rg retention current.go'))),
            ('PostToolUseFailure', dict(tool_name='Read', error='LeaseExpiredError EACCES LeaseExpiredError'))):
            self.event(name, **fields)
            self.quiet_result(self.pair())
            self.assertEqual(self.state().keys(), {SESSION + '.json'})
            hints = self.state()[SESSION + '.json']['hints']
            if name == 'PostToolUseFailure':
                self.assertEqual(hints['errors'], ['LeaseExpiredError', 'EACCES'])
            else:
                self.assertIn('current.go', hints['files'])
                if fields['tool_name'] != 'Read':
                    self.assertIn('retention', hints['searches'])
        self.quiet_result(self.pair())
        self.assertEqual(self.state()[SESSION + '.json']['hints']['errors'], ['LeaseExpiredError', 'EACCES'])

    def test_tool_enabled_file_search_and_error_hints(self):
        self.hints(True)

    def test_tool_disabled_file_search_and_error_hints(self):
        self.hints(False)

    def test_tool_pending_required_and_owner_state_is_not_a_recall_boundary(self):
        self.stage_tool()
        directory = Path(self.f.memory['state_dir'])
        directory.mkdir(mode=0o700)
        pending = dict(schema='cairn.inbox-recall-grants/1', session=SESSION, grants={},
                       required=dict(pending=True), opaque=['日本語', 900719925474099312345])
        write(directory / (SESSION + '.inbox-recall.json'), encoded(pending))
        self.event('PostToolUseFailure', prompt_id='not-an-inbox-lease', error='LeaseExpiredError')
        self.quiet_result(self.pair())
        self.assertEqual(self.state().keys(), {SESSION + '.json', SESSION + '.inbox-recall.json'})
        self.assertEqual(self.state()[SESSION + '.inbox-recall.json'], pending)
        self.assertEqual(self.state()[SESSION + '.json']['hints']['errors'], ['LeaseExpiredError'])

    def test_tool_no_hints_and_shell_metacharacters_keep_source_noop(self):
        self.stage_tool()
        for fields in (dict(tool_name='Read', tool_input=dict(file_path='../outside.go')),
                       dict(tool_name='Bash', tool_input=dict(command='rg retention; false')),
                       dict(tool_name='Unknown', tool_input={})):
            self.event('PostToolUse', **fields)
            self.quiet_result(self.pair())
            self.assertEqual(self.state(), {})

    def test_tool_privacy_and_account_exclusions_precede_binding_access(self):
        self.stage_tool()
        for kind in ('privacy', 'account'):
            for event in ('PostToolUse', 'PostToolUseFailure'):
                self.reset_state()
                self.event(event, tool_name='Read', tool_input=dict(file_path='current.go'), error='LeaseExpiredError')
                env = self.f.env
                marker = self.f.root / '.cairn-no-memory'
                parked = self.f.binding.with_name('parked-tool-binding')
                if kind == 'privacy':
                    write(marker, b'')
                else:
                    self.f.env = lambda: dict(env(), CLAUDE_CONFIG_DIR=str(self.root / 'other-account'))
                self.f.binding.rename(parked)
                try:
                    self.quiet_result(self.boundary_pair())
                    self.assertEqual(self.state(), {})
                finally:
                    parked.rename(self.f.binding)
                    marker.unlink(missing_ok=True)
                    self.f.env = env

    def test_tool_code_refusals_preserve_each_selected_launcher(self):
        self.stage_tool()
        for name, path in (('receipt', self.f.receipt_path), ('image', self.f.binary),
                           ('closure', Path(self.f.receipt['snapshot_root']) / 'memory.py'),
                           ('launcher', Path(self.rows['PostToolUse']['argv'][0]))):
            old = path.read_bytes()
            retained = Path(self.rows['PostToolUse']['retained_launcher'][0])
            source_old = retained.read_bytes()
            self.overwrite(path, old + b'\n')
            if name == 'launcher':
                # Each side validates its own issued launcher, not its peer's.
                self.overwrite(retained, source_old + b'\n')
            try:
                for event in ('PostToolUse', 'PostToolUseFailure'):
                    self.event(event, error='LeaseExpiredError')
                    result = self.boundary_pair()
                    self.quiet_result(result, code=1, stdout=b'')
                    self.assertEqual(result.stderr, b'Cairn lifecycle diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
                    self.assertEqual(self.state(), {})
            finally:
                self.overwrite(path, old)
                if name == 'launcher':
                    self.overwrite(retained, source_old)

    def test_tool_missing_memory_configuration_refuses_before_effects(self):
        self.stage_tool()
        parked = self.f.memory_path.with_name('parked-tool-memory')
        self.f.memory_path.rename(parked)
        try:
            for event in ('PostToolUse', 'PostToolUseFailure'):
                self.event(event, error='LeaseExpiredError')
                result = self.boundary_pair()
                self.quiet_result(result, code=1, stdout=b'')
                self.assertEqual(self.state(), {})
        finally:
            parked.rename(self.f.memory_path)

    def test_tool_exclusions_do_not_revalidate_binding_or_coordinator(self):
        self.stage_tool()
        write(self.f.root / 'current.go', b'fixture retention policy\n')
        for path in (self.f.binding, self.f.coord_path):
            parked = path.with_name('parked-unused-tool-input')
            path.rename(parked)
            try:
                for event in ('PostToolUse', 'PostToolUseFailure'):
                    self.reset_state()
                    self.event(event, tool_name='Read', tool_input=dict(file_path='current.go'), error='LeaseExpiredError')
                    self.quiet_result(self.pair())
                    self.assertEqual(self.state().keys(), {SESSION + '.json'})
                    hints = self.state()[SESSION + '.json']['hints']
                    if event == 'PostToolUse':
                        self.assertIn('current.go', hints['files'])
                    else:
                        self.assertEqual(hints['errors'], ['LeaseExpiredError'])
            finally:
                parked.rename(path)

    def test_tool_identity_workspace_and_workstream_refusals(self):
        self.stage_tool()
        for fields in (dict(session_id='not-a-session'), dict(cwd='/nonexistent/r04-tool-workspace'),
                       dict(workstream=7)):
            for event in ('PostToolUse', 'PostToolUseFailure'):
                self.reset_state()
                self.event(event, error='LeaseExpiredError', **fields)
                self.quiet_result(self.boundary_pair(), code=1, stdout=b'')
                self.assertEqual(self.state(), {})

    def test_tool_session_lock_and_invalid_error_preserve_source_refusals(self):
        self.stage_tool()
        source, go = self.source, self.go
        def locked(call):
            def invoke():
                directory = Path(self.f.memory['state_dir'])
                directory.mkdir(mode=0o700, exist_ok=True)
                with (directory / (SESSION + '.lock')).open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return call()
            return invoke
        self.source, self.go = locked(source), locked(go)
        try:
            self.event('PostToolUseFailure', error='LeaseExpiredError')
            result = self.boundary_pair()
            self.quiet_result(result, code=1, stdout=b'')
            self.assertIn(b'a memory hook is already running', result.stderr)
        finally:
            self.source, self.go = source, go
        self.reset_state()
        self.event('PostToolUseFailure', error=7)
        result = self.pair()
        self.quiet_result(result, code=1, stdout=b'')
        self.assertIn(b'error_class=TypeError', result.stderr)
        self.assertEqual(self.state(), {})

    def unsupported_consumer(self, enabled):
        self.stage_tool(enabled=enabled)
        write(self.f.root / 'current.go', b'fixture retention policy\n')
        for event in ('PostToolUse', 'PostToolUseFailure'):
            self.reset_state()
            self.event(event, tool_name='Read', tool_input=dict(file_path='current.go'), error='LeaseExpiredError')
            self.calls.clear()
            source = self.source()
            source_files = self.files()
            self.assertEqual((source.returncode, source.stdout, source.stderr), (0, b'{}\n', b''))
            self.assertIn(SESSION + '.json', self.state())
            self.reset_state()
            print('R04_BOUND_TOOL_CAPABILITY_PROBE ' + json.dumps(dict(
                selected_implementation='python', probe='direct checked Go; not emitted producer support',
                event=event, enabled=enabled, vector=self.go_vector())), flush=True)
            result = self.invoke_provider_side('go', lambda: BoundMemoryCase.go(self))
            self.quiet_result(result, code=1, stdout=b'')
            self.assertEqual(result.stderr,
                b'Cairn lifecycle diagnostic: phase=activation error_class=HookError\n'
                b'Cairn lifecycle: checked bound memory cell remains unavailable; use native tools or an explicit handoff.\n')
            self.assertEqual(self.state(), {})
            if self.evidence:
                def raw(value):
                    if isinstance(value, bytes):
                        return dict(base64=base64.b64encode(value).decode())
                    raise TypeError(type(value).__name__)
                write(self.evidence / (self._testMethodName + '-' + event + '.json'),
                    json.dumps(dict(kind='negative direct capability probe; no Go parity', event=self.f.event,
                        source=dict(exit=source.returncode, stdout=source.stdout, stderr=source.stderr, files=source_files),
                        checked_go=dict(exit=result.returncode, stdout=result.stdout, stderr=result.stderr, files=self.files()),
                        vector=self.go_vector(), rows=self.rows), default=raw).encode())

    def test_tool_enabled_checked_consumer_remains_unproduced(self):
        self.unsupported_consumer(True)

    def test_tool_disabled_checked_consumer_remains_unproduced(self):
        self.unsupported_consumer(False)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(R04BoundToolWiringTests(name) for name in sorted(
        name for name in R04BoundToolWiringTests.__dict__ if name.startswith('test_tool_')))


if __name__ == '__main__':
    unittest.main()
