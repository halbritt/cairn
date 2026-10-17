"""One bounded local write action; no launcher, stdin close or admission.

The session owner lends its controller and direct-child owner exclusively. Calls
are serialized and neither object may be advanced by another writer. These
Python objects do not authenticate a process or establish an admitted runtime.
"""
from trial_sdk_control import SDKControl, SDKControlError
from trial_sdk_native_process_owner import NativeProcessOwner


class SDKDirectWriterError(ValueError):
    pass


class SDKDirectWriter:
    def __init__(self, control, native):
        if type(control) is not SDKControl or type(native) is not NativeProcessOwner:
            raise SDKDirectWriterError('sdk_direct_writer_owners')
        control.pristine_binding()
        initial = native.snapshot()
        if initial['launch_attempted'] or initial['pid'] is not None or initial['release'] is not None:
            raise SDKDirectWriterError('sdk_direct_writer_order')
        self._control, self._native = control, native
        self._written = self._blocked = 0
        self._busy = False
        self._failure = None
        self._controller_failure_unknown = False

    def _check(self):
        native, ledger = self._native.snapshot(), self._control.ledger()
        input_state = native['native_input']
        if (not native['launched'] or native['primary'] is not None
                or native['wait'] is not None or native['stdin_closed']
                or input_state is None or input_state['closed_known']
                or input_state['close_unknown'] or input_state['write_unknown']):
            raise SDKDirectWriterError('sdk_direct_writer_native_unavailable')
        if (ledger['failure'] is not None or ledger['closed']
                or ledger['written_input_bytes'] != self._written
                or input_state['known_written'] != self._written):
            raise SDKDirectWriterError('sdk_direct_writer_progress_drift')

    def _failed(self):
        if self._failure is None:
            self._failure = 'sdk_direct_writer_unknown'
        try:
            self._control.write_failed()
        except SDKControlError:
            pass  # Expected sticky controller refusal, never a retry permit.
        except BaseException:
            self._controller_failure_unknown = True

    def advance(self):
        """Return one actual positive write count, or zero for idle/would-block.

        Reacquire the controller's current suffix on every call; report progress
        immediately after this owner's unbuffered syscall. Queue exhaustion never
        closes native stdin. Any ambiguous effect poisons this adapter and the
        controller while leaving process stop/wait/cleanup to the session owner.
        """
        if self._busy:
            raise SDKDirectWriterError('sdk_direct_writer_busy')
        if self._failure is not None:
            raise SDKDirectWriterError('sdk_direct_writer_unusable')
        self._busy = True
        pending = None
        try:
            self._check()
            pending = self._control.pending_write()
            if pending is None:
                return 0
            with memoryview(pending) as whole, whole[:512] as offered:
                try:
                    count = self._native.write(offered)
                except BlockingIOError:
                    self._check()  # Known no progress cannot hide other drift.
                    self._blocked += 1
                    return 0
                if type(count) is not int or not 0 < count <= len(offered):
                    raise SDKDirectWriterError('sdk_direct_writer_write_unknown')
                # Keep the actual positive return even if later bookkeeping
                # refuses. It must never become permission to replay that prefix.
                self._written += count
                input_state = self._native.snapshot()['native_input']
                if input_state['known_written'] != self._written:
                    raise SDKDirectWriterError('sdk_direct_writer_progress_drift')
                self._control.written(count)
            self._check()
            return count
        except BaseException:
            self._failed()
            raise
        finally:
            pending = None
            self._busy = False

    def snapshot(self):
        native, ledger = self._native.snapshot(), self._control.ledger()
        input_state = native['native_input']
        return dict(schema='cairn.sdk-direct-writer/1',
            failure=self._failure, controller_failure_unknown=self._controller_failure_unknown,
            positive_write_bytes=self._written, controller_written_bytes=ledger['written_input_bytes'],
            native_known_written_bytes=None if input_state is None else input_state['known_written'],
            native_write_outcome_unknown=None if input_state is None else input_state['write_unknown'],
            would_block_observations=self._blocked, stdin_closed=native['stdin_closed'],
            qualification='local_mechanism_only_not_runtime_admission')
