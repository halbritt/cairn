"""Nonblocking hook-producer RPC bridge under one original parent ledger.

The fixed builder supplies an actual owner/domain/allocation before constructing
either endpoint's buffers. It must authorize each held request before handing in
its actual prepared CommandOperation. This library does not supply that policy,
launch the producer, pump its stdio, or admit native forwarding.
"""
from dataclasses import dataclass
import hashlib
import select
import time

from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_command_operation import CommandOperation
from trial_sdk_command_service_broker import ServiceLink, WireCredit
from trial_sdk_control import Callback
from trial_sdk_engine_adapter import CommandTextOutcome
from trial_sdk_engine_memory_rpc_client import ClientQuote
from trial_sdk_incoming import BodyDecoder, parse_document, canonical_size_sha256
from trial_sdk_ipc import Codec
from trial_sdk_memory_broker_socket import BrokerListener
from trial_sdk_memory_gate import MemoryDispatchGate, HookRequest
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_parent_storage import Root, Channel
from trial_sdk_peer import AUXILIARY
from trial_sdk_stream_credit import StreamCredit, StreamCreditDenied
from trial_sdk_stream_driver import StreamEvent
from trial_sdk_resource_failure import freeze_resource
import trial_sdk_engine_memory_rpc_wire as wire
import trial_sdk_record_custody as custody


@dataclass(frozen=True)
class RPCQuote:
    """Combined callback lifetime, including retained producer result aliases.

    The builder includes its producer driver's simultaneous allocations too.
    These minimum shape checks do not certify physical fit or source authority.
    """
    raw: int
    working: int
    client: ClientQuote

    def check(self):
        wire.require(type(self.client) is ClientQuote, 'rpc_quote')
        self.client.check()
        wire.require(type(self.raw) is int and self.raw >= self.client.raw + 2080 and
                     type(self.working) is int and self.working > self.client.working,
                     'rpc_quote')


@dataclass(frozen=True)
class PendingRequest:
    operation: int
    deadline_ns: int
    request: HookRequest
    raw_bytes: int
    sha256: str


class ProducerRPC:
    RAW_STORAGE = 512 + 544 + 544 + AUXILIARY

    def __init__(self, resources, owner, domain, allocation, *, gate, link, event,
                 quote, clock=time.monotonic_ns):
        wire.require(type(resources) is AttemptResources and type(owner) is NativeProcessOwner
                     and type(gate) is MemoryDispatchGate and type(link) is ServiceLink
                     and link.resources is resources and type(quote) is RPCQuote,
                     'rpc_parent_binding')
        quote.check()
        # These are original parent-issued capabilities, not caller permission
        # flags. The source builder remains responsible for the allocation quote.
        resources._check()
        wire.require(resources._domain(domain)['owner'] is owner, 'rpc_owner_domain')
        held_domain, lifetime, raw, working = resources._lookup(resources._memory, allocation)
        wire.require(held_domain is domain and lifetime == 'operation' and
                     raw >= quote.raw and working >= quote.working, 'rpc_held_allocation')
        wire.require(type(event) is StreamEvent and event.kind == 'callback' and
                     type(event.callback) is Callback and type(event.observation) is dict,
                     'rpc_callback_binding')
        observed = event.observation
        wire.require(set(observed) == {'request_sha256', 'first_byte_ns', 'deadline_ns'} and
                     type(observed['first_byte_ns']) is int and observed['first_byte_ns'] >= 0
                     and type(observed['deadline_ns']) is int and
                     observed['deadline_ns'] == observed['first_byte_ns'] + 5_000_000_000
                     and observed['request_sha256'] == hashlib.sha256(
                         event.callback.request_id.encode()).hexdigest(), 'rpc_callback_binding')
        self.resources, self.owner, self.domain, self._allocation = resources, owner, domain, allocation
        self.gate, self.link, self.quote = gate, link, quote
        self._callback_deadline = self._deadline = observed['deadline_ns']
        self._clock, self._last_clock = clock, None
        self._busy = self._unknown = self._closed = self._retired = self._auxiliary = False
        self._primary = self._cleanup_error = self._io_recovery_error = None
        self._recovery_faults = set()
        self._listener = self._connection = self._root = None
        self._control = self._stream = self._operation = None
        self._tx = self._rx = self._io = None
        self._tx_kind = self._future = self._transaction = self._generation = None
        self._decoder = self._request = self._result = self._wire_sizes = None
        self._read_custody = None
        self._command_owner = None
        self._call_attempts = dict.fromkeys(('search', 'pull', 'pull-evidence', 'history', 'other'), 0)
        self._text_chunk = None
        self._phases = self._status_code = None
        self._text_stream = self._text_position = self._text_offset = 0
        self._cancel_reason = None
        self._phase = 'idle'
        self.transcript = wire.Transcript(self._callback_deadline)
        try:
            self._control = WireCredit(resources, domain, wire.BOOTSTRAP)
            self._stream = StreamCredit(resources, domain, 1)
            self._root = Root(self.RAW_STORAGE - AUXILIARY)
            self._body = self._root.allocate('body', 512)
            self._tx_region = self._root.allocate('tx', 544)
            self._rx_region = self._root.allocate('rx', 544)
            self._sender = Channel(Codec, self._root, self._tx_region, 'B2C', profile=5)
            self._receiver = Channel(Codec, self._root, self._rx_region, 'C2B', profile=5)
            self._arm(self._control, self._control.promise(76))
        except BaseException as exc:
            self._poison(type(exc).__name__)
            raise

    def _now(self):
        now = self._clock()
        wire.require(type(now) is int and now >= 0 and
                     (self._last_clock is None or now >= self._last_clock), 'rpc_clock')
        self._last_clock = now
        wire.require(now < self._deadline, 'rpc_deadline')

    def _enter(self):
        wire.require(not self._busy and not self._unknown and not self._retired and
                     not self._closed, 'rpc_parent_unavailable')
        self._busy = True
        self.resources._check()
        self._now()

    def _poison(self, code):
        self._unknown = True
        if self._primary is None:
            self._primary = code
        # Shared extraction preserves the exact post-syscall receipt before any
        # other credit can freeze the original bank.
        try:
            custody.reconcile_interrupted_transfer(self)
        except BaseException:
            self._recovery_faults.add('rpc_receipt_recovery_unknown')
        if self._root is not None:
            self._root.unknown = True
        try:
            if self._stream is not None and not self._stream.snapshot()['closed']:
                self._stream.quarantine()
        except BaseException as exc:
            self._cleanup_error = type(exc).__name__
            self._recovery_faults.add('rpc_stream_recovery_unknown')
        try:
            if self._control is not None and not self._control.snapshot()['closed']:
                self._control._poison()
        except BaseException:
            self._recovery_faults.add('rpc_control_recovery_unknown')
        try:
            if self._operation is not None:
                self._operation._poison(code)
        except BaseException:
            self._recovery_faults.add('rpc_operation_recovery_unknown')
        self._recovery_faults.update(freeze_resource(self.resources))

    def _socket_prefix(self, pending, direction, before):
        return custody.socket_prefix(self, pending, direction, before)

    def _reserve_auxiliary(self, size):
        wire.require(size == AUXILIARY and not self._auxiliary, 'rpc_auxiliary')
        self._auxiliary = True
        return True  # Already included in the actual held callback allocation.

    def _release_auxiliary(self, size):
        wire.require(size == AUXILIARY and self._auxiliary, 'rpc_auxiliary')
        self._auxiliary = False

    def prestart(self, directory):
        try:
            try:
                self._enter()
                wire.require(self._listener is None and
                             not self.owner.snapshot()['launch_attempted'], 'rpc_prestart')
                self._listener = BrokerListener()
                self._listener.start(directory, self._callback_deadline,
                                     self._reserve_auxiliary, self._release_auxiliary)
            except BaseException as exc:
                self._poison(type(exc).__name__)
                raise
            finally:
                self._busy = False
        except BaseException as exc:
            if not self._unknown:
                self._poison(type(exc).__name__)
            raise

    def accept(self):
        try:
            try:
                self._enter()
                wire.require(self._listener is not None and self._connection is None, 'rpc_accept')
                self._connection = self._listener.accept(self.owner)
                return self._connection is not None
            except BaseException as exc:
                self._poison(type(exc).__name__)
                raise
            finally:
                self._busy = False
        except BaseException as exc:
            if not self._unknown:
                self._poison(type(exc).__name__)
            raise

    def _arm(self, credit, promise):
        wire.require(self._rx is None, 'rpc_receive_pending')
        self._rx = dict(credit=credit, promise=promise, hold=self._rx_region.hold(promise.maximum))

    def _queue(self, kind, credit, *fields, data=None):
        wire.require(self._tx is None, 'rpc_send_pending')
        size = wire.STRUCTS[kind].size if data is None else len(data)
        promise = credit.promise(32 + size)
        body = self._body.hold(size)
        self._tx = dict(credit=credit, promise=promise, body=body, token=None)
        if data is None:
            wire.STRUCTS[kind].pack_into(body.view, 0, *fields)
        else:
            body.view[:] = data
        operation = 0 if kind == wire.SESSION_CLOSED else self.transcript.operation
        self._tx['token'] = self._sender.encode(kind, operation, body)
        self._tx_kind = kind

    def _finish_transaction(self):
        # The serialized source coordinator must finish each borrowed stdio
        # action's receipt before calling step again. EAGAIN is an actual zero
        # receipt for that stdio action, not permission to drop the RPC promise.
        state = self._stream.snapshot()
        wire.require(state['pending'] == 0 and state['future_reserved'] == 0,
                     'rpc_transaction_pending')
        self._transaction = self._generation = self._future = None

    def _stream_room(self):
        state = self._stream.snapshot()
        return state['maximum'] - state['known'] - state['promised'] - state['future_reserved']

    def _request_credit(self):
        count = min(512, self.transcript.request_size - self.transcript.request_count)
        maximum = 34 + 32 + count
        if self.resources.snapshot()['traffic_available'] + self._stream_room() < maximum:
            self._refuse(3)
            return
        self._queue(wire.REQUEST_CREDIT, self._stream, count)
        self._transaction = self._tx
        self._generation = self._tx['token'].generation
        self._future = self._stream.protect_future_rx(
            self._transaction, self._connection, self._generation, 32 + count,
            self._tx['promise'])
        self._phase = 'request_wait'

    def _consume(self, frame):
        if frame.kind == wire.BEGIN:
            self._deadline = self.transcript.deadline
            self._now()
            self._decoder = BodyDecoder(self.transcript.request_size)
            self._phase = 'request'
        elif frame.kind == wire.REQUEST:
            with frame.body as body:
                self._decoder.feed(body)
            self._phase = 'request'
        elif frame.kind == wire.REQUEST_END:
            text = self._decoder.finish()
            document = parse_document(text, self.transcript.request_size)
            wire.request_shape(document)
            size, digest = canonical_size_sha256(document)
            wire.require((size, digest) == (self.transcript.request_size,
                                          self.transcript.digest.hex()), 'rpc_canonical_request')
            self._request = PendingRequest(self.transcript.operation, self._deadline,
                HookRequest(document['operation'], tuple(document['args']), document['body']),
                size, digest)
            operation = document['operation']
            self._call_attempts[operation if operation in self._call_attempts else 'other'] += 1
            self._decoder = None
            self._phase = 'handoff'
        elif frame.kind == wire.SESSION_END:
            self._deadline = self._callback_deadline
            self._phase = 'closing'

    def pending_request(self):
        """Borrow immutable request facts under the still-held callback quote.

        This is not a read grant. Fixed source checks current-read, credential,
        route and source custody before preparing the operation for handoff.
        """
        return self._pending_request(None)

    def _pending_request(self, owner):
        """Internal fixed-source borrow; never returned by the fixed owner."""
        wire.require(owner is self._command_owner, 'rpc_exclusive_command_owner')
        wire.require(not self._unknown and self._phase == 'handoff', 'rpc_request_pending')
        return self._request

    def bind_producer(self, producer):
        """Opt into original-ticket phases before the actual producer starts."""
        from trial_sdk_callback_phases import CallbackPhases
        try:
            wire.require(self._phases is None, 'rpc_phase_binding')
            self._phases = CallbackPhases(self, producer)
        except BaseException as exc:
            self._poison(type(exc).__name__)
            raise

    def prepare_command_phase(self):
        """After REQUEST_END's consumer/loan returns; incomplete input parks."""
        try:
            wire.require(self._phases is not None and not self._unknown,
                         'rpc_phase_binding')
            self.resources._check()
            self._now()
            return self._phases.enter_command()
        except BaseException as exc:
            self._poison(type(exc).__name__)
            raise

    def start_operation(self, operation):
        """Accept only the exact prepared command; never authorize from its type."""
        return self._start_operation(operation, None)

    def _start_operation(self, operation, owner):
        try:
            try:
                self._enter()
                wire.require(owner is self._command_owner, 'rpc_exclusive_command_owner')
                wire.require(self._phases is None or self._phases.state.phase == 'command',
                             'rpc_phase_boundary')
                wire.require(self._phase == 'handoff' and self._operation is None and
                             type(operation) is CommandOperation and operation.link is self.link
                             and operation.resources is self.resources and operation._gate is self.gate,
                             'rpc_operation_binding')
                self._operation = operation  # Retain this owned allocation on later mismatch.
                state = operation.snapshot()
                wire.require(operation.deadline == self._request.deadline_ns and
                             operation._request == self._request.request and
                             operation._request_size == self._request.raw_bytes and
                             operation._digest.hex() == self._request.sha256 and
                             state['phase'] == 'prepare' and not state['dispatch_attempted'] and
                             not state['unknown'], 'rpc_operation_request')
                self._phase = 'command'
            except BaseException as exc:
                self._poison(type(exc).__name__)
                raise
            finally:
                self._busy = False
        except BaseException as exc:
            if not self._unknown:
                self._poison(type(exc).__name__)
            raise

    def cancel(self, reason=4):
        wire.require(type(reason) is int and reason in (1, 2, 3, 4, 5) and
                     not self._unknown and not self._retired, 'rpc_cancel')
        if self._closed or self._phase in ('phase_return', 'status', 'closing', 'refused'):
            return False
        if self._cancel_reason is None:
            self._cancel_reason = reason
        if self._operation is not None and not self._operation.snapshot()['closed']:
            self._operation.cancel(1)
        return True

    def _retire_operation(self):
        self._result = self._request = self._wire_sizes = self._text_chunk = None
        if self._operation is not None:
            self._operation.retire()
            self._operation = None

    def _refuse(self, reason):
        wire.require(self._tx is None and self._rx is None and
                     self._stream.snapshot()['pending'] == 0 and
                     self._stream.snapshot()['future_reserved'] == 0,
                     'rpc_refusal_pending')
        if self._phases is not None and self._operation is not None:
            # _text may still borrow its text and RPC-owned chunk here. All
            # command working transfers cumulatively; SURVIVING_RAW continues
            # to charge that callback chunk512, including any active part view.
            self._phases.retain_command_working()
        self._retire_operation()
        self._decoder = None
        self._arm(self._control, self._control.promise(32))
        self._queue(wire.REFUSE, self._control, reason)
        self._phase = 'refused'

    def _result_frame(self, kind, *fields, data=None):
        maximum = 32 + (wire.STRUCTS[kind].size if data is None else len(data))
        if self.resources.snapshot()['traffic_available'] + self._stream_room() < maximum:
            self._refuse(3)
            return False
        self._queue(kind, self._stream, *fields, data=data)
        return True

    def _text(self):
        while self._text_stream < 2:
            text = self._result.stdout if self._text_stream == 0 else self._result.stderr
            if self._text_chunk is None or self._text_offset == len(self._text_chunk):
                if self._text_position == len(text):
                    self._text_stream += 1
                    self._text_position = self._text_offset = 0
                    self._text_chunk = None
                    continue
                end = min(len(text), self._text_position + 128)
                self._text_chunk = text[self._text_position:end].encode('utf-8')
                self._text_position, self._text_offset = end, 0
            room = self.resources.snapshot()['traffic_available'] + self._stream_room() - 32
            if room <= 0:
                self._refuse(3)
                return
            count = min(room, len(self._text_chunk) - self._text_offset)
            with memoryview(self._text_chunk)[self._text_offset:self._text_offset + count] as part:
                self._result_frame(wire.STDOUT if self._text_stream == 0 else wire.STDERR, data=part)
            self._text_offset += count
            return
        if self._phases is not None:
            self._status_code = self._result.returncode
            self._phase = 'phase_return'
            return  # End this frame's local stderr alias BEFORE retiring.
        self._prepare_status(self._result.returncode)

    def _prepare_status(self, code):
        # Arm the next unsolicited BEGIN/END BEFORE STATUS returns the client.
        # Old control has97 unused: replacement173 needs76 genuinely global
        # bytes. STATUS36 may use already-held stream slack; without it need112.
        needed = 76 + max(0, 36 - self._stream_room())
        if self.resources.snapshot()['traffic_available'] < needed:
            self._refuse(3)
            return
        self._control.finish()
        self._control = WireCredit(self.resources, self.domain, wire.BOOTSTRAP)
        self._arm(self._control, self._control.promise(76))
        self._result_frame(wire.STATUS, code)
        self._phase = 'status'

    def _after_send(self, kind):
        if kind == wire.REQUEST_CREDIT:
            promise = self._stream.promote_future_rx(self._future, self._transaction,
                                                     self._connection, self._generation)
            self._future = None
            self._arm(self._stream, promise)
        elif kind == wire.SESSION_CLOSED:
            self._control.finish()
            self._control = None
            # Child awaits actual EOF, keeping this send's post-live check sound.
            self._connection.close()
            wire.require(self._connection.snapshot()['closed_known'], 'rpc_close_unknown')
            if self._listener is not None:
                self._listener.close()
                state = self._listener.snapshot()
                wire.require(not state['cleanup'] and not state['has_handles'], 'rpc_close_unknown')
            self._closed = True
        elif kind == wire.REFUSE:
            self._deadline = self._callback_deadline
        elif kind != wire.REFUSE:
            self._finish_transaction()
            if kind == wire.STATUS:
                if self._phases is None:
                    self._retire_operation()
                self._deadline = self._callback_deadline
                self._phase = 'idle'
            elif kind == wire.RESULT_META:
                self._phase = 'text'

    def step(self):
        if self._closed:
            return False
        try:
            try:
                self._enter()
                wire.require(self._connection is not None, 'rpc_not_connected')
                if self._tx is not None:
                    if not custody.send_step(self):
                        return False
                    self._now()
                    kind, self._tx_kind = self._tx_kind, None
                    self._after_send(kind)
                    return True
                if self._rx is not None:
                    credit = self._rx['credit']
                    if not custody.receive_step(self, self._consume, wire.RPCError):
                        return False
                    self._now()
                    if credit is self._stream:
                        self._finish_transaction()
                    return True
                if self._cancel_reason is not None and self._phase in ('request', 'handoff'):
                    self._refuse(self._cancel_reason)
                elif self._phase == 'request':
                    self._request_credit()
                elif self._phase == 'handoff':
                    return False
                elif self._phase == 'command':
                    progressed = self._operation.step()
                    if not self._operation.snapshot()['closed']:
                        return progressed
                    if self._cancel_reason is not None:
                        self._refuse(self._cancel_reason)
                        return True
                    self._result = self._operation.result()
                    if type(self._result) is not CommandTextOutcome:
                        self._refuse(5)
                        return True
                    # These are the actual service's validated OUTPUT wire counts,
                    # not a replacement-encoding estimate or another traffic debit.
                    if self._read_custody is not None:
                        self._read_custody.observe_completed()
                    self._wire_sizes = self.link.transcript.snapshot()['wire_sizes']
                    self._text_stream = self._text_position = self._text_offset = 0
                    self._result_frame(wire.RESULT_META, self._result.stdout_bytes,
                                       self._result.stderr_bytes, *self._wire_sizes)
                elif self._phase == 'text':
                    if self._cancel_reason is not None:
                        self._refuse(self._cancel_reason)
                    else:
                        self._text()
                elif self._phase == 'phase_return':
                    self._phases.before_status()
                    self._prepare_status(self._status_code)
                elif self._phase == 'closing':
                    self._queue(wire.SESSION_CLOSED, self._control)
                return True
            except StreamCreditDenied:
                # Existing preflight prevents normal known denial. A source-owned
                # stdio action can consume room only between steps, never during one.
                self._poison('rpc_credit_handoff_unknown')
                raise
            except BaseException as exc:
                self._poison(type(exc).__name__)
                raise
            finally:
                self._busy = False
        except BaseException as exc:
            if not self._unknown:
                self._poison(type(exc).__name__)
            raise

    def stream_credit(self):
        """Borrow the actual producer DATA credit for one serialized stdio action.

        The fixed coordinator uses promise_stdio BEFORE the actual owner edge,
        records its fresh receipt, and ends that promise before another RPC step.
        No second DATA grant is allowed. The SAME credit remains held before,
        during and after RPC channel closure until the actual producer lifetime
        ends, including its final stdout/stderr and wait/release observations.
        """
        wire.require(not self._unknown and not self._retired, 'rpc_parent_unavailable')
        return self._stream

    def interests(self):
        wire.require(not self._unknown and not self._retired, 'rpc_parent_unavailable')
        if self._closed:
            return ()
        if self._connection is None:
            return () if self._listener is None else ((self._listener.fileno(), select.POLLIN),)
        events = (select.POLLOUT if self._tx is not None else 0)
        events |= select.POLLIN if self._rx is not None else 0
        own = () if not events else ((self._connection.fileno(), events),)
        return own + (() if self._operation is None else self.link.interests())

    def retire(self):
        """Source first ends/transfers all parent aliases under this allocation."""
        wire.require(self._closed and not self._unknown and not self._retired and
                     self._control is None and self._stream is not None and self._operation is None
                     and self._tx is None and self._rx is None and not self._auxiliary,
                     'rpc_retirement_pending')
        release = self.owner.snapshot()['release']
        wire.require(release is not None and release.get('released') is True,
                     'rpc_retirement_pending')
        try:
            self._stream.finish()
            self._stream = None
            for region in (self._body, self._tx_region, self._rx_region):
                region.retire()
            if self._read_custody is not None and self._read_custody._service_custody is not None:
                self._read_custody._service_custody.release_producer_sources(self)
                self._phases = None
            self._read_custody = None
            self._command_owner = None
            self.resources.release(self._allocation)
            self._allocation = None
            self._retired = True
        except BaseException as exc:
            self._poison(type(exc).__name__)
            raise

    def snapshot(self):
        return dict(phase=self._phase, closed=self._closed, retired=self._retired,
                    unknown=self._unknown or self.resources.snapshot()['unknown'],
                    primary=self._primary, cleanup_error=self._cleanup_error,
                    recovery_faults=tuple(sorted(self._recovery_faults)),
                    io_recovery_error=self._io_recovery_error, deadline_ns=self._deadline,
                    stream_held=self._stream is not None,
                    memory_phase=None if self._phases is None else self._phases.snapshot(),
                    local_work=self._tx is None and self._rx is None and
                        self._phase in ('request', 'command', 'text', 'phase_return', 'closing'),
                    pending_operation=self._operation is not None,
                    send_pending=self._tx is not None, receive_pending=self._rx is not None,
                    transcript=self.transcript.snapshot(), qualification='hook_bridge_library_only')
