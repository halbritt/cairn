"""Profile2 receiver assembly; transport and actual descriptor provenance external."""
from trial_sdk_input_custody import InputCustody


class ReceiverError(ValueError):
    pass


class InputReceiver:
    def __init__(self, validate, reserve, release, writer, closer, maximum=3*1024*1024):
        if (not all(callable(f) for f in (validate, reserve, release, writer, closer))
                or type(maximum) is not int or not 0 < maximum <= 3*1024*1024):
            raise ReceiverError('receiver_configuration')
        self._writer, self._closer, self._maximum = writer, closer, maximum
        self._state, self._reply, self._end = 'open', None, None
        self._last = 0
        self._busy = self._paused = self._close_attempted = False
        self._closed_known = self._close_unknown = self._cleanup_unknown = False
        self._primary = None
        self._transport_unknown = False
        self._custody = InputCustody(validate, reserve, release)

    @property
    def reply(self):
        return self._reply  # Immutable scalar metadata, not a raw body or proof.

    def snapshot(self):
        snap = self._custody.snapshot()
        return dict(snap, custody_state=snap['state'], state=self._state, reply=self._reply,
                    paused=self._paused, primary=self._primary,
                    closed_known=self._closed_known, close_unknown=self._close_unknown,
                    transport_outcome_unknown=self._transport_unknown,
                    receiver_cleanup_unknown=self._cleanup_unknown,
                    qualification='unqualified')

    def _usable(self):
        if self._busy:
            raise ReceiverError('receiver_busy')
        if self._state in ('failed', 'cancelled', 'closed'):
            raise ReceiverError('receiver_unusable')

    def _close_once(self):
        if self._close_attempted:
            return
        self._close_attempted = True
        try:
            answer = self._closer()
            if answer is not None:
                raise ReceiverError('receiver_close_unknown')
        except BaseException:
            self._close_unknown = True
            raise
        self._closed_known = True

    def _abort(self, primary):
        if self._primary is None:
            self._primary = primary
        self._state, self._reply = 'failed', None
        try:
            self._custody.cancel()
        except BaseException:
            self._cleanup_unknown = True
        try:
            self._close_once()
        except BaseException:
            self._cleanup_unknown = True

    def receive(self, kind, operation, body, first_byte_ns):
        loan = None
        acquired = False
        try:
            self._usable()
            if self._reply is not None:
                raise ReceiverError('receiver_reply_pending')
            self._busy = acquired = True
            if self._state != 'open' or self._paused:
                raise ReceiverError('receiver_input_order')
            if (type(kind) is not int or kind not in (9, 5)
                    or type(operation) is not int or not self._last < operation < 2**53
                    or type(first_byte_ns) is not int or first_byte_ns < 0):
                raise ReceiverError('receiver_request')
            if type(body) is bytes:
                loan = memoryview(body)
            elif (type(body) is memoryview and body.readonly and body.ndim == 1
                  and body.format == 'B' and body.c_contiguous):
                loan = memoryview(body)
            else:
                raise ReceiverError('receiver_body')
            self._last = operation
            if kind == 9:
                remaining = self._maximum - self._custody.snapshot()['custodied_bytes']
                if not 0 < len(loan) <= min(512, remaining):
                    raise ReceiverError('receiver_offer_budget')
                n = self._custody.offer(loan, first_byte_ns)['custodied']
                self._paused = n == 0
                self._reply = (140, operation, n)
            else:
                if len(loan):
                    raise ReceiverError('receiver_end_body')
                self._custody.end_input()
                self._state, self._end = 'ending', operation
        except BaseException:
            if acquired:
                self._abort('receiver_input_failed')
            raise
        finally:
            if loan is not None:
                loan.release()
            body = None
            if acquired:
                self._busy = False

    def advance(self):
        self._usable()
        if self._reply is not None:
            return 0
        self._busy = True
        try:
            count = (self._custody.write_to(self._writer)
                     if self._custody.snapshot()['state'] == 'draining' else 0)
            snap = self._custody.snapshot()
            if self._state == 'ending' and snap['state'] == 'drained':
                if (snap['native_outcome_unknown'] or snap['cleanup_unknown']
                        or snap['known_native_written_bytes'] != snap['custodied_bytes']
                        or snap['validated_bytes'] != snap['custodied_bytes']):
                    raise ReceiverError('receiver_drain_accounting')
                self._close_once()
                self._reply = (133, self._end, snap['known_native_written_bytes'])
            elif self._paused and snap['state'] == 'collecting':
                self._reply = (132, 0, None)
            return count
        except BaseException:
            self._abort('receiver_advance_failed')
            raise
        finally:
            self._busy = False

    def reply_sent(self):
        self._usable()
        if self._reply is None:
            raise ReceiverError('receiver_reply_missing')
        kind = self._reply[0]
        self._reply = None
        if kind == 132:
            self._paused = False
        elif kind == 133:
            self._state = 'closed'

    def cancel(self):
        if self._busy:
            raise ReceiverError('receiver_busy')
        self._busy = True
        try:
            if self._state not in ('failed', 'cancelled', 'closed'):
                self._state = 'cancelled'
            self._reply = None
            error = None
            try:
                self._custody.cancel()
            except BaseException as fault:
                error = fault
                self._cleanup_unknown = True
            try:
                self._close_once()
            except BaseException as fault:
                if error is None:
                    error = fault
                self._cleanup_unknown = True
            if error is not None:
                self._state = 'failed'
                if self._primary is None:
                    self._primary = 'receiver_cancel_failed'
                raise error
        finally:
            self._busy = False

    def transport_unknown(self):
        self._usable()
        self._busy = True
        try:
            self._transport_unknown = True
            self._abort('receiver_transport_unknown')
            raise ReceiverError('receiver_transport_unknown')
        finally:
            self._busy = False
