"""Source-only serialized composition; no launch, endpoint creation or admission.

The fixed builder exclusively lends the original session, gate and service link.
It supplies an aggregate allocation before either callback endpoint is built.
Quotes are accounting inputs, not evidence of a complete physical backing bound.
Returned events/output remain charged until their actual owners end all aliases.
"""
from dataclasses import dataclass
import time

from trial_sdk_command_operation import CommandOperation, HookOperationForward, unsupported_native
from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_engine_memory_rpc import ProducerRPC, RPCQuote
from trial_sdk_memory_gate import MemoryDispatchGate
from trial_sdk_producer_driver import SDKProducerDriver
from trial_sdk_producer_resources import ProducerResources
from trial_sdk_resource_failure import annotate_resource_failure, freeze_resource, poison_resource
from trial_sdk_session_driver import SDKSessionDriver
from trial_sdk_callback_phases import MINIMUM_RAW as CALLBACK_PHASE_RAW


class CoordinatorError(ValueError):
    pass


def require(value, code):
    if not value:
        raise CoordinatorError(code)


@dataclass(frozen=True)
class AggregateQuote:
    """Explicit simultaneous partitions of ONE producer-domain operation ticket.

    rpc is the aggregate RPCQuote passed to ProducerRPC, including the driver.
    The other fields partition that aggregate: the RPC/client lifetime versus
    the producer canonical input, decoder, graph and retained output lifetime.
    Minima below only reject known undercounts. Caller/source/callback/controller
    aliases and Python working sizes still require an independently derived quote.
    """
    rpc: RPCQuote
    rpc_raw: int
    rpc_working: int
    producer_raw: int
    producer_working: int

    def check(self):
        require(type(self.rpc) is RPCQuote, 'coordinator_quote')
        self.rpc.check()
        require(all(type(n) is int and n > 0 for n in (
            self.rpc_raw, self.rpc_working, self.producer_raw, self.producer_working)),
            'coordinator_quote')
        require(self.rpc_raw >= self.rpc.client.raw + 2080
                and self.rpc_working > self.rpc.client.working
                and self.producer_raw >= ProducerResources.MINIMUM_RAW
                and self.rpc.raw >= self.rpc_raw + self.producer_raw
                and self.rpc.working >= self.rpc_working + self.producer_working,
                'coordinator_aggregate_underquoted')
        require(self.rpc.raw >= CALLBACK_PHASE_RAW, 'coordinator_phase_underquoted')


@dataclass(frozen=True)
class Turn:
    lane: str
    result: object


class SharedCoordinator:
    """One bounded action per turn, rotating SDK -> producer -> RPC/service.

    RPC.step owns CommandOperation/ServiceLink progress; never step those twice.
    There is one callback slot and no event backlog. The caller must consume each
    returned document before requesting another. Polling/stop/wait authority and
    the original capture/cleanup deadlines remain with the fixed outer builder.
    Exclusive lending is a source contract, not authentication against Python
    callers retaining aliases. No native-forward fallback is installed.
    """
    def __init__(self, session, gate, link, *, clock=time.monotonic_ns):
        require(type(session) is SDKSessionDriver and session._resources is not None
                and type(gate) is MemoryDispatchGate and type(link) is ServiceLink
                and callable(clock), 'coordinator_binding')
        bank = session._resources.resources
        require(link.resources is bank and gate._native_forward is unsupported_native
                and type(gate._hook_forward) is HookOperationForward
                and gate._hook_forward.link is link, 'coordinator_binding')
        session._live()
        self.session, self.gate, self.link, self.resources = session, gate, link, bank
        self._clock, self._last_clock = clock, None
        self._deadline = gate._native_deadline
        self._cursor = 0
        self._busy = self._unknown = self._cancelled = False
        self._faults = set()
        self._event = self._quote = self._allocation = None
        self.producer = self.rpc = self.operation = self._output = None
        self._reply_queued = self._reply_written = False

    def _live(self):
        require(not self._busy and not self._unknown, 'coordinator_unavailable')
        self.resources._check()
        self.session._live()  # Includes original preparation standing liveness.
        if self._allocation is not None:
            self.resources._lookup(self.resources._memory, self._allocation)
        now = self._clock()
        require(type(now) is int and now >= 0 and
                (self._last_clock is None or now >= self._last_clock), 'coordinator_clock')
        self._last_clock = now
        deadline = self._deadline
        if self._event is not None:
            deadline = min(deadline, self._event.observation['deadline_ns'])
        require(now < deadline, 'coordinator_deadline')

    def _fail(self, primary):
        self._unknown = True
        # Each recovery is independent. No failed poison/freeze skips the next
        # original owner; no guessed wait, successful delivery or refund follows.
        actions = []
        if self.rpc is not None:
            actions.append(('coordinator_rpc_cleanup_unknown',
                            lambda: self.rpc._poison('coordinator_failed')))
        if self.operation is not None:
            actions.append(('coordinator_operation_cleanup_unknown',
                            lambda: self.operation._poison('coordinator_failed')))
        if self.producer is not None:
            actions.append(('coordinator_producer_cleanup_unknown', self.producer.cancel))
        actions.append(('coordinator_session_cleanup_unknown', self.session._poison))
        for code, action in actions:
            try:
                action()
            except BaseException:
                self._faults.add(code)
        for binding in (self.session._resources,
                        None if self.producer is None else self.producer._resources):
            try:
                self._faults.update(poison_resource(binding, primary))
            except BaseException:
                self._faults.add('resource_poison_unknown')
        self._faults.update(freeze_resource(self.resources))
        annotate_resource_failure(primary, tuple(sorted(self._faults)))

    def _call(self, action):
        # A rejected reentrant call must not poison resources during an I/O edge.
        require(not self._busy, 'coordinator_busy')
        original = None
        try:
            try:
                self._live()
                self._busy = True
                return action()
            except BaseException as primary:
                original = primary
                raise
            finally:
                self._busy = False
        except BaseException as primary:
            if original is not None and primary is not original:
                self._faults.add('coordinator_final_cleanup_unknown')
                self._fail(original)
                raise original from None
            self._fail(primary)
            raise

    def step(self):
        def advance():
            for _ in range(3):
                lane = self._cursor
                self._cursor = (lane + 1) % 3
                if lane == 0 and not self._reply_written:
                    event = self.session.step(0)
                    if event.kind == 'callback':
                        require(self._event is None, 'coordinator_callback_pending')
                        self._event = event
                    elif event.kind == 'reply_written':
                        require(self._reply_queued and self._event is not None
                                and event.observation['request_sha256'] ==
                                self._event.observation['request_sha256'],
                                'coordinator_reply_binding')
                        self._reply_written = True
                    return Turn('session', event)
                if lane == 1 and self.producer is not None and not self.producer._ready:
                    return Turn('producer', self.producer.step(0))
                if lane == 2 and self.rpc is not None and not self.rpc._closed:
                    return Turn('rpc', self.rpc.step())
            return Turn('parked', None)
        return self._call(advance)

    def _bind_callback(self, event, owner, domain, allocation, quote):
        require(not self._cancelled and event is self._event and event is not None
                and self.rpc is None and self.producer is None,
                'coordinator_callback_binding')
        require(type(quote) is AggregateQuote, 'coordinator_quote')
        quote.check()
        row = self.resources._lookup(self.resources._memory, allocation)
        require(row[0] is domain and row[1] == 'operation'
                and row[2] >= quote.rpc.raw and row[3] >= quote.rpc.working,
                'coordinator_held_aggregate')
        self._allocation, self._quote = allocation, quote
        self.rpc = ProducerRPC(self.resources, owner, domain, allocation,
            gate=self.gate, link=self.link, event=event, quote=quote.rpc, clock=self._clock)
        binding = ProducerResources(self.resources, owner, domain, allocation,
                                    self.rpc.stream_credit())
        self.producer = SDKProducerDriver.from_resources(event, binding)
        self.rpc.bind_producer(self.producer)

    def reserve_callback(self, event, owner, domain, quote):
        """Reserve the aggregate before constructing either callback port."""
        def reserve():
            require(not self._cancelled and event is self._event and event is not None
                    and self.rpc is None and self.producer is None,
                    'coordinator_callback_binding')
            require(type(quote) is AggregateQuote, 'coordinator_quote')
            quote.check()
            require(self.resources._domain(domain)['owner'] is owner,
                    'coordinator_owner_binding')
            self._allocation = self.resources.allocate(domain, 'operation',
                quote.rpc.raw, quote.rpc.working)
            require(self._allocation is not None, 'coordinator_aggregate_denied')
            self._bind_callback(event, owner, domain, self._allocation, quote)
        return self._call(reserve)

    def bind_callback(self, event, owner, domain, allocation, quote):
        """Borrow preallocated actual owner; construct both ports from exact event.

        The builder creates/connects endpoints and starts the original owner only
        under separate authority. Neither operation is performed by this module.
        """
        return self._call(lambda: self._bind_callback(event, owner, domain, allocation, quote))

    def pending_request(self):
        return self._call(lambda: None if self.rpc is None else self.rpc.pending_request())

    def _prepare_command(self, pending, quote):
        require(not self._cancelled and self.rpc is not None and pending is not None
                and pending is self.rpc.pending_request(), 'coordinator_request_binding')
        if self.rpc._read_custody is not None:
            self.rpc._read_custody.authorize_pending()
        if not self.rpc.prepare_command_phase():
            return False
        state = self.link.transcript.snapshot()
        self.operation = CommandOperation(self.link, quote,
            operation=state['operation'] + 1, lease=state['lease'] + 1,
            deadline_ns=pending.deadline_ns, clock=self._clock)
        self.operation.bind_gate(self.gate)
        request = pending.request
        self.operation.prepare_hook(request.operation, request.args, request.body)
        self.rpc.start_operation(self.operation)
        # RPC now exclusively owns this borrow. It may retire the command
        # in a later step; the coordinator must not retain that operation.
        self.operation = None
        return True

    def prepare_command(self, pending, quote):
        """Explicit authorized borrow; caller must end its own pending aliases."""
        return self._call(lambda: self._prepare_command(pending, quote))

    def bind_read_custody(self, policy):
        """Bind fixed parent policy before this callback has issued commands."""
        def bind():
            from trial_sdk_read_custody import ReadCustody
            require(self.rpc is not None and self.rpc._read_custody is None,
                    'coordinator_read_custody')
            self.rpc._read_custody = ReadCustody(self.rpc, policy)
        return self._call(bind)

    def prepare_next_command(self, quote):
        """Authorize and hand off internally; return only progress, not a borrow."""
        def handoff():
            require(not self._cancelled and self.rpc is not None
                    and self.rpc._read_custody is not None, 'coordinator_read_custody')
            pending = self.rpc.pending_request()
            if pending is None:
                return False
            return self._prepare_command(pending, quote)
        return self._call(handoff)

    def take_output(self):
        def take():
            require(not self._cancelled and self.producer is not None
                    and self.rpc._closed and self._output is None,
                    'coordinator_output_pending')
            self._output = self.producer.take_output()
            return self._output
        return self._call(take)

    def reply(self, output, context):
        """Caller supplies its authorized output projection; no default context."""
        def enqueue():
            require(not self._cancelled and output is not None and output is self._output
                    and not self._reply_queued, 'coordinator_output_binding')
            self.session.reply(self._event.callback.request_id, context)
            self._reply_queued = True
        return self._call(enqueue)

    def detach_callback(self, event):
        """End this borrow after actual reply write; never retire or refund aliases.

        The outer owner still holds the ports/output/allocation and must end or
        transfer every backing before rpc.retire. Next binding cannot reuse a
        still-occupied producer domain operation slot.
        """
        def detach():
            require(not self._cancelled and event is self._event and self._reply_written,
                    'coordinator_callback_pending')
            self._event = self._quote = self._allocation = None
            self.producer = self.rpc = self.operation = self._output = None
            self._reply_queued = self._reply_written = False
        return self._call(detach)

    def cancel(self, reason=4):
        """Request protocol cancellation; keep bounded draining until deadline.

        This suppresses output/reply, not proof of remote cancellation. Actual
        process stop/wait and service cleanup remain the original owner's duty.
        """
        def request():
            require(type(reason) is int and 1 <= reason <= 5, 'coordinator_cancel_reason')
            self._cancelled = True
            if self.rpc is not None:
                self.rpc.cancel(reason)
        return self._call(request)

    def close_sources(self):
        """Independent local cleanup after externally observed native cleanup.

        Callable after failure. No allocation release, endpoint close, signal or
        wait is inferred. Session custody itself checks actual wait/detachment.
        """
        # An interrupted final busy-clear can leave the flag set after the
        # outer guard quarantines us. Original source cleanup must remain usable.
        require(not self._busy or self._unknown, 'coordinator_busy')
        self._busy = True
        primary = None
        try:
            try:
                if self.producer is not None:
                    try:
                        self.producer.cancel()
                    except BaseException as error:
                        primary = error
                try:
                    faults = self.session.close_sources()
                    self._faults.update(faults)
                except BaseException as error:
                    if primary is None:
                        primary = error
                    else:
                        self._faults.add('coordinator_source_cleanup_unknown')
                if primary is not None:
                    raise primary
                self._unknown = True  # Cleanup never reopens this coordinator.
                return tuple(sorted(self._faults))
            finally:
                self._busy = False
        except BaseException as error:
            if primary is not None and error is not primary:
                self._faults.add('coordinator_final_cleanup_unknown')
                self._fail(primary)
                raise primary from None
            self._fail(error)
            raise

    def snapshot(self):
        return dict(unknown=self._unknown, cancelled=self._cancelled,
            callback_pending=self._event is not None, reply_queued=self._reply_queued,
            reply_written=self._reply_written, allocation_held=self._allocation is not None,
            cleanup_faults=tuple(sorted(self._faults)),
            qualification='source_composition_only_no_full_quote_or_native_admission')
