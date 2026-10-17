"""Pure terminal-input classification; no process or task-grade evidence."""
import hashlib
import json
import unittest

from trial_sdk_incoming import LineAssembler, validate_line
from trial_sdk_session_driver import TerminalDocument, SessionDriverError

SESSION = '00000000-0000-4000-8000-000000000003'


def owned(value):
    # Use noncanonical spaces on one line; metadata must describe that wire.
    raw = json.dumps(value, ensure_ascii=False, separators=(', ', ': ')).encode() + b'\n'
    lines = LineAssembler(131072)
    rows = list(lines.feed(raw, 123))
    lines.finish()
    return validate_line(rows[0]), raw[:-1]


class TerminalDocumentTests(unittest.TestCase):
    def test_one_graph_transfer_and_original_noncanonical_wire_metadata(self):
        document, raw = owned(dict(type='result', session_id=SESSION,
            subtype='success', is_error=False, result='Unvalidated task result: café 😀'))
        terminal = TerminalDocument.consume(document, SESSION)
        self.assertTrue(document.taken)
        metadata = terminal.metadata()
        self.assertEqual(metadata['raw_bytes'], len(raw))
        self.assertEqual(metadata['raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(metadata['first_byte_ns'], 123)
        self.assertFalse(metadata['is_error'])
        metadata['session_id'] = 'changed copy'
        self.assertEqual(terminal.metadata()['session_id'], SESSION)
        self.assertEqual(terminal.take()['result'], 'Unvalidated task result: café 😀')
        with self.assertRaisesRegex(SessionDriverError, '^sdk_terminal_consumed$'):
            terminal.take()

    def test_error_result_is_input_terminal_only_and_not_success(self):
        document, _ = owned(dict(type='result', session_id=SESSION,
            subtype='error_max_turns', is_error=True, errors=['not retained in metadata']))
        terminal = TerminalDocument.consume(document, SESSION)
        self.assertTrue(terminal.metadata()['is_error'])
        self.assertNotIn('errors', terminal.metadata())
        self.assertEqual(terminal.take()['errors'], ['not retained in metadata'])

    def test_invalid_terminal_consumes_owned_token_and_returns_only_fixed_code(self):
        base = dict(type='result', session_id=SESSION, subtype='success', is_error=False)
        for change in (dict(type='user', message=dict(type='result')),
                       dict(session_id='00000000-0000-4000-8000-000000000004'),
                       dict(is_error=0), dict(is_error=True), dict(subtype='unclosed private text'),
                       dict(subtype='error_max_turns', is_error=False)):
            with self.subTest(change=change):
                document, _ = owned(base | change)
                with self.assertRaisesRegex(SessionDriverError, '^sdk_terminal_shape$'):
                    TerminalDocument.consume(document, SESSION)
                self.assertTrue(document.taken)
        with self.assertRaisesRegex(SessionDriverError, '^sdk_terminal_owned_required$'):
            TerminalDocument.consume(base, SESSION)


if __name__ == '__main__':
    unittest.main()
