"""Fixed source-worker negative controls through the retained Python runtime."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import struct

import check_semantic_reference as reference
from semantic_fixture_cases import embedding_failure_cases


BOOTSTRAP = "import sys,runpy;sys.path[:0]=[sys.argv[1],sys.argv[2]];p=sys.argv[3];sys.argv=sys.argv[3:];runpy.run_path(p,run_name='__main__')"
ACTOR_SOURCES = ('scripts/semantic_rank.py', 'scripts/embedding_worker.py',
                 'scripts/semantic_acceptance_isolation.py')


def inputs(value, root):
    pin=value['retained']['python-stream34']
    first=json.loads(reference.pinned(root,pin['relative'],pin['sha256']).read_bytes().splitlines()[0])
    model=first['result']['model_sha256']
    note=dict(record_id='record',version=1,body='body',body_sha256=hashlib.sha256(b'body').hexdigest())
    request=dict(query='query',notes=[note])
    key=hashlib.sha256(b'passage').hexdigest()
    vector=base64.b64encode(struct.pack('<384f',*([1.0]*384))).decode()
    snapshot=dict(format='cairn.passage-vectors/1',model_sha256=model,vectors={key:vector})
    cases=[]

    def add(identifier,mode,frames,error=None,raw=None,cache=True):
        if raw is None:raw=b''.join(json.dumps(frame,separators=(',',':'),ensure_ascii=False).encode()+b'\n' for frame in frames)
        cases.append(dict(id=identifier,mode=mode,input=raw,error=error,cache=cache))

    for row in embedding_failure_cases():
        ident=row['id']
        if ident.startswith('blank-query-'):target='F-EMBED/blank-query/'+ident
        elif ident.startswith('blank-document-'):target='F-EMBED/blank-document/'+ident
        else:target={'query-empty':'F-EMBED/blank-query/empty','document-empty':'F-EMBED/blank-document/empty',
                     'query-byte-overflow':'F-EMBED/wrong-fields-types/query-byte-overflow',
                     'document-byte-overflow':'F-EMBED/65536-byte-document/overflow65537',
                     'document-token-empty':'F-EMBED/token-empty-document/boundary'}[ident]
        expected=next(r for r in value['typed_failures'] if r['id']==ident)
        add(target,'embed',[{k:row[k] for k in ('id','operation','text')}],
            expected['expected_exception']+': '+expected['expected_message'])
    identity=dict(id='identity',operation='identity',text='')
    for field in ('id','operation','text'):
        add('F-EMBED/wrong-fields-types/missing-'+field,'embed',
            [{k:v for k,v in identity.items() if k!=field}],'ValueError: invalid embedding request')
    for name,frame,error in [('extra-field',dict(identity,extra=1),'invalid embedding request'),
                             ('id-type',dict(identity,id=1),'invalid request identity or text'),
                             ('operation-type',dict(identity,operation=[]),'unknown embedding operation'),
                             ('text-type',dict(identity,text=None),'invalid request identity or text'),
                             ('unknown-operation',dict(identity,operation='unknown'),'unknown embedding operation')]:
        add('F-EMBED/wrong-fields-types/'+name,'embed',[frame],'ValueError: '+error)
    for name,text in [('ascii128','x'*128),('ascii129','x'*129),('unicode128','漢'*128),('unicode129','漢'*129)]:
        add('F-EMBED/unicode-id-boundaries/'+name,'embed',[dict(identity,id=text)],
            'ValueError: invalid request identity or text' if name.endswith('129') else None)
    add('F-EMBED/nonempty-identity-text/boundary','embed',[dict(identity,text='nonempty')],
        'ValueError: identity request must have empty text')
    add('F-EMBED/missing-newline/boundary','embed',[], 'ValueError: embedding request frame exceeds bounds',
        raw=json.dumps(identity).encode())
    add('F-EMBED/over512KiB-frame/boundary','embed',[], 'ValueError: embedding request frame exceeds bounds',
        raw=b'x'*(512*1024+1)+b'\n')
    for kind in ('request','note'):
        frame=dict(request,extra='ignored') if kind=='request' else dict(request,notes=[dict(note,extra='ignored')])
        add('F-RANK/ignored-extra-'+kind+'-key/boundary','rank',[frame])
    for name,version in [('string','one'),('null',None),('boolean',True),('array',[1]),('object',{'version':1}),('fraction',1.5)]:
        add('F-RANK/echoed-version-types/'+name,'rank',[dict(request,notes=[dict(note,version=version)])])
    add('F-RANK/query-over512-within4096/boundary','rank',[dict(request,query='word '*600)],
        'ValueError: query exceeds model input limit')
    for name,count,error in [('128-occurrences',127,None),('129-occurrences',128,'ValueError: semantic chunk budget exceeded')]:
        body='word '*(320*count+384)
        add('F-RANK/'+name+'/boundary','rank',[dict(request,notes=[dict(note,body=body,
            body_sha256=hashlib.sha256(body.encode()).hexdigest())])],error)
    empty=dict(note,record_id='empty',body='\u200b',body_sha256=hashlib.sha256('\u200b'.encode()).hexdigest())
    add('F-RANK/token-empty-note/boundary','rank',[dict(request,notes=[note,empty])],
        'ValueError: note has no model tokens')

    def envelope(identifier,cache_marker=None):
        frame=dict(id=identifier,request=request)
        if cache_marker is not None:frame['cache']=cache_marker
        return frame

    cache_cases=[
      ('F-STREAM/corrupt-cache/nonobject',[], 'ValueError: invalid snapshot format'),
      ('F-STREAM/corrupt-cache/wrong-format',dict(snapshot,format='wrong'),'ValueError: invalid snapshot format'),
      ('F-STREAM/corrupt-cache/extra-field',dict(snapshot,extra=1),'ValueError: invalid snapshot format'),
      ('F-STREAM/corrupt-cache/missing-field',{k:v for k,v in snapshot.items() if k!='vectors'},'ValueError: invalid snapshot format'),
      ('F-STREAM/entry-hash-base64/wrong-container',dict(snapshot,vectors=[]),'ValueError: invalid snapshot entries'),
      ('F-STREAM/entry-hash-base64/129-entries',dict(snapshot,vectors={hashlib.sha256(str(i).encode()).hexdigest():vector for i in range(129)}),'ValueError: invalid snapshot entries'),
      ('F-STREAM/entry-hash-base64/hash-length',dict(snapshot,vectors={'short':vector}),'ValueError: invalid passage hash'),
      ('F-STREAM/entry-hash-base64/hash-alphabet',dict(snapshot,vectors={'g'*64:vector}),'ValueError: invalid passage hash'),
      ('F-STREAM/entry-hash-base64/encoded-nonstring',dict(snapshot,vectors={key:1}),'ValueError: invalid vector size'),
      ('F-STREAM/entry-hash-base64/encoded-length',dict(snapshot,vectors={key:'bad'}),'ValueError: invalid vector size'),
      ('F-STREAM/entry-hash-base64/invalid-base64',dict(snapshot,vectors={key:'!'*2048}),'binascii.Error: Only base64 data is allowed'),
      ('F-STREAM/entry-hash-base64/decoded-length',dict(snapshot,vectors={key:base64.b64encode(b'x'*1534).decode()}),'ValueError: invalid vector size'),
      ('F-STREAM/cache-size/snapshot-over600KiB',dict(snapshot,vectors={key:'x'*(600*1024)}),'ValueError: snapshot exceeds limit'),
      ('F-STREAM/cache-size/entry-count129',dict(snapshot,vectors={hashlib.sha256(str(i).encode()).hexdigest():vector for i in range(129)}),'ValueError: invalid snapshot entries'),
      ('F-STREAM/format-size-before-model/wrong-model-bad-format',dict(snapshot,model_sha256='different',format='wrong'),'ValueError: invalid snapshot format'),
      ('F-STREAM/format-size-before-model/wrong-model-oversize',dict(snapshot,model_sha256='different',vectors={key:'x'*(600*1024)}),'ValueError: snapshot exceeds limit'),
    ]
    for name,number in [('zero',0.0),('NaN',float('nan')),('positive-infinity',float('inf')),('negative-infinity',-float('inf'))]:
        cache_cases.append(('F-STREAM/finite-nonzero/'+name,dict(snapshot,vectors={key:base64.b64encode(struct.pack('<384f',*([number]*384))).decode()}),'ValueError: invalid vector values'))
    for identifier,state,error in cache_cases:add(identifier,'stream',[envelope('cache',state)],error)
    different=dict(snapshot,model_sha256='different',vectors={'malformed':17})
    for identifier in ('F-STREAM/wrong-model-cache/boundary','F-STREAM/format-size-before-model/wrong-model-malformed-values-ignored'):
        add(identifier,'stream',[envelope('ignored',different)])
    add('F-STREAM/warm-cold-equality/boundary','stream',[envelope('cold'),envelope('warm')])
    add('F-STREAM/late-restore/boundary','stream',[envelope('one'),envelope('two',snapshot)],
        'ValueError: cache may only be restored in a fresh worker')
    bad=dict(id='two',request=dict(request,notes=[dict(note,body_sha256='wrong')]))
    add('F-STREAM/second-frame-failure/boundary','stream',[envelope('one'),bad],'ValueError: note content hash mismatch')
    add('F-STREAM/unadvertised-cache/output-cache-omitted','stream',[envelope('old-host')],cache=False)
    return cases


def outcome(folder,case):
    raw=(folder/'worker.stdout').read_bytes();stderr=(folder/'worker.stderr').read_text()
    result=reference.load(folder/'result.json')
    reference.require(type(result['exit_code']) is int, 'source exit status must be an integer')
    frames=[json.loads(line) for line in raw.splitlines()]
    if case['error']:
        reference.require(result['exit_code']==1 and case['error'] in stderr.splitlines(),
                          'source refusal changed: '+case['id'])
        expected_frames=1 if case['id'].startswith(('F-STREAM/late-restore/','F-STREAM/second-frame-failure/')) else 0
        reference.require(len(frames)==expected_frames,'unexpected partial source reply')
    else:
        expected_frames=2 if case['id']=='F-STREAM/warm-cold-equality/boundary' else 1
        reference.require(result['exit_code']==0 and len(frames)==expected_frames,
                          'source accepted boundary failed: '+case['id'])
    if frames:
        requests=[json.loads(line) for line in case['input'].splitlines()]
        for frame,request in zip(frames,requests,strict=False):
            if case['mode']=='embed':
                reference.require(frame['id']==request['id'] and set(frame)=={'id','identity'} and
                    frame['identity']['algorithm']=='bge-original-passages/1' and
                    frame['identity']['dimensions']==384 and len(frame['identity']['model_sha256'])==64,
                    'source embedding identity reply changed')
            else:
                reply=frame['result'] if case['mode']=='stream' else frame
                request=request['request'] if case['mode']=='stream' else request
                reference.require(reply['algorithm']=='bge-max-chunk/1' and len(reply['model_sha256'])==64 and
                    len(reply['scores'])==len(request['notes']), 'source rank reply shape changed')
                for score,note in zip(reply['scores'],request['notes'],strict=True):
                    reference.require(all(type(score[k]) is type(note[k]) and score[k]==note[k]
                        for k in ('record_id','version','body_sha256')) and type(score['score']) is int and
                        -1000000<=score['score']<=1000000, 'source rank reply metadata changed')
    if case['id']=='F-STREAM/warm-cold-equality/boundary':
        reference.require(len(frames)==2 and frames[0]['result']==frames[1]['result'], 'source warm/cold results changed')
    if case['id'].startswith('F-RANK/echoed-version-types/'):
        request=json.loads(case['input']);version=request['notes'][0]['version'];actual=frames[0]['scores'][0]['version']
        reference.require(type(actual) is type(version) and actual==version,'Source version echo was retyped')
    if case['id']=='F-STREAM/unadvertised-cache/output-cache-omitted':
        reference.require(all('cache' not in f for f in frames),'unadvertised source cache emitted')
    runtime=reference.load(folder/'runtime-observations.json')
    invocation=result['invocation']
    workers=[w for w in runtime['model_workers'] if w['launcher_pid']==invocation['pid'] and
             w['launcher_start_ticks']==invocation['start_ticks'] and w['pid']!=invocation['pid']]
    reference.require(type(invocation['pid']) is int and invocation['pid']>1 and
                      len(workers)==1 and type(workers[0]['pid']) is int and workers[0]['pid']>1 and not runtime['errors'] and
                      workers[0]['role']==invocation['role']=='S01b-source-model-refusal' and
                      workers[0]['mode']==invocation['mode']==case['id'] and
                      workers[0]['environment_matches_contract'] is True,
                      'source model/runtime not observed for this invocation')
    snapshots=[f['cache'] for f in frames if 'cache' in f]
    return dict(id=case['id'],scope='Python/source-model-process',freshness='reused',
        disposition=dict(go_error_or_result=reference.unobserved('direct source worker, no Go owner'),
          discovery_coverage=reference.unobserved('no discovery call'),
          process_exit_kill=dict(state='observed',exit_code=result['exit_code'],killed=result['exit_code']<0),
          stdout_prefix=dict(state='observed',hex=raw[:64].hex(),sha256=reference.digest(folder/'worker.stdout')),
          cache=dict(state='observed',published_snapshots=len(snapshots),snapshot_sha256=[hashlib.sha256(json.dumps(s,sort_keys=True).encode()).hexdigest() for s in snapshots]),
          restarts=dict(state='observed',invocations=1,count=0)),
        runtime_model_workers=workers)


def arguments(parameters, case):
    worker=Path(parameters['project'])/'scripts'/('embedding_worker.py' if case['mode']=='embed' else 'semantic_rank.py')
    argv=[str(Path(parameters['python_home'])/'bin/python3.11'),'-I','-S','-B','-c',BOOTSTRAP,
          str(Path(parameters['project'])/'scripts'),parameters['site'],str(worker),'--model-dir',parameters['assets']]
    return argv+(['--stream'] if case['mode']=='stream' else [])


def environment(case):
    result=dict(reference.WORKER_ENV)
    if case['mode']=='stream' and case['cache']:
        result['CAIRN_SEMANTIC_STREAM_CACHE']='1'
    return result


def verify_case(value, root, row, case, parameters, libraries):
    folder=reference.pinned_case_files(root,row['folder'],row['files_sha256'],
        {'worker.stdin','worker.stdout','worker.stderr','result.json','runtime-observations.json',
         'cleanup.json','isolation-proof.json','telemetry-metadata.json','command-0001.json','command-0002.json'})
    reference.require((folder/'worker.stdin').read_bytes()==case['input'],'source input binding changed')
    cleanup=reference.load(folder/'cleanup.json');proof=reference.load(folder/'isolation-proof.json')
    reference.require(cleanup['all_observed_processes_exited'] is True and cleanup['errors']==[] and
        cleanup['backing_removed'] is True and
        proof['wrapper_sha256']==value['source_sha256']['scripts/semantic_acceptance_isolation.py'],
        'source isolation/cleanup changed')
    reference.require(cleanup['isolation_proof_sha256']==reference.digest(folder/'isolation-proof.json') and
        proof['profile']=='default' and all(m['read_only'] for m in proof['read_only_mounts']),
        'source isolation proof binding changed')
    result=reference.load(folder/'result.json');invocation=result['invocation']
    reference.require(cleanup['launcher_exit_codes']==[0,result['exit_code']] and
        len(cleanup['invocations'])==2 and invocation==cleanup['invocations'][1] and
        type(invocation['pid']) is int and invocation['pid']>1 and
        invocation['argv'][invocation['argv'].index('--')+1:]==arguments(parameters,case) and
        invocation['contract']['environment']==dict(environment(case),PWD='/') and
        invocation['declaration_sha256']==reference.digest(folder/'command-0002.json'),
        'source command/invocation binding changed')
    observation=outcome(folder,case)
    worker=observation['runtime_model_workers'][0]
    reference.require(worker['executable_sha256']==value['python_sha256'] and
        libraries<={(r['path'],r['sha256']) for r in worker['libraries']},
        'actual source numeric runtime differs from frozen binding')
    return observation


def reuse_manifest(value,path,sha,parameters,expected,libraries):
    reference.require(sha and reference.digest(path)==sha,'pinned interrupted capture manifest required')
    manifest=reference.load(path)
    reference.require(set(manifest)=={'schema','root','actor_sources','python_sha256','runtime_binding_sha256',
        'assets_sha256','parameters','producer_tool_provenance','cases'} and
        manifest['schema']=='cairn.semantic-interrupted-source-capture/1' and
        manifest['producer_tool_provenance']=='not retained by interrupted collector; exact raw captures sealed after interruption',
        'unsupported interrupted-capture provenance claim')
    reference.require(manifest['actor_sources']=={p:value['source_sha256'][p] for p in ACTOR_SOURCES} and
        manifest['python_sha256']==value['python_sha256'] and
        manifest['runtime_binding_sha256']==value['retained']['runtime-binding']['sha256'] and
        manifest['assets_sha256']==value['assets_sha256'] and manifest['parameters']==parameters,
        'interrupted capture source/runtime bindings changed')
    ids=[r['id'] for r in manifest['cases']]
    reference.require(len(ids)==len(set(ids)) and set(ids)<=set(expected),'duplicate/unknown interrupted source cases')
    qualified={};unqualified=[]
    for row in manifest['cases']:
        reference.require(set(row)=={'id','folder','files_sha256'},'interrupted capture fields changed')
        # All retained bytes are checked even when their runtime proof refuses qualification.
        for rel,pin in row['files_sha256'].items():reference.pinned(Path(manifest['root']),rel,pin)
        try:
            observation=verify_case(value,Path(manifest['root']),row,expected[row['id']],parameters,libraries)
        except (ValueError,KeyError,TypeError) as error:
            unqualified.append(dict(id=row['id'],reason=str(error)))
        else:
            qualified[row['id']]=(Path(manifest['root'])/row['folder'],observation)
    return qualified,unqualified


def collect(args,value):
    reference.verify_runtime(value,args.runtime_binding,args.site,args.python_home)
    reference.verify_assets(value,args.assets)
    cases=inputs(value,args.evidence_root)
    ledger=reference.failure_inventory(value);allowed={s['id'] for r in ledger['cases'] for s in r['subcases']}
    reference.require({c['id'] for c in cases}<=allowed,'source fixture outside ledger')
    output=args.output.absolute();reference.require(not output.exists(),'source output must be fresh');output.mkdir(mode=0o700)
    shutil.copyfile(args.runtime_binding,output/'runtime-binding.json')
    report=dict(schema='cairn.semantic-source-refusals/1',actor_sources={p:value['source_sha256'][p] for p in ACTOR_SOURCES},
        producer_ledger_sha256=value['failure_ledger']['sha256'],producer_tool_sha256=reference.tool_pins(),
        python_sha256=value['python_sha256'],runtime_binding_sha256=value['retained']['runtime-binding']['sha256'],
        assets_sha256=value['assets_sha256'],cases=[],failed=[],model_execution=True,
        parameters=dict(project=str(reference.PROJECT),python_home=str(args.python_home),site=str(args.site),assets=str(args.assets)),
        reuse=None,prior_unqualified=[])
    binding=reference.load(args.runtime_binding)
    library_pins=[dict(path=str(args.site/r['relative']),sha256=r['sha256'])
                  for r in binding['frozen_numeric_native_files']]
    libraries={(r['path'],r['sha256']) for r in library_pins}
    reused={}
    if args.reuse_capture_manifest:
        reused,report['prior_unqualified']=reuse_manifest(value,args.reuse_capture_manifest,
            args.reuse_capture_manifest_sha256,report['parameters'],{c['id']:c for c in cases},libraries)
        shutil.copyfile(args.reuse_capture_manifest,output/'prior-capture-manifest.json')
        report['reuse']=dict(sha256=reference.digest(output/'prior-capture-manifest.json'),
                            scope='raw captured prefix requalified; interrupted producer tool pins remain unknown')
    for index,case in enumerate(cases):
        folder=output/str(index).zfill(3)
        if case['id'] in reused:
            original,observation=reused[case['id']]
            folder.mkdir()
            for original_file in original.iterdir():
                if original_file.is_file():shutil.copy2(original_file,folder/original_file.name)
            files={str(p.relative_to(output)):reference.digest(p) for p in folder.iterdir() if p.is_file()}
            report['cases'].append(dict(id=case['id'],folder=folder.name,files_sha256=files,
                                        observation=observation,origin='reused interrupted raw capture'))
            continue
        env=environment(case)
        argv=arguments(report['parameters'],case)
        with reference.SemanticIsolation(read_only_paths=[reference.PROJECT,args.python_home,args.site,args.assets],output_dir=folder) as iso:
            (folder/'worker.stdin').write_bytes(case['input'])
            done=iso.run(argv,input=case['input'],timeout=25,env=env,stdout_limit=4194304,
                         role='S01b-source-model-refusal',mode=case['id'])
            (folder/'worker.stdout').write_bytes(done.stdout);(folder/'worker.stderr').write_bytes(done.stderr)
            reference.save(folder/'result.json',dict(exit_code=done.returncode,invocation=done.invocation))
            store=iso.home_dir/'.cache/Microsoft/DeveloperTools/.onnxruntime/onnxruntime.db'
            reference.save(folder/'telemetry-metadata.json',dict(mapped_disposable_store_present=store.exists(),
                           scope='selected path metadata only; no zero-effects claim; no store contents read'))
        files={str(p.relative_to(output)):reference.digest(p) for p in folder.iterdir() if p.is_file()}
        row=dict(id=case['id'],folder=folder.name,files_sha256=files)
        try:observation=verify_case(value,output,row,case,report['parameters'],libraries)
        except (ValueError,KeyError,TypeError) as error:
            report['failed'].append(dict(row,error=str(error)))
        else:
            report['cases'].append(dict(row,observation=observation,origin='new isolated execution'))
        print(json.dumps(dict(case=case['id'],exit_code=done.returncode)),flush=True)
    reference.require(report['producer_tool_sha256']==reference.tool_pins(),'source collector changed during execution')
    reference.save(output/'source-report.json',report)
    return report


def verify(value,root,path,sha):
    reference.require(sha and reference.digest(path)==sha,'pinned source report required')
    report=reference.load(path)
    reference.require(set(report)=={'schema','actor_sources','producer_ledger_sha256','producer_tool_sha256','python_sha256',
        'runtime_binding_sha256','assets_sha256','cases','failed','model_execution','parameters','reuse','prior_unqualified'} and
        report['schema']=='cairn.semantic-source-refusals/1' and report['model_execution'] is True,
        'unsupported source model/acceptance claim')
    reference.require(report['actor_sources']=={p:value['source_sha256'][p] for p in ACTOR_SOURCES} and
        report['python_sha256']==value['python_sha256'] and report['runtime_binding_sha256']==value['retained']['runtime-binding']['sha256'] and
        report['assets_sha256']==value['assets_sha256'],'source/runtime/asset bindings changed')
    parameters=report['parameters']
    reference.require(set(parameters)=={'project','python_home','site','assets'} and
                      all(Path(p).is_absolute() for p in parameters.values()),'source invocation paths changed')
    binding=reference.load(reference.pinned(path.parent,'runtime-binding.json',report['runtime_binding_sha256']))
    libraries={(str(Path(parameters['site'])/r['relative']),r['sha256']) for r in binding['frozen_numeric_native_files']}
    expected={c['id']:c for c in inputs(value,root)};ids=[c['id'] for c in report['cases']+report['failed']]
    reference.require(len(ids)==len(set(ids)) and set(ids)==set(expected),'missing/duplicate source cases')
    if report['reuse'] is not None:
        reference.require(set(report['reuse'])=={'sha256','scope'} and
            report['reuse']['scope']=='raw captured prefix requalified; interrupted producer tool pins remain unknown',
            'unsupported source reuse claim')
        qualified,unqualified=reuse_manifest(value,path.parent/'prior-capture-manifest.json',report['reuse']['sha256'],
            parameters,expected,libraries)
        reference.require(unqualified==report['prior_unqualified'],'interrupted runtime failure was hidden')
    else:
        qualified={}
        reference.require(report['prior_unqualified']==[],'unbound prior source failure')
    for row in report['failed']:
        reference.require(set(row)=={'id','folder','files_sha256','error'} and isinstance(row['error'],str) and row['error'],
                          'unqualified source case must name its gap')
        reference.pinned_case_files(path.parent,row['folder'],row['files_sha256'],
            {'worker.stdin','worker.stdout','worker.stderr','result.json','runtime-observations.json','cleanup.json'})
    result={}
    for row in report['cases']:
        reference.require(set(row)=={'id','folder','files_sha256','observation','origin'},'absent source disposition fields')
        reference.require(row['origin'] in ('new isolated execution','reused interrupted raw capture') and
            (row['origin']!='reused interrupted raw capture' or row['id'] in qualified), 'unbound source capture origin')
        observation=verify_case(value,path.parent,row,expected[row['id']],parameters,libraries)
        reference.require(observation==row['observation'],'source disposition disagrees with raw results')
        result[row['id']]=dict(observation,capture_origin=row['origin'],
            capture_producer_tool_binding=('unknown for interrupted producer; raw proof independently requalified'
                if row['origin']=='reused interrupted raw capture' else report['producer_tool_sha256']))
    return result
