"""Serialized, trusted nonretaining-view model. No transport or process effects."""
class Refused(RuntimeError):
    pass


class Root:
    def __init__(self, limit, refund=lambda n: True):
        if type(limit) is not int or limit < 0:
            raise ValueError('limit')
        self.limit, self.refund = limit, refund
        self.used = self.peak = self.leased = self.lease_peak = 0
        self.unknown = False
        self.regions = []

    def check(self):
        if self.unknown:
            raise Refused('root_unknown')

    def fail(self):
        self.unknown = True
        raise Refused('ownership_unknown')

    def allocate(self, name, size):
        self.check()
        if type(size) is not int or size <= 0 or any(r.name == name for r in self.regions):
            raise Refused('region_spec')
        if self.used + size > self.limit:
            raise Refused('physical_credit_denied')
        # Debit precedes creation of the backing. Each region is a distinct object.
        self.used += size
        self.peak = max(self.peak, self.used)
        try:
            region = Region(self, name, bytearray(size))
        except BaseException:
            self._refund(size)
            raise
        self.regions.append(region)
        return region

    def end_loan(self, n):
        self.check()
        if type(n) is not int or not 0 < n <= self.leased:
            self.fail()
        self.leased -= n

    def _refund(self, size):
        try:
            outcome = self.refund(size)
        except BaseException:
            self.unknown = True
            raise
        if outcome is not True:
            self.fail()
        self.used -= size


class Region:
    def __init__(self, root, name, backing):
        self.root, self.name, self.backing = root, name, backing
        self.size = len(backing)
        self.active = None
        self.closed = False
        self.generation = 0

    def hold(self, size=None):
        self.root.check()
        size = self.size if size is None else size
        if self.closed or self.active is not None:
            raise Refused('region_not_free')
        if type(size) is not int or not 0 <= size <= self.size:
            raise Refused('view_size')
        self.generation += 1
        hold = Hold(self, self.generation, size)
        self.active = hold
        return hold

    def retire(self):
        self.root.check()
        if self.closed or self.active is not None:
            raise Refused('region_not_retirable')
        self.closed = True
        self.backing = None
        # A failed refund leaves the charge in place and makes the root terminal.
        self.root._refund(self.size)


class Hold:
    def __init__(self, region, generation, size):
        self.region, self.generation = region, generation
        base = memoryview(region.backing)
        self.view = base[:size]
        base.release()
        self.loan = 0
        self.bridge = None
        self.closed = False

    def check(self):
        self.region.root.check()
        if self.closed or self.region.active is not self:
            raise Refused('stale_hold')

    def close(self):
        self.check()
        if self.loan or self.bridge is not None:
            raise Refused('loan_pending')
        self.view.release()
        self.closed = True
        self.region.active = None


class Bridge:
    """One bridge per codec. reserve borrows only the armed exact backing."""
    def __init__(self, root):
        self.root, self.hold = root, None
        self.denied = False

    def arm(self, hold):
        self.root.check()
        hold.check()
        if hold.region.root is not self.root or self.hold is not None or hold.loan or hold.bridge is not None:
            raise Refused('bridge_busy')
        self.hold = hold
        hold.bridge = self

    def reserve(self, n):
        self.root.check()
        h = self.hold
        if h is None or h.closed or h.region.active is not h or h.loan:
            self.root.fail()
        if type(n) is not int or n != len(h.view):
            self.root.fail()
        if self.denied:
            return False
        h.loan = n
        self.root.leased += n
        self.root.lease_peak = max(self.root.lease_peak, self.root.leased)
        return True

    def release(self, n):
        h = self.hold
        self.root.check()
        if h is None or type(n) is not int or n != h.loan or n <= 0:
            self.root.fail()
        self.root.end_loan(n)
        h.loan = 0
        h.bridge = None
        self.hold = None

    def unarm_denied(self):
        if self.hold is None or self.hold.loan:
            self.root.fail()
        self.hold.bridge = None
        self.hold = None


class Channel:
    def __init__(self, codec_class, root, region, direction, *, profile=2):
        if region.root is not root:
            raise Refused('foreign_region')
        self.root, self.region = root, region
        self.bridge = Bridge(root)
        self.codec = codec_class(direction, self.bridge.reserve, self.bridge.release, profile=profile)
        self.pending = None

    def _start(self, size):
        self.root.check()
        if self.pending is not None:
            raise Refused('frame_pending')
        h = self.region.hold(size)
        self.bridge.arm(h)
        return h

    def _issued(self, h, operation):
        try:
            frame = operation()
        except BaseException:
            if h.loan:
                self.root.unknown = True
            elif not self.root.unknown:
                self.bridge.unarm_denied()
                h.close()
            raise
        if frame is None or frame is False:
            self.bridge.unarm_denied()
            h.close()
            return None
        token = Token(self, h, frame)
        self.pending = token
        return token

    def encode(self, kind, op, body):
        body.check()
        if body.region.root is not self.root or body.loan or body.bridge is not None or body.region is self.region:
            raise Refused('same_backing')
        h = self._start(32 + len(body.view))
        return self._issued(h, lambda: self.codec.encode_into(h.view, kind, op, body.view))

    def decode(self, h):
        h.check()
        if h.region is not self.region or self.pending is not None:
            raise Refused('decode_owner')
        self.bridge.arm(h)
        return self._issued(h, lambda: self.codec.decode(h.view))


class Token:
    def __init__(self, channel, hold, frame):
        self.channel, self.hold, self.frame = channel, hold, frame
        self.generation = hold.generation
        self.sequence = frame.sequence
        self.done = False

    def visit(self, consumer):
        """Consumer MUST NOT retain frame views, derived aliases, or mutate them."""
        self.hold.check()
        if self.done:
            raise Refused('stale_token')
        return consumer(self.frame)

    def complete(self, generation, outcome):
        c = self.channel
        c.root.check()
        if self.done or generation != self.generation or c.pending is not self:
            c.root.fail()
        if outcome == 'again':
            return False  # retain this exact encoded frame; no sequence advance
        if outcome != 'known':
            c.root.fail()  # quarantined backing and logical loan, no guessed release
        record, body = self.frame.record, self.frame.body
        try:
            self.frame.release()
            body.release()
            record.release()
        except BaseException:
            c.root.unknown = True
            raise
        self.frame = None
        self.hold.close()
        c.pending = None
        self.done = True
        return True


class SendSlot:
    """Single-frame readiness model. Writer/wake testimonies are external facts."""
    def __init__(self, token):
        self.token = token
        self.ready, self.done, self.epoch = True, False, 0

    @property
    def ticket(self):
        return (self.token.generation, self.epoch)

    def wake(self, ticket):
        self.token.channel.root.check()
        if self.done or self.ready or ticket != self.ticket:
            self.token.channel.root.fail()
        self.ready = True

    def step(self, writer):
        root = self.token.channel.root
        root.check()
        if self.done:
            root.fail()
        if not self.ready:
            return 'blocked'
        try:
            outcome = writer(self.token)
        except BaseException:
            root.unknown = True
            raise
        self.token.complete(self.token.generation, outcome)
        if outcome == 'again':
            self.ready = False
            self.epoch += 1
        else:
            self.done = True
        return outcome
