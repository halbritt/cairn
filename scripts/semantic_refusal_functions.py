"""Fixed remaining source-function controls with explicitly labelled doubles."""
import hashlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
from unittest.mock import patch

import semantic_rank
import embedding_worker


def cache_hashes(cache):
    return {key:hashlib.sha256(vector.astype('<f4',copy=False).tobytes()).hexdigest()
            for key,vector in sorted(cache.items())}


def rank_and_retreat():
    import numpy as np

    class TokenizerDouble:
        def __init__(self,overflow=False):
            self.overflow=overflow
            self.calls=0

        def encode(self,text,**kwargs):
            self.calls+=1
            return SimpleNamespace(ids=list(range(513 if text=='decoded-overflow' else 1)))

        def decode(self,tokens,**kwargs):
            return 'decoded-overflow' if self.overflow else 'body'

    class ModelDouble:
        def __init__(self,pattern):
            self.pattern=pattern
            self.calls=[]
            self.vectors=None

        def embed(self,texts,batch_size):
            texts=list(texts)
            self.calls.append(dict(texts=texts,batch_size=batch_size))
            self.vectors=np.ones((len(texts),384),dtype=np.float32)
            if self.pattern=='zero':
                self.vectors[:]=0
            elif self.pattern!='finite':
                self.vectors[0,0]={'NaN':np.nan,'positive-infinity':np.inf,'negative-infinity':-np.inf}[self.pattern]
            return self.vectors

    request=dict(query='query',notes=[dict(record_id='fixture-record',version=1,body='body',
                  body_sha256=hashlib.sha256(b'body').hexdigest())])
    cases=[('F-RANK/decoded-chunk-token-overflow/boundary','finite',True)]
    cases.extend(('F-RANK/nonfinite-inference/'+name,name,False)
                 for name in ('NaN','positive-infinity','negative-infinity'))
    cases.append(('F-RANK/zero-inference/boundary','zero',False))
    rows=[]
    for identifier,pattern,overflow in cases:
        scorer=semantic_rank.Scorer.__new__(semantic_rank.Scorer)
        scorer.np=np
        scorer.model_hash='source-function-double'
        scorer.tokenizer=TokenizerDouble(overflow)
        scorer.model=ModelDouble(pattern)
        scorer.chunk_vectors={hashlib.sha256(b'previous').hexdigest():np.ones(384,dtype=np.float32)}
        before=cache_hashes(scorer.chunk_vectors)
        result=None;error=None
        try:
            result=scorer.score(request)
        except ValueError as exception:
            error=dict(type=type(exception).__name__,message=str(exception))
        rows.append(dict(id=identifier,input=dict(request=request,model_pattern=pattern,decoded_overflow=overflow),
            scope='function/actual-NumPy/tokenizer-model-doubles',result=result,exception=error,
            model_initialized=False,real_model_inference=False,model_double_calls=len(scorer.model.calls),
            model_double_inputs=scorer.model.calls,tokenizer_double_calls=scorer.tokenizer.calls,
            cache_before=before,cache_after=cache_hashes(scorer.chunk_vectors)))

    original='q'*100

    class RetreatTokenizerDouble:
        def __init__(self):
            self.calls=[]

        def encode(self,text,**kwargs):
            count=600 if text==semantic_rank.PREFIX+original else 513
            self.calls.append(dict(text=text,tokens=count))
            return SimpleNamespace(ids=list(range(count)),offsets=[(0,len(semantic_rank.PREFIX)+10)]*count)

    tokenizer=RetreatTokenizerDouble()
    worker=embedding_worker.Embeddings.__new__(embedding_worker.Embeddings)
    worker.scorer=SimpleNamespace(tokenizer=tokenizer)
    worker.identity={}
    calls=[]

    def forbidden_inference(texts):
        calls.append(texts)
        raise AssertionError('retreat refusal attempted inference')

    worker.vectors=forbidden_inference
    request=dict(id='retreat',operation='query',text=original)
    result=None;error=None
    try:
        result=worker.respond(request)
    except ValueError as exception:
        error=dict(type=type(exception).__name__,message=str(exception))
    rows.append(dict(id='F-EMBED/long-query-retreat-refusal/boundary',input=request,
        scope='function/tokenizer-double',result=result,exception=error,model_initialized=False,
        real_model_inference=False,model_double_calls=len(calls),tokenizer_double_calls=len(tokenizer.calls),
        tokenizer_double_inputs=tokenizer.calls,cache_before=None,cache_after=None))
    return rows


ASSETS=('model_optimized.onnx','config.json','tokenizer.json','special_tokens_map.json','tokenizer_config.json')
PACKAGES=('fastembed','onnxruntime','numpy','tokenizers')


def file_hashes(root,names):
    return {name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in names}


def identity_cases(output,assets):
    """Run the actual constructors' derivations, bypassing external model loading."""
    import numpy  # Load the real numeric package before installing external-package doubles.
    output=Path(output);output.mkdir()
    source=Path(__file__).parent
    originals=output/'original';originals.mkdir()
    original_code=originals/'code';original_code.mkdir()
    original_assets=originals/'assets';original_assets.mkdir()
    for name in ('semantic_rank.py','embedding_worker.py'):
        shutil.copy2(source/name,original_code/name)
    for name in ASSETS:shutil.copy2(Path(assets)/name,original_assets/name)
    baseline_files=dict(assets=file_hashes(original_assets,ASSETS),
                        code=file_hashes(original_code,('semantic_rank.py','embedding_worker.py')))
    versions={name:importlib.metadata.version(name) for name in PACKAGES}
    assert versions['fastembed']=='0.8.0','identity controls require the frozen metadata baseline'
    work=output/'work';work.mkdir()
    code=work/'code';shutil.copytree(original_code,code)
    models=work/'assets';shutil.copytree(original_assets,models)
    for path in models.iterdir():path.chmod(0o600)

    class TokenizerStub:
        def no_truncation(self):pass
        def no_padding(self):pass

    calls=[]

    def external_constructor(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model=SimpleNamespace(tokenizer=SimpleNamespace(to_str=lambda:'tokenizer-stub')))

    def pair(code_root,asset_root,version_changes=None,settings=None):
        declared=dict(versions,**(version_changes or {}))
        modules={name:sys.modules.get(name) for name in ('semantic_rank','embedding_worker')}
        start=len(calls)
        result=None;error=None
        try:
            with patch.dict(sys.modules,{'fastembed':SimpleNamespace(TextEmbedding=external_constructor),
                    'tokenizers':SimpleNamespace(Tokenizer=SimpleNamespace(from_str=lambda raw:TokenizerStub()))}):
                loaded=[]
                for name in ('semantic_rank','embedding_worker'):
                    spec=importlib.util.spec_from_file_location(name,code_root/(name+'.py'))
                    module=importlib.util.module_from_spec(spec);sys.modules[name]=module
                    spec.loader.exec_module(module);loaded.append(module)
                rank,embed=loaded
                for name,value in (settings or {}).items():setattr(rank,name,value)
                with patch.object(importlib.metadata,'version',side_effect=lambda name:declared[name]):
                    rank_value=rank.Scorer(asset_root).model_hash
                    embed_value=embed.Embeddings(asset_root).identity
                result=dict(rank=rank_value,embed=embed_value)
        except ValueError as exception:
            error=dict(type=type(exception).__name__,message=str(exception))
        finally:
            for name,module in modules.items():
                if module is None:sys.modules.pop(name,None)
                else:sys.modules[name]=module
        return dict(identities=result,exception=error,external_constructor_stub_calls=calls[start:])

    baseline=pair(code,models)
    assert baseline['exception'] is None
    rows=[]

    def row(identifier,changed,after,steps=None):
        rows.append(dict(id=identifier,scope='function/source-derivation/external-model-tokenizer-stubs',
            input=dict(baseline_files=baseline_files,baseline_package_versions=versions,mutation=changed),
            before=baseline,after=after,steps=steps or [],model_initialized=False,real_model_inference=False))

    deltas=output/'deltas';deltas.mkdir()
    for name in ASSETS:
        path=models/name
        before=file_hashes(models,ASSETS)
        with path.open('r+b') as stream:
            first=stream.read(1);assert first
            stream.seek(0);stream.write(bytes([first[0]^1]))
        after_bytes=file_hashes(models,ASSETS)
        saved=deltas/(name+'.after');shutil.copy2(path,saved)
        after=pair(code,models)
        row('F-IDENTITY/each-model-asset/'+name,dict(kind='one-byte-asset',file=name,offset=0,
            before=before,after=after_bytes,retained_before=str((original_assets/name).relative_to(output)),
            retained_after=str(saved.relative_to(output))),after)
        shutil.copy2(original_assets/name,path)
    for name in ('semantic_rank.py','embedding_worker.py'):
        path=code/name
        before=file_hashes(code,('semantic_rank.py','embedding_worker.py'))
        path.write_bytes(path.read_bytes()+b'\n# S01b disposable identity input mutation.\n')
        after_bytes=file_hashes(code,('semantic_rank.py','embedding_worker.py'))
        saved=deltas/(name+'.after');shutil.copy2(path,saved)
        row('F-IDENTITY/each-implementation-source/'+name,dict(kind='one-source-comment',file=name,
            before=before,after=after_bytes,retained_before=str((original_code/name).relative_to(output)),
            retained_after=str(saved.relative_to(output))),pair(code,models))
        shutil.copy2(original_code/name,path)
    for name in PACKAGES:
        mutated='0.0.0-s01b-control'
        row('F-IDENTITY/each-vector-setting-version/'+name+'-version',
            dict(kind='package-version-metadata-double',package=name,before=versions[name],after=mutated),
            pair(code,models,version_changes={name:mutated}))
    for label,name,value in [('prefix','PREFIX',semantic_rank.PREFIX+'control '),
                             ('window','WINDOW',semantic_rank.WINDOW+1),('stride','STRIDE',semantic_rank.STRIDE+1),
                             ('algorithm','ALGORITHM',semantic_rank.ALGORITHM+'/control'),
                             ('batch-size','BATCH_SIZE',semantic_rank.BATCH_SIZE+1)]:
        row('F-IDENTITY/each-vector-setting-version/'+label,
            dict(kind='consumed-rank-setting-double',symbol=name,before=getattr(semantic_rank,name),after=value),
            pair(code,models,settings={name:value}))
    moved=output/'moved';moved.mkdir()
    shutil.copytree(original_code,moved/'code');shutil.copytree(original_assets,moved/'assets')
    moves=[dict(input='source-path',result=pair(moved/'code',models)),
           dict(input='model-path',result=pair(code,moved/'assets'))]
    row('F-IDENTITY/path-move-stable/boundary',dict(kind='byte-identical-path-moves',
        moved_files=dict(code=file_hashes(moved/'code',('semantic_rank.py','embedding_worker.py')),
                         assets=file_hashes(moved/'assets',ASSETS))),moves[-1]['result'],moves)
    steps=[]
    for directory,names in ((code,('semantic_rank.py','embedding_worker.py')),(models,ASSETS)):
        for name in names:
            path=directory/name;stat=path.stat();before=hashlib.sha256(path.read_bytes()).hexdigest()
            os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns+123456789))
            result=pair(code,models)
            steps.append(dict(file=str(path.relative_to(work)),before_sha256=before,
                after_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),before_mtime_ns=stat.st_mtime_ns,
                after_mtime_ns=path.stat().st_mtime_ns,result=result))
            os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    row('F-IDENTITY/timestamp-stable/boundary',dict(kind='one-mtime-at-a-time'),steps[-1]['result'],steps)
    assert file_hashes(original_assets,ASSETS)==baseline_files['assets']
    assert file_hashes(original_code,('semantic_rank.py','embedding_worker.py'))==baseline_files['code']
    return rows
