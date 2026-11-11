"""Collect and check fixed source-function residuals; never initialize a model."""
import hashlib
import json
from pathlib import Path
import shutil
import struct

import check_semantic_reference as reference
from semantic_refusal_functions import ASSETS, PACKAGES


DRIVER="""import sys,json
from pathlib import Path
sys.path[:0]=[sys.argv[1],sys.argv[2]]
import semantic_refusal_functions as fixture
rows=fixture.rank_and_retreat()+fixture.identity_cases(Path(sys.argv[3])/'identity-inputs',Path(sys.argv[4]))
print(json.dumps(dict(schema='cairn.semantic-function-observations/1',cases=rows,model_execution=False),allow_nan=False))
"""
ROLE='S01b-function-residuals'
MODE='rank-retreat-identity-no-model'
RANK_ERRORS={
    'F-RANK/decoded-chunk-token-overflow/boundary':'decoded chunk exceeds model input limit',
    **{'F-RANK/nonfinite-inference/'+v:'invalid model embeddings'
       for v in ('NaN','positive-infinity','negative-infinity')},
    'F-RANK/zero-inference/boundary':'empty model embedding',
    'F-EMBED/long-query-retreat-refusal/boundary':'query prefix exceeds token bound',
}
IDENTITY_IDS={*('F-IDENTITY/each-model-asset/'+v for v in ASSETS),
    *('F-IDENTITY/each-implementation-source/'+v for v in ('semantic_rank.py','embedding_worker.py')),
    *('F-IDENTITY/each-vector-setting-version/'+v+'-version' for v in PACKAGES),
    *('F-IDENTITY/each-vector-setting-version/'+v for v in ('prefix','window','stride','algorithm','batch-size')),
    'F-IDENTITY/path-move-stable/boundary','F-IDENTITY/timestamp-stable/boundary'}


def validate_rows(value,root,rows,rank_identity,embed_identity):
    ids=[r['id'] for r in rows]
    reference.require(len(ids)==len(set(ids)) and set(ids)==set(RANK_ERRORS)|IDENTITY_IDS,
                      'missing/duplicate function residual IDs')
    result={}
    expected_cache={hashlib.sha256(b'previous').hexdigest():hashlib.sha256(struct.pack('<384f',*([1.0]*384))).hexdigest()}
    for row in rows:
        identifier=row['id']
        reference.require(row['model_initialized'] is False and row['real_model_inference'] is False,
                          'hash/function fixture cannot claim model initialization or real inference')
        disposition={name:reference.unobserved('direct source-function fixture; worker/Go/discovery lifecycle not exercised')
                     for name in reference.DISPOSITION_FIELDS}
        if identifier in RANK_ERRORS:
            rank=identifier.startswith('F-RANK/')
            keys={'id','input','scope','result','exception','model_initialized','real_model_inference',
                  'model_double_calls','tokenizer_double_calls','cache_before','cache_after'}
            keys.add('model_double_inputs' if rank else 'tokenizer_double_inputs')
            reference.require(set(row)==keys and row['result'] is None and
                row['exception']==dict(type='ValueError',message=RANK_ERRORS[identifier]),'function refusal/result changed')
            reference.require(row['scope']==('function/actual-NumPy/tokenizer-model-doubles' if rank else 'function/tokenizer-double'),
                              'function fixture scope changed')
            expected_calls=0 if 'decoded-chunk' in identifier or not rank else 1
            reference.require(type(row['model_double_calls']) is int and row['model_double_calls']==expected_calls,
                              'model-double call count changed')
            if rank:
                note=dict(record_id='fixture-record',version=1,body='body',body_sha256=hashlib.sha256(b'body').hexdigest())
                pattern='finite' if 'decoded-chunk' in identifier else 'zero' if 'zero-inference' in identifier else identifier.rsplit('/',1)[1]
                reference.require(row['input']==dict(request=dict(query='query',notes=[note]),model_pattern=pattern,
                    decoded_overflow='decoded-chunk' in identifier) and row['tokenizer_double_calls']==3,
                    'rank function input binding changed')
                reference.require(row['cache_before']==row['cache_after']==expected_cache,'failed function published partial cache')
                disposition['cache']=dict(state='observed',before=row['cache_before'],after=row['cache_after'],unchanged=True)
                wanted=[] if expected_calls==0 else [dict(texts=['Represent this sentence for searching relevant passages: query','body'],batch_size=1)]
                reference.require(row['model_double_inputs']==wanted,'rank double did not exercise intended inference boundary')
            else:
                prefix='Represent this sentence for searching relevant passages: '
                expected=[dict(text=prefix+'q'*100,tokens=600)]+[dict(text=prefix+'q'*10,tokens=513)]*8
                reference.require(row['input']==dict(id='retreat',operation='query',text='q'*100) and
                    row['tokenizer_double_calls']==9 and row['tokenizer_double_inputs']==expected and
                    row['cache_before'] is None and row['cache_after'] is None,'bounded retreat input/count changed')
        else:
            reference.require(set(row)=={'id','scope','input','before','after','steps','model_initialized','real_model_inference'} and
                row['scope']=='function/source-derivation/external-model-tokenizer-stubs','unsupported derivation fixture scope/claim')
            before=row['before'];after=row['after'];change=row['input']['mutation']
            reference.require(before['exception'] is None and before['identities']==dict(rank=rank_identity,embed=embed_identity),
                              'hash-only baseline differs from retained real-worker identity')
            reference.require(row['input']['baseline_files']==dict(assets=value['assets_sha256'],
                code={n:value['source_sha256']['scripts/'+n] for n in ('semantic_rank.py','embedding_worker.py')}),
                'derivation source/asset baseline changed')
            reference.require(set(row['input']['baseline_package_versions'])==set(PACKAGES) and
                row['input']['baseline_package_versions']['fastembed']=='0.8.0','package metadata baseline changed')
            if identifier.endswith('/fastembed-version'):
                reference.require(change==dict(kind='package-version-metadata-double',package='fastembed',
                    before='0.8.0',after='0.0.0-s01b-control') and after['identities'] is None and
                    after['exception']==dict(type='ValueError',message='worker requires fastembed==0.8.0') and
                    after['external_constructor_stub_calls']==[],'unsupported FastEmbed version did not actually refuse')
            else:
                reference.require(after['exception'] is None and len(after['external_constructor_stub_calls'])==2,
                                  'external constructor stub scope changed')
                previous=before['identities'];current=after['identities']
                if '/path-move-stable/' in identifier or '/timestamp-stable/' in identifier:
                    reference.require(previous==current and row['steps'] and
                        all(s['result']['identities']==previous and s['result']['exception'] is None for s in row['steps']),
                        'path/timestamp changed identity')
                    if '/timestamp-stable/' in identifier:
                        reference.require(len(row['steps'])==7 and len({s['file'] for s in row['steps']})==7 and
                            all(s['before_sha256']==s['after_sha256'] and s['before_mtime_ns']!=s['after_mtime_ns']
                                for s in row['steps']),'timestamp control did not mutate each copied input separately')
                    else:
                        reference.require({s['input'] for s in row['steps']}=={'source-path','model-path'} and
                            change['moved_files']==row['input']['baseline_files'],'path move changed bytes')
                elif '/each-implementation-source/' in identifier:
                    reference.require(previous['rank']==current['rank'] and previous['embed']!=current['embed'],
                                      'rank/embed source derivations were conflated')
                else:
                    reference.require(previous['rank']!=current['rank'] and previous['embed']!=current['embed'],
                                      'consumed input did not change affected derivation')
            if change['kind'] in ('one-byte-asset','one-source-comment'):
                reference.require([name for name in change['before'] if change['before'][name]!=change['after'][name]]==[change['file']],
                                  'identity control changed more than one input')
                first=reference.pinned(root/'identity-inputs',change['retained_before'],change['before'][change['file']])
                second=reference.pinned(root/'identity-inputs',change['retained_after'],change['after'][change['file']])
                original,mutated=first.read_bytes(),second.read_bytes()
                reference.require((len(original)==len(mutated) and mutated==bytes([original[0]^1])+original[1:])
                    if change['kind']=='one-byte-asset' else mutated==original+b'\n# S01b disposable identity input mutation.\n',
                    'identity mutation bytes do not match the declared single input')
        result[identifier]=dict(id=identifier,scope=row['scope'],freshness='new residual function capture',
            function_observation=row,disposition=disposition,complete=True)
    return result


def collect(args,value):
    reference.verify_runtime(value,args.runtime_binding,args.site,args.python_home)
    reference.verify_assets(value,args.assets)
    output=args.output.absolute();reference.require(not output.exists(),'function output must be fresh')
    tools=reference.tool_pins()
    argv=[str(args.python_home/'bin/python3.11'),'-I','-S','-B','-c',DRIVER,
          str(reference.PROJECT/'scripts'),str(args.site),str(output),str(args.assets)]
    with reference.SemanticIsolation(read_only_paths=[reference.PROJECT,args.python_home,args.site,args.assets],output_dir=output) as iso:
        done=iso.run(argv,env=dict(reference.WORKER_ENV),timeout=180,stdout_limit=4194304,role=ROLE,mode=MODE)
        (output/'fixture.stdout').write_bytes(done.stdout);(output/'fixture.stderr').write_bytes(done.stderr)
        reference.save(output/'result.json',dict(exit_code=done.returncode,invocation=done.invocation))
    reference.require(done.returncode==0,'function residual producer failed')
    reference.require(tools==reference.tool_pins(),'function producer changed during execution')
    shutil.copyfile(args.runtime_binding,output/'runtime-binding.json')
    report=dict(schema='cairn.semantic-function-refusals/1',ledger_sha256=value['failure_ledger']['sha256'],
        sources=value['source_sha256'],tool_sha256=tools,python_sha256=value['python_sha256'],
        runtime_binding_sha256=value['retained']['runtime-binding']['sha256'],model_execution=False,
        files_sha256={str(p.relative_to(output)):reference.digest(p) for p in output.rglob('*')
                      if p.is_file() and not p.is_symlink()},
        parameters=dict(project=str(reference.PROJECT),python_home=str(args.python_home),site=str(args.site),assets=str(args.assets)))
    reference.save(output/'function-report.json',report)
    return report


def verify(value,evidence_root,path,sha):
    reference.require(sha and reference.digest(path)==sha,'pinned function report required')
    report=reference.load(path)
    reference.require(set(report)=={'schema','ledger_sha256','sources','tool_sha256','python_sha256',
        'runtime_binding_sha256','model_execution','files_sha256','parameters'} and
        report['schema']=='cairn.semantic-function-refusals/1' and report['model_execution'] is False,
        'unsupported function/model acceptance claim')
    reference.require(report['ledger_sha256']==value['failure_ledger']['sha256'] and
        report['sources']==value['source_sha256'] and report['tool_sha256']==reference.tool_pins() and
        report['python_sha256']==value['python_sha256'] and
        report['runtime_binding_sha256']==value['retained']['runtime-binding']['sha256'],'function source/runtime/tool binding changed')
    for rel,pin in report['files_sha256'].items():reference.pinned(path.parent,rel,pin)
    required={'fixture.stdout','fixture.stderr','result.json','cleanup.json','isolation-proof.json','runtime-observations.json','command-0002.json'}
    reference.require(required<=set(report['files_sha256']),'unsealed function disposition artifact')
    data=reference.load(path.parent/'fixture.stdout');result=reference.load(path.parent/'result.json')
    cleanup=reference.load(path.parent/'cleanup.json');proof=reference.load(path.parent/'isolation-proof.json')
    reference.require(set(data)=={'schema','cases','model_execution'} and data['schema']=='cairn.semantic-function-observations/1' and
        data['model_execution'] is False and result['exit_code']==0,'function producer did not complete its declared fixture')
    invocation=result['invocation'];params=report['parameters']
    expected=[str(Path(params['python_home'])/'bin/python3.11'),'-I','-S','-B','-c',DRIVER,
              str(Path(params['project'])/'scripts'),params['site'],str(path.parent),params['assets']]
    reference.require(invocation['role']==ROLE and invocation['mode']==MODE and type(invocation['pid']) is int and invocation['pid']>1 and
        invocation['argv'][invocation['argv'].index('--')+1:]==expected and
        invocation['contract']['environment']==dict(reference.WORKER_ENV,PWD='/') and
        invocation['declaration_sha256']==reference.digest(path.parent/'command-0002.json') and
        cleanup['invocations'][1:]==[invocation] and cleanup['launcher_exit_codes']==[0,0] and
        cleanup['all_observed_processes_exited'] is True and cleanup['errors']==[] and cleanup['backing_removed'] is True and
        cleanup['isolation_proof_sha256']==reference.digest(path.parent/'isolation-proof.json') and
        proof['wrapper_sha256']==value['source_sha256']['scripts/semantic_acceptance_isolation.py'] and
        proof['profile']=='default' and all(m['read_only'] for m in proof['read_only_mounts']), 'function lifecycle/isolation binding changed')
    runtime=reference.load(path.parent/'runtime-observations.json')
    workers=[w for w in runtime['processes'] if w['launcher_pid']==invocation['pid'] and
        w['launcher_start_ticks']==invocation['start_ticks'] and w['executable_sha256']==value['python_sha256'] and
        any('numpy/_core/_multiarray_umath' in lib['path'] for lib in w['libraries'])]
    reference.require(len(workers)==1 and runtime['errors']==[] and runtime['model_workers']==[] and
        workers[0]['environment_matches_contract'] is True,'actual NumPy fixture binding missing or model scope crossed')
    binding=reference.load(reference.pinned(path.parent,'runtime-binding.json',report['runtime_binding_sha256']))
    libraries={(str(Path(params['site'])/row['relative']),row['sha256']) for row in binding['frozen_numeric_native_files']
               if row['relative'].startswith(('numpy/','numpy.libs/'))}
    reference.require(libraries<={(row['path'],row['sha256']) for row in workers[0]['libraries']},
                      'actual function numeric runtime differs from frozen libraries')
    pin=value['retained']['python-stream34'];rank=reference.pinned(evidence_root,pin['relative'],pin['sha256'])
    rank_identity=json.loads(rank.read_bytes().splitlines()[0])['result']['model_sha256']
    base=Path(value['retained']['python-characterization']['relative']).parent
    embed=reference.load(Path(evidence_root)/base/'raw/embed-identity.stdout')['identity']
    # The exact historical file is also verified through the characterization seal by audit().
    return validate_rows(value,path.parent,data['cases'],rank_identity,embed)
