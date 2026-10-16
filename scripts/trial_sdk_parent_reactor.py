"""Cairn parent-reactor/2 candidate. Required real adapters are not supplied here."""
from dataclasses import dataclass
from math import isfinite
from trial_sdk_parent_storage import Root, Channel, Refused


class ReactorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Limits:
    launch_bytes: int = 8500
    output_bytes: int = 4 * 1024 * 1024
    input_bytes: int = 3 * 1024 * 1024


class Credits(Root):
    """Opaque custody reserves are debits for ITS allocation, not alias loans."""
    def __init__(self):
        super().__init__(24576)
        self.external = []
        self.external_peak = 0
        self.denied_external_n = None

    def reserve_external(self, n):
        self.check()
        if type(n) is not int or n <= 0:
            self.fail()
        if self.used + n > self.limit:
            self.denied_external_n = n
            return False
        self.used += n
        self.peak = max(self.peak, self.used)
        self.external.append(n)
        self.external_peak = max(self.external_peak, sum(self.external))
        return True

    def release_external(self, n):
        self.check()
        if type(n) is not int or n not in self.external:
            self.fail()
        self.external.remove(n)
        self.used -= n


# Conservative resident counterparty/standing charges, not real allocations in
# another process. Each local model backing remains separate and owned.
LAYOUT = {
    'node_offered': 17000, 'node_readable': 2048, 'node_encoder': 2048,
    'node_data_tx': 544, 'node_data_rx': 544,
    'node_control_tx': 33, 'node_control_rx': 33,
    'data': 544, 'control_tx': 33, 'control_rx': 33,
    'output_source': 512, 'receive_aux': 480,
    'node_c_control': 112, 'node_c_credential': 12, 'node_c_fd': 4,
    'node_control_body': 1, 'control_body': 1, 'reply_body': 8,
}
# Custody scratch/encoding are reserved dynamically by actual InputCustody.
# Reply body is eight (not five) because actual-wait notification needs eight.


class Engine:
    CREDIT_CLASS = Credits
    STORAGE_LAYOUT = LAYOUT
    REQUIRED = {
        'data': ('receive_into', 'send', 'close'),
        'control': ('receive_into', 'send', 'close'),
        'launch': ('begin', 'collect', 'commit', 'cancel', 'release'),
        'process': ('request_stop', 'signal', 'wait_ready', 'release'),
        'native': ('write', 'close'),
        'streams': ('read_into', 'close'),
        'validator': ('validate',),
    }
    ORDER = ('control_rx', 'control_tx', 'wait', 'stdin', 'stdout',
             'stderr', 'data_tx', 'data_rx', 'internal')

    def __init__(self, *, codec, receiver, ports, clock, work_deadline, stop_seconds,
                 limits=Limits()):
        if set(ports) != set(self.REQUIRED):
            raise ReactorError('missing_ports')
        for key, methods in self.REQUIRED.items():
            if any(not callable(getattr(ports[key], name, None)) for name in methods):
                raise ReactorError('incomplete_port')
        if (not callable(clock) or type(work_deadline) not in (int, float) or not isfinite(work_deadline) or
                type(stop_seconds) not in (int,float) or not isfinite(stop_seconds) or not 0 < stop_seconds <= 10):
            raise ReactorError('deadline_contract')
        if limits != Limits():
            raise ReactorError('fixed_limits')
        now = clock()
        if type(now) not in (int, float) or not isfinite(now) or not now < work_deadline:
            raise ReactorError('deadline_expired')
        self.ports, self.clock = ports, clock
        self.limits, self.work_deadline, self.stop_seconds = limits, work_deadline, stop_seconds
        self.last_time, self.stop_deadline = now, None
        self.root = self.CREDIT_CLASS()
        self.regions = {n: self.root.allocate(n, size) for n, size in self.STORAGE_LAYOUT.items()}
        self.setup_retired = False
        # These phase loans do not claim that actual Peer constructors used them.
        for name in ('data', 'node_data_tx'):
            if name not in self.regions:continue
            h = self.regions[name].hold(512)
            h.close()
        self.setup_retired = True
        self.rx = Channel(codec, self.root, self.regions['data'], 'N2P_DATA')
        self.tx = Channel(codec, self.root, self.regions['data'], 'P2N_DATA')
        self.crx = Channel(codec, self.root, self.regions['control_rx'], 'N2P_CONTROL')
        self.ctx = Channel(codec, self.root, self.regions['control_tx'], 'P2N_CONTROL')
        self.receiver = receiver(ports['validator'].validate,
                                 self.root.reserve_external, self.root.release_external,
                                 self._native_write, self._native_close,
                                 maximum=limits.input_bytes)
        self.state, self.primary, self.unknown = 'COLLECTING', None, False
        self.started, self.native_closed, self.stop_requested = False, False, False
        self.launch_op, self.launch_total, self.launch_seen = None, None, 0
        self.last_data_op = self.last_control_op = 0
        self.output_bytes = 0
        self.stdout_credit = 0
        self.eof = {'stdout': False, 'stderr': False}
        self.eof_sent = (False, False)
        self.output_end_sent = False
        self.wait_code, self.wait_sent = None, False
        self.box = {'data': None, 'control': None}
        self.control_reply = None
        self.pending_output = None
        self.launch_reply = None
        self.input_cancelled = False
        self.end_requested = False
        self.ready = {key: False for key in self.ORDER if key != 'internal'}
        self.epoch = {key: 0 for key in self.ready}
        self.generation = 1
        self.cursor = 0
        self.completed = {'data': None, 'control': None}
        self.released = set()
        self.release_attempted = set()
        self.actions = 0

    def _reply(self):
        return self.receiver.reply

    def _native_write(self, body):
        if not self.started or self.native_closed or self.state != 'RUNNING':
            raise ReactorError('native_write_forbidden')
        return self.ports['native'].write(body)

    def _native_close(self):
        if not self.started or self.native_closed:
            return
        self.ports['native'].close()
        self.native_closed = True

    def _fail(self, code, unknown=False):
        if self.primary is None:
            self.primary = code
        self.unknown |= unknown
        if self.stop_deadline is None:
            self.stop_deadline = self.last_time + self.stop_seconds
        self.state = 'STOPPING'

    def snapshot(self):
        return dict(state=self.state, primary=self.primary, unknown=self.unknown,
                    actual_wait=self.wait_code, eof=dict(self.eof),
                    native_closed=self.native_closed, stdout_credit=self.stdout_credit, physical_used=self.root.used,
                    physical_peak=self.root.peak, logical_leased=self.root.leased,
                    external_reserved=sum(self.root.external),
                    released=tuple(sorted(self.released)), release_attempted=tuple(sorted(self.release_attempted)), actions=self.actions,
                    qualification='source_model_only')

    def _active(self, key):
        if self.state in ('RELEASED', 'UNRESOLVED', 'RELEASING'):
            return False
        if key == 'wait':
            return self.started and self.wait_code is None
        if self.unknown or self.root.unknown:
            return False
        if key == 'control_rx':
            return self.box['control'] is None and self.control_reply is None
        if key == 'control_tx':
            return self.box['control'] is not None
        if key == 'data_tx':
            return self.box['data'] is not None
        if key == 'data_rx':
            return (self.state in ('COLLECTING', 'RUNNING', 'WAIT_OBSERVED', 'STOPPING') and
                    self.regions['data'].active is None and self._reply() is None and
                    self.launch_reply is None)
        if key in ('stdout', 'stderr'):
            return (self.started and not self.eof[key] and self.pending_output is None and
                    (key != 'stdout' or self.stdout_credit > 0))
        if key == 'stdin':
            snap = self.receiver.snapshot()
            return (self.state == 'RUNNING' and self._reply() is None and
                    not self.native_closed and
                    (snap['validated_bytes'] > snap['known_native_written_bytes'] or snap['paused'] or self.end_requested))
        return False

    def interests(self):
        return {k: (self.generation, self.epoch[k]) for k in self.ready if self._active(k) and not self.ready[k]}

    def notify(self, key, ticket):
        if (key not in self.ready or not self._active(key) or self.ready[key] or
                ticket != (self.generation, self.epoch[key])):
            self._fail('stale_readiness', True)
            return False
        self.ready[key] = True
        return True

    def cancel(self):
        if self.state not in ('RELEASED', 'UNRESOLVED'):
            self._fail('cancelled')

    def step(self):
        if self.state in ('RELEASED', 'UNRESOLVED'):
            return 'terminal'
        try:now = self.clock()
        except BaseException:
            self._fail('clock_unknown', True)
            self.state = 'UNRESOLVED'
            return 'clock_unknown'
        if type(now) not in (int, float) or not isfinite(now) or now < self.last_time:
            self._fail('clock_invalid', True)
        else:
            self.last_time = now
        if self.stop_deadline is not None and self.last_time >= self.stop_deadline:
            self.state = 'UNRESOLVED'
            return 'shutdown_deadline'
        if self.primary is None and self.state != 'RELEASING' and self.last_time >= self.work_deadline:
            self._fail('work_deadline')
            return 'work_deadline'
        # Round robin bounds control/reap delay to at most one pass, independent
        # of a parked data outbox. One selected action per call.
        for offset in range(len(self.ORDER)):
            index = (self.cursor + offset) % len(self.ORDER)
            key = self.ORDER[index]
            if key == 'internal':
                action = self._internal_ready()
                if action is None:
                    continue
            else:
                if not self._active(key) or not self.ready[key]:
                    continue
                self.ready[key] = False
                self.epoch[key] += 1
                action = lambda key=key: self._io(key)
            self.cursor = (index + 1) % len(self.ORDER)
            self.actions += 1
            try:
                action()
            except BaseException:
                self._fail('action_unknown', True)
            return key
        return 'idle'

    def _io(self, key):
        if key in ('data_rx', 'control_rx'):
            return self._receive('data' if key == 'data_rx' else 'control')
        if key in ('data_tx', 'control_tx'):
            return self._send('data' if key == 'data_tx' else 'control')
        if key == 'wait':
            code = self.ports['process'].wait_ready()
            if code is None:
                return
            if type(code) is not int or not -(2**31) <= code < 2**31:
                self._fail('wait_unknown', True)
                return
            self.wait_code = code
            if self.primary is None:
                self.state = 'WAIT_OBSERVED'
                if not self.native_closed:self._fail('exit_before_input_close')
            return
        if key == 'stdin':
            self.receiver.advance()
            return
        return self._read_output(key)

    def _receive(self, which):
        region = self.regions['data' if which == 'data' else 'control_rx']
        h = region.hold()
        try:
            n = self.ports[which].receive_into(h.view)
        except BaseException:
            self._fail('receive_unknown', True)
            return  # exact backing quarantined
        if n == -1 and type(n) is int:
            h.close()
            return
        if type(n) is not int or not 32 <= n <= len(h.view):
            self._fail('transport_death_or_invalid_record', True)
            return
        old = h.view
        h.view = old[:n]
        old.release()
        frame = (self.rx if which == 'data' else self.crx).decode(h)
        try:
            frame.visit(self._semantic_data if which == 'data' else self._semantic_control)
        except ReactorError as e:
            code = str(e)
            allowed = ('launch_header_invalid', 'launch_sequence_invalid', 'data_semantics_invalid', 'control_semantics_invalid', 'stdout_credit_invalid')
            self._fail(code if code in allowed else 'port_refused')
        except BaseException:
            self._fail('semantic_or_effect_unknown', True)
        if not self.root.unknown:
            frame.complete(frame.generation, 'known')

    def _semantic_data(self, frame):
        kind, op, body = frame.kind, frame.operation, frame.body
        if kind == 6:
            # Credit is independent of input operation IDs and remains receivable
            # after actual wait/stop, so exhausted credit never implies EOF.
            grant = int.from_bytes(body, 'big')
            if (not self.started or self.state not in ('RUNNING', 'WAIT_OBSERVED', 'STOPPING') or
                    op != 0 or len(body) != 2 or not 1 <= grant <= 512 or
                    self.stdout_credit != 0 or self.eof['stdout']):
                raise ReactorError('stdout_credit_invalid')
            self.stdout_credit = grant
            return
        if self.state == 'COLLECTING':
            if kind == 1 and self.launch_op is None:
                total = int.from_bytes(body[:4], 'big')
                version = int.from_bytes(body[4:8], 'big')
                if version != 2 or not 0 < total <= self.limits.launch_bytes:
                    raise ReactorError('launch_header_invalid')
                self.ports['launch'].begin(total, op)
                self.launch_op, self.launch_total = op, total
            elif kind == 2 and op == self.launch_op and self.launch_seen + len(body) <= self.launch_total:
                view = body.toreadonly()
                try:self.ports['launch'].collect(view)
                finally:view.release()
                self.launch_seen += len(body)
            elif kind == 3 and op == self.launch_op and self.launch_seen == self.launch_total:
                # Adapter must validate the complete launch BEFORE any process effect;
                # it binds the already supplied process/native/stream ports itself.
                self.ports['launch'].commit()
                self.started, self.state = True, 'RUNNING'
                self.last_data_op = op
                self.launch_reply = (129, op, None)
            else:
                raise ReactorError('launch_sequence_invalid')
            return
        if self.state != 'RUNNING' or kind not in (9, 5) or op <= self.last_data_op:
            raise ReactorError('data_semantics_invalid')
        self.last_data_op = op
        view = body.toreadonly()
        try:self.receiver.receive(kind, op, view, int(self.last_time * 1_000_000_000))
        finally:view.release()
        if kind == 5:self.end_requested = True

    def _semantic_control(self, frame):
        if frame.kind != 201 or frame.operation <= self.last_control_op or frame.body[0] not in (1, 2, 3):
            raise ReactorError('control_semantics_invalid')
        self.last_control_op = frame.operation
        command = frame.body[0]
        # Cancellation has its selected stop policy; explicit signals keep their name.
        # Byte acknowledgement means request accepted, NEVER process death.
        if command == 1:
            self._fail('cancelled')
            accepted = 1
        elif not self.started:
            accepted = 0
        else:
            outcome = self.ports['process'].signal(frame.operation, 'SIGTERM' if command == 2 else 'SIGKILL')
            if outcome not in ('accepted', 'rejected'):
                self._fail('signal_unknown', True)
                return
            accepted = int(outcome == 'accepted')
        self.control_reply = (202, frame.operation, accepted)

    def _read_output(self, which):
        width = min(512, self.stdout_credit) if which == 'stdout' else 512
        if width <= 0:raise ReactorError('stdout_credit_unavailable')
        h = self.regions['output_source'].hold(width)
        try:n = self.ports['streams'].read_into(which, h.view)
        except BaseException:
            self._fail('stream_read_unknown', True)
            return
        if type(n) is not int or n < -1 or n > len(h.view):
            self._fail('stream_read_unknown', True)
            return
        if n <= 0:
            h.close()
            if n == 0:self.eof[which] = True
            return
        if which == 'stdout':self.stdout_credit -= n
        if self.output_bytes + n > self.limits.output_bytes:
            self._fail('stream_budget')
            h.close()
            return
        self.output_bytes += n
        old = h.view;h.view = old[:n];old.release()
        self.pending_output = (134 if which == 'stdout' else 136, h)

    def _identity(self, token):
        return token.visit(lambda f:(id(token), token.generation, f.sequence, f.kind, f.operation))

    def _encode_scalar(self, which, reply, purpose):
        kind, op, value = reply
        size = {129:0, 132:0, 133:5, 135:0, 137:8, 138:2, 139:1, 140:2, 202:1}[kind]
        h = self.regions['control_body' if which == 'control' else 'reply_body'].hold(size)
        # Assignment is scalar-by-scalar: no temporary bytes/int.to_bytes backing.
        if kind == 133:
            h.view[0] = 1
            for i in range(4):h.view[4-i] = (value >> (8*i)) & 255
        elif kind == 137:
            for i in range(4):h.view[3-i] = (value >> (8*i)) & 255
            for i in range(4,8):h.view[i] = 0
        elif kind == 138:
            h.view[0],h.view[1] = value
        elif size:
            for i in range(size):h.view[size-1-i] = (value >> (8*i)) & 255
        token = (self.tx if which == 'data' else self.ctx).encode(kind, op, h)
        h.close()
        self.box[which] = (token, self._identity(token), purpose, reply)

    def _send(self, which):
        token, identity, purpose, reply = self.box[which]
        if self._identity(token) != identity:
            self._fail('send_identity_unknown', True)
            return
        if purpose == 'input' and self._reply() != reply:
            self._fail('stale_receiver_reply', True)
            return
        def send(frame):
            view = frame.record.toreadonly()
            try:return self.ports[which].send(view),len(view)
            finally:view.release()
        try:n, size = token.visit(send)
        except BaseException:
            self._fail('send_unknown', True)
            return
        if type(n) is int and n == -1:
            return  # outbox retained; gate requires a new readiness epoch
        if type(n) is not int or n != size:
            self._fail('send_unknown', True)
            return
        self._finish_send(which, identity)

    def _finish_send(self, which, identity):
        box = self.box[which]
        if box is None or identity != box[1] or identity == self.completed[which]:
            self._fail('stale_send_completion', True)
            return
        token, _, purpose, reply = box
        if purpose == 'input' and self._reply() != reply:
            self._fail('stale_receiver_reply', True)
            return
        # Mark known send before release/callback; no later failure permits replay.
        self.completed[which] = identity
        self.box[which] = None
        token.complete(token.generation, 'known')
        if purpose == 'input':self.receiver.reply_sent()
        elif purpose == 'launch':self.launch_reply = None
        elif purpose == 'control':self.control_reply = None
        elif purpose == 'eof':self.eof_sent = reply[2]
        elif purpose == 'output_end':self.output_end_sent = True
        elif purpose == 'wait':self.wait_sent = True

    def _internal_ready(self):
        if self.unknown or self.root.unknown:
            return None
        if self.state == 'STOPPING' and not self.stop_requested:
            return self._stop
        if self.state == 'STOPPING' and self.stop_requested and not self.input_cancelled:
            return self._cancel_input
        if self.box['control'] is None and self.control_reply is not None:
            return lambda:self._encode_scalar('control', self.control_reply, 'control')
        if self.regions['data'].active is None:
            if self.launch_reply is not None:
                return lambda:self._encode_scalar('data', self.launch_reply, 'launch')
            if self._reply() is not None:
                return lambda:self._encode_scalar('data', self._reply(), 'input')
            if self.pending_output is not None:
                return self._encode_output
            current = (self.eof['stdout'], self.eof['stderr'])
            if current != self.eof_sent:
                return lambda:self._encode_scalar('data', (138,0,current), 'eof')
            if all(current) and not self.output_end_sent:
                return lambda:self._encode_scalar('data',(135,0,None),'output_end')
            if self.wait_code is not None and not self.wait_sent:
                return lambda:self._encode_scalar('data',(137,0,self.wait_code),'wait')
        if (self.wait_code is not None and all(self.eof.values()) and self.native_closed and
                self.wait_sent and self.output_end_sent and not any(self.box.values()) and
                self.pending_output is None and not self.root.external):
            return self._release_one
        if not self.started and self.stop_requested and not any(self.box.values()) and self.control_reply is None:
            return self._release_one
        return None

    def _encode_output(self):
        kind, h = self.pending_output
        token = self.tx.encode(kind, 0, h)
        h.close()
        self.pending_output = None
        self.box['data'] = (token, self._identity(token), 'output', None)

    def _stop(self):
        self.stop_requested = True
        if not self.started:
            self.ports['launch'].cancel()
        else:
            outcome = self.ports['process'].request_stop(self.primary)
            if outcome not in ('accepted', 'already_exited'):
                self._fail('stop_unknown', True)
                return

    def _cancel_input(self):
        self.input_cancelled = True
        self.receiver.cancel()
        self.native_closed = self.started and self.receiver.snapshot()['closed_known']

    def _release_one(self):
        # Exactly one owned release operation per step; failure never retried.
        self.state = 'RELEASING'
        if self.stop_deadline is None:self.stop_deadline = self.last_time + self.stop_seconds
        keys = ('streams', 'process', 'launch', 'data', 'control') if self.started else ('launch', 'data', 'control')
        for key in keys:
            if key not in self.released:
                if key in self.release_attempted:
                    self._fail('release_unknown', True)
                    return
                self.release_attempted.add(key)
                method = 'release' if key in ('process','launch') else 'close'
                getattr(self.ports[key], method)()
                self.released.add(key)
                return
        for region in self.regions.values():
            if not region.closed:
                region.retire()
                return
        if self.root.used or self.root.leased:
            self._fail('release_accounting_unknown', True)
            return
        self.state = 'RELEASED'
