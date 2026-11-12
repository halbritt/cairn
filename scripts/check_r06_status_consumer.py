#!/usr/bin/env python3
"""Qualify one public drain consumer against fixture-owned API/PostgreSQL.

No model, installed profile, live store, or native owner is used. The fixture
actors create/settle synthetic obligations; the inspected consumer only reads.
Run via mise host:exec; --evidence retains raw fixture inputs and outputs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from disposable_host import DisposableHost, OwnedCluster, require

ROOT = Path(__file__).resolve().parents[1]
BASE = 'f2080c8bec82a715c6ca84089c40cd374ce64883'


def uid():
    return str(uuid.uuid4())


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, body):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(body, sort_keys=True, indent=2) + '\n')
    path.chmod(0o600)


def run(argv, prefix, *, env=None, body=None, timeout=120):
    argv = list(map(str, argv))
    write(prefix.with_suffix('.argv.json'), argv)
    result = subprocess.run(argv, cwd=ROOT, env=env, input=body, text=True,
                            capture_output=True, timeout=timeout, check=False)
    prefix.with_suffix('.stdout').write_text(result.stdout)
    prefix.with_suffix('.stderr').write_text(result.stderr)
    write(prefix.with_suffix('.exit.json'), result.returncode)
    require(result.returncode == 0, f'{prefix}: exit {result.returncode}: {result.stderr[-2000:]}')
    return result


def build(evidence, tools):
    tools.mkdir(mode=0o700)
    images = {'api': tools / 'cairn', 'consumer': tools / 'consumer-tests'}
    run(['go', 'build', '-buildvcs=false', '-o', images['api'], './cmd/cairn'], tools / 'build-api')
    run(['go', 'test', '-c', '-o', images['consumer'], './internal/lifecycle/coordination'], tools / 'build-consumer')
    for image in images.values():
        image.chmod(0o500)
    version = run(['go', 'env', '-json', 'GOVERSION', 'GOROOT', 'GOTOOLDIR', 'GOTOOLCHAIN'], tools / 'go-env')
    toolchain = json.loads(version.stdout)
    metadata = dict(base=BASE, head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    tree=subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=ROOT, text=True).strip(),
                    go=toolchain, images={k: dict(path=str(v), sha256=sha(v.read_bytes())) for k, v in images.items()},
                    files={name: sha((ROOT / name).read_bytes()) for name in (
                        'go.mod', 'go.sum', 'scripts/disposable_host.py',
                        'scripts/check_r06_status_consumer.py',
                        'internal/lifecycle/coordination/shared_unit_api_integration_test.go')},
                    executables={str(p): sha(p.read_bytes()) for p in (
                        Path(shutil.which('go')).resolve(), Path(shutil.which('python3')).resolve())},
                    compiler_tools={str(p): sha(p.read_bytes()) for p in sorted(Path(toolchain['GOTOOLDIR']).iterdir()) if p.is_file()})
    write(tools / 'pins.json', metadata)
    return images


class Fixture:
    def __init__(self, cluster, binary, root, label):
        self.root = root
        root.mkdir(mode=0o700)
        self.seq = 0
        self.host = DisposableHost.start(cluster, binary=binary, repo='fixture:r06:' + label,
            database='r06_' + label.replace('-', '_'), home=root / 'home',
            roles=('sender', 'worker'), label='r06_' + label,
            log=root / 'api.log', socket_path=root / 'api.sock')
        self.repo = self.host.repo
        self.binding = 'r06-consumer'
        self.native = 'fixture-' + uid()
        self.agent = self.register()
        self.session = self.ref(self.agent)
        self.state_dir = root / 'state'
        self.state_dir.mkdir(mode=0o700)
        self.state_path = self.state_dir / (sha((self.binding + '\0' + self.native).encode()) + '.json')
        self.state = dict(schema='cairn.native-session/1', native_session_id=self.native,
                          agent=self.agent, inbox_journals=[], registration=dict(binding=self.binding, native_session_id=self.native))
        self.active = root / 'bindings'
        self.active.mkdir(mode=0o700)

    @staticmethod
    def ref(agent):
        return {key: agent[key] for key in ('agent_id', 'execution_id')}

    def call(self, role, operation, body, session=None, expected='OK'):
        self.seq += 1
        output = self.host.agent(role, operation, body, session=session, expected=expected)
        write(self.root / f'actor-{self.seq:03d}.json', dict(role=role, operation=operation,
              input=body, session=session, response=output))
        return output

    def register(self):
        return self.call('worker', 'agent-register', dict(request_id=uid(), binding=self.binding,
            native_session_id=self.native, metadata=dict(harness='codex', project='r06-fixture',
            workspace=str(self.root), state='idle', delivery_mode='existing-session')))

    def claim(self):
        source = self.call('sender', 'create', dict(request_id=uid(), draft=dict(kind='note',
            body='Synthetic R06 consumer fixture request', scope=dict(repo=self.repo, task_id='*', run_id='*'),
            claim_type='self', sensitivity='shareable')))
        event = self.call('sender', 'event-publish', dict(request_id=uid(), kind='request',
            ref=dict(record_id=source['record_id'], version=source['version']),
            destination=dict(type='agent', name=self.agent['inbox'])))
        delivery = self.call('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
        body = dict(request_id=uid(), session=self.session, delivery_id=delivery['delivery_id'],
                    native_turn_id='fixture-turn-' + uid(), turn_exclusive=True)
        attempt = self.call('worker', 'session-inbox-claim', body)['attempt']
        return event, body, attempt

    def retained(self, *, settle=True, close=True, resume=False):
        event, intent, attempt = self.claim()
        self.state.update(inbox_attempt=attempt, inbox_intent=intent)
        if settle:
            completion = dict(request_id=uid(), delivery_id=attempt['delivery']['delivery_id'],
                lease_id=attempt['delivery']['lease_id'], disposition='handled', draft=dict(kind='note',
                body='Synthetic selected R06 result', scope=dict(repo=self.repo, task_id='*', run_id='*'),
                claim_type='self', sensitivity='shareable'))
            completed = self.call('worker', 'event-complete', completion, session=self.session)
            ref = completed['result']
            reply = dict(request_id=uid(), kind='response', ref=ref,
                         destination=dict(type='agent', name=self.host.principal('sender')),
                         causation_id=event['event_id'], correlation_id=event['event_id'])
            response = self.call('worker', 'event-publish', reply, session=self.session)
            aid = attempt['attempt_id']
            journal = dict(schema='cairn.inbox-intent/1', attempt_id=aid,
                delivery_id=attempt['delivery']['delivery_id'], session=self.session, kind='request',
                completion=dict(kind='complete', request_id=completion['request_id'], status='committed', result=ref),
                finish_reply=dict(request_id=reply['request_id'], argv=['fixture-settled-reply']),
                response=dict(request_id=reply['request_id'], status='committed', expected_ref=ref, event=response))
            write(self.state_dir / 'intents' / (aid + '.json'), journal)
            self.state.update(inbox_completion=completion['request_id'], inbox_response=reply['request_id'],
                              inbox_journals=[aid])
            if close:
                closing = dict(request_id=uid(), session=self.session, attempt_id=aid, reason='delivery_completed')
                remote = self.call('worker', 'session-inbox-reconcile', closing)
                require(remote.get('finished_at'), 'fixture failed to close its own attempt')
                self.state['inbox_close'] = closing
        if resume:
            self.agent = self.register()
            self.session = self.ref(self.agent)
            self.state['agent'] = self.agent
            require(self.session != attempt['session'], 'historical fixture did not advance its execution')
        write(self.state_path, self.state)

    def configs(self, *, token=None, socket=None):
        binding = self.root / 'binding.json'
        common = dict(harness='codex', cairn=str(self.host.binary), socket=str(socket or self.host.socket),
                      token_file=str(token or self.host.token('worker')), repo=self.repo, inbox_recall_binding=str(binding))
        memory = dict(common, state_dir=str(self.root / 'memory'), recall_mode='agent_tools', context_bytes=9500)
        coord = dict(common, state_dir=str(self.state_dir), native_delivery=True, binding=self.binding)
        self.coord = self.root / 'coordination.json'
        memory_path = self.root / 'memory.json'
        write(memory_path, memory)
        write(self.coord, coord)
        desc = lambda p: dict(path=str(p), sha256=sha(p.read_bytes()))
        write(binding, dict(schema='cairn.inbox-recall-binding/2', enabled=False,
                            memory_config=desc(memory_path), coordination_configs=[desc(self.coord)]))
        write(self.active / 'one.json', coord)
        self.pins = {'one.json': sha((self.active / 'one.json').read_bytes())}
        self.next = self.root / 'next.json'
        # A different complete selection with the same valid application binding.
        self.next.write_bytes(self.coord.read_bytes() + b'\n')
        self.next.chmod(0o600)

    def snapshot(self):
        tables = self.host.sql("SELECT tablename FROM pg_tables WHERE schemaname='cairn' AND "
            "(tablename LIKE 'agent_%' OR tablename LIKE 'record%') ORDER BY tablename").splitlines()
        protected = {}
        for name in tables:
            require(name.replace('_', '').isalnum(), 'invalid protected table name')
            protected[name] = json.loads(self.host.sql(
                f"SELECT COALESCE(jsonb_agg(row ORDER BY row::text),'[]'::jsonb)::text FROM "
                f"(SELECT to_jsonb(t) AS row FROM cairn.{name} t) AS rows" , limit=1000000))
        files = {str(p): sha(p.read_bytes()) for p in sorted(self.state_dir.rglob('*')) if p.is_file()}
        return dict(database=protected, state_files=files)

    def inspect(self, image, *, inject=None):
        plan = dict(Current=dict(Directory=str(self.active), Pins=self.pins),
            Retained=str(self.root / 'retained'), Next=[dict(name='one.json', path=str(self.next), sha256=sha(self.next.read_bytes()))],
            Result=str(self.root / 'consumer-result.json'), BeforeExchange=[], BeforeInput='')
        if inject:
            plan.update(BeforeExchange=[str(self.host.binary), 'agent', *map(str, self.host.connection('worker')),
                        'session-inbox-claim'], BeforeInput=json.dumps(inject))
        write(self.root / 'plan.json', plan)
        before = self.snapshot()
        write(self.root / 'before.json', before)
        run([image, '-test.run=^TestR06RealAPIConsumer$', '-test.v'], self.root / 'consumer',
            env=dict(self.host.env, CAIRN_R06_API_PLAN=str(self.root / 'plan.json')), timeout=40)
        result = json.loads((self.root / 'consumer-result.json').read_text())
        after = self.snapshot()
        write(self.root / 'after.json', after)
        if not inject:
            require(before == after, 'inspection mutated protected native/delivery/result/journal state')
        return result


def check(evidence, cases):
    scratch = Path(tempfile.mkdtemp(prefix='r06-', dir=Path.home() / '.cache'))
    write(evidence / 'scratch.json', dict(path=str(scratch)))
    # The consumer verifies every binary ancestor. Keep its frozen executable
    # under this private fixture root even when the evidence parent is shared.
    images = build(evidence, scratch / 'tools')
    cluster = OwnedCluster.start(scratch / 'pg')
    outcomes = []
    try:
        for case in cases:
            root = scratch / case
            f = Fixture(cluster, images['api'], root, case)
            try:
                f.retained(settle=case != 'active', close=case != 'unfinished', resume=case == 'historical')
                if case == 'missing':
                    old = f.state['inbox_attempt']['attempt_id']
                    new = uid()
                    f.state['inbox_attempt']['attempt_id'] = new
                    f.state['inbox_intent']['request_id'] = new
                    shutil.copyfile(f.state_dir / 'intents' / (old + '.json'), f.state_dir / 'intents' / (new + '.json'))
                    write(f.state_path, f.state)
                f.configs(token=f.host.token('sender') if case == 'unauthorized' else None,
                          socket=f.root / 'absent.sock' if case == 'unavailable' else None)
                injection = None
                if case == 'invalidate':
                    # Publish a new pending obligation as a fixture actor. A
                    # real claim after capture must invalidate the receipt.
                    # A fresh unclaimed event is safe to inspect, then held.
                    source = f.call('sender', 'create', dict(request_id=uid(), draft=dict(kind='note', body='invalidation',
                        scope=dict(repo=f.repo, task_id='*', run_id='*'), claim_type='self', sensitivity='shareable')))
                    event = f.call('sender', 'event-publish', dict(request_id=uid(), kind='notice',
                        ref=dict(record_id=source['record_id'], version=source['version']), destination=dict(type='agent', name=f.agent['inbox'])))
                    delivery = f.call('sender', 'event-inspect', dict(event_id=event['event_id']))['deliveries'][0]
                    injection = dict(request_id=uid(), session=f.session, delivery_id=delivery['delivery_id'],
                                     native_turn_id='fixture-invalidate', turn_exclusive=True)
                result = f.inspect(images['consumer'], inject=injection)
                positive = case in ('terminal', 'historical')
                require(result['Exchange']['Published'] == positive, f'{case}: wrong publication: {result}')
                if positive:
                    require(result['Receipt'] and not result['Error'], f'{case}: terminal refused: {result}')
                    reads = [c for c in result['Calls'] if c['Argv'][-1] == 'session-inbox-attempt-status']
                    require(len(reads) >= 2, 'receipt/exchange did not both read exact historical status')
                    for read in reads:
                        remote = json.loads(read['Stdout'])['data']
                        require(remote['session'] == f.state['inbox_attempt']['session'], 'history execution changed')
                        require(not remote['delivery'].get('lease_id') and not remote['delivery'].get('lease_until'), 'status exposed lease')
                else:
                    require(bool(result['Error']), f'{case}: refusal lost error')
                    require(not (f.root / 'retained').exists(), 'refusal published retained selection')
                    require(sha((f.active / 'one.json').read_bytes()) == f.pins['one.json'], 'refusal changed old selection')
                    if case == 'invalidate':
                        require(result['Receipt'] and 'invalidated' in result['Error'], 'claim did not invalidate initial receipt')
                        actor = json.loads(result['ActorOutput'])
                        require(actor['ok'] and actor['data']['attempt'], 'fixture claim never happened')
                    else:
                        require(not result['Receipt'], 'unknown/held view created receipt')
                outcomes.append(dict(case=case, status='PASS', receipt=result['Receipt'], published=result['Exchange']['Published'], error=result['Error']))
                print(json.dumps(outcomes[-1]), flush=True)
            finally:
                f.host.close()
    finally:
        stopped = cluster.stop()
        write(evidence / 'cleanup.json', dict(postgres_stopped=stopped,
            api_pid_files=list(map(str, scratch.rglob('postmaster.pid')))))
        require(stopped, 'fixture PostgreSQL cleanup unresolved')
        shutil.copytree(scratch, evidence / 'fixtures', ignore=shutil.ignore_patterns('data', 'socket', '*.token', 'identities.json', 'api.sock'))
    write(evidence / 'outcomes.json', outcomes)
    print(f'R06 consumer: {len(outcomes)} passed, 0 failed, 0 skipped', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--case', action='append', choices=('terminal', 'historical', 'active', 'unfinished', 'missing', 'unauthorized', 'unavailable', 'invalidate'))
    args = parser.parse_args()
    args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    check(args.evidence, args.case or ['terminal', 'historical', 'active', 'unfinished', 'missing', 'unauthorized', 'unavailable', 'invalidate'])
