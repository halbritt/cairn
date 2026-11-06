"""J-01: synthetic Python oracle traces, including known compatibility defects."""
import contextlib
import fcntl
import hashlib
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
import test_installed_lifecycle_bundle as installed_fixture
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
        self.extra_roots = []
        self.maxDiff = None

    def normalize(self, trace):
        if isinstance(trace, dict):
            return {key: '$TIME' if key in ('recorded_at', 'issued_at', 'compact_issued_at')
                    else self.normalize(value) for key, value in trace.items()}
        if isinstance(trace, (list, tuple)):
            return [self.normalize(value) for value in trace]
        if isinstance(trace, str):
            for root, replacement in self.extra_roots:
                trace = trace.replace(str(root), replacement)
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

    def rejected_wake_with_released_request(self, completion_reply=fixture.HANDLED):
        wake = wake_fixture.WakeRefusalTests()
        wake.setUp()
        self.addCleanup(wake.doCleanups)
        self.extra_roots.append((wake.root, '$WAKE_FIXTURE'))
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
        self.replies({'complete': [completion_reply],
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

    def test_v5_explicit_respond_preserves_legacy_shape_and_string_version_checks(self):
        context = self.context()
        self.replies({'complete': [fixture.HANDLED], 'publish': [dict(
            fixture.REPLIED, data=dict(event_id='not-a-uuid', ref=dict(fixture.RESULT, version=9)))]})
        self.run_context(context['completion'], 'Selected result')
        target = coord.intent_path(self.config['state_dir'], fixture.ATTEMPT)
        committed = self.journal()
        cases = [('leading_zero', fixture.RESULT, '01'),
                 ('missing_version', dict(record_id=fixture.RESULT['record_id']), '1'),
                 ('non_dict_result', ['result'], '1'), ('explicit_legacy_shape', fixture.RESULT, '1')]
        traces = []
        for name, result, version in cases:
            coord.write_state(target, dict(committed, completion=dict(committed['completion'], result=result)))
            before = len(self.log())
            run = self.run_context([*context['response'], '--version', version, fixture.RESULT['record_id']])
            traces.append(dict(case=name, exit=run.returncode, output=json.loads(run.stdout),
                               api_sends=len(self.log()) - before, response=self.journal().get('response')))
        self.golden('v5_explicit_respond', traces)

    def test_v5_finish_reply_reports_stale_replay_even_with_both_slots_committed(self):
        context = self.context()
        self.replies({'complete': [fixture.HANDLED], 'publish': [fixture.REPLIED,
                                                               dict(ok=False, status='STALE_SESSION')]})
        first = self.run_context(context['finish_reply'], 'Selected result')
        replay = self.run_context(context['finish_reply'], 'Selected result')
        self.golden('v5_finish_reply_stale_replay', dict(first=json.loads(first.stdout), exit=replay.returncode,
                    output=json.loads(replay.stdout), journal=self.journal(), api=self.log()))

    def compact_run(self, context, *arguments, launcher=None):
        env = dict(os.environ, CAIRN_INBOX_ROOT=str(self.root), CLAUDE_SESSION_ID='native-1')
        if launcher:
            env['PATH'] = str(launcher.parent) + os.pathsep + env.get('PATH', '')
            command = ['cairn-inbox', *arguments, context['compact_handle']]
        else:
            command = [sys.executable, str(fixture.SCRIPT), 'inbox', 'compact',
                       arguments[0], context['compact_handle'], *arguments[1:]]
        return subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)

    def test_v5_bare_compact_launcher_selects_its_installed_root(self):
        with patch.object(coord.os, 'urandom', return_value=b'\x02' * 8):
            context, _ = self.compact_context('notice', 'Launcher source.')
        installer = installed_fixture.coordination
        installer.bundle.stage_runtime(fixture.ROOT / 'integrations/lifecycle', self.root)
        home = self.root / 'account'
        launcher = home / '.local/bin/cairn-inbox'
        with patch.dict(os.environ, PATH=str(launcher.parent) + os.pathsep + os.environ.get('PATH', '')):
            installer.install_compact_inbox_launcher(self.root, home)
        run = self.compact_run(context, 'read', launcher=launcher)
        self.golden('v5_compact_launcher', dict(exit=run.returncode, output=json.loads(run.stdout),
                    script=launcher.read_text(), mode=oct(launcher.stat().st_mode & 0o777), api=self.log()))

    def test_v5_compact_settle_reraises_stale_lease_for_unusable_journals(self):
        with patch.object(coord.os, 'urandom', return_value=b'\x03' * 8):
            context, _ = self.compact_context('notice', 'Retained exact source.')
        target = coord.intent_path(self.config['state_dir'], fixture.ATTEMPT)
        traces = []
        for name, body in [('missing', None), ('corrupt', '{'), ('non_object', '[]')]:
            target.unlink(missing_ok=True)
            if body is not None:
                target.parent.mkdir(exist_ok=True)
                target.write_text(body)
            (self.root / 'log.jsonl').unlink(missing_ok=True)
            self.replies({'event-renew': [dict(ok=False, status='STALE_LEASE')]})
            run = self.compact_run(context, 'settle', 'handled')
            traces.append(dict(case=name, exit=run.returncode, output=json.loads(run.stdout),
                               operations=[entry['operation'] for entry in self.log()]))
        self.golden('v5_compact_stale_settle', traces)

    def test_v5_compact_unconfirmed_ack_reports_confirmation_mismatch(self):
        with patch.object(coord.os, 'urandom', return_value=b'\x04' * 8):
            context, _ = self.compact_context('notice', 'Selected source.')
        replies = json.loads((self.root / 'replies.json').read_text())
        replies['ack'] = [dict(ok=True, status='OK', data=dict(state='failed', attempts=1))]
        self.replies(replies)
        run = self.compact_run(context, 'settle', 'handled')
        self.golden('v5_compact_confirmation', dict(exit=run.returncode, output=json.loads(run.stdout),
                                                  journal=self.journal(), api=self.log()))

    def test_v5_agent_only_seeding_adopts_and_then_prunes_final_other_execution_journals(self):
        directory = Path(self.config['state_dir']) / 'intents'
        directory.mkdir()
        for index, kind in enumerate(('notice', 'response')):
            target = directory / (str(index) + '.json')
            coord.write_state(target, dict(schema='cairn.inbox-intent/1', attempt_id=str(index), kind=kind,
                session=dict(agent_id=fixture.AGENT, execution_id=str(uuid.UUID(int=999))),
                completion=dict(kind='ack', status='committed')))
        coord.write_state(directory / '2.json', dict(attempt_id='2', kind='notice',
            completion=dict(kind='ack', status='committed', argv=[fixture.AGENT])))
        state = dict(agent=self.state['agent'])
        seeded = list(coord.session_journals(self.config, state))
        flushed = coord.flush_inbox_intents(self.config, state, 'all')
        self.golden('v5_agent_only_seeding', dict(seeded=seeded, settled=flushed,
                    remaining=state['inbox_journals'], api=self.log()))

    def test_v5_raw_readers_pin_missing_corrupt_subfield_and_extension_policies(self):
        target = coord.intent_path(self.config['state_dir'], fixture.ATTEMPT)
        target.parent.mkdir()
        base = dict(schema='cairn.inbox-intent/1', attempt_id=fixture.ATTEMPT,
                    session=dict(agent_id=fixture.AGENT, execution_id=fixture.EXECUTION))
        cases = [('missing', None), ('corrupt', '{'), ('non_object', '[]'), ('directory', '__directory__')]
        cases += [('completion_' + name, json.dumps(dict(base, completion=value))) for name, value in (
            ('null', None), ('false', False), ('zero', 0), ('empty_list', []), ('empty_string', ''),
            ('string', 'bad'), ('list', [1]), ('pending', dict(status='pending')),
            ('committed', dict(status='committed')), ('refused', dict(status='refused')))]
        cases += [('extension', json.dumps(dict(base, extension=True))),
                  ('non_dict_session', json.dumps(dict(base, session=['bad']))),
                  ('ownerless_non_dict_slot', json.dumps(dict(attempt_id=fixture.ATTEMPT, completion=['bad']))),
                  ('missing_attempt_id', json.dumps(dict(session=base['session'])))]
        traces = []
        for name, body in cases:
            target.unlink(missing_ok=True)
            if body == '__directory__':
                target.mkdir()
            elif body is not None:
                target.write_text(body)
            row = dict(case=name)
            readers = dict(committed=lambda: coord.journal_committed(self.config, self.state),
                completion_state=lambda: coord.journal_completion_state(self.config, self.state['inbox_attempt']),
                claim_only=lambda: coord.attempt_commands_absent(self.config, self.state['inbox_attempt']),
                settled=lambda: coord.settled_journal_index(self.config, dict(inbox_journals=[fixture.ATTEMPT])),
                seeded=lambda: coord.session_journals(self.config, dict(agent=self.state['agent'])))
            for reader, observe in readers.items():
                try:
                    row[reader] = observe()
                except (AttributeError, KeyError, ValueError, TypeError, OSError) as exc:
                    row[reader] = dict(raises=type(exc).__name__)
            traces.append(row)
            if target.is_dir():
                target.rmdir()
        self.assertEqual(self.log(), [])
        self.golden('v5_reader_policies', traces)

    def hook_state(self):
        self.path = coord.state_path(self.config, 'native-1')
        self.state.update(schema='cairn.native-session/1', workspace=str(self.root),
                          process=coord.process_reference(os.getpid()))
        self.state['agent'].update(context_revision=1, metadata=dict(harness='claude', model='', project='fixture',
            workspace=str(self.root), state='idle', delivery_mode='existing-session'))
        coord.write_state(self.path, self.state)
        config_path = self.root / 'hook.json'
        coord.write_state(config_path, self.config)
        return config_path

    def test_v5_corrupt_seed_and_indexed_non_object_escape_normal_hook_and_watcher(self):
        config_path = self.hook_state()
        directory = Path(self.config['state_dir']) / 'intents'
        directory.mkdir()
        target = directory / (fixture.ATTEMPT + '.json')
        self.replies({'agent-heartbeat': [dict(ok=True, status='OK', data=self.state['agent'])]})
        event = dict(session_id='native-1', cwd=str(self.root), hook_event_name='Stop', host_pid=os.getpid())
        traces = []
        for name, journal, indexed in [('missing_attempt_id', dict(session=dict(agent_id=fixture.AGENT)), False),
                                       ('indexed_non_object', [], True)]:
            for mode in ('hook', 'watch'):
                state = dict(self.state)
                if indexed:
                    state['inbox_journals'] = [fixture.ATTEMPT]
                coord.write_state(self.path, state)
                coord.write_state(target, journal)
                (self.root / 'log.jsonl').unlink(missing_ok=True)
                if mode == 'hook':
                    command = [sys.executable, str(fixture.SCRIPT), 'hook', '--config', str(config_path)]
                else:
                    bindings = self.root / 'watch-bindings'
                    bindings.mkdir(exist_ok=True)
                    coord.write_state(bindings / 'fixture.json', self.config)
                    command = [sys.executable, str(fixture.SCRIPT), 'watch', '--config-dir', str(bindings), '--once']
                run = subprocess.run(command, input=json.dumps(event), capture_output=True, text=True, timeout=30)
                traces.append(dict(case=name, mode=mode, exit=run.returncode, stdout=run.stdout,
                    error=run.stderr.splitlines()[-1], traceback=run.stderr.startswith('Traceback'),
                    operations=[entry['operation'] for entry in self.log()]))
        self.golden('v5_hook_watch_corruption', traces)

    def activation_fixture(self):
        wake = wake_fixture.WakeRefusalTests()
        wake.setUp()
        self.addCleanup(wake.doCleanups)
        self.extra_roots.append((wake.root, '$WAKE_FIXTURE'))
        wc = wake_fixture.coord
        owner = wc.process_reference(os.getpid())
        wake.state['process'] = wake.state['idle_wake']['parent'] = owner
        wake.event['host_pid'] = owner['pid']
        wake.cc['cairn'] = wake.mc['cairn'] = str(self.cairn)
        wake.freeze()
        return wake

    def test_v5_activation_refusal_records_receipt_and_classifies_seed_exceptions(self):
        traces = []
        for name, journal in [('empty', None), ('missing_attempt_id', 'missing_id'),
                              ('non_dict_session', dict(attempt_id=fixture.ATTEMPT, session=['bad'])),
                              ('ownerless_non_dict_slot', dict(attempt_id=fixture.ATTEMPT, completion=['bad']))]:
            wake = self.activation_fixture()
            if journal == 'missing_id':
                journal = dict(session=dict(agent_id=wake.state['agent']['agent_id']))
            if journal is not None:
                coord.write_state(coord.intent_path(wake.cc['state_dir'], fixture.ATTEMPT), journal)
            manifest = json.loads(wake.binding.read_text())
            manifest['enabled'] = 'malformed activation state'
            coord.write_state(wake.binding, manifest)
            (self.root / 'log.jsonl').unlink(missing_ok=True)
            self.replies({'event-inspect': [dict(ok=True, status='OK', data=dict(event={}, deliveries=[]))]})
            run = subprocess.run([sys.executable, str(fixture.SCRIPT), 'hook', '--config',
                                  str(wake.root / 'coordination.json')], input=json.dumps(wake.event),
                                 capture_output=True, text=True, timeout=30)
            state = json.loads(wake.path.read_text())
            receipt = json.loads((wake.path.parent / 'wake-refusals' / (state['wake_rejection'] + '.json')).read_text())
            digest = hashlib.sha256(json.dumps(receipt, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            self.assertEqual(digest, state['wake_rejection'])
            self.assertEqual(receipt['process'], {k: wake.state['process'][k] for k in ('pid', 'start', 'boot')})
            traces.append(dict(case=name, exit=run.returncode, stdout=run.stdout,
                diagnostics=[line for line in run.stderr.splitlines() if line.startswith('Cairn coordination')],
                error=run.stderr.splitlines()[-1], traceback='Traceback' in run.stderr,
                receipt=dict(schema=receipt['schema'], phase=receipt['phase'], error_class=receipt['error_class'],
                             native_prompt_id=receipt['native_prompt_id'], process_matches_owner=True,
                             wake_delivery=receipt['wake']['delivery_id']),
                operations=[entry['operation'] for entry in self.log()]))
        self.golden('v5_activation_refusal', traces)

    def test_v5_watcher_reconciles_while_missing_result_replays_every_cycle(self):
        omitted = dict(ok=True, status='OK', data=dict(state='handled'))
        wake, context, _ = self.rejected_wake_with_released_request(completion_reply=omitted)
        wc = wake_fixture.coord
        self.replies({'complete': [omitted]})
        code, _, _ = invoke([*context['completion'][2:-1], 'finish-reply'], 'Selected result')
        self.assertEqual(code, 7)
        wake.cc['idle_wakeup'] = str(self.root / 'unused-herdr')
        wake.freeze()
        event_id = wake.state['idle_wake']['event_id']
        delivery = wake.state['idle_wake']['delivery_id']
        inspected = dict(event=dict(event_id=event_id, repo=wake.cc['repo']), deliveries=[dict(
            delivery_id=delivery, consumer=wake.state['agent']['inbox'], state='pending', attempts=0)])
        uncertain = dict(inspected, deliveries=[dict(inspected['deliveries'][0], attempts=1)])
        self.replies({'complete': [omitted],
            'event-inspect': [dict(ok=True, status='OK', data=uncertain), dict(ok=True, status='OK', data=inspected)],
            'session-inbox-ready': [dict(ok=True, status='OK', data=dict(delivery_id=delivery, event_id=event_id)),
                                    dict(ok=True, status='OK', data=dict(delivery_id=None))]})
        wc.record_activation_refusal(wake.cc, wake.event, BlockingIOError('synthetic activation veto'))
        wake.state = json.loads(wake.path.read_text())
        self.assertIn('idle_wake', wake.state)
        cycles = []
        for _ in range(3):
            with wc.session_lock(wake.path):
                wc.watch_inbox(wake.cc, wake.state, wake.path)
                prepared = wc.prepare_idle_wake(wake.cc, wake.state, wake.path)
            journal = json.loads(coord.intent_path(wake.cc['state_dir'], fixture.ATTEMPT).read_text())
            cycles.append(dict(prepared=prepared, result=journal['completion'].get('result'),
                               response_present='response' in journal, completion_status=journal['completion']['status'],
                               settled_index=wc.settled_journal_index(wake.cc, wake.state),
                               wake_reconciled='wake_reconciled' in wake.state, wake_retained='idle_wake' in wake.state))
        self.golden('v5_prepare_idle_persistent_ref', dict(cycles=cycles,
                    operations=[entry['operation'] for entry in self.log()]))

    def test_v5_native_settlement_distinguishes_classified_unsettled_and_escaping_failures(self):
        traces = []
        for name, body in [('transport_pending', 'pending'), ('corrupt', '{'), ('non_object', '[]')]:
            for strict in (False, True):
                context = self.context()
                target = coord.intent_path(self.config['state_dir'], fixture.ATTEMPT)
                if body == 'pending':
                    self.replies({'complete': [fixture.LOST]})
                    self.run_context(context['completion'], 'Selected result')
                else:
                    target.parent.mkdir(exist_ok=True)
                    target.write_text(body)
                state = json.loads(json.dumps(self.state))
                error = None
                with contextlib.redirect_stderr(io.StringIO()):
                    try:
                        coord.native_session(self.config, state, self.path).settle('evidence', dict(settlement='pending'), strict=strict)
                    except AttributeError as exc:
                        error = type(exc).__name__
                persisted = json.loads(self.path.read_text())
                traces.append(dict(case=name, strict=strict, raises=error,
                    settlement=state['evidence']['settlement'], persisted_settlement=persisted['evidence']['settlement'],
                    ended=persisted.get('inbox_turn_ended'), delivered=persisted['delivered_since_idle']))
                target.unlink()
        self.golden('v5_settlement_taxonomy', traces)

    def test_v5_admitted_recall_wake_preserves_compact_utf8_stdout(self):
        wake = wake_fixture.claude_fixture.ClaudeInboxRecallTests()
        wake.setUp()
        self.addCleanup(wake.doCleanups)
        self.extra_roots.append((wake.root, '$WAKE_FIXTURE'))
        wake.cc['cairn'] = wake.mc['cairn'] = str(self.cairn)
        source = fixture.FAKE.replace("argv[-1] if argv[0] == 'agent'",
            "argv[9] if argv[0] == 'agent' and argv[5] == '--agent-id' else argv[5] if argv[0] == 'agent'")
        self.cairn.write_text(source.replace("queue = replies.get(operation)",
                                             "assert operation in replies, operation\nqueue = replies.get(operation)"))
        wake.process = coord.process_reference(os.getpid())
        wake.state['process'] = wake.process
        wake.event['host_pid'] = os.getpid()
        body = 'Golden source: \u2028\u2029 <>& 1.25 日本語.'
        version = wake.fixture['history']['versions'][0]
        version.update(body=body, body_bytes=len(body.encode()), body_sha256=hashlib.sha256(body.encode()).hexdigest())
        wake.freeze()
        self.replies({
            'agent-heartbeat': [dict(ok=True, status='OK', data=wake.state['agent'])],
            'session-inbox-reconcile': [dict(ok=False, status='DELIVERY_ACTIVE')],
            'event-renew': [dict(ok=True, status='OK', data=wake.delivery)],
            'history': [dict(ok=True, status='OK', data=wake.fixture['history'])],
            'search': [dict(ok=True, status='OK', data=wake.fixture['search'])],
            'pull': [dict(ok=True, status='OK', data=wake.fixture['pull'])]})
        run = subprocess.run([sys.executable, str(fixture.SCRIPT), 'hook', '--config',
                              str(wake.root / 'coordination.json')], input=json.dumps(wake.event),
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('\u2028\u2029', run.stdout)
        self.assertIn('<>&', run.stdout)
        self.assertEqual(run.stdout, json.dumps(json.loads(run.stdout), ensure_ascii=False, separators=(',', ':')) + '\n')
        self.golden('v5_recall_wake_stdout', dict(exit=run.returncode, stdout=run.stdout, stderr=run.stderr,
                    operations=[entry['operation'] for entry in self.log()]))

    def test_v5_finish_reply_materialized_identity_conflict_uses_exit_seven(self):
        context = self.context()
        self.replies({'complete': [fixture.HANDLED], 'publish': [fixture.REPLIED]})
        self.run_context(context['finish_reply'], 'Selected result')
        journal = self.journal()
        journal['response']['expected_ref']['version'] = 2
        coord.write_state(coord.intent_path(self.config['state_dir'], fixture.ATTEMPT), journal)
        run = self.run_context(context['finish_reply'], 'Selected result')
        self.golden('v5_materialized_identity_conflict', dict(exit=run.returncode, output=json.loads(run.stdout),
                                                            api_sends=len(self.log())))

    def scripted_exit_cli(self):
        source = fixture.FAKE.replace("if reply.get('hang'):",
            "if 'raw_stdout' in reply:\n    sys.stdout.write(reply['raw_stdout'])\n"
            "    sys.stderr.write(reply.get('stderr', ''))\n    sys.exit(reply['exit'])\nif reply.get('hang'):")
        source = source.replace("sys.exit(0 if reply['ok'] else 4)",
                                "sys.stderr.write(reply.get('stderr', ''))\nsys.exit(reply.get('exit', 0 if reply['ok'] else 4))")
        source = source.replace("print(json.dumps(dict(schema='cairn.response/1', **reply)))",
            "print(json.dumps(dict(schema='cairn.response/1', **{k: v for k, v in reply.items() if k not in ('exit', 'stderr')})))")
        self.cairn.write_text(source)

    def test_v5_single_slot_preserves_inner_streams_and_nonstandard_exit_status(self):
        context = self.context()
        self.scripted_exit_cli()
        traces = []
        for name, reply in [('non_json_zero', dict(raw_stdout='not JSON\n', stderr='CLI diagnostic\n', exit=0)),
                            ('ok_nonzero', dict(ok=True, status='OK', data={}, stderr='CLI diagnostic\n', exit=3))]:
            self.reset_journal()
            self.replies({'complete': [reply]})
            run = self.run_context(context['completion'], 'Selected result')
            traces.append(dict(case=name, exit=run.returncode, stdout=run.stdout, stderr=run.stderr,
                               status=self.journal()['completion']['status'], code=self.journal()['completion']['code']))
        self.golden('v5_passthrough_streams', traces)

    def test_v5_compact_settle_preserves_each_inner_failure_exit(self):
        with patch.object(coord.os, 'urandom', return_value=b'\x05' * 8):
            context, _ = self.compact_context('notice', 'Selected source.')
        self.scripted_exit_cli()
        original = json.loads((self.root / 'replies.json').read_text())
        traces = []
        for code in (1, 2, 3, 4, 7):
            self.reset_journal()
            self.replies(dict(original, ack=[dict(ok=False, status='FUTURE_REFUSAL', exit=code)]))
            run = self.compact_run(context, 'settle', 'handled')
            traces.append(dict(inner_exit=code, exit=run.returncode, output=json.loads(run.stdout),
                               status=self.journal()['completion']['status']))
        self.golden('v5_compact_inner_exits', traces)

    def test_v5_inbox_character_limit_differs_from_raw_hook_byte_limit(self):
        context = self.context()
        self.replies({'complete': [fixture.HANDLED]})
        body = 'é' * (1024 * 1024)
        accepted = self.run_context(context['completion'], body)
        self.assertEqual(self.log()[0]['input'], body)
        oversized = self.run_context(context['completion'], body + 'é')
        config = self.root / 'hook.json'
        coord.write_state(config, self.config)
        hook = subprocess.run([sys.executable, str(fixture.SCRIPT), 'hook', '--config', str(config)],
                              input=body, capture_output=True, text=True, timeout=30)
        self.golden('v5_input_limits', dict(characters=len(body), bytes=len(body.encode()),
                    accepted_exit=accepted.returncode, oversized_exit=oversized.returncode,
                    oversized_status=json.loads(oversized.stdout)['status'], api_sends=len(self.log()),
                    hook_exit=hook.returncode, hook_stdout=hook.stdout, hook_stderr=hook.stderr))

    def test_v5_flush_attempt_and_all_replay_the_same_historical_pending_journal(self):
        context = self.context()
        self.replies({'complete': [fixture.LOST]})
        self.run_context(context['completion'], 'Selected result')
        active = str(uuid.UUID(int=202))
        self.state['inbox_attempt'] = dict(attempt_id=active)
        self.state['inbox_journals'] = [fixture.ATTEMPT, active]
        outcomes = []
        with contextlib.redirect_stderr(io.StringIO()):
            for scope in ('attempt', 'all'):
                settled = coord.flush_inbox_intents(self.config, self.state, scope)
                outcomes.append(dict(scope=scope, settled=settled, journal_index=list(self.state['inbox_journals'])))
        self.assertTrue(all(entry == self.log()[0] for entry in self.log()))
        self.golden('v5_flush_scopes', dict(outcomes=outcomes, api_sends=len(self.log()), request=self.log()[0]))


if __name__ == '__main__':
    unittest.main()
