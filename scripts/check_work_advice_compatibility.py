#!/usr/bin/env python3
"""Actual CLI/HTTP advice compatibility and isolated real timeout proof.

All domain mutations use the captured binary. The registered executable is an
explicit synthetic boundary; no model or production service is invoked.
"""
import argparse
import hashlib
import http.cookiejar
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import urllib.error
import urllib.parse
import urllib.request
import uuid

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
PROVIDER = HERE.parent / 'tests/fixtures/work_advice/restore_provider.py'
SUPERVISOR = HERE / 'work_advice_restore_supervisor.py'
FIXTURE_PATHS = {
    'scripts/build_work_advice_compatibility.py': HERE / 'build_work_advice_compatibility.py',
    'scripts/check_work_advice_compatibility.py': Path(__file__).resolve(),
    'scripts/work_advice_restore_supervisor.py': SUPERVISOR,
    'tests/fixtures/work_advice/restore_provider.py': PROVIDER,
}
from build_work_advice_compatibility import OLD, digest, manifest, save, require


def uid():
    return str(uuid.uuid4())


def proc_identity(pid):
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return dict(pid=pid, pgid=int(fields[2]), start_ticks=int(fields[19]),
                boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())


class Case:
    def __init__(self, host, binaries, ui, run_envelope, report, output):
        self.host, self.binaries, self.ui, self.run_envelope = host, binaries, ui, run_envelope
        self.report, self.output = report, output
        self.web = self.supervisor = self.channel = self.log = None
        self.slug = 'lower-advice'
        self.runtime = host.home / 'lower-advice'; self.runtime.mkdir(mode=0o700)
        self.provider_home = self.runtime / 'provider'; self.provider_home.mkdir(mode=0o700)
        self.profile = 'synthetic-lower-advice'
        self.requests = {label: uid() for label in ('P', 'Q', 'R')}
        self.items = {label: uid() for label in ('P', 'Q', 'R')}
        self.repo = host.repo

    def command(self, name, argv, body=None, *, expected='OK', timeout=20):
        reply = self.run_envelope([str(self.binaries[name]), *argv], body, env=self.host.env, expected=None, timeout=timeout)
        self.report['commands'].append(dict(binary=name, argv=argv, response=reply))
        require(reply['status'] == expected, f'{name} {argv[:3]}: expected {expected}, got {reply["status"]}')
        return reply['data'] if expected == 'OK' else reply

    def work(self, name, *argv, **kwargs):
        return self.command(name, ['work', *argv, '--collection', self.repo], **kwargs)

    def advice(self, name, *argv, **kwargs):
        if argv[0] in ('profile', 'control', 'reconcile', 'sweep'):
            return self.command(name, ['work', 'advice', *argv], **kwargs)
        return self.work(name, 'advice', *argv, **kwargs)

    def feature(self, action, name='normal', feature='work-advice/1'):
        args = ['features', action, '--feature', feature]
        if action != 'status':
            args += ['--request-id', uid()]
        return self.work(name, *args)

    def sql_rows(self, table, condition='true', order='1'):
        return json.loads(self.host.sql(f"SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY {order}), '[]'::jsonb) FROM cairn.{table} t WHERE {condition}", limit=1_000_000))

    def job(self, label):
        rows = self.sql_rows('work_advice_job', f"request_uuid='{self.requests[label]}'", 'job_uuid')
        require(len(rows) == 1, label + ': exact one job missing')
        return rows[0]

    def invocations(self, expected):
        path = self.provider_home / 'invocations.jsonl'
        rows = [json.loads(row) for row in path.read_text().splitlines()] if path.exists() else []
        require(len(rows) == expected and len({row['request_id'] for row in rows}) == expected,
                f'expected {expected} distinct synthetic invocations, got {len(rows)}')
        return rows

    def mode(self, mode):
        save(self.provider_home / 'synthetic-fixture.json', dict(schema='cairn.synthetic-advice-fixture/1', mode=mode))

    def generation(self, label):
        return ['work', 'advice', 'generate', '--project', self.slug, '--item', self.items[label],
                '--profile', self.profile, '--timeout', '10', '--request-id', self.requests[label],
                '--source', self.source['record_id'] + ':' + str(self.source['version']), '--collection', self.repo]

    def start_web(self):
        self.web = SimpleNamespace(server=None, binary=self.binaries['lower'], host=self.host, collection=self.repo)
        self.ui.Fixture.start_web(self.web)
        self.web_pid = self.web.server.pid
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        request = urllib.request.Request(self.web.url + '/api/auth/login', data=json.dumps({'code': self.web.code}).encode(),
            headers={'Content-Type': 'application/json', 'Origin': self.web.url})
        with self.http.open(request, timeout=5) as response:
            require(response.status == 200 and json.load(response)['ok'], 'real lower HTTP login failed')
        self.web.code = None

    def read_http(self, stage, expected='OK'):
        require(self.web.server.pid == self.web_pid and self.web.server.poll() is None, 'lower HTTP process changed/exited')
        query = urllib.parse.urlencode(dict(project=self.slug, item=self.items['Q']))
        try:
            response = self.http.open(self.web.url + '/api/work/item?' + query, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(1_000_001); status = response.status
        require(len(raw) <= 1_000_000, 'HTTP output exceeded bound')
        data = json.loads(raw)
        self.report['http'].append(dict(stage=stage, status=status, payload=data, same_server_pid=self.web_pid))
        if expected == 'OK':
            require(status == 200 and data['item']['plane_item_uuid'] == self.items['Q'], 'lower HTTP exact item read failed')
        else:
            require(status >= 400 and data.get('code') == expected and 'item' not in data,
                    'warm HTTP did not refuse the compatibility boundary')
            require(self.source['body'] not in raw.decode(), 'compatibility error exposed selected source')
        return data

    def refusal(self, fragment):
        reply = self.work('lower', 'features', 'status', expected='SCHEMA_MISMATCH')
        require(fragment in reply['message'], 'lower refusal came from a different compatibility gate')
        return reply

    def supervisor_call(self, operation, **kwargs):
        require(self.supervisor is not None and self.supervisor.poll() is None, 'owned supervisor exited')
        self.channel.sendall(json.dumps(dict(operation=operation, **kwargs)).encode())
        raw = self.channel.recv(32769)
        require(raw and len(raw) <= 32768, 'missing/oversize supervisor reply')
        reply = json.loads(raw)
        require(reply['ok'] is True, 'supervisor operation failed')
        return reply['data']

    def wait(self, predicate, label, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            if self.supervisor is not None:
                require(self.supervisor.poll() is None, 'supervisor exited while waiting for ' + label)
            time.sleep(0.025)
        raise RuntimeError('bounded wait failed: ' + label)

    def setup(self, plane_program, write_private):
        for key in list(self.host.env):
            if key.startswith('PLANE_') or key == 'CAIRN_PLANE_BIN':
                del self.host.env[key]
        workspace, project = uid(), uid()
        plane, data, config = [self.runtime / name for name in ('plane-fixture', 'plane.json', 'plane.toml')]
        write_private(plane, plane_program); plane.chmod(0o500)
        write_private(data, json.dumps({project: {'project': dict(id=project, workspace=workspace, identifier='LOW'),
            'items': [dict(id=item, project=project, name='Lower advice ' + label, sequence_id=index + 1,
                state=None, assignees=[], updated_at='2026-11-05T00:00:00Z') for index, (label, item) in enumerate(self.items.items())]}}))
        write_private(config, 'base_url = "https://plane.invalid"\nworkspace = "synthetic"\n')
        self.host.env.update(CAIRN_PLANE_BIN=str(plane), PLANE_CONFIG=str(config), WORK_RESTORE_PLANE_DATA=str(data))
        self.feature('enable', feature='work-view/1')
        self.work('normal', 'project', 'add', '--slug', self.slug, '--workspace', workspace, '--project', project, '--request-id', uid())
        self.work('normal', 'refresh', '--project', self.slug)
        self.baseline = self.work('normal', 'progress', 'add', '--project', self.slug, '--item', self.items['Q'],
                                  '--text', 'Independent retained lower-support progress')
        self.source = self.host.agent('sender', 'create', dict(request_id=uid(), draft=dict(kind='note',
            body='Selected synthetic lower-support source ' + uid(), claim_type='self', sensitivity='shareable',
            scope=dict(repo=self.repo, task_id='*', run_id='*'))))
        executable = self.provider_home / 'synthetic-advice'
        shutil.copyfile(PROVIDER, executable); executable.chmod(0o500)
        self.mode('restore_progress')
        self.advice('normal', 'profile', 'register', '--profile', self.profile, '--executable', str(executable),
            '--binary-sha256', digest(executable), '--expected-upstream-commit', '6ff1e1ca3b30fc8b914720681fcf5fe62e95f856',
            '--expected-patch-sha256', hashlib.sha256(b'cairn-work-ui-synthetic-advice-provider-v1').hexdigest(),
            '--codex-home', str(self.provider_home), '--model', 'synthetic-work-ui-model', '--timeout-ms', '10000')
        self.advice('normal', 'policy', 'set', '--project', self.slug, '--fields', 'sources')
        self.advice('normal', 'control', 'set', '--generation=true', '--apply=true')
        self.report['synthetic_profile'] = dict(path=str(executable), sha256=digest(executable), no_native_subscription_claim=True)

    def run(self):
        initial = self.feature('status', 'lower')
        require(initial['status'] == 'disabled' and initial['pending_count'] == 0, 'advice baseline not disabled/empty')
        old = self.work('old78', 'features', 'status', expected='SCHEMA_MISMATCH')
        require('database schema version 79 exceeds supported version 78' in old['message'], 'old78 ceiling negative wrong')
        self.start_web(); self.read_http('disabled_empty')
        self.feature('enable')
        require(self.feature('status')['pending_count'] == 0, 'active-empty case has accepted work')
        self.refusal('active work feature "work-advice/1"'); self.read_http('active_empty', 'SCHEMA_MISMATCH')
        self.report['stages'].append('cold_and_same_warm_process_active_empty_refusal')

        generated = {}
        for label in ('P', 'Q'):
            generated[label] = self.command('normal', self.generation(label))
            actions = generated[label]['actions']
            require(len(actions) == 1 and actions[0]['action_type'] == 'annotate_progress', label + ': actual parsed progress proposal missing')
            require(self.job(label)['status'] == 'completed', label + ': real job did not complete')
        self.invocations(2)
        q = generated['Q']; action = q['actions'][0]
        apply_args = ['apply', '--proposal', q['proposal']['proposal_uuid'], '--version', str(q['proposal']['review_version']),
                      '--action', action['action_uuid'], '--request-id', uid()]
        receipt = self.advice('normal', *apply_args)['receipt']
        require(self.advice('normal', *apply_args)['receipt'] == receipt, 'Q exact Apply retry changed outcome')
        effect = receipt['output_revisions'][action['action_uuid']]
        require(effect['type'] == 'progress', 'Q actual progress effect missing')
        before_undo = self.sql_rows('work_annotation', f"annotation_id IN ('{effect['annotation_id']}','{self.baseline['annotation_id']}')", 'annotation_id')
        rows = {row['annotation_id']: row for row in before_undo}
        prior, applied = rows[self.baseline['annotation_id']], rows[effect['annotation_id']]
        require(prior['state'] == 'superseded' and prior['revision'] > self.baseline['revision']
                and applied['state'] == 'active' and applied['body'] == action['target_payload']['text'], 'Q real Apply baseline/effect invalid')
        self.feature('disable')
        require(self.feature('status')['pending_count'] == 1, 'disabled-P case not exactly one pending proposal')
        self.refusal('1 in-flight work advice job/proposal(s)'); self.read_http('disabled_pending', 'SCHEMA_MISMATCH')
        fresh_argv = self.generation('R')
        fresh_argv[fresh_argv.index('--request-id') + 1] = uid()
        self.command('normal', fresh_argv, expected='FEATURE_DISABLED')
        p = generated['P']['proposal']
        self.advice('normal', 'apply', '--proposal', p['proposal_uuid'], '--version', str(p['review_version']),
                    '--action', generated['P']['actions'][0]['action_uuid'], '--request-id', uid(), expected='FEATURE_DISABLED')
        self.invocations(2)
        self.advice('normal', 'reject', '--proposal', p['proposal_uuid'], '--version', str(p['review_version']))
        require(self.feature('status', 'lower')['pending_count'] == 0, 'rejection did not settle required state')
        self.read_http('disabled_settled')
        self.advice('lower', 'show', '--proposal', p['proposal_uuid'])
        undone = self.advice('lower', 'undo', '--receipt', receipt['receipt_uuid'])
        require(undone['receipt']['receipt_uuid'] == receipt['receipt_uuid'] and undone['receipt']['undone'], 'actual Undo receipt mismatch')
        require(self.advice('lower', 'undo', '--receipt', receipt['receipt_uuid']) == undone, 'actual Undo retry changed outcome')
        after = {row['annotation_id']: row for row in self.sql_rows('work_annotation',
            f"annotation_id IN ('{effect['annotation_id']}','{self.baseline['annotation_id']}')", 'annotation_id')}
        require(after[prior['annotation_id']]['state'] == 'active' and after[prior['annotation_id']]['body'] == self.baseline['body']
                and after[prior['annotation_id']]['revision'] > prior['revision']
                and after[applied['annotation_id']]['state'] == 'superseded'
                and after[applied['annotation_id']]['revision'] > applied['revision'], 'actual Undo did not compensate with new revisions')
        for row in [*before_undo, *after.values()]:
            history = self.sql_rows('work_annotation_history', f"annotation_id='{row['annotation_id']}' AND revision={row['revision']}")
            require(len(history) == 1 and history[0]['body'] == row['body'] and history[0]['state'] == row['state'], 'compensation history missing')
        q_after = self.advice('normal', 'show', '--proposal', q['proposal']['proposal_uuid'])
        require(all(row['status'] == 'undone' for row in q_after['actions']), 'Q compensation left an unapplied action')
        # No normal-support Reject may rescue zero-action phantom work after
        # lower Undo. The lower binary must remain usable for exact retry/read.
        require(self.feature('status', 'lower')['pending_count'] == 0, 'Q cleanup left required feature state')
        q_retained = self.advice('lower', 'show', '--proposal', q['proposal']['proposal_uuid'])
        require(any(row['receipt_uuid'] == receipt['receipt_uuid'] and row['undone'] for row in q_retained['applications']),
                'lower retained read lost compensated Q receipt')
        self.report['settlement'] = dict(proposals=generated, receipt=receipt, undone=undone, before=before_undo,
            after=list(after.values()), q_after_undo=q_after, q_settled_read=q_retained)
        self.report['stages'].append('disabled_pending_proposal_refusal_then_reject_and_lower_undo_retry')
        self.isolated_running_case()
        self.feature('retire')
        require(self.feature('status', 'lower')['status'] == 'retired', 'lower retained status after retirement failed')
        self.read_http('retired_settled')
        self.work('normal', 'features', 'enable', '--feature', 'work-advice/1', '--request-id', uid(), expected='FEATURE_RETIRED')
        old = self.work('old78', 'features', 'status', expected='SCHEMA_MISMATCH')
        require('database schema version 79 exceeds supported version 78' in old['message'], 'settlement bypassed schema ceiling')
        disabled_project = self.work('lower', 'project', 'disable', '--project', self.slug, '--request-id', uid())
        require(disabled_project['status'] == 'disabled', 'lower compatible project maintenance failed')
        self.invocations(3)
        self.report['stages'].append('retirement_retained_reads_lower_maintenance_and_true_old78_ceiling')

    def isolated_running_case(self):
        # Stop warm HTTP auto-maintenance before the timing-sensitive real job.
        self.ui.Fixture.stop_web(self.web)
        self.feature('enable')
        require(not self.sql_rows('work_advice_proposal', "status IN ('pending','partially_applied') AND EXISTS (SELECT 1 FROM cairn.work_advice_action a WHERE a.proposal_uuid=t.proposal_uuid AND a.status='pending')"), 'R gate confounded by a pending proposal')
        require(self.feature('status')['pending_count'] == 0, 'R gate starts with unsettled work')
        self.mode('hold_family')
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.channel = parent; self.channel.settimeout(20)
        self.log = (self.output / 'supervisor.log').open('wb')
        try:
            self.supervisor = subprocess.Popen([sys.executable, str(SUPERVISOR), '--fd', str(child.fileno()),
                '--root', str(self.runtime)], pass_fds=(child.fileno(),), env=self.host.env, stdin=subprocess.DEVNULL,
                stdout=self.log, stderr=self.log)
        finally:
            child.close()
        started = self.supervisor_call('start', argv=[str(self.binaries['normal']), *self.generation('R')])
        control = started['control_identity']
        ready = self.wait(lambda: json.loads((self.provider_home / 'before-input.json').read_text())
                          if (self.provider_home / 'before-input.json').exists() else None, 'real R pre-input launch')
        running = self.wait(lambda: next(iter(self.sql_rows('work_advice_job',
            f"request_uuid='{self.requests['R']}' AND status='running'", 'job_uuid')), None), 'persisted R process identity')
        require(running['process_identity'] == ready['identity'] == proc_identity(ready['identity']['pid']), 'R launch identity not exact')
        self.invocations(2)
        (self.provider_home / 'allow-input').write_text('release selected synthetic input\n')
        family = self.wait(lambda: json.loads((self.provider_home / 'family-ready.json').read_text())
                          if (self.provider_home / 'family-ready.json').exists() else None, 'R actual live child')
        require(family['leader'] == running['process_identity'] and family['child']['pgid'] == family['leader']['pgid'], 'R actual family group mismatch')
        state = self.supervisor_call('status')
        identities = {row['identity']['pid']: row['identity'] for row in state['family']}
        require(all(identities.get(row['pid']) == row for row in family.values()), 'supervisor did not pin both exact family members')
        self.feature('disable')
        require(self.job('R')['status'] == 'running' and self.feature('status')['pending_count'] == 1, 'R deadline elapsed before isolated negative')
        require(not self.sql_rows('work_advice_proposal', "status IN ('pending','partially_applied') AND EXISTS (SELECT 1 FROM cairn.work_advice_action a WHERE a.proposal_uuid=t.proposal_uuid AND a.status='pending')"), 'running-only gate confounded by proposals')
        refusal = self.refusal('1 in-flight work advice job/proposal(s)')
        require(self.job('R')['status'] == 'running', 'running-only refusal observed after R stopped')
        self.invocations(3)
        def exited():
            state = self.supervisor_call('status')
            family_rows = {row['identity']['pid']: row for row in state['family']}
            # The live Go controller reaps its provider leader via cmd.Wait;
            # this subreaper adopts/reaps only the provider's orphaned child.
            gone = all(family_rows.get(row['pid'], {}).get('exited')
                and not family_rows[row['pid']]['proc_present'] for row in family.values())
            adopted_reaped = family_rows.get(family['child']['pid'], {}).get('reaped')
            return state if state['cli_exit'] is not None and gone and adopted_reaped else None
        ended = self.wait(exited, 'actual 10-second runner timeout and reaped family', seconds=18)
        require(ended['cli_exit'] == 4 and ended['control_identity'] == control and not ended['killed_by_fixture'], 'timeout relied on fixture cleanup or affected control')
        out = self.runtime / 'controller.stdout'; err = self.runtime / 'controller.stderr'
        require(out.stat().st_size <= 1_000_000 and err.stat().st_size <= 100_000, 'R CLI output exceeded bounds')
        envelope = json.loads(out.read_text())
        require(envelope['schema'] == 'cairn.response/1' and envelope.get('ok') is False and envelope['status'] == 'MODEL_TIMEOUT', 'R did not retain the actual timeout outcome')
        final = self.job('R')
        require(final['status'] == 'failed' and final['error_code'] == 'MODEL_TIMEOUT'
                and final['process_identity'] == running['process_identity'], 'R terminal outcome/identity missing')
        require(not self.sql_rows('work_advice_proposal', f"job_uuid='{final['job_uuid']}'"), 'timed-out R acquired a proposal')
        require(self.feature('status', 'lower')['pending_count'] == 0, 'lower did not reopen after real timeout cleanup')
        self.advice('lower', 'show', '--request-id', self.requests['R'])
        self.invocations(3)
        self.report['running_only'] = dict(before=running, family=family, refusal=refusal, after=final, supervisor=ended, timeout_response=envelope)
        self.report['stages'].append('only_running_job_refuses_until_actual_runner_timeout_and_confirmed_family_cleanup')
        self.start_web(); self.read_http('after_real_timeout')

    def close(self):
        errors = []
        if self.web is not None:
            try:
                self.ui.Fixture.stop_web(self.web)
            except BaseException as error:
                errors.append('HTTP: ' + str(error))
        if self.supervisor is not None:
            try:
                closed = self.supervisor_call('close')
                self.supervisor.wait(timeout=20)
                require(closed['cleanup_confirmed'] and self.supervisor.returncode == 0, 'supervisor cleanup unconfirmed')
                self.report['supervisor_cleanup'] = closed
            except BaseException as error:
                errors.append('supervisor: ' + str(error))
                if self.channel is not None:
                    self.channel.close(); self.channel = None
                try:
                    self.supervisor.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    errors.append('supervisor remains alive; preserve owned runtime')
        if self.channel is not None:
            self.channel.close(); self.channel = None
        if self.log is not None:
            self.log.close(); self.log = None
        for name in ('invocations.jsonl', 'capabilities.jsonl'):
            path = self.provider_home / name
            if path.exists():
                self.report[name] = [json.loads(row) for row in path.read_text().splitlines()]
        final = self.runtime / 'supervisor-final.json'
        if final.exists():
            self.report['supervisor_final'] = json.loads(final.read_text())
        selected = self.report.get('synthetic_profile')
        if selected and digest(selected['path']) != selected['sha256']:
            errors.append('registered provider changed')
        self.report['case_cleanup_errors'] = errors
        require(not errors, 'case cleanup unconfirmed: ' + '; '.join(errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve(); require(not output.exists(), 'proof output must be new'); output.mkdir(parents=True, mode=0o700)
    build_raw = args.build_receipt.read_bytes(); build = json.loads(build_raw)
    require(build['schema'] == 'cairn.lower-support79-build/1' and build['status'] == 'passed', 'exact completed build receipt required')
    require(build.get('build_script_sha256') == digest(HERE / 'build_work_advice_compatibility.py'), 'build receipt does not bind reviewed builder')
    candidates = build['candidates']; require(set(candidates) == {'normal', 'lower', 'old78'}, 'three exact candidates required')
    require(candidates['normal']['commit'] == candidates['lower']['commit'] and candidates['old78']['commit'] == OLD,
            'candidate revisions differ from normal/lower pair and fixed old78')
    inputs = {name: json.loads(Path(row['source_manifest']).read_text()) for name, row in candidates.items()}
    for name, row in candidates.items():
        require(digest(row['source_manifest']) == row['source_manifest_sha256'] and manifest(row['source']) == inputs[name]
                and digest(row['binary']) == row['binary_sha256'], name + ': inputs differ from build receipt')
        if name in ('normal', 'old78'):
            for key in ('capture_manifest', 'physical_build_receipt'):
                require(digest(row[key]) == row[key + '_sha256'], name + ': shared receipt differs from build receipt')
    source = Path(candidates['normal']['source'])
    sys.path.insert(0, str(source / 'scripts'))
    import disposable_host as dh
    import work_coordination_ui_fixture as ui
    import check_work_projection_restore as projection
    for helper in (dh, ui, projection):
        require(Path(helper.__file__).resolve().parent == source / 'scripts', 'helper escaped exact candidate')
    packet = {name: digest(path) for name, path in FIXTURE_PATHS.items()}
    report = dict(schema='cairn.lower-support79-proof/1', status='running', stages=[], commands=[], http=[],
        candidate_commits={name: row['commit'] for name, row in candidates.items()}, fixture_inputs=packet,
        build_receipt_sha256=digest(args.build_receipt), cluster_cleaned=False,
        claims='real CLI, warm HTTP, parsed synthetic provider and real process timeout; no model/subscription/deployment claim')
    root = Path(tempfile.mkdtemp(prefix='cairn-lower79-'))
    report['runtime'] = str(root)
    cluster = dh.OwnedCluster(root); case = None; original = None; cleanup_error = None; binaries = {}
    try:
        binary_root = root / 'binaries'; binary_root.mkdir(mode=0o700)
        for name, row in candidates.items():
            target = binary_root / name; shutil.copyfile(row['binary'], target); target.chmod(0o500)
            require(digest(target) == row['binary_sha256'], 'candidate changed during private copy')
            binaries[name] = target
        cluster = dh.OwnedCluster.start(root)
        with dh.DisposableHost.start(cluster, repo='fixture:lower79:' + uid(), binary=binaries['normal'],
                                    database='cairn_lower79', roles=('sender',)) as host:
            migration_sql = "SELECT json_agg(json_build_object('version',version,'digest',encode(digest,'hex')) ORDER BY version)::text FROM public.cairn_migration"
            migrations = host.sql(migration_sql)
            report['migration_rows_before'] = json.loads(migrations)
            require([row['version'] for row in json.loads(migrations)] == list(range(1, 80)), 'candidate schema not contiguous79')
            case = Case(host, binaries, ui, dh.run_envelope, report, output)
            try:
                case.setup(projection.PLANE_PROGRAM, dh.write_private)
                case.run()
                migrated_after = host.sql(migration_sql)
                report['migration_rows_after'] = json.loads(migrated_after)
                require(migrated_after == migrations, 'migrations changed during compatibility proof')
            except BaseException as error:
                original = error
                raise
            finally:
                try:
                    case.close()
                except BaseException as error:
                    cleanup_error = error
                    report['cleanup_error'] = str(error)
                    if original is None:
                        raise
        report['status'] = 'passed'
    except BaseException as error:
        original = original or error
        report.update(status='failed', error=str(original), error_type=type(original).__name__)
    finally:
        try:
            report['pinned_binary_sha256_after'] = {name: digest(path) for name, path in binaries.items()}
            require(all(report['pinned_binary_sha256_after'][name] == candidates[name]['binary_sha256'] for name in binaries),
                    'owned pinned executable changed during proof')
        except BaseException as error:
            report.update(status='failed', pinned_identity_error=str(error))
        try:
            if cleanup_error is None and report['status'] == 'passed':
                cluster.remove(); report['cluster_cleaned'] = not root.exists()
            else:
                report['cluster_stopped'] = cluster.stop()
        except BaseException as error:
            cleanup_error = cleanup_error or error
            report.update(status='failed', cluster_cleanup_error=str(error))
        try:
            report['inputs_unchanged'] = all(manifest(row['source']) == inputs[name]
                and digest(row['source_manifest']) == row['source_manifest_sha256']
                and digest(row['binary']) == row['binary_sha256']
                and all(digest(row[key]) == row[key + '_sha256'] for key in ('capture_manifest', 'physical_build_receipt') if key in row)
                for name, row in candidates.items())
            require(report['inputs_unchanged'] and {name: digest(path) for name, path in FIXTURE_PATHS.items()} == packet
                    and args.build_receipt.read_bytes() == build_raw, 'source/binary/helper/receipt changed during proof')
        except BaseException as error:
            report.update(status='failed', identity_error=str(error))
        save(output / 'report.json', report)
    require(report['status'] == 'passed' and report['cluster_cleaned'] and cleanup_error is None,
            'lower-support79 proof incomplete: ' + report.get('error', report.get('identity_error', 'cleanup unconfirmed')))
    print(json.dumps(dict(status=report['status'], stages=report['stages'], cluster_cleaned=True)))


if __name__ == '__main__':
    main()
