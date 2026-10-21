"""Closed Python command-service transcript, without transport or grant authority.

Invoke observe only for known-sent or authenticated, validated received Frames.
PREPARED additionally requires the caller's fixed-policy and held-authority checks.
All storage, copies and traffic require original-owner reservations before use.
"""
import codecs
import hashlib
import os
import struct
from dataclasses import dataclass
from types import MappingProxyType

from trial_sdk_ipc import Frame, MAX_OPERATION, TABLE_PROFILE_4
from trial_sdk_incoming import IncomingError, parse_document
from trial_sdk_command_policy import FixedCommandPolicy

(READY, PREPARE, REQUEST, REQUEST_END, PREPARED, START, TURN, PROGRESS,
 OUTPUT, DELIVER, STDOUT, STDERR, STATUS, REFUSE, CANCEL, RELEASE, RELEASED,
 SHUTDOWN, BYE, STARTED) = range(1, 21)
TABLE = TABLE_PROFILE_4
STRUCTS = MappingProxyType({
    READY: struct.Struct('>'), PREPARE: struct.Struct('>BQQIQI32s'),
    REQUEST_END: struct.Struct('>'), PREPARED: struct.Struct('>Q32s'),
    START: struct.Struct('>Q'), TURN: struct.Struct('>QQIB'),
    PROGRESS: struct.Struct('>QQHHHBB'), OUTPUT: struct.Struct('>QQQQ32s32s32s32s32s'),
    DELIVER: struct.Struct('>QBH'), STATUS: struct.Struct('>Ii'),
    REFUSE: struct.Struct('>B'), CANCEL: struct.Struct('>B'),
    RELEASE: struct.Struct('>Q'), RELEASED: struct.Struct('>QB'),
    SHUTDOWN: struct.Struct('>'), BYE: struct.Struct('>'), STARTED: struct.Struct('>Q'),
})
RAW_LIMIT = 128 * 1024
WORKING_LIMIT = 512 * 1024 * 1024
TRAFFIC_LIMIT = 4 * 1024 * 1024
OUTPUT_LIMIT = 1024 * 1024


class ServiceWireError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ServiceRequest:
    """Engine-dialect input to policy, never directly executable arguments."""
    operation: str
    args: tuple
    body: str | None

    def plan(self, policy):
        _require(type(policy) is FixedCommandPolicy, 'service_policy')
        return policy.plan(self.operation, self.args, self.body)


def parse_request(text, raw_bytes, operation):
    """Parse only after reserving text/graph/tuple/planning working allocations.

    The caller binds these exact bytes to the transcript digest and operation.
    A returned request or plan confers no current-attempt read authority.
    """
    names = ('search', 'pull', 'pull-evidence', 'history', 'native-search', 'native-pull')
    _require(type(operation) is int and 1 <= operation <= 6, 'service_request')
    try:
        graph = parse_document(text, raw_bytes)
    except IncomingError as exc:
        raise ServiceWireError('service_request_json') from exc
    _require(set(graph) == {'operation', 'args', 'body'}, 'service_request')
    _require(type(graph['operation']) is str and graph['operation'] == names[operation-1]
             and type(graph['args']) is list
             and all(type(arg) is str for arg in graph['args'])
             and (graph['body'] is None or type(graph['body']) is str), 'service_request')
    return ServiceRequest(graph['operation'], tuple(graph['args']), graph['body'])


def _require(condition, code):
    if not condition:
        raise ServiceWireError(code)


def pack_into(kind, destination, *fields):
    """Encode one fixed body into caller-owned, already charged storage.

    This performs representation checks. Semantic checks belong to observe;
    neither function authorizes sending or executing the represented operation.
    """
    _require(type(kind) is int and kind in STRUCTS, 'service_kind')
    _require(type(destination) is memoryview and destination.ndim == 1
             and destination.format == 'B' and destination.c_contiguous
             and not destination.readonly, 'service_view')
    _require(len(destination) == STRUCTS[kind].size, 'service_length')
    digests = {PREPARE: (6,), PREPARED: (1,), OUTPUT: (4, 5, 6, 7, 8)}.get(kind, ())
    for index, field in enumerate(fields):
        _require((type(field) is bytes and len(field) == 32) if index in digests
                 else type(field) is int, 'service_fields')
    try:
        STRUCTS[kind].pack_into(destination, 0, *fields)
    except (struct.error, TypeError, OverflowError) as exc:
        raise ServiceWireError('service_fields') from exc


class ServiceTranscript:
    """One serialized endpoint transcript; bounded scalar diagnostics.

    Ordinals correlate existing owner tickets; they cannot create those tickets.
    No elapsed time, actual wait, source custody or accounting is authenticated
    here. A semantic refusal permits cleanup but prevents a new operation.
    Invalid/uncertain observation permanently poisons the transcript.
    """
    def __init__(self):
        self._phase = 'boot'
        self._next = {'P2S': 1, 'S2P': 1}
        self._busy = False
        self.failed = False
        self._attempt_failed = False
        self._cancelled = self._cancel_acknowledged = False
        self._crossed_prepared = False
        self._crossed_started = False
        self._operation = self._last_operation = self._last_grant = 0
        self._lease = self._deadline = self._request_size = self._request_count = 0
        self._quote = (0, 0)
        self._request_digest = None
        self._request_operation = None
        self._request_hash = None
        self._turn = self._delivery = None
        self._last_turn = self._lifecycle = 0
        self._progress_outcome = None
        self._raw = (0, 0, 0)
        self._wire = (0, 0)
        self._wire_sizes = None
        self._hashes = None
        self._text_hashes = self._text_expected = None
        self._decoders = None
        self._final_remaining = None
        self._returncode = self._wait_status = self._refusal = None
        self._release_unknown = False

    def observe(self, frame):
        # The finally is INSIDE the poisoning guard: cleanup/return interruption
        # after a consumed frame must never leave a replayable healthy state.
        try:
            _require(not self.failed, 'service_failed')
            _require(not self._busy, 'service_busy')
            self._busy = True
            try:
                _require(type(frame) is Frame, 'service_frame')
                with frame.record as record:
                    _require(record[4] == 4, 'service_profile')
                    kind, operation = frame.kind, frame.operation
                    _require(kind in TABLE, 'service_kind')
                    direction, low, high, correlated = TABLE[kind]
                    _require(frame.sequence == self._next[direction], 'service_sequence')
                    _require((operation != 0) == correlated, 'service_operation')
                    with record[32:] as body:
                        _require(low <= len(body) <= high, 'service_length')
                        fields = STRUCTS[kind].unpack(body) if kind in STRUCTS else ()
                        self._apply(kind, operation, fields, body)
                    self._next[direction] += 1
            finally:
                self._busy = False
            return (kind, frame.sequence, operation)
        except BaseException:
            self.failed = True
            raise

    def _phase_is(self, *phases):
        _require(self._phase in phases, 'service_order')

    def _grant(self, value):
        _require(self._last_grant < value <= MAX_OPERATION, 'service_grant')
        self._last_grant = value

    def _apply(self, kind, operation, fields, body):
        if kind == READY:
            self._phase_is('boot')
            self._phase = 'idle'
            return
        if kind == SHUTDOWN:
            self._phase_is('idle')
            self._phase = 'closing'
            return
        if kind == BYE:
            self._phase_is('closing')
            self._phase = 'closed'
            return
        if kind == PREPARE:
            self._phase_is('idle')
            _require(not self._attempt_failed, 'service_attempt_failed')
            _require(self._last_operation < operation <= MAX_OPERATION, 'service_operation')
            op, deadline, lease, raw, working, length, digest = fields
            _require(op in (1, 2, 3, 4, 5, 6) and deadline > 0, 'service_request')
            _require(1 <= lease <= MAX_OPERATION, 'service_lease')
            _require(raw <= RAW_LIMIT and raw + working <= WORKING_LIMIT, 'service_quote')
            _require(1 <= length <= RAW_LIMIT, 'service_request_length')
            self._operation = self._last_operation = operation
            self._request_operation = op
            self._lease, self._deadline, self._quote = lease, deadline, (raw, working)
            self._request_size, self._request_count = length, 0
            self._request_digest, self._request_hash = digest, hashlib.sha256()
            self._turn = self._delivery = None
            self._last_turn = self._lifecycle = 0
            self._progress_outcome = None
            self._raw, self._wire = (0, 0, 0), (0, 0)
            self._wire_sizes = self._hashes = self._decoders = None
            self._text_hashes = self._text_expected = None
            self._final_remaining = None
            self._returncode = self._wait_status = self._refusal = None
            self._phase = 'preparing'
            return
        _require(operation == self._operation, 'service_operation')
        if kind == REQUEST:
            self._phase_is('preparing')
            _require(self._request_count + len(body) <= self._request_size,
                     'service_request_length')
            self._request_count += len(body)
            self._request_hash.update(body)
        elif kind == REQUEST_END:
            self._phase_is('preparing')
            _require(self._request_count == self._request_size and
                     self._request_hash.digest() == self._request_digest,
                     'service_request_digest')
            self._phase = 'prepared_wait'
        elif kind == PREPARED:
            _require(self._phase == 'prepared_wait' or
                     (self._phase == 'cancelling' and self._crossed_prepared),
                     'service_order')
            _require(fields == (self._lease, self._request_digest), 'service_prepared')
            self._crossed_prepared = False
            if self._phase != 'cancelling':
                self._phase = 'prepared'
        elif kind == START:
            self._phase_is('prepared')
            _require(fields[0] == self._lease, 'service_lease')
            self._phase = 'start_wait'
        elif kind == STARTED:
            _require(self._phase == 'start_wait' or
                     (self._phase == 'cancelling' and self._crossed_started),
                     'service_order')
            _require(fields[0] == self._lease, 'service_lease')
            self._crossed_started = False
            if self._phase != 'cancelling':
                self._phase = 'running'
        elif kind == TURN:
            self._phase_is('running')
            grant, turn, maximum, final = fields
            _require(self._turn is None and self._lifecycle != 31, 'service_turn')
            _require(turn == self._last_turn + 1 and turn <= MAX_OPERATION,
                     'service_turn')
            _require(maximum <= 1536 and final in (0, 1)
                     and (maximum == 1536 or final == 1), 'service_allowance')
            if self._final_remaining is not None:
                _require(final == 1 and maximum <= self._final_remaining,
                         'service_final_allowance')
            elif final:
                self._final_remaining = maximum
            self._grant(grant)
            self._turn = fields
        elif kind == PROGRESS:
            self._phase_is('running', 'cancelling')
            grant, turn, stdin, stdout, stderr, lifecycle, outcome = fields
            _require(self._turn is not None and (grant, turn) == self._turn[:2],
                     'service_turn')
            counts = (stdin, stdout, stderr)
            _require(max(counts) <= 512 and sum(counts) <= self._turn[2],
                     'service_allowance')
            _require(lifecycle <= 31 and (lifecycle | self._lifecycle) == lifecycle
                     and (not lifecycle & 16 or lifecycle == 31)
                     and outcome in (0, 1, 2), 'service_lifecycle')
            _require(not any(counts[i] and self._lifecycle & (1 << i)
                             for i in range(3)), 'service_after_eof')
            totals = tuple(a + b for a, b in zip(self._raw, counts))
            # A limit-detecting read can report its one known excess byte before
            # refusal. Such a prefix is never eligible for OUTPUT.
            _require(totals[1] <= OUTPUT_LIMIT + 1 and totals[2] <= OUTPUT_LIMIT + 1
                     and sum(totals) <= TRAFFIC_LIMIT, 'service_raw_limit')
            self._raw, self._lifecycle = totals, lifecycle
            self._last_turn = turn
            self._progress_outcome = outcome
            if outcome == 1:
                self._attempt_failed = True
                self._phase = 'failed_operation'
            else:
                if self._final_remaining is not None:
                    self._final_remaining -= sum(counts)
                self._turn = None
                if outcome == 2:
                    self._attempt_failed = True
                    self._phase = 'refusal_pending'
        elif kind == OUTPUT:
            self._phase_is('running', 'cancelling')
            _require(self._turn is None and self._lifecycle == 31
                     and self._wire_sizes is None, 'service_lifecycle')
            rawout, rawerr, wireout, wireerr, inhash, outhash, errhash, outtext, errtext = fields
            _require((rawout, rawerr) == self._raw[1:]
                     and max(rawout, rawerr) <= OUTPUT_LIMIT, 'service_raw_counts')
            _require(wireout == rawout and rawerr <= wireerr <= 3 * rawerr,
                     'service_text_counts')
            _require(outhash == outtext, 'service_text_digest')
            self._wire_sizes = (wireout, wireerr)
            self._hashes = (inhash, outhash, errhash)
            self._text_expected = (outtext, errtext)
            self._text_hashes = (hashlib.sha256(), hashlib.sha256())
            self._decoders = tuple(codecs.getincrementaldecoder('utf-8')('strict')
                                   for _ in range(2))
            if self._phase != 'cancelling':
                self._phase = 'output'
        elif kind == DELIVER:
            self._phase_is('output')
            grant, stream, maximum = fields
            _require(self._delivery is None and stream in (1, 2), 'service_delivery')
            _require(1 <= maximum <= min(512, self._wire_sizes[stream-1]
                                        - self._wire[stream-1]), 'service_delivery')
            self._grant(grant)
            self._delivery = fields
        elif kind in (STDOUT, STDERR):
            self._phase_is('output', 'cancelling')
            index = 0 if kind == STDOUT else 1
            _require(self._delivery is not None and self._delivery[1] == index + 1
                     and len(body) <= self._delivery[2], 'service_delivery')
            count = self._wire[index] + len(body)
            _require(count <= self._wire_sizes[index], 'service_text_counts')
            try:
                self._decoders[index].decode(body, final=count == self._wire_sizes[index])
            except UnicodeError as exc:
                raise ServiceWireError('service_utf8') from exc
            values = list(self._wire)
            values[index] = count
            self._wire = tuple(values)
            self._text_hashes[index].update(body)
            self._delivery = None
        elif kind == STATUS:
            self._phase_is('output', 'cancelling')
            _require(self._wait_status is None and self._delivery is None
                     and self._wire == self._wire_sizes,
                     'service_text_counts')
            _require(tuple(sha.digest() for sha in self._text_hashes) == self._text_expected,
                     'service_text_digest')
            status, returncode = fields
            _require(status <= 65535 and
                     ((os.WIFEXITED(status) and status & 255 == 0) or
                      (os.WIFSIGNALED(status) and status <= 255)), 'service_wait')
            _require(os.WIFEXITED(status) or os.WIFSIGNALED(status), 'service_wait')
            _require(os.waitstatus_to_exitcode(status) == returncode, 'service_wait')
            self._wait_status, self._returncode = status, returncode
            if self._phase != 'cancelling':
                self._phase = 'result'
        elif kind == REFUSE:
            self._phase_is('preparing', 'prepared_wait', 'prepared', 'start_wait', 'running',
                           'output', 'cancelling', 'failed_operation', 'refusal_pending')
            _require(fields[0] in range(1, 8), 'service_refusal')
            prior = self._cancelled and fields[0] != 6
            if prior:
                _require(self._refusal is None and self._wait_status is None,
                         'service_prior_refusal')
            elif self._cancelled:
                self._cancel_acknowledged = True
            self._crossed_prepared = False
            self._crossed_started = False
            self._refusal = fields[0]
            self._attempt_failed = True
            if not prior:
                self._phase = 'refused'
        elif kind == CANCEL:
            self._phase_is('preparing', 'prepared_wait', 'prepared', 'start_wait', 'running', 'output',
                           'refused', 'result', 'failed_operation', 'refusal_pending')
            _require(not self._cancelled and self._refusal != 6
                     and fields[0] in (1, 2, 3), 'service_cancel')
            self._crossed_prepared = self._phase == 'prepared_wait'
            self._crossed_started = self._phase == 'start_wait'
            self._cancelled = True
            self._attempt_failed = True
            # Preserve the consumed progress fact across a crossed prior refusal.
            self._phase = {1: 'failed_operation', 2: 'refusal_pending'}.get(
                self._progress_outcome, 'cancelling')
        elif kind == RELEASE:
            self._phase_is('result', 'refused', 'failed_operation')
            _require(not self._cancelled or self._cancel_acknowledged,
                     'service_cancel_ack')
            _require(fields[0] == self._lease, 'service_lease')
            self._phase = 'releasing'
        elif kind == RELEASED:
            self._phase_is('releasing')
            _require(fields[0] == self._lease and fields[1] in (0, 1), 'service_release')
            self._release_unknown = bool(fields[1])
            self._phase = 'release_unknown' if fields[1] else 'idle'
        else:
            raise ServiceWireError('service_kind')

    def snapshot(self):
        return {'phase': self._phase, 'failed': self.failed,
                'cancelled': self._cancelled, 'cancel_acknowledged': self._cancel_acknowledged,
                'attempt_failed': self._attempt_failed, 'operation': self._operation,
                'lease': self._lease, 'deadline_ns': self._deadline,
                'quote': self._quote, 'request_count': self._request_count,
                'request_operation': self._request_operation,
                'request_sha256': self._request_digest,
                'turn': self._turn, 'delivery': self._delivery,
                'final_remaining': self._final_remaining,
                'raw_counts': self._raw, 'wire_counts': self._wire,
                'wire_sizes': self._wire_sizes, 'raw_sha256': self._hashes,
                'text_sha256': self._text_expected,
                'lifecycle': self._lifecycle, 'returncode': self._returncode,
                'progress_outcome': self._progress_outcome,
                'cli_wait_status': self._wait_status, 'refusal': self._refusal,
                'release_unknown': self._release_unknown}

