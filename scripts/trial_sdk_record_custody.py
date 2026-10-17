"""Internal record-loan custody shared by parent transports.

The caller retains its public guard, admission checks, mutation marker and all
state. These functions neither create owners/backings/credits nor settle them.
Keep the original boundary context until its exact receipt has been consumed.
"""
from trial_sdk_resource_failure import annotate_resource_failure


def _recover_prefix(self, pending, direction, before, primary):
    try:
        self._socket_prefix(pending, direction, before)
    except BaseException as recovery:
        # Preserve the actual edge failure for the caller's outer guard. Keep
        # the original _io and promise held; failure is not a zero-byte receipt.
        self._io_recovery_error = type(recovery).__name__
        annotate_resource_failure(primary, ('record_prefix_recovery_unknown',))


def send_step(self):
    pending = self._tx
    token = pending['token']
    def send(frame):
        with frame.record as record:
            return self._connection.send(record)
    before = self._connection.snapshot()['last_transfer']
    self._io = (pending, 'send', before, pending['credit'].snapshot()['known'])
    try:
        count = token.visit(send)
    except BaseException as primary:
        _recover_prefix(self, pending, 'send', before, primary)
        raise
    if count == -1:
        self._io = None
        return False  # The identical token/sequence/promise remains held.
    pending['credit'].known(pending['promise'], count)
    self._io = None
    token.visit(self.transcript.observe)
    token.complete(token.generation, 'known')
    pending['body'].close()
    self._tx = None
    return True


def receive_step(self, consumer, error_type):
    pending = self._rx
    if pending['hold'] is None:
        state = pending['credit'].burst_info(pending['burst'])
        if state['index'] >= len(state['maxima']):
            raise error_type('service_burst_complete')
        pending['hold'] = self._rx_region.hold(state['maxima'][state['index']])
    before = self._connection.snapshot()['last_transfer']
    self._io = (pending, 'receive', before, pending['credit'].snapshot()['known'])
    try:
        count = self._connection.receive_into(pending['hold'].view)
    except BaseException as primary:
        _recover_prefix(self, pending, 'receive', before, primary)
        raise
    if count == -1:
        self._io = None
        return False
    # Charge the actual edge even if framing/protocol/consumer later refuses.
    if 'burst' in pending:
        pending['credit'].burst_known(pending['burst'], count)
    else:
        pending['credit'].known(pending['promise'], count)
    self._io = None
    pending['hold'].close()
    pending['hold'] = self._rx_region.hold(count)
    token = self._receiver.decode(pending['hold'])
    token.visit(self.transcript.observe)
    token.visit(consumer)
    token.complete(token.generation, 'known')
    if 'burst' in pending:
        pending['hold'] = None
    else:
        self._rx = None
    return True


def socket_prefix(self, pending, direction, before):
    receipt = self._connection.snapshot()['last_transfer']
    if (receipt is not None and (before is None or receipt[0] > before[0])
            and receipt[1] == direction and type(receipt[3]) is int):
        if 'burst' in pending:
            pending['credit'].burst_unknown_prefix(pending['burst'], receipt[3])
        else:
            pending['credit'].unknown_prefix(pending['promise'], receipt[3])


def reconcile_interrupted_transfer(self):
    if self._io is not None:
        pending, direction, before, known = self._io
        credit = pending['credit']
        if not credit.snapshot()['unknown'] and credit.snapshot()['known'] == known:
            try:
                self._socket_prefix(pending, direction, before)
            except BaseException as exc:
                # Keep the original failure. The shared freeze below retains
                # all unresolved commitments even if recovery is interrupted.
                self._io_recovery_error = type(exc).__name__
                self.resources.freeze()
