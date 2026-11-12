"""Five ordinary real-Source controls and two labelled serialization guard controls."""
import hashlib
import json
from pathlib import Path
import shutil

import check_semantic_reference as reference
import semantic_refusal_source as source
from semantic_refusal_native_residuals import BOOTSTRAP, FIXTURE, clean, seal, seal_without_report
from compare_semantic_workers import ComparisonFailure, validate_embedding


CAP_IDS={'F-STREAM/cache-size/'+v for v in ('ordinary-response-over64KiB','combined-over-allowance')}


def inputs():
    note=dict(record_id='ordinary-control',version=1,body='body',body_sha256=hashlib.sha256(b'body').hexdigest())
    cases=[]
    for name,field in [('missing-record-id','record_id'),('missing-version','version')]:
        cases.append(dict(id='F-RANK/structural-errors/'+name,mode='rank',cache=False,
            input=(json.dumps(dict(query='query',notes=[{k:v for k,v in note.items() if k!=field}]))+'\n').encode(),
            error="KeyError: '"+field+"'"))
    for operation,text in [('identity',''),('query','source tuple control'),('document','é source span control.')]:
        cases.append(dict(id='F-EMBED/identity-query-document/'+operation,mode='embed',cache=False,
            input=(json.dumps(dict(id='ordinary-'+operation,operation=operation,text=text))+'\n').encode(),error=None))
    return cases


def cap_input():
    return (json.dumps(dict(id='cap',request=dict(query='query',notes=[dict(record_id='ordinary-control',version=1,body='body',
        body_sha256=hashlib.sha256(b'body').hexdigest())])))+'\n').encode()


def spec(value):
    ids={r['id'] for r in inputs()}|CAP_IDS
    rows=[s for r in reference.failure_inventory(value)['cases'] for s in r['subcases'] if s['id'] in ids]
    reference.require(len(rows)==7,'source residual catalogue changed')
    return dict(actors={p:reference.digest(reference.PROJECT/p) for p in (*source.ACTOR_SOURCES,FIXTURE)},
        case_inventory_sha256=hashlib.sha256(json.dumps(sorted(rows,key=lambda r:r['id']),sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        source_bootstrap=source.BOOTSTRAP,fixture_bootstrap=BOOTSTRAP)


def collect(args,value):
    reference.verify_runtime(value,args.runtime_binding,args.site,args.python_home);reference.verify_assets(value,args.assets)
    output=args.output.absolute();reference.require(not output.exists(),'source residual output must be fresh');output.mkdir(mode=0o700)
    shutil.copyfile(args.runtime_binding,output/'runtime-binding.json')
    params=dict(project=str(reference.PROJECT),python_home=str(args.python_home),site=str(args.site),assets=str(args.assets))
    for number,case in enumerate(inputs()):
        folder=output/('source-'+str(number))
        with reference.SemanticIsolation(read_only_paths=[reference.PROJECT,args.python_home,args.site,args.assets],output_dir=folder) as iso:
            (folder/'worker.stdin').write_bytes(case['input'])
            done=iso.run(source.arguments(params,case),input=case['input'],timeout=25,env=source.environment(case),stdout_limit=4194304,
                role='S01b-source-model-refusal',mode=case['id'])
            (folder/'worker.stdout').write_bytes(done.stdout);(folder/'worker.stderr').write_bytes(done.stderr)
            reference.save(folder/'result.json',dict(exit_code=done.returncode,invocation=done.invocation))
        print(json.dumps(dict(case=case['id'],exit_code=done.returncode)),flush=True)
    for kind in ('ordinary','combined'):
        for edge in ('boundary','over'):
            folder=output/('caps-'+kind+'-'+edge)
            with reference.SemanticIsolation(read_only_paths=[reference.PROJECT,args.python_home],output_dir=folder) as iso:
                raw=cap_input();(folder/'worker.stdin').write_bytes(raw)
                argv=[str(args.python_home/'bin/python3.11'),'-I','-S','-B','-c',BOOTSTRAP,str(reference.PROJECT/'scripts'),
                      str(reference.PROJECT/FIXTURE),'caps',kind,edge,str(folder/'double-observation.json')]
                done=iso.run(argv,input=raw,timeout=10,env=dict(reference.WORKER_ENV),stdout_limit=1048576,
                    role='S01b-source-protocol-double',mode=kind+'-'+edge)
                (folder/'worker.stdout').write_bytes(done.stdout);(folder/'worker.stderr').write_bytes(done.stderr)
                reference.save(folder/'result.json',dict(exit_code=done.returncode,invocation=done.invocation))
    report=dict(schema='cairn.semantic-source-residuals/1',producer=spec(value),parameters=params,
        python_sha256=value['python_sha256'],assets_sha256=value['assets_sha256'],
        runtime_binding_sha256=value['retained']['runtime-binding']['sha256'],
        scopes=dict(ordinary_source=dict(real_model_initialized=True,real_inference_operations=['rank','query','document']),
                    serialization_guards=dict(Scorer_double=True,real_model_initialized=False,real_model_inference=False)),
        files_sha256=seal(output))
    reference.save(output/'source-residual-report.json',report)
    return report


def receipt(value,folder,argv,role,mode,raw):
    cleanup=clean(value,folder);result=reference.load(folder/'result.json');invocation=result['invocation']
    reference.require((folder/'worker.stdin').read_bytes()==raw and type(result['exit_code']) is int and
        invocation['role']==role and invocation['mode']==mode and type(invocation['pid']) is int and invocation['pid']>1 and
        invocation['argv'][invocation['argv'].index('--')+1:]==argv and
        invocation['contract']['environment']==dict(reference.WORKER_ENV,PWD='/') and
        cleanup['launcher_exit_codes']==[0,result['exit_code']] and cleanup['invocations'][1:]==[invocation] and
        invocation['declaration_sha256']==reference.digest(folder/'command-0002.json'),'source residual input/command/lifecycle changed')
    return result


def source_row(value,folder,case,params,libraries,identity):
    result=receipt(value,folder,source.arguments(params,case),'S01b-source-model-refusal',case['id'],case['input'])
    raw=(folder/'worker.stdout').read_bytes();stderr=(folder/'worker.stderr').read_text()
    frames=[json.loads(line) for line in raw.splitlines()]
    if case['error']:
        reference.require(result['exit_code']==1 and not frames and case['error'] in stderr.splitlines(),'source metadata refusal changed')
    else:
        request=json.loads(case['input']);reference.require(result['exit_code']==0 and len(frames)==1 and raw.endswith(b'\n'),'source positive operation failed')
        frame=frames[0];operation=request['operation']
        fields={'id','identity'}|({'vector','query_projection'} if operation=='query' else {'passages'} if operation=='document' else set())
        reference.require(set(frame)==fields and frame['id']==request['id'] and
            frame['identity']==dict(model_sha256=identity,algorithm='bge-original-passages/1',dimensions=384),'source operation identity/fields changed')
        if operation!='identity':
            try:validate_embedding(frame,request,identity)
            except ComparisonFailure as error:raise ValueError(str(error)) from error
    runtime=reference.load(folder/'runtime-observations.json');invocation=result['invocation']
    workers=[w for w in runtime['model_workers'] if w['launcher_pid']==invocation['pid'] and
        w['launcher_start_ticks']==invocation['start_ticks'] and w['pid']!=invocation['pid']]
    reference.require(len(workers)==1 and workers[0]['pid']>1 and not runtime['errors'] and
        workers[0]['role']==invocation['role'] and workers[0]['mode']==case['id'] and
        workers[0]['environment_matches_contract'] is True and workers[0]['executable_sha256']==value['python_sha256'] and
        libraries<={(r['path'],r['sha256']) for r in workers[0]['libraries']},'actual source model/runtime not observed for this call')
    return dict(id=case['id'],scope='Python/source-model-process',freshness='new bounded ordinary Source capture',complete=True,
        model_initialized=True,real_model_inference=not case['id'].endswith('/identity'),runtime_model_workers=workers,
        disposition=dict(go_error_or_result=reference.unobserved('direct Source; no Go owner'),
            discovery_coverage=reference.unobserved('no discovery call'),
            process_exit_kill=dict(state='observed',exit_code=result['exit_code'],killed=False),
            stdout_prefix=dict(state='observed',hex=raw[:64].hex(),sha256=reference.digest(folder/'worker.stdout')),
            cache=reference.unobserved('no rank snapshot operation'),restarts=dict(state='observed',invocations=1,count=0)))


def cap_row(value,root,kind,params):
    observations=[]
    for edge in ('boundary','over'):
        folder=root/('caps-'+kind+'-'+edge)
        argv=[str(Path(params['python_home'])/'bin/python3.11'),'-I','-S','-B','-c',BOOTSTRAP,str(Path(params['project'])/'scripts'),
            str(Path(params['project'])/FIXTURE),'caps',kind,edge,str(folder/'double-observation.json')]
        result=receipt(value,folder,argv,'S01b-source-protocol-double',kind+'-'+edge,cap_input())
        row=reference.load(folder/'double-observation.json');raw=(folder/'worker.stdout').read_bytes();stderr=(folder/'worker.stderr').read_text()
        reference.require(set(row)=={'scope','model_initialized','real_model_inference','kind','edge','calls','source_sha256',
                'ordinary_bytes','snapshot_bytes','combined_bytes'} and row['scope']=='Python/actual-serve_stream/Scorer-double' and
            row['kind']==kind and row['edge']==edge and row['model_initialized'] is False and row['real_model_inference'] is False and
            row['source_sha256']==value['source_sha256']['scripts/semantic_rank.py'],'unsupported protocol double/model claim')
        reference.require(row['ordinary_bytes']==65536+int(kind=='ordinary' and edge=='over') and
            (row['combined_bytes']==614400+65536+int(edge=='over') and row['snapshot_bytes']<=614400 if kind=='combined' else True),
            'serialization guard input did not meet its exact boundary')
        methods=['constructor']+(['restore'] if kind=='combined' else [])+['score']+(['snapshot'] if kind=='combined' else [])
        reference.require([r['method'] for r in row['calls']]==methods and
            row['calls'][methods.index('score')]['request']==json.loads(cap_input())['request'],'protocol double did not reach actual serializer')
        if edge=='boundary':
            reference.require(result['exit_code']==0 and raw.endswith(b'\n') and len(raw)==(row['combined_bytes'] if kind=='combined' else row['ordinary_bytes']) and
                len(raw.splitlines())==1 and json.loads(raw)['id']=='cap','accepted serializer boundary changed')
        else:
            error='worker response exceeds limit' if kind=='ordinary' else 'worker response and cache exceed limit'
            reference.require(result['exit_code']==1 and raw==b'' and 'ValueError: '+error in stderr.splitlines(),'serializer overflow did not refuse without output')
        runtime=reference.load(folder/'runtime-observations.json');reference.require(runtime['model_workers']==[] and not runtime['errors'],'protocol double initialized a model')
        observations.append(dict(edge=edge,fixture=row,exit_code=result['exit_code'],killed=False,stdout_prefix_hex=raw[:64].hex(),
            stdout_sha256=reference.digest(folder/'worker.stdout'),stdout_bytes=len(raw)))
    identifier='F-STREAM/cache-size/'+('ordinary-response-over64KiB' if kind=='ordinary' else 'combined-over-allowance')
    disposition={k:reference.unobserved('actual Python serializer with Scorer double; no Go owner/discovery or real rank cache') for k in reference.DISPOSITION_FIELDS}
    disposition.update(process_exit_kill=dict(state='observed',probes=[{k:r[k] for k in ('edge','exit_code','killed')} for r in observations]),
        stdout_prefix=dict(state='observed',probes=[{k:r[k] for k in ('edge','stdout_prefix_hex','stdout_sha256','stdout_bytes')} for r in observations]),
        restarts=dict(state='observed',independent_invocations=2,count=0))
    return dict(id=identifier,scope='Python/actual-serve_stream/Scorer-double',freshness='new protocol-double capture',
        probes=observations,disposition=disposition,complete=True)


def verify(value,evidence_root,path,sha):
    reference.require(sha and reference.digest(path)==sha,'pinned source residual report required')
    report=reference.load(path);root=path.parent
    reference.require(set(report)=={'schema','producer','parameters','python_sha256','assets_sha256','runtime_binding_sha256','scopes','files_sha256'} and
        report['schema']=='cairn.semantic-source-residuals/1' and report['producer']==spec(value) and
        report['python_sha256']==value['python_sha256'] and report['assets_sha256']==value['assets_sha256'] and
        report['runtime_binding_sha256']==value['retained']['runtime-binding']['sha256'] and
        report['scopes']==dict(ordinary_source=dict(real_model_initialized=True,real_inference_operations=['rank','query','document']),
            serialization_guards=dict(Scorer_double=True,real_model_initialized=False,real_model_inference=False)),
        'source residual source/runtime/scope changed')
    for rel,pin in report['files_sha256'].items():reference.pinned(root,rel,pin)
    reference.require(report['files_sha256']==seal_without_report(root,'source-residual-report.json'),'unsealed source residual artifact')
    params=report['parameters'];reference.require(set(params)=={'project','python_home','site','assets'} and
        all(Path(p).is_absolute() for p in params.values()),'source residual paths changed')
    binding=reference.load(reference.pinned(root,'runtime-binding.json',report['runtime_binding_sha256']))
    libraries={(str(Path(params['site'])/r['relative']),r['sha256']) for r in binding['frozen_numeric_native_files']}
    base=Path(value['retained']['python-characterization']['relative']).parent
    identity=reference.load(Path(evidence_root)/base/'raw/embed-identity.stdout')['identity']['model_sha256']
    result={case['id']:source_row(value,root/('source-'+str(number)),case,params,libraries,identity) for number,case in enumerate(inputs())}
    for kind in ('ordinary','combined'):
        row=cap_row(value,root,kind,params);result[row['id']]=row
    return result
