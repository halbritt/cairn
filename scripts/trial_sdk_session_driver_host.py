"""Fixed ordinary custody/result/EOF checks; no native interface qualification."""
import hashlib
import json
import os
from pathlib import Path
import signal
import time
from unittest.mock import patch
from contextlib import nullcontext

import test_trial_sdk_bootstrap_review as fixtures
from trial_native_bootstrap import PreparationRefusal
from trial_sdk_control import SDKControlError
from trial_sdk_session_driver import SDKSessionDriver, SessionDriverError


def case(mode, work_end):
    baseline = set(os.listdir('/proc/self/fd'))
    fixture = fixtures.SDKReviewTests()
    fixture.setUp()
    driver = native = None
    result = dict(mode=mode, ok=False, errors=[], terminal_events=0)
    deadline = min(work_end, time.monotonic() + 2)
    primary = OSError('constructed actual-close uncertainty')
    actual_close, close_attempts = os.close, []
    target = None
    def close(fd):
        if fd == target:
            close_attempts.append(fd)
            actual_close(fd)
            raise primary
        return actual_close(fd)
    try:
        if time.monotonic() >= deadline:
            raise TimeoutError('suite_work_deadline')
        review = fixture.prepare()
        control = review.launch.control
        held = [(lease.fd, os.fstat(lease.fd).st_dev, os.fstat(lease.fd).st_ino)
                for lease in review._snapshot.tree.leases]
        driver = SDKSessionDriver(review)
        native = driver.native_owner
        here = Path(__file__).parent
        native.start(('/usr/bin/python3', '-I', '-B', str(here / 'trial_sdk_session_driver_child.py'), mode),
            str(here), {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})
        target = native.fileno('stdin')
        # Refusal of premature source close must leave the live session usable.
        try:
            driver.close_sources()
        except SessionDriverError as error:
            if str(error) != 'sdk_session_native_cleanup_pending':
                raise
        else:
            raise ValueError('live_sources_were_closed')
        try:
            driver.require_complete()
        except PreparationRefusal as error:
            if str(error) != 'hook_interface_unavailable':
                raise
        else:
            raise ValueError('runtime_gate_bypassed')
        review.close()  # The transferred original owner no longer closes anything.
        observed = exception_type = None
        with (patch.object(os, 'close', side_effect=close) if mode == 'close_unknown' else nullcontext()):
            while time.monotonic() < deadline:
                try:
                    event = driver.step()
                except BaseException as error:
                    observed = 'constructed_stdin_close_unknown' if error is primary else str(error)
                    exception_type = type(error)
                    break
                if event.kind == 'callback':
                    full = json.loads(b''.join(event.callback.iter_input_json()))
                    if full['extra'] != 'full canonical input: café':
                        raise ValueError('full_input_not_consumed')
                    if mode == 'source_drift' and event.callback.event == 'PostToolUseFailure':
                        source = fixture.source / 'prompt.txt'
                        source.chmod(0o600)
                        source.write_bytes(b'Intentional source drift while child remains owned')
                    driver.reply(event.callback.request_id, 'ordinary context: café')
                elif event.kind == 'terminal':
                    result['terminal_events'] += 1
                    result['terminal_metadata'] = event.document.metadata()
                    graph = event.document.take()
                    if graph['result'] != 'Surrogate output only; task not graded.':
                        raise ValueError('terminal_graph_lost')
                state = native.snapshot()
                if state['wait'] is not None and state['stdout_eof'] and state['stderr_eof']:
                    break
            else:
                raise TimeoutError('ordinary_session_deadline')
        result['observed_refusal'] = observed
        result['before_cleanup'] = driver.snapshot()
        result['input_ended'] = control.ledger()['input_ended']
        expected = dict(premature='sdk_input_not_drained', causal='sdk_terminal_before_input_drained',
            close_unknown='constructed_stdin_close_unknown', source_drift='session_custody_unknown',
            duplicate='sdk_terminal_duplicate', post_control='sdk_input_ended')
        if mode in ('normal', 'error'):
            result['release'] = native.release()
            result['ok'] = (observed is None and result['terminal_events'] == 1
                and result['terminal_metadata']['is_error'] is (mode == 'error')
                and result['input_ended'] and native.observe_wait()['returncode'] == 0
                and result['release']['released']
                and driver.snapshot()['stream']['stderr_sha256'] == hashlib.sha256(b'input-eof-observed\n').hexdigest())
        else:
            expected_type = dict(premature=SDKControlError, causal=SessionDriverError,
                close_unknown=OSError, source_drift=PreparationRefusal,
                duplicate=SessionDriverError, post_control=SDKControlError)[mode]
            result['ok'] = (observed == expected[mode] and exception_type is expected_type
                and driver.snapshot()['failure'] is not None)
            if mode in ('premature', 'causal', 'source_drift'):
                result['ok'] = (result['ok'] and not result['input_ended']
                    and not driver.snapshot()['input_close_attempted'])
            if mode == 'source_drift':
                result['held_after_live_refusal'] = all(
                    (os.fstat(fd).st_dev, os.fstat(fd).st_ino) == (dev, ino) for fd, dev, ino in held)
                result['ok'] = (result['ok'] and result['held_after_live_refusal']
                    and not control.ledger()['closed'] and native.observe_wait() is None)
            if mode == 'close_unknown':
                result['ok'] = (result['ok'] and result['input_ended'] and len(close_attempts) == 1
                    and driver.snapshot()['input_close_attempted']
                    and native.snapshot()['native_input']['close_unknown'])
                result['close_attempts'] = len(close_attempts)
    except BaseException as error:
        result['errors'].append(type(error).__name__ + ':' + str(error)[:100])
    finally:
        if native is not None:
            try:
                if native.observe_wait() is None:
                    native.signal(1, int(signal.SIGKILL))
                while native.observe_wait() is None and time.monotonic() < deadline + .25:
                    time.sleep(.001)
                result['cleanup'] = native.cleanup_after_wait()
            except BaseException as error:
                result['errors'].append('native_cleanup:' + type(error).__name__)
            try:
                result['wait'] = native.observe_wait()
            except BaseException as error:
                result['errors'].append('native_wait:' + type(error).__name__)
        if driver is not None:
            try:
                result['source_cleanup'] = driver.close_sources()
            except BaseException as error:
                result['errors'].append('source_cleanup:' + type(error).__name__)
            try:
                result['final'] = driver.snapshot()
            except BaseException as error:
                result['errors'].append('final:' + type(error).__name__)
        try:
            result['fixture_cleanup_ok'] = fixture.doCleanups()
            if not result['fixture_cleanup_ok']:
                result['errors'].append('fixture_cleanup_failed')
        except BaseException as error:
            result['errors'].append('fixture_cleanup:' + type(error).__name__)
    result['fd_restored'] = set(os.listdir('/proc/self/fd')) == baseline
    result['ok'] = (result['ok'] and not result['errors'] and result['fd_restored']
        and result.get('wait') is not None and result.get('cleanup', {}).get('handles_detached') is True
        and result.get('source_cleanup') == ())
    return result


def main():
    deadline = time.monotonic() + 7
    cases = [case(mode, deadline) for mode in ('normal', 'error', 'premature', 'causal',
        'close_unknown', 'source_drift', 'duplicate', 'post_control')]
    result = dict(ok=all(row['ok'] for row in cases), cases=cases,
        scope='ordinary source/input/EOF mechanism only; no SDK/native/provider/namespace/task grade')
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
