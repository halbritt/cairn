"""One fixed ordinary echo child; synthetic control frames, no native SDK."""
import hashlib
import json
import os
from pathlib import Path
import signal
import time
from unittest.mock import patch

from trial_coding_launch import compile_sdk_launch
from trial_sdk_direct_writer import SDKDirectWriter, SDKDirectWriterError
from trial_sdk_native_process_owner import NativeProcessOwner
import trial_sdk_native_input as native_input

HERE = Path(__file__).parent
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}


def failure_case(kind):
    """Real child/syscalls; selected boundary faults must never permit replay."""
    baseline = set(os.listdir('/proc/self/fd'))
    launch = compile_sdk_launch(session_id='00000000-0000-4000-8000-000000000002',
        model='fixture-model', effort='high', prompt='Fixed failure check.',
        memory_instruction='No memory source is opened.')
    control, native = launch.control, NativeProcessOwner()
    writer = SDKDirectWriter(control, native)
    deadline = time.monotonic() + 2
    expected_effect = b''
    received, stderr = bytearray(), bytearray()
    scratch = bytearray(512)
    result = {'kind': kind, 'ok': False, 'errors': []}
    primary = OSError('constructed uncertainty after actual syscall')
    try:
        native.start(('/usr/bin/python3', '-I', '-B',
                      str(HERE / 'trial_sdk_native_process_owner_child.py'), 'echo'),
                     str(HERE), dict(ENV))
        actual = os.write
        effect = []
        def ambiguous(fd, body):
            count = actual(fd, body[:7])
            effect.append(bytes(body[:count]))
            raise primary
        if kind == 'reported_progress_without_write':
            control.written(1)  # Deliberately invalid caller; no actual write.
            try:
                writer.advance()
            except SDKDirectWriterError as error:
                if str(error) != 'sdk_direct_writer_progress_drift':
                    raise
            else:
                raise ValueError('unwritten_progress_not_refused')
        elif kind == 'effect_then_exception':
            with patch.object(native_input.os, 'write', side_effect=ambiguous):
                try:
                    writer.advance()
                except OSError as error:
                    if error is not primary:
                        raise
                else:
                    raise ValueError('uncertain_write_not_propagated')
            expected_effect = b''.join(effect)
            if len(expected_effect) != 7:
                raise ValueError('constructed_effect_missing')
        else:
            raise ValueError('fixed_failure_case')
        snap = writer.snapshot()
        if snap['failure'] is None or control.ledger()['failure'] is None:
            raise ValueError('failure_not_sticky')
        with patch.object(native_input.os, 'write', side_effect=AssertionError('replayed write')):
            try:
                writer.advance()
            except SDKDirectWriterError as error:
                if str(error) != 'sdk_direct_writer_unusable':
                    raise
            else:
                raise ValueError('failed_writer_reused')
        native.close_stdin()
        while native.observe_wait() is None or not all(native.snapshot()[name + '_eof']
                                                       for name in ('stdout', 'stderr')):
            if time.monotonic() > deadline:
                raise TimeoutError('failure_case_deadline')
            for name, body in (('stdout', received), ('stderr', stderr)):
                with memoryview(scratch) as destination:
                    count = native.read_into(name, destination)
                    if count > 0:
                        if len(body) + count > 8500:
                            raise ValueError('failure_case_output_bound')
                        body.extend(destination[:count])
            time.sleep(.001)
        result.update(writer=snap, wait=native.observe_wait(), release=native.release(),
                      observed_effect_bytes=len(received),
                      observed_effect_sha256=hashlib.sha256(received).hexdigest())
        result['ok'] = (received == expected_effect and stderr == b'done'
            and snap['positive_write_bytes'] == 0
            and snap['native_known_written_bytes'] == 0
            and snap['native_write_outcome_unknown'] is (kind == 'effect_then_exception')
            and result['wait']['returncode'] == 0 and result['release']['released'] is True)
    except BaseException as error:
        result['errors'].append(type(error).__name__)
    finally:
        try:
            if native.snapshot()['pid'] is None:
                result['cleanup'] = native.cleanup_unlaunched()
            else:
                if native.observe_wait() is None:
                    native.signal(1, int(signal.SIGKILL))
                while native.observe_wait() is None and time.monotonic() < deadline + .5:
                    time.sleep(.001)
                result['cleanup'] = native.cleanup_after_wait()
        except BaseException as error:
            result['errors'].append('cleanup:' + type(error).__name__)
        try:
            result['final_wait'] = native.observe_wait()
        except BaseException as error:
            result['errors'].append('final_wait:' + type(error).__name__)
        try:
            result['native_final'] = native.snapshot()
        except BaseException as error:
            result['errors'].append('final_snapshot:' + type(error).__name__)
        try:
            control.close()
        except BaseException as error:
            result['errors'].append('control_close:' + type(error).__name__)
    result['fd_restored'] = set(os.listdir('/proc/self/fd')) == baseline
    result['ok'] = result['ok'] and result['fd_restored'] and not result['errors']
    return result


def main():
    baseline = set(os.listdir('/proc/self/fd'))
    launch = compile_sdk_launch(session_id='00000000-0000-4000-8000-000000000001',
        model='fixture-model', effort='high', prompt='Fixed ordinary writer check.',
        memory_instruction='No memory source is opened.')
    control, native = launch.control, NativeProcessOwner()
    writer = SDKDirectWriter(control, native)
    expected, received, stderr = bytearray(), bytearray(), bytearray()
    deadline = time.monotonic() + 7
    result = {'ok': False, 'scope': 'ordinary local write mechanism only', 'errors': []}
    scratch = bytearray(512)
    def drain():
        for name, body in (('stdout', received), ('stderr', stderr)):
            while not native.snapshot()[name + '_eof']:
                with memoryview(scratch) as destination:
                    count = native.read_into(name, destination)
                    if count <= 0:
                        break
                    if len(body) + count > 8500:
                        raise ValueError('fixture_output_bound')
                    body.extend(destination[:count])
    def pump():
        while control.pending_write() is not None:
            if time.monotonic() > deadline:
                raise TimeoutError('fixture_write_deadline')
            pending = control.pending_write()
            count = writer.advance()
            if count:
                expected.extend(memoryview(pending)[:count])
            drain()
        if native.snapshot()['stdin_closed']:
            raise ValueError('idle_is_not_stdin_close')
    try:
        native.start(('/usr/bin/python3', '-I', '-B',
                      str(HERE / 'trial_sdk_native_process_owner_child.py'), 'echo'),
                     str(HERE), dict(ENV))
        # A known pre-syscall would-block boundary is not kernel-pressure proof.
        before = control.ledger()['written_input_bytes']
        with patch.object(native_input.os, 'write', side_effect=BlockingIOError()):
            if writer.advance() != 0 or control.ledger()['written_input_bytes'] != before:
                raise ValueError('would_block_progress')
        actual_write = os.write
        def short_write(fd, body):
            return actual_write(fd, body[:7])
        with patch.object(native_input.os, 'write', side_effect=short_write):
            pump()
        control.receive({'type': 'control_response', 'response': {
            'subtype': 'success', 'request_id': control.bindings['initialize_request_id'],
            'response': {}}})
        pump()
        for index, event in enumerate(('SessionStart', 'UserPromptSubmit',
                                        'PostToolUse', 'PostToolUseFailure')):
            request = 'ordinary-' + str(index)
            control.receive({'type': 'control_request', 'request_id': request,
                'request': {'subtype': 'hook_callback',
                    'callback_id': control.bindings['callback_ids'][event],
                    'input': {'hook_event_name': event,
                              'session_id': control.bindings['session_id']}}})
            control.reply(request, 'Synthetic context: café.')
            pump()
        ledger = control.ledger()
        result['before_close'] = writer.snapshot()
        native.close_stdin()
        while native.observe_wait() is None or not all(native.snapshot()[name + '_eof']
                                                       for name in ('stdout', 'stderr')):
            if time.monotonic() > deadline:
                raise TimeoutError('fixture_drain_deadline')
            drain()
            time.sleep(.001)
        result.update(wait=native.observe_wait(), release=native.release(),
            writer=writer.snapshot(), sdk_written=ledger['written_input_bytes'],
            expected_bytes=len(expected), received_bytes=len(received),
            expected_sha256=hashlib.sha256(expected).hexdigest(),
            received_sha256=hashlib.sha256(received).hexdigest())
        result['ok'] = (expected == received and stderr == b'done'
            and ledger['written_input_bytes'] == len(expected)
            and native.snapshot()['native_input']['known_written'] == len(expected)
            and result['wait']['returncode'] == 0
            and result['release']['released'] is True and not result['release']['cleanup'])
    except BaseException as error:
        result['errors'].append(type(error).__name__)
    finally:
        try:
            if native.snapshot()['pid'] is None:
                result['cleanup'] = native.cleanup_unlaunched()
            else:
                if native.observe_wait() is None:
                    native.signal(1, int(signal.SIGKILL))
                while native.observe_wait() is None and time.monotonic() < deadline + .5:
                    time.sleep(.001)
                result['cleanup'] = native.cleanup_after_wait()
        except BaseException as error:
            result['errors'].append('cleanup:' + type(error).__name__)
        try:
            result['final_wait'] = native.observe_wait()
        except BaseException as error:
            result['errors'].append('final_wait:' + type(error).__name__)
        try:
            result['native_final'] = native.snapshot()
        except BaseException as error:
            result['errors'].append('final_snapshot:' + type(error).__name__)
        try:
            control.close()
        except BaseException as error:
            result['errors'].append('control_close:' + type(error).__name__)
    result['fd_restored'] = set(os.listdir('/proc/self/fd')) == baseline
    result['ok'] = result['ok'] and result['fd_restored'] and not result['errors']
    if result['ok']:
        result['failure_cases'] = [failure_case(kind) for kind in
            ('reported_progress_without_write', 'effect_then_exception')]
        result['ok'] = all(row['ok'] for row in result['failure_cases'])
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
