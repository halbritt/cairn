"""Explicit profile-2 input semantics; framing and provenance are external."""
LIMIT = 3 * 1024 * 1024
MAX_OP = 2**53 - 1


class ProfileError(ValueError):
    pass


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def receipt(body, width):
    view = None
    try:
        if type(body) is bytes:
            view = memoryview(body)
        elif (type(body) is memoryview and body.readonly and body.ndim == 1
              and body.itemsize == 1 and body.contiguous and body.format == 'B'):
            view = memoryview(body)
        else:
            raise ProfileError('receipt_type')
        if len(view) != width:
            raise ProfileError('receipt_width')
        if width == 2:
            return (view[0] << 8) | view[1]
        return view[0], ((view[1] << 24) | (view[2] << 16) | (view[3] << 8) | view[4])
    finally:
        if view is not None:
            view.release()
        body = None


def parse_custody(body):
    try:
        return receipt(body, 2)
    finally:
        body = None


def parse_end(body):
    try:
        status, total = receipt(body, 5)
        if status != 1:
            raise ProfileError('close_status')
        return total
    finally:
        body = None


class InputClient:
    def __init__(self, profile, maximum):
        if not integer(profile, 2, 2) or not integer(maximum, 1, LIMIT):
            raise ProfileError('configuration')
        self._maximum = maximum
        self._state = 'ready'
        self._pending = None
        self._last = self._custody = 0
        self._native = None
        self._unknown = False

    def snapshot(self):
        return dict(state=self._state, pending=self._pending,
                    custodied_total=self._custody, known_native_total=self._native,
                    unknown=self._unknown)

    def _fail(self, code):
        self._state = 'failed'
        raise ProfileError(code)

    def _begin(self, op, kind, offered):
        if self._state != 'ready' or self._pending is not None:
            self._fail('order')
        if not integer(op, 1, MAX_OP) or op <= self._last:
            self._fail('operation')
        self._last = op
        self._pending = (op, kind, offered)
        self._state = 'pending'

    def begin_custody(self, op, offered):
        if not integer(offered, 1, 512) or offered > self._maximum - self._custody:
            self._fail('budget_or_offer')
        self._begin(op, 140, offered)

    def begin_end(self, op):
        self._begin(op, 133, 0)

    def result(self, op, kind, body):
        try:
            if (self._state != 'pending' or self._pending is None
                    or not integer(op, 1, MAX_OP) or not integer(kind, 0, 255)
                    or (op, kind) != self._pending[:2]):
                self._fail('correlation')
            if kind == 140:
                count = parse_custody(body)
                if count > self._pending[2]:
                    self._fail('count')
                self._custody += count
                self._state = 'paused' if count == 0 else 'ready'
            else:
                total = parse_end(body)
                if total != self._custody:
                    self._fail('close_total')
                self._native = total
                self._state = 'closed'
            self._pending = None
        except BaseException:
            self._state = 'failed'
            raise
        finally:
            body = None

    def writable(self):
        if self._state != 'paused' or self._pending is not None:
            self._fail('writable_order')
        self._state = 'ready'

    def transport_unknown(self):
        if self._state not in ('ready', 'pending', 'paused'):
            self._fail('unknown_order')
        self._unknown = True
        self._fail('transport_unknown')
