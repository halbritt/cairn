"""J-01: synthetic Python oracle traces, including known compatibility defects."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import test_inbox_recovery as fixture
import test_wake_refusal as wake_fixture


coord = fixture.coordination
GOLDEN = Path(__file__).with_name('fixtures') / 'inbox_characterization_v1.json'
REQUEST_IDS = dict(completion=str(uuid.UUID(int=101)), response=str(uuid.UUID(int=102)))


def invoke(argv, body=''):
    out, err = io.StringIO(), io.StringIO()
    with patch.object(sys, 'argv', [str(fixture.SCRIPT), *argv]), \
            patch.object(sys, 'stdin', io.StringIO(body)), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = coord.main()
    return code, json.loads(out.getvalue()) if out.getvalue() else None, err.getvalue()


class InboxCharacterization(unittest.TestCase):
    # Reuse the existing disposable CLI fixture without inheriting its tests.
    context = fixture.InboxRecovery.context
    replies = fixture.InboxRecovery.replies
    journal = fixture.InboxRecovery.journal
    log = fixture.InboxRecovery.log
    run_context = fixture.InboxRecovery.run_context
    compact_context = fixture.InboxRecovery.compact_context

    def setUp(self):
        fixture.InboxRecovery.setUp(self)
        self.state.update(inbox_completion=REQUEST_IDS['completion'], inbox_response=REQUEST_IDS['response'])
        self.maxDiff = None

    def normalize(self, trace):
        if isinstance(trace, dict):
            return {key: '$TIME' if key in ('recorded_at', 'issued_at', 'compact_issued_at')
                    else self.normalize(value) for key, value in trace.items()}
        if isinstance(trace, (list, tuple)):
            return [self.normalize(value) for value in trace]
        if isinstance(trace, str):
            return trace.replace(str(fixture.SCRIPT), '$COORDINATION').replace(
                str(self.root), '$FIXTURE').replace(sys.executable, '$PYTHON')
        return trace

    def golden(self, name, trace):
        expected = json.loads(GOLDEN.read_text())['traces'][name]
        self.assertEqual(self.normalize(trace), expected)

    def reset_journal(self):
        coord.intent_path(self.config['state_dir'], fixture.ATTEMPT).unlink(missing_ok=True)
        (self.root / 'log.jsonl').unlink(missing_ok=True)

    def test_explicit_final_slot_retries_contact_api_while_watcher_does_not(self):
        context = self.context()
        traces = []
        for first in (fixture.HANDLED, dict(ok=False, status='HOLD_RELEASED')):
            with self.subTest(first=first['status']):
                self.reset_journal()
                self.replies({'complete': [first]})
                initial = self.run_context(context['completion'], 'Selected result')
                original = self.journal()['completion']
                watched = coord.flush_journal(self.config, fixture.ATTEMPT)
                self.assertEqual(len(self.log()), 1)
                retries = []
                for code in ('STALE_SESSION', 'NOT_FOUND', 'PAYLOAD_UNAVAILABLE'):
                    self.replies({'complete': [dict(ok=False, status=code)]})
                    reply = self.run_context(context['completion'], 'Selected result')
                    slot = self.journal()['completion']
                    retries.append(dict(exit=reply.returncode, reply=json.loads(reply.stdout),
                                        status=slot['status'], code=slot['code'], replay_code=slot['replay_code']))
                self.assertTrue(all(entry == self.log()[0] for entry in self.log()))
                changed = self.run_context(context['completion'], 'Changed result')
                self.assertEqual(len(self.log()), 4)
                traces.append(dict(initial=json.loads(initial.stdout), original=original,
                                   watcher_pending=sorted(watched), retries=retries,
                                   changed=dict(exit=changed.returncode, status=json.loads(changed.stdout)['status']),
                                   api_request=self.log()[0], api_sends=len(self.log())))
        self.golden('explicit_final_retries', traces)

    def test_context_complete_and_respond_require_the_exact_committed_result(self):
        context = self.context()
        self.replies({'complete': [fixture.HANDLED], 'publish': [fixture.REPLIED]})
        response = [*context['response'], '--version', '1', fixture.RESULT['record_id']]
        premature = self.run_context(response)
        self.assertEqual(self.log(), [])
        completed = self.run_context(context['completion'], 'Selected result')
        wrong_version = self.run_context([*context['response'], '--version', '2', fixture.RESULT['record_id']])
        self.assertEqual(len(self.log()), 1)
        replied = self.run_context(response)
        self.golden('explicit_context_grammar', dict(
            premature=dict(exit=premature.returncode, status=json.loads(premature.stdout)['status']),
            wrong_version=dict(exit=wrong_version.returncode, status=json.loads(wrong_version.stdout)['status']),
            complete=dict(exit=completed.returncode, output=json.loads(completed.stdout)),
            respond=dict(exit=replied.returncode, output=json.loads(replied.stdout)),
            journal=self.journal(), api=self.log()))

    def test_context_ack_journals_the_issued_acknowledgement_command(self):
        context = self.context()
        self.replies({'ack': [dict(ok=True, status='OK', data=dict(state='handled', attempts=1))]})
        acked = self.run_context(context['acknowledgement'])
        self.golden('explicit_ack_grammar', dict(exit=acked.returncode, output=json.loads(acked.stdout),
                                                journal=self.journal(), api=self.log()))

    def test_final_refusals_and_transport_uncertainty_keep_distinct_recovery_states(self):
        context = self.context()
        cases = [(code, dict(ok=False, status=code)) for code in (
            'STALE_LEASE', 'STALE_SESSION', 'HOLD_RELEASED', 'REQUEST_CANCELLED', 'DEADLINE_EXCEEDED',
            'IDEMPOTENCY_CONFLICT', 'INVALID_REQUEST', 'NOT_FOUND', 'AUTHORITY_DENIED',
            'PAYLOAD_UNAVAILABLE', 'DESTINATION_PROHIBITED', 'STALE_RESOLUTION',
            'API_CONNECTION_FAILED', 'FUTURE_REFUSAL')]
        cases.append(('non_json', dict(hang=True)))
        traces = []
        for name, reply in cases:
            with self.subTest(outcome=name):
                self.reset_journal()
                self.replies({'complete': [reply]})
                first = self.run_context(context['completion'], 'Selected result')
                before = self.journal()['completion']
                with contextlib.redirect_stderr(io.StringIO()):
                    pending = coord.flush_journal(self.config, fixture.ATTEMPT)
                traces.append(dict(outcome=name, exit=first.returncode, status=before['status'],
                                   code=before['code'], watcher_pending=sorted(pending),
                                   api_sends=len(self.log())))
        self.golden('refusal_matrix', traces)

    def test_timeout_stays_pending_and_reports_uncertain_execution(self):
        context = self.context()
        with patch.object(coord.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fixture', 30)):
            code, output, _ = invoke(context['completion'][2:], 'Selected result')
        self.golden('timeout', dict(exit=code, output=output, journal=self.journal()))

    def test_explicit_and_watcher_send_deadlines_are_observable_at_process_boundary(self):
        context = self.context()
        self.replies({'complete': [fixture.LOST, fixture.HANDLED]})
        timeouts = []
        run = subprocess.run

        def observed(argv, **kwargs):
            timeouts.append(kwargs['timeout'])
            return run(argv, **kwargs)

        with patch.object(coord.subprocess, 'run', side_effect=observed):
            code, output, _ = invoke(context['completion'][2:], 'Selected result')
            pending = coord.flush_journal(self.config, fixture.ATTEMPT)
        self.golden('send_deadlines', dict(timeouts=timeouts, explicit_exit=code,
                                          explicit_status=output['status'], watcher_pending=sorted(pending),
                                          journal=self.journal(), api=self.log()))

    def test_real_lock_contention_waits_thirty_seconds_explicitly_and_zero_in_watcher(self):
        context = self.context()
        self.replies({'complete': [fixture.LOST]})
        self.run_context(context['completion'], 'Selected result')
        target = coord.intent_path(self.config['state_dir'], fixture.ATTEMPT)
        elapsed = [0.0]

        def sleep(seconds):
            elapsed[0] += seconds

        with target.with_suffix('.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(coord.time, 'monotonic', side_effect=lambda: elapsed[0]), \
                    patch.object(coord.time, 'sleep', side_effect=sleep):
                code, output, _ = invoke(context['completion'][2:], 'Selected result')
                explicit_wait = round(elapsed[0], 1)
                elapsed[0] = 0
                pending = coord.flush_journal(self.config, fixture.ATTEMPT)
        self.golden('lock_contention', dict(explicit_exit=code, explicit_status=output['status'],
                                           explicit_wait=explicit_wait, watcher_wait=elapsed[0],
                                           watcher_pending=pending, api_sends=len(self.log())))

    def test_finish_reply_persists_both_intents_before_send_and_pins_all_issued_commands(self):
        context = self.context()
        self.replies({'complete': [fixture.HANDLED], 'publish': [fixture.REPLIED]})
        run = subprocess.run
        before_sends = []

        def observed(argv, **kwargs):
            target = coord.intent_path(self.config['state_dir'], fixture.ATTEMPT)
            before_sends.append(dict(journal=self.journal(), file_mode=oct(target.stat().st_mode & 0o777),
                                     directory_mode=oct(target.parent.stat().st_mode & 0o777)))
            return run(argv, **kwargs)

        with patch.object(coord.subprocess, 'run', side_effect=observed):
            code, output, _ = invoke(context['finish_reply'][2:], 'Selected result')
        self.golden('finish_reply', dict(context=context, before_sends=before_sends,
                                        exit=code, output=output, journal=self.journal(), api=self.log()))

    def test_mismatched_reply_success_keeps_pending_until_exact_reference_recovery(self):
        context = self.context()
        wrong = dict(fixture.REPLIED, data=dict(event_id=fixture.EVENT,
                     ref=dict(fixture.RESULT, version=2)))
        self.replies({'complete': [fixture.HANDLED], 'publish': [wrong, fixture.REPLIED]})
        first = self.run_context(context['finish_reply'], 'Selected result')
        before = self.journal()
        pending = coord.flush_journal(self.config, fixture.ATTEMPT)
        self.golden('reply_reference_recovery', dict(exit=first.returncode, output=json.loads(first.stdout),
                                                    before=before, watcher_pending=sorted(pending),
                                                    after=self.journal(), api=self.log()))

    def test_committed_completion_without_result_gets_one_exact_recovery_send(self):
        context = self.context()
        omitted = dict(ok=True, status='OK', data=dict(state='handled'))
        traces = []
        for recovery in (fixture.HANDLED, fixture.LOST):
            with self.subTest(recovery=recovery['status']):
                self.reset_journal()
                self.replies({'complete': [omitted, recovery], 'publish': [fixture.REPLIED]})
                first = self.run_context(context['finish_reply'], 'Selected result')
                before = self.journal()
                pending = coord.flush_journal(self.config, fixture.ATTEMPT)
                self.assertEqual(self.log()[0], self.log()[1])
                traces.append(dict(recovery=recovery['status'], exit=first.returncode,
                                   output=json.loads(first.stdout), before=before, watcher_pending=sorted(pending),
                                   settled_index=coord.settled_journal_index(self.config, self.state),
                                   after=self.journal(), api=self.log()))
        self.golden('missing_result_recovery', traces)

    def test_compact_read_inspect_settle_advance_preserve_handle_and_source_grammar(self):
        self.config['notice_batch_version'] = 1
        self.state['inbox_attempt']['native_turn_id'] = 'native-turn-one'
        with patch.object(coord.os, 'urandom', return_value=b'\x01' * 8), \
                patch.object(coord.uuid, 'uuid4', return_value=uuid.UUID(int=103)):
            context, _ = self.compact_context('notice', 'Selected synthetic notice.')
        handle = context['compact_handle']
        entry = json.loads((Path(self.config['state_dir']) / 'compact' / (handle + '.json')).read_text())
        replies = json.loads((self.root / 'replies.json').read_text())
        replies['session-inbox-advance'] = [dict(ok=True, status='OK', data=dict(attempt=None, remainder=''))]
        self.replies(replies)
        env = dict(os.environ, CAIRN_INBOX_ROOT=str(self.root), CLAUDE_SESSION_ID='native-1')
        outputs = []
        for operation in (['read'], ['inspect'], ['settle', 'handled'], ['advance']):
            command = [sys.executable, str(fixture.SCRIPT), 'inbox', 'compact', operation[0], handle, *operation[1:]]
            run = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
            outputs.append(dict(operation=operation, exit=run.returncode, output=json.loads(run.stdout)))
        self.golden('compact', dict(handle_entry=entry, outputs=outputs, journal=self.journal(), api=self.log()))

    def test_drain_uses_issued_commands_for_its_one_owned_lease(self):
        self.state.pop('inbox_intent')
        delivery = self.state.pop('inbox_attempt')['delivery']
        self.state['process'] = coord.process_reference(os.getpid())
        coord.write_state(self.path, self.state)
        config_path = self.root / 'config.json'
        coord.write_state(config_path, self.config)
        self.replies({'event-next': [dict(ok=True, status='OK', data=dict(delivery=delivery))]})
        with patch.object(coord.uuid, 'uuid4', side_effect=[uuid.UUID(int=101), uuid.UUID(int=102)]):
            code, output, _ = invoke(['inbox', 'drain', '--config', str(config_path),
                                     '--agent-id', fixture.AGENT, '--execution-id', fixture.EXECUTION,
                                     '--lease-seconds', '60'])
        context_path = Path(output['context'])
        context = json.loads(context_path.read_text())
        self.golden('drain', dict(exit=code, output=output, context=context,
                                 mode=oct(context_path.stat().st_mode & 0o777), api=self.log()))

    def test_directory_watcher_sorts_json_bindings_and_contends_on_watch_lock(self):
        directory = self.root / 'bindings'
        directory.mkdir()
        for name in ('codex-harm', 'claude', 'codex-3', 'codex'):
            config = dict(self.config, binding=name, state_dir=str(self.root / name))
            coord.write_state(directory / (name + '.json'), config)
        (directory / 'ignored.txt').write_text('not JSON')
        reads = []
        read_text = Path.read_text

        def observed(path, *args, **kwargs):
            if path.parent == directory and path.suffix == '.json':
                reads.append(path.name)
            return read_text(path, *args, **kwargs)

        with patch.object(Path, 'read_text', observed), patch.object(coord.signal, 'signal'):
            code, output, _ = invoke(['watch', '--config-dir', str(directory), '--once'])
        lock_path = directory / '.watch.lock'
        command = [sys.executable, str(fixture.SCRIPT), 'watch', '--config-dir', str(directory), '--once']
        with lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = subprocess.run(command, capture_output=True, text=True, timeout=5)
        released = subprocess.run(command, capture_output=True, text=True, timeout=5)
        single = subprocess.run([sys.executable, str(fixture.SCRIPT), 'watch', '--config',
                                 str(directory / 'codex.json'), '--once'], capture_output=True, text=True, timeout=5)
        self.golden('watcher', dict(bindings=reads, exit=code, output=output,
                                   lock=str(lock_path), lock_mode=oct(lock_path.stat().st_mode & 0o777),
                                   busy_exit=busy.returncode, busy_stderr=busy.stderr,
                                   released_exit=released.returncode, single_config_exit=single.returncode,
                                   single_config_error=single.stderr.splitlines()[-1], api=self.log()))

    def test_inspection_preserves_files_and_refuses_pending_or_corrupt_index_entries(self):
        context = self.context()
        self.replies({'complete': [fixture.HANDLED]})
        self.run_context(context['completion'], 'Selected result')
        original = self.journal()
        target = coord.intent_path(self.config['state_dir'], fixture.ATTEMPT)
        cases = [('absent', None), ('corrupt', '{'), ('non_object', '[]')]
        for status in ('pending', 'committed', 'refused', 'unknown'):
            journal = dict(original, completion=dict(original['completion'], status=status))
            cases.append((status, json.dumps(journal)))
        traces = []
        for name, body in cases:
            with self.subTest(journal=name):
                target.unlink(missing_ok=True)
                if body is not None:
                    target.write_text(body)
                before = target.read_bytes() if target.exists() else None
                settled = coord.settled_journal_index(self.config, self.state)
                self.assertEqual(target.read_bytes() if target.exists() else None, before)
                traces.append(dict(journal=name, settled=settled))
        self.assertEqual(len(self.log()), 1)
        self.golden('inspection', traces)

    def rejected_wake_with_released_request(self):
        wake = wake_fixture.WakeRefusalTests()
        wake.setUp()
        self.addCleanup(wake.doCleanups)
        wc = wake_fixture.coord
        wake.cc['cairn'] = str(self.cairn)
        wake.mc['cairn'] = str(self.cairn)
        owner = wc.process_reference(os.getppid())
        wake.state['process'] = owner
        wake.state['idle_wake']['parent'] = owner
        current_wake = wake.state.pop('idle_wake')
        wake.state['inbox_journals'] = [fixture.ATTEMPT]
        session = wc.session_ref(wake.state['agent'])
        delivery = self.state['inbox_attempt']['delivery']
        wake.state['inbox_intent'] = dict(request_id=fixture.ATTEMPT, session=session)
        wake.state['inbox_attempt'] = dict(attempt_id=fixture.ATTEMPT, session=session, delivery=delivery)
        wake.event['host_pid'] = owner['pid']
        wake.freeze()
        target, context = coord.inbox_handoff(wake.cc, session, delivery, fixture.ATTEMPT, REQUEST_IDS)
        coord.write_state(target, context)
        journal_path = coord.intent_path(wake.cc['state_dir'], fixture.ATTEMPT)
        self.replies({'complete': [fixture.HANDLED],
                      'session-inbox-reconcile': [dict(ok=True, status='OK', data={})]})
        completed, _, _ = invoke(context['completion'][2:], 'Selected result')
        self.assertEqual(completed, 0)
        with wc.session_lock(wake.path), patch.object(wc.uuid, 'uuid4', return_value=uuid.UUID(int=104)):
            late_reply = wc.flush_journal(wake.cc, fixture.ATTEMPT)
            self.assertTrue(wc.flush_inbox_intents(wake.cc, wake.state))
            self.assertTrue(wc.release_inbox(wake.cc, wake.state, wake.path, 'delivery_completed'))
        self.assertNotIn('inbox_intent', wake.state)
        self.assertNotIn('inbox_attempt', wake.state)
        wake.state['idle_wake'] = current_wake
        wc.write_state(wake.path, wake.state)
        return wake, context, late_reply

    def test_earlier_crashed_reply_is_misclassified_during_rejected_wake_reconciliation(self):
        wake, context, late_reply = self.rejected_wake_with_released_request()
        wc = wake_fixture.coord
        journal_path = coord.intent_path(wake.cc['state_dir'], fixture.ATTEMPT)
        fsync = os.fsync

        class CompletionCrash(BaseException):
            pass

        def crash_after_durable_commit(fd):
            fsync(fd)
            if (os.readlink('/proc/self/fd/' + str(fd)) == str(journal_path.parent)
                    and journal_path.exists()):
                journal = json.loads(journal_path.read_text())
                if journal.get('completion', {}).get('status') == 'committed' and not journal.get('response'):
                    raise CompletionCrash()

        with patch.object(coord.os, 'fsync', side_effect=crash_after_durable_commit), self.assertRaises(CompletionCrash):
            invoke([*context['completion'][2:-1], 'finish-reply'], 'Selected result')
        before = journal_path.read_bytes()
        state_before = wake.path.read_bytes()
        settled = wc.settled_journal_index(wake.cc, wake.state)
        self.assertEqual(journal_path.read_bytes(), before)
        self.assertEqual(wake.path.read_bytes(), state_before)
        event_id = wake.state['idle_wake']['event_id']
        wake_delivery = wake.state['idle_wake']['delivery_id']
        self.replies({
            'event-inspect': [dict(ok=True, status='OK', data=dict(event=dict(event_id=event_id, repo=wake.cc['repo']),
                deliveries=[dict(delivery_id=wake_delivery, consumer=wake.state['agent']['inbox'],
                                 state='pending', attempts=0)]))],
            'session-inbox-ready': [dict(ok=True, status='OK', data=dict(delivery_id=wake_delivery, event_id=event_id))],
            'publish': [fixture.LOST]})
        wc.record_activation_refusal(wake.cc, wake.event, BlockingIOError('synthetic activation veto'))
        after = json.loads(wake.path.read_text())
        self.assertEqual(journal_path.read_bytes(), before)
        operations_before_recovery = [entry['operation'] for entry in self.log()]
        with contextlib.redirect_stderr(io.StringIO()):
            pending = coord.flush_journal(wake.cc, fixture.ATTEMPT)
        self.golden('rejected_wake_discrepancy', dict(
            earlier_attempt=fixture.ATTEMPT, earlier_completion=json.loads(before)['completion']['status'],
            earlier_recovery_before_late_reply=sorted(late_reply), earlier_hold_released=True,
            earlier_finish_reply=bool(json.loads(before).get('finish_reply')),
            earlier_response_present='response' in json.loads(before),
            current_wake_delivery=wake_delivery, current_delivery_state='pending', current_delivery_attempts=0,
            settled_index=settled, wake_reconciled='wake_reconciled' in after,
            wake_retained='idle_wake' in after, earlier_journal_unchanged=True,
            journal_index=after['inbox_journals'], operations_before_recovery=operations_before_recovery,
            recovery_pending=sorted(pending), recovery_status=json.loads(journal_path.read_text())['response']['status'],
            operations_after_recovery=[entry['operation'] for entry in self.log()]))


if __name__ == '__main__':
    unittest.main()
