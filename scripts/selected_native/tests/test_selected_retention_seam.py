"""Integration seam: unchanged selected-retention V2 + candidate profile reader + terminal/custody routing.
Constructed events and a disposable temp directory only; retention.py is the accepted source, unmodified."""
import json
from pathlib import Path as _Path
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from .. import terminal_seam as seam
import trial_native_observation as tno
import trial_source_delivery as tsd
from ..frames import Frames, Profile
from ..retention import ExitSnapshot, Intent, Retention, RetentionError, inspect_run
from . import test_selected_coding as t

INTENT = Intent(t.S, 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64)
EXIT0 = ExitSnapshot(123, 456, '80000000-0000-4000-8000-000000000001', 0, False)
EXIT1 = ExitSnapshot(123, 456, '80000000-0000-4000-8000-000000000001', 1, False)


def stream_ok():
    hooks = t.HookChannel
    return [{'type': 'system', 'subtype': 'hook_started', 'hook_id': 'h1', 'hook_event': 'SessionStart', 'session_id': t.S},
            t.init(),
            {'type': 'system', 'subtype': 'hook_response', 'hook_id': 'h1', 'hook_event': 'SessionStart',
             'stdout': hooks.STDOUT, 'outcome': 'success', 'exit_code': 0, 'session_id': t.S},
            t.message('assistant', [t.use_block('a', 'Bash', {'command': 'go test ./localapi'})]),
            t.message('user', [t.result_block('a', 'ok')]), {'type': 'rate_limit_event'}, t.final(t.FINAL_TEXT)]


def reader(prof=None):
    return t.composed(None, prof=prof or t.profile(hooks='project', final_kind='text'), projector=tsd.hook_delivery)[0]


class SeamWithRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cairn248-seam-')
        self.fd = os.open(self.tmp.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def tearDown(self):
        os.close(self.fd)
        self.tmp.cleanup()

    def test_fix_profile_reader_output_passes_through_unmodified_retention(self):
        r = reader()
        t.feed_all(r, stream_ok())
        with Retention.create(self.fd, INTENT) as store:
            receipt = store.prepare(r, EXIT0)
        selected = json.loads((Path(self.tmp.name) / t.S / 'SELECTED.json').read_bytes())
        self.assertEqual(selected['final']['work_product']['kind'], 'text')
        self.assertEqual(selected['hooks']['status'], 'observed')
        self.assertEqual([a['tool'] for a in selected['actions']['actions']], ['Bash'])
        self.assertEqual(selected['unreviewed_event_labels'], [])
        self.assertNotIn('hook body', json.dumps(selected))
        self.assertFalse(receipt.return_receipt)

    def test_fix_nonzero_process_exit_is_refused_by_retention_not_by_frames_and_leaves_no_selected_file(self):
        r = reader()
        t.feed_all(r, stream_ok())                        # the stream itself is a valid success stream
        with Retention.create(self.fd, INTENT) as store:
            with self.assertRaises(RetentionError) as caught:
                store.prepare(r, EXIT1)
        self.assertEqual(str(caught.exception), 'native_exit_or_signal')
        self.assertFalse((Path(self.tmp.name) / t.S / 'SELECTED.json').exists())

    def test_fix_provider_failure_never_reaches_selected_evidence_but_is_routed_with_the_exit(self):
        events = [t.init(), t.message('assistant', [t.block('text', text="You've hit your weekly limit · resets Oct 12, 3am")]),
                  {'type': 'result', 'session_id': t.S, 'subtype': 'success', 'is_error': True, 'terminal_reason': 'api_error',
                   'api_error_status': 429, 'result': "You've hit your weekly limit · resets Oct 12, 3am", 'num_turns': 1}]
        events[1]['error'] = 'rate_limit'
        probe, r = seam.TerminalProbe(t.S), reader()
        code = None
        for event in events:
            raw = t.line(event)
            probe.feed(raw)
            try:
                r.feed(raw)
            except Exception as exc:                  # the success-only reader refuses; the probe is unaffected
                code = str(exc)
        self.assertEqual(code, 'native_result_failed')
        custody = {'schema': 'cairn.native-process-custody/1', 'classification': 'completed', 'legacy_exit_code': 1,
                   'observed_returncode': 1, 'observed_wait_status': 256, 'input_bytes': 10, 'input_bytes_written': 10,
                   'cleanup': {'status': 'observed_clear'}, 'leader_reaped': True}
        routed = seam.classify_attempt(custody, dict({'status': 'refused', 'code': code}, **r.stream_digest()), probe.report(),
                                       {'attempt_id': '90000000-0000-4000-8000-000000000001', 'session_id': t.S, 'custody_bound': True})
        self.assertEqual(routed['attempt_class'], 'provider_admission_stop')
        self.assertIn('nonzero_exit', routed['observations'])
        with Retention.create(self.fd, INTENT) as store:
            with self.assertRaises(RetentionError):
                store.prepare(r, EXIT1)
        self.assertFalse((Path(self.tmp.name) / t.S / 'SELECTED.json').exists())


if __name__ == '__main__':
    unittest.main()
