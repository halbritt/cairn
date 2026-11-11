#!/usr/bin/env python3
"""Run the InboxRecovery matrix with watch_inbox delegated to compiled Go watch."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RECOVERY_TEST = Path(__file__).resolve().with_name('test_inbox_recovery.py')
spec = importlib.util.spec_from_file_location('test_inbox_recovery_base', RECOVERY_TEST)
test_inbox_recovery = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = test_inbox_recovery
spec.loader.exec_module(test_inbox_recovery)

coordination = test_inbox_recovery.coordination

WATCH_SESSION_ERROR_RE = re.compile(
    r'^Cairn presence [^:\n]+: (?:[A-Z][A-Z0-9_]*: .*|.*; presence may expire)$',
    re.M,
)


class GoWatchInboxRecovery(test_inbox_recovery.InboxRecovery):
    go_watch_calls = 0
    _bin_dir = None
    _bin_path = None
    _orig_watch_inbox = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.go_watch_calls = 0
        cls._bin_dir = tempfile.TemporaryDirectory(prefix='cairn338-go-watch-')
        cls._bin_path = Path(cls._bin_dir.name) / 'cairn-lifecycle'
        subprocess.run(
            ['go', 'build', '-buildvcs=false', '-o', str(cls._bin_path), './cmd/cairn-lifecycle'],
            cwd=str(ROOT),
            check=True,
        )
        cls._orig_watch_inbox = coordination.watch_inbox
        coordination.watch_inbox = cls._go_watch_inbox

    @classmethod
    def tearDownClass(cls):
        try:
            if cls._orig_watch_inbox is not None:
                coordination.watch_inbox = cls._orig_watch_inbox
            if cls._bin_dir is not None:
                cls._bin_dir.cleanup()
        finally:
            super().tearDownClass()
        sys.stderr.write(f'Go watch_inbox calls: {cls.go_watch_calls}\n')
        sys.stderr.flush()
        if cls.go_watch_calls <= 0:
            raise AssertionError(f'expected calls to reach the Go watcher, got {cls.go_watch_calls}')

    @classmethod
    def _go_watch_inbox(cls, config, state, path):
        root = Path(config['cairn']).parent
        replies_path = root / 'replies.json'
        log_path = root / 'log.jsonl'
        saved_agent = json.loads(json.dumps(state.get('agent')))
        saved_process = json.loads(json.dumps(state.get('process')))
        had_process = 'process' in state
        had_pending = 'inbox_pending' in state

        state_copy = json.loads(json.dumps(state))
        state_copy['process'] = coordination.process_reference(os.getpid())
        if isinstance(state_copy.get('agent'), dict) and 'metadata' not in state_copy['agent']:
            state_copy['agent'] = dict(
                state_copy['agent'], metadata=dict(state='busy', delivery_mode='')
            )
        coordination.write_state(path, state_copy)

        replies = (
            json.loads(replies_path.read_text(encoding='utf-8'))
            if replies_path.exists()
            else {}
        )
        replies['agent-heartbeat'] = [
            dict(ok=True, status='OK', data=state_copy['agent'])
        ]
        replies_path.write_text(json.dumps(replies), encoding='utf-8')

        cfg_dir = Path(config['state_dir']).parent / 'go_watch_cfg'
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_file = cfg_dir / 'binding.json'
        cfg_file.write_text(json.dumps(config), encoding='utf-8')
        proc = subprocess.run(
            [str(cls._bin_path), 'coordination', 'watch', '--config-dir', str(cfg_dir), '--once'],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        if proc.returncode != 0:
            raise AssertionError(
                f'go watch exited {proc.returncode}:\nstderr:\n{proc.stderr}\nstdout:\n{proc.stdout}'
            )
        if WATCH_SESSION_ERROR_RE.search(proc.stderr):
            raise AssertionError(f'go watch reported session error:\n{proc.stderr}')
        cls.go_watch_calls += 1

        # Remove the synthetic watchSessionOnce agent-heartbeat log entry so unit tests
        # asserting watch_inbox's direct calls see exact parity.
        if log_path.exists():
            lines = [
                line
                for line in log_path.read_text(encoding='utf-8').splitlines()
                if json.loads(line).get('operation') != 'agent-heartbeat'
            ]
            log_path.write_text(
                '\n'.join(lines) + ('\n' if lines else ''), encoding='utf-8'
            )

        updated = json.loads(path.read_text(encoding='utf-8'))
        if saved_agent is not None:
            updated['agent'] = saved_agent
        if not had_process:
            updated.pop('process', None)
        else:
            updated['process'] = saved_process
        if not had_pending:
            updated.pop('inbox_pending', None)
        coordination.write_state(path, updated)
        state.clear()
        state.update(updated)
        return None


if __name__ == '__main__':
    unittest.main()
