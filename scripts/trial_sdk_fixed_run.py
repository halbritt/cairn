"""Source-owned run custody. No supported native launch/admission is supplied.

Construct this owner before prepare, retain it through any exception, and end
upstream preparation/config/event aliases in the builder's returning frame.
Methods with OS edges remain subject to separate runtime selection. Tests use
inert edges; neither a launched PID nor a boolean supplies native authority.
"""
import signal
import time
from pathlib import Path

from trial_native_bootstrap import SDKReviewPreparation, PreparationRefusal
from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_session_resources import SDKSessionResources
from trial_sdk_session_driver import SDKSessionDriver, TerminalDocument
from trial_sdk_fixed_bootstrap import FixedServiceLaunch
from trial_sdk_fixed_outer import FixedCallbackDriver
from trial_sdk_command_service_broker import ServiceLink
from trial_sdk_command_operation import HookOperationForward, unsupported_native
from trial_sdk_memory_gate import MemoryDispatchGate
from trial_sdk_shared_coordinator import SharedCoordinator, AggregateQuote, require
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_resource_failure import freeze_resource, annotate_resource_failure
from trial_sdk_stream_retention import SessionStreamRetention


class FixedRunOwner:
    def __init__(self, resources, *, clock=time.monotonic_ns):
        require(type(resources) is AttemptResources and callable(clock), 'run_resources')
        self.resources, self._clock = resources, clock
        self._state = 'new'
        self._busy = self._failed = self._cancelled = False
        self._faults = set()
        self._preparation = self._session_binding = None
        self._session = self._link = self._service = self._coordinator = None
        self._callback = self._callback_owner = self._callback_domain = None
        self._terminal_graph = self._terminal_metadata = None
        self._evidence = None
        self._paired = self._paired_join = self._publication = None
        self._cleanup_started = self._sources_closed = False
        self._owners = []  # Actual original owners, never caller-reported PID receipts.
        self._source_attempts = set()
        self._stop_sent = {}
        self._deadline = self._last_clock = None
        self._service_start_attempted = False
        self._ready_credit = None
        self._connect_cursor = 0

    def _now(self):
        now=self._clock()
        require(type(now) is int and now >= 0 and
                (self._last_clock is None or now >= self._last_clock), 'run_clock')
        self._last_clock=now
        return now

    def _fail(self, primary):
        self._failed = True
        if self._coordinator is not None:
            try:self._coordinator._fail(primary)
            except BaseException:self._faults.add('run_coordinator_poison_unknown')
        self._faults.update(freeze_resource(self.resources))
        annotate_resource_failure(primary, tuple(sorted(self._faults)))

    def prepare(self, preparation, binding, service_document, source_output,
                callback_root, callback_quote, command_quote, *, stream_output):
        require(self._state == 'new' and not self._busy, 'run_consumed')
        try:
            self._state = 'preparing'
            require(type(preparation) is SDKReviewPreparation
                and type(binding) is SDKSessionResources
                and binding.resources is self.resources
                and type(callback_quote) is AggregateQuote, 'run_binding')
            self._preparation, self._session_binding = preparation, binding
            self._owners.append(binding.owner)
            self._deadline = service_document['deadline_ns']
            require(type(self._deadline) is int and self._now() < self._deadline,
                    'run_deadline')
            require(type(callback_root) is str and Path(callback_root).is_absolute(),
                    'run_callback_root')
            self._callback_root = callback_root
            self._callback_quote, self._command_quote = callback_quote, command_quote
            self._construct_session()
            self._preparation = None
            self._evidence = SessionStreamRetention.__new__(SessionStreamRetention)
            SessionStreamRetention.__init__(self._evidence, binding, stream_output)
            self._session._pump._retention = self._evidence
            # Retain partially constructed owners before constructor effects.
            self._link = ServiceLink.__new__(ServiceLink)
            ServiceLink.__init__(self._link, self.resources, deadline_ns=self._deadline,
                standing_raw=service_document['standing_raw'],
                standing_working=service_document['standing_working'], clock=self._clock)
            self._owners.append(self._link.owner)
            self._service = FixedServiceLaunch.__new__(FixedServiceLaunch)
            self._service.prepare_owned(self._session,self._link,service_document,source_output)
            gate = MemoryDispatchGate(unsupported_native, HookOperationForward(self._link), self._deadline)
            self._coordinator = SharedCoordinator(self._session,gate,self._link,clock=self._clock)
            self._state = 'session'
        except BaseException as primary:
            self._fail(primary)
            raise

    def _construct_session(self):
        self._session = SDKSessionDriver.__new__(SDKSessionDriver)
        SDKSessionDriver.__init__(self._session,self._preparation,_resources=self._session_binding)

    def publish_evidence(self, output):
        """Private metadata after actual cleanup, never a result-only grade."""
        from trial_sdk_paired_evidence import EvidencePublication
        require(self._publication is None and not self._busy, 'run_publication_consumed')
        try:
            self._publication = EvidencePublication.__new__(EvidencePublication)
            EvidencePublication.__init__(self._publication, self)
            self._publication.publish(output)
        except BaseException as primary:
            self._fail(primary)
            raise

    def require_complete(self):
        # No invented native contract, namespace or successful launch fallback.
        raise PreparationRefusal('hook_interface_unavailable')

    def start_service(self):
        """Actual service edge for a separately selected runtime; no SDK launch."""
        require(self._state == 'session' and not self._busy and not self._failed and not self._cancelled
                and not self._service_start_attempted,
                'run_service_consumed')
        try:
            self._service_start_attempted = True
            self._state='service_connecting'
            self._ready_credit=self._link.credit(32)
            self._link.arm_receive(self._ready_credit,32)
            self._service.start_service(str(Path(self._service._runtime.path).parent))
        except BaseException as primary:
            self._fail(primary)
            raise

    def _service_step(self):
        if self._link._connection is None:
            self._link.accept()
        elif self._link.receive_step(lambda frame:None):
            self._ready_credit.finish()
            self._ready_credit=None
            self._state='session'
        return self._state

    def _connect_callback(self):
        c=self._coordinator
        c._live()
        lane=self._connect_cursor
        self._connect_cursor=(lane+1)%3
        if lane==0:
            if c.rpc.accept():self._state='callback'
        elif lane==1:
            c.producer.step(0)
        else:
            event=self._session.step(0)
            require(event.kind in ('idle','progress','stderr_eof'), 'run_connect_session_event')
        return self._state

    def _bind_callback(self):
        c = self._coordinator
        self._callback_owner = NativeProcessOwner()
        self._owners.append(self._callback_owner)  # Cleanup before bind/reserve effects.
        self._callback_domain = self.resources.bind(self._callback_owner)
        c.reserve_callback(c._event,self._callback_owner,self._callback_domain,self._callback_quote)
        directory = str(Path(self._callback_root)/c._event.observation['request_sha256'])
        c.rpc.prestart(directory)
        c.bind_fixed_producer(self._service)
        self._callback = FixedCallbackDriver(c,self._service,self._command_quote)
        self._service.start_producer(c.producer)
        self._state = 'callback_connecting'

    def _session_turn(self):
        turn = self._coordinator.step()
        if turn.lane != 'session':
            return
        event = turn.result
        if event.kind == 'callback':
            self._state = 'binding'  # Return event-bearing frame BEFORE binding.
        elif event.kind == 'document':
            require(self._evidence is not None and self._session._pump._retention is self._evidence
                    and self._evidence.state == 'open', 'run_document_retention_missing')
            # The complete original bytes are already private/persisted. Ending
            # this parsed alias is not a grade or a reservation release.
            event.document.take()
        elif event.kind == 'terminal':
            require(self._terminal_graph is None and type(event.document) is TerminalDocument,
                    'run_terminal_duplicate')
            self._terminal_metadata = event.document.metadata()
            self._terminal_graph = event.document.take()
            self._state = 'terminal'
        else:
            require(event.kind in ('idle','progress','control','stdout_eof','stderr_eof','wait'),
                    'run_unsupported_document')
            require(event.kind != 'wait', 'run_wait_without_terminal')

    def _drain_terminal(self):
        # Retain the terminal graph while bounded session I/O reaches actual EOF
        # and direct-child wait. No second result/callback is silently consumed.
        event=self._session.step(0)
        require(event.kind in ('idle','progress','stdout_eof','stderr_eof','wait'),
                'run_post_terminal_event')
        state=self._session.native_owner.snapshot()
        if state['wait'] is not None and state['stdout_eof'] and state['stderr_eof']:
            receipt=self._session.native_owner.release()
            require(receipt['released'] and receipt['handles_detached'] and not receipt['cleanup'],
                    'run_terminal_cleanup_unknown')
            require(self._evidence is not None, 'run_evidence_missing')
            self._evidence.finish(self._session._pump)
            require(self._now() < self._deadline, 'run_deadline')
            self._evidence._terminal_graph = self._terminal_graph
            self._terminal_graph = None
            self._state='terminal_waited'
        return self._state

    def _advance(self):
        if self._state == 'service_connecting':
            return self._service_step()
        if self._state == 'callback_connecting':
            return self._connect_callback()
        if self._state == 'terminal':
            return self._drain_terminal()
        if self._state == 'binding':
            self._bind_callback()
            return 'callback_bound'
        if self._state == 'callback':
            result = self._callback.step()
            if result == 'retired':
                self._callback = None
                self._callback_owner = self._callback_domain = None
                self._state = 'session'
            return result
        require(self._state == 'session', 'run_progress_unavailable')
        self._session_turn()
        return self._state  # Only closed progress tags, never event/result objects.

    def step(self):
        require(not self._busy and not self._failed and not self._cancelled,
                'run_unavailable')
        try:
            try:
                self._busy = True
                require(self._deadline is not None and self._now() < self._deadline,
                        'run_deadline')
                return self._advance()
            finally:self._busy = False
        except BaseException as primary:
            self._fail(primary)
            raise

    def cancel(self):
        require(not self._busy and not self._cancelled, 'run_cancel_consumed')
        try:
            self._cancelled = True  # Suppress all future output before protocol effects.
            if self._coordinator is not None:self._coordinator.cancel()
        except BaseException as primary:
            self._fail(primary)
            raise

    def _original_owners(self):
        # A constructor may have retained an owner before publishing its slot.
        owners=list(self._owners)
        if self._session_binding is not None and self._session_binding.owner not in owners:
            owners.append(self._session_binding.owner)
        if self._link is not None and hasattr(self._link,'owner'):
            if self._link.owner not in owners:owners.append(self._link.owner)
        return owners

    def cleanup_step(self):
        """One poll/stop attempt per original child, no blocking or guessed wait.

        Existing work deadline controls TERM then KILL, without a new grace or
        deadline reset. Late/unobserved waits remain pending, never completion.
        Source closure occurs only after actual waits and detached handles.
        No standing allocation or retained terminal/error graph is refunded.
        """
        require(not self._busy, 'run_busy')
        try:
            try:
                self._busy = True
                return self._cleanup()
            finally:self._busy = False
        except BaseException as primary:
            self._faults.add('run_cleanup_interrupted')
            self._fail(primary)
            raise

    def _cleanup(self):
        self._cleanup_started = True
        self._cancelled = True
        pending = False
        primary_error = None
        for owner in self._original_owners():
            try:
                state=owner.snapshot()
                if state['launch_attempted'] and state['wait'] is None:
                    owner.observe_wait()
                    state=owner.snapshot()
                    if state['wait'] is None:
                        level=2 if self._deadline is not None and self._now() >= self._deadline else 1
                        previous=self._stop_sent.get(owner,0)
                        if level > previous:
                            self._stop_sent[owner]=level  # Consume before signal uncertainty.
                            owner.signal(level,int(signal.SIGKILL if level==2 else signal.SIGTERM))
                        pending=True
                        continue
                receipt=(owner.cleanup_after_wait() if state['wait'] is not None
                         else owner.cleanup_unlaunched())
                actual=owner.snapshot()
                require(actual['release']==receipt and receipt['handles_detached']
                        and not actual['has_handles'], 'run_cleanup_pending')
                if not receipt['released'] or receipt['cleanup']:
                    self._faults.add('run_child_cleanup_unknown')
            except BaseException as primary:
                pending=True
                self._faults.add('run_child_cleanup_unknown')
                self._fail(primary)
                if primary_error is None:primary_error=primary
        if primary_error is not None:raise primary_error
        if pending:return 'cleanup_pending'
        if not self._sources_closed:
            # Independent source owners: failure closing one must not skip another.
            actions=[]
            if self._evidence is not None:
                actions.append(('evidence', self._evidence.close))
            if self._service is not None and hasattr(self._service,'_snapshot') and self._service._snapshot is not None:
                actions.append(('fixed',self._service.close))
            if self._session is not None and getattr(self._session,'_custody',None) is not None:
                actions.append(('session',self._session.close_sources))
            if self._preparation is not None:
                actions.append(('preparation',self._preparation.close))
            if self._link is not None and hasattr(self._link,'_listener'):
                actions.append(('link',self._link.close_channels))
            if self._coordinator is not None and self._coordinator.rpc is not None:
                listener=self._coordinator.rpc._listener
                if listener is not None:actions.append(('listener',listener.close))
            for name,action in actions:
                if name in self._source_attempts:continue
                code='run_'+name+'_close_unknown'
                self._faults.add(code)
                self._source_attempts.add(name)
                try:
                    faults=action()
                    if name in ('fixed','session','preparation') and not faults:
                        if name=='fixed':snapshot=self._service._snapshot
                        elif name=='session':snapshot=self._session._custody._snapshot
                        else:
                            recovery=self._preparation._move_recovery
                            snapshot=(recovery._snapshot if recovery is not None
                                      else self._preparation._snapshot)
                        if snapshot is not None:
                            require(snapshot.closed and not snapshot.cleanup_faults
                                    and all(lease.fd is None for lease in snapshot.tree.leases),
                                    'run_source_receipt_unknown')
                    if name in ('link','listener'):
                        held=self._link._listener if name=='link' else listener
                        if held is not None:
                            receipt=held.snapshot()
                            require(receipt['closed'] and not receipt['has_handles'] and not receipt['cleanup']
                                    and (receipt['connection'] is None or receipt['connection']['closed_known']),
                                    'run_channel_cleanup_unknown')
                    if not faults:self._faults.discard(code)
                except BaseException as primary:
                    self._faults.add('run_source_cleanup_unknown')
                    self._fail(primary)
                    if primary_error is None:primary_error=primary
            self._sources_closed = True
        if primary_error is not None:raise primary_error
        self._state='closed_unknown' if self._faults or self._failed else 'closed'
        return self._state

    def snapshot(self):
        return dict(state=self._state,failed=self._failed,cancelled=self._cancelled,
                    terminal_observed=self._terminal_metadata is not None,
                    terminal_graph_held=self._terminal_graph is not None,
                    cleanup_faults=tuple(sorted(self._faults)),
                    evidence=None if self._evidence is None else self._evidence.snapshot(),
                    qualification='source_run_custody_only_no_native_or_task_acceptance')
