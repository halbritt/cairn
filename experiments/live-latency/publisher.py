"""Publish serial, bounded live latency requests to an existing idle trial session."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,required=True,help='new private result directory')
parser.add_argument('--token-file',required=True)
parser.add_argument('--agent-id',required=True)
parser.add_argument('--execution-id',required=True)
parser.add_argument('--recipient',required=True)
parser.add_argument('--collection',required=True)
args=parser.parse_args()
root=args.root.resolve()
os.umask(0o077)
root.mkdir(exist_ok=False)
token=args.token_file
sender=['--token-file',token,'--agent-id',args.agent_id,'--execution-id',args.execution_id]
target=args.recipient
def call(args,body=None):
    r=subprocess.run(['cairn',*args],input=body,text=True,capture_output=True,timeout=30)
    d=json.loads(r.stdout)
    if r.returncode or not d.get('ok'): raise RuntimeError(d)
    return d['data']
def save(p,d):
    (root/p).write_text(json.dumps(d,indent=2))
def idle():
    r=call(['agents','list','--token-file',token,'--agent-id',target])
    return len(r['agents'])==1 and r['agents'][0]['online'] and r['agents'][0]['metadata']['state']=='idle'
for i in range(10):
    deadline=time.monotonic()+180
    while not idle():
        if time.monotonic()>deadline: raise RuntimeError('recipient not idle; no new publication')
        time.sleep(1)
    time.sleep([0,3,7,11,17,2,5,13,19,23][i])
    beacon=f'CAIRN-LIVE-LATENCY-{i+1:02d}-{uuid.uuid4().hex[:8]}'
    e=dict(sample=i+1,beacon=beacon,remember_request=str(uuid.uuid4()),publish_request=str(uuid.uuid4()))
    save(f'sample-{i+1:02d}.json',e)
    body=f'Owner-authorized live latency sample {i+1}/10. Read this exact source version from the supplied native inbox context. Complete this request with the exact result text: {beacon}. Then send one response using the native context response argv with the completion result record UUID and version. No other work, polling, waits, registrations, or record changes. Finish the turn after the explicit completion and response.'
    e['source']=call(['agent','--token-file',token,'remember','--repo',args.collection,'--request-id',e['remember_request'],'--shareable','--kind','note','--stdin'],body)
    resolved=call(['agents','resolve','--token-file',token,'--agent-id',target])
    assert resolved.get('resolution'),resolved
    resolution=root/f'resolution-{i+1:02d}.json'
    resolution.write_text(json.dumps(resolved['resolution']))
    until=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(minutes=10)).isoformat()
    publish_args=['publish',*sender,'--request-id',e['publish_request'],'--resolution',str(resolution),'--kind','request','--response-policy','all','--response-deadline',until,'--version',str(e['source']['version']),e['source']['record_id']]
    e['publish_args']=publish_args
    e['publish_start_ns']=time.monotonic_ns()
    save(f'sample-{i+1:02d}.json',e)
    e['publication']=call(publish_args)
    e['publish_ack_ns']=time.monotonic_ns()
    save(f'sample-{i+1:02d}.json',e)
    print(json.dumps(dict(sample=i+1,stage='published',publication=e['publication'])),flush=True)
    event=e['publication']['event']['event_id'] if 'event' in e['publication'] else e['publication']['event_id']
    deadline=time.monotonic()+600
    while True:
        group=call(['response-group',*sender,event])
        e['last_group']=group
        save(f'sample-{i+1:02d}.json',e)
        # Retain exact response-group shape for independent audit.
        g=group.get('group',group)
        if g.get('state')=='collected':
            e['response_observed_ns']=time.monotonic_ns()
            e['status']=call(['event-status',*sender,event])
            save(f'sample-{i+1:02d}.json',e)
            print(json.dumps(dict(sample=i+1,stage='collected',elapsed_ms=(e['response_observed_ns']-e['publish_ack_ns'])/1e6,group=group)),flush=True)
            break
        if time.monotonic()>deadline: raise RuntimeError('response timeout; retain request, do not replay')
        time.sleep(.25)
save('controller-finished.json',dict(samples=10,mono_ns=time.monotonic_ns()))
