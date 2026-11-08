"""F6 normalized interval shape checks, not a journal collector or truth attestation."""
from dataclasses import dataclass

MAX_PAGES = 64
MAX_EVENTS = 4096
MAX_TOKEN = 512


class JournalUnknown(Exception): pass


@dataclass(frozen=True)
class Selection:
    boot: str
    start_cursor: str
    end_cursor: str
    unit: str
    old_pid: int
    new_pid: int | None


def token(value):
    return isinstance(value, str) and 0 < len(value.encode()) <= MAX_TOKEN and '\x00' not in value


def validate(selection, pages):
    """Check a bounded collector projection. Completeness/provenance remain UNBOUND.

    Cursors are opaque equality tokens. The end cursor must be acquired after the
    structural read in the same selected boot; that causal fact is external evidence.
    No timestamp ordering, remote wall-clock comparison or raw MESSAGE is accepted.
    """
    if not all(token(v) for v in (selection.boot, selection.start_cursor, selection.end_cursor, selection.unit)):
        raise JournalUnknown('selection_missing')
    if type(selection.old_pid) is not int or selection.old_pid <= 1:
        raise JournalUnknown('old_identity_missing')
    if selection.new_pid is not None and (type(selection.new_pid) is not int or selection.new_pid <= 1):
        raise JournalUnknown('new_identity_missing')
    if not isinstance(pages, (tuple, list)) or not 1 <= len(pages) <= MAX_PAGES:
        raise JournalUnknown('pagination_missing_or_unbounded')
    expected_after = selection.start_cursor
    seen = set(); count = 0; classes = []
    for index, page in enumerate(pages):
        if set(page) != {'boot', 'scan_after', 'next_cursor', 'terminal', 'end_seen', 'events', 'loss', 'error'}:
            raise JournalUnknown('page_schema')
        if page['boot'] != selection.boot or page['scan_after'] != expected_after:
            raise JournalUnknown('boot_or_cursor_chain_changed')
        if page['loss'] is not False or page['error'] is not None:
            raise JournalUnknown('loss_or_read_error')
        if type(page['terminal']) is not bool or type(page['end_seen']) is not bool:
            raise JournalUnknown('completion_type')
        last = index == len(pages)-1
        if page['terminal'] != last or page['end_seen'] != last:
            raise JournalUnknown('interval_not_closed')
        if not token(page['next_cursor']) or not isinstance(page['events'], list):
            raise JournalUnknown('cursor_or_events_missing')
        if last and page['next_cursor'] != selection.end_cursor:
            raise JournalUnknown('wrong_end_cursor')
        if not last and page['next_cursor'] == expected_after:
            raise JournalUnknown('pagination_did_not_advance')
        for event in page['events']:
            if set(event) != {'cursor', 'boot', 'unit', 'subject_pid', 'class'}:
                raise JournalUnknown('event_schema_or_private_body')
            cursor = event['cursor']
            if not token(cursor) or cursor in seen or cursor == selection.start_cursor:
                raise JournalUnknown('event_cursor_duplicate_or_missing')
            seen.add(cursor); count += 1
            if count > MAX_EVENTS: raise JournalUnknown('event_limit')
            if event['boot'] != selection.boot or event['unit'] != selection.unit:
                raise JournalUnknown('event_identity_mismatch')
            if event['subject_pid'] not in (selection.old_pid, selection.new_pid) or type(event['subject_pid']) is not int:
                raise JournalUnknown('event_subject_unresolved')
            if event['class'] not in {'STOP_REQUEST', 'NORMAL_EXIT', 'START', 'UNIT_SUCCESS'}:
                raise JournalUnknown('unknown_or_contradictory_lifecycle_event')
            if event['class'] in ('STOP_REQUEST', 'NORMAL_EXIT') and event['subject_pid'] != selection.old_pid:
                raise JournalUnknown('wrong_old_subject')
            if event['class'] == 'START' and event['subject_pid'] != selection.new_pid:
                raise JournalUnknown('unexpected_start')
            classes.append(event['class'])
        expected_after = page['next_cursor']
    return {'status': 'STRUCTURE_CONSISTENT_NOT_QUALIFIED', 'qualified': False,
            'pages': len(pages), 'selected_events': count, 'classes': classes,
            'missing': ['trusted complete collector', 'pre-signal cursor provenance',
                        'post-read end-cursor causality', 'exact versioned event classification']}
