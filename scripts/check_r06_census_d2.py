#!/usr/bin/env python3
"""Authenticated retained-attempt/census join using disposable API/PG fixtures."""
import argparse
import copy
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

import check_r06_census_control as composition
import check_r06_status_consumer as status
import check_r06_unit_census as census
import check_r06_unit_control as control
from disposable_host import OwnedCluster, pg_bindir, require

ROOT = Path(__file__).resolve().parents[1]
CASES = ('matched-policy', 'mismatched-claim', 'no-journal', 'unknown-attempt',
         'wrong-session', 'final-policy-change', 'final-claim')


def source_policy():
    path = ROOT / 'integrations/lifecycle/coordination.py'
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location('census_d2_source', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PolicyOwner(status.Fixture):
    def claim(self):
        event, intent, attempt = super().claim()
        self.state['inbox_result_policy'] = dict(claim_request_id=intent['request_id'], version=1)
        return event, intent, attempt

    def call(self, role, operation, body, session=None, expected='OK'):
        if operation == 'event-complete':
            scope = self.source.completion_scope(self.state, self.state['inbox_attempt'])
            require(scope == ['--task', 'coordination-result/' + body['delivery_id'], '--run', '*'],
                    'retained Source completion scope differs')
            census.write(self.root / 'actual-source-completion-scope.json', dict(scope=scope))
            body = copy.deepcopy(body)
            body['draft']['scope'].update(task_id=scope[1], run_id=scope[3])
        return super().call(role, operation, body, session=session, expected=expected)


def retain(fixture, path, source):
    owner = PolicyOwner.__new__(PolicyOwner)
    owner.root = fixture.evidence / ('owner-' + path.stem)
    owner.root.mkdir(mode=0o700)
    owner.host, owner.source, owner.repo, owner.seq = fixture.host, source, fixture.host.repo, 0
    owner.state_path, owner.state_dir = path, path.parent
    owner.state = json.loads(path.read_text())
    owner.agent = owner.state['agent']
    owner.session = owner.ref(owner.agent)
    owner.retained(settle=True, close=True)
    return owner


def forward(path, argv):
    plan = json.loads(path.read_text())
    counter = path.with_name('read-counts.json')
    counts = json.loads(counter.read_text()) if counter.exists() else {}
    actor = plan.get('d2_claim')
    if actor and argv[-1] == 'wake-attempts' and counts.get('wake-attempts', 0) == 3:
        census.write(path.with_name('actor-before.json'), control.snapshot(actor['dsn'], actor['pg_bin'], Path(actor['state_dir'])))
        result = control.run_envelope(actor['argv'], actor['body'], expected='OK', timeout=10)
        require(result['attempt'] and result['attempt']['session'] == actor['body']['session']
                and result['attempt']['delivery']['delivery_id'] == actor['body']['delivery_id'],
                'injected claim did not authenticate the matching fixture session/delivery')
        census.write(path.with_name('actor-result.json'), result)
        census.write(path.with_name('actor-after.json'), control.snapshot(actor['dsn'], actor['pg_bin'], Path(actor['state_dir'])))
    return composition.forward(path, argv)


def invoke(f, operation, label, *, error=None, actor_path=None, claim=False):
    plan = f.plan()
    path = f.evidence / (label + '.plan.json')
    census.write(path, plan)
    before = control.snapshot(f.host.dsn, f.host.pg_bin, f.universe / 'state-a')
    files_before = census.snapshot(f.universe)
    result = control.command([f.images['control'], 'unit-control', operation, '--plan', path,
        '--sha256', census.sha(path)], f.evidence / label, env=f.host.env, expected=1 if error else 0)
    parsed = json.loads(result.stdout)
    after = control.snapshot(f.host.dsn, f.host.pg_bin, f.universe / 'state-a')
    files_after = census.snapshot(f.universe)
    census.write(f.evidence / (label + '.preservation.json'), dict(before=before, after=after,
        files_before=files_before, files_after=files_after))
    if claim:
        require(before == json.loads(f.transport.with_name('actor-before.json').read_text()) and
                after == json.loads(f.transport.with_name('actor-after.json').read_text()),
                'inspector changed API state beyond the actual claim')
    else:
        require(before['database'] == after['database'], 'inspector changed protected API state')
        require({k:v for k,v in before['state_files'].items() if k != actor_path} ==
                {k:v for k,v in after['state_files'].items() if k != actor_path}, 'inspector changed retained session/journal files')
    protected = lambda rows: {k:v for k,v in rows.items()
        if not Path(k).is_relative_to(f.catalog['ConfigRoot']) and k != actor_path}
    require(protected(files_before) == protected(files_after), 'inspector changed protected files/grants/credentials')
    if error:
        require(error in parsed['error'] and not parsed['exchange']['Published'], f'wrong refusal: {parsed}')
        require({x.name:census.sha(x) for x in Path(plan['current']['directory']).glob('*.json')} ==
                plan['current']['pins'], 'refusal changed active selection')
        if actor_path or claim:
            require(parsed['inspected'], 'final invalidation happened before initial receipt')
            prepared = Path(plan['retained'])
            require({x.name for x in prepared.iterdir()} == {x['name'] for x in plan['next']} | {'.watch.lock'},
                    'final refusal lost full preparation')
            require({x.name:census.sha(x) for x in prepared.glob('*.json')} ==
                    {x['name']:x['sha256'] for x in plan['next']}, 'final refusal changed prepared pins')
            require(os.path.samefile(prepared / '.watch.lock', Path(plan['current']['directory']) / '.watch.lock'),
                    'final refusal split watch-lock inode')
        else:
            require(files_before == files_after, 'early refusal changed any fixture file')
    else:
        require(parsed['inspected'] and parsed['census']['FixtureReady'] and parsed['census']['DrainRequirements'],
                'combined path did not satisfy named requirements through the drain')
        require(parsed['census']['ExternalCoverage'] == 'unknown' and parsed['census']['NativeRefresh'] == 'unobserved',
                'finite join claimed native/external coverage')
        if operation != 'inspect':
            require(parsed['exchange']['Published'] and parsed['final_census']['FixtureReady'],
                    'combined publication/final census failed')
        else:
            require(files_before == files_after, 'inspect changed fixture files')
    return parsed


def run(evidence):
    scratch = Path(tempfile.mkdtemp(prefix='r06-census-d2-', dir=Path.home() / '.cache'))
    census.write(evidence / 'scratch.json', dict(path=str(scratch)))
    images = control.build(scratch)
    helper = scratch / 'tools/helper'
    control.command(['go', 'test', '-race', '-buildvcs=false', '-c', '-o', helper,
                     './internal/lifecycle/coordination'], scratch / 'tools/build-helper')
    helper.chmod(0o500)
    images['helper'] = helper
    control.command(['go', 'version', '-m', helper], scratch / 'tools/helper-modules')
    census.write(evidence / 'images-before-use.json', {k:dict(path=str(v), sha256=census.sha(v)) for k,v in images.items()})
    census.write(evidence / 'source-before-use.json', {str(x.relative_to(ROOT)):census.sha(x) for x in
        [Path(__file__), *sorted((ROOT / 'internal/lifecycle/coordination').glob('shared_unit*.go')),
         *(ROOT / 'scripts' / n for n in ('check_r06_status_consumer.py', 'check_r06_census_control.py',
          'check_r06_unit_control.py', 'check_r06_unit_census.py', 'disposable_host.py'))]})
    pg_bin = pg_bindir()
    census.write(evidence / 'PG-before-use.json', {n:dict(path=str(pg_bin/n), sha256=census.sha(pg_bin/n))
        for n in ('initdb','pg_ctl','postgres','createdb','psql')})
    cluster = OwnedCluster.start(scratch / 'pg', pg_bin=pg_bin)
    source = source_policy()
    outcomes = []
    try:
        for name in CASES:
            f = composition.Fixture(cluster, scratch, evidence, images, name)
            try:
                client = Path(f.catalog['Clients'][0]['Path'])
                client.chmod(0o700)
                client.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-B', str(Path(__file__).resolve()),
                    '--forward-api', str(f.transport)]) + ' "$@"\n')
                client.chmod(0o500)
                f.catalog['Clients'][0]['SHA256'] = census.sha(client)
                path = Path(f.proposal['Sessions'][0])
                owner = retain(f, Path(f.proposal['Sessions'][1]) if name == 'wrong-session' else path, source)
                state = json.loads(path.read_text())
                if name == 'wrong-session':
                    state.update({k:v for k,v in owner.state.items() if k.startswith('inbox_')})
                elif name == 'mismatched-claim':
                    state['inbox_result_policy']['claim_request_id'] = control.uid()
                elif name == 'unknown-attempt':
                    state['inbox_attempt']['attempt_id'] = control.uid()
                elif name == 'no-journal':
                    (owner.state_dir / 'intents' / (owner.state['inbox_attempt']['attempt_id'] + '.json')).unlink()
                census.write(path, state)
                for pin in f.catalog['Sessions']:
                    pin['SHA256'] = census.sha(pin['Path'])
                census.write(f.catalog_path, f.catalog)
                standalone = f.invoke(dict(Mode='inspect', Catalog=census.write(f.catalog_path, f.catalog), Proposal=f.proposal))
                require(not standalone['Census']['FixtureReady'] and standalone['Census']['DrainRequirements']
                        and 'authenticated drain correspondence' in standalone['Error'], 'standalone census waived retained requirements')
                census.write(f.evidence / 'standalone-requirements.json', standalone)
                if name == 'matched-policy':
                    invoke(f, 'inspect', 'inspect')
                    invoke(f, 'switch', 'switch')
                elif name == 'final-policy-change':
                    transport = json.loads(f.transport.read_text())
                    transport['mutation'] = dict(path=str(path), field='inbox_result_policy',
                        value=dict(claim_request_id=control.uid(), version=1), event_count=3)
                    census.write(f.transport, transport)
                    invoke(f, 'switch', 'negative', error='D6 drain receipt invalidated', actor_path=str(path))
                    require(f.transport.with_name('actor.json').exists(), 'final-read mutation was not exercised')
                elif name == 'final-claim':
                    note = f.host.agent('sender', 'create', dict(request_id=control.uid(), draft=dict(kind='note',
                        body='D2 join final claim', scope=dict(repo=f.host.repo, task_id='*', run_id='*'),
                        claim_type='self', sensitivity='shareable')))
                    event = f.host.agent('sender', 'event-publish', dict(request_id=control.uid(), kind='notice',
                        ref=dict(record_id=note['record_id'], version=note['version']),
                        destination=dict(type='agent', name=owner.agent['inbox'])))
                    delivery = f.host.agent('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
                    transport = json.loads(f.transport.read_text())
                    transport['d2_claim'] = dict(dsn=f.host.dsn, pg_bin=str(f.host.pg_bin), state_dir=str(f.universe/'state-a'),
                        argv=[str(images['api']), 'agent', *map(str,f.host.connection('worker')), 'session-inbox-claim'],
                        body=dict(request_id=control.uid(), session=owner.session, delivery_id=delivery['delivery_id'],
                                  native_turn_id='d2-final-claim', turn_exclusive=True))
                    census.write(f.transport, transport)
                    invoke(f, 'switch', 'negative', error='D6 drain receipt invalidated', claim=True)
                else:
                    error = {'mismatched-claim':'D2 retained result policy does not match', 'no-journal':'D3',
                             'unknown-attempt':'session-inbox-attempt-status', 'wrong-session':'session-inbox-attempt-status'}[name]
                    invoke(f, 'switch', 'negative', error=error)
                outcomes.append(dict(case=name, status='PASS'))
                print(json.dumps(outcomes[-1]), flush=True)
            finally:
                f.close()
    finally:
        pidfile = scratch / 'pg/data/postmaster.pid'
        pid = int(pidfile.read_text().splitlines()[0]) if pidfile.exists() else None
        require(pid is None or pid > 1, 'unsafe owned PG PID')
        group = os.getpgid(pid) if pid else None
        require(group is None or group > 1, 'unsafe owned PG group')
        stopped = cluster.stop()
        census.write(evidence / 'cleanup.json', dict(pid=pid, group=group, stopped=stopped, group_signals=False))
        require(stopped, 'owned PG cleanup unresolved')
    census.write(evidence / 'outcomes.json', outcomes)
    print(f'R06 census D2 join: {len(outcomes)} passed, 0 failed, 0 skipped', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--forward-api', type=Path)
    args, extra = parser.parse_known_args()
    if args.forward_api:
        raise SystemExit(forward(args.forward_api, extra))
    require(args.evidence is not None and not extra, '--evidence required')
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    run(args.evidence)
