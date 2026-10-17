"""Exclusive nonblocking stdin descriptor ownership; no launch or authentication."""
import errno
import fcntl
import os
import stat


class NativeInputError(ValueError):
    pass


class NativeInputRejected(NativeInputError):
    """A selected command's stdin rejected this write with known zero progress."""


class NativeInput:
    def __init__(self, fd, *, allow_epipe=False):
        # Validation borrows fd. Only successful construction transfers custody.
        if type(allow_epipe) is not bool:
            raise NativeInputError('native_input_configuration')
        if type(fd) is not int or fd < 0:
            raise NativeInputError('native_input_descriptor')
        mode = os.fstat(fd).st_mode
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        if (not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode))
                or not flags & os.O_NONBLOCK
                or flags & os.O_ACCMODE == os.O_RDONLY
                or os.get_inheritable(fd)):
            raise NativeInputError('native_input_configuration')
        self._fd = fd
        self._busy = self._closed_known = self._close_unknown = False
        self._write_unknown = False
        self._allow_epipe = allow_epipe
        self._input_rejected = False
        self._written = 0
        self._creator = os.getpid()
        self._receipt = None

    def write_receipt(self):
        """Latest immutable syscall observation; no FD probes or accounting."""
        return self._receipt

    def fileno(self):
        if self._fd is None:
            raise NativeInputError('native_input_detached')
        return self._fd  # Borrow for readiness only, never duplicate/close/rebind.

    def snapshot(self):
        return dict(known_written=self._written, closed_known=self._closed_known,
                    close_unknown=self._close_unknown,
                    write_unknown=self._write_unknown,
                    input_rejected=self._input_rejected,
                    qualification='unqualified')

    def write(self, body):
        if os.getpid() != self._creator or self._busy:
            raise NativeInputError('native_input_busy')
        if self._receipt is not None and self._receipt[0] == 2**53-1:
            raise NativeInputError('native_input_sequence')
        loan = None
        acquired = attempted = False
        known_error = None
        count = None
        try:
            try:
                self._busy = acquired = True
                self._receipt = (1 if self._receipt is None else self._receipt[0]+1,
                                 'stdin', None, False, None, False)
                if self._input_rejected and not self._write_unknown:
                    raise NativeInputRejected('native_input_rejected')
                if self._fd is None or self._write_unknown:
                    raise NativeInputError('native_input_unusable')
                if type(body) is bytes:
                    loan = memoryview(body)
                elif (type(body) is memoryview and body.readonly and body.ndim == 1
                      and body.format == 'B' and body.c_contiguous):
                    loan = memoryview(body)
                else:
                    raise NativeInputError('native_input_body')
                if not 0 < len(loan) <= 512:
                    raise NativeInputError('native_input_chunk')
                self._receipt = (*self._receipt[:2], len(loan), True, None, False)
                attempted = True
                if self._allow_epipe:
                    self._write_unknown = True
                try:
                    count = os.write(self._fd, loan)
                except BlockingIOError as error:
                    count = 0
                    self._receipt = (*self._receipt[:4], count, False)
                    known_error = error
                    if self._allow_epipe:
                        self._write_unknown = False
                    raise  # This exact unbuffered syscall accepted zero bytes.
                except BrokenPipeError as error:
                    self._write_unknown = True
                    if self._allow_epipe and error.errno == errno.EPIPE:
                        # This syscall accepted no new bytes. Earlier writes stay counted.
                        self._input_rejected = True
                        count = 0
                        self._receipt = (*self._receipt[:4], count, False)
                        known_error = NativeInputRejected('native_input_rejected')
                        self._write_unknown = False
                        raise known_error from None
                    raise
                except BaseException:
                    self._write_unknown = True
                    raise
                if type(count) is not int or not 0 <= count <= len(loan):
                    count = None
                    raise NativeInputError('native_input_count')
                self._receipt = (*self._receipt[:4], count, False)
                self._written += count
                if self._allow_epipe:
                    self._write_unknown = False
            finally:
                if loan is not None:
                    loan.release()
                body = None
                if acquired:
                    self._busy = False
            return count
        except BaseException as error:
            # Include final cleanup, not just the syscall/accounting block. Only
            # the exact observed zero-progress exception is a known error return.
            if attempted and error is not known_error:
                self._write_unknown = True
                known = count if type(count) is int and 0 <= count <= self._receipt[2] else self._receipt[4]
                self._receipt = (*self._receipt[:4], known, True)
            body = None
            if acquired:
                self._busy = False
            raise

    def close(self):
        if self._busy:
            raise NativeInputError('native_input_busy')
        if self._fd is None:
            if self._close_unknown:
                raise NativeInputError('native_input_close_unknown')
            return
        self._busy = True
        fd, self._fd = self._fd, None  # Detach BEFORE a possibly uncertain close.
        try:
            os.close(fd)
        except BaseException:
            self._close_unknown = True
            raise
        else:
            self._closed_known = True
        finally:
            self._busy = False
