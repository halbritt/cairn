"""Audit selected live-trial evidence before computing latency summaries."""
import argparse
import datetime
import json
import math
from pathlib import Path
import subprocess

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,required=True)
parser.add_argument('--metadata',type=Path,required=True)
parser.add_argument('--token-file',required=True)
parser.add_argument('--samples',type=int,default=10)
args=parser.parse_args()
events=[json.loads(line) for line in args.metadata.read_text().splitlines()]
assert not any(e['kind']=='reader_error' for e in events),'native observer failed'
rows=[]
def one(values,label):
    assert len(values)==1,(label,len(values))
    return values[0]
def dt(value):
    return datetime.datetime.fromisoformat(value)
def ms(a,b):
    assert b>=a,('reversed interval',a,b)
    return (b-a)/1e6
for n in range(1,args.samples+1):
    d=json.loads((args.root/f'sample-{n:02d}.json').read_text())
    event=d['publication']
    group=d['last_group']
    assert group['state']=='collected' and group['expected']==group['responded']==1
    assert not group['more'] and not d['status']['more']
    assert group['event_id']==event['event_id']
    assert d['status']['event']['event_id']==event['event_id']
    member=one(group['members'],'response member')
    delivery=one(d['status']['deliveries'],'delivery')
    assert delivery['state']=='handled' and delivery['attempts']==1
    assert delivery['event']['event_id']==event['event_id']
    assert delivery['consumer']==member['consumer']==event['destination']['name']
    assert member['delivery_id']==delivery['delivery_id']
    assert member['payload_available'] and member['ref']==delivery['result']
    observation=one(group['observations'],'response observation')
    assert observation['event_id']==member['response_event_id']
    assert observation['from']==delivery['consumer']
    r=subprocess.run(['cairn','agent','--token-file',args.token_file,'history'],input=json.dumps(member['ref']),text=True,capture_output=True,check=True)
    history=json.loads(r.stdout)
    assert history['ok']
    version=one(history['data']['versions'],'result version')
    assert version['version']==member['ref']['version'] and version['payload_available']
    assert version['observed_writer']==delivery['consumer']
    assert version['body'].strip()==d['beacon'],('wrong result',n)
    context=one([e for e in events if e['kind']=='context' and e['event_id']==event['event_id']],'context')
    assert context['delivery_id']==delivery['delivery_id'] and context['source']==event['ref']
    turn=context['native_turn_id']
    native=[e for e in events if e['kind']=='native' and e.get('turn_id')==turn]
    start=one([e for e in native if e['method']=='turn/started'],'turn start')
    end=one([e for e in native if e['method']=='turn/completed'],'turn completion')
    assert end['status']=='completed'
    deltas=[e for e in native if e['method']=='item/agentMessage/delta']
    assert deltas,('no observed provider text',n)
    first=min(deltas,key=lambda e:e['mono_ns'])
    wakes=[e for e in events if e['kind']=='state' and e.get('idle_wake',{} ) and e['idle_wake']['delivery_id']==delivery['delivery_id']]
    assert wakes,('wake marker missing',n)
    wake=min(wakes,key=lambda e:e['mono_ns'])
    assert wake['state']=='idle'
    assert wake['idle_wake']['transport']=='codex-queue'
    assert wake['idle_wake']['session']['agent_id']==event['resolution']['agent_id']
    assert wake['idle_wake']['session']['execution_id']==event['resolution']['execution_id']
    thread=wake['idle_wake']['native_id']
    assert all(e['thread_id']==thread for e in native)
    assert start['mono_ns']<=first['mono_ns']<=end['mono_ns']
    assert context['mono_ns']<=end['mono_ns']
    # The previous native turn must have ended before this wake was observed.
    # Polling may observe the wake marker after its native turn/start event.
    # Check the preceding different turn at this turn's start boundary.
    previous=[e for e in events if e['kind']=='native' and e.get('thread_id')==thread and e.get('turn_id')!=turn and e.get('method') in ('turn/started','turn/completed') and e['mono_ns']<start['mono_ns']]
    assert previous
    prior=max(previous,key=lambda e:e['mono_ns'])
    assert prior['method']=='turn/completed' and prior['status']=='completed'
    row=dict(sample=n,event_id=event['event_id'],delivery_id=delivery['delivery_id'],
        attempt_id=context['attempt_id'],turn_id=turn,result_ref=member['ref'],
        response_event_id=member['response_event_id'],result_verified=True,attempts=1,
        publish_ack_ms=ms(d['publish_start_ns'],d['publish_ack_ns']),
        ack_to_response_observed_ms=ms(d['publish_ack_ns'],d['response_observed_ns']),
        wake_observed_to_context_ms=ms(wake['mono_ns'],context['mono_ns']),
        wake_observed_to_first_delta_ms=ms(wake['mono_ns'],first['mono_ns']),
        context_observed_to_first_delta_ms=ms(context['mono_ns'],first['mono_ns']),
        turn_start_to_first_delta_ms=ms(start['mono_ns'],first['mono_ns']),
        turn_duration_ms=ms(start['mono_ns'],end['mono_ns']),
        database_event_to_completion_ms=(dt(delivery['completed_at'])-dt(event['created_at'])).total_seconds()*1000)
    rows.append(row)
metrics={}
for k in rows[0]:
    if k.endswith('_ms'):
        values=sorted(r[k] for r in rows)
        metrics[k]=dict(n=len(values),p50=values[math.ceil(.5*len(values))-1],p95=values[math.ceil(.95*len(values))-1],maximum=max(values),minimum=min(values))
out=dict(schema='cairn.live-latency-audit/1',samples=rows,metrics=metrics)
print(json.dumps(out,indent=2))
