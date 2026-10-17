"""One source-owned, nonblocking parent operation under the original ledger.

The fixed builder owns request authority, the actual ServiceLink and service
standing quote, and the shared gate. This module neither launches nor waits.
It does not implement native argument mapping or admit an experiment.
"""
from dataclasses import dataclass
import sys
import time

from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_memory_gate import MemoryDispatchGate, HookRequest
from trial_sdk_memory_command_job import RAW_RESERVATION, TEXT_RESERVATION
from trial_sdk_engine_adapter import CommandTextOutcome
from trial_sdk_incoming import BodyDecoder, canonical_chunks, canonical_size_sha256
import trial_sdk_command_service_wire as wire


class OperationError(ValueError):
    pass


def _require(value, code):
    if not value:
        raise OperationError(code)


@dataclass(frozen=True)
class OperationQuote:
    """Builder's simultaneous logical allocations, not a physical-fit claim.

    Parent includes input aliases, canonical graph/scratch, UTF-8 text fragments,
    joined results, and all bookkeeping. Child includes preparation graph/plan,
    CommandJob's raw/text allocations and its largest retained body alias.
    """
    parent_raw: int
    parent_working: int
    child_raw: int
    child_working: int
    preparation_raw: int
    preparation_working: int
    job_body_working: int

    def check(self):
        _require(all(type(n) is int and n >= 0 for n in (
            self.parent_raw, self.parent_working, self.child_raw,
            self.child_working, self.preparation_raw,
            self.preparation_working, self.job_body_working)), 'operation_quote')
        _require(self.parent_raw >= 32768 and self.parent_working > 0 and
                 self.preparation_raw > 0 and self.preparation_working > 0 and
                 self.child_raw >= self.preparation_raw + RAW_RESERVATION and
                 self.child_working >= self.preparation_working +
                 TEXT_RESERVATION + self.job_body_working, 'operation_quote')


def unsupported_native(request, deadline_ns):
    raise OperationError('operation_native_unimplemented')


class HookOperationForward:
    """Fixed gate forward for successive operations on one actual service link.

    The builder retains this object for the entire gate lifetime. Rebinding
    requires the preceding operation and all of its source-owned aliases to end.
    """
    def __init__(self, link):
        _require(type(link) is ServiceLink, 'operation_forward_link')
        self.link = link
        self._operation = None
        self._gate = None

    def bind(self, operation, gate):
        _require(type(operation) is CommandOperation and operation.link is self.link,
                 'operation_forward_link')
        _require(type(gate) is MemoryDispatchGate and gate._hook_forward is self and
                 (self._gate is None or self._gate is gate), 'operation_gate_binding')
        prior = self._operation
        _require(prior is None or prior._closed and prior._retired and not prior._unknown,
                 'operation_forward_pending')
        self._operation = operation
        self._gate = gate

    def __call__(self, request, deadline_ns):
        _require(self._operation is not None, 'operation_forward_unbound')
        return self._operation.forward_hook(request, deadline_ns)


class CommandOperation:
    """Exactly one operation; a returned gate forward retains this object.

    Construct, bind_gate, prepare_hook, then call step on readiness. CANCEL is
    serialized with existing sends. Each step makes at most one send and one
    receive attempt; no readiness polling, subprocess or hidden retry occurs.
    Source callers must retain this owner on every exception, including interrupts.
    """
    def __init__(self, link, quote, *, operation, lease, deadline_ns,
                 clock=time.monotonic_ns):
        _require(type(link) is ServiceLink and type(quote) is OperationQuote,
                 'operation_binding')
        quote.check()
        _require(all(type(n) is int and 0 < n <= 2**53-1 for n in (operation, lease))
                 and type(deadline_ns) is int and deadline_ns > 0
                 and callable(clock), 'operation_binding')
        state = link.transcript.snapshot()
        _require(state['phase'] == 'idle', 'operation_service_not_ready')
        _require(operation > state['operation'] and lease > state['lease'],
                 'operation_identity_replay')
        self.link, self.resources, self.quote = link, link.resources, quote
        self.operation, self.lease, self.deadline = operation, lease, deadline_ns
        self._clock, self._last_clock = clock, None
        self._busy = self._unknown = self._retired = self._closed = False
        self._primary = None
        self._cleanup_error = None
        self._gate = self._handle = None
        self._dispatch_attempted = self._forwarded = False
        self._parent = self._child = None
        self._a = self._b = None
        self._rx_credit = self._future = None
        self._rx_mode = self._tx_kind = None
        self._phase = 'unprepared'
        self._request = self._document = self._chunks = self._chunk = None
        self._offset = self._request_size = 0
        self._digest = None
        self._data = self._data_grant = None
        self._data_known = 0
        self._progress_unknown = False
        self._grant = self._turn = 0
        self._offer = None
        self._decoders = self._result = None
        self._cancel_reason = None
        self._cancel_queued = self._cancel_sent = False
        self._last_kind = None
        self._refusal = None
        self._prior_refusal = None
        self._budget_refusal = None
        try:
            self._parent = self.resources.allocate(self.resources.local, 'operation',
                                                    quote.parent_raw, quote.parent_working)
            _require(self._parent is not None, 'operation_parent_denied')
            self._child = self.resources.allocate(link.domain, 'operation',
                                                  quote.child_raw, quote.child_working)
            _require(self._child is not None, 'operation_child_denied')
            self._b = link.credit(251, emergency=True)
        except BaseException:
            # Constructor handoff can fail after issuance. Preserve its tickets.
            self.resources.freeze()
            raise

    def bind_gate(self, gate):
        _require(self._gate is None and type(gate) is MemoryDispatchGate,
                 'operation_gate_binding')
        forward = gate._hook_forward
        _require(forward == self.forward_hook or type(forward) is HookOperationForward,
                 'operation_gate_binding')
        if type(forward) is HookOperationForward:
            forward.bind(self, gate)
        self._gate = gate

    def _now(self):
        now = self._clock()
        _require(type(now) is int and now >= 0 and
                 (self._last_clock is None or now >= self._last_clock), 'operation_clock')
        self._last_clock = now
        _require(now < self.deadline, 'operation_deadline')

    def _poison(self, code):
        self._unknown = True
        if self._primary is None:
            self._primary = code
        # Never infer a handle from gate snapshots if its handoff was interrupted.
        if self._handle is not None:
            handle, self._handle = self._handle, None
            try:
                self._gate.fail_pending(handle)
            except BaseException as exc:
                self._cleanup_error = type(exc).__name__
        if self._data is not None:
            try:
                self.resources.quarantine(self._data, self._data_known)
            except BaseException as exc:
                self._cleanup_error = type(exc).__name__
        self.link._poison()
        self.resources.freeze()

    def prepare_hook(self, operation, args=(), body=None):
        _require(self._phase == 'unprepared' and self._gate is not None,
                 'operation_prepare_consumed')
        try:
            self._now()
            _require(type(operation) is str and operation in
                     ('search', 'pull', 'pull-evidence', 'history') and
                     type(args) is tuple and all(type(arg) is str for arg in args)
                     and (body is None or type(body) is str), 'operation_request')
            _require(body is None or sys.getsizeof(body) <= self.quote.job_body_working,
                     'operation_body_quote')
            # Policy normalization may add scope fields or change JSON escaping.
            # Its returned UTF-8 body is bounded by RAW_LIMIT, not input sizeof.
            _require(operation == 'search' or self.quote.job_body_working >=
                     4 * wire.RAW_LIMIT + sys.getsizeof('\U0010ffff'), 'operation_body_quote')
            # Both operation allocations precede this graph and canonical scratch.
            self._request = HookRequest(operation, args, body)
            self._document = dict(operation=operation, args=list(args), body=body)
            self._request_size, digest = canonical_size_sha256(self._document)
            _require(0 < self._request_size <= wire.RAW_LIMIT, 'operation_request_size')
            self._digest = bytes.fromhex(digest)
            self._chunks = canonical_chunks(self._document)
            self._phase = 'prepare'
        except BaseException as exc:
            self._poison(type(exc).__name__)
            raise

    def forward_hook(self, request, deadline_ns):
        """Only the bound shared gate calls this after its sole logical debit."""
        _require(self._dispatch_attempted and not self._forwarded and
                 self._phase == 'prepared' and type(request) is HookRequest and
                 request == self._request and deadline_ns == self.deadline,
                 'operation_forward_binding')
        self._forwarded = True
        if not self._stage(113, (40, 33), 'start'):
            return self
        self._queue(wire.START, self._a, self.lease)
        return self

    def cancel(self, reason=1):
        _require(type(reason) is int and reason in (1, 2, 3), 'operation_cancel')
        _require(not self._unknown and not self._retired, 'operation_unavailable')
        if self._closed or self._phase == 'releasing':
            return False  # RELEASE is already final; protocol admits no later CANCEL.
        if self._cancel_reason is None:
            self._cancel_reason = reason
        return True

    def _queue(self, kind, credit, *fields, data=None):
        self.link.queue(kind, self.operation, credit, *fields, data=data)
        self._tx_kind = kind

    def _stage(self, maximum, replies, mode):
        _require(self._a is None and self._rx_credit is None, 'operation_stage_pending')
        if self.resources.snapshot()['traffic_available'] < maximum:
            self._capacity_stop()
            return False
        self._a = self.link.credit(maximum)
        if replies:
            self._adopt(self._a, self._a.burst(replies), mode)
        return True

    def _capacity_stop(self):
        # Known refusal BEFORE a grant/send must not poison the emergency loan.
        self._budget_refusal = 'operation_control_exhausted'
        if self._phase == 'prepare':
            # PREPARE was never queued: no child operation aliases were created.
            self.resources.release(self._child)
            self._child = None
            self._b.finish()
            self._b = None
            self._close()
            return
        if self._cancel_reason is None:
            self._cancel_reason = 3
        self._send_cancel()

    def _adopt(self, credit, cursor, mode):
        self.link.adopt_receive_burst(credit, cursor)
        self._rx_credit, self._rx_mode = credit, mode

    def _end_receive(self):
        self.link.finish_receive_burst()
        self._rx_credit = self._rx_mode = None

    def _finish_a(self):
        if self._a is not None:
            self._a.finish()
            self._a = None

    def _future_terminal(self, maxima):
        if self._future is None:
            self._future = self._b.burst(maxima)

    def _send_cancel(self):
        # Existing prepare/start/turn cursors cover their crossed ACK. A text
        # frame may occupy all 544 bytes, so reserve its future ACK in B first.
        if self._rx_credit is None:
            self._adopt(self._b, self._b.burst((33,)), 'cancel')
        elif self._rx_mode == 'text':
            self._future_terminal((33,))
        self._cancel_queued = True
        self._queue(wire.CANCEL, self._b, self._cancel_reason)

    def _after_send(self, kind):
        if kind == wire.PREPARE:
            self._phase = 'request'
            self._finish_a()
        elif kind == wire.REQUEST:
            self._finish_a()
        elif kind == wire.REQUEST_END:
            self._phase = 'prepared_wait'
        elif kind == wire.START:
            self._phase = 'start_wait'
        elif kind == wire.CANCEL:
            self._cancel_sent = True
        elif kind == wire.RELEASE:
            self._phase = 'releasing'

    def _consume(self, frame):
        self._last_kind = frame.kind
        with frame.body as body:
            fields = wire.STRUCTS[frame.kind].unpack(body) if frame.kind in wire.STRUCTS else ()
            if frame.kind == wire.PROGRESS:
                grant, turn, stdin, stdout, stderr, lifecycle, outcome = fields
                _require(self._data is not None and
                         (grant, turn) == (self._data_grant, self._turn), 'operation_data_receipt')
                self._data_known = stdin + stdout + stderr
                if outcome == 1:
                    self._progress_unknown = True
                    return
                self.resources.settle(self._data, self._data_known)
                self._data = None
            elif frame.kind == wire.OUTPUT:
                self._decoders = tuple(BodyDecoder(max(1, size)) for size in fields[2:4])
            elif frame.kind in (wire.STDOUT, wire.STDERR):
                _require(self._decoders is not None, 'operation_text_before_output')
                self._decoders[0 if frame.kind == wire.STDOUT else 1].feed(body)
            elif frame.kind == wire.REFUSE:
                self._refusal = fields[0]
                if fields[0] != 6:
                    self._prior_refusal = fields[0]
            elif frame.kind == wire.RELEASED:
                _require(fields == (self.lease, 0), 'operation_release_unknown')
        # No borrowed Frame, memoryview or record survives this callback.

    def _after_receive(self):
        if self._progress_unknown:
            self._poison('operation_progress_unknown')
        if self._unknown:
            return
        kind = self._last_kind
        state = self.link.transcript.snapshot()
        if self._rx_mode == 'text':
            # DELIVER has exactly one direct reply. Any automatic STATUS or
            # crossed ACK was committed in B before DELIVER/CANCEL respectively.
            self._end_receive()
            self._finish_a()
            if self._future is not None:
                cursor, self._future = self._future, None
                self._adopt(self._b, cursor, 'terminal')
                if kind == wire.REFUSE and self._refusal == 6:
                    # This is impossible for a healthy queued DELIVER before
                    # CANCEL; retain the unobserved future on protocol ambiguity.
                    raise OperationError('operation_unobserved_terminal')
                if kind == wire.REFUSE and not self._cancel_queued:
                    # The last DELIVER may refuse before producing any text.
                    # Its precommitted terminal cursor is still unobserved; use
                    # that exact cursor for RELEASED instead of refunding it.
                    self._queue(wire.RELEASE, self._b, self.lease)
                    self._phase = 'releasing'
            else:
                self._phase = 'output' if kind != wire.REFUSE else 'refused'
            return
        if kind == wire.RELEASED:
            self._end_receive()
            self.resources.release(self._child)
            self._child = None
            self._finish_a()
            self._b.finish()
            self._b = None
            self._close()
            return
        if self._cancel_queued:
            if not state['cancel_acknowledged']:
                return
            _require(self._cancel_sent, 'operation_ack_before_cancel')
            self._end_receive()
            self._finish_a()
            self._phase = 'refused'
            return
        ended = (kind in (wire.PREPARED, wire.STARTED, wire.STATUS, wire.REFUSE) or
                 kind == wire.PROGRESS and state['progress_outcome'] == 0
                 and state['lifecycle'] != 31 or
                 kind == wire.OUTPUT and state['wire_counts'] != state['wire_sizes'])
        if ended:
            self._end_receive()
            self._finish_a()
            self._phase = {wire.PREPARED: 'prepared', wire.STARTED: 'running',
                           wire.PROGRESS: 'running', wire.OUTPUT: 'output',
                           wire.STATUS: 'result', wire.REFUSE: 'refused'}[kind]

    def _next_grant(self, ticket):
        # Both callers pass a newly issued ORIGINAL ticket: TURN's data ticket
        # or DELIVER's fresh A credit, never the older emergency B. Thus these
        # ordinals remain increasing across operations on this same ledger/link.
        _require(self._grant < ticket.ordinal <= 2**53-1, 'operation_grant_exhausted')
        self._grant = ticket.ordinal
        return self._grant

    def _schedule(self):
        if self._phase == 'prepare':
            if not self._stage(32 + wire.STRUCTS[wire.PREPARE].size, (), None):
                return
            name = ('search', 'pull', 'pull-evidence', 'history').index(self._request.operation) + 1
            self._queue(wire.PREPARE, self._a, name, self.deadline, self.lease,
                        self.quote.child_raw, self.quote.child_working,
                        self._request_size, self._digest)
        elif self._phase == 'request':
            if self._chunk is None or self._offset == len(self._chunk):
                self._chunk = next(self._chunks, None)
                self._offset = 0
            if self._chunk is None:
                if not self._stage(137, (72, 33), 'prepare'):
                    return
                self._queue(wire.REQUEST_END, self._a)
            else:
                count = min(512, len(self._chunk) - self._offset)
                if not self._stage(32 + count, (), None):
                    return
                with memoryview(self._chunk)[self._offset:self._offset + count] as piece:
                    self._queue(wire.REQUEST, self._a, data=piece)
                self._offset += count
        elif self._phase == 'prepared':
            _require(not self._dispatch_attempted, 'operation_dispatch_consumed')
            self._dispatch_attempted = True
            channel = self._gate.hook(self.deadline)
            try:
                self._handle = channel.start(self._request.operation,
                                             self._request.args, self._request.body)
            finally:
                channel.close()
        elif self._phase == 'running':
            # Hold both control credits BEFORE selecting the CLI allowance.
            if not self._stage(406, (56, 224, 40, 33), 'turn'):
                return
            available = self.resources.snapshot()['traffic_available']
            final_left = self.link.transcript.snapshot()['final_remaining']
            maximum = min(1536, available)
            if final_left is not None:
                maximum = min(maximum, final_left)
            # A zero final offer need not mean the original ledger is exhausted.
            # Hold an actual one-byte ticket but offer its known zero subset.
            held = 1 if maximum == 0 and available > 0 else maximum
            self._data = self.resources.grant(self.link.domain, 'data', held)
            _require(self._data is not None, 'operation_data_denied')
            self._data_known = 0
            self._data_grant = self._next_grant(self._data)
            self._turn += 1
            self._offer = maximum
            self._queue(wire.TURN, self._a, self._data_grant, self._turn, maximum,
                        int(final_left is not None or maximum < 1536))
        elif self._phase == 'output':
            state = self.link.transcript.snapshot()
            remaining = tuple(total - count for total, count in
                              zip(state['wire_sizes'], state['wire_counts']))
            stream = 1 if remaining[0] else 2
            _require(remaining[stream - 1] > 0, 'operation_output_pending_status')
            count = min(512, remaining[stream - 1],
                        self.resources.snapshot()['traffic_available'] - 75)
            if count <= 0:
                self._capacity_stop()
                return
            if not self._stage(43 + 32 + count, (32 + count,), 'text'):
                return
            if sum(remaining) == count:
                self._future_terminal((41, 41, 41))
            self._queue(wire.DELIVER, self._a, self._next_grant(self._a.ticket), stream, count)
        elif self._phase in ('result', 'refused'):
            self._adopt(self._b, self._b.burst((41,)), 'release')
            self._queue(wire.RELEASE, self._b, self.lease)
            self._phase = 'releasing'
        else:
            raise OperationError('operation_schedule_state')

    def step(self):
        _require(not self._busy and not self._unknown and not self._retired
                 and self._phase != 'unprepared', 'operation_unavailable')
        if self._closed:
            return False
        self._busy = True
        try:
            try:
                self._now()
                if self._tx_kind is not None:
                    if self.link.send_step():
                        kind, self._tx_kind = self._tx_kind, None
                        self._after_send(kind)
                        return True
                    if self._rx_credit is None:
                        return False
                elif (self._cancel_reason is not None and not self._cancel_queued
                      and self._phase != 'prepare' and self._phase != 'releasing'):
                    self._send_cancel()
                    return True
                if self._rx_credit is not None:
                    if not self.link.receive_step(self._consume):
                        return False
                    self._after_receive()
                    return True
                self._schedule()
                return True
            finally:
                self._busy = False
        except BaseException as exc:
            self._poison(type(exc).__name__)
            raise

    def _close(self):
        state = self.link.transcript.snapshot()
        if self._refusal is None and self._budget_refusal is None:
            _require(state['returncode'] is not None and self._decoders is not None,
                     'operation_result_missing')
            stdout, stderr = (decoder.finish() for decoder in self._decoders)
            self._result = CommandTextOutcome(state['returncode'], stdout, stderr,
                                             *state['raw_counts'][1:])
        else:
            self._result = dict(refusal=self._refusal,
                                prior_refusal=self._prior_refusal,
                                budget_refusal=self._budget_refusal,
                                cancelled=state['cancel_acknowledged'], api_confirmation=None)
        if self._handle is not None:
            _require(self._gate.complete_pending(self._handle) is self, 'operation_gate_result')
            self._handle = None
        self._closed, self._phase = True, 'closed'

    def result(self):
        _require(self._closed and not self._unknown and not self._retired,
                 'operation_result_pending')
        return self._result

    def retire(self):
        """Source caller first ends/transfers ALL aliases returned by result()."""
        _require(self._closed and not self._unknown and not self._retired,
                 'operation_retirement_pending')
        try:
            self._request = self._document = self._chunks = self._chunk = None
            self._decoders = self._result = None
            self.resources.release(self._parent)
            self._parent = None
            self._retired = True
        except BaseException as exc:
            self._poison(type(exc).__name__)
            raise

    def snapshot(self):
        return dict(phase=self._phase, unknown=self._unknown, primary=self._primary,
                    closed=self._closed, retired=self._retired, cleanup_error=self._cleanup_error,
                    dispatch_attempted=self._dispatch_attempted, forwarded=self._forwarded,
                    budget_refusal=self._budget_refusal,
                    cancel_queued=self._cancel_queued, cancel_sent=self._cancel_sent,
                    pending_gate_handle=self._handle is not None, turn=self._turn,
                    data_grant=self._data_grant, offered=self._offer,
                    data_ticket_held=self._data is not None, parent_allocation=self._parent is not None,
                    child_allocation=self._child is not None,
                    qualification='pure_coordinator_only')
