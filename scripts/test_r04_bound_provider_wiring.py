"""Enabled bound staging through actual retained Source/Go and local providers.

Only the synthetic API and selector are fixtures. The R02 issuer, binding,
activation, SDKs and recall bodies are real. Codex agent_tools is explicitly a
transitive Go baseline; the other Source rows execute retained Python bodies.
"""
import base64
import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.error
import urllib.request

import test_f02_ordinary_provider as provider
from test_f02_bound_memory import BoundMemoryCase
import test_r04_bound_wiring as bound
import test_r04_hook_settings as hooks
from test_f04_activation import SESSION, TURN, encoded, write, sha


class R04BoundProviderWiringTests(provider.OrdinaryProviderTests):
    go_vector = BoundMemoryCase.go_vector
    go_launcher = BoundMemoryCase.go_launcher
    timeout = BoundMemoryCase.timeout
    @classmethod
    def setUpClass(cls):
        # Build all images from this checkout, including its R02 issuer.
        BoundMemoryCase.setUpClass.__func__(cls)
        cls.evidence = Path(os.environ['R04_BOUND_EVIDENCE_DIR']) if os.environ.get('R04_BOUND_EVIDENCE_DIR') else None
        if cls.evidence:
            cls.evidence.mkdir(mode=0o700)
            images = cls.evidence / 'images'
            images.mkdir(mode=0o700)
            pins = {}
            for name, image in (('lifecycle', cls.binary), ('stager', cls.stager), ('cairn', cls.cli)):
                held = images / name
                shutil.copyfile(image, held)
                held.chmod(0o500)
                pins[name] = dict(sha256=sha(held.read_bytes()), build_image=str(image),
                    build_info=subprocess.check_output(['go', 'version', '-m', str(image)], text=True))
            write(images / 'pins.json', encoded(pins), 0o400)
            root = Path(__file__).resolve().parents[1]
            write(cls.evidence / 'inputs.json', encoded(dict(
                source=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
                clean=not subprocess.check_output(['git', 'status', '--porcelain'], cwd=root),
                go_version=subprocess.check_output(['go', 'version'], text=True).strip(),
                python=dict(executable=sys.executable, version=sys.version),
                dependencies={name: sha((root / name).read_bytes()) for name in ('go.mod', 'go.sum')}
            )), 0o400)

    tearDownClass = classmethod(BoundMemoryCase.tearDownClass.__func__)

    def setUp(self):
        super().setUp()
        old = self.f
        # The ordinary fixture deliberately forces unbound profiles. Keep its
        # external API/provider engines and issue a real bound HookFixture.
        self.f = hooks.HookFixture(self.root / 'bound project', self.binary, stager=self.stager)
        for key in ('cairn', 'socket', 'token_file', 'claude', 'model', 'preview_model',
                    'recall_model', 'semantic_fallback', 'recall_observations'):
            if key in old.memory:
                self.f.memory[key] = old.memory[key]
        for key in ('cairn', 'socket', 'token_file'):
            self.f.coord[key] = old.coord[key]
        original = self.f.env
        self.f.env = lambda: dict(original(), CLAUDECODE='synthetic-parent')
        self.original_memory = copy.deepcopy(self.f.memory)
        self.case_number = 0

    def stage_bound(self, harness, mode, *, enabled=True, remove_package=True, changes=None, remove_fields=()):
        self.f.memory = copy.deepcopy(self.original_memory)
        self.f.memory.update(changes or {})
        for field in remove_fields:
            self.f.memory.pop(field, None)
        metadata = bound.R04BoundWiringTests.stage_rows(
            self, harness, mode, enabled=enabled, remove_package=remove_package)
        self.trace = self.f.memory_path.parent / 'invocation-trace' / 'trace.jsonl'
        self.trace.parent.mkdir(mode=0o700, exist_ok=True)
        self.assertIs(json.loads(self.f.binding.read_bytes())['enabled'], enabled)
        self.assertTrue(self.f.memory['inbox_recall_binding'])
        for event in ('SessionStart', 'UserPromptSubmit'):
            selected_go = mode == 'agent_tools'
            self.assertEqual(self.rows[event]['implementation'], 'go' if selected_go else 'python')
            self.assertEqual(self.rows[event]['hook']['timeout'], 13)
            if selected_go:
                self.assertNotEqual(self.rows[event]['checked_launcher'], self.rows[event]['retained_launcher'])
            else:
                self.assertEqual(self.rows[event]['checked_launcher'], self.rows[event]['retained_launcher'])
        print('R04_BOUND_SELECTION ' + json.dumps(dict(test=self.id(), harness=harness, mode=mode,
            enabled=enabled, receipt_sha256=self.f.receipt_sha, image_sha256=self.f.receipt['binary']['sha256'],
            source_members=self.f.receipt['members'], rows=self.rows,
            source_baseline='transitive pinned Go' if harness == 'codex' and mode == 'agent_tools'
                            else 'retained Python frontend; body entry checked by paired-body trace')), flush=True)
        return metadata

    def source(self):
        return self.invoke_provider_side('source', lambda: bound.R04BoundWiringTests.staged_process(self, 'source'))

    def event(self, name, **fields):
        if self.f.memory['harness'] == 'claude':
            fields.setdefault('prompt_id', 'native-r04-fixture-prompt')
        return super().event(name, **fields)

    def go(self):
        selected_go = self.rows[self.f.event['hook_event_name']]['implementation'] == 'go'
        print('R04_BOUND_COMPARISON ' + json.dumps(dict(event=self.f.event['hook_event_name'],
            selected_go=selected_go, direct_checked_capability_probe=not selected_go)), flush=True)
        if selected_go:
            invoke = lambda: bound.R04BoundWiringTests.staged_process(self, 'go')
        else:
            # Capability/refusal comparison only: this direct checked consumer
            # is not selected by the staged settings for unsupported modes.
            invoke = lambda: BoundMemoryCase.go(self)
        return self.invoke_provider_side('go', invoke)

    def invoke_provider_side(self, side, invoke):
        result = super().invoke_provider_side(side, invoke)
        log = self.root / 'provider.calls'
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        print('R04_BOUND_PROVIDER ' + json.dumps(dict(test=self.id(), side=side, calls=rows)), flush=True)
        print('R04_BOUND_PROCESS ' + json.dumps(dict(test=self.id(), side=side,
            input=self.f.raw.decode(), exit=result.returncode,
            stdout=result.stdout.decode(), stderr=result.stderr.decode())), flush=True)
        return result

    def normalize(self, row, side):
        row = copy.deepcopy(row)
        delegated = (self.f.memory['harness'] == 'codex' and self.f.memory.get('recall_mode') == 'agent_tools')
        for _, _, headers in row['calls']:
            token = headers['Cairn-Client-Diagnostics']
            diagnostic = json.loads(base64.urlsafe_b64decode(token + '=' * (-len(token) % 4)))
            expected = dict(component='lifecycle-memory', basis='reported')
            if side == 'go' or delegated:
                expected['implementation_id'] = self.f.receipt['binary']['sha256']
            self.assertEqual(diagnostic['origin'], expected, 'raw origin must be checked before normalization')
            print('R04_BOUND_ORIGIN ' + json.dumps(dict(side=side, origin=diagnostic['origin'])), flush=True)
            if side == 'source' and not delegated:
                # The retained R02 launcher reports unknown Source identity;
                # adapt only to the existing standalone-body comparison helper.
                diagnostic['origin']['implementation_id'] = self.f.receipt['members']['memory-hook.py']['sha256']
                headers['Cairn-Client-Diagnostics'] = base64.urlsafe_b64encode(encoded(diagnostic)).decode().rstrip('=')
        return super().normalize(row, side)

    def pair(self):
        result = super().pair()
        self.retain_pair()
        return result

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
        write(root / 'pair.json', json.dumps(self.last_raw_pair, default=raw).encode(), 0o600)
        for name, path in (('memory.json', self.f.memory_path), ('coordination.json', self.f.coord_path),
                           ('binding.json', self.f.binding), ('receipt.json', self.f.receipt_path)):
            write(root / name, path.read_bytes(), 0o600)
        write(root / 'rows.json', encoded(self.rows), 0o600)
        write(root / 'provider-input.json', encoded(dict(path=str(self.provider),
            sha256=sha(self.provider.read_bytes()), responses=self.responses,
            calls=self.provider_sides)), 0o600)

    def required(self, body='Required lifecycle recall policy. 日本語 🛡.'):
        super().required(body)
        # The API serializes these zero-value Record fields even when the
        # small external fixture omits them. Supply the complete fixture shape
        # so the assertion preserves every returned field, not just body text.
        self.selected[0]['record'].update({
            'class': '', 'lifecycle': '', 'sensitivity': '', 'kind': '',
            'scope': {'repo': '', 'task_id': '', 'run_id': ''},
            'claim_type': '', 'observed_writer': '', 'witness': '',
            'written_at': '0001-01-01T00:00:00Z', 'attribution_state': ''})

    def recall_pair(self, harness, mode, enabled=True):
        self.required('Required checked lifecycle policy. 日本語 🛡.')
        self.optional_note('Use PostgreSQL for durable lifecycle storage. 日本語 😀')
        self.stage_bound(harness, mode, enabled=enabled)
        for event in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=event):
                self.reset_state()
                self.event(event, session_id=SESSION, turn_id=TURN, source='startup', prompt='storage')
                result = self.pair()
                self.assertEqual((result.returncode, result.stderr), (0, b''))
                context = self.context(result)
                self.assertEqual(context['selected'][0]['record'], self.selected[0]['record'])
                self.assertTrue(context['selected'][0]['mandatory'])
                if mode != 'agent_tools':
                    self.assertEqual(context['expanded']['selection']['record']['body'], self.optional[0]['body'])
                    self.assertEqual([row['stage'] for row in self.provider_sides['source']], ['recall'])
                    self.assertEqual(self.recall_status()['discovery'], 'verified')
                else:
                    self.assertEqual(self.provider_sides['go'], [])
                self.assertIn('recall-observation', [op for op, _, _ in self.calls])

    def test_staged_bound_claude_agent_tools(self):
        self.recall_pair('claude', 'agent_tools')

    def test_staged_bound_claude_prompt_owner_does_not_refund_grant(self):
        self.required()
        self.optional_note('Current optional retention guidance. 日本語 😀')
        self.stage_bound('claude', 'agent_tools')
        self.assertFalse(self.f.package.exists())
        self.event('SessionStart', session_id=SESSION, turn_id=TURN, source='startup', prompt='')
        self.assertEqual(self.pair().returncode, 0)
        self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN,
                   prompt_id='first-staged-owner', prompt='Inspect current retention policy.')
        first = self.pair()
        self.assertEqual(first.returncode, 0)
        self.assertEqual(self.context(first)['candidate_bodies'][0]['response']['selection']['record']['body'],
                         self.optional[0]['body'])
        ledger_name = SESSION + '.inbox-recall.json'
        before = self.state()[ledger_name]
        grant = before['grants'][before['active']]
        self.assertGreater(grant['output_bytes'], 0)
        self.assertGreater(grant['native_allowance_bytes'], 0)
        repeated = self.pair()
        self.assertEqual((repeated.returncode, repeated.stderr), (0, b''))
        self.assertNotIn(b'candidate_bodies', repeated.stdout)
        after = self.state()[ledger_name]
        self.assertEqual(len(after['grants']), 1)
        self.assertEqual(after['active'], before['active'])
        self.assertEqual(after['grants'][after['active']]['output_bytes'], grant['output_bytes'])
        self.assertEqual(after['grants'][after['active']]['native_allowance_bytes'], grant['native_allowance_bytes'])
        self.assertEqual(self.provider_sides, dict(source=[], go=[]))

    def test_staged_bound_disabled_claude_agent_tools_start_and_prompt(self):
        self.required('Required checked lifecycle policy. 日本語 🛡.')
        self.optional_note('Use PostgreSQL for durable lifecycle storage. 日本語 😀')
        self.stage_bound('claude', 'agent_tools', enabled=False)
        self.assertFalse(self.f.package.exists())
        for event in ('SessionStart', 'UserPromptSubmit'):
            with self.subTest(event=event):
                self.reset_state()
                self.event(event, session_id=SESSION, turn_id=TURN, source='startup', prompt='storage')
                result = self.pair()
                self.assertEqual((result.returncode, result.stderr), (0, b''))
                context = self.context(result)
                self.assertEqual(context['selected'][0]['record'], self.selected[0]['record'])
                self.assertEqual(context['candidate_bodies'][0]['response']['selection']['record']['body'],
                                 self.optional[0]['body'])
                self.assertEqual(self.state().keys(), {SESSION + '.json'})
                self.assertEqual([op for op, _, _ in self.calls], ['version', 'index', 'expand', 'recall-observation'])
                self.assertEqual(self.provider_sides, dict(source=[], go=[]))

    def test_staged_bound_disabled_claude_agent_tools_owner_repeat_and_wake(self):
        self.required()
        self.optional_note('Current optional retention guidance. 日本語 😀')
        self.stage_bound('claude', 'agent_tools', enabled=False)
        self.assertFalse(self.f.package.exists())
        self.state = lambda: BoundMemoryCase.state(self)
        self.event('SessionStart', session_id=SESSION, turn_id=TURN, source='startup', prompt='')
        self.assertEqual(self.pair().returncode, 0)
        self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN,
                   prompt_id='disabled-staged-owner', prompt='Inspect current retention policy.')
        first = self.pair()
        self.assertEqual(first.returncode, 0)
        self.assertEqual(self.context(first)['candidate_bodies'][0]['response']['selection']['record']['body'],
                         self.optional[0]['body'])
        ordinary = self.state()[SESSION + '.json']
        repeated = self.pair()
        self.assertEqual((repeated.returncode, repeated.stderr), (0, b''))
        self.assertEqual(self.state().keys(), {SESSION + '.json'})
        self.assertEqual(self.state()[SESSION + '.json']['seen'], ordinary['seen'])
        self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN,
                   prompt_id='disabled-staged-wake', prompt=self.f.wake())
        for repeat in (False, True):
            with self.subTest(repeat=repeat):
                result = self.pair()
                self.assertEqual((result.returncode, result.stderr), (0, b''))
                self.assertEqual(self.context(result)['selected'][0]['record'], self.selected[0]['record'])
                self.assertEqual(self.state().keys(), {
                    SESSION + '.json', SESSION + '.inbox-recall.json', SESSION + '.inbox-recall.initialized'})
                ledger = self.state()[SESSION + '.inbox-recall.json']
                self.assertEqual(ledger['grants'], {})
                self.assertEqual(ledger['inbox_activation'], {'claude-channel:disabled-staged-wake': False})
                self.assertEqual([op for op, _, _ in self.calls], ['version', 'index', 'recall-observation'])
                self.assertEqual(self.provider_sides, dict(source=[], go=[]))

    def test_staged_bound_claude_prompt_wake_keeps_exact_parent_keys(self):
        self.stage_bound('claude', 'agent_tools')
        self.assertFalse(self.f.package.exists())
        self.state = lambda: BoundMemoryCase.state(self)
        self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN,
                   prompt_id='staged-native-wake', prompt=self.f.wake())
        first_state = None
        for repeat in (False, True):
            with self.subTest(repeat=repeat):
                result = self.pair()
                self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
                self.assertEqual(self.state().keys(), {
                    SESSION + '.inbox-recall.initialized', SESSION + '.inbox-recall.json'})
                self.assertEqual(self.calls, [])
                self.assertEqual(self.provider_sides, dict(source=[], go=[]))
                if repeat:
                    self.assertEqual(self.state(), first_state)
                else:
                    first_state = self.state()
                directory = Path(self.f.memory['state_dir'])
                for name in self.files():
                    if name.endswith('.lock'):
                        with (directory / name).open('r+') as lock:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            fcntl.flock(lock, fcntl.LOCK_UN)

    def test_staged_bound_claude_ambient(self):
        self.refused_mode_pair('claude', 'ambient')

    def test_staged_bound_claude_default(self):
        self.refused_mode_pair('claude', None)

    def test_staged_bound_codex_ambient(self):
        self.refused_mode_pair('codex', 'ambient')

    def test_staged_bound_codex_default(self):
        self.refused_mode_pair('codex', None)

    def refused_mode_pair(self, harness, mode):
        self.stage_bound(harness, mode)
        for event in ('SessionStart', 'UserPromptSubmit'):
            self.reset_state()
            self.event(event, session_id=SESSION, turn_id=TURN, prompt='storage')
            self.calls.clear()
            results = [self.source(), self.go()]
            self.assertEqual((results[0].returncode, results[0].stdout, results[0].stderr),
                             (results[1].returncode, results[1].stdout, results[1].stderr))
            self.assertEqual((results[0].returncode, results[0].stdout), (1, b''))
            self.assertIn(b'phase=activation error_class=ValueError', results[0].stderr)
            self.assertEqual(self.calls, [])
            self.assertEqual(self.provider_sides['go'], [])
            self.assertEqual(self.state(), {})

    def test_staged_bound_codex_agent_tools_transitive_baseline(self):
        self.recall_pair('codex', 'agent_tools')

    def test_staged_bound_disabled_claude(self):
        self.recall_pair('claude', 'ambient', enabled=False)

    def test_staged_bound_disabled_codex(self):
        self.recall_pair('codex', None, enabled=False)

    def test_staged_bound_required_conflict_preserves_source_boundary(self):
        for harness in ('claude', 'codex'):
            self.stage_bound(harness, 'agent_tools', remove_package=False)
            self.refusal = 'OPEN_CONFLICT'
            for event in ('SessionStart', 'UserPromptSubmit'):
                with self.subTest(harness=harness, event=event):
                    self.reset_state()
                    self.event(event, session_id=SESSION, turn_id=TURN, prompt='Restore required storage policy.')
                    result = self.pair()
                    self.assertTrue(result.stderr)
                    if event == 'UserPromptSubmit':
                        self.assertEqual((result.returncode, result.stdout), (2, b''))
                    self.assertEqual(self.provider_sides['go'], [])

    def test_staged_bound_disabled_provider_and_model_errors(self):
        self.required()
        self.optional_note('Concrete current storage guidance.')
        for harness in ('claude', 'codex'):
            for changes, error, remove_fields in (
                    (dict(claude=None), 'TypeError', ()),
                    (dict(model=1), 'TypeError', ('recall_model',)),
                    (dict(model='bad\0model'), 'ValueError', ('recall_model',)),
                    (dict(recall_model=None), None, ())):
                with self.subTest(harness=harness, changes=changes):
                    self.reset_state()
                    self.stage_bound(harness, 'ambient', enabled=False, remove_package=False,
                                     changes=changes, remove_fields=remove_fields)
                    self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN, prompt='storage')
                    result = self.pair()
                    if error:
                        self.assertEqual((result.returncode, result.stdout), (1, b''))
                        self.assertIn(('error_class=' + error).encode(), result.stderr)
                        self.assertEqual(self.provider_sides['go'], [])
                    else:
                        self.assertEqual(result.returncode, 0)
                        self.assertEqual(self.context(result)['selected'][0]['record'], self.selected[0]['record'])
                        self.assertNotIn('expanded', self.context(result))
                        self.assertEqual(self.recall_status()['rejected'], dict(verification_unavailable=1))
                        self.assertEqual(self.provider_sides['go'], [])

    def test_staged_bound_disabled_invalid_selection_retains_whole_required(self):
        self.required()
        self.optional_note('Concrete current storage guidance.')
        self.responses['recall'] = dict(result=dict(structured_output=dict(index='0')))
        for harness in ('claude', 'codex'):
            self.reset_state()
            self.stage_bound(harness, 'ambient', enabled=False, remove_package=False)
            self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN, prompt='storage')
            result = self.pair()
            self.assertEqual(self.context(result)['selected'][0]['record'], self.selected[0]['record'])
            self.assertNotIn('expanded', self.context(result))
            self.assertEqual(self.recall_status()['rejected'], dict(invalid_verdict=1))
            self.assertEqual(self.state()[SESSION + '.json']['seen'], {})

    def test_staged_bound_claude_start_privacy_account_and_session_lock(self):
        self.stage_bound('claude', 'agent_tools')
        self.event('SessionStart', session_id=SESSION, source='startup', prompt='storage')
        self.state = lambda: BoundMemoryCase.state(self)
        observed = {}

        def before(side):
            self.boundary_side = side
            self.boundary_offset = len(self.trace.read_text().splitlines()) if self.trace.exists() else 0

        def after():
            lines = self.trace.read_text().splitlines() if self.trace.exists() else []
            observed[self.boundary_side] = dict(files=self.files(), calls=copy.deepcopy(self.calls),
                trace=[json.loads(line) for line in lines[self.boundary_offset:]], locks=[])

        self.before_run, self.after_run = before, after
        for kind in ('privacy', 'account', 'session-lock'):
            with self.subTest(boundary=kind):
                self.reset_state({})
                original_env, source, go = self.f.env, self.source, self.go
                marker = self.f.root / '.cairn-no-memory'
                parked = self.f.binding.with_name('parked-start-binding.json')
                if kind == 'privacy':
                    write(marker, b'')
                if kind == 'account':
                    self.f.env = lambda: dict(original_env(), CLAUDE_CONFIG_DIR=str(self.root / 'other-account'))
                if kind != 'session-lock':
                    self.f.binding.rename(parked)
                else:
                    def locked(launch):
                        def invoke():
                            directory = Path(self.f.memory['state_dir'])
                            directory.mkdir(mode=0o700, exist_ok=True)
                            with (directory / (SESSION + '.lock')).open('a') as lock:
                                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                return launch()
                        return invoke
                    # Acquire after the parent restores each side's snapshot;
                    # never unlink an already-held lock between Source and Go.
                    self.source, self.go = locked(source), locked(go)
                try:
                    result = BoundMemoryCase.pair(self)
                    self.assertEqual((result.returncode, result.stdout),
                                     (1, b'') if kind == 'session-lock' else (0, b'{}\n'))
                    if kind == 'session-lock':
                        self.assertIn(b'a memory hook is already running', result.stderr)
                    else:
                        self.assertEqual(result.stderr, b'')
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.state(), {})
                    for row in observed.values():
                        row.update(exit=result.returncode, stdout=result.stdout, stderr=result.stderr)
                    self.validate_keysets([observed['source'], observed['go']])
                    self.assertEqual(self.normalize(observed['source'], 'source'),
                                     self.normalize(observed['go'], 'go'))
                    print('R04_BOUND_START_BOUNDARY ' + json.dumps(dict(boundary=kind,
                        source_trace=observed['source']['trace'], go_trace=observed['go']['trace'])), flush=True)
                finally:
                    self.source, self.go, self.f.env = source, go, original_env
                    marker.unlink(missing_ok=True)
                    if parked.exists():
                        parked.rename(self.f.binding)

    def test_staged_bound_activation_wake_privacy_account_and_locks(self):
        # These boundaries intentionally stop before the Source recall body.
        # Reuse the existing exact-stream/state comparator without claiming
        # preflight refusal as provider-body evidence.
        self.before_run = lambda side: None
        self.after_run = lambda: None
        self.state = lambda: BoundMemoryCase.state(self)
        for harness in ('claude', 'codex'):
            self.stage_bound(harness, 'agent_tools', remove_package=False)
            # Both supported agent_tools prompt rows execute their emitted Go
            # command against the retained Source callback and restored state.
            self.reset_state()
            self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN, prompt=self.f.wake())
            first = BoundMemoryCase.pair(self)
            self.assertEqual(first.stdout, b'{}\n')
            ledger_name = SESSION + ('.inbox-recall.json' if harness == 'claude' else '.memory-budget.json')
            ledger = self.state()[ledger_name]
            self.assertEqual(list(ledger['inbox_activation'].values()), [True])
            self.assertEqual(BoundMemoryCase.pair(self).stdout, b'{}\n')
            self.assertEqual(self.state()[ledger_name], ledger)
            for kind in ('privacy', 'account'):
                self.reset_state()
                self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN, prompt='storage')
                original = self.f.env
                marker = self.f.root / '.cairn-no-memory'
                parked = self.f.binding.with_name('parked-binding.json')
                if kind == 'privacy':
                    write(marker, b'')
                else:
                    variable = 'CODEX_HOME' if harness == 'codex' else 'CLAUDE_CONFIG_DIR'
                    self.f.env = lambda: dict(original(), **{variable: str(self.root / 'other-account')})
                self.f.binding.rename(parked)
                try:
                    result = BoundMemoryCase.pair(self)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b'{}\n', b''))
                    self.assertEqual(self.calls, [])
                    self.assertEqual(self.state(), {})
                finally:
                    parked.rename(self.f.binding)
                    marker.unlink(missing_ok=True)
                    self.f.env = original
            directory = Path(self.f.memory['state_dir'])
            directory.mkdir(mode=0o700, exist_ok=True)
            for wake in (False, True):
                self.event('UserPromptSubmit', session_id=SESSION, turn_id=TURN,
                           prompt=self.f.wake() if wake else 'storage')
                with (directory / (SESSION + '.lock')).open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.calls.clear()
                    results = [self.source(), self.go()]
                self.assertEqual((results[0].returncode, results[0].stdout, results[0].stderr),
                                 (results[1].returncode, results[1].stdout, results[1].stderr))
                self.assertEqual((results[0].returncode, results[0].stdout), (1, b''))
                self.assertEqual(self.calls, [])
                self.assertEqual(self.state(), {})

    def reset_state(self, seed=None):
        if seed is None:
            return super().reset_state()
        return BoundMemoryCase.reset_state(self, seed)


class NativeClaudeProviderTests(unittest.TestCase):
    """Local wire-protocol controls; native execution is qualified separately."""

    def setUp(self):
        from check_r04_native_claude import MessagesFixture
        self.paths = (Path('/owned/read.txt'), Path('/owned/missing.txt'))
        self.server = MessagesFixture(read_paths=self.paths)
        self.addCleanup(self.server.close)

    def request(self, messages, tools=()):
        body = dict(model='r04-synthetic-model', messages=messages,
                    tools=[dict(name=name) for name in tools])
        request = urllib.request.Request(self.server.url + '/v1/messages', data=encoded(body),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            return [json.loads(line[6:]) for line in response.read().decode().splitlines()
                    if line.startswith('data: ')]

    def test_zero_recall_native_pair_reaches_complete_borrowed_comparator(self):
        from check_r04_native_claude import native_tool_comparator
        from test_f02_opencode_ambient import OpenCodeAmbientTests
        ledger = encoded(dict(schema='cairn.inbox-recall-grants/1', required=dict(pending=True),
                              opaque=['日本語 🛡️ 😀', 900719925474099312345])).decode()
        for event in ('PostToolUse', 'PostToolUseFailure'):
            with self.subTest(event=event):
                comparator = native_tool_comparator(SimpleNamespace(event=dict(hook_event_name=event)))
                self.assertIs(type(comparator), OpenCodeAmbientTests)

                def row(inv, pid, at):
                    hints = (dict(files={'fixture.txt': at}) if event == 'PostToolUse'
                             else dict(errors=['FixtureError'], error_at=at))
                    return dict(exit=0, stdout=b'{}\n', stderr=b'', calls=[], locks=[True],
                        files={'session.json': dict(mode=0o600, raw=encoded(dict(hints=hints)).decode()),
                               'session.inbox-recall.json': dict(mode=0o600, raw=ledger)},
                        trace=[dict(kind='entry', inv=inv, at=at, pid=pid, ppid=pid-1),
                               dict(kind='parsed', inv=inv, at=at, event=event, session='session'),
                               dict(kind='exit', inv=inv, at=at, duration_ms=1, event=event,
                                    session='session', phase='handle', main_return_code=0)])

                pair = [row('a'*32, 101, 100), row('b'*32, 201, 200)]
                original = copy.deepcopy(pair)
                comparator.validate_keysets(pair)
                source, go = (comparator.normalize(value, side)
                              for value, side in zip(pair, ('source', 'go')))
                self.assertEqual(source, go)
                self.assertEqual(set(source), {'exit', 'stdout', 'stderr', 'files', 'requests', 'trace', 'locks'})
                self.assertEqual((source['exit'], source['stdout'], source['stderr'], source['requests'], source['locks']),
                                 (0, '{}\n', '', [], [True]))
                self.assertEqual(source['files']['session.inbox-recall.json']['raw'], ledger)
                self.assertEqual(pair, original, 'complete normalization must retain raw evidence')
                changed = copy.deepcopy(pair[1])
                changed['stderr'] = b'unexpected diagnostic'
                self.assertNotEqual(source, comparator.normalize(changed, 'go'))

    def test_default_keeps_empty_tools_and_plain_text(self):
        events = self.request([dict(role='user', content='ordinary fixture prompt')])
        self.assertEqual(next(e['content_block']['type'] for e in events
                              if e['type'] == 'content_block_start'), 'text')
        self.assertEqual(next(e['delta']['stop_reason'] for e in events
                              if e['type'] == 'message_delta'), 'end_turn')
        self.assertEqual(self.server.read_results, {})
        self.assertEqual(self.server.failures, [])

    def test_scripted_read_requires_matching_result_and_runs_before_once(self):
        before = []
        ident = self.server.script_read('read fixture', self.paths[0], lambda: before.append('before'))
        messages = [dict(role='user', content='read fixture')]
        events = self.request(messages, ('Read',))
        block = next(e['content_block'] for e in events if e['type'] == 'content_block_start')
        self.assertEqual((block['type'], block['id'], block['name']), ('tool_use', ident, 'Read'))
        arguments = json.loads(next(e['delta']['partial_json'] for e in events
                                   if e['type'] == 'content_block_delta'))
        self.assertEqual(arguments, dict(file_path=str(self.paths[0])))
        result = dict(type='tool_result', tool_use_id=ident, content='fixture result')
        events = self.request(messages + [dict(role='user', content=[result])], ('Read',))
        self.assertEqual(before, ['before'])
        self.assertEqual(self.server.read_results, {ident: result})
        self.assertEqual(next(e['delta']['stop_reason'] for e in events
                              if e['type'] == 'message_delta'), 'end_turn')
        self.assertIsNone(self.server.pending_read)

    def test_read_path_and_single_pending_call_are_bounded(self):
        with self.assertRaises(ValueError):
            self.server.script_read('outside', Path('/outside/secret'), lambda: None)
        self.server.script_read('one', self.paths[0], lambda: None)
        with self.assertRaises(ValueError):
            self.server.script_read('second', self.paths[1], lambda: None)
        self.assertEqual(self.server.requests, [])

    def test_script_refuses_other_tools_before_issuing_read(self):
        before = []
        self.server.script_read('read fixture', self.paths[0], lambda: before.append('before'))
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request([dict(role='user', content='read fixture')], ('Read', 'Bash'))
        self.assertEqual(error.exception.code, 500)
        error.exception.close()
        self.assertEqual(before, [])
        self.assertFalse(self.server.pending_read['issued'])
        self.assertEqual(len(self.server.failures), 1)

    def test_self_reported_text_does_not_complete_native_tool_result(self):
        self.server.script_read('read fixture', self.paths[1], lambda: None)
        messages = [dict(role='user', content='read fixture')]
        self.request(messages, ('Read',))
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request(messages + [dict(role='assistant', content='I read the file')], ('Read',))
        self.assertEqual(error.exception.code, 500)
        error.exception.close()
        self.assertEqual(self.server.read_results, {})
        self.assertEqual(len(self.server.failures), 1)

    def test_equal_launcher_argv_is_disambiguated_by_native_stdin(self):
        from check_r04_native_claude import native_hook_io
        trace = ''
        for pid, event in [('7', 'UserPromptSubmit'), ('8', 'PostToolUse')]:
            raw = encoded(dict(hook_event_name=event, tool_name='Read'))
            trace += f'{pid} read(0, {json.dumps(raw.decode())}, 4096) = {len(raw)}\n'
            trace += f'{pid} write(1, "{{}}\\n", 3) = 3\n'
        rows = native_hook_io(trace, [dict(pid='7'), dict(pid='8')], 'PostToolUse')
        self.assertEqual(set(rows), {'8'})
        self.assertEqual(rows['8']['stdin']['payload']['hook_event_name'], 'PostToolUse')
        self.assertEqual(base64.b64decode(rows['8']['stdout']['base64']), b'{}\n')

    def test_truncated_native_stdin_cannot_be_dropped_as_another_event(self):
        from check_r04_native_claude import native_hook_io
        with self.assertRaisesRegex(AssertionError, 'truncated kernel I/O'):
            native_hook_io('7 read(0, "{}"..., 500) = 500\n', [dict(pid='7')], 'PostToolUse')

    def test_selected_raw_stdio_roundtrips_without_dumping_regular_file(self):
        from check_r04_native_claude import native_hook_io
        from check_r04_native_opencode import successful_execs
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = b'regular-file-payload-must-not-be-dumped-' * 4096
            regular, trace = root / 'regular', root / 'trace'
            regular.write_bytes(marker)
            raw = json.dumps(dict(hook_event_name='PostToolUse',
                opaque=['日本語 😀', 900719925474099312345]), ensure_ascii=False).encode()
            code = ('import os,sys; open(sys.argv[1],"rb").read(); '
                    'data=os.read(0,4096); os.write(1,data); os.write(2,b"diag")')
            result = subprocess.run(['strace', '-f', '-s', '16384', '-e',
                'trace=execve,clone,clone3,read,write', '-e', 'raw=read,write',
                '-e', 'read=0', '-e', 'write=1,2', '-o', str(trace),
                sys.executable, '-I', '-S', '-c', code, str(regular)],
                input=raw, capture_output=True, timeout=10)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, raw, b'diag'))
            captured = trace.read_text()
            self.assertNotIn(marker[:80].decode(), captured)
            executions = list(successful_execs(captured))
            self.assertEqual(len(executions), 1)
            row = native_hook_io(captured, executions, 'PostToolUse', raw_stdio=True)[executions[0]['pid']]
            self.assertEqual(base64.b64decode(row['stdin']['base64']), raw)
            self.assertEqual(base64.b64decode(row['stdout']['base64']), raw)
            self.assertEqual(base64.b64decode(row['stderr']['base64']), b'diag')

    def test_raw_stdio_joins_resume_and_ignores_foreign_pending_client(self):
        from check_r04_native_claude import stdio_trace
        from r04_agy_trace import io_bytes
        dump = ' | 00000  ' + '7b 7d 0a'.ljust(48) + '  {}.              |\n'
        trace = ('9 read(0, 0x999, 0x1000 <unfinished ...>\n'
                 '7 read(0, 0xabc, 0x1000 <unfinished ...>\n'
                 '7 <... read resumed>) = 0x3\n' + dump)
        self.assertEqual(io_bytes(stdio_trace(trace, ['7']), ['7'], 0, 'read'), b'{}\n')
        for malformed in (trace.replace(dump, ''), trace.replace('00000', '00001'),
                          trace.replace('7b 7d', 'zz 7d'), trace.replace('= 0x3', '= 0x4'),
                          '7 <... read resumed>) = 0x3\n' + dump):
            with self.subTest(trace=malformed), self.assertRaises(AssertionError):
                stdio_trace(malformed, ['7'])


def load_tests(loader, tests, pattern):
    suite = unittest.TestSuite(R04BoundProviderWiringTests(name) for name in sorted(
        name for name in R04BoundProviderWiringTests.__dict__ if name.startswith('test_staged_bound_')))
    suite.addTests(loader.loadTestsFromTestCase(NativeClaudeProviderTests))
    return suite


if __name__ == '__main__':
    unittest.main()
