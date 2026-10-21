"""Fixed callback completion owner; no launch or complete physical-fit claim.

The fixed builder lends the bound coordinator exclusively after its binding
frame returns. No external event/output/pending/result aliases may survive that
handoff. Python identity checks do not authenticate that source obligation.
This owner returns only fixed progress strings, never callback payloads.
"""
from trial_sdk_shared_coordinator import SharedCoordinator, require
from trial_sdk_fixed_bootstrap import FixedServiceLaunch
from trial_sdk_session_driver import SDKSessionDriver
from trial_sdk_engine_adapter import engine_context
from trial_sdk_command_operation import OperationQuote
from trial_sdk_resource_failure import annotate_resource_failure, freeze_resource
from trial_sdk_read_custody import RETAINED_WORKING


class FixedCallbackDriver:
    def __init__(self, coordinator, service, command_quote):
        require(type(coordinator) is SharedCoordinator
                and type(coordinator.session) is SDKSessionDriver
                and type(service) is FixedServiceLaunch
                and type(command_quote) is OperationQuote, 'outer_binding')
        coordinator._live()
        require(coordinator.rpc is not None and coordinator.producer is not None
                and coordinator._event is not None and coordinator._output is None
                and not coordinator._reply_queued and not coordinator._cancelled,
                'outer_binding')
        require(coordinator.rpc._read_custody is not None
                and coordinator.rpc._read_custody._service_custody is service
                and coordinator.producer in service._producers
                and service.policy_for(coordinator.rpc) is coordinator.rpc._read_custody._policy,
                'outer_service_binding')
        self._coordinator = coordinator
        self._rpc = coordinator.rpc  # Survives coordinator detach for original retirement.
        self._quote = command_quote
        self._state = 'running'
        self._selected_input = None
        self._busy = self._failed = False
        self._faults = set()
        # Only a pristine RPC can be lent exclusively. A late wrapper remains
        # generic: prior public pending/result borrows cannot be revoked.
        rpc = self._rpc
        require(rpc._command_owner is None, 'outer_command_owner')
        if rpc._phase == 'idle' and rpc.transcript.operation == 0 and not rpc._closed:
            require(rpc._operation is None
                    and rpc._request is None and rpc._read_custody._last_operation is None,
                    'outer_command_owner')
            rpc._command_owner = self

    def _release_command(self, phases):
        """Healthy serialized return; all parent result/parse frames have ended."""
        rpc, c = self._rpc, self._coordinator
        require(rpc._command_owner is self and phases is rpc._phases
                and not self._failed and self._state == 'running'
                and c.rpc is rpc and c.operation is None
                and rpc._phase == 'phase_return' and rpc._cancel_reason is None,
                'outer_command_return')
        phases._settled()
        operation = rpc._operation
        require(operation is not None and operation._closed and not operation._unknown
                and not operation._retired and phases.state.phase == 'command'
                and rpc._read_custody._last_operation is operation,
                'outer_command_return')
        rpc._read_custody._live()  # Exact original service/configuration custody.
        # Publish persistent metadata backing BEFORE the parent ticket is freed.
        # This is a one-time charge, not a reduced command quote or a refund of
        # generic borrowed results. Exceptions retain both tickets conservatively.
        phases._transition(phases.state.row[2],
            max(phases.state.row[3], phases.initial_working + RETAINED_WORKING), 'return')
        rpc._retire_operation()

    def _queue_reply(self):
        # This frame must RETURN before detach/retirement. On any exception its
        # graph/context traceback stays charged by the original frozen ticket.
        output = self._coordinator.take_output()
        graph = output.take()
        context = engine_context(graph, self._coordinator._event.callback.event)
        self._coordinator.reply(output, context)

    def _pump(self):
        # Consume the returned event locally. Session callback is already bound;
        # documents/terminal/new callbacks here are unsupported, never dropped.
        turn = self._coordinator.step()
        if turn.lane == 'session':
            require(turn.result.kind in ('idle', 'progress', 'stderr_eof', 'reply_written'),
                    'outer_unexpected_session_event')

    def _detach(self):
        self._coordinator.detach_callback(self._coordinator._event)
        # No event/output/producer local survives this returning handoff frame.

    def _advance(self):
        c = self._coordinator
        if self._state == 'running':
            if c.producer._ready and self._rpc._closed:
                self._queue_reply()
                self._state = 'writing'
                return 'reply_queued'
            if self._rpc._phase == 'handoff':
                if not c.prepare_next_command(self._quote, owner=self._rpc._command_owner):
                    self._pump()  # Incomplete producer stdin must still progress.
            else:
                self._pump()
            return 'progress'
        if self._state == 'writing':
            if not c._reply_written:
                self._pump()
                return 'progress'
            if self._selected_input is not None:
                self._selected_input.hook(self)
            self._detach()
            self._state = 'detached'
            return 'detached'
        require(self._state == 'detached', 'outer_consumed')
        # Both projection and detach frames have returned on earlier calls.
        require(c._event is None and c._output is None and c.producer is None
                and c.rpc is None and c.operation is None, 'outer_alias_pending')
        self._rpc.retire()
        self._state = 'retired'
        return 'retired'

    def step(self):
        require(not self._busy and not self._failed and self._state != 'retired',
                'outer_unavailable')
        try:
            try:
                self._busy = True
                self._coordinator._live()  # Original callback/session clocks, never reset.
                self._rpc.resources._check()
                self._rpc._now()  # Retain callback deadline even AFTER detach.
                return self._advance()
            finally:
                self._busy = False
        except BaseException as primary:
            self._failed = True
            # Coordinator detach can already have cleared rpc. Retain independent
            # original RPC poison authority; never infer cleanup from detach.
            for code, action in (
                ('outer_coordinator_cleanup_unknown', lambda:self._coordinator._fail(primary)),
                ('outer_rpc_cleanup_unknown', lambda:self._rpc._poison('outer_failed'))):
                try:
                    action()
                except BaseException:
                    self._faults.add(code)
            self._faults.update(freeze_resource(self._rpc.resources))
            annotate_resource_failure(primary, tuple(sorted(self._faults)))
            raise

    def snapshot(self):
        return dict(state=self._state, failed=self._failed,
                    cleanup_faults=tuple(sorted(self._faults)),
                    qualification='owned_callback_sequence_only_no_physical_or_native_admission')
