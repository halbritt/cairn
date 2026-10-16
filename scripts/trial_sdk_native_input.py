"""Exclusive nonblocking stdin descriptor ownership; no launch or authentication."""
import fcntl
import os
import stat


class NativeInputError(ValueError):
    pass


class NativeInput:
    def __init__(self, fd):
        # Validation borrows fd. Only successful construction transfers custody.
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
        self._written = 0

    def fileno(self):
        if self._fd is None:
            raise NativeInputError('native_input_detached')
        return self._fd  # Borrow for readiness only, never duplicate/close/rebind.

    def snapshot(self):
        return dict(known_written=self._written, closed_known=self._closed_known,
                    close_unknown=self._close_unknown,
                    write_unknown=self._write_unknown,
                    qualification='unqualified')

    def write(self, body):
        loan = None
        acquired = False
        try:
            if self._busy:
                raise NativeInputError('native_input_busy')
            if self._fd is None or self._write_unknown:
                raise NativeInputError('native_input_unusable')
            self._busy = acquired = True
            if type(body) is bytes:
                loan = memoryview(body)
            elif (type(body) is memoryview and body.readonly and body.ndim == 1
                  and body.format == 'B' and body.c_contiguous):
                loan = memoryview(body)
            else:
                raise NativeInputError('native_input_body')
            if not 0 < len(loan) <= 512:
                raise NativeInputError('native_input_chunk')
            try:
                count = os.write(self._fd, loan)
            except BlockingIOError:
                raise  # Unbuffered syscall: would-block accepted zero bytes.
            except BaseException:
                self._write_unknown = True
                raise
            self._written += count
            return count
        finally:
            if loan is not None:
                loan.release()
            body = None
            if acquired:
                self._busy = False

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
