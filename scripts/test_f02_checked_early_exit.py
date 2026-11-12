"""Actual H13 Source/Go checked agent_tools early-return trace processes."""
import copy
import json
from pathlib import Path
import shutil
import unittest

import test_f02_checked_ordinary as ordinary
import test_f02_hermes_recall as hermes
import test_f02_opencode_ambient as ambient


class CheckedEarlyExitTests(unittest.TestCase):
    setUpClass = classmethod(ordinary.CheckedOrdinaryTests.setUpClass.__func__)
    close_server = hermes.HermesRecallTests.close_server
    go = ordinary.CheckedOrdinaryTests.go
    go_vector = ordinary.CheckedOrdinaryTests.go_vector
    selected_vector = ordinary.CheckedOrdinaryTests.selected_vector
    go_launcher = ordinary.CheckedOrdinaryTests.go_launcher
    overwrite = ordinary.CheckedOrdinaryTests.overwrite
    repin = ordinary.CheckedOrdinaryTests.repin
    configure = hermes.HermesRecallTests.configure
    source = ambient.OpenCodeAmbientTests.source
    event = ambient.OpenCodeAmbientTests.event
    state_key = ambient.OpenCodeAmbientTests.state_key
    files = ambient.OpenCodeAmbientTests.files
    same_keys = ambient.OpenCodeAmbientTests.same_keys
    measurement = ambient.OpenCodeAmbientTests.measurement
    validate_keysets = ambient.OpenCodeAmbientTests.validate_keysets
    normalize = ambient.OpenCodeAmbientTests.normalize
    normalized_stream = ambient.OpenCodeAmbientTests.normalized_stream
    check_pull_intents = ambient.OpenCodeAmbientTests.check_pull_intents
    selection = ambient.OpenCodeAmbientTests.selection
    pairs = 0

    def setUp(self):
        ambient.OpenCodeAmbientTests.setUp(self)
        self.directory = Path(self.f.memory['state_dir'])
        self.base_env = self.f.env

    @classmethod
    def tearDownClass(cls):
        print('CHECKED_EARLY_COUNT='+json.dumps(dict(pairs=cls.pairs)), flush=True)
        ordinary.CheckedOrdinaryTests.tearDownClass.__func__(cls)

    def cell(self, harness, event, *, seed=False, label='VALID'):
        self.f.env = self.base_env
        for config, path in ((self.f.memory, self.f.memory_path), (self.f.coord, self.f.coord_path)):
            config.update(harness=harness, recall_mode='agent_tools', semantic_fallback=False)
            self.overwrite(path, ordinary.encoded(config))
        self.repin()
        self.event(event)
        if label == 'VALID':
            self.f.event['session_id'] = ('11111111-1111-4111-8111-111111111111'
                                         if harness == 'claude' else 'ses_CheckedEarly1')
        elif label == 'MISSING':
            del self.f.event['session_id']
        else:
            self.f.event['session_id'] = label
        self.f.raw = ordinary.encoded(self.f.event)
        self.restore(None)
        if seed:
            self.directory.mkdir(mode=0o750)
            self.directory.chmod(0o750)
            ordinary.write(self.directory/'preserved.json',
                           ordinary.encoded(dict(opaque=['keep', 900719925474099312345, '日本語'])), 0o640)
            ordinary.write(self.directory/'unrelated.lock', b'retained lock bytes\x00\n', 0o400)

    def snapshot(self):
        return dict(mode=self.directory.stat().st_mode & 0o777, files=self.files()) if self.directory.exists() else None

    def restore(self, snapshot):
        if self.directory.exists():
            shutil.rmtree(self.directory)
        if snapshot is not None:
            self.directory.mkdir(mode=snapshot['mode'])
            self.directory.chmod(snapshot['mode'])
            for name, item in snapshot['files'].items():
                ordinary.write(self.directory/name, item['raw'].encode(), item['mode'])

    def validate_rows(self, rows, stage):
        for row in rows:
            self.assertEqual([x['kind'] for x in row['trace']], ['entry', 'parsed', 'exit'])
            self.assertEqual(len({x['inv'] for x in row['trace']}), 1)
            for item in row['trace']:
                self.assertEqual(item['schema'], 'cairn.hook-invocation/1')
                self.assertIs(type(item['at']), int)
                self.measurement(item['at'])
                self.assertRegex(item['inv'], '^[0-9a-f]{32}$')
                if item['kind'] == 'entry':
                    for key in ('pid', 'ppid'):
                        self.assertIs(type(item[key]), int)
                        self.assertGreater(item[key], 1)
                if item['kind'] == 'exit':
                    self.assertEqual(item['phase'], 'handle', 'preflight is not Source handler parity')
                    self.assertIs(type(item['duration_ms']), int)
                    self.measurement(item['duration_ms'])
                    self.assertIs(type(item['entry_recorded']), bool)
                    self.assertTrue(item['entry_recorded'])
                    self.assertEqual(item['main_return_code'], row['exit'])
                    self.assertIn('stage', item)
                    self.assertIs(type(item['stage']), str)
                    self.assertEqual(item['stage'], stage)
            for item in row['files'].values():
                if not item['raw'].startswith('{'):
                    continue
                body = json.loads(item['raw'])
                if 'last_recall' in body:
                    for key in ('at', 'elapsed_seconds', 'duration_ms'):
                        self.measurement(body['last_recall'][key])
        # Check exact field sets, types, tuple structure and lengths BEFORE any
        # timestamp/UUID/process normalization. Stage is never synthesized.
        self.validate_keysets(rows)
        for key in ('exit', 'stdout', 'stderr'):
            self.same_keys(rows[0][key], rows[1][key], (key,))

    def pair(self, *, stage, unchanged=False):
        seed = self.snapshot()
        rows = []
        for side, launch in (('source', self.source), ('go', self.go)):
            self.restore(seed)
            self.assertEqual(self.snapshot(), seed, side+' independent seed')
            self.calls.clear()
            self.report_locks.clear()
            self.trace.unlink(missing_ok=True)
            result = launch()
            trace = [json.loads(line) for line in self.trace.read_bytes().splitlines()] if self.trace.exists() else []
            # Cast the observer list first, so copying never replays its append
            # callbacks or fabricates a second lock observation.
            rows.append(dict(exit=result.returncode, stdout=result.stdout, stderr=result.stderr,
                             files=self.files(), directory_mode=self.snapshot()['mode'] if self.directory.exists() else None,
                             calls=copy.deepcopy(list(self.calls)), trace=trace,
                             locks=list(self.report_locks)))
        type(self).pairs += 1
        print('CHECKED_EARLY_RAW_PAIR='+json.dumps(dict(test=self.id(), harness=self.f.memory['harness'],
              event=self.f.event, input_sha256=ordinary.sha(self.f.raw), input_hex=self.f.raw.hex(),
              receipt_sha256=self.f.receipt_sha, image_sha256=self.f.receipt['binary']['sha256'],
              source_members=self.f.receipt['members'], seed=seed,
              **{side:dict(row, stdout=row['stdout'].decode(), stderr=row['stderr'].decode())
                 for side, row in zip(('source', 'go'), rows)}), ensure_ascii=True), flush=True)
        if unchanged:
            for row in rows:
                self.assertEqual(dict(mode=row['directory_mode'], files=row['files']) if row['directory_mode'] is not None else None, seed)
                self.assertEqual(row['calls'], [])
                self.assertEqual(row['locks'], [])
        self.validate_rows(rows, stage)
        normalized = [self.normalize(row, side) for side, row in zip(('source', 'go'), rows)]
        self.assertEqual(rows[0]['directory_mode'], rows[1]['directory_mode'])
        self.assertEqual(normalized[0], normalized[1])
        self.assertTrue(all(row['locks'][0] for row in rows if row['locks']))
        self.last_raw_pair = rows
        return rows

    def test_disabled_and_child_returns_keep_initial_activation(self):
        for harness in ('claude', 'opencode'):
            for event in ('SessionStart', 'UserPromptSubmit'):
                for variable in ('CAIRN_LIFECYCLE_DISABLED', 'CAIRN_LIFECYCLE_CHILD'):
                    for seed in (False, True):
                        for label in ('VALID', 'not-shaped'):
                            with self.subTest(harness=harness, event=event, variable=variable, seed=seed, label=label):
                                self.cell(harness, event, seed=seed, label=label)
                                self.f.env = lambda variable=variable: dict(self.base_env(), **{variable:'1'})
                                rows = self.pair(stage='activation', unchanged=True)
                                self.assertEqual((rows[1]['exit'], rows[1]['stdout'], rows[1]['stderr']), (0, b'{}\n', b''))
                                self.assertEqual(rows[1]['trace'][-1]['outcome'], 'early_return:disabled_env')

    def test_foreign_account_policy_is_harness_specific(self):
        for harness in ('claude', 'opencode'):
            for event in ('SessionStart', 'UserPromptSubmit'):
                for variable in ('CLAUDE_CONFIG_DIR', 'CODEX_HOME', 'HERMES_HOME'):
                    for seed in (False, True):
                        with self.subTest(harness=harness, event=event, variable=variable, seed=seed):
                            self.cell(harness, event, seed=seed)
                            self.f.env = lambda variable=variable: dict(self.base_env(), **{variable:str(self.root/'foreign-account')})
                            skipped = harness == 'claude' and variable == 'CLAUDE_CONFIG_DIR'
                            rows = self.pair(stage='activation' if skipped else 'saved', unchanged=skipped)
                            self.assertEqual((rows[1]['exit'], rows[1]['stdout'], rows[1]['stderr']), (0, b'{}\n', b''))
                            self.assertEqual([op for op, _, _ in rows[1]['calls']], [] if skipped else ['version', 'index', 'recall-observation'])

    def test_scalar_invalid_and_missing_identity_keep_activation(self):
        for harness in ('claude', 'opencode'):
            for event in ('SessionStart', 'UserPromptSubmit'):
                for label in (None, '', 17, 'not-shaped', 'h'*129, 'control\nlabel', 'MISSING'):
                    for seed in (False, True):
                        with self.subTest(harness=harness, event=event, label=label, seed=seed):
                            self.cell(harness, event, seed=seed, label=label)
                            rows = self.pair(stage='activation', unchanged=True)
                            self.assertEqual((rows[1]['exit'], rows[1]['stdout']), (1, b''))
                            self.assertIn(b'phase=handle error_class=HookError', rows[1]['stderr'])

    def test_privacy_and_identity_precedence_keep_source_stages(self):
        marker = self.f.root/'.cairn-no-memory'
        for harness in ('claude', 'opencode'):
            for event in ('SessionStart', 'UserPromptSubmit'):
                for seed in (False, True):
                    for label in ('VALID', 'not-shaped'):
                        with self.subTest(harness=harness, event=event, seed=seed, label=label):
                            self.cell(harness, event, seed=seed, label=label)
                            marker.touch()
                            try:
                                rows = self.pair(stage='validated' if label == 'VALID' else 'activation', unchanged=True)
                                if label == 'VALID':
                                    self.assertEqual((rows[1]['exit'], rows[1]['stdout'], rows[1]['stderr']), (0, b'{}\n', b''))
                                    self.assertEqual(rows[1]['trace'][-1]['outcome'], 'early_return:no_memory_marker')
                                else:
                                    self.assertEqual((rows[1]['exit'], rows[1]['stdout']), (1, b''))
                            finally:
                                marker.unlink()

    def test_healthy_later_stage_survives_initialization(self):
        for harness in ('claude', 'opencode'):
            for event in ('SessionStart', 'UserPromptSubmit'):
                for seed in (False, True):
                    with self.subTest(harness=harness, event=event, seed=seed):
                        self.cell(harness, event, seed=seed)
                        rows = self.pair(stage='saved')
                        self.assertEqual((rows[1]['exit'], rows[1]['stdout'], rows[1]['stderr']), (0, b'{}\n', b''))
                        self.assertEqual([op for op, _, _ in rows[1]['calls']], ['version', 'index', 'recall-observation'])
                        self.assertEqual(rows[1]['locks'], [True])


if __name__ == '__main__':
    unittest.main(verbosity=2)
