"""Combined owned SDK-shaped child/producer test; no native/provider execution."""
import hashlib
import os
from pathlib import Path
import signal
import time
from unittest.mock import patch

import test_trial_sdk_bootstrap_review as fixtures
from trial_paired_request import canonical_sha
from trial_sdk_producer_driver import SDKProducerDriver
from trial_sdk_session_driver import SDKSessionDriver


def combined_case(work_end):
    baseline = set(os.listdir('/proc/self/fd'))
    fixture = fixtures.SDKReviewTests()
    fixture.setUp()
    session = native = producer = None
    rows, replies = [], []
    result = dict(mode='combined_session',ok=False,errors=[],producer_rows=rows,reply_written=replies)
    actual_write = os.write
    deadline = min(work_end,time.monotonic()+3)
    observed_callback = False
    try:
        prompt = fixture.source/'prompt.txt'
        prompt.chmod(0o600)
        body = (fixture.prompt+'x'*2048).encode()
        prompt.write_bytes(body)
        fixture.document['sources']['prompt']['sha256'] = hashlib.sha256(body).hexdigest()
        fixture.document['binding']['source_set_sha256'] = canonical_sha(
            {role:ref['sha256'] for role,ref in fixture.document['sources'].items()})
        review = fixture.prepare()
        control = review.launch.control
        initial_bytes = control.pristine_binding()['initialize_bytes']
        session = SDKSessionDriver(review)
        native = session.native_owner
        here = Path(__file__).parent
        native.start(('/usr/bin/python3','-B',str(here/'trial_sdk_producer_session_child.py')),
                     str(here),{'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
        input_fd, actual_input = native.fileno('stdin'), 0
        def write(fd,body):
            nonlocal actual_input
            # Model zero-accepted backpressure after the first partial user
            # write, until the first callback is handed off. Real accepted
            # writes resume during producer service; no accepted count is faked.
            if fd == input_fd and actual_input >= initial_bytes+512 and not observed_callback:
                raise BlockingIOError()
            count = actual_write(fd,body)
            if fd == input_fd:
                actual_input += count
            return count
        first_native_progress = first_stderr = None
        producer_request = producer_input_at_start = None
        terminals = 0
        with patch.object(os,'write',side_effect=write):
            while time.monotonic() < deadline:
                status = producer.step(0) if producer is not None else 'idle'
                if status == 'ready':
                    state = producer.snapshot()
                    graph = producer.take_output().take()
                    assert graph['input_sha256'] == state['input_sha256']
                    assert graph['input_bytes'] == state['input_bytes']
                    assert graph['event'] == state['event']
                    assert state['release']['released'] and state['wait']['returncode'] == 0
                    if first_native_progress is None:
                        first_native_progress = actual_input > producer_input_at_start
                    if state['input_bytes'] > 8500 and first_stderr is None:
                        first_stderr = session.snapshot()['stream']['stderr_bytes'] > stderr_at_start
                    session.reply(producer_request,graph['context'])
                    rows.append(state)
                    producer.cancel()
                    producer = None
                event = session.step(0)
                if event.kind == 'callback':
                    assert producer is None
                    observed_callback = True
                    if not rows:
                        result['partial_user_at_first_handoff'] = not control.ledger()['user_written']
                    producer = SDKProducerDriver(event)
                    producer_request = event.callback.request_id
                    producer_input_at_start = actual_input
                    stderr_at_start = session.snapshot()['stream']['stderr_bytes']
                    producer.native_owner.start(('/usr/bin/python3','-B',
                        str(here/'trial_sdk_producer_driver_child.py'),'session'),str(here),
                        {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'})
                elif event.kind == 'terminal':
                    assert event.document.take()['result'] == 'ordinary combined mechanism only'
                    terminals += 1
                elif event.kind == 'reply_written':
                    replies.append(event.observation)
                state = native.snapshot()
                if state['wait'] is not None and state['stdout_eof'] and state['stderr_eof']:
                    break
                if status == 'idle' and event.kind == 'idle':
                    time.sleep(.001)
            else:
                raise TimeoutError('combined_work_deadline')
        result['native_progress_during_first_producer'] = first_native_progress
        result['native_stderr_during_large_producer'] = first_stderr
        result['native'] = native.release()
        result['input_actual_bytes'] = actual_input
        result['controller_written_bytes'] = control.ledger()['written_input_bytes']
        assert actual_input == result['controller_written_bytes']
        assert result['partial_user_at_first_handoff'] and first_native_progress and first_stderr
        assert len(rows) == len(replies) == 4 and terminals == 1 and result['native']['released']
        for row,reply in zip(rows,replies):
            assert row['request_sha256'] == reply['request_sha256']
            assert row['first_byte_ns'] == reply['first_byte_ns']
            assert row['deadline_ns'] == reply['deadline_ns']
            assert row['first_byte_ns'] <= reply['completed_ns'] < row['deadline_ns']
        assert all(row['memory_call_attempts'] is None for row in rows)
        result['ok'] = True
    except BaseException as error:
        result['errors'].append(type(error).__name__+':'+str(error)[:100])
    finally:
        for label,owner in [('producer',None if producer is None else producer.native_owner),('native',native)]:
            if owner is None:
                continue
            try:
                if owner.observe_wait() is None:
                    owner.signal(1,int(signal.SIGKILL))
                while owner.observe_wait() is None and time.monotonic() < deadline+.25:
                    time.sleep(.001)
                result[label+'_wait'] = owner.observe_wait()
                result[label+'_cleanup'] = owner.cleanup_after_wait()
            except BaseException as error:
                result['errors'].append(label+'_cleanup:'+type(error).__name__)
        if producer is not None:
            try:
                producer.cancel()
            except BaseException as error:
                result['errors'].append('producer_cancel:'+type(error).__name__)
        if session is not None:
            try:
                result['source_cleanup'] = session.close_sources()
            except BaseException as error:
                result['errors'].append('source_cleanup:'+type(error).__name__)
        try:
            result['fixture_cleanup_ok'] = fixture.doCleanups()
        except BaseException as error:
            result['errors'].append('fixture_cleanup:'+type(error).__name__)
    result['fd_restored'] = set(os.listdir('/proc/self/fd')) == baseline
    result['ok'] = (result['ok'] and not result['errors'] and result['fd_restored']
        and result.get('fixture_cleanup_ok') is True and result.get('source_cleanup') == ())
    return result
