"""Direct session composition; no launcher, provider or runtime admission.

Terminal input policy is separate from task grading and external exit evidence.
Python token identity is a trusted serialized ownership convention, never an
authentication boundary against callers inside this process.
"""
import re

from trial_bootstrap_snapshot import record_cleanup
from trial_native_bootstrap import SDKReviewPreparation, SDKSessionCustody, PreparationRefusal
from trial_sdk_control import SDKControlError
from trial_sdk_incoming import OwnedJSON
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_stream_driver import SDKStreamDriver, StreamEvent
from trial_sdk_session_resources import SDKSessionResources, resource_boundary
from trial_sdk_resource_failure import poison_resource


class SessionDriverError(ValueError):
    pass


class TerminalDocument:
    """One parsed result graph handed on without a second raw encoding/copy."""
    def __init__(self, graph, metadata):
        self._graph, self._metadata = graph, metadata
        self._taken = False

    @classmethod
    def consume(cls, document, session_id):
        """Recognize an input-terminal result, not a validated work product.

        Only top-level type/session and closed scalar terminal fields are used.
        The full original graph still requires its separate evidence/task path.
        Supplied OwnedJSON provenance is not independently authenticated here.
        """
        if type(document) is not OwnedJSON:
            raise SessionDriverError('sdk_terminal_owned_required')
        metadata = document.metadata()
        graph = document.take()
        subtype, error = graph.get('subtype'), graph.get('is_error')
        valid = (type(session_id) is str and graph.get('type') == 'result'
            and graph.get('session_id') == session_id
            and type(subtype) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,39}', subtype) is not None
            and type(error) is bool and (subtype == 'success') is (error is False))
        if not valid:
            graph = None
            raise SessionDriverError('sdk_terminal_shape')
        selected = dict(schema='cairn.sdk-terminal-input/1', session_id=session_id,
            subtype=subtype, is_error=error, raw_bytes=metadata['raw_bytes'],
            raw_sha256=metadata['raw_sha256'], first_byte_ns=metadata['first_byte_ns'],
            qualification='input_termination_only_not_task_acceptance')
        return cls(graph, selected)

    def metadata(self):
        return dict(self._metadata)

    def take(self):
        if self._taken:
            raise SessionDriverError('sdk_terminal_consumed')
        self._taken = True
        graph, self._graph = self._graph, None
        return graph


class SDKSessionDriver:
    """Consume original review once; own its controller and one native owner.

    No process is started here. The separately authorized fixed builder may use
    native_owner and launch, but cannot introduce another writer. All producer,
    capture, wall/resource, endpoint and evidence authorities stay external.
    """
    @classmethod
    def from_resources(cls, preparation, binding):
        if type(binding) is not SDKSessionResources:
            raise SessionDriverError('sdk_session_resource_binding')
        return cls(preparation, _resources=binding)

    def __init__(self, preparation, *, _resources=None):
        if type(preparation) is not SDKReviewPreparation:
            raise SessionDriverError('sdk_session_review_required')
        if preparation._transferred or preparation._move_attempted:
            raise PreparationRefusal('sdk_review_transferred')
        if preparation._allocation is not None and _resources is None:
            raise SessionDriverError('sdk_session_resources_required')
        if _resources is not None:
            if (type(_resources) is not SDKSessionResources or preparation._allocation is None
                    or preparation._allocation.resources is not _resources.resources):
                raise SessionDriverError('sdk_session_resource_binding')
            preparation._allocation.check()
        self._resources = _resources
        self._custody = self._native = self._pump = None
        self._terminal_observed = None
        self._first_step_checked = self._input_close_attempted = False
        self._sources_close_attempted = self._busy = False
        self._failure = None
        self._cleanup = ()
        try:
            if _resources is not None:
                _resources.claim()
                self._native = _resources.owner
            self._custody = SDKSessionCustody.empty()
            SDKSessionCustody.__init__(self._custody, preparation)
            if _resources is None:
                self._native = NativeProcessOwner()
            self._pump = SDKStreamDriver(self._custody.control, self._native, _resources=_resources)
        except BaseException as primary:
            faults = list(poison_resource(_resources, primary))
            if self._native is not None:
                try:
                    release = self._native.cleanup_unlaunched()
                    faults.extend(release['cleanup'])
                except BaseException:
                    faults.append('native_unlaunched_cleanup_unknown')
            try:
                faults.extend(preparation.close())
            except BaseException:
                faults.append('source_cleanup_unknown')
            if self._custody is not None:
                try:
                    faults.extend(self._custody.close())
                except BaseException:
                    faults.append('source_cleanup_unknown')
            if faults:
                # Existing failed-assignment facts belong to the inner owner.
                # Inspect optional annotations only after all new owned cleanup.
                try:
                    inherited = getattr(primary, 'cleanup_faults', ())
                    valid = (type(inherited) is tuple and len(inherited) <= 6
                        and all(type(code) is str and code in {
                            'descriptor_cleanup_unknown', 'sdk_control_cleanup_unknown',
                            'cleanup_annotation_unknown', 'source_cleanup_unknown',
                            'native_unlaunched_cleanup_unknown', 'resource_freeze_unknown',
                            'resource_poison_unknown'} for code in inherited))
                except BaseException:
                    valid = False
                if not valid:
                    inherited = ('cleanup_annotation_unknown',)
                record_cleanup(primary, tuple(sorted(set(inherited + tuple(faults)))))
            # No new faults: preserve the inner exception's metadata untouched.
            raise

    def _live(self):
        if self._resources is not None:
            self._custody._allocation.check()
            self._resources.room()
        if self._busy or self._failure is not None or self._sources_close_attempted:
            raise SessionDriverError('sdk_session_unavailable')

    @property
    def launch(self):
        self._live()
        return self._custody.launch

    @property
    def native_owner(self):
        # Available for actual wait/signal/cleanup even after session failure.
        return self._native

    @resource_boundary
    def source_bindings(self):
        self._live()
        self._busy = True
        try:
            return self._custody.held_source_bindings()
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    @resource_boundary
    def verify_sources(self):
        self._live()
        self._busy = True
        try:
            self._custody.verify_held_sources()
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    def require_complete(self):
        self.verify_sources()
        if self._custody.control._memory is None:
            raise PreparationRefusal('hook_interface_unavailable')
        if self._resources is None or self._pump._memory_deadline is None:
            raise PreparationRefusal('sdk_native_resources_unbound')

    @resource_boundary
    def memory_reply(self, request_id, result):
        self._live()
        self._busy = True
        try:
            if self._terminal_observed is not None:
                raise SessionDriverError('sdk_session_input_ended')
            self._pump.memory_reply(request_id, result)
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    def _poison(self):
        self._failure = self._failure or 'sdk_session_operation_failed'
        faults = set(self._cleanup)
        try:
            self._pump.cancel()
        except BaseException:
            faults.add('stream_cleanup_unknown')
        try:
            self._custody.control.write_failed()
        except SDKControlError:
            pass  # Expected sticky controller refusal; never permission to replay.
        except BaseException:
            faults.add('control_failure_unknown')
        self._cleanup = tuple(sorted(faults))

    @resource_boundary
    def reply(self, request_id, context):
        self._live()
        self._busy = True
        try:
            if self._terminal_observed is not None:
                raise SessionDriverError('sdk_session_input_ended')
            self._pump.reply(request_id, context)
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    @resource_boundary
    def step(self, timeout=.05):
        self._live()
        self._busy = True
        terminal = None
        try:
            if not self._first_step_checked:
                self._custody.verify_held_sources()
                self._first_step_checked = True
            event = self._pump.step(timeout)
            if event.kind != 'document' or event.document.kind != 'result':
                return event
            terminal = TerminalDocument.consume(event.document,
                self._custody.control.bindings['session_id'])
            if self._terminal_observed is not None:
                raise SessionDriverError('sdk_terminal_duplicate')
            self._terminal_observed = terminal.metadata()
            stream = self._pump.snapshot()
            writer = stream['writer']
            written = writer['positive_write_bytes']
            if (writer['failure'] is not None or writer['controller_failure_unknown']
                    or writer['native_write_outcome_unknown'] is not False
                    or written != writer['controller_written_bytes']
                    or written != writer['native_known_written_bytes']):
                raise SessionDriverError('sdk_terminal_input_progress_unknown')
            last = stream['last_positive_write_ns']
            if last is None or terminal.metadata()['first_byte_ns'] < last:
                raise SessionDriverError('sdk_terminal_before_input_drained')
            # Recheck the held original sources before making this input close.
            self._custody.verify_held_sources()
            self._custody.control.seal_input()
            self._input_close_attempted = True
            self._native.close_stdin()
            return StreamEvent('terminal', document=terminal, observation=terminal.metadata(),
                               line_receipt=event.line_receipt)
        except BaseException:
            terminal = None
            self._poison()
            raise
        finally:
            self._busy = False

    def close_sources(self):
        """Release original files after no launch or an actual detached child.

        The external owner must first stop/wait/clean its native child. This
        method never fabricates that wait, kills a process, or retries an
        uncertain close. Outer capture exit and evidence closure remain distinct.
        """
        if self._busy:
            raise SessionDriverError('sdk_session_busy')
        if self._sources_close_attempted:
            return self._cleanup
        native = self._native.snapshot()
        if native['pid'] is None:
            if native['launch_attempted']:
                raise SessionDriverError('sdk_session_launch_outcome_unknown')
            self._native.cleanup_unlaunched()
        elif (native['wait'] is None or native['release'] is None
                or native['release']['handles_detached'] is not True):
            raise SessionDriverError('sdk_session_native_cleanup_pending')
        self._sources_close_attempted = True
        faults = set(self._cleanup)
        try:
            self._pump.cancel()
        except BaseException:
            faults.add('stream_cleanup_unknown')
        # A stream-loan fault cannot skip the independently owned source FDs.
        try:
            faults.update(self._custody.close())
        except BaseException:
            faults.add('source_cleanup_unknown')
            self._cleanup = tuple(sorted(faults))
            raise
        self._cleanup = tuple(sorted(faults))
        return self._cleanup

    def snapshot(self):
        return dict(schema='cairn.sdk-session-driver/1', failure=self._failure,
            terminal_observed=None if self._terminal_observed is None else dict(self._terminal_observed),
            input_close_attempted=self._input_close_attempted,
            sources_close_attempted=self._sources_close_attempted, cleanup=self._cleanup,
            stream=self._pump.snapshot(), native=self._native.snapshot(),
            resource_binding=None if self._resources is None else self._resources.snapshot(),
            qualification='source_custody_and_input_termination_only_not_runtime_admission')
