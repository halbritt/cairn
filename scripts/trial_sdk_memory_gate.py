"""Serialized attempt-owned memory debit and dispatch; no process or admission.

The trusted builder binds both fixed forwards and original deadlines. These local
objects are not peer authentication or evidence of exclusive downstream custody.
Separate producers need a reviewed broker; do not construct a gate per callback.
"""
import dataclasses
import math
import time

from trial_sdk_incoming import MAX_DOCUMENT_BYTES, canonical_size_sha256


class MemoryGateError(ValueError):
    """Fixed labels only; never include request, response or exception text."""


@dataclasses.dataclass(frozen=True)
class HookRequest:
    operation: str
    args: tuple
    body: str | None


@dataclasses.dataclass(frozen=True)
class NativeRequest:
    tool: str
    arguments: dict


class MemoryDispatchGate:
    """One shared budget for native calls and every callback in one attempt.

    native_deadline_ns is the original capture-origin + 30 seconds, supplied by
    its actual owner. Hook channels receive their original first-read + 5 seconds.
    Fixed forwards accept (request, absolute_deadline_ns), do not retain requests,
    and perform at most one downstream dispatch. A return is not API success.
    Any raised/interrupted forward is ambiguous: keep the debit and poison this
    gate. Known service refusals should return their original result envelope.
    """
    def __init__(self, native_forward, hook_forward, native_deadline_ns):
        if (not callable(native_forward) or not callable(hook_forward)
                or type(native_deadline_ns) is not int or native_deadline_ns <= 0):
            raise MemoryGateError('memory_gate_binding')
        self._native_forward, self._hook_forward = native_forward, hook_forward
        self._native_deadline = native_deadline_ns
        self._clock = None
        self._busy = False
        self._failure = None
        self._requests = self._dispatches = self._returned = self._refusals = 0
        self._searches = self._expansions = self._hook_searches = 0
        self._unknown = self._accounting_unknown = False
        self._last = None

    def _refuse(self, code):
        self._refusals += 1
        if self._failure is None:
            self._failure = code
        raise MemoryGateError(self._failure)

    def _now(self, deadline):
        try:
            now = time.monotonic_ns()
        except BaseException:
            self._accounting_unknown = True
            if self._failure is None: self._failure = 'memory_clock_unknown'
            raise
        if (type(now) is not int or now < 0
                or self._clock is not None and now < self._clock):
            self._refuse('memory_clock_unknown')
        self._clock = now
        if now >= deadline:
            self._refuse('memory_deadline')
        return now

    def hook(self, deadline_ns):
        if type(deadline_ns) is not int or deadline_ns <= 0:
            self._refuse('memory_hook_binding')
        return _HookChannel(self, deadline_ns)

    def native(self, tool, arguments):
        return self._dispatch('native', NativeRequest(tool, arguments), self._native_deadline)

    def _classify(self, origin, request):
        if origin == 'native':
            operations = {'mcp__cairn__cairn_search': 'search', 'mcp__cairn__cairn_pull': 'pull'}
            if type(request.tool) is not str or request.tool not in operations:
                self._refuse('memory_native_operation')
            if type(request.arguments) is not dict:
                self._refuse('memory_request_shape')
            return operations[request.tool], request.arguments, self._native_forward
        operation = request.operation
        if type(operation) is not str or operation not in ('search', 'pull', 'pull-evidence', 'history'):
            self._refuse('memory_hook_operation')
        if (type(request.args) is not tuple or any(type(arg) is not str for arg in request.args)
                or request.body is not None and type(request.body) is not str):
            self._refuse('memory_request_shape')
        document = {'operation': operation, 'args': list(request.args), 'body': request.body}
        return operation, document, self._hook_forward

    def _bound(self, document):
        try:
            length, _ = canonical_size_sha256(document)
            if length > MAX_DOCUMENT_BYTES:
                self._refuse('memory_request_bound')
        except MemoryGateError:
            raise
        except (ValueError, UnicodeError, RecursionError):
            self._refuse('memory_request_shape')

    def _debit(self, origin, operation):
        if origin == 'native' and operation == 'search':
            if self._searches == 2:
                self._refuse('memory_native_search_limit')
            self._searches += 1
        elif operation != 'search':
            if self._expansions == 4:
                self._refuse('memory_expansion_limit')
            self._expansions += 1
        else:
            self._hook_searches += 1

    def _dispatch(self, origin, request, deadline):
        owns_transaction = False
        try:
            self._requests += 1
            if self._failure is not None:
                self._refuse(self._failure)
            if self._busy:
                self._refuse('memory_dispatch_reentrant')
            owns_transaction = True
            self._busy = True
            self._now(deadline)
            operation, document, forward = self._classify(origin, request)
            self._bound(document)
            self._now(deadline)  # Validation cannot carry dispatch past expiry.
            self._debit(origin, operation)
            return self._forward(origin, operation, request, deadline, forward)
        except BaseException:
            if self._failure is None:
                self._accounting_unknown = True
                self._failure = 'memory_gate_unknown'
            raise
        finally:
            if owns_transaction:
                self._busy = False

    def _forward(self, origin, operation, request, deadline, forward):
        self._dispatches += 1
        self._last = {'sequence': self._dispatches, 'origin': origin,
                      'operation': operation, 'outcome': 'unknown'}
        try:
            result = forward(request, deadline)
            self._returned += 1
            self._last['outcome'] = 'returned'
            if self._failure is not None:
                raise MemoryGateError(self._failure)
            self._now(deadline)
            return result
        except BaseException:
            if self._last['outcome'] == 'unknown':
                self._unknown = True
            if self._failure is None:
                self._accounting_unknown = True
                self._failure = ('memory_dispatch_unknown' if self._unknown
                                 else 'memory_gate_unknown')
            raise

    def snapshot(self):
        return dict(schema='cairn.sdk-memory-dispatch/1', requests=self._requests,
            dispatches=self._dispatches, returned=self._returned, refusals=self._refusals,
            native_searches_debited=self._searches, expansions_debited=self._expansions,
            hook_searches=self._hook_searches, dispatch_unknown=self._unknown,
            accounting_unknown=self._accounting_unknown,
            failure=self._failure, in_flight=self._busy,
            last=None if self._last is None else dict(self._last))


class _HookChannel:
    def __init__(self, gate, deadline):
        self._gate, self._deadline = gate, deadline
        self._closed = False

    def call(self, operation, args=(), body=None, timeout=5):
        if self._closed:
            self._gate._refuse('memory_hook_closed')
        if (type(timeout) not in (int, float) or not 0 < timeout <= 5
                or not math.isfinite(timeout)):
            self._gate._refuse('memory_hook_timeout')
        # A requested shorter RPC timeout can tighten, never reset, the callback.
        deadline = min(self._deadline, self._gate._now(self._deadline) + int(timeout * 1_000_000_000))
        return self._gate._dispatch('hook', HookRequest(operation, args, body), deadline)

    def close(self):
        self._closed = True
