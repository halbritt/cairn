"""Blocking channel for the independently owned producer, never the parent pump.

The fixed builder must hold the child's complete callback allocation before
construction, including canonical scratch, decoded results retained by its engine
and socket auxiliary storage. This process does not create another traffic bank.
"""
from dataclasses import dataclass
import math
import select
import time

from trial_sdk_engine_adapter import CommandTextOutcome
from trial_sdk_incoming import BodyDecoder, canonical_chunks, canonical_size_sha256
from trial_sdk_ipc import Codec, MAX_OPERATION
from trial_sdk_memory_broker_socket import RecordSocket
from trial_sdk_parent_storage import Root, Channel
from trial_sdk_peer import AUXILIARY
import trial_sdk_engine_memory_rpc_wire as wire


@dataclass(frozen=True)
class ClientQuote:
    raw: int
    working: int

    def check(self):
        wire.require(type(self.raw) is int and self.raw >= 32768 + 2080 and
                     type(self.working) is int and self.working > 0, 'rpc_client_quote')


class RPCRefused(wire.RPCError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('rpc_refused_%d' % reason)


class ProducerClient:
    RAW_STORAGE = 512 + 544 + 544 + AUXILIARY

    def __init__(self, deadline_ns, quote, *, clock=time.monotonic_ns):
        wire.require(type(quote) is ClientQuote and callable(clock), 'rpc_client_binding')
        quote.check()
        self.transcript = wire.Transcript(deadline_ns)
        self._callback_deadline = self._deadline = deadline_ns
        self._clock, self._last_clock = clock, None
        self._busy = self._failed = self._closed = self._auxiliary = False
        self._connection = None
        self._root = Root(self.RAW_STORAGE - AUXILIARY)
        self._body = self._root.allocate('body', 512)
        self._tx_region = self._root.allocate('tx', 544)
        self._rx_region = self._root.allocate('rx', 544)
        self._sender = Channel(Codec, self._root, self._tx_region, 'C2B', profile=5)
        self._receiver = Channel(Codec, self._root, self._rx_region, 'B2C', profile=5)

    def _reserve_auxiliary(self, size):
        wire.require(size == AUXILIARY and not self._auxiliary, 'rpc_auxiliary')
        self._auxiliary = True
        return True  # Included in the builder's already-held child allocation.

    def _release_auxiliary(self, size):
        wire.require(size == AUXILIARY and self._auxiliary, 'rpc_auxiliary')
        self._auxiliary = False

    def connect(self, path):
        wire.require(self._connection is None and not self._failed and not self._closed,
                     'rpc_connect_consumed')
        self._connection = RecordSocket()
        try:
            self._now()
            self._connection.connect_parent(path, self._callback_deadline,
                                            self._reserve_auxiliary, self._release_auxiliary)
        except BaseException:
            self._failed = self._root.unknown = True
            raise

    def _now(self):
        now = self._clock()
        wire.require(type(now) is int and now >= 0 and
                     (self._last_clock is None or now >= self._last_clock), 'rpc_clock')
        self._last_clock = now
        wire.require(now < self._deadline, 'rpc_deadline')
        return now

    def _wait(self, events):
        now = self._now()
        poll = select.poll()
        poll.register(self._connection.fileno(), events)
        poll.poll(min(50, max(1, (self._deadline - now + 999999) // 1000000)))
        self._now()

    def _send(self, kind, operation, *fields, data=None):
        size = wire.STRUCTS[kind].size if data is None else len(data)
        body = self._body.hold(size)
        if data is None:
            wire.STRUCTS[kind].pack_into(body.view, 0, *fields)
        else:
            body.view[:] = data
        token = self._sender.encode(kind, operation, body)
        def send(frame):
            with frame.record as record:
                return self._connection.send(record)
        while True:
            self._now()
            count = token.visit(send)
            if count != -1:
                break
            self._wait(select.POLLOUT)
        self._now()
        token.visit(self.transcript.observe)
        token.complete(token.generation, 'known')
        body.close()

    def _receive(self, consume=None):
        hold = self._rx_region.hold(544)
        while True:
            self._now()
            count = self._connection.receive_into(hold.view)
            if count != -1:
                break
            self._wait(select.POLLIN)
        hold.close()
        hold = self._rx_region.hold(count)
        token = self._receiver.decode(hold)
        token.visit(self.transcript.observe)
        kind = token.visit(lambda frame: frame.kind)
        if consume is not None:
            token.visit(consume)
        token.complete(token.generation, 'known')
        self._now()
        if kind == wire.REFUSE:
            raise RPCRefused(self.transcript.refusal)
        return kind

    def call(self, operation, args=(), body=None, timeout=5):
        wire.require(not self._busy and not self._failed and not self._closed and
                     self._connection is not None and self.transcript.phase == 'idle',
                     'rpc_client_unavailable')
        try:
            self._busy = True
            try:
                self._deadline = self._callback_deadline
                start = self._now()
                wire.require(type(timeout) in (int, float) and math.isfinite(timeout) and
                             0 < timeout <= 5, 'rpc_timeout')
                self._deadline = min(self._callback_deadline, start + int(timeout * 1000000000))
                wire.require(type(args) is tuple, 'rpc_request_shape')
                document = dict(operation=operation, args=list(args), body=body)
                wire.request_shape(document)
                size, digest = canonical_size_sha256(document)
                wire.require(0 < size <= wire.REQUEST_LIMIT, 'rpc_request_size')
                ordinal = self.transcript.operation + 1
                wire.require(ordinal <= MAX_OPERATION, 'rpc_operation_exhausted')
                self._send(wire.BEGIN, ordinal, self._deadline, size, bytes.fromhex(digest))
                chunks = canonical_chunks(document)
                chunk, offset = None, 0
                while True:
                    wire.require(self._receive() == wire.REQUEST_CREDIT, 'rpc_expected_credit')
                    permitted = self.transcript.permit
                    if not permitted:
                        # END permits the parent's canonical rehash to reuse this
                        # quoted scratch. Drop both generator and yielded aliases.
                        chunks.close()
                        chunks = chunk = None
                        self._send(wire.REQUEST_END, ordinal)
                        break
                    if chunk is None or offset == len(chunk):
                        chunk, offset = next(chunks), 0
                    count = min(permitted, len(chunk) - offset)
                    with memoryview(chunk)[offset:offset + count] as part:
                        self._send(wire.REQUEST, ordinal, data=part)
                    offset += count
                wire.require(self._receive() == wire.RESULT_META, 'rpc_expected_meta')
                decoders = tuple(BodyDecoder(max(1, size)) for size in self.transcript.wire_sizes)
                def consume(frame):
                    if frame.kind in (wire.STDOUT, wire.STDERR):
                        with frame.body as text:
                            decoders[0 if frame.kind == wire.STDOUT else 1].feed(text)
                while self._receive(consume) != wire.STATUS:
                    pass
                self._now()
                stdout, stderr = (decoder.finish() for decoder in decoders)
                raw_stdout, raw_stderr = self.transcript.raw_counts
                wire.require(len(stdout) <= raw_stdout and len(stderr) <= raw_stderr,
                             'rpc_output_characters')
                return CommandTextOutcome(self.transcript.returncode, stdout, stderr,
                                          raw_stdout, raw_stderr)
            except RPCRefused:
                raise  # A known refusal still permits the bounded close handshake.
            except BaseException:
                self._failed = self._root.unknown = True
                raise
            finally:
                self._busy = False
        except RPCRefused:
            raise
        except BaseException:
            if not self._failed:
                self._failed = self._root.unknown = True
            raise

    def close(self):
        wire.require(not self._busy and not self._failed and not self._closed and
                     self._connection is not None and
                     self.transcript.phase in ('idle', 'refused'), 'rpc_close_unavailable')
        try:
            self._busy = True
            try:
                self._deadline = self._callback_deadline
                self._send(wire.SESSION_END, 0)
                wire.require(self._receive() == wire.SESSION_CLOSED, 'rpc_expected_ack')
                # ACK alone is insufficient: the parent still performs its post-send
                # liveness check. Stay alive until it completes that check and closes.
                hold = self._rx_region.hold(32)
                while True:
                    self._now()
                    count = self._connection.receive_terminal_into(hold.view)
                    if count != -1:
                        break
                    self._wait(select.POLLIN)
                self._now()
                wire.require(count == 0, 'rpc_terminal_eof')
                hold.close()
                self.transcript.terminal_eof()
                self._connection.close()
                wire.require(self._connection.snapshot()['closed_known'], 'rpc_close_unknown')
                self._closed = True
            except BaseException:
                self._failed = self._root.unknown = True
                raise
            finally:
                self._busy = False
        except BaseException:
            if not self._failed:
                self._failed = self._root.unknown = True
            raise

    def snapshot(self):
        return dict(closed=self._closed, failed=self._failed,
                    transcript=self.transcript.snapshot(),
                    connection=None if self._connection is None else self._connection.snapshot(),
                    qualification='producer_library_only')
