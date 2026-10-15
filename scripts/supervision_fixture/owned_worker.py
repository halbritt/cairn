"""Fixed disposable workers for the internal boundary. No provider/actor argv API."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
from trial_worker_protocol import decode_end


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def selected(name,data):
    fd=os.open('/output/'+name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        offset=0
        while offset<len(data):
            offset+=os.write(fd,data[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)
    fd=os.open('/output',os.O_RDONLY|os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def line(value):
    sys.stdout.buffer.write(encoded(value)+b'\n');sys.stdout.buffer.flush()


def exact(count):
    data=bytearray()
    while len(data)<count:
        block=sys.stdin.buffer.read(count-len(data))
        if not block:
            raise ValueError('fixture_truncated')
        data.extend(block)
    return bytes(data)


def capture(mode,run):
    if exact(1)!=b'G':
        raise ValueError('fixture_gate')
    data=sys.stdin.buffer.read(8501)
    if len(data)>8500:
        raise ValueError('fixture_input_bounds')
    read,write=os.pipe()
    pid=os.fork()
    if pid==0:
        os.close(write)
        while os.read(read,4096):
            pass
        os.close(read)
        if mode=='descendant':
            if os.fork()==0:
                os.setsid();time.sleep(1000);os._exit(0)
        if mode=='backpressure':
            payload=b'x'*(1024*1024)
        else:
            payload=b'partial disposable work\n' if mode!='stream' else b'x'*(256*1024)
        while payload:
            payload=payload[os.write(1,payload):]
        if mode=='signal':
            os.kill(os.getpid(),15)
        os._exit(17 if mode in ('nonzero','missing','descendant') else 0)
    os.close(read)
    start=int(Path('/proc/'+str(pid)+'/stat').read_text().rsplit(') ',1)[1].split()[19])
    written=0
    while written<len(data):
        written+=os.write(write,data[written:])
    os.close(write)
    _,raw=os.waitpid(pid,0)
    if mode=='missing':
        return 36
    fact=dict(pid=pid,start_ticks=start,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              raw_status=raw,returncode=os.waitstatus_to_exitcode(raw))
    sys.stderr.buffer.write(encoded(dict(kind='ACTOR_WAIT',run_id=run,actor_wait=fact,
        input_bytes=len(data),input_bytes_written=written,observations=[]))+b'\n')
    sys.stderr.buffer.flush()
    return 0


def composition(mode,run,source,intent):
    intent_bytes=encoded(dict(run_id=run,source_manifest_sha256=source))
    if hashlib.sha256(intent_bytes).hexdigest()!=intent:
        raise ValueError('fixture_intent_binding')
    selected('INTENT.json',intent_bytes)
    line(dict(kind='READY',run_id=run,intent_sha256=intent))
    if mode=='hang':
        time.sleep(1000)
    if mode=='overflow':
        os.write(2,b'x'*9000);time.sleep(1000)
    digest=hashlib.sha256();count=0;end=None
    while True:
        tag=sys.stdin.buffer.read(1)
        if not tag:
            break
        length=struct.unpack('!I',exact(4))[0]
        if length>65536:
            raise ValueError('fixture_packet_bounds')
        payload=exact(length)
        if end is not None:
            raise ValueError('fixture_end_order')
        if tag==b'D':
            digest.update(payload);count+=len(payload)
            if count>4*1024*1024:
                raise ValueError('fixture_stream_bounds')
        elif tag==b'E':
            end=decode_end(payload,run_id=run,source_manifest_sha256=source)
            if (end['run_id']!=run or end['source_manifest_sha256']!=source or
                end['stream_sha256']!=digest.hexdigest() or end['stream_bytes']!=count):
                raise ValueError('fixture_end_binding')
        else:
            raise ValueError('fixture_packet_tag')
    if end is None:
        raise ValueError('fixture_end_missing')
    # This fixture selects only metadata, never the transient D stream. It does
    # not implement or attest the actual coding profile or success retention.
    data=encoded(dict(schema='cairn.disposable-failure-observation/1',end=end))
    selected('FAILURE.json',data)
    if mode=='lost':
        return 0
    line(dict(kind='CLOSED',run_id=run,intent_sha256=intent,outcome='failure_evidence',
              manifest_sha256=hashlib.sha256(data).hexdigest(),selected_bytes=len(data)))
    return 29 if mode=='nonzero' else 0


def auxiliary(role,mode):
    data=sys.stdin.buffer.read(65537)
    if len(data)>65536:
        raise ValueError('fixture_aux_bounds')
    if mode=='hang':
        time.sleep(1000)
    if mode=='pids':
        children=[]
        for _ in range(20):
            try:
                pid=os.fork()
            except BlockingIOError:
                break
            if pid==0:
                time.sleep(.25);os._exit(0)
            children.append(pid)
        data=encoded(dict(requested=20,forked=len(children)))
        for pid in children:
            os.waitpid(pid,0)
    selected(role+'.json',data)
    line(dict(kind='AUX_CLOSED',role=role,sha256=hashlib.sha256(data).hexdigest()))
    return 0


if __name__=='__main__':
    role,mode,run,source,intent=sys.argv[1:]
    if role=='capture' and mode in ('normal','nonzero','missing','descendant','stream','backpressure','signal'):
        code=capture(mode,run)
    elif role=='composition' and mode in ('normal','hang','lost','nonzero','overflow'):
        code=composition(mode,run,source,intent)
    elif role in ('diagnostic','export') and mode in ('normal','hang','pids'):
        code=auxiliary(role,mode)
    else:
        raise ValueError('fixture_fixed_worker')
    sys.exit(code)
