"""Disposable protocol/process double. Never shipped as a worker launcher."""
import json
import os
from pathlib import Path
import sys
import time


def run(case, events):
    def record(event, **fields):
        with Path(events).open('a') as stream:
            stream.write(json.dumps(dict(event=event, pid=os.getpid(), **fields)) + '\n')

    record('start', start_ticks=Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19],
           pgrp=os.getpgrp(), parent=os.getppid(), stdin_pipe=os.readlink('/proc/self/fd/0'),
           stdout_pipe=os.readlink('/proc/self/fd/1'))
    if case.startswith('rank-start-busy'):
        sys.stdin.buffer.read()
        os.write(1,json.dumps(dict(model_sha256='fixture',algorithm='fixture/1',scores=[])).encode())
        return
    if case in ('stream-blocked-input','rank-blocked-input'):
        time.sleep(10)
        return
    if case=='rank-blocked-output':
        sys.stdin.buffer.read()
        time.sleep(10)
        return
    if case in ('rank-early-exit', 'stream-early-exit'):
        raise SystemExit(5)
    if case in ('rank-descendant', 'stream-descendant'):
        child = os.fork()
        if child == 0:
            record('descendant', parent=os.getppid(), start_ticks=Path('/proc/self/stat').read_text().rsplit(') ', 1)[1].split()[19])
            time.sleep(10)
            os._exit(0)
        record('fork', child=child)
        os._exit(0)
    if case.startswith('rank-output'):
        size = 65536 if case.endswith('boundary') else 65537
        reply = dict(model_sha256='fixture', algorithm='', scores=[])
        raw = json.dumps(reply, separators=(',', ':')).encode()
        reply['algorithm'] = 'x' * (size - len(raw))
        os.write(1, json.dumps(reply, separators=(',', ':')).encode())
        return
    frame = 0
    for line in sys.stdin.buffer:
        request = json.loads(line)
        frame += 1
        record('request', frame=frame, cache='cache' in request, operation=request.get('operation'),request=request)
        if case in ('stream-blocked-output', 'stream-owner-close', 'stream-busy'):
            record('stream-blocked')
            time.sleep(10)
            return
        if case.startswith('embed-'):
            if request['operation'] == 'document' and case in ('embed-concurrency','embed-busy','embed-owner-close','embed-earlier-deadline'):
                record('document-blocked')
                time.sleep(10)
                return
            if request['text'] == 'block':
                record('query-blocked')
                time.sleep(10)
                return
            starts = [json.loads(x) for x in Path(events).read_text().splitlines() if json.loads(x)['event'] == 'start']
            identity = dict(model_sha256=('b' if case == 'embed-restart-identity' and len(starts)>1 else 'a')*64,
                            algorithm='bge-original-passages/1', dimensions=384)
            reply = dict(id=request['id'], identity=identity, vector=[1.0]*384)
            if request['operation']=='document':
                reply=dict(id=request['id'],identity=identity,passages=[dict(span=dict(offset=0,length=4),vector=[1.0]*384)])
            if case.startswith('embed-projection-'):
                import hashlib
                projection=dict(method='original-prefix/1',truncated=False,original_tokens=4,embedded_tokens=4,
                                prefix_bytes=5,prefix_sha256=hashlib.sha256(b'query').hexdigest())
                if case.endswith('method'):projection['method']='wrong'
                elif case.endswith('hash'):projection['prefix_sha256']='wrong'
                elif case.endswith('byte-length'):projection['prefix_bytes']=6
                elif case.endswith('mid-codepoint'):projection['prefix_bytes']=1
                elif case.endswith('token-counts'):projection['embedded_tokens']=513
                reply['query_projection']=projection
            if case == 'embed-output-overflow':
                raw = b'{"extra":"' + b'x'*(4*1024*1024+1) + b'"}\n'
                os.write(1, raw)
                return
        else:
            result = dict(model_sha256='a'*64, algorithm='fixture/1', scores=[])
            starts=len([x for x in Path(events).read_text().splitlines() if json.loads(x)['event']=='start'])
            if case=='stream-new-child-identity' and starts>1:result['model_sha256']='b'*64
            if case == 'stream-identity-model' and frame == 2: result['model_sha256']='b'*64
            if case == 'stream-identity-algorithm' and frame == 2: result['algorithm']='fixture/2'
            reply = dict(id=request['id'], result=result)
            if case == 'stream-wrong-id': reply['id']='wrong'
            if case == 'stream-missing-id': del reply['id']
            if case == 'stream-missing-result': del reply['result']
            if case == 'stream-null-result': reply['result']=None
            if case == 'stream-failure-restart' and starts == 1 and frame==2:
                raise SystemExit(5)
            if case in ('stream-failure-restart','stream-close-reopen'):reply['cache']={'fixture':'snapshot'}
            if case in ('stream-cache-omission','stream-cache-null'):
                if frame == 1: reply['cache']={'fixture':'snapshot'}
                elif case == 'stream-cache-null': reply['cache']=None
            if case == 'stream-cache-overflow': reply['cache']='x'*(600*1024)
            if case == 'stream-output-overflow': result['algorithm']='x'*65536
        raw=json.dumps(reply,separators=(',',':')).encode()+b'\n'
        if case == 'stream-missing-newline': raw=raw[:-1]
        if case == 'stream-trailing-json': raw=raw[:-1]+b'{}\n'
        if case == 'stream-trailing-line': raw+=b'{}\n'
        os.write(1,raw)
        if case == 'stream-missing-newline': return


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
