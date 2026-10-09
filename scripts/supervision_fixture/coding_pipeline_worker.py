"""Fixed synthetic actor/capture and existing root sink; no native bootstrap.

SDK-shaped records and hook/account counters are constructed parser fixtures.
Only process identity, waitpid, input delivery and capture origin are real here.
"""
import json
import os
from pathlib import Path
import sys
import time

from supervision_fixture.coding_sink_worker import main as sink_main
from trial_coding_stream import coding_profile
from trial_worker_protocol import encoded


INPUT_MESSAGE=(json.dumps(dict(type='user',message=dict(role='user',content='new task')),
                          ensure_ascii=False)+'\n').encode()


def synthetic_records(run,failed):
    """One fixed T1 format sample, not task work or native/provider testimony."""
    profile=coding_profile('T1')
    values=[dict(type='system',subtype='init',session_id=run,model='fixture-model',
                 claude_code_version='fixture-cli',permissionMode='dontAsk',
                 tools=sorted(profile.init_tools),mcp_servers=[dict(name='cairn',status='connected')]),
            dict(type='user',session_id=run,message=dict(content='new task'))]
    for index,event in enumerate(('SessionStart','UserPromptSubmit')):
        values.extend([dict(type='system',subtype='hook_started',session_id=run,
                            hook_id=str(index),hook_event=event),
                       dict(type='system',subtype='hook_response',session_id=run,
                            hook_id=str(index),hook_event=event,stdout='{}\n',outcome='success',exit_code=0)])
    values.append(dict(type='result',session_id=run,subtype='error_max_turns' if failed else 'success',
        is_error=failed,result='fixed synthetic failure' if failed else json.dumps(dict.fromkeys(profile.final_required_keys,None)),
        permission_denials=[],num_turns=1,total_cost_usd=.125,
        modelUsage={'fixture-model':dict(provider='firstParty',inputTokens=11,outputTokens=12,
                                        cacheReadInputTokens=13,cacheCreationInputTokens=14)}))
    return values


def capture(mode,run,source):
    if sys.stdin.buffer.read(1)!=b'G':
        return 31
    data=sys.stdin.buffer.read(8501)
    if len(data)>8500 or data!=INPUT_MESSAGE:
        return 32
    read,write=os.pipe()
    origin=time.monotonic_ns()
    sys.stderr.buffer.write(encoded(dict(kind='CAPTURE_START',schema='cairn.worker-capture-start/1',
        run_id=run,source_manifest_sha256=source,
        start_monotonic_ns=origin+60*10**9 if mode=='future_start' else origin))+b'\n')
    sys.stderr.buffer.flush()
    pid=os.fork()
    if pid==0:
        os.close(write)
        received=bytearray()
        while len(received)<=8500:
            block=os.read(read,8501-len(received))
            if not block:break
            received.extend(block)
        os.close(read)
        if received!=INPUT_MESSAGE:os._exit(33)
        for value in synthetic_records(run,mode=='actor_nonzero'):
            payload=encoded(value)+b'\n'
            while payload:
                count=os.write(1,payload);payload=payload[count:]
            time.sleep(.005)
        os._exit(17 if mode=='actor_nonzero' else 0)
    os.close(read)
    ticks=int(Path(f'/proc/{pid}/stat').read_text().rsplit(') ',1)[1].split()[19])
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    written=0
    while written<len(data):written+=os.write(write,data[written:])
    os.close(write)
    _,raw=os.waitpid(pid,0)
    if mode!='lost_wait':
        sys.stderr.buffer.write(encoded(dict(kind='ACTOR_WAIT',run_id=run,
            actor_wait=dict(pid=pid,start_ticks=ticks,boot_id=boot,raw_status=raw,
                            returncode=os.waitstatus_to_exitcode(raw)),
            input_bytes=len(data),input_bytes_written=written,observations=[]))+b'\n')
        sys.stderr.buffer.flush()
    return 0


if __name__=='__main__':
    role,mode,run,source,intent=sys.argv[1:]
    if role=='capture' and mode in ('success','actor_nonzero','future_start','lost_wait'):
        result=capture(mode,run,source)
    elif role=='composition' and mode in ('normal','nonzero','hang'):
        result=sink_main()
    else:
        result=36
    sys.exit(result)
