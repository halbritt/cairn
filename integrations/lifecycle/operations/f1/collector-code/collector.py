"""Disabled fixed-scope F1 collector candidate. No effect or generic command API."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
def require_no_pycache(directory):
    # -B suppresses writes, not reads of a pre-existing local bytecode cache.
    try: os.lstat(Path(directory)/'__pycache__')
    except FileNotFoundError: return
    except OSError: raise RuntimeError('CODE_CACHE_VISIBILITY_UNKNOWN') from None
    raise RuntimeError('CODE_CACHE_PRESENT')


def require_code_closure(directory, entry):
    expected={entry,'parsers.py','read_process.py'}
    if entry not in ('collector.py','metadata_helper.py'):raise RuntimeError('CODE_CLOSURE_ENTRY')
    directory=Path(directory)
    def identity(info):
        return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    try:
        before=os.lstat(directory)
        if not stat.S_ISDIR(before.st_mode):raise RuntimeError('CODE_CLOSURE_DIRECTORY')
        names=set()
        with os.scandir(directory) as entries:
            for item in entries:
                if len(names)>=3 or item.name not in expected:raise RuntimeError('CODE_CLOSURE_ENTRIES')
                names.add(item.name)
        if names!=expected:raise RuntimeError('CODE_CLOSURE_ENTRIES')
        for name in sorted(expected):
            path=directory/name;info=os.lstat(path)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise RuntimeError('CODE_CLOSURE_FILE')
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
            try:
                if identity(os.fstat(fd))!=identity(info):raise RuntimeError('CODE_CLOSURE_RACED')
            finally:os.close(fd)
            if identity(os.lstat(path))!=identity(info):raise RuntimeError('CODE_CLOSURE_RACED')
        if identity(os.lstat(directory))!=identity(before):raise RuntimeError('CODE_CLOSURE_RACED')
    except OSError:raise RuntimeError('CODE_CLOSURE_VISIBILITY_UNKNOWN') from None


require_no_pycache(Path(__file__).resolve().parent)
require_code_closure(Path(__file__).resolve().parent,'collector.py')
sys.path.insert(0,str(Path(__file__).resolve().parent))
from parsers import Unknown,need,decode,unit,tailscale,directory,local_map,API_SOCKET,TS_SOCKET
from read_process import ReadProcess

ENACTED = False
OUTPUT_ROOT = None
ROOT = Path(__file__).resolve().parent
BINDING = {'api_image_sha256':None,'daemon_image_sha256':None,'postgres_image_sha256':None,
           'cairn_sha256':'d43ca49b01aa05766ee68351b2a197d9e45bf0fda5d89695be32cfd7c9d66504','systemctl_sha256':None,'ss_sha256':None,
           'sudo_sha256':None,'python_sha256':None,'python_alias_target':None,'helper_files':None}
TAILSCALE_SHA='ae6d013e3a9dac4d6ae7cd682cf89215e512d6f12ae94d39c72a93f13d2fce66'
HELPER='/usr/local/libexec/cairn-f1-metadata-v2/metadata_helper.py'
PROPERTIES='--property=Id,MainPID,NRestarts,ActiveState,SubState'
API_UNIT=('/usr/bin/systemctl','--user','show','cairn-api.service',PROPERTIES,'--no-pager','--no-ask-password')
TS_UNIT=('/usr/bin/systemctl','show','tailscaled.service',PROPERTIES,'--no-pager','--no-ask-password')
TS=('/usr/bin/tailscale','--socket=/var/run/tailscale/tailscaled.sock','serve','status','--json')
DIRECTORY=('/home/halbritt/.local/bin/cairn','agent','--socket',API_SOCKET,'--token-file',
           '/home/halbritt/.local/share/cairn/hosted-agent.token','agent-directory')
CLIENTS=DIRECTORY[:-1]+('clients','--limit','1')
BODY=b'{"repo":"/home/halbritt/git/cairn","machine_id":"archon","limit":1}\n'


SUDO_LINKS=(('/usr/bin/sudo','/etc/alternatives/sudo'),('/etc/alternatives/sudo','/usr/lib/cargo/bin/sudo'))
SUDO_ENDPOINT='/usr/lib/cargo/bin/sudo'
SUDO_PARENTS=('/','/usr','/usr/bin','/etc','/etc/alternatives','/usr/lib','/usr/lib/cargo','/usr/lib/cargo/bin')


def sudo_chain():
    def identity(info):
        return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    observed=[]
    for path in SUDO_PARENTS:
        info=os.lstat(path)
        need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'SUDO_PARENT_GUARD')
        observed.append((path,identity(info)))
    for path,target in SUDO_LINKS:
        info=os.lstat(path)
        need(stat.S_ISLNK(info.st_mode) and info.st_uid==0,'SUDO_LINK_GUARD')
        need(os.readlink(path)==target,'SUDO_LINK_CHANGED')
        observed.append((path,identity(info),target))
    endpoint=os.lstat(SUDO_ENDPOINT)
    need(stat.S_ISREG(endpoint.st_mode) and endpoint.st_uid==0 and endpoint.st_nlink==1 and not endpoint.st_mode&0o022,'SUDO_ENDPOINT_GUARD')
    observed.append((SUDO_ENDPOINT,identity(endpoint)))
    return observed


def checked_image(path,digest,owner,alias=None):
    need(type(digest) is str and re.fullmatch(r'[0-9a-f]{64}',digest),'IMAGE_PIN_UNBOUND')
    # Do not resolve a different installed image or silently follow a final symlink.
    original=path
    chain=None
    if path=='/usr/bin/sudo':
        need(alias is None and owner==0,'SUDO_ALIAS_INPUT')
        chain=sudo_chain();path=SUDO_ENDPOINT
    if alias is not None:
        need(path=='/usr/bin/python3' and re.fullmatch(r'python3\.[0-9]+',alias),'ALIAS_UNBOUND')
        need(os.readlink(path)==alias,'ALIAS_CHANGED')
        path='/usr/bin/'+alias
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_NONBLOCK)
    try:
        before=os.fstat(fd);need(stat.S_ISREG(before.st_mode) and before.st_uid==owner and
            before.st_nlink==1 and not before.st_mode&0o022,'IMAGE_GUARD')
        h=hashlib.sha256();total=0
        while True:
            block=os.read(fd,min(65536,67108865-total))
            if not block:break
            total+=len(block);need(total<=67108864,'IMAGE_BOUND');h.update(block)
        after=os.fstat(fd)
        need((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==
             (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'IMAGE_RACED')
        need(h.hexdigest()==digest,'IMAGE_PIN_CHANGED')
    finally:os.close(fd)
    if alias is not None:need(os.readlink(original)==alias,'ALIAS_CHANGED')
    if chain is not None:need(sudo_chain()==chain,'SUDO_CHAIN_RACED')


class HostReads:
    def __init__(self):
        need(ENACTED,'NOT_ENACTED')
        # Binding is a reviewed source delta, never a caller JSON/pass flag.
        for key,value in BINDING.items():
            if key.endswith('_sha256'):need(type(value) is str and re.fullmatch(r'[0-9a-f]{64}',value),'BINDING_UNAVAILABLE')
        need(type(BINDING['helper_files']) is dict and set(BINDING['helper_files'])=={'metadata_helper.py','parsers.py','read_process.py'},'HELPER_BINDING_UNAVAILABLE')
        self.receipts=[]

    def now(self):return time.monotonic()

    def run(self,kind,argv,body,deadline):
        self.pins()
        reader=ReadProcess([API_UNIT,TS_UNIT,TS,DIRECTORY,CLIENTS]+([tuple(argv)] if kind.startswith('metadata-') and self._helper_argv(argv) else []))
        try:return reader.run(kind,argv,body,deadline)
        finally:self.receipts.extend(reader.receipts)

    def _helper_argv(self,argv):
        return (len(argv)==10 and tuple(argv[:7])==('/usr/bin/sudo','-n','/usr/bin/python3','-I','-B',HELPER,'--api-pid') and
                re.fullmatch(r'[1-9][0-9]{0,9}',argv[7]) and argv[8]=='--tailscaled-pid' and
                re.fullmatch(r'[1-9][0-9]{0,9}',argv[9]))

    def pins(self):
        require_no_pycache(ROOT)
        require_no_pycache(Path(HELPER).parent)
        require_code_closure(ROOT,'collector.py')
        require_code_closure(Path(HELPER).parent,'metadata_helper.py')
        for path,key,owner in [('/home/halbritt/.local/bin/cairn','cairn_sha256',1000),('/usr/bin/systemctl','systemctl_sha256',0),
                ('/usr/bin/ss','ss_sha256',0),('/usr/bin/sudo','sudo_sha256',0),('/usr/bin/python3','python_sha256',0)]:
            checked_image(path,BINDING[key],owner,BINDING['python_alias_target'] if key=='python_sha256' else None)
        checked_image('/usr/bin/tailscale',TAILSCALE_SHA,0)
        # Future privileged closure must be exclusively root-owned and immutable
        # to the invoking uid. This proposes no installed sudo policy.
        parent=Path(HELPER).parent
        for directory_path in (parent,*parent.parents):
            info=os.lstat(directory_path)
            need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'HELPER_DIRECTORY_GUARD')
        for name,digest in BINDING['helper_files'].items():checked_image(parent/name,digest,0)


def child_receipt(row):
    need(type(row) is dict and row.get('class') in ('unix-connected','unix-listeners','tcp-listeners','tcp-connections'),'HELPER_CHILD_CLASS')
    need(row.get('outcome') in ('COMPLETE_ZERO','COMPLETE_NONZERO','LAUNCH_UNKNOWN','TIMEOUT_UNKNOWN','OVERFLOW_UNKNOWN','INTAKE_ERROR_UNKNOWN'),'HELPER_CHILD_OUTCOME')
    out={key:row.get(key) for key in ('class','pid','start_ticks','begin_mono','end_mono','outcome','returncode','stdout_bytes','stderr_bytes','eof')}
    for key in ('pid','start_ticks'):
        need(out[key] is None or type(out[key]) is int and out[key]>0,'HELPER_CHILD_IDENTITY')
    for key in ('begin_mono','end_mono'):
        need(type(out[key]) in (int,float) and math.isfinite(out[key]),'HELPER_CHILD_BRACKET')
    need(out['end_mono']>=out['begin_mono'],'HELPER_CHILD_BRACKET')
    need(out['returncode'] is None or type(out['returncode']) is int,'HELPER_CHILD_STATUS')
    need(type(out['eof']) is bool and all(type(out[k]) is int and out[k]>=0 for k in ('stdout_bytes','stderr_bytes')),'HELPER_CHILD_SHAPE')
    if out['outcome']=='COMPLETE_ZERO':need(out['returncode']==0 and out['eof'] and out['end_mono']-out['begin_mono']<=2,'HELPER_CHILD_COMPLETION')
    return out


def metadata(raw,api_unit,daemon_unit,binding):
    value=decode(raw)
    need(type(value) is dict and value.get('schema')=='cairn.F1-metadata/2' and 'status' not in value,'METADATA_UNKNOWN')
    for name,unit_row,pin in [('api',api_unit,'api_image_sha256'),('daemon',daemon_unit,'daemon_image_sha256')]:
        row=value.get(name)
        need(type(row) is dict and row.get('pid')==unit_row['pid'] and type(row.get('start_ticks')) is int and row['start_ticks']>0,'METADATA_PROCESS')
        need(row.get('image_sha256')==binding[pin] and binding[pin] is not None,'METADATA_IMAGE')
    need(value['api'].get('uid')==1000 and value['daemon'].get('uid')==0,'METADATA_OWNER')
    need(value.get('api_listener',{}).get('path')==API_SOCKET and value.get('api_listener',{}).get('owner_pid')==api_unit['pid'],'METADATA_API_LISTENER')
    need(value.get('control_listener',{}).get('path')==TS_SOCKET and value.get('control_listener',{}).get('owner_pid')==daemon_unit['pid'],'METADATA_CONTROL_LISTENER')
    need(value.get('api_tcp_listener')=='127.0.0.1:18889','METADATA_TCP_LISTENER')
    backends=value.get('pg_backends');parent=value.get('pg_parent')
    need(type(backends) is list and 0<len(backends)<=16 and type(parent) is dict,'METADATA_PG')
    need(binding['postgres_image_sha256'] is not None and all(row.get('image_sha256')==binding['postgres_image_sha256'] for row in [parent]+backends),'METADATA_PG_IMAGE')
    children=value.get('children')
    need(type(children) is list and len(children)==4 and [r.get('class') for r in children]==['unix-connected','unix-listeners','tcp-listeners','tcp-connections'] and all(child_receipt(r)['outcome']=='COMPLETE_ZERO' and r.get('eof') is True for r in children),'METADATA_CHILD_UNKNOWN')
    for process in [value['api'],value['daemon'],parent]+backends:
        ns=process.get('namespaces');need(type(ns) is dict and set(ns)=={'pid','net','mnt','time'},'METADATA_NAMESPACE')
        need(all(type(v) is str and re.fullmatch(k+r':\[[0-9]+\]',v) for k,v in ns.items()),'METADATA_NAMESPACE')
    return value


def identity(value):
    return {name:{k:value[name][k] for k in ('pid','start_ticks','image_sha256','namespaces')} for name in ('api','daemon','pg_parent')}


def collect(adapter,binding):
    start=adapter.now();deadline=start+30.0;steps=[];observations={}
    result={'schema':'cairn.F1-observation/2','status':'UNKNOWN','f1_acceptance':'NOT_ASSESSED','readiness':'NOT_ASSESSED',
            'begin_mono':start,'end_mono':None,'completed_stages':steps,'children':adapter.receipts,'reason':None,
            'claims':{},'helper_children':[],'helper_descendants':'UNKNOWN','limits':['cooperative configured-credential relation only','no physical-machine or per-request TCP attestation',
                                'no complete owner census','non-atomic metadata; no future clock/store guarantee']}
    def read(name,argv,body=b''):
        before=adapter.now();need(before<deadline,'WHOLE_BUDGET_EXHAUSTED')
        raw=adapter.run(name,argv,body,deadline)
        after=adapter.now();need(after<=deadline,'WHOLE_BUDGET_EXHAUSTED')
        need(after-before<=2.0,'COMPOSITE_READ_OVERRUN')
        steps.append({'name':name,'begin_mono':before,'end_mono':after});return raw
    def meta(name,argv,api,daemon):
        raw=read(name,argv)
        decoded=decode(raw)
        if type(decoded) is dict and type(decoded.get('children')) is list:
            for child in decoded['children']:
                result['helper_children'].append(dict(parent_stage=name,**child_receipt(child)))
        return metadata(raw,api,daemon,binding)
    try:
        api=unit(read('api-unit-before',API_UNIT),'cairn-api.service')
        daemon=unit(read('daemon-unit-before',TS_UNIT),'tailscaled.service')
        helper=('/usr/bin/sudo','-n','/usr/bin/python3','-I','-B',HELPER,'--api-pid',str(api['pid']),'--tailscaled-pid',str(daemon['pid']))
        before=meta('metadata-before',helper,api,daemon)
        first_route=tailscale(read('route-before',TS))
        sample=directory(read('directory',DIRECTORY,BODY))
        own_map=local_map(read('clients',CLIENTS))
        last_route=tailscale(read('route-after',TS))
        after=meta('metadata-after',helper,api,daemon)
        api_after=unit(read('api-unit-after',API_UNIT),'cairn-api.service')
        daemon_after=unit(read('daemon-unit-after',TS_UNIT),'tailscaled.service')
        adapter.pins()
        need(adapter.now()<=deadline,'WHOLE_BUDGET_EXHAUSTED')
        need(api==api_after and daemon==daemon_after and identity(before)==identity(after),'IDENTITY_CHANGED')
        for key in ('api_listener','control_listener','api_socket','control_socket','control_aliases','journal_endpoint'):
            need(before[key]==after[key],'ENDPOINT_CHANGED')
        need(first_route==last_route,'ROUTE_CHANGED')
        result['helper_descendants']='ALL_REPORTED_READ_CHILDREN_COMPLETED_ZERO'
        result['status']='OBSERVED_COOPERATIVE_RELATIONSHIP_CANDIDATE'
        result['claims']={'local_mapping':own_map,'directory_sample':sample,'route':last_route,'identities':identity(after),
                          'api_listener':after['api_listener'],'control_listener':after['control_listener'],
                          'control_aliases':after['control_aliases'],'journal_endpoint':after['journal_endpoint'],
                          'postgres_pair_counts':[len(before['postgres_pairs']),len(after['postgres_pairs'])],
                          'same_store_basis':'bound selected API image; source shares one Server/DSN across endpoints; selected local backend metadata',
                          'assurance':'configuration-level relationship; acceptance belongs to Root and independent operations review'}
    except Unknown as exc:result['reason']=str(exc)
    except (OSError,ValueError,TypeError,KeyError):result['reason']='OBSERVATION_ACCESS_SHAPE_OR_RACE'
    finally:result['end_mono']=adapter.now()
    return result


def durable(path,value):
    raw=json.dumps(value,separators=(',',':'),allow_nan=False).encode()+b'\n'
    need(len(raw)<=65536,'FINAL_OUTPUT_BOUND')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        offset=0
        while offset<len(raw):
            count=os.write(fd,raw[offset:]);need(count>0,'OUTPUT_WRITE_FAILED');offset+=count
        os.fsync(fd)
    finally:os.close(fd)
    d=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(d)
    finally:os.close(d)


def prepare_output(output):
    output.mkdir(mode=0o700)  # exclusive one invocation, no resume
    parent_fd=os.open(output.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(parent_fd)
    finally:os.close(parent_fd)
    durable(output/'intent.json',{'schema':'cairn.F1-intent/2','state':'STARTED_NO_RETRY'})


def main():
    if not ENACTED:raise SystemExit('F1 COLLECTOR NOT ENACTED')
    need(type(OUTPUT_ROOT) is str and Path(OUTPUT_ROOT).is_absolute(),'OUTPUT_NOT_SELECTED')
    output=Path(OUTPUT_ROOT);prepare_output(output)
    adapter=None
    try:
        adapter=HostReads();value=collect(adapter,BINDING)
    except Unknown as exc:value={'schema':'cairn.F1-observation/2','status':'UNKNOWN','reason':str(exc),'f1_acceptance':'NOT_ASSESSED','readiness':'NOT_ASSESSED'}
    except BaseException:
        value={'schema':'cairn.F1-observation/2','status':'UNKNOWN','reason':'INTERRUPTED_OR_UNEXPECTED_FAILURE','f1_acceptance':'NOT_ASSESSED','readiness':'NOT_ASSESSED','children':adapter.receipts if adapter else []}
    # Missing/partial final or a durability error is UNKNOWN; no automatic resume.
    durable(output/'final.json',value)
    print(json.dumps({'status':value['status'],'f1_acceptance':'NOT_ASSESSED'}))

if __name__=='__main__':main()
