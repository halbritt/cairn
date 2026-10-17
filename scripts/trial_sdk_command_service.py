"""Service-owned command execution, driven by authenticated profile-4 frames.

No endpoints, launch policy or original resource authority are created here.
The fixed service builder supplies routing, standing storage and preparation
quotes; the parent holds the real allocation/traffic tickets before messages.
"""
import hashlib
import time

from trial_sdk_command_policy import FixedCommandPolicy, CommandPolicyError
from trial_sdk_incoming import BodyDecoder, IncomingError
from trial_sdk_memory_command_job import CommandJob, CommandJobError
import trial_sdk_command_service_wire as wire


class ServiceError(ValueError):
    pass


class _Deadline(ServiceError):
    pass


def _sublease_guard(method):
    def guarded(self, *args):
        acquired = False
        try:
            try:
                _require(not self._busy, 'service_sublease_busy')
                self._busy = True
                acquired = True
                self._mutating = False
                answer = method(self, *args)
            finally:
                if acquired:
                    self._busy = False
            return answer
        except BaseException:
            if acquired and self._mutating:
                self._unknown = True
            raise
    return guarded


def _require(value, code):
    if not value:
        raise ServiceError(code)


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


class ChildSublease:
    """Child mirror of an already-held parent allocation, never a fresh pool.

    open/begin_turn are trusted authenticated-message handlers. Scalars alone
    prove no parent grant. The parent alone settles/quarantines actual tickets.
    """
    def __init__(self):
        self._lease = None
        self._last_lease = self._last_grant = 0
        self._quote = (0, 0)
        self._raw = self._working = 0
        self._allocations = {}
        self._turn = None
        self._unknown = False
        self._busy = self._mutating = False
        self._last_end = None

    def _live(self):
        _require(self._lease is not None and not self._unknown, 'service_sublease_unavailable')

    @_sublease_guard
    def open(self, lease, raw, working):
        _require(self._lease is None and not self._unknown, 'service_sublease_busy')
        _require(_integer(lease, self._last_lease + 1, 2**53-1)
                 and _integer(raw, 0, wire.RAW_LIMIT)
                 and _integer(working, 0, wire.WORKING_LIMIT)
                 and 0 < raw + working <= wire.WORKING_LIMIT, 'service_sublease_quote')
        self._unknown = self._mutating = True
        self._lease = self._last_lease = lease
        self._quote = (raw, working)
        self._unknown = False

    @_sublease_guard
    def reserve(self, raw, working):
        self._live()
        _require(_integer(raw, 0, wire.RAW_LIMIT) and _integer(working, 0, wire.WORKING_LIMIT)
                 and raw + working > 0, 'service_sublease_reservation')
        if self._raw + raw > self._quote[0] or self._working + working > self._quote[1]:
            return False
        _require(sum(self._allocations.values()) < 2, 'service_sublease_slots')
        self._unknown = self._mutating = True
        self._raw += raw
        self._working += working
        key = (raw, working)
        self._allocations[key] = self._allocations.get(key, 0) + 1
        self._unknown = False
        return True

    @_sublease_guard
    def release(self, raw, working):
        self._live()
        key = (raw, working)
        _require(type(raw) is int and type(working) is int
                 and self._allocations.get(key, 0) > 0, 'service_sublease_release')
        self._unknown = self._mutating = True
        self._allocations[key] -= 1
        if not self._allocations[key]:
            del self._allocations[key]
        self._raw -= raw
        self._working -= working
        self._unknown = False

    @_sublease_guard
    def begin_turn(self, grant, maximum):
        self._live()
        _require(self._turn is None and _integer(grant, self._last_grant + 1, 2**53-1)
                 and _integer(maximum, 0, 1536), 'service_sublease_turn')
        self._unknown = self._mutating = True
        self._last_grant = grant
        self._turn = (grant, maximum, 0)
        self._unknown = False

    def stream_room(self):
        self._live()
        _require(self._turn is not None, 'service_sublease_no_grant')
        return self._turn[1] - self._turn[2]

    @_sublease_guard
    def charge_stream(self, count):
        self._live()
        _require(self._turn is not None and _integer(count, 1, self.stream_room()),
                 'service_sublease_charge')
        self._unknown = self._mutating = True
        grant, maximum, used = self._turn
        self._turn = (grant, maximum, used + count)
        self._unknown = False

    @_sublease_guard
    def finish_turn(self, grant, known, unknown):
        _require(self._turn is not None and grant == self._turn[0]
                 and _integer(known, 0, self._turn[1]) and type(unknown) is bool,
                 'service_sublease_receipt')
        unknown = unknown or self._unknown or known != self._turn[2]
        self._unknown = self._mutating = True
        if not unknown:
            self._turn = None
            self._unknown = False

    @_sublease_guard
    def end(self, lease, unknown):
        _require(self._lease == lease and type(unknown) is bool, 'service_sublease_end')
        if unknown or self._unknown:
            self._unknown = self._mutating = True
            self._last_end = (lease, True)
            return
        _require(not self._allocations and self._turn is None, 'service_sublease_live')
        self._unknown = self._mutating = True
        self._last_end = (lease, False)
        self._lease = None
        self._quote = (0, 0)
        self._unknown = False

    def snapshot(self):
        return dict(lease=self._lease, quote=self._quote, raw_reserved=self._raw,
                    working_reserved=self._working, turn=self._turn,
                    unknown=self._unknown, last_end=self._last_end)


class CommandService:
    """One serialized service with one current job and one outbound record.

    accept receives an already authenticated incoming Frame. sent observes the
    exact pending Frame ONLY after known transport send; EAGAIN keeps that same
    record/loan outside this core. Constructor dependencies and standing storage
    must already be covered by the parent's original resource reservations.
    """
    def __init__(self, policy, cwd, env, budgets, preparation_raw, preparation_working,
                 reply_storage, *, clock=time.monotonic_ns):
        _require(type(policy) is FixedCommandPolicy and type(cwd) is str
                 and cwd.startswith('/') and type(env) is dict
                 and all(type(k) is str and type(v) is str for k, v in env.items()),
                 'service_binding')
        _require(type(budgets) is ChildSublease and callable(clock)
                 and _integer(preparation_raw, 1, wire.RAW_LIMIT)
                 and _integer(preparation_working, 1, wire.WORKING_LIMIT)
                 and preparation_raw + preparation_working <= wire.WORKING_LIMIT,
                 'service_preparation_quote')
        _require(type(reply_storage) is memoryview and reply_storage.format == 'B'
                 and reply_storage.ndim == 1 and reply_storage.c_contiguous
                 and not reply_storage.readonly and len(reply_storage) == 512,
                 'service_reply_storage')
        self._policy, self._cwd, self._env = policy, cwd, env
        self._budgets, self._preparation = budgets, (preparation_raw, preparation_working)
        self._storage, self._clock = reply_storage, clock
        self._transcript = wire.ServiceTranscript()
        self._reply = None
        self._busy = self._fatal = False
        self._primary = None
        self._job = self._plan = self._decoder = self._outcome = None
        self._lease = self._deadline = self._operation = None
        self._prepared_reserved = self._preparation_unknown = False
        self._refusal_pending = None
        self._preparing_refusal = None
        self._last_clock = None
        self._metadata = self._text_state = None
        self._release_requested = self._release_consumed = False
        self._queue(wire.READY, (), operation=0)

    def _now(self):
        _require(not self._fatal, 'service_unavailable')
        now = self._clock()
        _require(type(now) is int and now >= 0 and
                 (self._last_clock is None or now >= self._last_clock), 'service_clock_unknown')
        self._last_clock = now
        if now >= self._deadline:
            raise _Deadline('service_deadline')

    def _queue(self, kind, fields=(), *, data=None, operation=None):
        _require(self._reply is None, 'service_reply_pending')
        size = len(data) if data is not None else wire.STRUCTS[kind].size
        with self._storage[:size] as view:
            if data is None:
                wire.pack_into(kind, view, *fields)
            else:
                view[:] = data
            digest = hashlib.sha256(view).digest()
        self._reply = (kind, self._operation if operation is None else operation, size, digest)

    def pending(self):
        return None if self._reply is None else self._reply[:3]

    def copy_reply(self, destination):
        _require(not self._fatal and self._reply is not None, 'service_reply_unavailable')
        _require(type(destination) is memoryview and destination.format == 'B'
                 and destination.ndim == 1 and destination.c_contiguous
                 and not destination.readonly and len(destination) == self._reply[2],
                 'service_reply_storage')
        with self._storage[:self._reply[2]] as source:
            destination[:] = source

    def _enter(self):
        _require(not self._fatal and not self._busy, 'service_unavailable')
        self._busy = True

    def _poison(self):
        self._fatal = True
        if self._primary is None:
            self._primary = 'service_unknown'

    def accept(self, frame):
        acquired = False
        try:
            try:
                self._enter()
                acquired = True
                _require(wire.TABLE.get(frame.kind, (None,))[0] == 'P2S', 'service_direction')
                _require(self._reply is None or frame.kind == wire.CANCEL, 'service_reply_pending')
                self._transcript.observe(frame)
                with frame.body as body:
                    fields = wire.STRUCTS[frame.kind].unpack(body) if frame.kind in wire.STRUCTS else ()
                    try:
                        self._accept(frame.kind, frame.operation, fields, body)
                    except _Deadline:
                        self._refuse(1)
            finally:
                if acquired:
                    self._busy = False
            return self.pending()
        except BaseException:
            self._poison()
            raise

    def sent(self, frame):
        acquired = False
        try:
            try:
                self._enter()
                acquired = True
                _require(self._reply is not None, 'service_reply_unavailable')
                kind, operation, size, digest = self._reply
                _require((frame.kind, frame.operation) == (kind, operation), 'service_reply_identity')
                with frame.body as body:
                    _require(len(body) == size and hashlib.sha256(body).digest() == digest,
                             'service_reply_identity')
                self._transcript.observe(frame)
                self._reply = None
                try:
                    self._after_sent(kind)
                except _Deadline:
                    self._refuse(1)
            finally:
                if acquired:
                    self._busy = False
            return self.pending()
        except BaseException:
            self._poison()
            raise

    def _refuse(self, code):
        if self._primary is None:
            self._primary = 'service_refused'
        # The parent may already have sent the declared finite request. Keep
        # hashing its bounded frames until END before changing wire phase.
        if self._transcript.snapshot()['phase'] == 'preparing':
            if self._preparing_refusal is None:
                self._preparing_refusal = code
            self._decoder = None
            return
        self._queue(wire.REFUSE, (code,))

    def _accept(self, kind, operation, fields, body):
        if kind == wire.PREPARE:
            op, deadline, lease, raw, working, length, digest = fields
            self._operation, self._deadline, self._lease = operation, deadline, lease
            self._job = self._plan = self._decoder = self._outcome = None
            self._metadata = self._text_state = None
            self._primary = self._refusal_pending = None
            self._preparing_refusal = None
            self._release_requested = self._release_consumed = False
            self._prepared_reserved = self._preparation_unknown = False
            self._budgets.open(lease, raw, working)
            self._now()
            self._preparation_unknown = True
            accepted = self._budgets.reserve(*self._preparation)
            if accepted is False:
                self._preparation_unknown = False
                self._refuse(7)
                return
            _require(accepted is True, 'service_preparation_unknown')
            self._prepared_reserved = True
            self._preparation_unknown = False
            self._decoder = BodyDecoder(length)
        elif kind == wire.REQUEST:
            if self._preparing_refusal is None:
                self._now()
                try:
                    self._decoder.feed(body)
                except IncomingError:
                    self._refuse(3)
        elif kind == wire.REQUEST_END:
            self._prepare_end()
        elif kind == wire.START:
            self._now()
            try:
                self._job.start(self._plan.argv, self._cwd, self._env)
            except CommandJobError:
                self._refuse(1 if self._job.snapshot()['primary'] == 'command_deadline' else 2)
        elif kind == wire.TURN:
            self._turn(fields)
        elif kind == wire.DELIVER:
            self._deliver(fields)
        elif kind == wire.CANCEL:
            if self._job is not None:
                self._job.abort()
            self._refusal_pending = 6
            if self._reply is None:
                self._refusal_pending = None
                self._refuse(6)
        elif kind == wire.RELEASE:
            self._release_requested = True
            self._release_if_ready()
        elif kind == wire.SHUTDOWN:
            self._queue(wire.BYE, (), operation=0)

    def _prepare_end(self):
        if self._preparing_refusal is not None:
            self._refuse(self._preparing_refusal)
            return
        try:
            self._now()
            text = self._decoder.finish()
            state = self._transcript.snapshot()
            _require(bytes.fromhex(self._decoder.raw_sha256) == state['request_sha256'],
                     'service_request_digest')
            request = wire.parse_request(text, self._decoder.raw_bytes, state['request_operation'])
            self._plan = request.plan(self._policy)
            self._job = CommandJob(self._budgets, self._deadline, clock=self._clock)
            self._job.prepare(self._plan.body)
            self._now()
            self._queue(wire.PREPARED, (self._lease, state['request_sha256']))
        except (IncomingError, CommandPolicyError, wire.ServiceWireError):
            self._refuse(3)
        except CommandJobError:
            code = self._job.snapshot()['primary']
            self._refuse(1 if code == 'command_deadline' else 7 if code == 'command_credit_refused' else 2)

    @staticmethod
    def _counts(state):
        return (state['input_written'], state['streams']['stdout']['raw_bytes'],
                state['streams']['stderr']['raw_bytes'])

    @staticmethod
    def _lifecycle(state):
        owner = state['owner']
        bits = int(owner.get('stdin_closed') is True)
        bits |= 2 * int(state['streams']['stdout']['eof'] is True)
        bits |= 4 * int(state['streams']['stderr']['eof'] is True)
        bits |= 8 * int(state['wait'] is not None)
        bits |= 16 * int(state['release'] is not None and state['release'].get('released') is True)
        return bits

    def _turn(self, fields):
        grant, ordinal, maximum, final = fields
        self._budgets.begin_turn(grant, maximum)
        before = self._job.snapshot()
        error = None
        try:
            self._job.step()
        except CommandJobError as exc:
            error = exc
        after = self._job.snapshot()
        deltas = tuple(b-a for a,b in zip(self._counts(before), self._counts(after)))
        _require(all(type(n) is int and 0 <= n <= 512 for n in deltas)
                 and sum(deltas) <= maximum, 'service_progress_unknown')
        charged = self._budgets.snapshot()['turn'][2]
        unknown = (after['outcome_unknown'] or after['reservation_unknown']
                   or charged != sum(deltas) or self._budgets.snapshot()['unknown'])
        self._budgets.finish_turn(grant, sum(deltas), bool(unknown))
        if error is not None or unknown:
            self._refusal_pending = 1 if after['primary'] == 'command_deadline' else 2
        self._queue(wire.PROGRESS, (grant, ordinal, *deltas, self._lifecycle(after), int(bool(unknown))))

    def _output(self):
        try:
            self._now()
            self._outcome = self._job.take_outcome()
            state = self._job.snapshot()
            texts = (self._outcome.stdout, self._outcome.stderr)
            counts, digests = [], []
            for text in texts:
                digest, count = hashlib.sha256(), 0
                for offset in range(0, len(text), 128):
                    self._now()
                    chunk = text[offset:offset+128].encode('utf-8')
                    digest.update(chunk)
                    count += len(chunk)
                counts.append(count)
                digests.append(digest.digest())
            self._now()
            hashes = (state['input_sha256'], state['streams']['stdout']['raw_sha256'],
                      state['streams']['stderr']['raw_sha256'])
            _require(all(type(value) is str for value in hashes), 'service_hash_unknown')
            self._metadata = (self._outcome.stdout_bytes, self._outcome.stderr_bytes,
                              *counts, *(bytes.fromhex(value) for value in hashes), *digests)
            self._text_state = [[0, b''], [0, b'']]
            self._queue(wire.OUTPUT, self._metadata)
        except UnicodeError:
            self._refuse(5)
        except CommandJobError:
            self._refuse(1 if self._job.snapshot()['primary'] == 'command_deadline' else 2)

    def _deliver(self, fields):
        grant, stream, maximum = fields
        self._now()
        text = self._outcome.stdout if stream == 1 else self._outcome.stderr
        state = self._text_state[stream-1]
        data = bytearray()
        while len(data) < maximum:
            if not state[1]:
                _require(state[0] < len(text), 'service_text_length')
                state[1] = text[state[0]:state[0]+128].encode('utf-8')
                state[0] += min(128, len(text)-state[0])
            count = min(maximum-len(data), len(state[1]))
            data.extend(state[1][:count])
            state[1] = state[1][count:]
        self._now()
        self._queue(wire.STDOUT if stream == 1 else wire.STDERR, data=data)

    def _after_sent(self, kind):
        state = self._transcript.snapshot()
        if state['cancelled'] and not state['cancel_acknowledged']:
            self._refusal_pending = None
            self._refuse(6)
        elif kind == wire.PROGRESS:
            if self._refusal_pending is not None:
                code, self._refusal_pending = self._refusal_pending, None
                self._refuse(code)
            elif state['lifecycle'] == 31:
                self._output()
        elif kind in (wire.OUTPUT, wire.STDOUT, wire.STDERR):
            if state['wire_counts'] == state['wire_sizes']:
                self._now()
                wait = self._job.snapshot()['wait']
                self._queue(wire.STATUS, (wait['raw_status'], wait['returncode']))

    def cleanup_step(self):
        """One explicit cleanup action; caller parks on actual readiness."""
        acquired = False
        try:
            try:
                # Cleanup remains available after poisoning, but never overlaps
                # an unfinished command action or another cleanup invocation.
                _require(not self._busy, 'service_busy')
                self._busy = True
                acquired = True
                if self._job is not None:
                    self._job.abort()
                    self._job.cleanup_step()
                if self._release_requested and not self._release_consumed:
                    self._release_if_ready()
            finally:
                if acquired:
                    self._busy = False
            return self.snapshot()
        except BaseException:
            self._poison()
            raise

    def _release_if_ready(self):
        _require(not self._release_consumed and self._reply is None, 'service_release_consumed')
        state = None if self._job is None else self._job.snapshot()
        if state is not None and state['release'] is None:
            return
        self._release_consumed = True
        unknown = self._fatal or self._preparation_unknown or self._budgets.snapshot()['unknown']
        if state is not None and state['release'].get('released') is not True:
            unknown = True
        if not unknown:
            self._outcome = self._metadata = self._text_state = self._decoder = self._plan = None
            try:
                if self._job is not None:
                    self._job.retire()
                if self._prepared_reserved:
                    self._budgets.release(*self._preparation)
                    self._prepared_reserved = False
            except BaseException:
                self._preparation_unknown = True
                raise
        self._budgets.end(self._lease, bool(unknown))
        self._queue(wire.RELEASED, (self._lease, int(bool(unknown))))

    def interests(self):
        return {} if self._job is None else self._job.interests()

    def snapshot(self):
        return dict(fatal=self._fatal, primary=self._primary,
                    transcript=self._transcript.snapshot(), budgets=self._budgets.snapshot(),
                    pending=self.pending(), preparation_reserved=self._prepared_reserved,
                    preparation_unknown=self._preparation_unknown,
                    release_requested=self._release_requested, release_consumed=self._release_consumed,
                    job=None if self._job is None else self._job.snapshot(),
                    qualification='unqualified')

