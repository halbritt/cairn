"""Fixed ordinary producer I/O/failure checks, not native or memory evidence."""
from contextlib import ExitStack, nullcontext
import hashlib
import json
import os
from pathlib import Path
import signal
import time
from unittest.mock import patch

from test_trial_sdk_producer_driver import callback_event
from trial_sdk_incoming import IncomingError
from trial_sdk_producer_driver import SDKProducerDriver, ProducerDriverError
from trial_sdk_producer_session_host import combined_case


def case(mode, work_end):
    baseline = set(os.listdir('/proc/self/fd'))
    result = dict(mode=mode, ok=False, errors=[])
    control = driver = native = None
    actual_readv, actual_write, actual_close, actual_clock = os.readv, os.write, os.close, time.monotonic_ns
    actual_sha = hashlib.sha256
    primary = KeyboardInterrupt('constructed post-read clock failure')
    close_primary = OSError('constructed post-close uncertainty')
    write_primary = OSError('constructed post-write uncertainty')
    hash_primary = KeyboardInterrupt('constructed generated-hash interruption')
    force_clock, close_count = False, 0
    deadline = min(work_end, time.monotonic() + 2)
    try:
        control, event = callback_event('Full callback content: café 😀' + 'é' * 35000)
        class Digest:
            def __init__(self, *args, **kwargs):
                self.value = actual_sha(*args, **kwargs)
            def update(self, body):
                if bytes(body).startswith(b'{"extra":'):
                    raise hash_primary
                self.value.update(body)
            def hexdigest(self):
                return self.value.hexdigest()
        # Keep the genuine hashing backend; inject interruption at its update
        # boundary rather than replacing a producer/process owner.
        with (patch.object(hashlib, 'sha256', side_effect=Digest)
                if mode == 'generated_hash' else nullcontext()):
            driver = SDKProducerDriver(event)
        native = driver.native_owner
        if mode == 'input_drift':
            event.callback._input['extra'] = 'Full callback content: café 😀' + 'z' * 70000
        here = Path(__file__).parent
        native.start(('/usr/bin/python3', '-B', str(here/'trial_sdk_producer_driver_child.py'), mode),
            str(here), {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'})
        input_fd, output_fd = native.fileno('stdin'), native.fileno('stdout')
        read_prefix, accepted = bytearray(), 0
        def readv(fd, buffers):
            nonlocal force_clock
            count = actual_readv(fd, buffers)
            if fd == output_fd and count > 0:
                read_prefix.extend(buffers[0][:count])
                if mode == 'read_clock':
                    force_clock = True
            return count
        def write(fd, body):
            nonlocal force_clock, accepted
            count = actual_write(fd, body)
            if fd == input_fd:
                accepted += count
                if mode == 'write_unknown' and count > 0:
                    raise write_primary
                if mode == 'late_write' and count > 0:
                    force_clock = True
            return count
        def clock():
            if force_clock:
                if mode == 'read_clock':
                    raise primary
                return event.observation['deadline_ns']
            return actual_clock()
        def close(fd):
            nonlocal close_count
            actual_close(fd)
            if fd == input_fd:
                close_count += 1
                if mode == 'close_unknown':
                    raise close_primary
        observed = None
        with ExitStack() as stack:
            stack.enter_context(patch.object(os, 'readv', side_effect=readv))
            stack.enter_context(patch.object(os, 'write', side_effect=write))
            stack.enter_context(patch.object(os, 'close', side_effect=close))
            stack.enter_context(patch.object(time, 'monotonic_ns', side_effect=clock))
            while time.monotonic() < deadline:
                try:
                    if driver.step() == 'ready':
                        break
                except BaseException as error:
                    observed = error
                    break
            else:
                raise TimeoutError('ordinary_producer_deadline')
        state = driver.snapshot()
        result['observation'] = state
        result['observed_refusal'] = None if observed is None else (
            'constructed_read_clock' if observed is primary else
            'constructed_close_unknown' if observed is close_primary else str(observed))
        if mode in ('normal', 'empty'):
            output = driver.take_output()
            metadata, graph = output.metadata(), output.take()
            if mode == 'normal':
                expected_graph = dict(early='x'*40000, input_bytes=event.callback.input_bytes,
                    input_sha256=event.callback.input_sha256, event=event.callback.event,
                    context='ordinary producer context: café 😀')
                assert graph == expected_graph
                raw = json.dumps(expected_graph, ensure_ascii=False, separators=(',', ':')).encode()
            else:
                assert graph is None
                raw = b''
            assert state['stdout_bytes'] == len(raw) and state['stdout_sha256'] == hashlib.sha256(raw).hexdigest()
            assert metadata['input_sha256'] == event.callback.input_sha256
            assert state['input_generated_bytes'] == state['input_written_bytes'] == event.callback.input_bytes
            assert state['input_generated_sha256'] == state['input_written_sha256'] == event.callback.input_sha256
            stderr = b'stderr-before-input\n'+b'z'*50000+b'\ninput-eof-observed\n'
            assert state['stderr_bytes'] == len(stderr) and state['stderr_sha256'] == hashlib.sha256(stderr).hexdigest()
            assert state['release']['released'] and state['wait']['returncode'] == 0
            assert state['memory_call_attempts'] is None and not state['memory_call_measurement_attested']
            assert state['input_close_attempted'] and close_count == 1 and not state['pending_input_bytes']
            for take in (output.take, driver.take_output):
                try:
                    take()
                except ProducerDriverError as error:
                    assert str(error) == 'sdk_producer_output_consumed'
                else:
                    raise ValueError('output_reused')
            assert observed is None
        else:
            expected = dict(nonzero=(ProducerDriverError,'sdk_producer_nonzero'),
                malformed=(IncomingError,'incoming_utf8'), duplicate=(IncomingError,'incoming_json'),
                input_drift=(ProducerDriverError,'sdk_producer_input_drift'),
                close_unknown=(OSError,str(close_primary)), read_clock=(KeyboardInterrupt,str(primary)),
                late_write=(ProducerDriverError,'sdk_producer_deadline'),
                write_unknown=(OSError,str(write_primary)), generated_hash=(KeyboardInterrupt,str(hash_primary)))
            assert type(observed) is expected[mode][0] and str(observed) == expected[mode][1]
            assert state['failure'] is not None and not state['ready']
            if mode == 'read_clock':
                assert observed is primary and len(read_prefix) > 0
                assert state['stdout_bytes'] == len(read_prefix)
                assert state['stdout_sha256'] == hashlib.sha256(read_prefix).hexdigest()
            if mode == 'late_write':
                assert accepted > 0 and state['input_written_bytes'] == accepted
                assert native.snapshot()['native_input']['known_written'] == accepted
            if mode == 'input_drift':
                assert not state['input_close_attempted']
            if mode == 'close_unknown':
                assert observed is close_primary and state['input_close_attempted'] and close_count == 1
                assert native.snapshot()['native_input']['close_unknown']
            if mode == 'write_unknown':
                assert observed is write_primary and accepted > 0
                assert state['input_written_bytes'] == 0
                assert state['native_input']['write_unknown']
                assert state['native_primary'] == 'owner_input_failed'
            if mode == 'generated_hash':
                assert observed is hash_primary and state['input_generated_bytes'] > 0
                assert state['input_generated_hash_unknown'] and state['input_written_bytes'] == 0
        result['ok'] = True
    except BaseException as error:
        result['errors'].append(type(error).__name__+':'+str(error)[:100])
    finally:
        if native is not None:
            try:
                if native.observe_wait() is None:
                    native.signal(1, int(signal.SIGKILL))
                while native.observe_wait() is None and time.monotonic() < deadline+.25:
                    time.sleep(.001)
                result['wait'] = native.observe_wait()
                result['cleanup'] = native.cleanup_after_wait()
            except BaseException as error:
                result['errors'].append('native_cleanup:'+type(error).__name__)
        if driver is not None:
            try:
                driver.cancel()
            except BaseException as error:
                result['errors'].append('input_cleanup:'+type(error).__name__)
        if control is not None:
            control.close()
    result['fd_restored'] = set(os.listdir('/proc/self/fd')) == baseline
    result['ok'] = (result['ok'] and not result['errors'] and result['fd_restored']
        and result.get('wait') is not None and result.get('cleanup',{}).get('handles_detached') is True)
    return result


def main():
    work_end = time.monotonic()+7
    cases = [case(mode, work_end) for mode in ('normal','empty','nonzero','malformed',
        'duplicate','input_drift','close_unknown','read_clock','late_write','write_unknown','generated_hash')]
    cases.append(combined_case(work_end))
    result = dict(ok=all(row['ok'] for row in cases), cases=cases,
                  scope='ordinary independently owned full-input producer mechanism; no native SDK/memory/provider')
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
