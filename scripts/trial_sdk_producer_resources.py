"""Trusted producer binding to the original allocation and shared DATA stream.

No owner, ledger, allocation or DATA ticket is created here. The builder quotes
all simultaneous RPC/driver/engine aliases before constructing either endpoint.
This checks held identities and named minima, not authority or physical fit.
"""
from trial_sdk_attempt_resources import AttemptResources
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_stream_credit import StreamCredit
from trial_sdk_incoming import MAX_CHUNK_BYTES
from trial_sdk_resource_failure import freeze_resource


class ProducerResourceError(ValueError):
    pass


class ProducerResources:
    # Named canonical buffers, output slabs and UTF-8 carry only; working
    # objects and other endpoints must also be covered by the builder's quote.
    MINIMUM_RAW = 3 * MAX_CHUNK_BYTES + 1024 + 4

    def __init__(self, resources, owner, domain, allocation, stream):
        if (type(resources) is not AttemptResources or type(owner) is not NativeProcessOwner
                or type(stream) is not StreamCredit or stream.resources is not resources):
            raise ProducerResourceError('producer_resource_binding')
        resources._check()
        held_domain, lifetime, raw, working = resources._lookup(resources._memory, allocation)
        if (resources._domain(domain)['owner'] is not owner or held_domain is not domain
                or lifetime != 'operation' or raw < self.MINIMUM_RAW or working <= 0
                or resources._stream_row(stream.ticket)['domain'] is not domain
                or owner.snapshot()['launch_attempted']):
            raise ProducerResourceError('producer_resource_binding')
        stream._check()
        self.resources, self.owner, self.domain = resources, owner, domain
        self.allocation, self.stream = allocation, stream
        self._claimed = self._busy = self._unknown = False
        self._pending = self._primary = self._recovery_error = None
        self._probe = self._last_probe_receipt = None
        self._unfunded_probe_bytes = 0
        self._resource_cleanup_faults = ()

    def claim(self):
        self.resources._check()
        if self._claimed or self._unknown or self.owner.snapshot()['launch_attempted']:
            raise ProducerResourceError('producer_resource_consumed')
        self.resources._lookup(self.resources._memory, self.allocation)
        self._claimed = True

    def room(self):
        self.resources._check()
        self.resources._lookup(self.resources._memory, self.allocation)
        if self._unknown or self._busy:
            raise ProducerResourceError('producer_resource_unknown')
        state = self.stream.snapshot()
        if state['pending'] >= 2:
            return 0
        available = (state['maximum'] - state['known'] - state['promised']
                     - state['future_reserved'] + self.resources.snapshot()['traffic_available'])
        return min(512, available)

    def _receipt(self):
        promise, before, name, maximum, known = self._pending
        receipt = self.owner.io_receipt()
        generation = 1 if before is None else before[0] + 1
        if (receipt is None or receipt[0] != generation or receipt[1] != name
                or receipt[2] != maximum):
            raise ProducerResourceError('producer_io_receipt')
        return receipt

    def can_probe_eof(self):
        """Only exhausted credit, never occupied promises or protected replies."""
        if self.room() != 0:
            return False
        state = self.stream.snapshot()
        return (state['pending'] == 0 and state['future_reserved'] == 0
                and state['maximum'] == state['known']
                and self.resources.snapshot()['traffic_available'] == 0)

    def _capture_probe(self):
        before, name = self._probe
        receipt = self.owner.io_receipt()
        if receipt == before:
            return None  # Queue/readiness observation only, no new read.
        if (receipt is None or receipt[0] != (1 if before is None else before[0] + 1)
                or receipt[1:3] != (name, 1)):
            raise ProducerResourceError('producer_probe_receipt')
        self._last_probe_receipt = receipt
        if type(receipt[4]) is int and receipt[4] > 0:
            # A violated EOF-only contract is not funded traffic. Retain the
            # actual excess explicitly; never grow a quote or report success.
            self._unfunded_probe_bytes = receipt[4]
        return receipt

    def _probe_eof(self, name, scratch):
        try:
            try:
                self._busy = True
                self._probe = (self.owner.io_receipt(), name)
                outcome = self.owner.probe_output_eof(name, scratch)
                receipt = self._capture_probe()
                if receipt is not None and (receipt[5] or receipt[4] != 0):
                    raise ProducerResourceError('producer_probe_unknown')
                if outcome == 'data':
                    raise ProducerResourceError('producer_traffic_exhausted')
                if outcome not in ('pending', 'eof'):
                    raise ProducerResourceError('producer_probe_unknown')
                if outcome == 'eof' and not self.owner.snapshot()[name + '_eof']:
                    raise ProducerResourceError('producer_probe_unknown')
                self._probe = None
                return 0 if outcome == 'eof' else -1
            finally:
                self._busy = False
        except BaseException as error:
            self.poison(error)
            raise

    def poison(self, error):
        self._unknown = True
        if self._primary is None:
            self._primary = type(error).__name__
        try:
            if self._probe is not None:
                self._capture_probe()
            if self._pending is not None:
                promise, before, name, maximum, known = self._pending
                state = self.stream.snapshot()
                if not state['unknown'] and state['known'] == known:
                    receipt = self._receipt()
                    if type(receipt[4]) is int and 0 <= receipt[4] <= maximum:
                        self.stream.unknown_prefix(promise, receipt[4])
            self.stream.quarantine()
        except BaseException as recovery:
            self._recovery_error = type(recovery).__name__
        finally:
            self._resource_cleanup_faults = tuple(sorted(set(
                self._resource_cleanup_faults + freeze_resource(self.resources))))
        return self._resource_cleanup_faults

    def _io(self, name, offered):
        if not self._claimed or self._busy or self._unknown:
            raise ProducerResourceError('producer_resource_unavailable')
        if (type(offered) is not memoryview or offered.ndim != 1 or offered.format != 'B'
                or not offered.c_contiguous or not 0 < len(offered) <= 512
                or name != 'stdin' and offered.readonly):
            raise ProducerResourceError('producer_io_buffer')
        maximum = min(len(offered), self.room())
        if maximum == 0:
            if name == 'stdin':
                raise BlockingIOError('producer_credit_parked')
            if self.can_probe_eof():
                return self._probe_eof(name, offered)
            return -1
        try:
            try:
                self._busy = True
                promise = self.stream.promise_stdio(maximum)
                self._pending = (promise, self.owner.io_receipt(), name, maximum,
                                 self.stream.snapshot()['known'])
                blocked = False
                with offered[:maximum] as loan:
                    if name == 'stdin':
                        try:
                            count = self.owner.write(loan)
                        except BlockingIOError:
                            count, blocked = 0, True
                    else:
                        count = self.owner.read_into(name, loan)
                        blocked = count == -1
                    receipt = self._receipt()
                    observed = 0 if blocked else count
                    # Prior known EOF is an unattempted receipt, never a syscall.
                    prior_eof = (name != 'stdin' and count == 0 and not receipt[3]
                                 and receipt[4] is None)
                    if (receipt[5] or type(observed) is not int or not 0 <= observed <= maximum
                            or not prior_eof and receipt[4] != observed):
                        raise ProducerResourceError('producer_io_unknown')
                    self.stream.known(promise, observed)
                    self._pending = None
                if blocked and name == 'stdin':
                    return None
                return count
            finally:
                self._busy = False
        except BaseException as error:
            self.poison(error)
            raise

    def write(self, offered):
        count = self._io('stdin', offered)
        if count is None:
            raise BlockingIOError('producer_write_pending')
        return count

    def read_into(self, name, offered):
        if name not in ('stdout', 'stderr'):
            raise ProducerResourceError('producer_output_name')
        return self._io(name, offered)

    def snapshot(self):
        return dict(claimed=self._claimed,
                    unknown=self._unknown or self.resources.snapshot()['unknown'],
                    primary=self._primary, recovery_error=self._recovery_error,
                    resource_cleanup_faults=self._resource_cleanup_faults,
                    io_pending=self._pending is not None,
                    probe_pending=self._probe is not None,
                    last_probe_receipt=self._last_probe_receipt,
                    unfunded_probe_bytes=self._unfunded_probe_bytes,
                    qualification='original_ledger_binding_not_admission')
