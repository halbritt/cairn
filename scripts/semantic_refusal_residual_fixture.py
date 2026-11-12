"""Explicit Scorer/protocol doubles for otherwise unreachable output and idle guards."""
import hashlib
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import semantic_rank


def encoded(value):
    return json.dumps(value,allow_nan=False).encode()


def cap_payload(kind,edge):
    response=dict(id='cap',result=dict(padding=''))
    ordinary_target=65536+(edge=='over' and kind=='ordinary')
    response['result']['padding']='x'*(ordinary_target-len(encoded(response))-1)
    snapshot=dict(padding='')
    if kind=='combined':
        combined=dict(response,cache=snapshot)
        target=semantic_rank.CACHE_LIMIT+65536+(edge=='over')
        snapshot['padding']='y'*(target-len(encoded(combined))-1)
    return response['result'],snapshot


def caps(kind,edge,sidecar):
    if kind not in ('ordinary','combined') or edge not in ('boundary','over'):
        raise ValueError('unknown fixed protocol guard')
    result,snapshot=cap_payload(kind,edge)
    calls=[]

    class ScorerDouble:
        def __init__(self,model_dir):calls.append(dict(method='constructor',model_dir=str(model_dir)))
        def restore(self,state):calls.append(dict(method='restore',state=state))
        def score(self,request):
            calls.append(dict(method='score',request=request))
            return result
        def snapshot(self):
            calls.append(dict(method='snapshot'))
            return snapshot

    observation=dict(scope='Python/actual-serve_stream/Scorer-double',model_initialized=False,real_model_inference=False,
        kind=kind,edge=edge,calls=calls,source_sha256=hashlib.sha256(Path(semantic_rank.__file__).read_bytes()).hexdigest(),
        ordinary_bytes=len(encoded(dict(id='cap',result=result)))+1,
        snapshot_bytes=len(encoded(snapshot)),combined_bytes=len(encoded(dict(id='cap',result=result,cache=snapshot)))+1)
    try:
        with patch.object(semantic_rank,'Scorer',ScorerDouble):
            semantic_rank.serve_stream(Path('/nonexistent-protocol-double-model'),sys.stdin.buffer,sys.stdout.buffer,
                                       cache_enabled=kind=='combined')
    finally:
        Path(sidecar).write_bytes(encoded(observation)+b'\n')


def idle(events):
    def record(event,**fields):
        with Path(events).open('a') as stream:
            stream.write(json.dumps(dict(event=event,pid=os.getpid(),**fields))+'\n')
    record('start',start_ticks=Path('/proc/self/stat').read_text().rsplit(') ',1)[1].split()[19],
           pgrp=os.getpgrp(),parent=os.getppid(),stdin_pipe=os.readlink('/proc/self/fd/0'),
           stdout_pipe=os.readlink('/proc/self/fd/1'))
    for number,line in enumerate(sys.stdin.buffer,1):
        request=json.loads(line)
        record('request',frame=number,cache='cache' in request,request=request)
        reply=dict(id=request['id'],result=dict(model_sha256='a'*64,algorithm='s01b-idle-double/1',scores=[]),
                   cache=dict(fixture='state-'+request['id']))
        sys.stdout.buffer.write(encoded(reply)+b'\n');sys.stdout.buffer.flush()


if __name__=='__main__':
    if len(sys.argv)==5 and sys.argv[1]=='caps':caps(*sys.argv[2:])
    elif len(sys.argv)==3 and sys.argv[1]=='idle':idle(sys.argv[2])
    else:raise SystemExit('expected fixed caps or idle fixture mode')
