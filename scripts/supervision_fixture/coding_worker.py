"""Fixed disposable coding transport peers, not root's actual coding worker."""
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
from fixture_base import encoded,selected,line,exact
from trial_coding_protocol import CodingDecoder


def capture(mode,run,source):
    if exact(1)!=b'G':
        raise ValueError('fixture_gate')
    data=sys.stdin.buffer.read(8501)
    if len(data)>8500:
        raise ValueError('fixture_input')
    origin=time.monotonic_ns()
    start=dict(kind='CAPTURE_START',schema='cairn.worker-capture-start/1',run_id=run,
               source_manifest_sha256=source,start_monotonic_ns=origin)
    if mode=='future':
        start['start_monotonic_ns']=origin+60*10**9
    if mode=='malformed':
        start['private']='fixture extra payload'
    if mode=='startup_hang':
        # Fill a real raw pipe without any start record. Controller must retain
        # backpressure and stop safely rather than manufacture an origin.
        while True:
            os.write(1,b'x'*65536)
    if mode!='missing':
        sys.stderr.buffer.write(encoded(start)+b'\n');sys.stderr.buffer.flush()
    read,write=os.pipe();pid=os.fork()
    if pid==0:
        os.close(write)
        while os.read(read,4096):
            pass
        os.close(read)
        if mode=='delayed':
            time.sleep(.04)
        payload=b'x'*(256*1024) if mode=='large' else b'coding disposable work\n'
        if mode=='fragments':
            for byte in payload:
                os.write(1,bytes([byte]));time.sleep(.003)
        else:
            os.write(1,payload)
        os._exit(17 if mode=='nonzero' else 0)
    os.close(read)
    ticks=int(Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19])
    written=0
    while written<len(data):
        written+=os.write(write,data[written:])
    os.close(write);_,raw=os.waitpid(pid,0)
    fact=dict(pid=pid,start_ticks=ticks,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              raw_status=raw,returncode=os.waitstatus_to_exitcode(raw))
    if mode!='lost_final':
        sys.stderr.buffer.write(encoded(dict(kind='ACTOR_WAIT',run_id=run,actor_wait=fact,
            input_bytes=len(data),input_bytes_written=written,observations=[]))+b'\n');sys.stderr.buffer.flush()
    return 0


def sink(mode,run,source,intent):
    selected('INTENT.json',encoded(dict(run_id=run,source_manifest_sha256=source)))
    line(dict(kind='READY',run_id=run,intent_sha256=intent))
    if mode=='slow':
        time.sleep(.2)
    if mode=='hang':
        time.sleep(1000)
    decoder=CodingDecoder(run,source);count=0;first_elapsed=None;last_elapsed=None
    first_sink_elapsed=None
    while True:
        tag=sys.stdin.buffer.read(1)
        if not tag:
            break
        length=struct.unpack('!I',exact(4))[0]
        if length>65536:
            raise ValueError('fixture_packet_bounds')
        event=decoder.feed(tag,exact(length))
        if event[0]=='data':
            count+=1
            if count==1 and decoder.origin is not None:
                first_sink_elapsed=(time.monotonic_ns()-decoder.origin)/1e9
            if first_elapsed is None:
                first_elapsed=event[2]
            last_elapsed=event[2]
    if decoder.end is None:
        raise ValueError('fixture_end_missing')
    # Never retain the D native bytes or hook/private raw output.
    data=encoded(dict(end=decoder.end,hooks=decoder.hooks,packets=count,
                      first_elapsed=first_elapsed,last_elapsed=last_elapsed,
                      first_sink_elapsed=first_sink_elapsed))
    selected('CODING-FAILURE.json',data)
    line(dict(kind='CLOSED',run_id=run,intent_sha256=intent,outcome='failure_evidence',
              manifest_sha256=hashlib.sha256(data).hexdigest(),selected_bytes=len(data)))
    return 0


if __name__=='__main__':
    role,mode,run,source,intent=sys.argv[1:]
    if role=='capture' and mode in ('normal','nonzero','fragments','delayed','future','malformed','missing','large','startup_hang','lost_final'):
        result=capture(mode,run,source)
    elif role=='composition' and mode in ('normal','slow','hang'):
        result=sink(mode,run,source,intent)
    else:
        raise ValueError('fixed_fixture_role')
    sys.exit(result)
