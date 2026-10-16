"""Decoded fragment custody, whole-line admission, then actual native writes.

Custody receipts are NOT native-write receipts. Original spelling is decoded
working text, not a raw line buffer; the attempt's aggregate limit still applies.
The transport owns its incoming <=512-byte raw loan until offer returns.
"""
from trial_sdk_forwarding import OriginalLine
from trial_sdk_incoming import IncomingError, LineAssembler, MAX_DOCUMENT_BYTES


class CustodyError(ValueError):
    pass


class InputCustody:
    def __init__(self, validate, reserve, release, max_bytes=MAX_DOCUMENT_BYTES):
        if (not all(callable(f) for f in (validate, reserve, release))
                or type(max_bytes) is not int or not 0 < max_bytes <= MAX_DOCUMENT_BYTES):
            raise CustodyError('custody_argument')
        self._validate, self._reserve, self._release = validate, reserve, release
        self._max = max_bytes
        self._line = None
        self._busy = self._ended = self._unknown = self._cleanup_unknown = False
        self._custodied = self._validated = self._written = 0
        self._state = 'collecting'
        self._scratch_credit = 0
        # Existing shared raw credit, not a fresh allowance: <=15 bytes of
        # decoder carry/head/pending temporaries, conservatively reserved16.
        granted = reserve(16)
        if type(granted) is not bool or not granted:
            raise CustodyError('custody_scratch_credit')
        self._scratch_credit = 16
        try:
            self._assembler = LineAssembler(max_bytes)
        except BaseException:
            self._discard('failed')
            raise

    def snapshot(self):
        line = None if self._line is None else self._line.snapshot()
        return dict(state=self._state, custodied_bytes=self._custodied,
                    validated_bytes=self._validated, known_native_written_bytes=self._written,
                    native_outcome_unknown=self._unknown, cleanup_unknown=self._cleanup_unknown,
                    raw_reserved_bytes=self._scratch_credit + (0 if line is None else line['reserved_bytes']),
                    partial_original_bytes=0 if self._assembler is None else self._assembler.pending_bytes)

    def _usable(self):
        if self._busy:
            raise CustodyError('custody_busy')
        if self._state in ('failed', 'cancelled', 'drained'):
            raise CustodyError('custody_unusable')

    def _discard(self, state):
        self._state = state
        line, self._line = self._line, None
        self._assembler = None
        error = None
        if line is not None:
            try:
                line.cancel()
            except BaseException as fault:
                error = fault
                self._cleanup_unknown = True
            try:
                self._cleanup_unknown |= line.snapshot()['cleanup_unknown']
            except BaseException as fault:
                if error is None:
                    error = fault
                self._cleanup_unknown = True
        credit, self._scratch_credit = self._scratch_credit, 0
        if credit:
            try:
                self._release(credit)
            except BaseException as fault:
                if error is None:
                    error = fault
                self._cleanup_unknown = True
        return error

    def offer(self, fragment, first_byte_ns):
        """Acknowledge decoded custody through at most the first LF, never I/O."""
        loan = prefix = None
        decode_failure = None
        acquired = False
        try:
            self._usable()
            if self._ended:
                raise CustodyError('custody_input_ended')
            if self._state == 'draining':
                return {'custodied': 0}
            self._busy = acquired = True
            if type(fragment) is bytes:
                loan = memoryview(fragment)
            elif (type(fragment) is memoryview and fragment.readonly
                  and fragment.ndim == 1 and fragment.itemsize == 1
                  and fragment.contiguous and fragment.format in ('B', 'b', 'c')):
                loan = fragment.cast('B')
            else:
                raise CustodyError('custody_fragment')
            if not 0 < len(loan) <= 512:
                raise CustodyError('custody_fragment')
            width = next((i + 1 for i, byte in enumerate(loan) if byte == 10), len(loan))
            prefix = loan[:width]
            complete = None
            for complete in self._assembler.feed(prefix, first_byte_ns):
                pass  # Exhaust/release the raw feed before semantic validation.
            if complete is not None:
                self._line = OriginalLine(complete, self._reserve, self._release, self._max)
                document = self._line.take_document()
                if self._validate(document.take(), document.metadata()) is not True:
                    raise CustodyError('custody_refused')
                self._validated += complete.raw_bytes + 1
                self._state = 'draining'
            self._custodied += width
            return {'custodied': width}
        except IncomingError as error:
            # Do not export a decoder traceback holding raw segment views/head
            # copies after their credit is returned. Raise only its fixed code
            # after leaving this exception context and releasing our raw loans.
            decode_failure = error.code
            if acquired:
                self._discard('failed')
        except BaseException:
            if acquired:
                self._discard('failed')  # Preserve the active outer call on reentry.
            raise
        finally:
            if prefix is not None:
                prefix.release()
            if loan is not None:
                loan.release()
            fragment = None
            if acquired:
                self._busy = False
        if decode_failure is not None:
            raise CustodyError(decode_failure) from None

    def write_to(self, writer):
        self._usable()
        if not callable(writer):
            raise CustodyError('custody_writer')
        if self._line is None:
            return 0
        self._busy = True
        try:
            line = self._line
            before = line.snapshot()['acknowledged_bytes']
            attempted = known = False
            def actual_write(view):
                nonlocal attempted, known
                attempted, known = True, False
                try:
                    count = writer(view)
                except BlockingIOError as error:
                    known = not getattr(error, 'characters_written', 0)
                    raise
                known = type(count) is int and 0 <= count <= len(view)
                return count
            try:
                count = line.write_to(actual_write)
            except BaseException:
                self._unknown |= attempted and not known
                self._discard('failed')
                raise
            finally:
                snap = line.snapshot()
                self._written += snap['acknowledged_bytes'] - before
                self._cleanup_unknown |= snap['cleanup_unknown']
            if snap['state'] == 'complete':
                self._line = None
                if self._ended:
                    error = self._discard('drained')
                    if error is not None:
                        self._state = 'failed'
                        raise error
                else:
                    self._state = 'collecting'
            return count
        except BaseException:
            self._discard('failed')
            raise
        finally:
            self._busy = False

    def end_input(self):
        self._usable()
        self._busy = True
        try:
            self._assembler.finish()
            self._ended = True
            if self._line is None:
                error = self._discard('drained')
                if error is not None:
                    raise error
        except BaseException:
            self._discard('failed')
            raise
        finally:
            self._busy = False

    def cancel(self):
        if self._busy:
            raise CustodyError('custody_busy')
        self._busy = True
        try:
            state = self._state if self._state in ('failed', 'cancelled', 'drained') else 'cancelled'
            error = self._discard(state)
            if error is not None:
                raise error
        finally:
            self._busy = False
