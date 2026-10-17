"""Bounded direct-child stream pump; no launcher, producer or runtime admission.

The trusted session owner exclusively lends its original SDKControl and fresh
NativeProcessOwner before launching through a separately authorized fixed
builder. It retains source custody, work/cleanup deadlines and actual process
cleanup. Calls are serialized. Returned payloads transfer to that owner; they
must not be accumulated without separately bounded storage.
"""
from dataclasses import dataclass, field
import hashlib
import math
import select
import time

from trial_sdk_control import SDKControlError
from trial_sdk_direct_writer import SDKDirectWriter
from trial_sdk_incoming import LineAssembler, MAX_DOCUMENT_BYTES, validate_line


class StreamDriverError(ValueError):
    pass


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    callback: object = field(default=None, repr=False)
    document: object = field(default=None, repr=False)
    observation: object = None


class SDKStreamDriver:
    def __init__(self, control, native):
        # Also checks exact types, pristine control and an unlaunched owner.
        self._writer = SDKDirectWriter(control, native)
        self._control, self._native = control, native
        # The suspended stdout iterator owns its slab. Stderr cannot reuse it.
        self._stdout = bytearray(512)
        self._stderr = bytearray(512)
        self._assembler = LineAssembler(MAX_DOCUMENT_BYTES)
        self._lines = self._active = None
        self._bytes = {'stdout': 0, 'stderr': 0}
        self._hash = {name: hashlib.sha256() for name in self._bytes}
        self._clock = self._failure = None
        self._cleanup_unknown = self._busy = self._cancelled = False
        self._wait_reported = False
        self._completed = 0
        self._last_write_ns = None

    def _now(self):
        now = time.monotonic_ns()
        if (type(now) is not int or now < 0
                or (self._clock is not None and now < self._clock)):
            raise StreamDriverError('sdk_stream_clock_unknown')
        self._clock = now
        if self._active is not None and now >= self._active['deadline_ns']:
            self._control.callback_timeout(self._active['request_id'])
        return now

    def _usable(self):
        if self._busy or self._failure is not None or self._cancelled:
            raise StreamDriverError('sdk_stream_unusable')

    def _drop_lines(self):
        lines, self._lines = self._lines, None
        if lines is not None:
            try:
                lines.close()
            except BaseException:
                self._cleanup_unknown = True
                raise

    def _poison(self):
        self._failure = self._failure or 'sdk_stream_operation_failed'
        try:
            self._drop_lines()
        except BaseException:
            pass  # The generator close uncertainty is sticky; preserve primary.
        try:
            self._control.write_failed()
        except SDKControlError:
            pass  # Expected terminal refusal, never permission to retry.
        except BaseException:
            self._cleanup_unknown = True

    def cancel(self):
        """Drop the outstanding read loan; no signal, wait or stdin close."""
        if self._busy:
            raise StreamDriverError('sdk_stream_busy')
        if not self._cancelled:
            self._cancelled = True
            self._drop_lines()

    def _line(self):
        if self._active is not None or self._lines is None:
            return None
        try:
            line = next(self._lines)
        except StopIteration:
            self._lines = None
            return None
        document = validate_line(line)
        first = document.first_byte_ns
        line = None
        if document.kind is not None and document.kind.startswith('control_'):
            callback = self._control.receive_owned(document)
            if callback is None:
                return StreamEvent('control')
            self._active = dict(request_id=callback.request_id,
                request_sha256=hashlib.sha256(callback.request_id.encode()).hexdigest(),
                row=len(self._control.ledger()['rows']) - 1,
                first_byte_ns=first, deadline_ns=first + 5_000_000_000)
            if self._native.snapshot()['wait'] is not None:
                raise StreamDriverError('sdk_stream_callback_after_wait')
            self._now()  # Slow assembly/parsing cannot restart this deadline.
            return StreamEvent('callback', callback=callback,
                observation={k: v for k, v in self._active.items() if k not in ('request_id', 'row')})
        return StreamEvent('document', document=document)

    def reply(self, request_id, context):
        """Enqueue the actual producer's context; this does not assert its wait.

        The external owner must deliver full Callback.iter_input_json to an
        independently owned producer, observe its bounded I/O/wait and account
        for it. This method accepts no producer success or deadline assertion.
        The active slot stays held through the reply's final actual write.
        """
        self._usable()
        self._busy = True
        try:
            self._now()
            if self._active is None or request_id != self._active['request_id']:
                raise StreamDriverError('sdk_stream_callback_binding')
            self._control.reply(request_id, context)
            self._now()
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    def _completed_reply(self):
        if self._active is None:
            return None
        row = self._control.ledger()['rows'][self._active['row']]
        if row['request_sha256'] != self._active['request_sha256']:
            raise StreamDriverError('sdk_stream_callback_drift')
        if not row['reply_written']:
            return None
        now = self._now()  # Includes the final syscall and its bookkeeping.
        observation = {k: v for k, v in self._active.items() if k not in ('request_id', 'row')}
        observation['completed_ns'] = now
        self._active = None
        self._completed += 1
        return StreamEvent('reply_written', observation=observation)

    def _read(self, name):
        slab = self._stdout if name == 'stdout' else self._stderr
        started = self._now()
        with memoryview(slab) as view:
            count = self._native.read_into(name, view)
        # Retain every positive syscall return before clocks/parsing can fail.
        if count > 0:
            self._bytes[name] += count
            with memoryview(slab)[:count] as body:
                self._hash[name].update(body)
        self._now()
        if count < 0:
            return None
        if count == 0:
            if name == 'stdout':
                self._assembler.finish()
            return StreamEvent(name + '_eof')
        with memoryview(slab)[:count] as body:
            if (self._bytes['stdout'] > 3 * 1024 * 1024
                    or sum(self._bytes.values()) > 4 * 1024 * 1024):
                raise StreamDriverError('sdk_stream_bytes_bound')
            if name == 'stdout':
                # Conservative observed read-interval start, not kernel arrival.
                self._lines = self._assembler.feed(body, started)
        return self._line() if name == 'stdout' else StreamEvent('progress')

    def _wait_event(self):
        wait = self._native.observe_wait()
        if wait is not None:
            if self._active is not None or self._control.write_pending:
                raise StreamDriverError('sdk_stream_wait_with_pending_input')
            if not self._wait_reported:
                self._wait_reported = True
                return StreamEvent('wait', observation=wait)
        return None

    def step(self, timeout=.05):
        """At most one stdout event, one stderr read and one local write.

        Poll <=timeout<=50ms, shortened to the callback deadline; zero is valid.
        No sleep/reset
        at callback handoff or reply enqueue. A suspended stdout batch is pinned
        until exhausted; stdout pauses while one callback is active. This is a
        restricted mechanism whose native compatibility is still unproved.
        """
        self._usable()
        self._busy = True
        try:
            now = self._now()
            if (type(timeout) not in (int, float) or not math.isfinite(timeout)
                    or not 0 <= timeout <= .05):
                raise StreamDriverError('sdk_stream_poll_bounds')
            state = self._native.snapshot()
            if not state['launched']:
                raise StreamDriverError('sdk_stream_native_unavailable')
            event = self._wait_event() or self._completed_reply() or self._line()
            if event is not None:
                self._now()
                return event
            wait = self._native.snapshot()['wait']
            reads = {}
            if not state['stderr_eof']:
                reads[self._native.fileno('stderr')] = 'stderr'
            if not state['stdout_eof'] and self._active is None and self._lines is None:
                reads[self._native.fileno('stdout')] = 'stdout'
            if wait is None:
                reads[self._native.fileno('pidfd')] = 'wait'
            writes = []
            if wait is None and self._control.write_pending:
                writes = [self._native.fileno('stdin')]
            if self._active is not None:
                timeout = min(timeout, max(0, (self._active['deadline_ns'] - now) / 1e9))
            ready, writable, _ = select.select(list(reads), writes, [], timeout)
            self._now()
            if any(reads[fd] == 'wait' for fd in ready):
                event = self._wait_event()
                if event is not None:
                    return event
            # Distinct slab keeps stderr serviceable while stdout is suspended.
            for fd in ready:
                if reads[fd] == 'stderr':
                    self._read('stderr')
            # Finish this bounded batch before another stdout read. A ready
            # input write gets service even under continuous ordinary output.
            if writable:
                count = self._writer.advance()
                completed = self._now()
                if count:
                    self._last_write_ns = completed
                event = self._completed_reply()
                if event is not None:
                    return event
            for fd in ready:
                if reads[fd] == 'stdout':
                    event = self._read('stdout')
                    if event is not None:
                        self._now()
                        return event
            if writable:
                return StreamEvent('progress')
            return StreamEvent('idle')
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    def close_input_after_wait(self):
        """Only an actual wait permits this cleanup close; not a result policy.

        A native implementation waiting for EOF after result needs a separately
        reviewed terminal protocol. The outer work deadline must stop that
        unresolved session. Queue-empty, a result document and EOF are no wait.
        """
        self._usable()
        self._busy = True
        try:
            self._now()
            if self._native.observe_wait() is None:
                raise StreamDriverError('sdk_stream_wait_pending')
            self._native.close_stdin()
        except BaseException:
            self._poison()
            raise
        finally:
            self._busy = False

    def snapshot(self):
        return dict(schema='cairn.sdk-stream-driver/1', failure=self._failure,
            cleanup_unknown=self._cleanup_unknown, cancelled=self._cancelled,
            active=None if self._active is None else {
                k: v for k, v in self._active.items() if k not in ('request_id', 'row')},
            completed_replies=self._completed, stdout_batch_held=self._lines is not None,
            last_positive_write_ns=self._last_write_ns,
            pending_line_bytes=self._assembler.pending_bytes,
            stdout_bytes=self._bytes['stdout'], stderr_bytes=self._bytes['stderr'],
            stdout_sha256=self._hash['stdout'].hexdigest(), stderr_sha256=self._hash['stderr'].hexdigest(),
            writer=self._writer.snapshot(), qualification='unqualified_local_stream_mechanism')
