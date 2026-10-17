"""Fixed ordinary owned test process; no SDK or provider access."""
import os
import sys
import signal
import fcntl
import stat

if sys.argv[1] == 'empty':
    raise SystemExit(0 if len(sys.argv) == 3 and sys.argv[2] == '' else 11)
if sys.argv[1] == 'fds':
    for fd in (0, 1, 2):
        if not stat.S_ISSOCK(os.fstat(fd).st_mode) or fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
            raise SystemExit(13)
    for fd in range(3, 64):
        try:
            os.fstat(fd)
        except OSError as error:
            if error.errno != 9:
                raise
        else:
            raise SystemExit(14)
    os.write(1, b'stdio-only')
    raise SystemExit(0)
if sys.argv[1] == 'eofwait':
    os.close(1)
    os.close(2)
    while os.read(0, 512):
        pass
    raise SystemExit(0)
if sys.argv[1] == 'exit':
    os.write(1, b'out')
    os.write(2, b'err')
    raise SystemExit(7)
if sys.argv[1] == 'echo':
    while True:
        body = os.read(0, 512)
        if not body:
            break
        os.write(1, body)
    os.write(2, b'done')
    raise SystemExit(0)
if sys.argv[1] == 'wait':
    os.write(1, b'ready')
    while os.read(0, 512):
        pass
    raise SystemExit(0)
if sys.argv[1] == 'block':
    os.write(1, b'ready')
    signal.pause()
    raise SystemExit(12)
raise SystemExit(64)
