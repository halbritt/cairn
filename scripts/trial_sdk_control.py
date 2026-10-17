"""Pure sdk-hooks/1 control writer and local delivery ledger; no runtime admission.

The external owner supplies actual write counts, enforces hook deadlines and owns
the native/producer processes. This module observes none of their waits or costs.
Native schema/delivery compatibility, including PostToolUseFailure, is unproved.
"""
from collections import deque
import dataclasses
import hashlib
import json
import math
import re
import uuid

from selected_native.evidence import EvidenceError
from selected_native.frames import FrameError, parse
from trial_sdk_incoming import (IncomingError, OwnedJSON, canonical_chunks,
                                canonical_size_sha256, parse_document)


EVENTS = ('SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure')
REQUIRED_EVENTS = frozenset({'SessionStart', 'UserPromptSubmit'})
HOOK_SECONDS = 5
CAPTURE_LIMIT, SELECTED_LIMIT, RESERVE = 8500, 9500, 512
FRAME_LIMIT, LEDGER_LIMIT, ROW_LIMIT = 128 * 1024, 65536, 128
REQUEST_ID_BYTES = 128


class SDKControlError(ValueError):
    """A fixed refusal code. Failed controllers cannot be reused."""


def _canonical(frame):
    return json.dumps(frame, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(content):
    return hashlib.sha256(content).hexdigest()


def _reply_document(request_id, event, context):
    return dict(type='control_response', response={'subtype': 'success',
                    'request_id': request_id, 'response': {'continue': True,
                    'hookSpecificOutput': {'hookEventName': event, 'additionalContext': context}}})


def _reply_wire(request_id, event, context):
    return _canonical(_reply_document(request_id, event, context)) + b'\n'


def reply_wire_bytes(request_id, event, additional_context=''):
    """Exact sdk-hooks/1 reply cost, including JSON escaping and final newline.

    Empty context gives this profile's minimum envelope, not a claim that the
    native protocol has no smaller valid encoding. This pure query reserves or
    writes nothing and establishes no callback count or native compatibility.
    """
    if (type(request_id) is not str or not 0 < len(request_id) <= REQUEST_ID_BYTES
            or re.fullmatch(r'[A-Za-z0-9._:-]+', request_id) is None):
        raise SDKControlError('sdk_callback_identity')
    if type(event) is not str or event not in EVENTS:
        raise SDKControlError('sdk_callback_binding')
    if type(additional_context) is not str:
        raise SDKControlError('sdk_context_shape')
    if len(additional_context) > CAPTURE_LIMIT:
        raise SDKControlError('sdk_input_budget')
    try:
        return canonical_size_sha256(_reply_document(request_id, event, additional_context))[0] + 1
    except (UnicodeError, IncomingError):
        raise SDKControlError('sdk_context_shape') from None


def _plain(frame):
    """Freeze a bounded plain caller graph before encoding or inspecting it."""
    nodes, text_bytes = 0, 0

    def clone(entry, depth):
        nonlocal nodes, text_bytes
        nodes += 1
        if nodes > FRAME_LIMIT or depth > 32:
            raise SDKControlError('sdk_frame_bounds')
        if type(entry) is str:
            if len(entry) > FRAME_LIMIT:
                raise SDKControlError('sdk_frame_bounds')
            for offset in range(0, len(entry), 2048):
                text_bytes += len(entry[offset:offset + 2048].encode('utf-8'))
                if text_bytes > FRAME_LIMIT:
                    raise SDKControlError('sdk_frame_bounds')
            return entry
        if entry is None or type(entry) is bool:
            return entry
        if type(entry) is int and entry.bit_length() <= 1024:
            return entry
        if type(entry) is float and math.isfinite(entry):
            return entry
        if type(entry) is dict and len(entry) <= FRAME_LIMIT and all(type(k) is str for k in entry):
            return {clone(k, depth + 1): clone(v, depth + 1) for k, v in entry.items()}
        if type(entry) is list and len(entry) <= FRAME_LIMIT:
            return [clone(v, depth + 1) for v in entry]
        raise SDKControlError('sdk_frame_shape')

    return clone(frame, 0)


@dataclasses.dataclass(frozen=True)
class Callback:
    request_id: str
    event: str
    _input: dict = dataclasses.field(repr=False)
    input_bytes: int
    input_sha256: str
    frame_raw_bytes: int
    frame_raw_sha256: str
    frame_encoding: str

    def iter_input_json(self):
        """Full canonical input in bounded chunks; owner charges actual transport.

        This is semantic JSON, not byte-identical original wire. The owner must
        drain producer output while writing, enforce deadlines and account for
        each emitted chunk and its actual write progress.
        """
        return canonical_chunks(self._input)

    def diagnostic_projection(self):
        """Explicit reduced view for the fixed marker producer only.

        Full input was validated and hashed. This view is not equivalent to
        delivering the complete input to a general producer.
        """
        result = dict(schema='cairn.sdk-marker-input-projection/1', event=self.event,
                      session_id=self._input['session_id'], input_bytes=self.input_bytes,
                      input_sha256=self.input_sha256, frame_raw_bytes=self.frame_raw_bytes,
                      frame_raw_sha256=self.frame_raw_sha256, frame_encoding=self.frame_encoding)
        if self.event.startswith('PostToolUse'):
            for name, limit in (('tool_name', 128), ('tool_use_id', 256)):
                value = self._input.get(name)
                if type(value) is not str or not 0 < len(value) <= limit:
                    raise SDKControlError('sdk_projection_shape')
                result[name] = value
        return result


@dataclasses.dataclass(frozen=True)
class ContextCapacity:
    """Additional JSON string-content bytes, or guaranteed raw UTF-8 bytes.

    json_content_bytes excludes the surrounding quotes, which the empty reply
    already reserves. guaranteed_utf8_bytes uses JSON's worst-case 6x expansion.
    Neither value is an observed producer or native-context measurement.
    """
    json_content_bytes: int
    guaranteed_utf8_bytes: int
    empty_reply_wire_bytes: int


@dataclasses.dataclass
class _Write:
    kind: str
    request_id: str
    wire: bytes = dataclasses.field(repr=False)
    offset: int = 0


class SDKControl:
    """One frozen user; only fixed initialize and bound callback replies.

    instruction_bytes is the caller's UTF-8 byte count, not instruction content.
    pending_write returns an offered suffix; written reports a positive actual
    local write. Neither queued bytes nor a replay establishes native settlement.
    The owner serializes calls and reports each write immediately; an offered
    suffix must not be reused after receive/reply changes the controller state.
    """

    def __init__(self, session_id, user_message, instruction_bytes):
        error = None
        try:
            valid_session = type(session_id) is str and str(uuid.UUID(session_id)) == session_id
        except (ValueError, AttributeError):
            valid_session = False
        if not valid_session:
            raise SDKControlError('sdk_session')
        if (type(user_message) is not bytes or not 0 < len(user_message) <= CAPTURE_LIMIT
                or not user_message.endswith(b'\n') or user_message.count(b'\n') != 1):
            raise SDKControlError('sdk_user_frame')
        try:
            user = parse(user_message)
        except (FrameError, EvidenceError):
            error = 'sdk_user_frame'
        if error is not None:
            raise SDKControlError(error)
        if (set(user) != {'type', 'message'} or user['type'] != 'user'
                or type(user['message']) is not dict or set(user['message']) != {'role', 'content'}
                or user['message']['role'] != 'user' or type(user['message']['content']) is not str
                or not user['message']['content']):
            raise SDKControlError('sdk_user_frame')
        if type(instruction_bytes) is not int or not 0 <= instruction_bytes <= SELECTED_LIMIT:
            raise SDKControlError('sdk_instruction_bytes')
        self._session = session_id
        identities = [str(uuid.uuid4()) for _ in range(5)]
        if len(set(identities)) != 5:
            raise SDKControlError('sdk_identity_collision')
        self._initialize_id = identities[0]
        self._callback_ids = dict(zip(EVENTS, identities[1:]))
        initialize = dict(type='control_request', request_id=self._initialize_id,
                          request=dict(subtype='initialize', hooks={
                              event: [dict(hookCallbackIds=[ident], timeout=HOOK_SECONDS)]
                              for event, ident in self._callback_ids.items()}))
        wire = _canonical(initialize) + b'\n'
        # Immutable initial facts for source review, without another raw copy.
        self._initial_identity = (session_id, self._initialize_id,
                                  tuple(self._callback_ids.items()))
        self._initial_wire = len(wire), _sha(wire)
        self._initial_user = len(user_message), _sha(user_message)
        self._initial_instruction_bytes = instruction_bytes
        fixed_bytes = len(wire) + len(user_message)
        if (CAPTURE_LIMIT - fixed_bytes < 1000 or
                SELECTED_LIMIT - fixed_bytes - instruction_bytes - RESERVE < 1000):
            raise SDKControlError('sdk_input_budget')
        self._instruction_bytes = instruction_bytes
        self._reserved_wire = fixed_bytes
        self._user = user_message
        self._queue = deque([_Write('initialize', self._initialize_id, wire)])
        self._queued_bytes, self._written_bytes = len(wire), 0
        self._initialize_written = self._initialized = False
        self._user_queued = self._user_written = False
        self._user_enqueued = False
        self._user_written_bytes = 0
        self._callbacks, self._rows, self._expected_replays = {}, [], {}
        self._future_required = set(REQUIRED_EVENTS)
        self._empty_replies = {}
        self._failure, self._closed = None, False
        if self._held_reply_bytes() > min(self.remaining_wire_bytes, self.remaining_selected_bytes):
            raise SDKControlError('sdk_input_budget')

    @property
    def bindings(self):
        return dict(session_id=self._session, initialize_request_id=self._initialize_id,
                    callback_ids=dict(self._callback_ids))

    def pristine_binding(self):
        """Non-consuming source-review projection of the actual initial writer.

        Bind held user/initialize bytes as well as IDs and untouched state. This
        refuses after any progress, closure, failure or drift without changing
        the writer. It is not a runtime handoff, native observation or admission.
        Decoded metadata and digest working state remain under the outer budget;
        no extra complete raw user or initialize buffer is created here.
        """
        pristine = (self._failure is None and not self._closed
            and not self._initialize_written and not self._initialized
            and not self._user_queued and not self._user_enqueued and not self._user_written
            and self._user_written_bytes == self._written_bytes == 0
            and self._instruction_bytes == self._initial_instruction_bytes
            and not self._callbacks and not self._rows and not self._expected_replays
            and not self._empty_replies and self._future_required == set(REQUIRED_EVENTS)
            and (self._session, self._initialize_id, tuple(self._callback_ids.items())) == self._initial_identity
            and type(self._user) is bytes and (len(self._user), _sha(self._user)) == self._initial_user
            and len(self._queue) == 1)
        if pristine:
            head = self._queue[0]
            pristine = (type(head) is _Write and head.kind == 'initialize'
                and head.request_id == self._initialize_id and head.offset == 0
                and type(head.wire) is bytes and (len(head.wire), _sha(head.wire)) == self._initial_wire
                and self._queued_bytes == len(head.wire)
                and self._reserved_wire == len(head.wire) + len(self._user))
        if not pristine:
            raise SDKControlError('sdk_control_not_pristine')
        return dict(schema='cairn.sdk-pristine-control/1', profile='sdk-hooks/1',
                    bindings=self.bindings, user_message_bytes=len(self._user),
                    user_message_sha256=self._initial_user[1],
                    initialize_bytes=len(head.wire), initialize_sha256=self._initial_wire[1],
                    instruction_bytes=self._instruction_bytes,
                    guaranteed_context_bytes=self.guaranteed_context_bytes,
                    ledger=self.ledger())

    @property
    def remaining_selected_bytes(self):
        """9500-based room for this writer's charges, before held empty replies.

        Actual producer output and native memory results need further independent
        charging; this value is not the composed attempt's remaining allowance.
        """
        return SELECTED_LIMIT - self._instruction_bytes - RESERVE - self._reserved_wire

    @property
    def remaining_wire_bytes(self):
        """8500-based unspent wire room, before held empty replies."""
        return CAPTURE_LIMIT - self._reserved_wire

    def _future_reply_bytes(self, events):
        # These two events are mandatory observations in the existing evaluator,
        # not predictions of native event counts. Additional events reserve at receipt.
        return sum(reply_wire_bytes('r' * REQUEST_ID_BYTES, event) for event in events)

    def _held_reply_bytes(self):
        return sum(self._empty_replies.values()) + self._future_reply_bytes(self._future_required)

    def _context_json_bytes(self):
        return min(self.remaining_wire_bytes, self.remaining_selected_bytes) - self._held_reply_bytes()

    def reply_budget(self):
        """Read-only exact current reservations, also available after refusal.

        Tool events are reserved only when observed. These diagnostics do not
        predict remaining callbacks or include external producer/memory charges.
        """
        pending = sum(self._empty_replies.values())
        required = self._future_reply_bytes(self._future_required)
        return dict(pending_callbacks=len(self._empty_replies),
                    pending_empty_reply_bytes=pending,
                    mandatory_callbacks_unobserved=sorted(self._future_required),
                    mandatory_empty_reply_bytes=required,
                    reserved_empty_reply_bytes=pending + required,
                    remaining_wire_bytes=self.remaining_wire_bytes,
                    remaining_selected_bytes=self.remaining_selected_bytes,
                    context_json_bytes=self._context_json_bytes(),
                    additional_tool_callback_count=None,
                    external_selected_charges_included=False)

    @property
    def guaranteed_context_bytes(self):
        """Current conservative raw UTF-8 pool; initially suitable for compilation.

        This can be less than the minimum 1000 raw hook-wire bytes because empty
        replies and escaping consume that allowance. It is not a per-event grant.
        The composed evaluator must independently apply any tighter allowance.
        """
        return 0 if self._closed or self._failure else self._context_json_bytes() // 6

    def context_capacity(self, request_id):
        self._live()
        self._need(type(request_id) is str and request_id in self._empty_replies,
                   'sdk_reply_unknown')
        available = self._context_json_bytes()
        return ContextCapacity(available, available // 6, self._empty_replies[request_id])

    def _live(self):
        if self._failure is not None or self._closed:
            raise SDKControlError('sdk_unusable')

    def _discard(self):
        self._queue.clear()
        self._expected_replays.clear()
        self._user = b''

    def _refuse(self, code):
        self._failure = code
        self._discard()
        raise SDKControlError(code)

    def _need(self, condition, code):
        if not condition:
            self._refuse(code)

    def pending_write(self):
        self._live()
        return self._queue[0].wire[self._queue[0].offset:] if self._queue else None

    @property
    def write_pending(self):
        """Current queue readiness without allocating an offered suffix.

        Cumulative queued_input_bytes also includes an unstarted user frame
        temporarily deferred by a callback, so it is not readiness evidence.
        """
        self._live()
        return bool(self._queue)

    def written(self, count):
        self._live()
        self._need(bool(self._queue), 'sdk_write_without_frame')
        head = self._queue[0]
        self._need(type(count) is int and 0 < count <= len(head.wire) - head.offset,
                   'sdk_write_progress')
        head.offset += count
        self._written_bytes += count
        if head.kind == 'user':
            self._user_written_bytes += count
        elif head.kind == 'reply':
            self._callbacks[head.request_id]['reply_written_bytes'] += count
        if head.offset == len(head.wire):
            self._queue.popleft()
            if head.kind == 'initialize':
                self._initialize_written = True
            elif head.kind == 'user':
                self._user_written = True
                self._user_enqueued = False
                self._user = b''
            else:
                self._callbacks[head.request_id]['reply_written'] = True
            self._queue_user()

    def write_failed(self):
        self._live()
        self._refuse('sdk_write_failed')

    def _queue_user(self):
        if (self._initialized and not self._user_written and not self._user_enqueued and not self._queue
                and all(row['reply_written'] for row in self._rows)):
            self._queue.append(_Write('user', '', self._user))
            self._user_enqueued = True
            if not self._user_queued:
                self._queued_bytes += len(self._user)
            self._user_queued = True

    def _defer_unstarted_user(self):
        if self._user_enqueued and self._user_written_bytes == 0:
            self._queue = deque(frame for frame in self._queue if frame.kind != 'user')
            self._user_enqueued = False

    def _frame(self, frame):
        error = None
        try:
            if type(frame) is dict:
                frozen = _plain(frame)
                length, digest = canonical_size_sha256(frozen)
                encoding = 'canonical_caller_frame'
            elif type(frame) is bytes:
                if len(frame) > FRAME_LIMIT:
                    raise SDKControlError('sdk_frame_bounds')
                frozen = _plain(parse_document(frame.decode('utf-8'), len(frame)))
                length, digest = len(frame), _sha(frame)
                encoding = 'supplied_frame_bytes'
            else:
                raise SDKControlError('sdk_frame_shape')
            if length > FRAME_LIMIT or canonical_size_sha256(frozen)[0] > FRAME_LIMIT:
                raise SDKControlError('sdk_frame_bounds')
        except SDKControlError as exc:
            error = str(exc)
        except IncomingError as exc:
            error = ('sdk_frame_bounds' if exc.code in ('incoming_depth', 'incoming_graph_bounds',
                     'incoming_document_bytes', 'canonical_depth', 'canonical_graph_bounds')
                     else 'sdk_frame_shape')
        except (FrameError, EvidenceError, UnicodeError, ValueError, TypeError, RecursionError):
            error = 'sdk_frame_shape'
        if error is not None:
            self._refuse(error)
        return frozen, (length, digest, encoding)

    def receive(self, frame):
        self._live()
        self._need(self._initialize_written, 'sdk_before_initialize_write')
        frame, metadata = self._frame(frame)
        return self._receive(frame, metadata)

    def receive_owned(self, document):
        """Consume one fully validated stream document, including on refusal.

        The owner demultiplexes document.kind first, then relinquishes the token.
        Ownership and supplied wire metadata establish no native authentication.
        Direct bytes/dict receive remains a pure compatibility entry point, not
        a bounded transport driver.
        """
        error = None
        try:
            if type(document) is not OwnedJSON:
                raise SDKControlError('sdk_frame_shape')
            metadata = document.raw_bytes, document.raw_sha256, 'stream_line_without_lf'
            value = document.take()
        except (IncomingError, SDKControlError):
            error = 'sdk_frame_shape'
        self._live()
        if error is not None:
            self._refuse(error)
        self._need(self._initialize_written, 'sdk_before_initialize_write')
        self._need(type(metadata[0]) is int and 0 < metadata[0] <= FRAME_LIMIT,
                   'sdk_frame_bounds')
        self._need(type(metadata[1]) is str and re.fullmatch(r'[a-f0-9]{64}', metadata[1]) is not None,
                   'sdk_frame_shape')
        try:
            frozen = _plain(value)
            length, _ = canonical_size_sha256(frozen)
        except SDKControlError as exc:
            error = str(exc)
        except (IncomingError, UnicodeError, ValueError, TypeError, RecursionError):
            error = 'sdk_frame_shape'
        if error is not None:
            self._refuse(error)
        self._need(length <= FRAME_LIMIT, 'sdk_frame_bounds')
        return self._receive(frozen, metadata)

    def _receive(self, frame, metadata):
        kind = frame.get('type')
        if kind == 'control_response':
            self._response(frame)
            return None
        if kind == 'control_cancel_request':
            self._need(set(frame) == {'type', 'request_id'}, 'sdk_cancel_shape')
            ident = frame['request_id']
            self._need(type(ident) is str and ident in self._callbacks, 'sdk_unknown_cancel')
            self._callbacks[ident]['cancelled'] = True
            self._refuse('sdk_callback_cancelled')
        self._need(kind == 'control_request', 'sdk_unknown_control')
        return self._callback(frame, metadata)

    def _response(self, frame):
        self._need(set(frame) == {'type', 'response'} and type(frame['response']) is dict,
                   'sdk_response_shape')
        response = frame['response']
        ident, subtype = response.get('request_id'), response.get('subtype')
        self._need(type(ident) is str, 'sdk_response_shape')
        if ident == self._initialize_id:
            self._need(not self._initialized, 'sdk_duplicate_initialize')
            self._need(subtype != 'error', 'sdk_initialize_error')
            fields = {'subtype', 'request_id', 'response'}
            pending = {'pending_permission_requests', 'pending_user_dialog_requests'}
            self._need(set(response) in (fields, fields | pending)
                       and subtype == 'success' and type(response['response']) is dict,
                       'sdk_initialize_shape')
            if pending <= response.keys():
                self._need(all(type(response[key]) is list for key in pending), 'sdk_initialize_shape')
                self._need(not any(response[key] for key in pending), 'sdk_initialize_pending')
            self._initialized = True
            self._queue_user()
            return
        self._need(ident in self._callbacks, 'sdk_unknown_response')
        row = self._callbacks[ident]
        self._need(not row['replayed'], 'sdk_duplicate_replay')
        self._need(row['reply_written'], 'sdk_early_replay')
        self._need(subtype != 'error', 'sdk_callback_response_error')
        self._need(canonical_size_sha256(frame) == self._expected_replays[ident], 'sdk_reply_content_drift')
        row['replayed'] = True
        del self._expected_replays[ident]

    def _callback(self, frame, metadata):
        self._need(set(frame) == {'type', 'request_id', 'request'}, 'sdk_callback_shape')
        ident, request = frame['request_id'], frame['request']
        self._need(type(ident) is str and 0 < len(ident) <= REQUEST_ID_BYTES
                   and re.fullmatch(r'[A-Za-z0-9._:-]+', ident)
                   and ident != self._initialize_id and ident not in self._callbacks,
                   'sdk_callback_identity')
        self._need(type(request) is dict and {'subtype', 'callback_id', 'input'} <= set(request)
                   and not set(request) - {'subtype', 'callback_id', 'input', 'tool_use_id',
                                          'issued_at', 'deadline_ms'}
                   and request['subtype'] == 'hook_callback', 'sdk_callback_shape')
        payload = request['input']
        self._need(type(payload) is dict, 'sdk_callback_input')
        event = payload.get('hook_event_name')
        self._need(type(event) is str and event in self._callback_ids
                   and request['callback_id'] == self._callback_ids[event]
                   and payload.get('session_id') == self._session, 'sdk_callback_binding')
        self._need(event == 'SessionStart' or self._user_written, 'sdk_callback_before_user')
        self._need('tool_use_id' not in request or
                   (type(request['tool_use_id']) is str and 0 < len(request['tool_use_id']) <= 256),
                   'sdk_callback_shape')
        for key in ('issued_at', 'deadline_ms'):
            self._need(key not in request or (type(request[key]) is int and
                       request[key] >= (1 if key == 'deadline_ms' else 0)), 'sdk_callback_shape')
        self._need(len(self._rows) < ROW_LIMIT, 'sdk_ledger_bounds')
        empty_reply = reply_wire_bytes(ident, event)
        future_required = self._future_required - {event}
        held = sum(self._empty_replies.values()) + empty_reply + self._future_reply_bytes(future_required)
        self._need(held <= min(self.remaining_wire_bytes, self.remaining_selected_bytes),
                   'sdk_input_budget')
        input_bytes, input_digest = canonical_size_sha256(payload)
        row = dict(event=event, request_sha256=_sha(ident.encode()),
                   input_sha256=input_digest, input_bytes=input_bytes,
                   frame_raw_bytes=metadata[0], frame_raw_sha256=metadata[1],
                   frame_encoding=metadata[2],
                   reply_sha256=None, context_sha256=None, context_bytes=None,
                   reply_bytes=None, reply_written_bytes=0, reply_written=False,
                   replayed=False, cancelled=False, timed_out=False)
        self._need(canonical_size_sha256(self.ledger() | {'rows': self._rows + [row]})[0] <= LEDGER_LIMIT,
                   'sdk_ledger_bounds')
        self._callbacks[ident] = row
        self._rows.append(row)
        self._future_required = future_required
        self._empty_replies[ident] = empty_reply
        self._defer_unstarted_user()
        return Callback(ident, event, payload, input_bytes, input_digest, *metadata)

    def reply(self, request_id, additional_context):
        self._live()
        self._need(type(request_id) is str and request_id in self._callbacks, 'sdk_reply_unknown')
        row = self._callbacks[request_id]
        self._need(row['reply_sha256'] is None, 'sdk_reply_duplicate')
        capacity = self.context_capacity(request_id)
        self._need(type(additional_context) is str, 'sdk_context_shape')
        self._need(len(additional_context) <= capacity.json_content_bytes, 'sdk_input_budget')
        error = None
        try:
            cost = reply_wire_bytes(request_id, row['event'], additional_context)
        except SDKControlError as exc:
            error = str(exc)
        if error is not None:
            self._refuse(error)
        self._need(cost - capacity.empty_reply_wire_bytes <= capacity.json_content_bytes,
                   'sdk_input_budget')
        # Exact cost is checked before allocating either complete raw value.
        content = additional_context.encode('utf-8')
        wire = _reply_wire(request_id, row['event'], additional_context)
        # Replacing this callback's empty reservation cannot spend another reply.
        self._reserved_wire += len(wire)
        del self._empty_replies[request_id]
        self._queued_bytes += len(wire)
        row.update(reply_sha256=_sha(memoryview(wire)[:-1]), context_sha256=_sha(content),
                   context_bytes=len(content), reply_bytes=len(wire))
        self._expected_replays[request_id] = len(wire) - 1, row['reply_sha256']
        self._queue.append(_Write('reply', request_id, wire))

    def callback_timeout(self, request_id):
        """Record the external owner's deadline observation; never infer a wait."""
        self._live()
        self._need(type(request_id) is str and request_id in self._callbacks,
                   'sdk_timeout_unknown')
        self._callbacks[request_id]['timed_out'] = True
        self._refuse('sdk_callback_timeout')

    def ledger(self):
        return dict(schema='cairn.sdk-hook-delivery/2', profile='sdk-hooks/1',
                    failure=self._failure, closed=self._closed,
                    initialize_written=self._initialize_written, initialized=self._initialized,
                    user_queued=self._user_queued, user_written=self._user_written,
                    user_written_bytes=self._user_written_bytes,
                    queued_input_bytes=self._queued_bytes, written_input_bytes=self._written_bytes,
                    reserved_input_bytes=self._reserved_wire,
                    instruction_bytes=self._instruction_bytes,
                    remaining_selected_bytes=self.remaining_selected_bytes,
                    remaining_wire_bytes=self.remaining_wire_bytes,
                    reserved_reply_bytes=self._held_reply_bytes(),
                    mandatory_callbacks_unobserved=sorted(self._future_required),
                    guaranteed_context_bytes=self.guaranteed_context_bytes,
                    native_consumer_settled=None, native_settlement_attested=False,
                    native_hook_schema_attested=False,
                    runtime_attested=False,
                    rows=[dict(row) for row in self._rows])

    def close(self):
        self._closed = True
        self._discard()
