"""Original producer DATA commitment with a protected future RPC receive.

Source-held identities partition accounting; they authenticate no remote peer.
The trusted serialized builder owns actual effects, observations and lifetimes.
"""
from dataclasses import dataclass, replace
from functools import wraps

from trial_sdk_attempt_resources import AttemptResources, MAX_ORDINAL, TRAFFIC


class StreamCreditError(ValueError):
    pass


class StreamCreditDenied(StreamCreditError):
    """Known original-bank capacity denial, with no new commitment/effect."""


@dataclass(frozen=True, eq=False)
class _Promise:
    maximum: int
    kind: str


class _Claim:
    __slots__ = ()


@dataclass(frozen=True)
class _Future:
    token: _Claim
    transaction: object
    peer: object
    generation: int
    maximum: int
    trigger: _Promise
    trigger_known: bool = False


@dataclass(frozen=True)
class _State:
    known: int = 0
    promises: tuple = ()
    future: object = None
    generation: int = 0


def _guard(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        acquired = False
        try:
            try:
                self._check()
                if self._busy:
                    raise StreamCreditError('stream_credit_busy')
                self._busy, acquired = True, True
                self._mutating = self._bank_call = False
                answer = method(self, *args, **kwargs)
            finally:
                if acquired:
                    self._busy = False
            return answer
        except BaseException as error:
            if acquired and (self._mutating or self._bank_call):
                if self._primary is None:
                    self._primary = type(error).__name__
                self._poison()
            raise
    return guarded


class StreamCredit:
    """One DATA slot for all producer stdio and RPCs, until real lifetime end.

    At most two active promises plus one protected, non-loaned future receive.
    The exact same bank pays every pre-effect increment. finish's caller must
    know ALL relevant producer edges/loans ended; RPC end alone is insufficient.
    """
    def __init__(self, resources, domain, initial_maximum):
        if type(resources) is not AttemptResources:
            raise StreamCreditError('stream_original_resources')
        self.resources = resources
        self.ticket = None
        self._state = _State()
        self._closed = self._unknown = self._busy = False
        self._mutating = self._bank_call = False
        self._receipt = self._primary = self._quarantine_error = None
        self._final_maximum = initial_maximum
        self._settling = False
        try:
            self._bank_call = True
            self.ticket = resources.stream_grant(domain, initial_maximum)
            if self.ticket is None:
                self._bank_call = False
                raise StreamCreditDenied('stream_capacity_denied')
            self._mutating = True
            self._bank_call = False
            answer = None
            return answer
        except BaseException:
            if self._bank_call or self._mutating:
                resources.freeze()
            raise

    @property
    def maximum(self):
        if self._closed or self._settling:
            return self._final_maximum
        return self.resources.stream_snapshot(self.ticket)['maximum']

    def _check(self):
        if self._unknown:
            raise StreamCreditError('stream_credit_unknown')
        if self._closed:
            raise StreamCreditError('stream_credit_closed')
        self.resources.stream_info(self.ticket)

    def _poison(self):
        self._unknown = True
        try:
            # An observed count staged before the single state replacement
            # survives interruption without counting an applied receipt twice.
            if self._receipt is not None:
                before, _, count = self._receipt
                if self._state is before:
                    self._state = replace(before, known=before.known + count)
            if not self._closed:
                self.resources.quarantine_stream(self.ticket, self._state.known)
        except BaseException as error:
            self._quarantine_error = type(error).__name__
        finally:
            self.resources.freeze()

    def _grow(self, maximum):
        current = self.maximum
        if type(maximum) is not int or not current <= maximum <= TRAFFIC:
            raise StreamCreditError('stream_growth')
        if maximum == current:
            return True
        # This marker covers a bank mutation whose successful return has not
        # reached our own state yet. A known False alone has no mutation to undo.
        self._bank_call = True
        accepted = self.resources.grow_stream(self.ticket, maximum)
        if accepted:
            self._mutating = True
        self._bank_call = False
        return accepted

    @_guard
    def grow(self, maximum):
        return self._grow(maximum)

    def _ensure(self, required):
        if required > TRAFFIC:
            raise StreamCreditDenied('stream_capacity_denied')
        if required > self.maximum and not self._grow(required):
            raise StreamCreditDenied('stream_capacity_denied')

    def _pledged(self):
        return sum(p.maximum for p in self._state.promises)

    def _future_reserved(self):
        return 0 if self._state.future is None else self._state.future.maximum

    def _promise(self, maximum, kind):
        upper, lower = (512, 1) if kind == 'stdio' else (544, 32)
        if type(maximum) is not int or not lower <= maximum <= upper:
            raise StreamCreditError('stream_promise')
        if len(self._state.promises) >= 2:
            raise StreamCreditError('stream_promise_slots')
        self._ensure(self._state.known + self._pledged() + self._future_reserved() + maximum)
        self._mutating = True
        promise = _Promise(maximum, kind)
        self._state = replace(self._state, promises=self._state.promises + (promise,))
        return promise

    @_guard
    def promise(self, maximum):
        return self._promise(maximum, 'wire')

    @_guard
    def promise_stdio(self, maximum):
        return self._promise(maximum, 'stdio')

    @_guard
    def protect_future_rx(self, transaction, peer, generation, maximum, trigger):
        state = self._state
        if (transaction is None or peer is None or type(generation) is not int
                or not state.generation < generation <= MAX_ORDINAL):
            raise StreamCreditError('stream_future_binding')
        if state.future is not None:
            raise StreamCreditError('stream_future_pending')
        if (type(trigger) is not _Promise or trigger not in state.promises
                or trigger.kind != 'wire'):
            raise StreamCreditError('stream_future_trigger')
        if type(maximum) is not int or not 32 <= maximum <= 544:
            raise StreamCreditError('stream_future_size')
        self._ensure(state.known + self._pledged() + maximum)
        self._mutating = True
        token = _Claim()
        future = _Future(token, transaction, peer, generation, maximum, trigger)
        self._state = replace(state, future=future, generation=generation)
        return token

    @_guard
    def promote_future_rx(self, claim, transaction, peer, generation):
        state = self._state
        row = state.future
        if (type(claim) is not _Claim or row is None or row.token is not claim
                or row.transaction is not transaction or row.peer is not peer
                or type(generation) is not int or row.generation != generation):
            raise StreamCreditError('stream_future_binding')
        if not row.trigger_known:
            raise StreamCreditError('stream_future_trigger_pending')
        if len(state.promises) >= 2:
            raise StreamCreditError('stream_promise_slots')
        self._mutating = True
        promise = _Promise(row.maximum, 'wire')
        # No observable healthy gap or bank return: one state replacement
        # exchanges the protected claim for exactly one active receive pledge.
        self._state = replace(state, promises=state.promises + (promise,), future=None)
        return promise

    def _validate_count(self, promise, count):
        if type(promise) is not _Promise or promise not in self._state.promises:
            raise StreamCreditError('stream_promise')
        if type(count) is not int or not 0 <= count <= promise.maximum:
            raise StreamCreditError('stream_count')

    @_guard
    def known(self, promise, count):
        self._validate_count(promise, count)
        self._mutating = True
        state = self._state
        self._receipt = (state, promise, count)
        future = state.future
        if future is not None and future.trigger is promise:
            future = replace(future, trigger_known=count == promise.maximum)
        self._state = replace(state, known=state.known + count,
                              promises=tuple(p for p in state.promises if p is not promise),
                              future=future)
        self._receipt = None

    @_guard
    def unknown_prefix(self, promise, count):
        self._validate_count(promise, count)
        self._mutating = True
        state = self._state
        self._receipt = (state, promise, count)
        # The loan stays unresolved; observed bytes do not refund its remainder.
        self._state = replace(state, known=state.known + count)
        self._poison()

    def quarantine(self):
        # Also freeze after a consumed settlement / uncertain outer handoff.
        self.resources.freeze()
        self._poison()

    @_guard
    def finish(self):
        if self._state.promises or self._state.future is not None:
            raise StreamCreditError('stream_receipt_pending')
        self._mutating = True
        self._final_maximum = self.maximum
        self._settling = True
        self.resources.settle_stream(self.ticket, self._state.known)
        self._closed = True

    def snapshot(self):
        state = self._state
        return {'maximum': self.maximum, 'known': state.known,
                'promised': self._pledged(), 'pending': len(state.promises),
                'future_reserved': self._future_reserved(), 'generation': state.generation,
                'closed': self._closed,
                'unknown': self._unknown or self.resources.snapshot()['unknown'],
                'primary': self._primary, 'quarantine_error': self._quarantine_error,
                'receipt_in_progress': None if self._receipt is None else
                    (self._receipt[1].maximum, self._receipt[2])}
