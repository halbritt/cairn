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
import unittest

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
                self.assertEqual(self.state().keys(), {SESSION + '.json', SESSION + '.inbox-recall.json'})
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


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(R04BoundProviderWiringTests(name) for name in sorted(
        name for name in R04BoundProviderWiringTests.__dict__ if name.startswith('test_staged_bound_')))


if __name__ == '__main__':
    unittest.main()
