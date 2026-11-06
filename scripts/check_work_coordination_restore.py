#!/usr/bin/env python3
"""One older physical DB restore containing genuine waits and native notice holds.

All coordination state is created by actual CLI/API/native ingress. SQL only
observes exact rows. The only model boundary is the local scripted Responses
server. Supply an explicit Cairn executable and complete native package manifest;
this script never builds or installs either. It owns its disposable cluster and
retains stopped private runtime diagnostics on failure.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys
import tempfile
import threading

sys.dont_write_bytecode = True
from disposable_host import DisposableHost, OwnedCluster, checked, require
from check_work_wait_native import WaitFixture, new_id, session, ref
from codex_loopback_fixture import message_item, function_call_item, exec_tool_item
import check_codex_organic_native as native

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fixture_inputs():
    paths = [*sorted((ROOT / 'scripts').glob('*.py')),
             *sorted((ROOT / 'integrations/lifecycle').glob('*.py'))]
    return {str(path.relative_to(ROOT)): digest(path) for path in paths}


def native_inputs(manifest):
    selected = json.loads(manifest.read_text())
    return {'manifest_sha256': digest(manifest),
            'binary_sha256': digest(Path(selected['binary_path'])),
            'code_mode_host_sha256': digest(Path(selected['code_mode_host']['path']))}


class RestoreController:
    def __init__(self, cluster, report, report_directory):
        self.cluster, self.report = cluster, report
        self.report_directory = report_directory
        self.mode = 'idle'
        self.held = threading.Event()
        self.release = threading.Event()
        self.fresh_done = threading.Event()
        self.resumed = None
        self.tool = None
        self.action = 'read'
        self.sequence = 0

    def record(self, name, value):
        self.report[name] = value
        (self.report_directory / 'report-running.json').write_text(json.dumps(self.report, indent=2) + '\n')

    def response(self, request, _results):
        if self.mode == 'hold':
            self.old_attempt = dict(self.ctx['saved']()['inbox_attempt'])
            require(self.old_attempt['admission_kind'] == 'organic' and not self.old_attempt.get('finished_at'),
                    'native observation did not hold a genuine unfinished organic attempt')
            require(self.old_attempt['delivery']['event']['event_id'] == self.held_event['event_id'],
                    'native admission selected a different event')
            self.held.set()
            require(self.release.wait(45), 'controller did not release paused local provider within 45 seconds')
            return message_item('msg_' + new_id(), 'Checkpoint captured; end the owned fixture turn.')
        if self.mode != 'fresh':
            return message_item('msg_' + new_id(), 'Local restore fixture turn complete.')
        if self.tool:
            outputs = [item for item in request.get('input', []) if isinstance(item, dict)
                       and item.get('call_id') == self.tool and item.get('type') in
                       ('custom_tool_call_output', 'function_call_output')]
            require(len(outputs) == 1, 'fresh native tool result absent')
            text = json.dumps(outputs[0])
            require('exit code 0' in text.lower() or '"exit_code": 0' in text or '\\"exit_code\\":0' in text,
                    'fresh native tool failed: ' + text[:1200])
            if self.action == 'read':
                require(self.fresh_body in text, 'fresh native exact source body absent')
                self.action = 'settle'
            elif self.action == 'settle':
                self.action = 'advance'
            else:
                require('empty' in text, 'fresh native advance did not end empty')
                self.mode = 'idle'
                self.fresh_done.set()
                return message_item('msg_' + new_id(), 'Fresh notice handled after physical recovery.')
        current = self.ctx['saved']()['inbox_attempt']
        require(current['admission_kind'] == 'organic' and current['batch_index'] == 1,
                'fresh native turn did not own first organic batch item')
        context = json.loads((self.ctx['state_dir'] / 'inbox' / (current['attempt_id'] + '.json')).read_text())
        require(context['event_id'] == self.fresh_event['event_id'], 'fresh turn selected an old event')
        if self.action == 'read':
            require(self.fresh_body in json.dumps(request), 'fresh source not delivered to native context')
            self.report['fresh_native_item'] = {key: context[key] for key in
                ('event_id', 'attempt_id', 'delivery_id', 'lease_id', 'source')}
            self.report['fresh_native_item']['native_turn_id'] = current['native_turn_id']
        argv = [sys.executable, str(self.ctx['engine_root'] / 'coordination.py'),
                'inbox', 'compact', self.action, context['compact_handle']]
        if self.action == 'settle':
            argv.append('handled')
        self.sequence += 1
        self.tool = 'restore_tool_' + str(self.sequence)
        command = shlex.join(argv)
        if any(tool.get('name') == 'exec_command' for tool in request.get('tools', []) if isinstance(tool, dict)):
            return function_call_item(self.tool, 'exec_command', {'cmd': command, 'max_output_tokens': 3000})
        return exec_tool_item(self.tool, command)

    def wait_native(self, predicate, label, *, terminal=None, timeout=35):
        return native.wait_for(lambda: predicate() or self.ctx['server'].failures,
                               label, timeout=timeout, terminal=terminal or self.ctx['terminal'])

    def assert_provider(self):
        require(not self.ctx['server'].failures, 'local scripted provider failed: ' + str(self.ctx['server'].failures))

    def snapshot(self):
        host = self.ctx['host']
        waits = ','.join("'" + row['wait_uuid'] + "'" for row in self.waits)
        events = ','.join("'" + event['event_id'] + "'" for event in (self.held_event, self.forgotten_event))
        attempt_id = self.old_attempt['attempt_id']
        return json.loads(host.sql(f"""SELECT json_build_object(
          'generation',(SELECT generation FROM cairn.retrieval_generation WHERE singleton),
          'waits',(SELECT json_agg(to_jsonb(w) ORDER BY wait_uuid) FROM cairn.agent_wait w WHERE wait_uuid IN ({waits})),
          'outboxes',(SELECT json_agg(to_jsonb(o) ORDER BY wait_uuid) FROM cairn.agent_wait_outbox o WHERE wait_uuid IN ({waits})),
          'notices',(SELECT json_agg(to_jsonb(n) ORDER BY event_id) FROM cairn.agent_organic_notice n WHERE event_id IN ({events})),
          'native_attempt',(SELECT to_jsonb(a) FROM cairn.agent_session_attempt a WHERE attempt_id='{attempt_id}'),
          'native_delivery',(SELECT to_jsonb(d) FROM cairn.agent_delivery d JOIN cairn.agent_session_attempt a USING(delivery_id) WHERE attempt_id='{attempt_id}')
        )""", limit=100_000))

    def prepare_waits(self):
        host = self.ctx['host']
        host.operator('work', args=('features', 'enable', '--feature', 'coordination-waits/1', '--request-id', new_id()))
        self.wait_fixture = WaitFixture(host)
        self.wait_fixture.register('alice')
        self.wait_fixture.register('bob')
        resolved = []
        for predicate in ('request_response', 'response_group_closed'):
            event = self.wait_fixture.request(predicate)
            wait = self.wait_fixture.create_wait(event, predicate)
            self.wait_fixture.respond(event, wait, predicate)
            resolved.append((event, wait))
        drain = host.operator('wait-outbox-drain', {'repo': host.repo, 'limit': 1})
        self.report['initial_one_item_drain'] = drain
        states = [(event, wait, self.wait_fixture.get_wait(wait)) for event, wait in resolved]
        published = [(event, wait) for event, wait, state in states if state['outbox_state'] == 'published']
        pending = [(event, wait) for event, wait, state in states if state['outbox_state'] == 'pending']
        require(len(published) == len(pending) == 1, 'one-item drain did not leave one resolved pending outbox')
        self.published_event, self.w3 = published[0]
        self.pending_event, self.w2 = pending[0]
        self.publication = self.wait_fixture.published(self.w3)
        require(self.publication['delivery']['state'] == 'pending' and self.publication['delivery']['attempts'] == 0,
                'published wait notice was already claimed')
        self.waiting_event = self.wait_fixture.request('request_response')
        self.w1 = self.wait_fixture.create_wait(self.waiting_event, 'request_response')
        self.wait_fixture.settle_next('alice', self.waiting_event)
        require(self.wait_fixture.get_wait(self.w1)['state'] == 'waiting', 'handling task request satisfied W1')
        self.waits = [self.w1, self.w2, self.w3]
        self.report['wait_identities'] = {'waiting': self.w1, 'resolved_pending': self.w2, 'published': self.w3,
                                          'published_notice': self.publication}

    def __call__(self, context):
        self.ctx = context
        host, terminal = context['host'], context['terminal']
        context['extension']['handler'] = self.response
        try:
            self.prepare_waits()
            self.held_event = context['publish']('Eligible held notice survives source recovery.')
            self.forgotten_body = 'FORGOTTEN_QUIET_SOURCE_' + new_id()
            self.forgotten_event = context['publish'](self.forgotten_body)
            require(host.sql('SELECT count(*) FROM cairn.agent_organic_notice WHERE state=\'queued\'') == '2',
                    'two genuine quiet publications not retained')
            self.mode = 'hold'
            terminal.submit('Hold the selected notice while the operator takes a restore checkpoint.')
            self.wait_native(self.held.is_set, 'genuine admitted organic hold')
            self.assert_provider()
            require(self.held.is_set(), 'native provider did not observe the hold')
            state = context['saved']()
            self.old_agent = dict(state['agent'])
            self.old_session = session(self.old_agent)
            self.old_native_id = self.old_agent['native_session_id']
            signed = next(row for row in context['observations_for']('UserPromptSubmit')
                          if row.get('turn_id') == self.old_attempt['native_turn_id'])
            old_claim = {'request_id': new_id(), 'session': self.old_session,
                         'native_owner_turn_id': self.old_attempt['native_turn_id'],
                         'owner_input_origin': signed['owner_input_origin'],
                         'owner_input_attestation': signed['owner_input_attestation']}
            duplicate = host.agent('worker', 'session-organic-notice-claim', old_claim)
            require(duplicate['attempt']['attempt_id'] == self.old_attempt['attempt_id'], 'same-root receipt changed hold identity')
            self.report['old_native_identity'] = {'session': self.old_session, 'native_session_id': self.old_native_id,
                'attempt_id': self.old_attempt['attempt_id'], 'root_id': self.old_attempt['organic_root'],
                'claim_request_id': old_claim['request_id'], 'native_turn_id': self.old_attempt['native_turn_id']}
            control_scope = {'repo': 'fixture:physical-restore-control:' + new_id(), 'task_id': 't', 'run_id': 'r'}
            control = host.operator('create', {'request_id': new_id(), 'draft': {'kind': 'note',
                'body': 'Independent retained physical restore control.', 'scope': control_scope,
                'claim_type': 'self', 'sensitivity': 'shareable'}})
            control_receipt = host.operator('compile', {'request_id': new_id(), 'scope': control_scope,
                'query': '', 'purpose': 'context', 'available_tokens': 64000})
            checkpoint = host.operator('checkpoint', {'request_id': new_id(), 'export_id': 'coordination-held.dump'})
            before = self.snapshot()
            require(before['native_attempt']['finished_at'] is None and before['native_delivery']['state'] == 'leased',
                    'checkpoint did not contain a live admitted native hold')
            require(sorted(row['state'] for row in before['notices']) == ['admitted', 'queued'],
                    'checkpoint notice states differ from admitted+queued')
            backup = host.home / 'coordination-held.dump'
            checked([self.cluster.pg_bin / 'pg_dump', '--format=custom', '--file', backup, host.dsn],
                    'actual five-state pg_dump', timeout=120)
            self.record('pre_backup_snapshot', before)
            self.report['backup'] = {'sha256': digest(backup), 'checkpoint': checkpoint, 'bytes': backup.stat().st_size}
            self.report['stages'].append('actual_backup_with_three_wait_states_and_genuine_organic_hold')
            self.mode = 'idle'
            self.release.set()
            terminal.close()
            require(terminal.process.poll() is not None, 'original native launcher exit unobserved')
            self.report['old_native_exit_code'] = terminal.process.returncode

            # Forget an actual queued notice source after the backup. The held
            # notice source remains eligible, so deletion cannot hide its fence.
            source = self.forgotten_event['ref']
            preview = host.operator('preview-delete', args=(source['record_id'],))
            deletion = host.operator('forget', {'request_id': new_id(), 'record_id': source['record_id'],
                'expected_version': source['version'], 'grant_id': context['root_grant']['grant_id'],
                'preview_id': preview['preview_id']})
            host.operator('purge-deletion', args=(deletion['deletion_id'],))
            host.agent('sender', 'history', source, expected='PAYLOAD_UNAVAILABLE', forbidden=self.forgotten_body)
            witness = host.home / 'post-backup-coordination-recovery.json'
            exported = host.operator('recovery-export', args=(witness,))
            require(self.forgotten_body not in witness.read_text(), 'recovery witness copied forgotten notice text')
            require(host.operator('recovery-inspect', args=(witness,))['consistent'], 'live witness is inconsistent')
            recovery = json.loads(witness.read_text())
            self.report['witness'] = {'sha256': exported['sha256'], 'source_ref': source,
                                      'deletion_id': deletion['deletion_id']}
            require(host.stop_server() == 0, 'old API exit unobserved')
            require(host.process is None, 'old API handle still active')
            checked([self.cluster.pg_bin / 'dropdb', '-h', self.cluster.socket, host.database],
                    'drop only fixture-owned database', timeout=30)
            require(self.cluster.create_database(host.database) == host.dsn, 'restored database DSN changed')
            checked([self.cluster.pg_bin / 'pg_restore', '--no-owner', '--no-privileges', '--dbname', host.dsn, backup],
                    'actual older five-state pg_restore', timeout=120)
            require(self.snapshot() == before, 'actual old dump did not revive exact pre-backup coordination rows')
            gap = host.operator('recovery-inspect', args=(witness,), expected='INTEGRITY_FAILURE')
            require(gap['data']['gaps'], 'old backup did not expose missing post-backup source witness')
            restore = host.operator('begin-restore', {'request_id': new_id(), 'target': backup.name,
                'reason': 'Recover real waits and native notice capabilities from older backup'})
            require(restore['generation'] > before['generation'], 'begin-restore did not fence old generation')
            host.start_server()
            host.agent('sender', 'history', self.held_event['ref'], expected='RESTORE_PAUSED')
            apply_args = ('--request-id', new_id(), '--expected-sha256', exported['sha256'], '--reason',
                          'Retain actual queued-notice source deletion after older restore', str(witness))
            applied = host.operator('recovery-reapply', args=apply_args)
            require(host.operator('recovery-reapply', args=apply_args) == applied, 'recovery exact retry changed result')
            pending_deletions = host.sql("SELECT DISTINCT deletion_id FROM cairn.deletion_effect WHERE status IN ('pending','running','failed') ORDER BY deletion_id").splitlines()
            for deletion_id in pending_deletions:
                host.operator('purge-deletion', args=(deletion_id,))
            pages, after = [], ''
            for _ in range(20):
                request = {'request_id': new_id(), 'session_id': restore['session_id'], 'limit': 1}
                if after:
                    request['after'] = after
                page = host.operator('rebuild-restore', request)
                require(host.operator('rebuild-restore', request) == page, 'rebuild page retry changed result')
                pages.append(page)
                if page['complete']:
                    break
                require(page.get('next_after') and page['next_after'] != after, 'rebuild cursor made no progress')
                after = page['next_after']
            require(pages[-1]['complete'], 'bounded restore rebuild did not complete')
            verification = {'session_id': restore['session_id'], 'checkpoint': {
                'checkpoint_id': checkpoint['checkpoint_id'], 'expected_sha256': checkpoint['sha256'],
                'expected_export_id': checkpoint['export_id']}, 'recovery': recovery,
                'fixtures': [{'receipt_id': control_receipt['receipt_id'], 'query': ''}]}
            verified = host.operator('verify-restore', verification)
            require(verified['ready'], 'actual restored coordination database not ready')
            resumed = host.operator('resume-restore', {'request_id': new_id(), 'verification': verification,
                'policy': 'local-restore/1', 'reason': 'Resume after exact generation fence and source recovery'})
            require(not host.operator('restore-status')['paused'], 'resume left admission paused')
            host.agent('sender', 'history', source, expected='PAYLOAD_UNAVAILABLE', forbidden=self.forgotten_body)
            held_history = host.agent('sender', 'history', self.held_event['ref'])
            require(held_history['versions'][0]['body'] == 'Eligible held notice survives source recovery.',
                    'held source became unavailable and could mask generation fencing')
            control_history = host.operator('history', ref(control))
            require(control_history['versions'][0]['body'] == 'Independent retained physical restore control.',
                    'unrelated retained source changed')
            self.record('restore', {'session': restore, 'reapply': applied, 'rebuild_pages': pages,
                                            'verification': verified, 'resume': resumed})
            self.report['stages'].append('actual_old_restore_notice_source_witness_reapply_rebuild_verify_resume')

            old_replay = host.agent('worker', 'session-organic-notice-claim', old_claim, expected='STALE_SESSION')
            require('restore' in old_replay['message'] or 'generation' in old_replay['message'],
                    'old signed claim refused for a reason other than restore generation')
            host.agent('worker', 'session-organic-notice-advance', {'request_id': new_id(),
                'session': self.old_session, 'attempt_id': self.old_attempt['attempt_id'],
                'root_id': self.old_attempt['organic_root'], 'version': 1}, expected='STALE_SESSION')
            reconciled = host.agent('worker', 'session-inbox-reconcile', {'request_id': new_id(),
                'session': self.old_session, 'attempt_id': self.old_attempt['attempt_id'], 'reason': 'process_exited'})
            require(reconciled['attempt_id'] == self.old_attempt['attempt_id'] and reconciled['finished_at']
                    and reconciled['reason'] == 'process_exited' and reconciled['delivery']['state'] == 'failed',
                    'restored old hold was not closed exactly as failed after observed process exit')
            self.report['restored_hold_cleanup'] = reconciled
            self.wait_fixture.register('alice')
            self.wait_fixture.register('bob')
            w1, w2, w3 = [self.wait_fixture.get_wait(wait) for wait in self.waits]
            require(w1['state'] == 'restore_fenced' and w1['terminal_reason'] == 'restore_fenced', 'W1 not fenced')
            require(w2['state'] == 'satisfied' and w2['outbox_state'] == 'restore_fenced', 'W2 pending outbox revived')
            require(w3['state'] == 'satisfied' and w3['outbox_state'] == 'published', 'W3 publication identity lost')
            self.wait_fixture.empty_all_paths()
            sweep = host.operator('schedule-serve', args=('--repo', host.repo, '--once'))
            require(sweep['published_notices'] == 0, 'old fenced outbox republished')
            self.wait_fixture.empty_all_paths()
            old_publication = self.wait_fixture.published(self.w3)
            require(old_publication == self.publication, 'published unclaimed wait notice was changed/claimed')
            no_old_pending_source = host.sql("SELECT count(*) FROM cairn.memory_record WHERE record_id='" + self.w2['reserved_source_record_uuid'] + "'")
            require(no_old_pending_source == '0', 'old pending outbox materialized a source')
            fenced_notice = host.sql("SELECT state FROM cairn.agent_organic_notice WHERE event_id='" + self.forgotten_event['event_id'] + "'")
            require(fenced_notice == 'restore_fenced', 'old queued organic notice revived')
            self.record('post_restore_snapshot', self.snapshot())
            self.report['stages'].append('old_wait_admission_paths_and_exact_old_native_hold_fenced')

            self.mode = 'idle'
            # Native 0.166 rejects permission overrides on remote resume. Keep
            # the original task's saved permissions instead of repeating the
            # fresh-launch -a/-s choices (tui/src/app/startup.rs:657).
            resume_argv = list(context['native_argv'])
            for flag in ('-a', '-s'):
                index = resume_argv.index(flag)
                del resume_argv[index:index + 2]
            self.resumed = native.Terminal(resume_argv + ['resume', self.old_native_id],
                                           context['native_env'], context['native_work'])
            # The composer placeholder appears during startup too. Wait for
            # the loaded banner before typing; startup draft replay must remain
            # unknown-origin in native Core. Resume registration follows input.
            self.wait_native(lambda: b'Ask Codex to do anything' in self.resumed.output
                             and b'YOLO mode' in self.resumed.output,
                             'loaded same-conversation resume composer', terminal=self.resumed)
            prior_inputs = {row.get('owner_input_origin', {}).get('input_id')
                            for row in context['observations_for']('UserPromptSubmit')}
            self.resumed.submit('Initialize the recovered existing conversation.')
            self.wait_native(lambda: context['saved']()['agent']['execution_id'] != self.old_session['execution_id'],
                             'same-conversation new execution', terminal=self.resumed)
            current = context['saved']()['agent']
            require(current['native_session_id'] == self.old_native_id and current['agent_id'] == self.old_session['agent_id']
                    and current['database_generation'] == restore['generation'], 'native resume bypassed original conversation identity')
            current_session = session(current)
            self.wait_native(lambda: host.sql("SELECT count(*) FROM cairn.agent_native_owner_session_binding WHERE agent_id='" +
                current['agent_id'] + "' AND execution_id='" + current['execution_id'] + "' AND state='active'") == '1',
                'fresh signed generation activation', terminal=self.resumed)
            warmup = self.wait_native(lambda: next((row for row in context['observations_for']('UserPromptSubmit')
                if row.get('owner_input_origin', {}).get('source') == 'owner_composer'
                and row.get('owner_input_origin', {}).get('input_id') not in prior_inputs), None),
                'actual resumed composer observation', terminal=self.resumed)
            self.assert_provider()
            self.wait_native(lambda: any(row.get('turn_id') == warmup['turn_id']
                for row in context['observations_for']('Stop')), 'resumed composer turn ended', terminal=self.resumed)
            self.assert_provider()
            # New-generation wait remains deliverable to the same resumed waiter.
            event = self.wait_fixture.request('request_response')
            wait = self.wait_fixture.create_wait(event, 'request_response')
            require(wait['generation'] == restore['generation'], 'fresh wait reused old generation')
            self.wait_fixture.respond(event, wait, 'request_response')
            host.operator('wait-outbox-drain', {'repo': host.repo, 'limit': 10})
            self.wait_fixture.finish_native_control(self.wait_fixture.published(wait))
            self.wait_fixture.unchanged_outcome(event)
            self.fresh_body = 'Fresh physical-restore notice ' + new_id()
            self.fresh_event = context['publish'](self.fresh_body)
            self.mode = 'fresh'
            self.resumed.submit('Read and acknowledge the new routine notice after restore.')
            self.wait_native(self.fresh_done.is_set, 'fresh exact native read/ack/advance', terminal=self.resumed)
            self.assert_provider()
            require(self.fresh_done.is_set(), 'fresh native round did not finish')
            self.wait_native(lambda: any(row.get('turn_id') == self.report['fresh_native_item']['native_turn_id']
                for row in context['observations_for']('Stop')), 'fresh notice exact-turn Stop', terminal=self.resumed)
            require(host.sql("SELECT state FROM cairn.agent_delivery WHERE event_id='" + self.fresh_event['event_id'] + "'") == 'handled',
                    'fresh native notice lacks actual handled delivery')
            require(host.sql("SELECT attempts FROM cairn.agent_delivery WHERE event_id='" + self.w3['reserved_notice_event_uuid'] + "'") == '0',
                    'new-generation positive control admitted old wait notice')
            require(host.sql("SELECT count(*) FROM cairn.agent_delivery WHERE event_id='" + self.forgotten_event['event_id'] + "'") == '0',
                    'old queued quiet notice was materialized')
            for event in (self.waiting_event, self.pending_event, self.published_event):
                self.wait_fixture.unchanged_outcome(event)
            self.report['new_generation_sessions'] = {'native': current_session, 'waiter': session(self.wait_fixture.sessions['bob'])}
            self.report['stages'].append('same_conversation_fresh_composer_and_wait_positive_controls')
        finally:
            self.mode = 'idle'
            self.release.set()
            if self.resumed is not None:
                self.resumed.close()
                self.report['resumed_native_exit_code'] = self.resumed.process.returncode
                diagnostic = context['native_root'] / 'resumed-terminal.txt'
                diagnostic.write_bytes(self.resumed.output)
                diagnostic.chmod(0o600)
                self.report['resumed_terminal_diagnostic'] = str(diagnostic)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--native-manifest', type=Path, required=True)
    parser.add_argument('--report-directory', type=Path, required=True)
    args = parser.parse_args()
    binary, manifest = args.binary.resolve(), args.native_manifest.resolve()
    output = args.report_directory.resolve()
    require(binary.is_file(), 'explicit candidate executable required')
    require(manifest.is_file(), 'complete native package manifest required')
    require(not output.is_relative_to(ROOT), 'report directory must be outside the source checkout')
    require(not output.exists() or not any(output.iterdir()), 'report directory must be new or empty')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected_fixtures, expected_native = fixture_inputs(), native_inputs(manifest)
    report = {'schema': 'cairn.work-coordination-physical-restore-proof/1', 'status': 'running', 'stages': [],
              'binary_sha256': digest(binary), 'fixture_sha256': digest(__file__),
              'fixture_inputs': expected_fixtures, 'native_inputs': expected_native,
              'provider': 'local scripted Responses fixture', 'default_code_mode': True,
              'model_usefulness_measured': False, 'cluster_cleaned': False}
    root = Path(tempfile.mkdtemp(prefix='cairn-physical-restore-'))
    cluster = OwnedCluster(root)
    report['disposable_root'] = str(root)
    try:
        cluster = OwnedCluster.start(root)
        with DisposableHost.start(cluster, repo=str(root / 'collection'), binary=binary,
                                  database='cairn_physical_restore', roles=('sender', 'worker', 'alice', 'bob')) as host:
            native.scenario(host, manifest, report, restore_case=RestoreController(cluster, report, output))
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        try:
            if report['status'] == 'passed':
                cluster.remove()
                report['cluster_cleaned'] = not root.exists()
            else:
                report['cluster_stopped'] = cluster.stop()
        except Exception as error:
            report.update(status='failed', cleanup_error=str(error))
            raise
        finally:
            report['fixture_inputs_unchanged'] = fixture_inputs() == expected_fixtures
            report['native_inputs_unchanged'] = native_inputs(manifest) == expected_native
            report['binary_sha256_after'] = digest(binary)
            report['fixture_sha256_after'] = digest(__file__)
            if (not report['fixture_inputs_unchanged'] or not report['native_inputs_unchanged']
                    or report['binary_sha256_after'] != report['binary_sha256']
                    or report['fixture_sha256_after'] != report['fixture_sha256']):
                report['status'] = 'failed'
                report['identity_error'] = 'binary/native/fixture drift during proof'
            (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    require(report['status'] == 'passed' and report['cluster_cleaned'], 'proof or cleanup incomplete')
    print(json.dumps({'status': report['status'], 'stages': report['stages'], 'cluster_cleaned': report['cluster_cleaned']}))


if __name__ == '__main__':
    main()
