"""Parent-owned service record custody under one original AttemptResources.

This is a fixed builder mechanism, not native/API route admission. A wire credit
is a subcommitment of the parent's ledger, never another traffic pool.
"""
from dataclasses import dataclass
import select
import time

from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_memory_broker_socket import BrokerListener
from trial_sdk_peer import AUXILIARY
from trial_sdk_parent_storage import Root, Channel
from trial_sdk_ipc import Codec
import trial_sdk_command_service_wire as wire


class ServiceLinkError(ValueError):
    pass


@dataclass(frozen=True, eq=False)
class _Promise:
    maximum: int


class _Burst:
    __slots__ = ()


def _guard(method):
    def guarded(self, *args, **kwargs):
        acquired = False
        try:
            try:
                self._check()
                if self._busy:
                    raise ServiceLinkError('service_link_busy')
                self._busy, acquired, self._mutating = True, True, False
                answer = method(self, *args, **kwargs)
            finally:
                if acquired:
                    self._busy = False
            return answer
        except BaseException as exc:
            if acquired and (self._mutating or getattr(self, '_receipt', None) is not None):
                if self._primary is None:
                    self._primary = type(exc).__name__
                self._poison()
            raise
    return guarded


class WireCredit:
    """One original traffic ticket, with disjoint in-flight record promises.

Promise before authorizing either edge. Matching send/receive is charged at its
one designated observation; relay traffic needs another promise. finish returns
only unused commitment, after every promised edge has a known receipt.
"""
    def __init__(self, resources, domain, maximum):
        if type(resources) is not AttemptResources:
            raise ServiceLinkError('wire_original_resources')
        if type(maximum) is not int or maximum <= 0:
            raise ServiceLinkError('wire_control_credit')
        self.resources = resources
        self.ticket = resources.grant(domain, 'control', maximum)
        if self.ticket is None:
            raise ServiceLinkError('wire_credit_denied')
        self.maximum = maximum
        self._promises = set()
        self._pledged = self._known = 0
        self._closed = self._unknown = False
        self._busy = self._mutating = False
        self._quarantine_error = None
        self._receipt = None
        self._primary = None
        self._burst = None

    def _check(self):
        if self._unknown:
            raise ServiceLinkError('wire_credit_unknown')
        if self._closed:
            raise ServiceLinkError('wire_credit_closed')
        self.resources.grant_info(self.ticket)  # Also rejects shared uncertainty.

    def _poison(self):
        self._unknown = True
        try:
            if self._receipt is not None:
                # The count assignment may or may not have completed. Repeated
                # terminal observations must not apply that prefix twice.
                before, _, count = self._receipt
                if self._known == before:
                    self._known = before + count
            if not self._closed:
                self.resources.quarantine(self.ticket, self._known)
        except BaseException as exc:
            # Preserve cleanup failure without replacing the original failure.
            self._quarantine_error = type(exc).__name__
        finally:
            # Settlement may already have consumed this ticket before its outer
            # handoff was interrupted. A closed ticket cannot quarantine itself.
            self.resources.freeze()

    @_guard
    def promise(self, maximum):
        return self._promise(maximum)

    def _promise(self, maximum):
        if type(maximum) is not int or not 32 <= maximum <= 544:
            raise ServiceLinkError('wire_promise')
        if len(self._promises) >= 2:
            raise ServiceLinkError('wire_promise_slots')
        if self._known + self._pledged + maximum > self.maximum:
            raise ServiceLinkError('wire_credit_exhausted')
        self._mutating = True
        promise = _Promise(maximum)
        self._promises.add(promise)
        self._pledged += maximum
        return promise

    @_guard
    def burst(self, maxima):
        if (self._burst is not None or type(maxima) is not tuple or
                not 1 <= len(maxima) <= 4 or
                any(type(n) is not int or not 32 <= n <= 544 for n in maxima) or
                sum(maxima) > 544):
            raise ServiceLinkError('wire_burst_spec')
        promise = self._promise(sum(maxima))
        token = _Burst()
        self._burst = {'token': token, 'promise': promise, 'maxima': maxima,
                       'index': 0, 'remaining': sum(maxima), 'known': 0}
        return token

    def _burst_row(self, token):
        row = self._burst
        if type(token) is not _Burst or row is None or row['token'] is not token:
            raise ServiceLinkError('wire_burst_identity')
        return row

    def burst_info(self, token):
        self._check()
        row = self._burst_row(token)
        return {key: row[key] for key in ('maxima', 'index', 'remaining', 'known')}

    def _burst_prefix(self, token, count, advance):
        row = self._burst_row(token)
        if (row['index'] >= len(row['maxima']) or type(count) is not int or
                not 0 <= count <= row['maxima'][row['index']]):
            raise ServiceLinkError('wire_burst_count')
        self._receipt = (self._known, row['maxima'][row['index']], count)
        self._mutating = True
        self._known += count
        self._pledged -= count
        row['known'] += count
        row['remaining'] -= count
        if advance:
            row['index'] += 1
            self._receipt = None

    @_guard
    def burst_known(self, token, count):
        self._burst_prefix(token, count, True)

    @_guard
    def burst_unknown_prefix(self, token, count):
        self._burst_prefix(token, count, False)
        self._poison()

    @_guard
    def end_burst(self, token):
        """Owner first validates no additional automatic reply on this branch."""
        row = self._burst_row(token)
        if row['index'] == 0:
            raise ServiceLinkError('wire_burst_unobserved')
        self._mutating = True
        self._promises.remove(row['promise'])
        self._pledged -= row['remaining']
        self._burst = None

    @_guard
    def known(self, promise, count):
        if type(promise) is not _Promise or promise not in self._promises:
            raise ServiceLinkError('wire_promise')
        if type(count) is not int or not 0 <= count <= promise.maximum:
            raise ServiceLinkError('wire_count')
        self._receipt = (self._known, promise.maximum, count)
        self._mutating = True
        self._known += count
        self._promises.remove(promise)
        self._pledged -= promise.maximum
        self._receipt = None

    def quarantine(self):
        self._check()
        self._poison()

    @_guard
    def unknown_prefix(self, promise, count):
        if type(promise) is not _Promise or promise not in self._promises:
            raise ServiceLinkError('wire_promise')
        if type(count) is not int or not 0 <= count <= promise.maximum:
            raise ServiceLinkError('wire_count')
        self._receipt = (self._known, promise.maximum, count)
        self._mutating = True
        self._known += count
        # Leave the promise and loan unresolved; no unused portion can return.
        self._poison()

    @_guard
    def finish(self):
        if self._promises:
            raise ServiceLinkError('wire_receipt_pending')
        self._mutating = True
        self._unknown = True
        self.resources.settle(self.ticket, self._known)
        self._closed, self._unknown = True, False

    def snapshot(self):
        return {'maximum': self.maximum, 'known': self._known,
                'promised': self._pledged, 'pending': len(self._promises),
                'closed': self._closed, 'unknown': self._unknown,
                'primary': self._primary,
                'receipt_in_progress': None if self._receipt is None else self._receipt[1:],
                'quarantine_error': self._quarantine_error,
                'burst': None if self._burst is None else
                    {key: self._burst[key] for key in ('maxima', 'index', 'remaining', 'known')}}


class ServiceLink:
    """Actual direct service owner plus bounded parent-side profile4 transport.

    Fixed builder supplies source/route/environment custody and standing working
    quote. This class does not provide request authority, gate scheduling, child
    operation reservations or descendant containment. Those remain required.
    """
    RAW_STORAGE = 512 + 544 + 544 + AUXILIARY

    def __init__(self, resources, *, deadline_ns, standing_raw, standing_working,
                 clock=time.monotonic_ns):
        if type(resources) is not AttemptResources:
            raise ServiceLinkError('wire_original_resources')
        if (type(deadline_ns) is not int or deadline_ns <= 0 or
                type(standing_raw) is not int or standing_raw < self.RAW_STORAGE or
                type(standing_working) is not int or standing_working <= 0):
            raise ServiceLinkError('service_standing_quote')
        self.resources, self._clock, self._deadline = resources, clock, deadline_ns
        self.owner = NativeProcessOwner()
        self.domain = resources.bind(self.owner)
        self._allocation = resources.allocate(self.domain, 'standing',
                                              standing_raw, standing_working)
        self._root = self._listener = self._connection = None
        self._busy = self._mutating = self._unknown = False
        self._closed = self._started = self._auxiliary = False
        self._retired = False
        self._tx = self._rx = None
        self._io = None
        self._io_recovery_error = None
        self._credits = []
        self._primary = None
        if self._allocation is None:
            resources.retire(self.domain)
            raise ServiceLinkError('service_standing_denied')
        try:
            self._root = Root(self.RAW_STORAGE - AUXILIARY)
            self._body = self._root.allocate('body', 512)
            self._tx_region = self._root.allocate('tx', 544)
            self._rx_region = self._root.allocate('rx', 544)
            self._sender = Channel(Codec, self._root, self._tx_region, 'P2S', profile=4)
            self._receiver = Channel(Codec, self._root, self._rx_region, 'S2P', profile=4)
            self.transcript = wire.ServiceTranscript()
        except BaseException:
            # A partially constructed link is not a successful allocation handoff.
            # Keep the original ticket reserved rather than guessing alias release.
            self._unknown = True
            raise

    def _check(self):
        if self._unknown:
            raise ServiceLinkError('service_link_unknown')
        if self._closed:
            raise ServiceLinkError('service_link_closed')
        if self.resources.snapshot()['unknown']:
            raise ServiceLinkError('service_attempt_unknown')
        if self._clock() >= self._deadline:
            raise ServiceLinkError('service_link_deadline')

    def _poison(self):
        self._unknown = True
        # A boundary may return its actual count and then be interrupted before
        # the caller consumes it. Retain the pre-effect context across that
        # handoff. Credit guards already preserve a partially consumed receipt;
        # only a still-healthy, unchanged credit needs this fresh reconciliation.
        if self._io is not None:
            pending, direction, before, known = self._io
            credit = pending['credit']
            if not credit.snapshot()['unknown'] and credit.snapshot()['known'] == known:
                try:
                    self._socket_prefix(pending, direction, before)
                except BaseException as exc:
                    # Keep the original failure. The shared freeze below retains
                    # all unresolved commitments even if recovery is interrupted.
                    self._io_recovery_error = type(exc).__name__
                    self.resources.freeze()
        if self._root is not None:
            self._root.unknown = True
        for credit in self._credits:
            if not credit.snapshot()['closed'] and not credit.snapshot()['unknown']:
                credit._poison()

    def _reserve_auxiliary(self, size):
        if self._auxiliary or size != AUXILIARY:
            raise ServiceLinkError('service_auxiliary')
        self._auxiliary = True
        return True  # This exact scratch was included in the standing debit.

    def _release_auxiliary(self, size):
        if not self._auxiliary or size != AUXILIARY:
            raise ServiceLinkError('service_auxiliary')
        self._auxiliary = False

    @_guard
    def credit(self, maximum, *, emergency=False):
        self._credits = [c for c in self._credits if not c.snapshot()['closed']]
        if len(self._credits) >= 2:
            raise ServiceLinkError('service_credit_slots')
        domain = self.resources.local if emergency else self.domain
        self._mutating = True
        credit = WireCredit(self.resources, domain, maximum)
        self._credits.append(credit)
        return credit

    def _credit(self, credit):
        if not any(credit is held for held in self._credits):
            raise ServiceLinkError('service_foreign_credit')

    @_guard
    def prestart(self, directory, argv, cwd, env):
        if self._started or self._rx is None:
            raise ServiceLinkError('service_prestart')
        # arm_receive(READY) is required before the service can emit that record.
        self._mutating = self._started = True
        self._listener = BrokerListener()
        self._listener.start(directory, self._deadline,
                             self._reserve_auxiliary, self._release_auxiliary)
        self.owner.start(argv, cwd, env)
        self.owner.close_stdin()  # Fixed service uses only the record channel.

    @_guard
    def accept(self):
        if not self._started or self._connection is not None:
            raise ServiceLinkError('service_accept')
        self._mutating = True
        self._connection = self._listener.accept(self.owner)
        return self._connection is not None

    @_guard
    def queue(self, kind, operation, credit, *fields, data=None):
        if self._tx is not None:
            raise ServiceLinkError('service_send_pending')
        self._credit(credit)
        size = wire.STRUCTS[kind].size if data is None else len(data)
        if not 0 <= size <= 512:
            raise ServiceLinkError('service_body')
        self._mutating = True
        promise = credit.promise(32 + size)
        body = self._body.hold(size)
        self._tx = {'credit': credit, 'promise': promise, 'body': body, 'token': None}
        if data is None:
            wire.pack_into(kind, body.view, *fields)
        else:
            body.view[:] = data
        self._tx['token'] = self._sender.encode(kind, operation, body)

    @_guard
    def send_step(self):
        if self._tx is None or self._connection is None:
            raise ServiceLinkError('service_send_unavailable')
        self._mutating = True
        pending = self._tx
        token = pending['token']
        def send(frame):
            with frame.record as record:
                return self._connection.send(record)
        before = self._connection.snapshot()['last_transfer']
        self._io = (pending, 'send', before, pending['credit'].snapshot()['known'])
        try:
            count = token.visit(send)
        except BaseException:
            self._socket_prefix(pending, 'send', before)
            raise
        if count == -1:
            self._io = None
            return False  # The identical token/sequence/promise remains held.
        pending['credit'].known(pending['promise'], count)
        self._io = None
        token.visit(self.transcript.observe)
        token.complete(token.generation, 'known')
        pending['body'].close()
        self._tx = None
        return True

    @_guard
    def arm_receive(self, credit, maximum):
        if self._rx is not None or type(maximum) is not int or not 32 <= maximum <= 544:
            raise ServiceLinkError('service_receive_pending')
        self._credit(credit)
        self._mutating = True
        promise = credit.promise(maximum)
        self._rx = {'credit': credit, 'promise': promise,
                    'hold': self._rx_region.hold(maximum)}

    @_guard
    def adopt_receive_burst(self, credit, burst):
        """Adopt a future-reply cursor committed BEFORE its P2S trigger.

        The cursor may have been reserved while a different credit's text frame
        occupied RX. Adoption reuses the existing backing, not new traffic.
        """
        if self._rx is not None:
            raise ServiceLinkError('service_receive_pending')
        self._credit(credit)
        state = credit.burst_info(burst)
        if state['index'] >= len(state['maxima']):
            raise ServiceLinkError('service_burst_complete')
        self._mutating = True
        self._rx = {'credit': credit, 'burst': burst, 'hold': None}

    @_guard
    def finish_receive_burst(self):
        """Call only after validated source state excludes further auto-replies."""
        if self._rx is None or 'burst' not in self._rx or self._rx['hold'] is not None:
            raise ServiceLinkError('service_burst_pending')
        self._mutating = True
        self._rx['credit'].end_burst(self._rx['burst'])
        self._rx = None

    @_guard
    def receive_step(self, consumer):
        """consumer borrows one authenticated frame; it MUST retain no views."""
        if self._rx is None or self._connection is None:
            raise ServiceLinkError('service_receive_unavailable')
        self._mutating = True
        pending = self._rx
        if pending['hold'] is None:
            state = pending['credit'].burst_info(pending['burst'])
            if state['index'] >= len(state['maxima']):
                raise ServiceLinkError('service_burst_complete')
            pending['hold'] = self._rx_region.hold(state['maxima'][state['index']])
        before = self._connection.snapshot()['last_transfer']
        self._io = (pending, 'receive', before, pending['credit'].snapshot()['known'])
        try:
            count = self._connection.receive_into(pending['hold'].view)
        except BaseException:
            self._socket_prefix(pending, 'receive', before)
            raise
        if count == -1:
            self._io = None
            return False
        # Charge the actual edge even if framing/protocol/consumer later refuses.
        if 'burst' in pending:
            pending['credit'].burst_known(pending['burst'], count)
        else:
            pending['credit'].known(pending['promise'], count)
        self._io = None
        pending['hold'].close()
        pending['hold'] = self._rx_region.hold(count)
        token = self._receiver.decode(pending['hold'])
        token.visit(self.transcript.observe)
        token.visit(consumer)
        token.complete(token.generation, 'known')
        if 'burst' in pending:
            pending['hold'] = None
        else:
            self._rx = None
        return True

    def _socket_prefix(self, pending, direction, before):
        receipt = self._connection.snapshot()['last_transfer']
        if (receipt is not None and (before is None or receipt[0] > before[0])
                and receipt[1] == direction and type(receipt[3]) is int):
            if 'burst' in pending:
                pending['credit'].burst_unknown_prefix(pending['burst'], receipt[3])
            else:
                pending['credit'].unknown_prefix(pending['promise'], receipt[3])

    def close_channels(self):
        # Actual owner/wait/descendant cleanup continues independently, including
        # after failure. This does not return any memory/traffic commitment.
        self._closed = True
        if self._tx is not None or self._rx is not None:
            self._poison()
        if self._listener is not None:
            self._listener.close()

    def interests(self):
        """Borrowed readiness FDs, never ownership or permission to replay."""
        self._check()
        if self._connection is None:
            return () if self._listener is None else ((self._listener.fileno(), select.POLLIN),)
        events = (select.POLLOUT if self._tx is not None else 0)
        events |= select.POLLIN if self._rx is not None else 0
        return () if not events else ((self._connection.fileno(), events),)

    def retire(self):
        """Retire known local backings only after actual service owner release."""
        if self._retired:
            raise ServiceLinkError('service_retired')
        if (not self._closed or self._unknown or self._auxiliary or
                self._tx is not None or self._rx is not None or
                any(not c.snapshot()['closed'] for c in self._credits)):
            raise ServiceLinkError('service_retirement_pending')
        state = self.owner.snapshot()
        if state['release'] is None or state['release'].get('released') is not True:
            raise ServiceLinkError('service_retirement_pending')
        if self._listener is not None:
            listener = self._listener.snapshot()
            if (not listener['closed'] or listener['has_handles'] or listener['cleanup'] or
                    (listener['connection'] is not None and
                     not listener['connection']['closed_known'])):
                raise ServiceLinkError('service_retirement_pending')
        try:
            for region in (self._body, self._tx_region, self._rx_region):
                region.retire()
            self.resources.release(self._allocation)
            self._allocation = None
            self.resources.retire(self.domain)
            self._retired = True
        except BaseException:
            self._poison()
            raise

    def snapshot(self):
        return {'started': self._started, 'connected': self._connection is not None,
                'closed': self._closed, 'unknown': self._unknown,
                'retired': self._retired,
                'primary': self._primary,
                'io_recovery_error': self._io_recovery_error,
                'send_pending': self._tx is not None, 'receive_pending': self._rx is not None,
                'backing_bytes': 0 if self._root is None else self._root.used,
                'service_wait': self.owner.snapshot()['wait'],
                'last_socket_transfer': None if self._connection is None else
                    self._connection.snapshot()['last_transfer'],
                'transcript': self.transcript.snapshot(),
                'credits': tuple(c.snapshot() for c in self._credits),
                'qualification': 'unqualified'}
