"""Trusted disposable worker roles; all blocking I/O belongs to an external owner."""
import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time


def emit(value):
    data = json.dumps(value, sort_keys=True, separators=(',', ':')).encode()+b'\n'
    if len(data) > 8192:
        raise ValueError('control_bounds')
    os.write(1, data)


def exact(count):
    out = bytearray()
    while len(out) < count:
        block = os.read(0, count-len(out))
        if not block:
            raise ValueError('protocol_eof')
        out.extend(block)
    return bytes(out)


def capture_actor(mode, run_id, timeout):
    if exact(1) != b'G':
        raise ValueError('launch_gate')
    import trial_native_capture as capture
    recorded = {}
    original_owner = capture.OwnedProcesses

    class Owner(original_owner):
        def __init__(self, child):
            super().__init__(child)
            recorded['pid'] = child.pid
            recorded['start_ticks'] = self.leader['start'] if self.leader else None

    # Fixture transport adapter: the source algorithm is unchanged. Selected
    # child identity is carried on a pipe owned by this worker, never by actor.
    capture.OwnedProcesses = Owner
    original_popen = capture.subprocess.Popen

    def small_pipe(*args, **kwargs):
        child = original_popen(*args, **kwargs)
        fcntl.fcntl(child.stdin, fcntl.F_SETPIPE_SZ, 4096)
        return child

    capture.subprocess.Popen = small_pipe
    raw, _, _, _ = capture.capture(['/usr/bin/python3', '/sources/actor.py', mode, run_id],
        timeout, Path('/tmp/timing.json'), input_bytes=b'x'*8500,
        custody_path=Path('/tmp/custody.json'))
    custody = json.loads(Path('/tmp/custody.json').read_bytes())
    if mode == 'capture_report_loss':
        os._exit(36)
    # stdout is raw transient pipe transport. stderr is fixed selected control;
    # captured actor stderr never reaches either selected channel.
    fact = dict(kind='ACTOR_WAIT', **recorded, custody=custody)
    os.write(2, json.dumps(fact, separators=(',', ':')).encode()+b'\n')
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def retention_worker(mode, run_id, task_hash, oracle_hash, config_hash, source_hash, boot_id):
    from selected_native.frames import Frames
    from selected_native.retention import Retention, Intent, ExitSnapshot, RetentionError
    root = os.open('/retention', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    writer = None
    try:
        writer = Retention.create(root, Intent(run_id, task_hash, oracle_hash, config_hash, source_hash))
        emit(dict(kind='READY', run_id=run_id, intent_sha256=writer.intent_sha256))
        reader = Frames(run_id, 'fixture-model', 'fixture-cli', profile=None)
        if mode == 'fsync_hang':
            def stalled_fsync(_):
                emit(dict(kind='STAGE', stage='sink'))
                time.sleep(60)
            os.fsync = stalled_fsync
        while True:
            kind = exact(1)
            count = struct.unpack('!I', exact(4))[0]
            if count > 65536:
                raise ValueError('protocol_bounds')
            chunk = exact(count)
            if kind == b'D':
                if mode == 'parse_hang':
                    emit(dict(kind='STAGE', stage='parser'))
                    time.sleep(60)
                reader.feed(chunk)
            elif kind == b'E':
                fact = json.loads(chunk)
                receipt = writer.prepare(reader, ExitSnapshot(fact['pid'], fact['start_ticks'],
                    boot_id, fact['exit_code'], fact['signal_observed']))
                if mode == 'after_prepared':
                    os._exit(33)
                writer.close()
                emit(dict(kind='RECEIPT', **dataclasses.asdict(receipt)))
                return
            else:
                raise ValueError('protocol_kind')
    except RetentionError as exc:
        code = 'target_consumed' if str(exc) == 'target_consumed' else 'retention_refused'
        emit(dict(kind='FAILURE', code=code))
        sys.exit(21)
    except (ValueError, KeyError, TypeError, OSError, RecursionError):
        emit(dict(kind='FAILURE', code='worker_refused'))
        sys.exit(22)
    finally:
        if writer is not None:
            writer.close()
        os.close(root)


def diagnostic(mode):
    data = sys.stdin.buffer.read(65537)
    if len(data) > 65536:
        sys.exit(23)
    if mode == 'persist_hang':
        time.sleep(60)
    fd = os.open('/retained/SUPERVISOR.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    fd = os.open('/retained', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    os.fsync(fd)
    os.close(fd)


role, *args = sys.argv[1:]
if role == 'capture':
    capture_actor(args[0], args[1], float(args[2]))
elif role == 'retention':
    retention_worker(*args)
elif role == 'diagnostic':
    diagnostic(*args)
else:
    sys.exit(24)
