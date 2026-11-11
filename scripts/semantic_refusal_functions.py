"""Fixed remaining source-function controls with explicitly labelled doubles."""
import hashlib
from types import SimpleNamespace

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
