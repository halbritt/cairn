"""Fixed disposable composition worker; no actor/provider/argv extension."""
import os
import sys
import time

from selected_native.retention import Intent
from trial_coding_stream import CodingStream
from trial_coding_sink import CodingSink, SinkError, serve
from trial_worker_protocol import ProtocolError
from selected_native.retention import RetentionError


def main():
    role, mode, run, source, intent_sha256 = sys.argv[1:]
    if role != 'composition' or mode not in ('normal', 'nonzero', 'hang'):
        return 36
    root_fd = os.open('/output', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    sink = None
    try:
        stream = CodingStream('T1', run, 'fixture-model', 'fixture-cli', workspace=None,
                              required_commands={'make_check':'make check'}, prompt='new task', input_charge=700)
        sink = CodingSink(root_fd, Intent(run,'a'*64,'b'*64,'c'*64,source),stream,intent_sha256)
        if mode == 'hang':
            from trial_worker_protocol import encoded
            sys.stdout.buffer.write(encoded(sink.ready())+b'\n');sys.stdout.buffer.flush()
            time.sleep(1000)
            return 36
        serve(sink,sys.stdin.buffer,sys.stdout.buffer)
        return 29 if mode == 'nonzero' else 0
    except (SinkError, RetentionError, ProtocolError, OSError, ValueError):
        # Only the fixed exit is public, never private traceback or raw input.
        return 36
    finally:
        if sink is not None:
            sink.close()
        os.close(root_fd)


if __name__ == '__main__':
    sys.exit(main())
