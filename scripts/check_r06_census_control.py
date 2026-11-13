#!/usr/bin/env python3
"""Real census/control composition using the existing disposable API/PG fixtures."""
import argparse
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

import check_r06_unit_census as census
import check_r06_unit_control as control
from disposable_host import DisposableHost, OwnedCluster, pg_bindir, require

ROOT = Path(__file__).resolve().parents[1]


def forward(path, argv):
    plan = json.loads(path.read_text())
    operation = argv[-1]
    require(operation in control.READS, 'composition command attempted a non-read API operation')
    count_path = path.with_name('read-counts.json')
    counts = json.loads(count_path.read_text()) if count_path.exists() else {}
    counts[operation] = counts.get(operation, 0) + 1
    census.write(count_path, counts)
    result = subprocess.run([plan['api'], *argv], input=sys.stdin.read(), text=True,
                            capture_output=True, timeout=20)
    with path.with_name('read-calls.jsonl').open('a') as stream:
        stream.write(json.dumps(dict(operation=operation, argv=argv, exit=result.returncode,
                                     stdout=result.stdout, stderr=result.stderr)) + '\n')
    actor = plan.get('mutation')
    if actor and operation == 'event-watch' and counts[operation] == actor['event_count']:
        selected = Path(actor['path'])
        require(selected.is_relative_to(plan['universe']), 'actor path outside owned fixture')
        before = selected.read_bytes()
        body = json.loads(before)
        body[actor['field']] = actor['value']
        selected.chmod(0o600)
        census.write(selected, body)
        census.write(path.with_name('actor.json'), dict(path=str(selected), before_sha256=control.sha(before),
                     after_sha256=census.sha(selected), event_count=counts[operation], operation=operation))
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


class Fixture(census.Fixture):
    def __init__(self, cluster, scratch, evidence, images, name):
        super().__init__(scratch, evidence, images['helper'], name)
        self.host = None
        self.api_launch = None
        try:
            self.host = DisposableHost.start(cluster, binary=images['api'], repo='fixture:census:a',
                database='adopt_' + name.replace('-', '_'), home=self.universe / 'api-home',
                roles=('sender', 'worker'), socket_path=self.root / 'api.sock', log=self.evidence / 'api.log')
            self.api_launch = census.process_ref(self.host.process.pid)
            self.images = images
            self.transport = self.evidence / 'transport.json'
            census.write(self.transport, dict(api=str(images['api']), universe=str(self.universe)))
            client = Path(self.catalog['Clients'][0]['Path'])
            client.chmod(0o600)
            client.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, '-B', str(Path(__file__).resolve()),
                              '--forward-api', str(self.transport)]) + ' "$@"\n')
            client.chmod(0o500)
            self.catalog['Clients'][0]['SHA256'] = census.sha(client)
            token = str(self.host.token('worker'))
            self.catalog['CredentialPaths'].append(token)
            manifest_path = Path(self.manifests['a'])
            manifest = json.loads(manifest_path.read_text())
            memory_path = Path(self.memories['a'])
            memory = json.loads(memory_path.read_text())
            memory.update(socket=str(self.host.socket), token_file=token)
            memory_pin = census.write(memory_path, memory)
            manifest['memory_config'] = dict(path=str(memory_path), sha256=memory_pin['SHA256'])
            self.selection = self.catalog['Selections'][0]
            for descriptor in manifest['coordination_configs']:
                immutable = Path(descriptor['path'])
                cfg = json.loads(immutable.read_text())
                cfg.update(socket=str(self.host.socket), token_file=token)
                census.write(immutable, cfg)
                descriptor['sha256'] = census.sha(immutable)
                active = Path(self.selection['Directory']) / immutable.name
                census.write(active, cfg);active.chmod(0o400)
                self.selection['Pins'][immutable.name] = census.sha(active)
                native = 'fixture-native-a' + immutable.name
                state_path = Path(cfg['state_dir']) / (control.sha((cfg['binding'] + '\0' + native).encode()) + '.json')
                state = json.loads(state_path.read_text())
                agent = self.host.agent('worker', 'agent-register', dict(request_id=control.uid(),
                    binding=cfg['binding'], native_session_id=native,
                    metadata=dict(harness='codex', project='r06-adoption', workspace=state['agent']['metadata']['workspace'],
                                  state='idle', delivery_mode='existing-session')))
                state['agent'] = agent
                census.write(state_path, state)
                for pin in self.catalog['Sessions']:
                    if pin['Path'] == str(state_path):pin['SHA256'] = census.sha(state_path)
            self.catalog['Bindings'][0] = census.write(manifest_path, manifest)
            lock = Path(self.selection['Directory']) / '.watch.lock'
            lock.write_bytes(b'');lock.chmod(0o600)
            self.proposal['Bindings'] = [copy.deepcopy(self.selection)]
            self.proposal['Callbacks'] = []
            self.retained = []
            self.call_serial = 0
            # B is a genuinely different complete immutable selection, with
            # its own disabled binding/profile descriptors and identical unit selectors.
            target_root = self.universe / 'descriptors/a-next'
            target_binding = target_root / 'binding.json'
            target_memory = dict(memory, inbox_recall_binding=str(target_binding))
            target_mem_pin = census.write(target_root / 'memory.json', target_memory)
            target_coords = []
            self.next = []
            for descriptor in manifest['coordination_configs']:
                cfg = json.loads(Path(descriptor['path']).read_text())
                cfg['inbox_recall_binding'] = str(target_binding)
                target = target_root / Path(descriptor['path']).name
                pin = census.write(target, cfg);target.chmod(0o400)
                target_coords.append(dict(path=str(target), sha256=pin['SHA256']))
                self.next.append(dict(name=target.name, path=str(target), sha256=pin['SHA256']))
            target_manifest = dict(manifest, memory_config=dict(path=target_mem_pin['Path'],sha256=target_mem_pin['SHA256']),
                                   coordination_configs=target_coords)
            self.target_binding = census.write(target_binding, target_manifest)
        except BaseException:
            self.close()
            raise

    def plan(self, *, reverse=False):
        self.call_serial += 1
        catalog = census.write(self.universe / f'catalog-{self.call_serial}.json', self.catalog)
        selected = self.catalog['Selections'][0]
        next_files = self.next
        if reverse:
            old = self.retained[0]
            next_files = [dict(name=name, path=str(Path(old['Directory'])/name), sha256=pin)
                          for name,pin in old['Pins'].items()]
        return dict(schema='cairn.unit-control/2',
            current=dict(directory=selected['Directory'], pins=dict(selected['Pins'])), next=next_files,
            retained=str(Path(selected['Directory']).with_name('retained-new' if reverse else 'retained-old')),
            census=dict(Catalog=catalog, Proposal=copy.deepcopy(self.proposal), Retained=copy.deepcopy(self.retained)))

    def control(self, operation, plan, label, *, error=None, mutation=None):
        if mutation:
            source = plan['census']['Catalog']['Path'] if mutation=='catalog' else self.record['Join']['Path']
            transport = json.loads(self.transport.read_text())
            transport['mutation'] = dict(path=source, field='Scope' if mutation=='catalog' else 'ExitCode',
                value='external-unknown' if mutation=='catalog' else 99, event_count=6)
            census.write(self.transport, transport)
        plan_path = self.evidence / (label + '.plan.json')
        census.write(plan_path, plan)
        before = control.snapshot(self.host.dsn, self.host.pg_bin, self.universe / 'state-a')
        census.write(self.evidence / (label + '.before.json'), before)
        files_before = census.snapshot(self.universe)
        census.write(self.evidence / (label + '.files-before.json'), files_before)
        started=time.time_ns()
        try:
            result = control.command([self.images['control'],'unit-control',operation,'--plan',plan_path,
                '--sha256',census.sha(plan_path)], self.evidence / label, env=self.host.env, expected=1 if error else 0)
        finally:
            census.write(self.evidence/(label+'.time.json'),dict(start_unix_ns=started,end_unix_ns=time.time_ns()))
        parsed = json.loads(result.stdout)
        after = control.snapshot(self.host.dsn, self.host.pg_bin, self.universe / 'state-a')
        census.write(self.evidence / (label + '.after.json'), after)
        files_after = census.snapshot(self.universe)
        census.write(self.evidence / (label + '.files-after.json'), files_after)
        require(before == after, 'control/inspector mutated protected API or session state')
        actor_path = json.loads(self.transport.read_text()).get('mutation', {}).get('path')
        protected = lambda rows: {p:v for p,v in rows.items() if not Path(p).is_relative_to(self.catalog['ConfigRoot']) and p!=actor_path}
        require(protected(files_before)==protected(files_after), 'control changed protected descriptors/references/grants/credentials')
        if (error and not mutation) or operation=='inspect':
            require(files_before==files_after,'refusing/inspect command changed the finite unit')
        if error:
            require(error in parsed['error'] and not parsed['exchange']['Published'], f'{label}: wrong refusal {parsed}')
            require({p.name: census.sha(p) for p in Path(plan['current']['directory']).glob('*.json')} == plan['current']['pins'],
                    'refusal changed active selection')
        else:
            require(parsed['inspected'] and parsed['census']['FixtureReady'] and len(parsed['census']['Members']) == 2,
                    'complete two-binding census was not bound to the receipt')
            require(parsed['census']['ExternalCoverage']=='unknown' and parsed['census']['NativeRefresh']=='unobserved',
                    'finite census claimed external/native qualification')
            if operation!='inspect':
                for selection in (parsed['exchange']['Active'],parsed['exchange']['Retained']):
                    directory=Path(selection['Directory'])
                    require({p.name for p in directory.iterdir()}==set(selection['Pins'])|{'.watch.lock'},'published selection membership incomplete')
                    require({p.name:census.sha(p) for p in directory.glob('*.json')}==selection['Pins'],'published selection pins incomplete')
        if mutation:
            require((self.evidence/'actor.json').exists() and parsed['inspected'], 'mutation did not reach final publication observation')
            prepared=Path(plan['retained'])
            require({p.name:census.sha(p) for p in prepared.glob('*.json')}=={x['name']:x['sha256'] for x in plan['next']},'refusal lost complete preparation')
            require(os.path.samefile(prepared/'.watch.lock',Path(plan['current']['directory'])/'.watch.lock'),'refusal lost shared lock inode')
        return parsed

    def close(self):
        super().close()
        if self.host:
            process = self.host.process
            require(process.pid > 1, 'owned API PID must exceed 1')
            group = os.getpgid(process.pid) if process.poll() is None else None
            require(group is None or group > 1, 'owned API group must exceed 1')
            code = self.host.close()
            census.write(self.evidence/'api-cleanup.json',dict(pid=process.pid,process=self.api_launch,group=group,exit=code,joined=True,group_signals=False))


CASES = ('roundtrip-disjoint','joined-selected','omitted-binding','omitted-session','omitted-callback',
         'mismatched-directory','spanning-directories','running','delayed','unjoined','unknown-process',
         'cached-a','positive-grant','final-catalog','final-join')


def check(evidence, selected):
    scratch = Path(tempfile.mkdtemp(prefix='r06-census-control-',dir=Path.home()/'.cache'))
    census.write(evidence/'scratch.json',dict(path=str(scratch)))
    images = control.build(scratch)
    images['helper'] = scratch/'tools/census-tests'
    control.command(['go','test','-race','-buildvcs=false','-c','-o',images['helper'],'./internal/lifecycle/coordination'],scratch/'tools/build-helper')
    images['helper'].chmod(0o500)
    census.write(evidence/'images.json',{k:dict(path=str(p),sha256=census.sha(p)) for k,p in images.items()})
    census.write(evidence/'source-before-use.json',{str(p.relative_to(ROOT)):census.sha(p) for p in
        [*sorted((ROOT/'internal/lifecycle/coordination').glob('shared_unit*.go')),Path(__file__),ROOT/'go.mod',ROOT/'go.sum']})
    pg_bin = pg_bindir()
    census.write(evidence/'pg-before-use.json',{n:dict(path=str(pg_bin/n),sha256=census.sha(pg_bin/n)) for n in ('initdb','pg_ctl','postgres','createdb','psql')})
    cluster = OwnedCluster.start(scratch/'pg',pg_bin=pg_bin)
    outcomes = []
    try:
        for name in selected:
            f = Fixture(cluster,scratch,evidence,images,name)
            try:
                error = None
                if name in ('joined-selected','omitted-callback','running','delayed','unjoined','unknown-process','cached-a','final-join','roundtrip-disjoint'):
                    if name in ('cached-a','roundtrip-disjoint'):
                        unit='b' if name=='roundtrip-disjoint' else 'a'
                        p=Path(f.manifests[unit]);body=json.loads(p.read_text());body['enabled']=True
                        f.catalog['Bindings'][1 if unit=='b' else 0]=census.write(p,body)
                    f.callback(role='issued' if name=='cached-a' else 'callback', delay=1500 if name=='delayed' else 0,
                               unit='b' if name=='roundtrip-disjoint' else 'a')
                    if name in ('joined-selected','omitted-callback','final-join'):f.join()
                    elif name in ('unjoined','unknown-process'):f.join(receipt=False)
                    if name=='unknown-process':
                        f.record['Process']['PID']=2147483646;f.update_record()
                    if name=='cached-a':f.publish_disabled()
                if name=='positive-grant':
                    path=f.universe/'memory-a/retained.memory-budget.json'
                    body=json.loads(path.read_text());body['inbox_activation']['old-turn']=True;census.write(path,body)
                if name=='spanning-directories':
                    a=json.loads(Path(f.manifests['a']).read_text());p=Path(f.manifests['b']);b=json.loads(p.read_text())
                    b['binary']=a['binary'];f.catalog['Bindings'][1]=census.write(p,b)
                if name=='roundtrip-disjoint':f.proposal['Callbacks']=[]
                plan=f.plan()
                if name=='omitted-binding':plan['census']['Proposal']['Bindings'][0]['Pins'].pop('two.json');error='proposal differs'
                elif name=='omitted-session':plan['census']['Proposal']['Sessions'].pop();error='session coverage'
                elif name=='omitted-callback':plan['census']['Proposal']['Callbacks']=[];error='callback coverage'
                elif name=='mismatched-directory':plan['census']['Proposal']['Bindings'][0]['Directory']=str(f.universe/'other');error='proposal differs'
                elif name=='spanning-directories':error='proposed half unit'
                elif name in ('running','delayed','unjoined','unknown-process'):error='callback is running or lacks'
                elif name=='cached-a':error='old cached bytes'
                elif name=='positive-grant':error='retained positive activation grant'
                elif name in ('final-catalog','final-join'):error='D6 unit census receipt invalidated'
                if name=='roundtrip-disjoint':
                    f.control('inspect',plan,'inspect')
                    old=copy.deepcopy(f.selection)
                    result=f.control('switch',f.plan(),'switch')
                    require(result['exchange']['Published'],'switch did not publish')
                    f.retained=[result['exchange']['Retained']]
                    f.selection['Pins']=dict(result['exchange']['Active']['Pins'])
                    f.catalog['Bindings'].append(f.target_binding)
                    f.proposal['Bindings']=[copy.deepcopy(f.selection)]
                    reversed_=f.control('reverse',f.plan(reverse=True),'reverse')
                    require(reversed_['exchange']['Published'] and reversed_['exchange']['Active']['Pins']==old['Pins'], 'reverse did not restore the complete old set')
                    active=Path(old['Directory'])
                    require(all(os.path.samefile(active/'.watch.lock',Path(x['Directory'])/'.watch.lock') for x in
                                (result['exchange']['Retained'],reversed_['exchange']['Retained'])), 'roundtrip split watcher lock inode')
                    require(f.child.poll() is None,'known disjoint enabled callback was stopped')
                else:
                    f.control('switch',plan,'control',error=error,mutation=name.removeprefix('final-') if name.startswith('final-') else None)
                outcomes.append(dict(case=name,status='PASS'));print(json.dumps(outcomes[-1]),flush=True)
            finally:
                f.close()
                shutil.copytree(f.universe,f.evidence/'selected-files',ignore=shutil.ignore_patterns('*.token','identities.json','api.sock','image'))
    finally:
        pid_path=scratch/'pg/data/postmaster.pid'
        pid=int(pid_path.read_text().splitlines()[0]) if pid_path.exists() else None
        require(pid is None or pid>1,'owned PG PID must exceed 1')
        group=os.getpgid(pid) if pid else None
        require(group is None or group>1,'owned PG group must exceed 1')
        stopped=cluster.stop();census.write(evidence/'cleanup.json',dict(pid=pid,group=group,stopped=stopped,group_signals=False))
        require(stopped,'owned PG cleanup unresolved')
    census.write(evidence/'outcomes.json',outcomes)
    print(f'R06 census/control: {len(outcomes)} passed, 0 failed, 0 skipped',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path)
    parser.add_argument('--case',choices=CASES,action='append')
    parser.add_argument('--forward-api',type=Path)
    args,extra=parser.parse_known_args()
    if args.forward_api:raise SystemExit(forward(args.forward_api,extra))
    require(args.evidence is not None and not extra,'--evidence required')
    args.evidence.mkdir(mode=0o700,parents=True,exist_ok=False)
    check(args.evidence,args.case or CASES)
