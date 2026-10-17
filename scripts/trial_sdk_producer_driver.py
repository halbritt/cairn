"""One owned callback producer's I/O; no launcher or runtime admission.

The fixed builder separately launches native_owner inside the original resource
domain. This object owns no alternate SDK protocol writer. All calls require
trusted serialized access; event/reference identity is not authentication.
"""
import hashlib
import math
import re
import select
import time

from trial_sdk_control import Callback, EVENTS
from trial_sdk_incoming import BodyDecoder, MAX_CHUNK_BYTES, MAX_DOCUMENT_BYTES, parse_document, canonical_chunks, canonical_size_sha256
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_stream_driver import StreamEvent
from trial_sdk_producer_resources import ProducerResources


class ProducerDriverError(ValueError):
    pass


class ProducerOutput:
    """One parsed stdout graph (or None for zero output), never task success."""
    def __init__(self, graph, observation):
        self._graph, self._observation = graph, observation
        self._taken = False

    def metadata(self):
        return dict(self._observation)

    def take(self):
        if self._taken:
            raise ProducerDriverError('sdk_producer_output_consumed')
        self._taken = True
        graph, self._graph = self._graph, None
        return graph


class SDKProducerDriver:
    @classmethod
    def from_resources(cls, event, binding):
        """Construct under an already-held original allocation and DATA stream."""
        if type(binding) is not ProducerResources:
            raise ProducerDriverError('sdk_producer_resource_binding')
        try:
            return cls(event, _resources=binding)
        except BaseException as error:
            binding.poison(error)
            raise

    def __init__(self, event, *, _resources=None):
        valid = (type(event) is StreamEvent and event.kind == 'callback'
                 and type(event.callback) is Callback and type(event.observation) is dict)
        if valid:
            callback, observed = event.callback, event.observation
            valid = (type(callback.request_id) is str and 0 < len(callback.request_id) <= 128
                and re.fullmatch(r'[A-Za-z0-9._:-]+', callback.request_id) is not None
                and callback.event in EVENTS and type(callback.input_bytes) is int
                and 0 < callback.input_bytes <= MAX_DOCUMENT_BYTES
                and type(callback.input_sha256) is str
                and re.fullmatch('[0-9a-f]{64}', callback.input_sha256) is not None
                and set(observed) == {'request_sha256', 'first_byte_ns', 'deadline_ns'}
                and observed['request_sha256'] == hashlib.sha256(callback.request_id.encode()).hexdigest()
                and type(observed['first_byte_ns']) is int and observed['first_byte_ns'] >= 0
                and type(observed['deadline_ns']) is int
                and observed['deadline_ns'] == observed['first_byte_ns'] + 5_000_000_000)
        if not valid:
            raise ProducerDriverError('sdk_producer_binding')
        if _resources is not None and type(_resources) is not ProducerResources:
            raise ProducerDriverError('sdk_producer_resource_binding')
        self._resources = _resources
        if _resources is not None:
            _resources.claim()
        self.request_id, self.event = callback.request_id, callback.event
        self._binding = dict(observed, input_bytes=callback.input_bytes,
                             input_sha256=callback.input_sha256, event=callback.event)
        self._callback = callback
        self._transport = dict(input_bytes=callback.input_bytes, input_sha256=callback.input_sha256)
        self._bootstrap = None
        self._bootstrap_attempted = False
        self._input = callback.iter_input_json()
        self._pending, self._offset = None, 0
        self._generated = self._written = 0
        self._generated_hash, self._written_hash = hashlib.sha256(), hashlib.sha256()
        self._generated_hash_unknown = False
        self._input_exhausted = self._input_close_attempted = False
        self._native = NativeProcessOwner() if _resources is None else _resources.owner
        self._io = self._native if _resources is None else _resources
        self._slabs = {name: bytearray(512) for name in ('stdout', 'stderr')}
        self._bytes = {name: 0 for name in self._slabs}
        self._hash = {name: hashlib.sha256() for name in self._slabs}
        self._hash_unknown = set()
        self._write_hash_unknown = False
        self._decoder = BodyDecoder(MAX_DOCUMENT_BYTES)
        self._output = None
        self._ready = self._taken = self._cancelled = self._busy = False
        self._failure = self._clock = None
        self._cleanup = set()

    def _bind_bootstrap(self, event, document):
        try:
            return self._install_bootstrap(event, document)
        except BaseException as primary:
            try:
                self._poison(primary)
            except BaseException:
                self._failure = 'sdk_producer_bootstrap_failed'
                self._cleanup.add('producer_bootstrap_poison_unknown')
            raise

    def _install_bootstrap(self, event, document):
        """Fixed parent only; preserve original input binding separately."""
        self._usable()
        if (self._resources is None or not self._resources._claimed
                or self._native.snapshot()['launch_attempted'] or self._bootstrap_attempted
                or self._generated or event.callback is not self._callback
                or document['deadline_ns'] != self._binding['deadline_ns']
                or document['input'] is not self._callback._input
                or document['input_bytes'] != self._binding['input_bytes']
                or document['input_sha256'] != self._binding['input_sha256']
                or document['event'] != self.event):
            raise ProducerDriverError('sdk_producer_bootstrap_binding')
        self._resources.resources._lookup(self._resources.resources._memory,
                                          self._resources.allocation)
        self._bootstrap_attempted = True
        if canonical_size_sha256(document['input']) != (self._binding['input_bytes'],
                                                       self._binding['input_sha256']):
            raise ProducerDriverError('sdk_producer_original_input_drift')
        size, digest = canonical_size_sha256(document)
        if size > MAX_DOCUMENT_BYTES:
            raise ProducerDriverError('sdk_producer_bootstrap_bound')
        self._drop_input()
        self._bootstrap = document
        self._transport = dict(input_bytes=size, input_sha256=digest)
        self._input = canonical_chunks(document)

    @property
    def native_owner(self):
        return self._native  # Actual stop/wait/cleanup remains available on failure.

    def _now(self):
        now = time.monotonic_ns()
        if (type(now) is not int or now < self._binding['first_byte_ns']
                or self._clock is not None and now < self._clock):
            raise ProducerDriverError('sdk_producer_clock_unknown')
        self._clock = now
        if now >= self._binding['deadline_ns']:
            raise ProducerDriverError('sdk_producer_deadline')
        return now

    def _usable(self):
        if self._busy or self._failure is not None or self._cancelled:
            raise ProducerDriverError('sdk_producer_unavailable')

    def _drop_input(self):
        source, self._input = self._input, None
        self._pending, self._offset = None, 0
        if source is not None:
            try:
                source.close()
            except BaseException:
                self._cleanup.add('producer_input_iterator_close_unknown')
                raise

    def _poison(self, primary):
        if self._resources is not None:
            self._resources.poison(primary)
        if self._failure is None:
            self._failure = (str(primary) if type(primary) is ProducerDriverError
                             else 'sdk_producer_operation_failed')
        try:
            self._drop_input()
        except BaseException:
            pass  # Sticky iterator cleanup uncertainty; preserve primary.
        self._decoder = self._output = None

    def cancel(self):
        """Release local input/decoded graphs, not signal/wait or close native FDs."""
        if self._busy:
            raise ProducerDriverError('sdk_producer_busy')
        if not self._cancelled:
            self._cancelled = True
            try:
                self._drop_input()
            finally:
                self._decoder = self._output = None

    def _prepare_input(self):
        if self._pending is not None or self._input_exhausted:
            return
        try:
            body = next(self._input)
        except StopIteration:
            self._input_exhausted = True
            self._drop_input()
            if (self._generated != self._transport['input_bytes']
                    or self._generated_hash.hexdigest() != self._transport['input_sha256']):
                raise ProducerDriverError('sdk_producer_input_drift')
            return
        if type(body) is not bytes or not 0 < len(body) <= MAX_CHUNK_BYTES:
            raise ProducerDriverError('sdk_producer_input_chunk')
        self._generated_hash_unknown = True
        self._generated += len(body)
        self._generated_hash.update(body)
        self._generated_hash_unknown = False
        if self._generated > self._transport['input_bytes']:
            raise ProducerDriverError('sdk_producer_input_drift')
        self._pending, self._offset = body, 0

    def _write(self):
        with memoryview(self._pending)[self._offset:self._offset + 512] as offered:
            try:
                count = self._io.write(offered)
            except BlockingIOError:
                return False
            if type(count) is not int or not 0 < count <= len(offered):
                raise ProducerDriverError('sdk_producer_write_progress')
            # Actual positive return is recorded before a clock can refuse.
            self._write_hash_unknown = True
            self._written += count
            self._offset += count
            self._written_hash.update(offered[:count])
            self._write_hash_unknown = False
        if self._offset == len(self._pending):
            self._pending, self._offset = None, 0
        self._now()
        return True

    def _close_input(self):
        if self._input_exhausted and not self._input_close_attempted:
            state = self._native.snapshot()['native_input']
            if (state is None or state['write_unknown'] or self._write_hash_unknown
                    or state['known_written'] != self._written
                    or self._written != self._transport['input_bytes']
                    or self._written_hash.hexdigest() != self._transport['input_sha256']):
                raise ProducerDriverError('sdk_producer_input_progress_unknown')
            self._input_close_attempted = True
            self._native.close_stdin()
            self._now()

    def _read(self, name):
        with memoryview(self._slabs[name]) as slab:
            count = self._io.read_into(name, slab)
            if count > 0:
                self._hash_unknown.add(name)
                self._bytes[name] += count
                self._hash[name].update(slab[:count])
                self._hash_unknown.remove(name)
            self._now()
            if count > 0:
                if sum(self._bytes.values()) > 4 * 1024 * 1024:
                    raise ProducerDriverError('sdk_producer_stream_bound')
                if name == 'stdout':
                    self._decoder.feed(slab[:count])
            return count >= 0

    def _finish(self, state):
        if state['wait'] is None or not state['stdout_eof'] or not state['stderr_eof']:
            return False
        if state['wait']['returncode'] != 0:
            raise ProducerDriverError('sdk_producer_nonzero')
        if not self._input_exhausted or not state['stdin_closed']:
            raise ProducerDriverError('sdk_producer_input_incomplete')
        release = self._native.release()
        if release['released'] is not True:
            raise ProducerDriverError('sdk_producer_release_unknown')
        text = self._decoder.finish()
        graph = parse_document(text, self._bytes['stdout']) if text else None
        self._decoder = None
        self._now()
        self._output = ProducerOutput(graph, dict(self._binding,
            stdout_bytes=self._bytes['stdout'], stdout_sha256=self._hash['stdout'].hexdigest(),
            stderr_bytes=self._bytes['stderr'], stderr_sha256=self._hash['stderr'].hexdigest(),
            qualification='producer_output_only_not_native_delivery_or_task_acceptance'))
        self._ready = True
        return True

    def step(self, timeout=.005):
        """At most one 512-byte write and each output read; poll <=50 ms.

        A complete canonical body is never concatenated. One encoder chunk up
        to8192 plus its generator scratch remains held through partial writes.
        The caller must interleave the SDK pump and charge all actual transport
        volumes against the shared original stream/queue/resource controls.
        """
        self._usable()
        try:
            self._busy = True
            try:
                now = self._now()
                if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= .05:
                    raise ProducerDriverError('sdk_producer_poll_bounds')
                if self._ready:
                    return 'ready'
                state = self._native.snapshot()
                if not state['launched']:
                    raise ProducerDriverError('sdk_producer_child_unavailable')
                wait = self._native.observe_wait()
                if wait is not None and not self._input_exhausted:
                    raise ProducerDriverError('sdk_producer_input_incomplete')
                self._prepare_input()
                self._close_input()
                state = self._native.snapshot()
                if self._finish(state):
                    return 'ready'
                io_ready = self._resources is None or self._resources.room() > 0
                eof_ready = (self._resources is not None and not io_ready
                             and self._resources.can_probe_eof())
                reads = {self._native.fileno(name): name for name in self._slabs
                         if not state[name + '_eof'] and (io_ready or eof_ready)}
                if state['wait'] is None:
                    reads[self._native.fileno('pidfd')] = 'wait'
                writes = [self._native.fileno('stdin')] if self._pending is not None and io_ready else []
                delay = min(timeout, max(0, (self._binding['deadline_ns'] - now) / 1e9))
                ready, writable, _ = select.select(list(reads), writes, [], delay)
                self._now()
                if any(reads[fd] == 'wait' for fd in ready):
                    self._native.observe_wait()
                for fd in ready:
                    name = reads[fd]
                    if name != 'wait':
                        self._read(name)
                # A child wait must precede any further input effect in this step.
                if self._native.snapshot()['wait'] is not None and not self._input_exhausted:
                    raise ProducerDriverError('sdk_producer_input_incomplete')
                if writable:
                    self._write()
                if self._finish(self._native.snapshot()):
                    return 'ready'
                return 'progress' if ready or writable else 'idle'
            except BaseException as primary:
                self._poison(primary)
                raise
            finally:
                self._busy = False
        except BaseException as primary:
            if self._failure is None:
                self._poison(primary)
            raise

    def take_output(self):
        self._usable()
        if not self._ready:
            raise ProducerDriverError('sdk_producer_output_pending')
        if self._taken:
            raise ProducerDriverError('sdk_producer_output_consumed')
        try:
            self._now()
        except BaseException as primary:
            self._poison(primary)
            raise
        self._taken = True
        output, self._output = self._output, None
        return output

    def snapshot(self):
        state = self._native.snapshot()
        return dict(schema='cairn.sdk-producer-io/1', **self._binding,
            bootstrap_input=None if self._bootstrap is None else dict(self._transport),
            input_generated_bytes=self._generated, input_generated_sha256=self._generated_hash.hexdigest(),
            input_generated_hash_unknown=self._generated_hash_unknown,
            input_written_bytes=self._written, input_written_sha256=self._written_hash.hexdigest(),
            input_written_hash_unknown=self._write_hash_unknown,
            pending_input_bytes=0 if self._pending is None else len(self._pending) - self._offset,
            input_exhausted=self._input_exhausted, input_close_attempted=self._input_close_attempted,
            stdout_bytes=self._bytes['stdout'], stdout_sha256=self._hash['stdout'].hexdigest(),
            stderr_bytes=self._bytes['stderr'], stderr_sha256=self._hash['stderr'].hexdigest(),
            stream_hash_unknown=tuple(sorted(self._hash_unknown)),
            stdout_eof=state['stdout_eof'], stderr_eof=state['stderr_eof'], wait=state['wait'],
            pid=state['pid'], launch_attempted=state['launch_attempted'], launched=state['launched'],
            native_input=state['native_input'], native_primary=state['primary'],
            native_wait_unknown=state['wait_unknown'], native_signal_unknown=state['signal_unknown'],
            release=state['release'], ready=self._ready, output_taken=self._taken,
            cancelled=self._cancelled, failure=self._failure, cleanup=tuple(sorted(self._cleanup)),
            memory_call_attempts=None, memory_batches=None, memory_call_measurement_attested=False,
            resource_binding=None if self._resources is None else self._resources.snapshot(),
            qualification='unqualified_direct_producer_mechanism')
