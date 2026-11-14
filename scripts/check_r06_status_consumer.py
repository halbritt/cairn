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
import sys
import tempfile
import time
import uuid

from disposable_host import DisposableHost, OwnedCluster, require, run_envelope, run_sql, stop_process

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


def snapshot(dsn, pg_bin, state_dir):
    tables = run_sql(dsn, "SELECT tablename FROM pg_tables WHERE schemaname='cairn' AND "
        "(tablename LIKE 'agent_%' OR tablename LIKE 'record%') ORDER BY tablename", pg_bin=pg_bin).splitlines()
    protected = {}
    for name in tables:
        require(name.replace('_', '').isalnum(), 'invalid protected table name')
        protected[name] = json.loads(run_sql(dsn,
            f"SELECT COALESCE(jsonb_agg(row ORDER BY row::text),'[]'::jsonb)::text FROM "
            f"(SELECT to_jsonb(t) AS row FROM cairn.{name} t) AS rows", limit=1000000, pg_bin=pg_bin))
    files = {str(p): sha(p.read_bytes()) for p in sorted(state_dir.rglob('*')) if p.is_file()}
    return dict(database=protected, state_files=files)


def claim_actor(path):
    """Separate fixture actor; never called by the inspector transport."""
    plan = json.loads(path.read_text())
    before = snapshot(plan['dsn'], plan['pg_bin'], Path(plan['state_dir']))
    write(path.with_name('actor-before.json'), before)
    result = run_envelope(plan['argv'], plan['body'], expected=None, timeout=10)
    after = snapshot(plan['dsn'], plan['pg_bin'], Path(plan['state_dir']))
    write(path.with_name('actor-after.json'), after)
    print(json.dumps(result), flush=True)


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


def serialization_probe(argv, prefix, env, host, delivery_id):
    """Hold the actual row lock, observe the API waiting, then commit its
    concurrent MVCC version. Same-value UPDATE preserves logical state while
    forcing the documented RepeatableRead serialization boundary.
    """
    require(str(uuid.UUID(delivery_id)) == delivery_id, 'fixture delivery UUID required')
    writer_argv = [str(host.pg_bin / 'psql'), '-X', '-q', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-d', host.dsn]
    sql = f"BEGIN; UPDATE cairn.agent_delivery SET code=code WHERE delivery_id='{delivery_id}';\n"
    write(prefix.with_name('serialization-writer.json'), dict(argv=writer_argv, input=sql))
    writer = subprocess.Popen(writer_argv, env=dict(env, PGAPPNAME='r06-serialization-writer'),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    require(writer.pid > 1, 'owned writer PID must exceed 1')
    consumer = None
    observed = []
    try:
        writer.stdin.write(sql)
        writer.stdin.flush()
        deadline = time.monotonic() + 3
        backend = ''
        while time.monotonic() < deadline:
            backend = host.sql("SELECT pid FROM pg_stat_activity WHERE application_name='r06-serialization-writer' "
                               "AND state='idle in transaction'")
            if backend:
                break
            require(writer.poll() is None, 'fixture lock writer exited early')
            time.sleep(.02)
        require(backend.isdigit(), 'fixture lock writer did not reach its held row')
        write(prefix.with_suffix('.argv.json'), list(map(str, argv)))
        consumer = subprocess.Popen(list(map(str, argv)), env=env, cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        require(consumer.pid > 1, 'owned consumer PID must exceed 1')
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            blocked = host.sql("SELECT COALESCE(jsonb_agg(jsonb_build_object('pid',pid,'state',state,"
                "'wait_event_type',wait_event_type,'wait_event',wait_event,'query',query)),'[]'::jsonb)::text "
                f"FROM pg_stat_activity WHERE {int(backend)}=ANY(pg_blocking_pids(pid)) "
                "AND query LIKE '%cairn.agent_delivery%'")
            observed = json.loads(blocked)
            if observed:
                break
            require(consumer.poll() is None, 'consumer finished before the locked status read')
            time.sleep(.02)
        write(prefix.with_name('serialization-lock-observation.json'), dict(writer_backend=int(backend),
            blocked=observed, state='REPRODUCED' if observed else 'UNKNOWN'))
        require(observed, 'bounded serialization race UNKNOWN; no simulated refusal is supplied')
        writer_out, writer_err = writer.communicate('COMMIT;\n', timeout=5)
        write(prefix.with_name('serialization-writer-exit.json'), dict(pid=writer.pid, exit=writer.returncode,
            stdout=writer_out, stderr=writer_err))
        require(writer.returncode == 0, 'fixture lock writer failed to commit')
        stdout, stderr = consumer.communicate(timeout=30)
        prefix.with_suffix('.stdout').write_text(stdout)
        prefix.with_suffix('.stderr').write_text(stderr)
        write(prefix.with_suffix('.exit.json'), consumer.returncode)
        require(consumer.returncode == 0, f'consumer helper failed: {stderr}')
        diagnostic = (host.source.root / 'postgres.log').read_text()
        require(any(f"[{row['pid']}] ERROR:  could not serialize access due to concurrent update" in diagnostic
                    for row in observed), 'real PostgreSQL serialization diagnostic not retained')
    finally:
        for process in (writer, consumer):
            if process is not None:
                require(process.pid > 1, 'owned process PID must exceed 1')
                stop_process(process, timeout=5, label='R06 fixture child')


def build(evidence, tools):
    tools.mkdir(mode=0o700)
    images = {'api': tools / 'cairn', 'consumer': tools / 'consumer-tests'}
    run(['go', 'build', '-buildvcs=false', '-o', images['api'], './cmd/cairn'], tools / 'build-api')
    run(['go', 'test', '-race', '-c', '-o', images['consumer'], './internal/lifecycle/coordination'], tools / 'build-consumer')
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

    def retained(self, *, settle=True, close=True, resume=False, cleanup=False):
        event, intent, attempt = self.claim()
        self.state.update(inbox_attempt=attempt, inbox_intent=intent)
        if cleanup:
            executable = str(Path(shutil.which('true')).resolve())
            child = subprocess.Popen([executable])
            require(child.pid > 1, 'owned tool PID must exceed 1')
            try:
                capture = dict(request_id=uid(), session=self.session, attempt_id=attempt['attempt_id'],
                    items=[dict(item_id='fixture-tool', process_id=str(child.pid), command=executable,
                                native_turn_id=attempt['native_turn_id'])])
                self.call('worker', 'session-tool-capture', capture)
                code = child.wait(timeout=5)
                require(code == 0, 'owned fixture tool did not complete')
                report = dict(request_id=uid(), session=self.session, attempt_id=attempt['attempt_id'],
                    turn_stop='ended', capture_state='complete', terminal_scan='clear',
                    tools=[dict(item_id='fixture-tool', stop_state='terminated')])
                self.call('worker', 'session-tool-stop', report)
                self.state['tool_calls'] = {'fixture-tool': dict(attempt_id=attempt['attempt_id'], ended=True, capture=capture)}
                self.state['cancel_final_report'] = report
                write(self.root / 'owned-tool.json', dict(pid=child.pid, executable=executable,
                    sha256=sha(Path(executable).read_bytes()), exit=code, joined=True))
            finally:
                stop_process(child, timeout=5, label='R06 fixture tool')
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
        return snapshot(self.host.dsn, self.host.pg_bin, self.state_dir)

    def inspect(self, image, *, inject=None, serialization=False):
        plan = dict(Current=dict(Directory=str(self.active), Pins=self.pins),
            Retained=str(self.root / 'retained'), Next=[dict(name='one.json', path=str(self.next), sha256=sha(self.next.read_bytes()))],
            Result=str(self.root / 'consumer-result.json'), BeforeExchange=[], BeforeInput='')
        if inject:
            actor = self.root / 'claim-actor.json'
            write(actor, dict(dsn=self.host.dsn, pg_bin=str(self.host.pg_bin), state_dir=str(self.state_dir),
                argv=[str(self.host.binary), 'agent', *map(str, self.host.connection('worker')), 'session-inbox-claim'], body=inject))
            plan.update(BeforeExchange=[sys.executable, '-B', str(Path(__file__).resolve()), '--claim-actor', str(actor)])
        write(self.root / 'plan.json', plan)
        before = self.snapshot()
        write(self.root / 'before.json', before)
        argv = [image, '-test.run=^TestR06RealAPIConsumer$', '-test.v']
        env = dict(self.host.env, CAIRN_R06_API_PLAN=str(self.root / 'plan.json'))
        if serialization:
            serialization_probe(argv, self.root / 'consumer', env, self.host, self.state['inbox_attempt']['delivery']['delivery_id'])
        else:
            run(argv, self.root / 'consumer', env=env, timeout=40)
        result = json.loads((self.root / 'consumer-result.json').read_text())
        after = self.snapshot()
        write(self.root / 'after.json', after)
        if not inject:
            require(before == after, 'inspection mutated protected native/delivery/result/journal state')
        else:
            require(before == json.loads((self.root / 'actor-before.json').read_text()), 'capture mutated protected state')
            require(after == json.loads((self.root / 'actor-after.json').read_text()), 'exchange inspection mutated protected state')
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
                f.retained(settle=case != 'active', close=case != 'unfinished', resume=case == 'historical', cleanup=case == 'cleanup')
                if case == 'missing':
                    old = f.state['inbox_attempt']['attempt_id']
                    new = uid()
                    f.state['inbox_attempt']['attempt_id'] = new
                    f.state['inbox_intent']['request_id'] = new
                    missing_journal = json.loads((f.state_dir / 'intents' / (old + '.json')).read_text())
                    missing_journal['attempt_id'] = new
                    write(f.state_dir / 'intents' / (new + '.json'), missing_journal)
                    f.state['inbox_journals'].append(new)
                    write(f.state_path, f.state)
                if case == 'foreign':
                    # Preserve this record's native identity so the consumer
                    # reaches the foreign-attempt API association check.
                    other = f.call('worker', 'agent-register', dict(request_id=uid(), binding='fixture-other',
                        native_session_id=f.native, metadata=f.agent['metadata']))
                    f.state['agent'] = other
                    write(f.state_path, f.state)
                if case == 'stale':
                    f.register()  # The retained local reader is now fenced.
                token = f.host.token('sender') if case == 'unauthorized' else None
                if case == 'unauthenticated':
                    token = root / 'invalid.token'
                    token.write_text('synthetic-invalid-token-' + uid())
                    token.chmod(0o600)
                f.configs(token=token,
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
                result = f.inspect(images['consumer'], inject=injection, serialization=case == 'serialization')
                positive = case in ('terminal', 'historical', 'cleanup')
                require(result['Exchange']['Published'] == positive, f'{case}: wrong publication: {result}')
                if positive:
                    require(result['Receipt'] and not result['Error'], f'{case}: terminal refused: {result}')
                    reads = [c for c in result['Calls'] if c['Argv'][-1] == 'session-inbox-attempt-status']
                    require(len(reads) >= 2, 'receipt/exchange did not both read exact historical status')
                    for read in reads:
                        remote = json.loads(read['Stdout'])['data']
                        require(remote['session'] == f.state['inbox_attempt']['session'], 'history execution changed')
                        require(not remote['delivery'].get('lease_id') and not remote['delivery'].get('lease_until'), 'status exposed lease')
                        if case == 'cleanup':
                            require(remote['capture_state'] == 'complete' and remote['terminal_scan'] == 'clear' and
                                remote['turn_stop_state'] == 'ended' and remote['tools'][0]['stop_state'] == 'terminated' and
                                remote['tools'][0].get('stop_at'), 'authoritative tool cleanup correspondence missing')
                else:
                    require(bool(result['Error']), f'{case}: refusal lost error')
                    require(sha((f.active / 'one.json').read_bytes()) == f.pins['one.json'], 'refusal changed old selection')
                    if case == 'invalidate':
                        require(result['Receipt'] and 'invalidated' in result['Error'], 'claim did not invalidate initial receipt')
                        actor = json.loads(result['ActorOutput'])
                        require(actor['ok'] and actor['data']['attempt'], 'fixture claim never happened')
                        prepared = f.root / 'retained'
                        require(result['Exchange']['Preparation'] == str(prepared), 'lost prepared selection identity')
                        require(sha((prepared / 'one.json').read_bytes()) == sha(f.next.read_bytes()), 'prepared selection incomplete')
                        require(set(p.name for p in f.active.iterdir()) == {'one.json', '.watch.lock'}, 'old selection membership changed')
                        require(os.path.samefile(f.active / '.watch.lock', prepared / '.watch.lock'), 'prepared selection lost shared lock inode')
                    else:
                        require(not result['Receipt'], 'unknown/held view created receipt')
                        require(not (f.root / 'retained').exists(), 'receipt refusal prepared an exchange')
                    statuses = [json.loads(c['Stdout']) for c in result['Calls'] if c['Stdout'].startswith('{')]
                    expected = {'missing':'NOT_FOUND', 'foreign':'NOT_FOUND', 'stale':'STALE_SESSION',
                                'unauthorized':'NOT_FOUND', 'unauthenticated':'AUTHORITY_DENIED', 'serialization':'STORE_ERROR'}
                    if case in expected:
                        require(any(r.get('status') == expected[case] for r in statuses), f'{case}: missing real API refusal: {statuses}')
                    if case in ('missing', 'foreign'):
                        require(any(c['Argv'][-1] == 'session-inbox-control' and
                            json.loads(c['Stdout'])['data']['attempt'] is None for c in result['Calls']),
                            'null control did not reach exact historical lookup')
                outcomes.append(dict(case=case, status='PASS', receipt=result['Receipt'], published=result['Exchange']['Published'], error=result['Error']))
                print(json.dumps(outcomes[-1]), flush=True)
            finally:
                api_pid = f.host.process.pid
                require(api_pid > 1, 'owned API PID must exceed 1')
                api_exit = f.host.close()
                write(root / 'cleanup.json', dict(api_pid=api_pid, api_exit=api_exit, joined=True))
    finally:
        postmaster = scratch / 'pg' / 'data' / 'postmaster.pid'
        postgres_pid = int(postmaster.read_text().splitlines()[0]) if postmaster.exists() else None
        require(postgres_pid is None or postgres_pid > 1, 'owned PostgreSQL PID must exceed 1')
        stopped = cluster.stop()
        write(evidence / 'cleanup.json', dict(postgres_pid=postgres_pid, postgres_stopped=stopped,
            postmaster_pid_files=list(map(str, scratch.rglob('postmaster.pid')))))
        require(stopped, 'fixture PostgreSQL cleanup unresolved')
        tools = images['api'].parent
        pins = json.loads((tools / 'pins.json').read_text())
        pins['postgres_executables'] = {str(cluster.pg_bin / name): sha((cluster.pg_bin / name).read_bytes()) for name in ('initdb', 'pg_ctl', 'psql', 'postgres')}
        write(tools / 'pins.json', pins)
        shutil.copytree(scratch, evidence / 'fixtures', ignore=shutil.ignore_patterns('data', 'socket', '*.token', 'identities.json', 'api.sock'))
    write(evidence / 'outcomes.json', outcomes)
    print(f'R06 consumer: {len(outcomes)} passed, 0 failed, 0 skipped', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path)
    parser.add_argument('--claim-actor', type=Path)
    cases = ('terminal', 'historical', 'cleanup', 'active', 'unfinished', 'missing', 'foreign', 'stale', 'unauthorized', 'unauthenticated', 'unavailable', 'serialization', 'invalidate')
    parser.add_argument('--case', action='append', choices=cases)
    args = parser.parse_args()
    if args.claim_actor:
        claim_actor(args.claim_actor)
    else:
        parser.error('--evidence required') if args.evidence is None else None
        args.evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
        check(args.evidence, args.case or cases)
