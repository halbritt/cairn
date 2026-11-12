"""Fixed disposable-store and idle-owner evidence; no production model execution."""
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil

import check_semantic_reference as reference
from semantic_refusal_processes import process_observation


STORE_IDS={
    *('F-EMBED/invalid-utf8-span/'+v for v in ('negative-offset','zero-length','outside-source','mid-codepoint')),
    *('F-EMBED/too-many-passages/'+v for v in ('zero','256','257')),
    *('F-EMBED/bad-dimensions/'+v for v in ('383','384','385')),
    *('F-EMBED/nonfinite-vectors/'+v for v in ('NaN','positive-infinity','negative-infinity')),
    'F-EMBED/zero-vectors/boundary'}
IDLE_ID='F-STREAM/idle-exit-restore/boundary'
FIXTURE='scripts/semantic_refusal_residual_fixture.py'
GO_SOURCE='scripts/testdata/semantic-refusal-residual-driver/main.go'
BOOTSTRAP="import sys,runpy;sys.path.insert(0,sys.argv[1]);sys.argv=sys.argv[2:];runpy.run_path(sys.argv[0],run_name='__main__')"
PG=Path('/usr/lib/postgresql/18/bin')
PG_INNER="""import json,os,subprocess,sys
from pathlib import Path
root=Path('/tmp/cairn-s01b-pg');out=Path(sys.argv[1]);driver=sys.argv[2];pg=Path(sys.argv[3])
root.mkdir(mode=0o700);(root/'socket').mkdir(mode=0o700)
commands=[]
def run(name,argv):
 p=subprocess.run(argv,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=150)
 (out/(name+'.stdout')).write_bytes(p.stdout);(out/(name+'.stderr')).write_bytes(p.stderr)
 commands.append(dict(name=name,argv=argv,exit_code=p.returncode))
 return p.returncode
try:
 if run('initdb',[str(pg/'initdb'),'-D',str(root/'data'),'-U','s01bfixturepg','--auth-local=trust','--auth-host=reject','--no-locale','--encoding=UTF8']):raise RuntimeError('private initdb failed')
 (root/'data/postgresql.conf').write_text((root/'data/postgresql.conf').read_text()+"\\nlisten_addresses=''\\nunix_socket_directories='/tmp/cairn-s01b-pg/socket'\\n")
 if run('start',[str(pg/'pg_ctl'),'-D',str(root/'data'),'-l',str(out/'postgres.log'),'-w','start']):raise RuntimeError('private PostgreSQL start failed')
 lines=(root/'data/postmaster.pid').read_text().splitlines();pid=int(lines[0])
 if pid<=1:raise RuntimeError('invalid owned PostgreSQL PID')
 stat=Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split()
 result=run('store-driver',[driver,'store',str(out)])
finally:
 if (root/'data/postmaster.pid').exists():
  stopped=run('stop',[str(pg/'pg_ctl'),'-D',str(root/'data'),'-m','fast','-w','stop'])
 else:stopped=None
 (out/'cluster-lifecycle.json').write_text(json.dumps(dict(commands=commands,pid=locals().get('pid'),start_ticks=locals().get('stat',[None]*20)[19],
  data_directory=str(root/'data'),socket=str(root/'socket'),postmaster_pid_file_absent=not (root/'data/postmaster.pid').exists(),stop_exit_code=stopped))+'\\n')
sys.exit(result)
"""


def seal(root):
    return {str(p.relative_to(root)):reference.digest(p) for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


def dependency_sources(raw):
    decoder=json.JSONDecoder();packages=[];raw=raw.strip()
    while raw:
        item,end=decoder.raw_decode(raw);packages.append(item);raw=raw[end:].lstrip()
    local=[p for p in packages if p.get('Module',{}).get('Main')]
    reference.require({p['ImportPath'] for p in local}=={
        'github.com/halbritt/cairn/core','github.com/halbritt/cairn/semantic',
        'github.com/halbritt/cairn/internal/advice','github.com/halbritt/cairn/internal/plane',
        'github.com/halbritt/cairn/scripts/testdata/semantic-refusal-residual-driver'},'native dependency package closure changed')
    sources={'go.mod','go.sum','scripts/testdata/semantic-refusal-residual-driver/main_test.go'}
    for package in local:
        directory=Path(package['Dir']);module=Path(package['Module']['Dir']);relative=directory.relative_to(module)
        for name in package.get('GoFiles',[])+package.get('EmbedFiles',[]):sources.add(str(relative/name))
    return sources


def verify_build(root):
    report=reference.load(root/'build-report.json')
    reference.require(set(report)=={'schema','sources','go_sha256','go_version','environment','commands','driver_sha256','files_sha256'} and
        report['schema']=='cairn.semantic-residual-native-build/1' and report['go_version']=='go1.25.0','unsupported native build claim')
    required={GO_SOURCE,'scripts/testdata/semantic-refusal-residual-driver/main_test.go','go.mod','go.sum',
              'semantic/index.go','semantic/stream.go','semantic/command.go'}
    reference.require(required<=set(report['sources']),'missing native build source pins')
    required_sources=dependency_sources((root/'deps.stdout').read_text())
    current_sources={str(p.relative_to(reference.PROJECT)) for base in ('core','semantic','internal/advice','internal/plane')
        for p in (reference.PROJECT/base).rglob('*') if p.suffix in ('.go','.sql') and not p.name.endswith('_test.go') and 'testdata' not in p.parts}
    current_sources|={GO_SOURCE,'scripts/testdata/semantic-refusal-residual-driver/main_test.go','go.mod','go.sum'}
    reference.require(required_sources==set(report['sources'])==current_sources,'native build source/dependency closure changed')
    for name,sha in report['sources'].items():reference.pinned(reference.PROJECT,name,sha)
    for name,sha in report['files_sha256'].items():reference.pinned(root,name,sha)
    reference.require(reference.digest(root/'driver')==report['driver_sha256'],'native binary differs from checked build')
    commands=report['commands'];cleanup=reference.load(root/'cleanup.json');proof=reference.load(root/'isolation-proof.json')
    reference.require([r['mode'] for r in commands]==['deps','test','build'] and all(r['exit_code']==0 for r in commands) and
        cleanup['invocations'][1:]==[r['invocation'] for r in commands] and cleanup['launcher_exit_codes']==[0,0,0,0],
        'native build/tests did not complete')
    for row in commands:
        invocation=row['invocation'];argv=invocation['argv'][invocation['argv'].index('--')+1:]
        arguments=({'deps':['list','-deps','-json','./scripts/testdata/semantic-refusal-residual-driver'],
            'test':['test','-v','./scripts/testdata/semantic-refusal-residual-driver'],
            'build':['build','-trimpath','-o',argv[-2],'./scripts/testdata/semantic-refusal-residual-driver']}[row['mode']])
        expected=[argv[0],'-C',argv[2],*arguments]
        reference.require(argv==expected and Path(argv[2]).is_absolute() and
            argv[2]==commands[0]['invocation']['argv'][commands[0]['invocation']['argv'].index('--')+3] and
            reference.digest(Path(argv[0]))==report['go_sha256'] and
            invocation['contract']['environment']==dict(report['environment'],PWD='/'),'native build command/environment changed')
    reference.require(report['environment']['GOPROXY']=='off' and report['environment']['GOSUMDB']=='off' and
        report['environment']['GOTOOLCHAIN']=='local' and report['environment']['GOFLAGS']=='-p=2 -mod=readonly' and
        report['environment']['CGO_ENABLED']=='0','native build may not fetch dependencies or vary toolchain')
    clean(value=None,root=root,cleanup=cleanup,proof=proof)
    return report


def clean(value,root,cleanup=None,proof=None):
    cleanup=cleanup or reference.load(root/'cleanup.json');proof=proof or reference.load(root/'isolation-proof.json')
    reference.require(cleanup['all_observed_processes_exited'] is True and cleanup['backing_removed'] is True and
        cleanup['errors']==[] and cleanup['isolation_proof_sha256']==reference.digest(root/'isolation-proof.json') and
        proof['profile']=='default' and all(m['read_only'] for m in proof['read_only_mounts']) and
        proof['wrapper_sha256']==reference.digest(reference.PROJECT/'scripts/semantic_acceptance_isolation.py'),
        'native residual isolation/cleanup changed')
    return cleanup


def collect(args,value):
    build=verify_build(args.build)
    output=args.output.absolute();reference.require(not output.exists(),'native residual output must be fresh');output.mkdir(mode=0o700)
    shutil.copytree(args.build,output/'build')
    sources={name:reference.digest(reference.PROJECT/name) for name in (FIXTURE,GO_SOURCE,'semantic/stream.go','semantic/index.go')}
    pgpins={name:reference.digest(PG/name) for name in ('initdb','pg_ctl','postgres')}
    store=output/'store'
    with reference.SemanticIsolation(read_only_paths=[reference.PROJECT,output/'build'],output_dir=store) as iso:
        result=iso.run(['/usr/bin/python3','-I','-S','-B','-c',PG_INNER,str(store),str(output/'build/driver'),str(PG)],
            env=dict(reference.WORKER_ENV),timeout=210,role='S01b-disposable-store',mode='14-fixed-Index.Step-vectorText-controls')
        (store/'stdout').write_bytes(result.stdout);(store/'stderr').write_bytes(result.stderr)
        reference.save(store/'result.json',dict(exit_code=result.returncode,invocation=result.invocation))
    reference.require(result.returncode==0,'private store residual producer failed')
    collect_idle(output,'idle')
    return write_report(output,sources,pgpins,'idle')


def collect_idle(output,folder_name):
    idle=output/folder_name
    with reference.SemanticIsolation(read_only_paths=[reference.PROJECT,output/'build'],output_dir=idle) as iso:
        launcher=idle/'worker';launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 -I -S -B -c '+shlex.quote(BOOTSTRAP)+' '+
            ' '.join(shlex.quote(str(p)) for p in [reference.PROJECT/'scripts',reference.PROJECT/FIXTURE,'idle',idle/'events.jsonl'])+'\n');launcher.chmod(0o700)
        result=iso.run(['/usr/bin/strace','-ff','-ttt','-xx','-yy','-s','128','-e','trace=process,kill,read,write',
            '-o',str(idle/'trace'),str(output/'build/driver'),'idle',str(launcher),str(idle/'events.jsonl')],
            env=dict(reference.WORKER_ENV),timeout=20,role='S01b-Go-idle-process-double',mode=IDLE_ID)
        (idle/'driver.stdout').write_bytes(result.stdout);(idle/'driver.stderr').write_bytes(result.stderr)
        reference.save(idle/'result.json',dict(exit_code=result.returncode,invocation=result.invocation))
    reference.require(result.returncode==0,'idle residual producer failed')


def write_report(output,sources,pgpins,idle_folder,*,capture_root=None):
    report=dict(schema='cairn.semantic-native-residuals/1',sources=sources,pg_sha256=pgpins,model_execution=False,
        producer_bootstrap=PG_INNER,parameters=dict(project=str(reference.PROJECT),output=str(capture_root or output)),
        idle_folder=idle_folder,build_report_sha256=reference.digest(output/'build/build-report.json'),files_sha256=seal(output))
    reference.save(output/'native-report.json',report)
    return report


def store_rows(data):
    reference.require(set(data)=={'schema','cases','postgres_version','data_directory','unix_socket','model_execution'} and
        data['schema']=='cairn.semantic-store-refusals/1' and data['model_execution'] is False and
        data['data_directory']=='/tmp/cairn-s01b-pg/data' and data['unix_socket']=='/tmp/cairn-s01b-pg/socket',
        'unsupported store/model claim')
    reference.require({r['id'] for r in data['cases']}==STORE_IDS and len(data['cases'])==14,'missing/duplicate fixed store residuals')
    result={}
    for number,row in enumerate(data['cases']):
        reference.require(set(row)=={'id','scope','input','step','search','discovery','store','model_double_calls','database','pgvector_version',
            'model_initialized','real_model_inference'},'unsupported store observation claim')
        identifier=row['id'];name=identifier.rsplit('/',1)[1]
        positive=identifier in ('F-EMBED/too-many-passages/256','F-EMBED/bad-dimensions/384')
        expected_error=('' if positive else 'invalid passage source span' if '/invalid-utf8-span/' in identifier else
            'invalid passage count' if '/too-many-passages/' in identifier else 'embedding must have 384 dimensions' if '/bad-dimensions/' in identifier else
            'embedding must be finite' if '/nonfinite-vectors/' in identifier else 'embedding must be nonzero')
        reference.require(row['scope']=='Go/Index.Step-vectorText/disposable-store/model-double' and
            row['model_initialized'] is False and row['real_model_inference'] is False and
            row['database']=='s01b_residual_%02d'%number and row['pgvector_version'], 'store scope/database changed')
        passages=row['input']['passages'];count=0 if name=='zero' else int(name) if '/too-many-passages/' in identifier else 1
        dimensions=int(name) if '/bad-dimensions/' in identifier else 384
        reference.require(row['input']['body']=='é source span control.' and len(passages)==count,'store input/count changed')
        offset,length=({'negative-offset':(-1,2),'zero-length':(0,0),'outside-source':(len(row['input']['body'].encode())+1,2),'mid-codepoint':(1,1)}.get(name,(0,2)))
        first={'NaN':0x7fc00001,'positive-infinity':0x7f800000,'negative-infinity':0xff800000}.get(name,0 if '/zero-vectors/' in identifier else 0x3f800000)
        reference.require(all(p==dict(span=dict(offset=offset,length=length),float32_bits=[first]+[0]*(dimensions-1)) for p in passages),
                          'store input span/vector bits changed')
        reference.require(row['step']==dict(result=True,error=expected_error) and row['search']['error']=='' and row['discovery']['error']=='',
                          'actual Step/search/discovery result changed')
        reference.require(row['store']==dict(documents=int(positive),passages=count if positive else 0,jobs=int(not positive),
            attempts=int(not positive),lease_released=True),'refused store input published rows or lost its retry lease')
        reference.require(row['search']['result']['Indexed']==int(positive) and
            len(row['search']['result']['Hits'])==int(positive),'store search coverage changed')
        search=row['search']['result']
        reference.require(set(search)=={'QueryProjection','ModelSHA256','Algorithm','Indexed','Hits'} and search['QueryProjection'] is None and
            search['ModelSHA256']=='a'*64 and search['Algorithm']=='s01b-store-double/1','store search identity/projection changed')
        if positive:
            hit=search['Hits'][0]
            reference.require(set(hit)=={'record_id','version','body_sha256','score','span'} and
                re.fullmatch(r'[0-9a-f-]{36}',hit['record_id']) and hit['version']==1 and hit['score']==1000000 and
                hit['body_sha256']==hashlib.sha256(row['input']['body'].encode()).hexdigest() and
                hit['span']==dict(offset=0,length=2),'published store hit source/span/vector result changed')
        discovery=row['discovery']['result']
        reference.require(row['model_double_calls']==dict(identity=1,document=1,query=2) and
            (discovery['state']=='ready' and discovery['coverage']==dict(indexed=1,eligible=1) if positive else discovery==dict(state='unavailable')),
            'store compiler coverage or model-double calls changed')
        disposition={k:reference.unobserved('in-process Go/store fixture; no supervised model worker or rank cache') for k in reference.DISPOSITION_FIELDS}
        disposition.update(go_error_or_result=dict(state='observed',step=row['step'],search=row['search']),
            discovery_coverage=dict(state='observed',compiler=row['discovery'],store=row['store']))
        result[identifier]=dict(id=identifier,scope=row['scope'],freshness='new disposable store capture',
            store_observation=row,disposition=disposition,complete=True)
    return result


def idle_row(root):
    data=reference.load(root/'driver.stdout');extra={'old_child_absent_before_replacement','old_child_pid','old_child_start_ticks'}
    observed=process_observation(root,'residual-idle',expected_errors=['','',''],extra_fields=extra)
    events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
    starts=[r for r in events if r['event']=='start'];requests=[r for r in events if r['event']=='request']
    reference.require(len(starts)==2 and len(requests)==3 and data['old_child_absent_before_replacement'] is True and
        data['old_child_pid']==starts[0]['pid'] and data['old_child_start_ticks']==starts[0]['start_ticks'] and
        requests[0]['pid']==requests[1]['pid']==starts[0]['pid'] and requests[2]['pid']==starts[1]['pid'] and
        [r['request'] for r in requests]==[
            dict(id='1',request=dict(query='query',notes=None)),dict(id='2',request=dict(query='query',notes=None)),
            dict(id='3',request=dict(query='query',notes=None),cache=dict(fixture='state-2'))],
        'idle replacement did not preserve the last successful bounded cache')
    workers=observed['disposition']['process_exit_kill']['workers']
    reference.require(all(w['stdout_prefix_state']=='observed' and w['stdout_prefix_hex'] for w in workers) and
        observed['disposition']['process_exit_kill']['process_group_signals'] and
        observed['disposition']['process_exit_kill']['direct_child_waits'],'idle child exit/stdout/join not observed')
    observed['disposition']['cache']=dict(state='observed',incoming_requests=requests,last_successful_state=dict(fixture='state-2'))
    observed['disposition']['restarts']=dict(state='observed',successful_starts=2,replacement_count=1,owner_generations=1)
    observed.update(id=IDLE_ID,complete=True,freshness='new idle/cache process-double capture',old_child_absent_before_replacement=True)
    return observed


def verify(value,path,sha):
    reference.require(sha and reference.digest(path)==sha,'pinned native residual report required')
    report=reference.load(path);root=path.parent
    reference.require(set(report)=={'schema','sources','pg_sha256','model_execution','producer_bootstrap','parameters','idle_folder','build_report_sha256','files_sha256'} and
        report['schema']=='cairn.semantic-native-residuals/1' and report['model_execution'] is False and
        report['producer_bootstrap']==PG_INNER,'unsupported native residual claim')
    reference.require(report['sources']=={p:reference.digest(reference.PROJECT/p) for p in (FIXTURE,GO_SOURCE,'semantic/stream.go','semantic/index.go')},
        'native residual actor changed')
    for rel,pin in report['files_sha256'].items():reference.pinned(root,rel,pin)
    reference.require(report['files_sha256']==seal_without_report(root,'native-report.json'),'unsealed native residual artifact')
    reference.require(reference.digest(root/'build/build-report.json')==report['build_report_sha256'],'native build report changed');verify_build(root/'build')
    reference.require(report['pg_sha256']=={name:reference.digest(PG/name) for name in ('initdb','pg_ctl','postgres')},'PostgreSQL binary changed')
    reference.require(report['idle_folder'] in ('idle','idle-repair') and set(report['parameters'])=={'project','output'} and
        all(Path(p).is_absolute() for p in report['parameters'].values()),'native residual path changed')
    capture_root=Path(report['parameters']['output'])
    for name in ('store','idle'):
        folder=root/(name if name=='store' else report['idle_folder']);cleanup=clean(value,folder);result=reference.load(folder/'result.json');invocation=result['invocation']
        reference.require(result['exit_code']==0 and cleanup['launcher_exit_codes']==[0,0] and cleanup['invocations'][1:]==[invocation] and
            invocation['contract']['environment']==dict(reference.WORKER_ENV,PWD='/') and
            invocation['declaration_sha256']==reference.digest(folder/'command-0002.json'),'native residual invocation changed')
        argv=invocation['argv'][invocation['argv'].index('--')+1:]
        captured_folder=capture_root/(name if name=='store' else report['idle_folder'])
        expected=(['/usr/bin/python3','-I','-S','-B','-c',PG_INNER,str(capture_root/'store'),str(capture_root/'build/driver'),str(PG)] if name=='store' else
            ['/usr/bin/strace','-ff','-ttt','-xx','-yy','-s','128','-e','trace=process,kill,read,write','-o',str(captured_folder/'trace'),
             str(capture_root/'build/driver'),'idle',str(captured_folder/'worker'),str(captured_folder/'events.jsonl')])
        reference.require(argv==expected,'native residual fixed command changed')
        if name=='idle':
            launcher='#!/bin/sh\nexec /usr/bin/python3 -I -S -B -c '+shlex.quote(BOOTSTRAP)+' '+ ' '.join(
                shlex.quote(str(p)) for p in [Path(report['parameters']['project'])/'scripts',Path(report['parameters']['project'])/FIXTURE,'idle',captured_folder/'events.jsonl'])+'\n'
            reference.require((folder/'worker').read_text()==launcher,'idle launcher actor changed')
    lifecycle=reference.load(root/'store/cluster-lifecycle.json')
    reference.require(lifecycle['pid']>1 and lifecycle['start_ticks'] and lifecycle['stop_exit_code']==0 and
        lifecycle['postmaster_pid_file_absent'] is True and
        [r['name'] for r in lifecycle['commands']]==['initdb','start','store-driver','stop'] and
        all(r['exit_code']==0 for r in lifecycle['commands']),'private cluster start/stop failed')
    runtime=reference.load(root/'store/runtime-observations.json');invocation=reference.load(root/'store/result.json')['invocation']
    members=[p for p in runtime['processes'] if p['launcher_pid']==invocation['pid'] and
        p['launcher_start_ticks']==invocation['start_ticks'] and p['pid']>1 and p['process_start_ticks']]
    vector_path=Path('/usr/lib/postgresql/18/lib/vector.so');vector_sha=reference.digest(vector_path)
    servers=[p for p in members if p['executable_sha256']==report['pg_sha256']['postgres']]
    vector_bindings=[dict(pid=p['pid'],process_start_ticks=p['process_start_ticks'],library=lib) for p in servers
        for lib in p['libraries'] if lib['path']==str(vector_path) and lib['sha256']==vector_sha]
    reference.require(any(p['process_start_ticks']==lifecycle['start_ticks'] for p in servers) and vector_bindings and
        any(p['executable_sha256']==report['files_sha256']['build/driver'] for p in members) and
        any(p['environment_matches_contract'] is True and p['executable'].startswith('/usr/bin/python') for p in members) and
        runtime['model_workers']==[], 'actual native driver/PostgreSQL/vector binding missing or model scope crossed')
    runtime_scope=dict(state='partial',postgres_main_start_ticks=lifecycle['start_ticks'],observed_postgres_processes=len(servers),
        vector_library_bindings=vector_bindings,observer_errors=runtime['errors'],
        native_driver_environment=reference.unobserved('native Go process exec environment was not recovered; the bound Python launch command inherits its observed environment'),
        server_environment=reference.unobserved('PostgreSQL rewrites its process title/environment block; runtime observer cannot certify server exec environment'),
        scope='native driver and selected PostgreSQL/vector mappings observed; not a complete process inventory or server environment proof')
    result=store_rows(reference.load(root/'store/store-observations.json'))
    for row in result.values():row['runtime_observation']=runtime_scope
    result[IDLE_ID]=idle_row(root/report['idle_folder'])
    return result


def seal_without_report(root,name):
    return {rel:pin for rel,pin in seal(root).items() if rel!=name}
