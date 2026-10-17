"""Fixed ordinary session check; synthetic hooks, no native/runtime qualification."""
import hashlib
import json
import os
from pathlib import Path
import signal
import select
import time
from unittest.mock import patch

from trial_coding_launch import compile_sdk_launch
from trial_sdk_native_process_owner import NativeProcessOwner
from trial_sdk_stream_driver import SDKStreamDriver
from trial_sdk_control import SDKControlError


def fault_case(kind):
    """Real owned child and syscalls; only time/syscall boundaries injected."""
    baseline = set(os.listdir('/proc/self/fd'))
    here = Path(__file__).parent
    launch = compile_sdk_launch(session_id='00000000-0000-4000-8000-000000000004',
        model='surrogate-model', effort='high', prompt='Fixed boundary check.',
        memory_instruction='No memory or provider source is opened.')
    control, native = launch.control, NativeProcessOwner()
    driver = SDKStreamDriver(control, native)
    deadline = time.monotonic() + 2
    result = dict(kind=kind, ok=False, errors=[])
    actual_read, actual_ns = os.readv, time.monotonic_ns
    consumed = []
    read_hash = {name: hashlib.sha256() for name in ('stdout', 'stderr')}
    stream_fds = {}
    primary = OSError('constructed post-read clock failure')
    def read_then_fault(fd, buffers):
        count = actual_read(fd, buffers)
        if count > 0:
            consumed.append(count)
            read_hash[stream_fds[fd]].update(memoryview(buffers[0])[:count])
        return count
    def clock():
        if consumed:
            raise primary
        return actual_ns()
    try:
        argv = ('/usr/bin/python3', '-I', '-B', str(here / 'trial_sdk_stream_driver_child.py'))
        mode = ('exit_callback' if kind == 'callback_after_wait' else
                'exit_document' if kind == 'ordinary_after_wait' else
                'partial_exit' if kind == 'partial_line_eof' else
                'eof_alive' if kind == 'eof_before_wait' else
                'batch' if kind in ('held_batch_tail', 'slab_isolation') else
                'flood' if kind == 'writable_fairness' else None)
        native.start(argv + ((mode,) if mode is not None else ()),
            str(here), {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        stream_fds = {native.fileno(name): name for name in ('stdout', 'stderr')}
        if kind == 'read_clock_unknown':
            with patch.object(os, 'readv', side_effect=read_then_fault), \
                    patch.object(time, 'monotonic_ns', side_effect=clock):
                try:
                    while time.monotonic() < deadline:
                        driver.step()
                    raise TimeoutError('clock_fault_not_reached')
                except OSError as error:
                    if error is not primary:
                        raise
            snapshot = driver.snapshot()
            result['consumed_bytes'] = sum(consumed)
            result['observed_bytes'] = snapshot['stdout_bytes'] + snapshot['stderr_bytes']
            result['expected_hashes'] = {name: value.hexdigest() for name, value in read_hash.items()}
            result['ok'] = (bool(consumed) and result['observed_bytes'] == result['consumed_bytes']
                and all(snapshot[name + '_sha256'] == value.hexdigest() for name, value in read_hash.items()))
        elif kind in ('callback_after_wait', 'ordinary_after_wait', 'partial_line_eof',
                      'eof_before_wait', 'read_interval_overrun'):
            while not control.ledger()['user_written'] and time.monotonic() < deadline:
                driver.step()
            if kind == 'read_interval_overrun':
                def delayed_clock():
                    return actual_ns() + (5_000_000_000 if consumed else 0)
                with patch.object(os, 'readv', side_effect=read_then_fault), \
                        patch.object(time, 'monotonic_ns', side_effect=delayed_clock):
                    try:
                        while time.monotonic() < deadline:
                            driver.step()
                        raise TimeoutError('read_interval_overrun_not_reached')
                    except SDKControlError as error:
                        if str(error) != 'sdk_callback_timeout':
                            raise
                result['ok'] = bool(consumed) and control.ledger()['rows'][0]['timed_out']
                result['consumed_bytes'] = sum(consumed)
            if kind == 'callback_after_wait':
                while native.observe_wait() is None and time.monotonic() < deadline:
                    time.sleep(.001)
                if native.observe_wait() is None:
                    raise TimeoutError('surrogate_wait_missing')
                refused = False
                for _ in range(20):
                    try:
                        event = driver.step()
                    except ValueError as error:
                        if str(error) != 'sdk_stream_callback_after_wait':
                            raise
                        refused = True
                        break
                    if event.kind == 'callback':
                        break  # Defect: producer work could be launched after wait.
                result['ok'] = refused
            elif kind in ('ordinary_after_wait', 'partial_line_eof'):
                while native.observe_wait() is None and time.monotonic() < deadline:
                    time.sleep(.001)
                if native.observe_wait() is None:
                    raise TimeoutError('ordinary_wait_missing')
                document, refusal = None, None
                while time.monotonic() < deadline:
                    try:
                        event = driver.step()
                    except ValueError as error:
                        refusal = str(error)
                        break
                    if event.document is not None:
                        document = event.document.take()
                    state = native.snapshot()
                    if state['stdout_eof'] and state['stderr_eof']:
                        break
                if kind == 'ordinary_after_wait':
                    result['ok'] = (refusal is None and document == dict(
                        type='surrogate_document', content='after actual wait 😀')
                        and native.snapshot()['stdout_eof'] and native.snapshot()['stderr_eof'])
                    if result['ok']:
                        driver.close_input_after_wait()
                        result['known_release'] = native.release()
                        result['ok'] = result['known_release']['released']
                else:
                    result['ok'] = refusal == 'incoming_partial_line' and native.snapshot()['stdout_eof']
                    result['observed_refusal'] = refusal
            elif kind == 'eof_before_wait':
                while time.monotonic() < deadline:
                    driver.step()
                    state = native.snapshot()
                    if state['stdout_eof'] and state['stderr_eof']:
                        break
                state = native.snapshot()
                result['ok'] = (state['stdout_eof'] and state['stderr_eof']
                    and native.observe_wait() is None and not state['stdin_closed'])
        elif kind == 'writable_fairness':
            while not control.ledger()['user_written'] and time.monotonic() < deadline:
                event = driver.step()
                if event.document is not None:
                    event.document.take()  # Do not accumulate flood payloads.
            result['stdout_before_user_written'] = driver.snapshot()['stdout_bytes']
            result['ok'] = control.ledger()['user_written'] and result['stdout_before_user_written'] <= 2048
        elif kind in ('held_batch_tail', 'slab_isolation',
                      'idle_timeout', 'enqueue_timeout', 'late_final_write', 'poll_deadline_bound'):
            callback_event = None
            while time.monotonic() < deadline:
                event = driver.step()
                if event.kind == 'callback':
                    callback_event = event
                    break
            if callback_event is None:
                raise TimeoutError('surrogate_callback_missing')
            callback, bound = callback_event.callback, callback_event.observation['deadline_ns']
            before = control.ledger()['written_input_bytes']
            if kind in ('held_batch_tail', 'slab_isolation'):
                if callback.request_id != 'a':
                    raise ValueError('batch_first_identity')
                driver.reply(callback.request_id, 'surrogate context: café')
                while time.monotonic() < deadline:
                    if driver.step().kind == 'reply_written':
                        break
                else:
                    raise TimeoutError('batch_reply_missing')
                if kind == 'held_batch_tail':
                    with patch.object(time, 'monotonic_ns', return_value=bound):
                        try:
                            driver.step()
                        except SDKControlError as error:
                            if str(error) != 'sdk_callback_timeout':
                                raise
                        else:
                            raise ValueError('tail_callback_timer_restarted')
                    state = driver.snapshot()
                    result['ok'] = (state['active']['first_byte_ns'] == callback_event.observation['first_byte_ns']
                        and control.ledger()['rows'][1]['timed_out'] and state['completed_replies'] == 1)
                else:
                    second = driver.step()
                    if second.kind != 'callback' or second.callback.request_id != 'b':
                        raise ValueError('batch_second_identity')
                    while not select.select([native.fileno('stderr')], [], [], .01)[0]:
                        if time.monotonic() > deadline:
                            raise TimeoutError('batch_stderr_missing')
                    driver.step()  # Actual stderr read while stdout tail is pinned.
                    if driver.snapshot()['stderr_bytes'] != 512 or not driver.snapshot()['stdout_batch_held']:
                        raise ValueError('batch_stderr_not_observed')
                    driver.reply('b', 'surrogate context: café')
                    while time.monotonic() < deadline:
                        if driver.step().kind == 'reply_written':
                            break
                    else:
                        raise TimeoutError('batch_second_reply_missing')
                    tail = driver.step()
                    result['ok'] = (tail.kind == 'document'
                        and tail.document.take() == dict(type='tail', content='unchanged😀')
                        and driver.snapshot()['stderr_sha256'] == hashlib.sha256(b'Z' * 512).hexdigest())
            elif kind == 'poll_deadline_bound':
                polls = []
                def poll(reads, writes, exceptional, timeout):
                    polls.append(timeout)
                    return [], [], []
                with patch.object(time, 'monotonic_ns', return_value=bound - 12345), \
                        patch('trial_sdk_stream_driver.select.select', side_effect=poll):
                    event = driver.step()
                result['ok'] = (event.kind == 'idle' and len(polls) == 1
                    and 0 < polls[0] <= 12345 / 1e9 and driver.snapshot()['failure'] is None)
                result['poll_seconds'] = polls
            elif kind == 'late_final_write':
                driver.reply(callback.request_id, 'surrogate context: café')
                actual_write, effects = os.write, []
                def write_then_overrun(fd, body):
                    count = actual_write(fd, body)
                    effects.append(count)
                    return count
                def write_clock():
                    return bound if effects else actual_ns()
                with patch.object(os, 'write', side_effect=write_then_overrun), \
                        patch.object(time, 'monotonic_ns', side_effect=write_clock):
                    try:
                        while time.monotonic() < deadline:
                            driver.step()
                        raise TimeoutError('final_write_overrun_not_reached')
                    except SDKControlError as error:
                        if str(error) != 'sdk_callback_timeout':
                            raise
                row = control.ledger()['rows'][0]
                result['actual_reply_write_bytes'] = sum(effects)
                result['ok'] = (bool(effects) and row['reply_written'] and row['timed_out']
                    and control.ledger()['written_input_bytes'] == before + sum(effects)
                    and driver.snapshot()['completed_replies'] == 0)
            else:
                with patch.object(time, 'monotonic_ns', return_value=bound):
                    try:
                        if kind == 'idle_timeout':
                            driver.step()
                        else:
                            driver.reply(callback.request_id, 'surrogate context: café')
                    except SDKControlError as error:
                        if str(error) != 'sdk_callback_timeout':
                            raise
                    else:
                        raise ValueError('elapsed_callback_was_not_refused')
                result['ok'] = (control.ledger()['rows'][0]['timed_out']
                    and control.ledger()['written_input_bytes'] == before)
            if kind not in ('poll_deadline_bound', 'slab_isolation'):
                with patch.object(os, 'write', side_effect=AssertionError('replayed terminal write')):
                    try:
                        driver.step()
                    except ValueError as error:
                        if str(error) != 'sdk_stream_unusable':
                            raise
                    else:
                        raise ValueError('terminal_driver_reused')
        else:
            raise ValueError('fixed_boundary_case')
    except BaseException as error:
        result['errors'].append(type(error).__name__ + ':' + str(error)[:120])
    finally:
        try:
            if native.observe_wait() is None:
                native.signal(1, int(signal.SIGKILL))
            while native.observe_wait() is None and time.monotonic() < deadline + .5:
                time.sleep(.001)
            result['cleanup'] = native.cleanup_after_wait()
        except BaseException as error:
            result['errors'].append('cleanup:' + type(error).__name__)
        for name, operation in (('wait', native.observe_wait), ('driver', driver.snapshot),
                                ('native', native.snapshot)):
            try:
                result[name] = operation()
            except BaseException as error:
                result['errors'].append(name + ':' + type(error).__name__)
        try:
            driver.cancel()
            result['after_cancel'] = driver.snapshot()
        except BaseException as error:
            result['errors'].append('cancel:' + type(error).__name__)
        control.close()
    result['fd_restored'] = set(os.listdir('/proc/self/fd')) == baseline
    result['ok'] = (result['ok'] and not result['errors'] and result['fd_restored']
        and result.get('wait') is not None and result['cleanup']['handles_detached']
        and result.get('after_cancel', {}).get('cancelled') is True
        and result['after_cancel']['stdout_batch_held'] is False
        and result['after_cancel']['cleanup_unknown'] is False)
    return result


def main():
    baseline = set(os.listdir('/proc/self/fd'))
    here = Path(__file__).parent
    launch = compile_sdk_launch(session_id='00000000-0000-4000-8000-000000000004',
        model='surrogate-model', effort='high', prompt='Fixed ordinary stream check.',
        memory_instruction='No memory or provider source is opened.')
    control, native = launch.control, NativeProcessOwner()
    driver = SDKStreamDriver(control, native)
    deadline = time.monotonic() + 7
    result = dict(ok=False, errors=[], scope='ordinary source mechanism only')
    callbacks, written, documents = [], [], []
    try:
        native.start(('/usr/bin/python3', '-I', '-B', str(here / 'trial_sdk_stream_driver_child.py')),
            str(here), {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        while time.monotonic() < deadline:
            event = driver.step()
            if event.kind == 'callback':
                callback = event.callback
                full = json.loads(b''.join(callback.iter_input_json()))
                if full['extra'] != 'café-😀' or full['nested'] != [{'full': True}]:
                    raise ValueError('surrogate_full_input_lost')
                callbacks.append(callback.event)
                if native.snapshot()['stdin_closed']:
                    raise ValueError('idle_closed_input')
                driver.reply(callback.request_id, 'surrogate context: café')
            elif event.kind == 'reply_written':
                written.append(event.observation)
            elif event.kind == 'document':
                documents.append(event.document.take())
            snapshot = native.snapshot()
            if snapshot['wait'] is not None and snapshot['stdout_eof'] and snapshot['stderr_eof']:
                break
        else:
            raise TimeoutError('ordinary_stream_deadline')
        driver.close_input_after_wait()
        result.update(driver=driver.snapshot(), ledger=control.ledger(),
            wait=native.observe_wait(), release=native.release(), callbacks=callbacks,
            written=written, documents=documents)
        state = result['driver']
        result['ok'] = (callbacks == ['SessionStart', 'UserPromptSubmit', 'PostToolUse', 'PostToolUseFailure']
            and len(written) == 4 and all(row['completed_ns'] < row['deadline_ns'] for row in written)
            and documents == [dict(type='surrogate_document', content='strict UTF-8 😀')]
            and state['stderr_bytes'] == len(b'observed-reply\n') * 4
            and state['stderr_sha256'] == hashlib.sha256(b'observed-reply\n' * 4).hexdigest()
            and state['writer']['positive_write_bytes'] == result['ledger']['written_input_bytes']
            and result['wait']['returncode'] == 0 and result['release']['released'])
    except BaseException as error:
        result['errors'].append(type(error).__name__ + ':' + str(error)[:160])
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
        for name, operation in (('final_wait', native.observe_wait), ('native_final', native.snapshot),
                                ('driver_final', driver.snapshot)):
            try:
                result[name] = operation()
            except BaseException as error:
                result['errors'].append(name + ':' + type(error).__name__)
        try:
            driver.cancel()
            result['after_cancel'] = driver.snapshot()
        except BaseException as error:
            result['errors'].append('driver_cancel:' + type(error).__name__)
        control.close()
    result['fd_restored'] = set(os.listdir('/proc/self/fd')) == baseline
    result['ok'] = result['ok'] and not result['errors'] and result['fd_restored']
    result['fault_cases'] = [fault_case(kind) for kind in ('read_clock_unknown', 'callback_after_wait',
        'read_interval_overrun', 'idle_timeout', 'enqueue_timeout', 'late_final_write',
        'poll_deadline_bound', 'writable_fairness', 'held_batch_tail', 'slab_isolation',
        'ordinary_after_wait', 'partial_line_eof', 'eof_before_wait')]
    result['ok'] = result['ok'] and all(row['ok'] for row in result['fault_cases'])
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
