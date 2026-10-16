"""Explicit real-adapter composition; an actual actor owner is required."""
from trial_sdk_parent_reactor import Engine, Credits, LAYOUT, ReactorError


class OwnedCredits(Credits):
    """Fixed remote quota before actor creation, not remote payload allocation."""
    def __init__(self):
        super().__init__()
        self.counterparty = {name: size for name, size in LAYOUT.items() if name.startswith('node_')}
        self.counterparty['node_control_body'] = 8
        self.counterparty_reserved = sum(self.counterparty.values())
        if self.counterparty_reserved > self.limit:
            raise ReactorError('counterparty_quota')
        self.used = self.peak = self.counterparty_reserved

    def retire_counterparty(self):
        # Only OwnedEngine after its held actor port's actual wait+cleanup.
        self.check()
        if not self.counterparty_reserved or self.used != self.counterparty_reserved or self.leased or self.external:
            self.fail()
        self._refund(self.counterparty_reserved)
        self.counterparty_reserved = 0
        self.counterparty.clear()


class OwnedEngine(Engine):
    CREDIT_CLASS = OwnedCredits
    STORAGE_LAYOUT = {name: size for name, size in LAYOUT.items()
                      if not name.startswith('node_') and name != 'receive_aux'}
    REQUIRED = {**Engine.REQUIRED, 'actor': ('wait_ready', 'release')}
    ORDER = (*Engine.ORDER[:-1], 'actor_wait', 'internal')

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.release_operation = None
        self.target_released = self.local_released = False
        self.release_ack_queued = self.release_ack_sent = False
        self.actor_wait_code = None
        self.actor_release_attempted = self.actor_released = False

    def snapshot(self):
        result = super().snapshot()
        result['backing_reserved'] = result.pop('physical_used')
        result['backing_quota_peak'] = result.pop('physical_peak')
        result.update(target_released=self.target_released, local_released=self.local_released,
                      actor_released=self.actor_released, actor_wait=self.actor_wait_code,
                      release_operation=self.release_operation, release_ack_sent=self.release_ack_sent,
                      counterparty_reserved=self.root.counterparty_reserved,
                      local_reserved=self.root.used-self.root.counterparty_reserved,
                      qualification='owned_adapters_required_not_runtime_admission')
        return result

    def _active(self, key):
        if key == 'actor_wait':
            return self.state not in ('RELEASED', 'UNRESOLVED') and self.actor_wait_code is None
        if self.local_released:
            return False
        if key == 'data_rx' and self.state == 'WAIT_RELEASE' and not self.unknown and not self.root.unknown:
            return self.regions['data'].active is None and self._reply() is None and self.launch_reply is None
        return super()._active(key)

    def _io(self, key):
        if key != 'actor_wait':
            return super()._io(key)
        code = self.ports['actor'].wait_ready()
        if code is None:
            return
        if type(code) is not int or not -(2**31) <= code < 2**31:
            self._fail('actor_wait_unknown', True)
            return
        self.actor_wait_code = code
        if not self.release_ack_sent and self.started:
            self._fail('actor_exit_before_release')
        elif code != 0 and self.primary is None:
            self.primary = 'actor_failed'

    def _semantic_data(self, frame):
        if frame.kind != 8:
            return super()._semantic_data(frame)
        if (self.state != 'WAIT_RELEASE' or self.release_operation is not None or
                frame.operation <= self.last_data_op or len(frame.body) != 0):
            raise ReactorError('data_semantics_invalid')
        self.release_operation = frame.operation
        self.last_data_op = frame.operation

    def _finish_send(self, which, identity):
        box = self.box[which]
        purpose = box[2] if box is not None else None
        super()._finish_send(which, identity)
        if which == 'data' and purpose == 'target_release' and self.completed[which] == identity and not self.unknown:
            self.release_ack_sent = True

    def _internal_ready(self):
        if self.local_released:
            if not self.unknown and not self.root.unknown and self.actor_wait_code is not None and not self.actor_released:
                return self._finish_actor
            return None
        if (self.target_released and not self.release_ack_queued and not self.unknown and not self.root.unknown and
                self.regions['data'].active is None):
            return self._queue_release_ack
        action = super()._internal_ready()
        if self.state == 'WAIT_RELEASE' and self.release_operation is None and action == self._release_one:
            return None
        return action

    def _queue_release_ack(self):
        self.release_ack_queued = True
        self._encode_scalar('data', (139, self.release_operation, 1), 'target_release')

    def _release_one(self):
        if self.started and self.release_operation is None:
            self.state = 'WAIT_RELEASE'
            return
        if self.stop_deadline is None:
            self.stop_deadline = self.last_time+self.stop_seconds
        target_keys = ('streams', 'process', 'launch') if self.started else ('launch',)
        for key in target_keys:
            if key not in self.released:
                return self._release_port(key)
        if self.started and not self.target_released:
            self.target_released = True
            return
        if self.started and not self.release_ack_sent:
            return
        self.state = 'RELEASING'
        for key in ('data', 'control'):
            if key not in self.released:
                return self._release_port(key)
        for region in self.regions.values():
            if not region.closed:
                region.retire()
                return
        if self.root.used != self.root.counterparty_reserved or self.root.leased or self.root.external:
            self._fail('local_release_accounting_unknown', True)
            return
        self.local_released = True
        self.state = 'ENGINE_LOCAL_RELEASED'

    def _release_port(self, key):
        if key in self.release_attempted:
            self._fail('release_unknown', True)
            return
        self.release_attempted.add(key)
        method = 'release' if key in ('process', 'launch') else 'close'
        if getattr(self.ports[key], method)() is not None:
            self._fail('release_unknown', True)
            return
        self.released.add(key)

    def _finish_actor(self):
        if self.actor_release_attempted:
            self._fail('actor_release_unknown', True)
            return
        self.actor_release_attempted = True
        if self.ports['actor'].release() is not None:
            self._fail('actor_release_unknown', True)
            return
        self.root.retire_counterparty()
        self.actor_released = True
        self.state = 'RELEASED'
