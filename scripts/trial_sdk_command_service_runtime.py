"""Fixed-builder child record runtime. No endpoint or budget authority is minted.

The parent must hold standing/operation allocations and precommit every possible
response before the triggering message. This child records observations; only
the parent's original ledger settles the designated wire edges.
"""
from dataclasses import dataclass
import copy
import select
import time

from trial_sdk_command_policy import FixedCommandPolicy
from trial_sdk_command_service import CommandService, ChildSublease
from trial_sdk_ipc import Codec
from trial_sdk_memory_broker_socket import RecordSocket
from trial_sdk_parent_storage import Root, Channel
from trial_sdk_peer import AUXILIARY, Peer
import trial_sdk_command_service_wire as wire


RAW_STORAGE = 512 + 512 + 544 + 544 + AUXILIARY


class RuntimeError(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise RuntimeError(code)


@dataclass(frozen=True, slots=True)
class RuntimeBinding:
    """Trusted fixed-source configuration, not deserialized request authority."""
    policy: FixedCommandPolicy
    cwd: str
    env: dict
    path: str
    deadline_ns: int
    standing_raw: int
    standing_working: int
    preparation_raw: int
    preparation_working: int


def _guard(method):
    def guarded(self, *args, **kwargs):
        acquired = False
        try:
            try:
                _require(not self._busy and not self._unknown and not self._closed,
                         'runtime_unavailable')
                self._busy = acquired = True
                result = method(self, *args, **kwargs)
            finally:
                if acquired:
                    self._busy = False
            return result
        except BaseException as exc:
            # The syscall owner may have retained a known prefix before this
            # wrapper's accounting copy was interrupted. Copy that observation
            # once; never repeat the transfer or turn it into usable protocol.
            try:
                if self._socket is not None:
                    self._capture_transfer()
            except BaseException:
                self._cleanup.add('traffic_copy_unknown')
            self._poison(exc)
            raise
    return guarded


class CommandServiceRuntime:
    """Retain this inert owner before start; never pass arbitrary argv or FDs."""
    def __init__(self, binding):
        _require(type(binding) is RuntimeBinding, 'runtime_binding')
        _require(type(binding.policy) is FixedCommandPolicy and type(binding.cwd) is str
                 and binding.cwd.startswith('/') and type(binding.path) is str
                 and binding.path.startswith('/') and type(binding.env) is dict
                 and all(type(k) is str and type(v) is str for k,v in binding.env.items()),
                 'runtime_binding')
        _require(type(binding.deadline_ns) is int and binding.deadline_ns > 0
                 and type(binding.standing_raw) is int
                 and RAW_STORAGE <= binding.standing_raw <= wire.RAW_LIMIT
                 and type(binding.standing_working) is int and binding.standing_working > 0
                 and binding.standing_raw + binding.standing_working <= wire.WORKING_LIMIT,
                 'runtime_standing_quote')
        _require(type(binding.preparation_raw) is int and
                 0 < binding.preparation_raw <= wire.RAW_LIMIT and
                 type(binding.preparation_working) is int and binding.preparation_working > 0
                 and binding.preparation_raw + binding.preparation_working <= wire.WORKING_LIMIT,
                 'runtime_preparation_quote')
        self.binding = binding
        self._busy = self._unknown = self._closed = self._started = False
        self._standing_claimed = self._standing_released = False
        self._primary = None
        self._cleanup = set()
        self._last_clock = None
        self._root = self._socket = self._service = None
        self._service_final = None
        self._regions = []
        self._reply_hold = self._tx = self._rx = None
        self._auxiliary = False
        self._traffic = (0,0,0)  # Last copied syscall ordinal, sent, received.
        self._bye_sent = self._done = False
        self._local_refunded = 0
        self._cleanup_started = False
        self._paced = False

    def _poison(self, error):
        self._unknown = True
        if self._primary is None:
            self._primary = type(error).__name__
        if self._root is not None:
            self._root.unknown = True

    def _now(self):
        now = time.monotonic_ns()
        _require(type(now) is int and now >= 0 and
                 (self._last_clock is None or now >= self._last_clock), 'runtime_clock_unknown')
        self._last_clock = now
        _require(now < self._deadline(), 'runtime_deadline')
        return now

    def _deadline(self):
        deadline = self.binding.deadline_ns
        if self._service is not None:
            state = self._service.snapshot()['transcript']
            if state['phase'] not in ('boot','idle','closing','closed'):
                deadline = min(deadline,state['deadline_ns'])
        return deadline

    def _local_refund(self, size):
        # Local backing retirement only. This never returns the parent ticket.
        _require(type(size) is int and size > 0 and
                 self._local_refunded + size <= RAW_STORAGE-AUXILIARY,
                 'runtime_local_retirement')
        self._local_refunded += size
        return True

    def _reserve_auxiliary(self, size):
        _require(self._standing_claimed and not self._auxiliary and size == AUXILIARY,
                 'runtime_auxiliary')
        self._auxiliary = True
        return True

    def _release_auxiliary(self, size):
        _require(self._auxiliary and size == AUXILIARY, 'runtime_auxiliary')
        self._auxiliary = False

    @_guard
    def start(self):
        _require(not self._started, 'runtime_start_consumed')
        self._started = True
        self._now()
        # Exact local claim of the parent-held child subquote precedes all
        # backing allocation. It does not create or authenticate that ticket.
        self._standing_claimed = True
        self._root = Root(RAW_STORAGE-AUXILIARY, self._local_refund)
        for name,size in (('reply',512),('body',512),('tx',544),('rx',544)):
            self._regions.append(self._root.allocate(name,size))
        self._reply_hold = self._regions[0].hold()
        self._sender = Channel(Codec,self._root,self._regions[2],'S2P',profile=4)
        self._receiver = Channel(Codec,self._root,self._regions[3],'P2S',profile=4)
        self._service = CommandService(
            self.binding.policy,self.binding.cwd,self.binding.env,ChildSublease(),
            self.binding.preparation_raw,self.binding.preparation_working,
            self._reply_hold.view,clock=time.monotonic_ns)
        self._socket = RecordSocket()
        Peer.protect()
        self._socket.connect_parent(self.binding.path,self.binding.deadline_ns,
                                    self._reserve_auxiliary,self._release_auxiliary)
        self._now()

    def _queue(self):
        if self._tx is not None or self._service.pending() is None:
            return
        kind,operation,size = self._service.pending()
        self._tx = {'body':None,'token':None,'kind':kind}
        self._tx['body'] = self._regions[1].hold(size)
        self._service.copy_reply(self._tx['body'].view)
        self._tx['token'] = self._sender.encode(kind,operation,self._tx['body'])
        self._paced = False
        if kind == wire.PROGRESS:
            fields = wire.STRUCTS[kind].unpack(self._tx['body'].view)
            prior = self._service.snapshot()['transcript']
            self._paced = (fields[2:5] == (0,0,0) and fields[-1] == 0
                           and fields[-2] == prior['lifecycle'] and fields[-2] != 31
                           and not prior['cancelled'])

    def _capture_transfer(self):
        receipt = self._socket.snapshot()['last_transfer']
        prior,sent,received = self._traffic
        if receipt is None or receipt[0] <= prior:
            return
        sequence,direction,maximum,count = receipt
        if type(count) is int:
            _require(0 <= count <= maximum, 'runtime_transfer_unknown')
            if direction == 'send':
                sent += count
            elif direction == 'receive':
                received += count
            else:
                raise RuntimeError('runtime_transfer_direction')
        # One tuple publication keeps ordinal and prefix together. The outer
        # guard can copy an uncopied observation without double counting it.
        self._traffic = (sequence,sent,received)
        _require(sent+received <= wire.TRAFFIC_LIMIT, 'runtime_wire_ceiling')

    def _send(self):
        token = self._tx['token']
        def send(frame):
            with frame.record as record:
                return self._socket.send(record)
        try:
            count = token.visit(send)
        except BaseException:
            try:self._capture_transfer()
            except BaseException:self._cleanup.add('traffic_copy_unknown')
            raise
        self._capture_transfer()
        if count == -1:
            return
        token.visit(self._service.sent)
        kind = self._tx['kind']
        token.complete(token.generation,'known')
        self._tx['body'].close()
        self._tx = None
        if kind == wire.BYE:
            self._bye_sent = True

    def _receive(self):
        if self._rx is None:
            self._rx = {'hold':self._regions[3].hold(),'token':None}
        try:
            if self._bye_sent:
                count = self._socket.receive_terminal_into(self._rx['hold'].view)
            else:
                count = self._socket.receive_into(self._rx['hold'].view)
        except BaseException:
            try:self._capture_transfer()
            except BaseException:self._cleanup.add('traffic_copy_unknown')
            raise
        self._capture_transfer()
        if count == -1:
            return
        if self._bye_sent:
            _require(count == 0 and self._socket.snapshot()['terminal_eof'],
                     'runtime_terminal_unknown')
            self._rx['hold'].close()
            self._rx = None
            self._done = True
            return
        self._rx['hold'].close()
        self._rx['hold'] = self._regions[3].hold(count)
        self._rx['token'] = self._receiver.decode(self._rx['hold'])
        token = self._rx['token']
        token.visit(self._service.accept)
        token.complete(token.generation,'known')
        self._rx = None
        if self._service.snapshot()['transcript']['cancelled']:
            self._paced = False

    def _needs_cleanup(self):
        if self._service is None:
            return False
        state = self._service.snapshot()
        job = state['job']
        return (job is not None and job['release'] is None and
                not job['release_attempted'] and
                (self._unknown or state['fatal'] or state['primary'] is not None or
                 state['transcript']['cancelled'] or state['release_requested']))

    def _cleanup_interest(self):
        if not self._needs_cleanup():
            return None
        if not self._cleanup_started:
            self._cleanup_started = True
            self._service.cleanup_step()
        if self._needs_cleanup():
            return self._service.interests().get('pidfd')
        return None

    @_guard
    def step(self, timeout=0.05):
        _require(self._started and not self._done and
                 type(timeout) in (int,float) and 0 <= timeout <= 0.05,
                 'runtime_step')
        now = self._now()
        cleanup_fd = self._cleanup_interest()
        self._queue()
        fd = self._socket.fileno()
        events = select.POLLIN | (select.POLLOUT if self._tx is not None and not self._paced else 0)
        poll = select.poll();poll.register(fd,events)
        paced_fds = ()
        if self._paced:
            interests = self._service.interests()
            paced_fds = tuple(interests.values())
            for name,interest in interests.items():
                poll.register(interest,select.POLLOUT if name=='stdin' else select.POLLIN)
        if cleanup_fd is not None:
            poll.register(cleanup_fd,select.POLLIN)
        milliseconds = min(timeout*1000,(self._deadline()-now)/1_000_000)
        ready = dict(poll.poll(milliseconds))
        self._now()
        observed = ready.get(fd,0)
        _require(not observed & select.POLLNVAL, 'runtime_readiness_unknown')
        if observed & (select.POLLIN|select.POLLHUP|select.POLLERR):
            self._receive()
        if any(ready.get(interest,0) for interest in paced_fds):
            _require(not any(ready.get(interest,0)&select.POLLNVAL for interest in paced_fds),
                     'runtime_readiness_unknown')
            self._paced = False
        if observed & select.POLLOUT and self._tx is not None and not self._paced:
            self._send()
        if cleanup_fd is not None and ready.get(cleanup_fd,0):
            self._service.cleanup_step()
        # A just-received CANCEL/RELEASE can initiate owned cleanup once; later
        # turns park on the actual job pidfd rather than polling writable stdin.
        self._cleanup_interest()
        self._now()
        return self.snapshot()

    def cleanup_step(self, timeout=0.05):
        """Independent bounded job abandonment after channel/core failure."""
        acquired = False
        try:
            try:
                _require(not self._busy and type(timeout) in (int,float)
                         and 0 <= timeout <= 0.05, 'runtime_cleanup')
                self._busy = acquired = True
                if self._service is not None:
                    # This is explicit abandonment; no grant is manufactured.
                    self._service.cleanup_step()
                    state = self._service.snapshot()['job']
                    if state is not None and state['release'] is None and not state['release_attempted']:
                        now = time.monotonic_ns()
                        remaining = max(0,(self._deadline()-now)/1_000_000)
                        fd = self._service.interests().get('pidfd')
                        if fd is not None and remaining:
                            poll = select.poll();poll.register(fd,select.POLLIN)
                            if poll.poll(min(timeout*1000,remaining)):
                                self._service.cleanup_step()
            finally:
                if acquired:
                    self._busy = False
            return self.snapshot()
        except BaseException as exc:
            self._cleanup.add('job_cleanup_unknown')
            self._poison(exc)
            raise

    def close(self):
        """Independent once-only channel cleanup, not proof of child-job release."""
        if self._closed:
            return
        self._cleanup.add('runtime_close_unknown')
        self._closed = True
        try:
            try:
                if self._service is not None and not self._done:
                    try:self.cleanup_step(0)
                    except BaseException:self._cleanup.add('job_cleanup_unknown')
            finally:
                if self._socket is not None:
                    try:self._socket.close()
                    except BaseException:self._cleanup.add('socket_cleanup_unknown')
            if self._done and not self._unknown:
                _require(self._tx is None and self._rx is None and not self._auxiliary
                         and self._socket.snapshot()['closed_known'], 'runtime_close_unknown')
                self._service_final = self._service.snapshot()
                _require(self._service_final['budgets']['lease'] is None,
                         'runtime_operation_still_held')
                self._service = None  # Ends the core's reply-storage alias.
                self._reply_hold.close()
                self._reply_hold = None
                for region in self._regions:
                    region.retire()
                _require(self._root.used == 0, 'runtime_backings_still_held')
                self._standing_released = True  # Local aliases only, no parent refund.
            self._cleanup.discard('runtime_close_unknown')
        except BaseException as exc:
            self._poison(exc)
            raise

    def snapshot(self):
        return dict(started=self._started,closed=self._closed,unknown=self._unknown,
                    primary=self._primary,cleanup=tuple(sorted(self._cleanup)),
                    standing_claimed=self._standing_claimed,standing_released=self._standing_released,
                    backing_bytes=0 if self._root is None else self._root.used,
                    sent_bytes=self._traffic[1],received_bytes=self._traffic[2],
                    send_pending=self._tx is not None,receive_pending=self._rx is not None,
                    bye_sent=self._bye_sent,done=self._done,
                    paced=self._paced,
                    socket=None if self._socket is None else self._socket.snapshot(),
                    service=copy.deepcopy(self._service_final) if self._service is None else self._service.snapshot(),
                    qualification='unqualified')


def serve(binding):
    """Fixed child builder entrypoint; original owner retains outer containment."""
    owner = CommandServiceRuntime(binding)
    primary = None
    try:
        owner.start()
        while not owner.snapshot()['done']:
            owner.step()
        owner.close()
        return owner.snapshot()
    except BaseException as exc:
        primary = exc
        raise
    finally:
        try:
            while owner._service is not None:
                state = owner._service.snapshot()['job']
                if state is None or state['release'] is not None or state['release_attempted']:
                    break
                owner.cleanup_step()
                if time.monotonic_ns() >= owner._deadline():
                    break
        except BaseException:
            owner._cleanup.add('job_cleanup_unknown')
        try:owner.close()
        except BaseException:
            if primary is None:raise
