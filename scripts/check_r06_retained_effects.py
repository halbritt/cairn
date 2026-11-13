#!/usr/bin/env python3
"""Qualify retained Python-issued effects against Go, then retained Python.

This is a disposable compatibility fixture, not a native refresh or unit
activation acceptance. All identities and journals come from the product.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

from check_r06_native_unit import (NativeUnit, checked_images, digest,
    owned_command, write)
import stage_lifecycle_compatibility as installer
from disposable_host import OwnedCluster, pg_bindir, require


def go_issued(control, legacy, issued):
    """Replace only the executor; preserve the actual producer's command tail."""
    prefix = [str(legacy['binary']), str(legacy['entry'])]
    require(issued[:2] == prefix and issued[2:3] == ['inbox'],
            'issued command is not from the selected retained Python producer')
    return [str(control), 'coordination', *issued[2:]]


def journal_observation(journal):
    """Report reply possibility separately from delivery completion/settlement."""
    completion = journal.get('completion') or {}
    response = journal.get('response') or {}
    pending = [name for name in ('completion', 'response')
               if (journal.get(name) or {}).get('status') == 'pending']
    reply_possible = (journal.get('kind') in (None, 'request')
        and completion.get('status') == 'committed' and completion.get('result') is not None
        and not journal.get('finish_reply') and not response)
    return dict(pending=pending, completed=completion.get('status') == 'committed',
        settled=not pending, reply_possible=reply_possible,
        replied=response.get('status') == 'committed')


def snapshot(paths):
    result = {}
    for path in paths:
        path = Path(path)
        info = path.stat()
        require(path.is_file() and not path.is_symlink(), 'protected regular file required')
        result[str(path)] = dict(sha256=digest(path), bytes=info.st_size,
            mode=info.st_mode & 0o777, uid=info.st_uid, gid=info.st_gid,
            inode=info.st_ino, device=info.st_dev)
    return result


def preserve(paths, destination, *, excluded=()):
    """Retain selected fixture bytes, excluding even disposable credentials."""
    destination.mkdir(mode=0o700)
    selected = []
    excluded = {str(Path(path)) for path in excluded}
    for index, path in enumerate(paths):
        path = Path(path)
        if str(path) in excluded:
            continue
        target = destination / str(index)
        target.write_bytes(path.read_bytes())
        target.chmod(0o600)
        selected.append(dict(path=str(path), retained=str(target), sha256=digest(target)))
    write(destination / 'index.json', selected)


class EffectsUnit(NativeUnit):
    def stage_go(self):
        # Both installed profiles select one real state/API/credential unit.
        self.coordination.update(compact_inbox_version=1,
            fixture_opaque=dict(integer=900719925474099312345, text='日本語 🙂'))
        return super().stage_go()

    def stage_legacy(self, python=None):
        legacy = super().stage_legacy(python)
        # This is the documented bare compact lookup's configuration location,
        # provisioned from the actual installed profile, before any callback.
        bindings = self.root / 'bindings'
        bindings.mkdir(mode=0o700)
        path = bindings / (self.binding + '.json')
        path.write_bytes(legacy['config'].read_bytes())
        path.chmod(0o600)
        self.compact_config = path
        return legacy

    def callback(self, release, event, label):
        return self.setup_callback(release, 'coordination',
            dict(session_id=self.native, cwd=str(self.work), **event), label)

    def publish(self, kind, label):
        state = self.state()
        source = self.host.agent('sender', 'create', dict(request_id=str(uuid.uuid4()), draft=dict(
            kind='note', body='Owned retained-effects ' + label + '. 日本語 🙂',
            scope=dict(repo=self.host.repo, task_id='*', run_id='*'),
            claim_type='self', sensitivity='shareable')))
        event = self.host.agent('sender', 'event-publish', dict(request_id=str(uuid.uuid4()), kind=kind,
            ref=dict(record_id=source['record_id'], version=source['version']),
            destination=dict(type='agent', name=state['agent']['inbox'])))
        self.callback(self.legacy, dict(hook_event_name='UserPromptSubmit',
            prompt='Read the single owned compatibility fixture ' + label), label + '-claim')
        state = self.state()
        attempt = state['inbox_attempt']
        path = self.state_dir / 'inbox' / (attempt['attempt_id'] + '.json')
        context = json.loads(path.read_bytes())
        require(context['event_id'] == event['event_id'] and context['source'] ==
                dict(record_id=source['record_id'], version=source['version']),
                'actual retained producer claimed a different source')
        require(context['agent_id'] == state['agent']['agent_id'] and
                context['execution_id'] == state['agent']['execution_id'], 'issued session mismatch')
        write(self.evidence / (label + '-issued.json'), dict(path=str(path), context=context,
            source=source, event=event, attempt=attempt, producer='retained Python /2'))
        return context, path

    def journal(self, context, label):
        path = self.state_dir / 'intents' / (context['attempt_id'] + '.json')
        raw = path.read_bytes()
        journal = json.loads(raw)
        require(journal['attempt_id'] == context['attempt_id'] and journal['delivery_id'] == context['delivery_id']
            and journal['session'] == {key: context[key] for key in ('agent_id', 'execution_id')},
            'product journal ownership differs from issued context')
        (self.evidence / (label + '.journal.raw.json')).write_bytes(raw)
        observation = journal_observation(journal)
        write(self.evidence / (label + '.observation.json'), observation)
        return journal, observation

    def effects(self, context):
        delivery = str(uuid.UUID(context['delivery_id']))
        event = str(uuid.UUID(context['event_id']))
        row = json.loads(self.host.sql("SELECT row_to_json(d)::text FROM cairn.agent_delivery d "
            f"WHERE delivery_id='{delivery}'"))
        replies = json.loads(self.host.sql("SELECT COALESCE(jsonb_agg(to_jsonb(e) ORDER BY position),'[]'::jsonb)::text "
            f"FROM cairn.agent_event e WHERE causation_id='{event}'"))
        versions = 0 if not row['result_id'] else int(self.host.sql(
            f"SELECT count(*) FROM cairn.record_version WHERE record_id='{row['result_id']}'"))
        return dict(delivery=row, replies=replies, result_versions=versions)

    def compact(self, release, operation, context, label, *, foreign=None):
        handle = context['compact_handle']
        original = ['cairn-inbox', operation, handle]
        tail = ['inbox', 'compact', operation, handle]
        if operation == 'settle':
            original.append('handled')
            tail.append('handled')
        # Retained cues name a bare installed command. Its documented CLI
        # expansion is used directly; no replacement HOME or global PATH edit.
        argv = ([str(self.legacy['binary']), str(self.legacy['entry']), *tail]
                if release is self.legacy else [str(self.go['binary']), 'coordination', *tail])
        env = dict(self.host.env, CAIRN_INBOX_ROOT=str(self.root),
            CLAUDE_SESSION_ID=foreign or self.native)
        write(self.evidence / (label + '.cue.json'), dict(issued=original, executed=argv,
            lookup_root=str(self.root), native=foreign or self.native))
        return owned_command(argv, self.evidence / label, env=env, expected=None)


def assert_observation(actual, *, pending, completed, settled, reply_possible, replied):
    require(actual == dict(pending=pending, completed=completed, settled=settled,
        reply_possible=reply_possible, replied=replied), 'unexpected journal state: ' + json.dumps(actual))


def exercise(unit):
    unit.native = str(uuid.uuid4())
    unit.callback(unit.legacy, dict(hook_event_name='SessionStart', source='startup'), 'python2-start')
    owner = unit.native
    request, context_path = unit.publish('request', 'request')
    # A second actual fixture session owns an independent live delivery. Its
    # state and effect rows are protected throughout the owner's operations.
    unit.native = str(uuid.uuid4())
    unit.callback(unit.legacy, dict(hook_event_name='SessionStart', source='startup'), 'foreign-start')
    foreign_native = unit.native
    foreign, foreign_path = unit.publish('notice', 'foreign')
    foreign_state = unit.state_path
    foreign_effects = unit.effects(foreign)
    unit.native = owner

    mutable = (unit.state_dir, unit.memory_dir)
    protected_paths = [path for path in unit.root.rglob('*') if path.is_file()
        and not any(path.is_relative_to(directory) for directory in mutable)]
    protected_paths.extend([context_path, foreign_path, foreign_state, unit.state_path])
    protected = snapshot(protected_paths)
    write(unit.evidence / 'protected-before.json', protected)
    preserve(protected_paths, unit.evidence / 'protected-bytes-before',
        excluded=[path for path in protected_paths if path.is_relative_to(unit.host.home)])
    original = request['completion']
    command = go_issued(unit.go['binary'], unit.legacy, original)
    result_body = 'Selected retained-effects result. 日本語 🙂\n'

    # A real transport outage creates the pending journal through the actual
    # retained command. No journal, auth field or API response is fabricated.
    process = unit.host.process
    require(process is not None and process.pid > 1, 'owned API process required')
    pid = process.pid
    stopped = unit.host.stop_server()
    write(unit.evidence / 'transport-outage.json', dict(pid=pid, exit=stopped,
        joined=process.poll() is not None, group_signals=False))
    pending = owned_command(original, unit.evidence / 'python2-pending', body=result_body,
        env=unit.host.env, expected=None)
    require(pending.returncode != 0 and json.loads(pending.stdout)['ok'] is False,
            'real unavailable API did not refuse the issued completion')
    _, observed = unit.journal(request, 'pending')
    assert_observation(observed, pending=['completion'], completed=False, settled=False,
        reply_possible=False, replied=False)
    unit.host.start_server()

    completed = owned_command(command, unit.evidence / 'go3-retry', body=result_body, env=unit.host.env)
    ref = json.loads(completed.stdout)['data']['result']
    _, observed = unit.journal(request, 'completed-reply-possible')
    assert_observation(observed, pending=[], completed=True, settled=True, reply_possible=True, replied=False)
    before_retry = unit.effects(request)
    owned_command(command, unit.evidence / 'go3-duplicate-completion', body=result_body, env=unit.host.env)
    require(unit.effects(request) == before_retry, 'duplicate completion produced another effect')
    reply = [*go_issued(unit.go['binary'], unit.legacy, request['response']),
             '--version', str(ref['version']), ref['record_id']]
    owned_command(reply, unit.evidence / 'go3-reply', env=unit.host.env)
    _, observed = unit.journal(request, 'replied')
    assert_observation(observed, pending=[], completed=True, settled=True, reply_possible=False, replied=True)
    effects = unit.effects(request)
    require(effects['delivery']['state'] == 'handled' and effects['result_versions'] == 1
        and len(effects['replies']) == 1 and effects['replies'][0]['record_id'] == ref['record_id']
        and effects['replies'][0]['version'] == ref['version'], 'result/reply join or effect cardinality differs')

    # Explicit reverse pins the unchanged installed /2 generation. The old
    # issued references remain exact; this is not an activation-unit cutover.
    reverse = installer.inspect(str(unit.images['stager']), unit.legacy['selection'], legacy=True)
    write(unit.evidence / 'reverse-selection.json', dict(selection=unit.legacy['selection'],
        inspected=reverse, previous_executor=str(unit.go['binary']), current_executor=str(unit.legacy['binary']),
        native_refresh=False, whole_unit_acceptance=False))
    owned_command(original, unit.evidence / 'reverse-python2-completion', body=result_body, env=unit.host.env)
    owned_command([*request['response'], '--version', str(ref['version']), ref['record_id']],
        unit.evidence / 'reverse-python2-reply', env=unit.host.env)
    require(unit.effects(request) == effects, 'retained reverse duplicated completion or reply')
    unit.journal(request, 'reverse-final')
    require(snapshot(protected_paths) == protected, 'issued effects changed protected unit bytes/metadata')
    write(unit.evidence / 'protected-after-request.json', snapshot(protected_paths))
    require(unit.effects(foreign) == foreign_effects, 'issued effects mutated another session delivery')

    # A fresh retained /2 notice issues the compact handle. Go reads/inspects
    # it; Python reads it after explicit reverse, Go settles and Python retries.
    unit.callback(unit.legacy, dict(hook_event_name='Stop'), 'python2-request-stop')
    identity = {key: unit.state()['agent'][key] for key in ('agent_id', 'execution_id')}
    unit.callback(unit.go, dict(hook_event_name='SessionStart', source='resume'), 'go3-retained-state-resume')
    unit.callback(unit.go, dict(hook_event_name='Stop'), 'go3-retained-state-stop')
    require({key: unit.state()['agent'][key] for key in identity} == identity,
        'actual staged Go callback changed the retained session/execution')
    write(unit.evidence / 'compact-reverse-selection.json', installer.inspect(
        str(unit.images['stager']), unit.legacy['selection'], legacy=True))
    unit.callback(unit.legacy, dict(hook_event_name='SessionStart', source='resume'), 'python2-state-reverse')
    notice, notice_path = unit.publish('notice', 'compact')
    require('compact_handle' in notice, 'actual retained callback did not issue a compact handle')
    compact_protected = snapshot([notice_path, unit.state_path, foreign_path, foreign_state,
        unit.state_dir / 'compact' / (notice['compact_handle'] + '.json')])
    before_read = unit.effects(notice)
    for release, label in ((unit.go, 'go3'), (unit.legacy, 'reverse-python2')):
        for operation in ('read', 'inspect'):
            result = unit.compact(release, operation, notice, label + '-compact-' + operation)
            require(result.returncode == 0 and json.loads(result.stdout)['ok'] is True,
                'actual compact ' + operation + ' refused: ' + result.stdout + result.stderr)
            value = json.loads(result.stdout)
            if operation == 'read':
                require(value['source'] == notice['source'] and value['body'] ==
                    'Owned retained-effects compact. 日本語 🙂', 'compact reader returned another source/body')
            else:
                require(value['context'] == notice, 'compact inspector changed the issued context')
    refused = unit.compact(unit.go, 'settle', notice, 'go3-foreign-session-refusal', foreign=foreign_native)
    require(refused.returncode != 0 and json.loads(refused.stdout)['status'] == 'NOT_THIS_SESSION',
        'cross-reader did not refuse the foreign native session')
    require(snapshot(list(map(Path, compact_protected))) == compact_protected,
        'read/inspect/cross-reader refusal changed native compact state')
    after_read = unit.effects(notice)
    # Read/inspect renew only this delivery's lease; every other delivery
    # field and all result/reply cardinalities must remain unchanged.
    for row in (before_read['delivery'], after_read['delivery']):
        row.pop('lease_until')
    require(after_read == before_read, 'compact reader/refusal changed delivery effects')
    require(unit.effects(foreign) == foreign_effects, 'compact reads/refusal changed foreign delivery')
    result = unit.compact(unit.go, 'settle', notice, 'go3-compact-settle')
    require(result.returncode == 0 and json.loads(result.stdout)['status'] == 'handled',
        'actual Go compact settlement failed: ' + result.stdout + result.stderr)
    notice_effects = unit.effects(notice)
    result = unit.compact(unit.legacy, 'settle', notice, 'reverse-python2-compact-retry')
    require(result.returncode == 0 and json.loads(result.stdout)['status'] == 'handled',
        'retained compact retry failed: ' + result.stdout + result.stderr)
    require(unit.effects(notice) == notice_effects and notice_effects['delivery']['attempts'] == 1
        and notice_effects['delivery']['state'] == 'handled' and not notice_effects['replies'],
        'compact retry duplicated an effect')
    require(unit.effects(foreign) == foreign_effects, 'compact settlement affected another session')
    require(snapshot(list(map(Path, compact_protected))) == compact_protected,
        'compact settlement/retry changed protected native state')
    fixed_paths = [path for path in protected_paths if Path(path) != unit.state_path]
    require(snapshot(fixed_paths) == {key: value for key, value in protected.items()
        if key != str(unit.state_path)}, 'compact operations changed installed/opaque/foreign state')
    write(unit.evidence / 'compact-protected-after.json', snapshot(list(map(Path, compact_protected))))
    preserve([notice_path, unit.state_path,
        unit.state_dir / 'compact' / (notice['compact_handle'] + '.json')], unit.evidence / 'compact-product-bytes')
    write(unit.evidence / 'effects-result.json', dict(request=request['event_id'], result=ref,
        request_effects=effects, notice_effects=notice_effects, protected_bytes_preserved=True,
        foreign_delivery_preserved=True, pending_exercised=True, reply_possible_exercised=True,
        native_client_executed=False, whole_unit_acceptance=False, native_refresh=False))


def child(images_path, evidence):
    images, pins = checked_images(images_path)
    write(evidence / 'images-before-use.json', pins)
    # Short owned fixture paths also keep the real Unix socket below its limit.
    scratch = Path(tempfile.mkdtemp(prefix='r06effects-'))
    write(evidence / 'scratch.json', dict(path=str(scratch)))
    cluster, unit = None, None
    try:
        cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bindir())
        unit = EffectsUnit(scratch / 'unit', evidence, images, cluster)
        exercise(unit)
    finally:
        try:
            if unit is not None:
                unit.close()
        finally:
            if cluster is not None:
                pidfile = scratch / 'pg/data/postmaster.pid'
                pid = int(pidfile.read_text().splitlines()[0]) if pidfile.exists() else None
                require(pid is None or pid > 1, 'unsafe owned PG PID')
                stopped = cluster.stop()
                write(evidence / 'pg-cleanup.json', dict(pid=pid, stopped=stopped, group_signals=False))
                require(stopped, 'owned PG did not stop')
            after = {role: dict(path=str(path), sha256=digest(path)) for role, path in images.items()}
            write(evidence / 'images-after-use.json', after)
            require(all(after[role]['sha256'] == pins['images'][role]['sha256'] for role in images),
                'selected image changed during effects gate')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--child', action='store_true')
    args = parser.parse_args()
    require(args.evidence.is_absolute() and not args.evidence.exists(), 'new absolute evidence path required')
    args.evidence.mkdir(mode=0o700)
    images, pins = checked_images(args.images)
    write(args.evidence / 'images-before-owner.json', pins)
    if args.child:
        child(args.images, args.evidence)
        return
    plan, product = args.evidence / 'owner.plan.json', args.evidence / 'owner.product.json'
    program = Path(sys.executable).resolve()
    write(plan, dict(program=str(program), sha256=digest(program), input='', result=str(product), timeout_ms=420000,
        args=['-B', str(Path(__file__).resolve()), '--child', '--images', str(args.images),
              '--evidence', str(args.evidence / 'effects')]))
    owned_command([images['helper'], '-test.run=^TestR06OwnedUnitFixtureProcess$', '-test.v'],
        args.evidence / 'owner', env=dict(os.environ, CAIRN_R06_OWNED_UNIT_PLAN=str(plan)), timeout=420)
    actual = json.loads(product.read_bytes())
    require(actual['exit'] == 0 and not actual['timed_out'],
        'actual retained-effects gate refused: ' + actual['stdout'] + actual['stderr'])
    print('Retained issued effects: real Python2 -> Go3 -> Python2 PASS; native refresh NOTRUN', flush=True)


if __name__ == '__main__':
    main()
