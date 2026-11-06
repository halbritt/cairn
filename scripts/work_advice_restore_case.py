"""Optional real advice state for the owned whole-database restore controller.

No SQL mutates state. The provider is explicitly synthetic;
the CLI, policy, job, proposal, application, witness and process paths are real.
"""
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlencode
from datetime import datetime, timezone
from types import SimpleNamespace

from disposable_host import require
from check_work_wait_native import new_id, ref
import work_coordination_ui_fixture as ui


HERE = Path(__file__).resolve().parent
PROVIDER = HERE.parent / 'tests/fixtures/work_advice/restore_provider.py'
SUPERVISOR = HERE / 'work_advice_restore_supervisor.py'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    temporary = path.with_suffix('.pending')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def identity(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, pgid=int(fields[2]), start_ticks=int(fields[19]),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


class AdviceRestoreCase:
    def __init__(self, source):
        # No files, sockets, children or host changes before on_phase owns cleanup.
        self.source = source
        self.host = self.runtime = self.web = None
        self.supervisor = self.channel = None
        self.supervisor_log = None
        self.extra_inputs = []
        self.report = {'status': 'not_started', 'provider': 'registered synthetic executable; no model call'}

    def input_files(self):
        return [Path(__file__).resolve(), SUPERVISOR,
                PROVIDER,
                self.source / 'tests/fixtures/work_ui/plane.py', *self.extra_inputs]

    def record(self, controller, name, value):
        self.report[name] = value
        controller.record('advice_restore', self.report)

    def work(self, *args, expected='OK', timeout=15):
        return self.host.command(['work', *args, '--collection', self.host.repo],
                                 env=self.env, expected=expected, timeout=timeout)

    def advice(self, *args, expected='OK', timeout=15):
        if args[0] in ('profile', 'control', 'reconcile', 'sweep'):
            return self.host.command(['work', 'advice', *args], env=self.env, expected=expected, timeout=timeout)
        return self.work('advice', *args, expected=expected, timeout=timeout)

    def sql(self, query):
        return json.loads(self.host.sql(query, limit=1_000_000))

    def rows(self, table, condition, order):
        return self.sql(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY {order}), '[]'::jsonb) FROM cairn.{table} t WHERE {condition}")

    def job(self, label):
        rows = self.rows('work_advice_job', f"request_uuid='{self.requests[label]}'", 'job_uuid')
        require(len(rows) == 1, f'{label} request did not retain exactly one actual job')
        return rows[0]

    def invocations(self):
        path = self.provider_home / 'invocations.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def counts(self, expected):
        rows = self.invocations()
        require(len(rows) == expected, f'expected {expected} real advice invocations, observed {len(rows)}')
        require(len({row['request_id'] for row in rows}) == expected, 'provider received a duplicate request ID')
        return rows

    def mode(self, name):
        save(self.provider_home / 'synthetic-fixture.json',
             {'schema': 'cairn.synthetic-advice-fixture/1', 'mode': name})

    def generation_argv(self, label, item, source):
        return ['work', 'advice', 'generate', '--project', self.slug, '--item', item,
                '--profile', self.profile, '--timeout', '180', '--request-id', self.requests[label],
                '--candidate-revision', self.revision['revision_id'],
                '--source', source['record_id'] + ':' + str(source['version']), '--collection', self.host.repo]

    def generate(self, label, item, source, mode):
        self.mode(mode)
        argv = self.generation_argv(label, item, source)
        result = self.host.command(argv, env=self.env, timeout=20)
        self.generations[label] = {'argv': argv, 'result': result}
        job = self.job(label)
        observed = self.invocations()[-1]
        require(job['status'] == 'completed' and job['job_uuid'] == result['proposal']['job_uuid'],
                f'{label} did not complete through its actual job')
        require(job['input_payload_digest'] == observed['snapshot_sha256']
                and job['input_snapshot'] == observed['snapshot'], 'provider saw a different immutable job input')
        require(result['proposal']['proposal_uuid'] and result['actions'], 'generation did not persist real actions')
        return result

    def apply_args(self, result, kinds, request):
        selected = [a['action_uuid'] for a in result['actions'] if a['action_type'] in kinds]
        require(len(selected) == len(kinds), 'selected action set differs from the actual proposal')
        return ['apply', '--proposal', result['proposal']['proposal_uuid'], '--version',
                str(result['proposal']['review_version']), '--request-id', request,
                *[arg for value in selected for arg in ('--action', value)]]

    def supervisor_call(self, operation, **values):
        require(self.supervisor is not None and self.supervisor.poll() is None, 'owned supervisor exited')
        self.channel.settimeout(20 if operation == 'close' else 12)
        self.channel.sendall(json.dumps(dict(operation=operation, **values)).encode())
        raw = self.channel.recv(32769)
        require(raw and len(raw) <= 32768, 'supervisor reply missing or oversized')
        result = json.loads(raw)
        require(result.get('ok') is True, 'supervisor operation failed')
        return result['data']

    def wait(self, predicate, label, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            if self.supervisor is not None:
                require(self.supervisor.poll() is None, 'supervisor exited while waiting for ' + label)
            time.sleep(0.05)
        raise RuntimeError('bounded wait failed: ' + label)

    def start_web(self):
        if self.web is not None:
            return
        self.web = SimpleNamespace(binary=self.host.binary, host=self.host, collection=self.host.repo, server=None)
        ui.Fixture.start_web(self.web)
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        request = urllib.request.Request(self.web.url + '/api/auth/login',
            data=json.dumps({'code': self.web.code}).encode(),
            headers={'Content-Type': 'application/json', 'Origin': self.web.url})
        with self.http.open(request, timeout=5) as response:
            require(response.status == 200 and json.load(response)['ok'], 'actual operator HTTP login failed')
        self.web.code = None

    def stop_web(self):
        if self.web is not None:
            ui.Fixture.stop_web(self.web)
            self.web = None

    def http_get(self, path, **query):
        self.start_web()
        with self.http.open(self.web.url + path + '?' + urlencode(query), timeout=5) as response:
            raw = response.read(1_000_001)
            require(response.status == 200 and len(raw) <= 1_000_000, 'bounded actual HTTP read failed')
            return json.loads(raw)

    def prepare(self, controller):
        self.host = controller.ctx['host']
        self.runtime = self.host.home / 'advice-restore'
        self.runtime.mkdir(mode=0o700)
        self.env = dict(self.host.env)
        self.requests = {label: new_id() for label in ('P', 'Q', 'R', 'fresh')}
        self.generations = {}
        self.slug, self.profile = 'physical-advice', 'synthetic-physical-restore'
        self.report['status'] = 'preparing'
        for feature in ('work-view/1', 'work-artifacts/1', 'work-advice/1'):
            self.work('features', 'enable', '--feature', feature, '--request-id', new_id())
        key, config, state_file = self.runtime / 'plane.key', self.runtime / 'plane.toml', self.runtime / 'plane.json'
        key.write_text('synthetic-fixture-key-never-sent\n'); key.chmod(0o600)
        config.write_text(f'default_instance = "work-ui"\n[instance.work-ui]\nbase_url = "{ui.ORIGIN}"\nworkspace = "work-ui"\napi_key_file = "{key}"\n')
        config.chmod(0o600)
        self.env.update(PLANE_CONFIG=str(config), CAIRN_PLANE_BIN=str(self.source / 'tests/fixtures/work_ui/plane.py'),
                        CAIRN_WORK_UI_PLANE_STATE=str(state_file))
        workspace, project = new_id(), new_id()
        self.items = {label: new_id() for label in ('P', 'Q', 'R')}
        items = [dict(id=item, sequence_id=index + 1, name='Physical advice ' + label, workspace=workspace,
                      project=project, state=dict(id=new_id(), name='Started', group='started'), assignees=[],
                      updated_at=datetime.now(timezone.utc).isoformat(), project_detail=dict(id=project, identifier='ARP'))
                 for index, (label, item) in enumerate(self.items.items())]
        save(state_file, dict(origin=ui.ORIGIN, workspace=workspace, projects={project:
            dict(name='Physical advice restore fixture', identifier='ARP', mode='normal', items=items)}))
        self.work('project', 'add', '--slug', self.slug, '--workspace', workspace, '--project', project,
                  '--origin', ui.ORIGIN, '--instance', 'work-ui')
        self.work('refresh', '--project', self.slug)
        project_row = self.work('project', 'show', '--project', self.slug)
        self.binding = project_row['binding_id']
        require({row['plane_item_uuid'] for row in self.work('list', '--project', self.slug)['items']} == set(self.items.values()),
                'actual Plane refresh did not persist the three exact items')
        self.advice('policy', 'set', '--project', self.slug, '--fields', 'identifier,title,state,blockers,artifacts,sources')
        # control set has no collection flag; it is an operator-global control.
        self.host.command(['work', 'advice', 'control', 'set', '--generation=true', '--apply=true'], env=self.env)
        self.provider_home = self.runtime / 'provider'; self.provider_home.mkdir(mode=0o700)
        executable = self.provider_home / 'synthetic-advice'
        shutil.copyfile(PROVIDER, executable); executable.chmod(0o500)
        self.mode('restore_copies')
        registered = self.advice('profile', 'register', '--profile', self.profile, '--executable', str(executable),
            '--binary-sha256', digest(executable), '--expected-upstream-commit', '6ff1e1ca3b30fc8b914720681fcf5fe62e95f856',
            '--expected-patch-sha256', hashlib.sha256(b'cairn-work-ui-synthetic-advice-provider-v1').hexdigest(),
            '--codex-home', str(self.provider_home), '--model', 'synthetic-work-ui-model', '--timeout-ms', '180000')
        require(registered['profile']['binary_sha256'] == digest(executable), 'profile did not bind the exact synthetic executable')
        self.report['provider_identity'] = dict(path=str(executable), sha256=digest(executable), native_subscription_claim=False)
        self.source_record = self.host.agent('sender', 'create', dict(request_id=new_id(), draft=dict(kind='note',
            body='Independent retained advice source ' + new_id(), claim_type='self', sensitivity='shareable',
            scope=dict(repo=self.host.repo, task_id='*', run_id='*'))))
        self.source_body = self.host.agent('sender', 'history', ref(self.source_record))['versions'][0]['body']
        self.revision = self.work('artifact', 'register', '--kind', 'report', '--url',
            'https://reports.fixture.invalid/physical-restore/' + new_id(), '--revision', 'exact-v1', '--display-name', 'Retained report control')
        actual = controller.ctx['saved']()['agent']
        for kind, target in (('session', actual['agent_id'] + ':' + actual['execution_id']),
                             ('event', controller.waiting_event['event_id']),
                             ('response_group', controller.pending_event['event_id'])):
            # The response-group source must actually be the selected group case.
            if kind == 'response_group':
                group_event = next(event for event in (controller.pending_event, controller.published_event)
                                   if event.get('response_group_id') or self.host.sql(
                                       f"SELECT count(*) FROM cairn.agent_response_group WHERE event_id='{event['event_id']}'") == '1')
                target = group_event['event_id']
            self.work('associate', '--project', self.slug, '--item', self.items['P'], '--target-type', kind, '--target-id', target)
        self.manual = self.work('progress', 'add', '--project', self.slug, '--item', self.items['R'],
                                '--text', 'Unrelated retained manual advice control')
        self.manual_before = self.rows('work_annotation', f"binding_id='{self.binding}' AND plane_item_uuid='{self.items['R']}'", 'annotation_id')
        self.manual_history_before = self.rows('work_annotation_history',
            f"annotation_id='{self.manual_before[0]['annotation_id']}'", 'revision')
        p = self.generate('P', self.items['P'], controller.forgotten_event['ref'], 'restore_copies')
        self.p_apply = self.apply_args(p, {'annotate_progress', 'link_artifact'}, new_id())
        self.p_receipt = self.advice(*self.p_apply)['receipt']
        require(self.advice(*self.p_apply)['receipt'] == self.p_receipt, 'P exact application retry changed receipt')
        require(controller.forgotten_event['ref']['record_id'] in self.p_receipt['source_record_ids'],
                'applied copied outputs lack the immutable selected-source dependency')
        outputs = self.p_receipt['output_revisions']
        self.annotation_id = next(row['annotation_id'] for row in outputs.values() if row['type'] == 'progress')
        self.link_id = next(row['link_id'] for row in outputs.values() if row['type'] == 'artifact_linked')
        link = self.rows('work_artifact_link', f"link_id='{self.link_id}'", 'link_id')
        history = self.rows('work_artifact_link_history', f"link_id='{self.link_id}'", 'revision')
        require(len(link) == 1 and controller.forgotten_body in link[0]['rationale']
                and controller.forgotten_body in json.dumps(history), 'real prebackup link/history lacks copied selected rationale')
        annotation = self.rows('work_annotation', f"annotation_id='{self.annotation_id}'", 'annotation_id')
        require(len(annotation) == 1 and controller.forgotten_body in annotation[0]['body'], 'real prebackup progress copy absent')
        self.p_review = self.advice('show', '--proposal', p['proposal']['proposal_uuid'])
        require(self.p_review['proposal']['status'] == 'partially_applied'
                and [a['action_type'] for a in self.p_review['actions'] if a['status'] == 'pending'] == ['annotate_blocker'],
                'P did not retain an actual unapplied blocker alongside its applied outputs')
        q = self.generate('Q', self.items['Q'], ref(self.source_record), 'restore_progress')
        require(q['proposal']['status'] == 'pending' and all(a['status'] == 'pending' for a in q['actions'])
                and not self.rows('work_advice_application_receipt', f"proposal_uuid='{q['proposal']['proposal_uuid']}'", 'receipt_uuid'),
                'Q was not an eligible completed but unapplied proposal before backup')
        self.q_apply = self.apply_args(q, {'annotate_progress'}, new_id())
        self.counts(2)
        self.mode('hold_family')
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.channel = parent; self.channel.settimeout(12)
        self.supervisor_log = (self.runtime / 'supervisor.log').open('wb')
        try:
            self.supervisor = subprocess.Popen([sys.executable, str(SUPERVISOR),
                '--fd', str(child.fileno()), '--root', str(self.runtime)], pass_fds=(child.fileno(),),
                stdin=subprocess.DEVNULL, stdout=self.supervisor_log, stderr=self.supervisor_log, env=self.env)
        finally:
            child.close()
        self.r_argv = self.generation_argv('R', self.items['R'], ref(self.source_record))
        started = self.supervisor_call('start', argv=[str(self.host.binary), *self.r_argv])
        self.control_identity = started['control_identity']
        before_input = self.wait(lambda: json.loads((self.provider_home / 'before-input.json').read_text())
            if (self.provider_home / 'before-input.json').exists() else None, 'provider waiting before input consumption')
        r_job = self.wait(lambda: next(iter(self.rows('work_advice_job',
            f"request_uuid='{self.requests['R']}' AND status='running'", 'job_uuid')), None), 'persisted real launch identity')
        require(r_job['process_identity'] == before_input['identity'] == identity(before_input['identity']['pid']),
                'Store did not persist the actual provider identity before input consumption')
        self.counts(2)
        (self.provider_home / 'allow-input').write_text('release actual selected request\n')
        family = self.wait(lambda: json.loads((self.provider_home / 'family-ready.json').read_text())
            if (self.provider_home / 'family-ready.json').exists() else None, 'real same-group provider descendant')
        require(family['leader'] == r_job['process_identity'] and family['child']['pgid'] == family['leader']['pgid'],
                'provider child does not belong to the actual recorded group')
        self.family = family
        state = self.supervisor_call('status')
        retained = {row['identity']['pid']: row['identity'] for row in state['family']}
        require(all(retained.get(value['pid']) == value for value in family.values()), 'supervisor did not pin the exact real family')
        r_observation = self.counts(3)[-1]
        require(r_observation['snapshot_sha256'] == r_job['input_payload_digest'], 'R consumed another input snapshot')
        self.record(controller, 'pre_backup', dict(binding=self.binding, items=self.items, requests=self.requests,
            source=ref(self.source_record), p_receipt=self.p_receipt, p_link_id=self.link_id,
            p_annotation_id=self.annotation_id, r_job_uuid=r_job['job_uuid'], r_family=family,
            before_input=before_input, control_identity=self.control_identity, invocations=self.counts(3)))

    def snapshot(self, controller):
        condition = f"binding_id='{self.binding}'"
        proposals = f"proposal_uuid IN (SELECT proposal_uuid FROM cairn.work_advice_proposal WHERE {condition})"
        annotation_ids = f"annotation_id IN (SELECT annotation_id FROM cairn.work_annotation WHERE {condition})"
        return dict(jobs=self.rows('work_advice_job', condition, 'job_uuid'),
            proposals=self.rows('work_advice_proposal', condition, 'proposal_uuid'),
            actions=self.rows('work_advice_action', proposals, 'action_uuid'),
            receipts=self.rows('work_advice_application_receipt', condition, 'receipt_uuid'),
            annotations=self.rows('work_annotation', condition, 'annotation_id'),
            annotation_history=self.rows('work_annotation_history', annotation_ids, 'annotation_id,revision'),
            links=self.rows('work_artifact_link', condition, 'link_id'),
            link_history=self.rows('work_artifact_link_history', f"link_id='{self.link_id}'", 'revision'))

    def copies_absent(self, controller, label, *, http_history=True):
        p = self.generations['P']['result']['proposal']
        reads = {'show_proposal': self.advice('show', '--proposal', p['proposal_uuid']),
            'show_request': self.advice('show', '--request-id', self.requests['P']),
            'list': self.advice('list', '--project', self.slug, '--item', self.items['P']),
            'work_detail': self.work('show', '--project', self.slug, '--item', self.items['P']),
            'artifact_list': self.work('artifact', 'list', '--project', self.slug, '--item', self.items['P'], '--include-removed'),
            'artifact_impact': self.work('artifact', 'impact', '--revision', self.revision['revision_id']),
            'persisted': self.snapshot(controller)}
        if http_history:
            reads['artifact_history'] = self.http_get('/api/work/artifact/history',
                project=self.slug, item=self.items['P'], link_id=self.link_id)
        require(controller.forgotten_body not in json.dumps(reads), 'forgotten selected text remains in actual advice/work/artifact reads: ' + label)
        review = reads['show_proposal']
        require(review['proposal']['proposal_uuid'] == p['proposal_uuid'] and review['job']['job_uuid'] == p['job_uuid'],
                'privacy read discarded opaque advice identities')
        require(review['input']['payload_available'] is False and review['currentness']['state'] == 'purged',
                'forgotten advice input was not explicitly unavailable')
        require(any(r['receipt_uuid'] == self.p_receipt['receipt_uuid'] for r in review['applications']),
                'privacy read lost the existing application identity')
        require(any(r['link_id'] == self.link_id for r in reads['artifact_list']['artifacts']),
                'privacy cleanup removed the opaque artifact relationship')
        require(self.rows('work_annotation', f"binding_id='{self.binding}' AND plane_item_uuid='{self.items['R']}'", 'annotation_id') == self.manual_before,
                'source deletion or reconciliation changed unrelated manual progress')
        require(self.rows('work_annotation_history', f"annotation_id='{self.manual_before[0]['annotation_id']}'", 'revision') == self.manual_history_before,
                'source deletion or reconciliation changed unrelated manual history')
        retained = self.host.agent('sender', 'history', ref(self.source_record))
        require(retained['versions'][0]['body'] == self.source_body, 'independent advice source lost eligibility')
        self.record(controller, label, dict(reads=reads, http_history_executed=http_history, invocations=self.counts(3)))

    def restored(self, controller):
        cached = self.host.command(self.generations['P']['argv'], env=self.env)
        require(cached['proposal']['proposal_uuid'] == self.generations['P']['result']['proposal']['proposal_uuid']
                and controller.forgotten_body not in json.dumps(cached), 'cached P generation resurrected copied text or identity')
        cached_apply = self.advice(*self.p_apply)['receipt']
        require(cached_apply == self.p_receipt, 'exact P application replay changed its recorded outcome')
        q_before = self.snapshot(controller)
        refused = self.advice(*self.q_apply, expected='RESTORE_FENCE_TRIGGERED')
        require('restore generation' in refused['message'].lower(), 'Q refused for something other than generation fencing')
        require(self.snapshot(controller) == q_before, 'old Q apply changed a target or receipt')
        q_show = self.advice('show', '--proposal', self.generations['Q']['result']['proposal']['proposal_uuid'])
        require(q_show['input']['payload_available'] is True and self.source_body in json.dumps(q_show['input']),
                'source restriction could mask Q restore-generation refusal')
        r_before = self.job('R')
        require(r_before['status'] in ('running', 'uncertain', 'cleanup_unconfirmed'), 'restored R became silently executable/complete')
        expected = 'INFLIGHT_LIMIT' if r_before['status'] == 'running' else (r_before.get('error_code') or 'MODEL_EXECUTION_FAILED')
        refusal = self.host.command(self.r_argv, env=self.env, expected=expected)
        require(r_before['job_uuid'] in refusal['message'] and self.job('R') == r_before, 'R retry changed or hid its original job')
        state_before = self.supervisor_call('status')
        require(state_before['control_identity'] == self.control_identity and not state_before['killed_by_fixture'],
                'fixture cleanup ran before the real reconciliation')
        for observed in self.family.values():
            require(identity(observed['pid']) == observed, 'old actual provider family exited before Store reconciliation')
        self.counts(3)
        reconciled = self.advice('reconcile', timeout=20)
        self.record(controller, 'reconciliation_attempt', dict(response=reconciled,
            actual_job=self.job('R'), supervisor=self.supervisor_call('status'), invocations=self.counts(3)))
        match = [row for row in reconciled['reconciled'] if row['job_uuid'] == r_before['job_uuid']]
        require(len(match) == 1 and match[0]['final_state'] in ('failed', 'uncertain'),
                'Store did not confirm exact provider cleanup; keep cleanup_unconfirmed fenced')
        def reaped():
            state = self.supervisor_call('status')
            selected = {row['identity']['pid']: row for row in state['family']}
            return state if all(selected.get(v['pid'], {}).get('reaped') and not selected[v['pid']]['proc_present']
                                for v in self.family.values()) else None
        after = self.wait(reaped, 'Store-killed provider family reaped', seconds=8)
        require(not after['killed_by_fixture'] and after['control_identity'] == self.control_identity,
                'reconciliation killed unrelated control or relied on fixture cleanup')
        r_after = self.job('R')
        require(r_after['status'] in ('failed', 'uncertain') and r_after['process_identity'] == r_before['process_identity'],
                'R lost its durable terminal/process identity')
        require(not self.rows('work_advice_proposal', f"job_uuid='{r_after['job_uuid']}'", 'proposal_uuid'),
                'restored R created a proposal without another observed invocation')
        require(not self.rows('work_advice_application_receipt',
                f"binding_id='{self.binding}' AND plane_item_uuid='{self.items['R']}'", 'receipt_uuid'),
                'restored R acquired an application receipt')
        self.host.command(self.r_argv, env=self.env, expected=r_after.get('error_code') or 'MODEL_EXECUTION_FAILED')
        again = self.advice('reconcile', timeout=20)
        self.counts(3)
        # Work serve legitimately performs startup/periodic reconciliation.
        # Only start it once the explicit exact-process recovery is observed.
        self.copies_absent(controller, 'restored_copy_privacy')
        undo = self.advice('undo', '--receipt', self.p_receipt['receipt_uuid'])
        require(undo['receipt']['receipt_uuid'] == self.p_receipt['receipt_uuid'] and undo['receipt']['undone'],
                'guarded compensation did not retain the exact application identity')
        require(self.advice('undo', '--receipt', self.p_receipt['receipt_uuid']) == undo, 'compensation retry changed outcome')
        self.copies_absent(controller, 'restored_privacy_after_exact_retries_and_undo')
        self.record(controller, 'reconciliation', dict(first=reconciled, second=again, before=r_before, after=r_after,
            supervisor_after=after, no_automatic_provider_rerun=True, late_completion_race_executed=False))

    def fresh(self, controller):
        project = self.work('project', 'show', '--project', self.slug)
        stamp = project.get('next_refresh_allowed_at')
        if stamp:
            remaining = (datetime.fromisoformat(stamp.replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds()
            require(remaining <= 65, 'real refresh throttle exceeds the selected bounded wait')
            while remaining > 0:
                time.sleep(min(remaining + 0.03, 0.25))
                remaining = (datetime.fromisoformat(stamp.replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds()
        self.work('refresh', '--project', self.slug)
        require(self.work('project', 'show', '--project', self.slug)['binding_id'] == self.binding,
                'fresh control replaced the restored project binding')
        fresh = self.generate('fresh', self.items['Q'], ref(self.source_record), 'restore_progress')
        job = self.job('fresh')
        generation = int(self.host.sql('SELECT generation FROM cairn.retrieval_generation WHERE singleton'))
        require(job['input_manifest']['restore_generation'] == generation
                and generation > self.job('Q')['input_manifest']['restore_generation'], 'fresh advice lacks new generation')
        args = self.apply_args(fresh, {'annotate_progress'}, new_id())
        receipt = self.advice(*args)['receipt']
        require(self.advice(*args)['receipt'] == receipt, 'fresh exact apply retry duplicated or changed outcome')
        annotations = self.rows('work_annotation', f"binding_id='{self.binding}' AND plane_item_uuid='{self.items['Q']}'", 'annotation_id')
        receipts = self.rows('work_advice_application_receipt', f"proposal_uuid='{fresh['proposal']['proposal_uuid']}'", 'receipt_uuid')
        require(len(annotations) == len(receipts) == 1 and self.source_body in annotations[0]['body']
                and receipts[0]['receipt_uuid'] == receipt['receipt_uuid'], 'fresh control did not create exactly one real effect/receipt')
        require(not self.rows('work_advice_application_receipt',
                f"proposal_uuid='{self.generations['Q']['result']['proposal']['proposal_uuid']}'", 'receipt_uuid'), 'old Q gained an application')
        self.report['status'] = 'passed'
        self.record(controller, 'fresh_control', dict(job=job, receipt=receipt, annotations=annotations, invocations=self.counts(4)))

    def on_phase(self, name, controller):
        if name == 'notices_queued':
            self.prepare(controller)
        elif name == 'backup_complete':
            result = self.supervisor_call('kill_controller')
            require(result['cli_exit'] == -9 and result['control_identity'] == self.control_identity,
                    'owned controller crash failed or disturbed unrelated control')
            require(all(identity(value['pid']) == value for value in self.family.values()), 'provider family did not survive controller crash')
            self.record(controller, 'controller_crash', result)
        elif name == 'source_forgotten':
            self.copies_absent(controller, 'live_post_backup_forget', http_history=False)
            require(all(identity(value['pid']) == value for value in self.family.values()),
                    'actual provider family did not survive the pre-restore Forget/read phase')
            self.stop_web()  # No extra DB connection may survive into drop/restore.
        elif name == 'restore_paused':
            for label in ('P', 'Q', 'R'):
                self.advice('show', '--request-id', self.requests[label], expected='RESTORE_PAUSED')
            self.advice(*self.q_apply, expected='RESTORE_PAUSED')
            require(all(identity(value['pid']) == value for value in self.family.values()),
                    'actual provider family exited before the selected explicit recovery command')
            self.record(controller, 'paused_refusals', {'request_labels': ['P', 'Q', 'R'], 'invocations': self.counts(3)})
        elif name == 'restore_resumed':
            self.restored(controller)
        elif name == 'coordination_controls_complete':
            self.fresh(controller)
        else:
            raise RuntimeError('unreviewed advice restore phase: ' + name)

    def close(self, controller):
        errors = []
        try:
            self.stop_web()
        except BaseException as error:
            errors.append('work HTTP cleanup: ' + str(error))
        if self.supervisor is not None:
            try:
                closed = self.supervisor_call('close')
                self.supervisor.wait(timeout=15)
                require(closed['cleanup_confirmed'] and self.supervisor.returncode == 0, 'supervisor could not confirm owned process cleanup')
                self.report['process_cleanup'] = closed
            except BaseException as error:
                errors.append('advice supervisor cleanup: ' + str(error))
                if self.channel is not None:
                    self.channel.close()  # EOF asks the supervisor to clean its own pinned tree.
                    self.channel = None
                try:
                    self.supervisor.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    errors.append('supervisor remains alive; process cleanup unconfirmed')
        if self.channel is not None:
            self.channel.close(); self.channel = None
        if self.supervisor_log is not None:
            self.supervisor_log.close(); self.supervisor_log = None
        if self.runtime is not None:
            if 'provider_identity' in self.report:
                selected = self.report['provider_identity']
                if digest(selected['path']) != selected['sha256']:
                    errors.append('registered synthetic provider bytes changed during proof')
            capabilities = self.provider_home / 'capabilities.jsonl' if hasattr(self, 'provider_home') else None
            if capabilities is not None and capabilities.exists():
                self.report['capability_probes'] = [json.loads(row) for row in capabilities.read_text().splitlines()]
            path = self.runtime / 'supervisor-final.json'
            if path.exists():
                self.report['supervisor_final'] = json.loads(path.read_text())
        if errors:
            self.report['cleanup_errors'] = errors
            self.report['status'] = 'failed'
        controller.record('advice_restore', self.report)
        require(not errors, 'advice extension cleanup unconfirmed: ' + '; '.join(errors))
