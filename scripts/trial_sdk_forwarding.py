"""One validated original JSON line; no transport, protocol writer or authority.

Decoded text and the exclusive parsed graph are bounded working representations.
Only one <=512-byte encoded chunk is held. The caller owns aggregate credit and
must keep all input reservations until LineAssembler.feed is exhausted/closed.
"""
from trial_sdk_incoming import MAX_DOCUMENT_BYTES, validate_line


CHUNK_CHARACTERS = 128  # At most 512 UTF-8 bytes, without splitting a scalar.


class ForwardingError(ValueError):
    pass


class OriginalLine:
    """Transfer a trusted DecodedLine reference and validate its graph once.

    Caller relinquishes line (immutable aliases are not copies). take_document
    transfers the OwnedJSON token once, independently of forwarding/cancellation.
    reserve(n) must return exact bool: True owns n credits; False owns none.
    release(n) returns them once; failure records cleanup_unknown, never retries.
    These synchronous accounting callbacks must not reenter this object.

    write_to uses a synchronous non-retaining writer(view)->accepted byte count.
    The read-only view is borrowed only for that call. Any retained/derived view
    or writer copy requires separate caller-owned credit and lifetime. Exceptions
    must mean no accepted bytes; uncertain writes terminate, never replay.
    """

    def __init__(self, line, reserve, release, max_bytes=MAX_DOCUMENT_BYTES):
        self._document = validate_line(line, max_bytes)
        self._text = line.text
        self._metadata = self._document.metadata()
        self._reserve, self._release = reserve, release
        self._cursor = self._offset = self._credit = self._written = 0
        self._pending = None
        self._lf = False
        self._busy = False
        self._state = 'open'
        self._failure = None
        self._cleanup_unknown = False

    def snapshot(self):
        return dict(self._metadata, state=self._state, acknowledged_bytes=self._written,
                    pending_bytes=0 if self._pending is None else len(self._pending) - self._offset,
                    reserved_bytes=self._credit, text_retained=self._text is not None,
                    failure=self._failure, cleanup_unknown=self._cleanup_unknown)

    def take_document(self):
        if self._busy or self._document is None:
            raise ForwardingError('forward_document_unavailable')
        document, self._document = self._document, None
        return document

    def _drop_chunk(self):
        self._pending = None
        self._offset = 0
        credit, self._credit = self._credit, 0
        if credit:
            try:
                self._release(credit)
            except BaseException:
                self._cleanup_unknown = True
                raise

    def _stop(self, state, failure=None):
        self._state, self._failure = state, failure
        self._text = self._document = None
        self._drop_chunk()

    def cancel(self):
        if self._busy:
            raise ForwardingError('forward_busy')
        if self._state == 'open':
            self._stop('cancelled')
        elif self._state == 'complete':
            self._document = None

    def _prepare(self):
        if self._pending is not None:
            return True
        self._lf = self._cursor == len(self._text)
        piece = '\n' if self._lf else self._text[self._cursor:self._cursor + CHUNK_CHARACTERS]
        size = sum(1 if ord(c) < 0x80 else 2 if ord(c) < 0x800
                   else 3 if ord(c) < 0x10000 else 4 for c in piece)
        granted = self._reserve(size)
        if type(granted) is not bool:
            raise ForwardingError('forward_credit_contract')
        if not granted:
            return False
        self._credit = size  # Account BEFORE encode, including its failure path.
        self._pending = piece.encode('utf-8')
        if not self._lf:
            self._cursor += len(piece)
        return True

    def write_to(self, writer):
        """One bounded write attempt. Zero means backpressure/short credit.

        Completion is snapshot()['state']=='complete', only after LF acceptance.
        BlockingIOError without partial progress is backpressure. Other errors
        close this line, release owned storage, and preserve the original error.
        No generator, retry loop, hidden transport or fabricated delivery exists.
        """
        if self._busy or self._state != 'open':
            raise ForwardingError('forward_unusable')
        self._busy = True
        try:
            if not self._prepare():
                return 0
            view = memoryview(self._pending)[self._offset:]
            try:
                try:
                    count = writer(view)
                except BlockingIOError as error:
                    if getattr(error, 'characters_written', 0):
                        raise
                    return 0
                if type(count) is not int or not 0 <= count <= len(view):
                    raise ForwardingError('forward_write_count')
            finally:
                view.release()
            self._offset += count
            self._written += count
            if self._offset == len(self._pending):
                self._drop_chunk()
                if self._lf:
                    self._state = 'complete'
                    self._text = None
            return count
        except BaseException:
            # Fixed primary classification; caller receives original exception.
            # A release fault must not replace it, nor trigger another release.
            try:
                self._stop('failed', 'forward_operation_failed')
            except BaseException:
                pass  # _drop_chunk retained cleanup_unknown before raising.
            raise
        finally:
            self._busy = False
