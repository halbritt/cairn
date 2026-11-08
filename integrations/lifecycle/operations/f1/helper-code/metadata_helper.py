"""Proposed root-owned F1 metadata helper. No privilege policy is selected, bound or verified by this packet."""
import hashlib
import json
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
require_code_closure(Path(__file__).resolve().parent,'metadata_helper.py')
sys.path.insert(0,str(Path(__file__).resolve().parent))
from parsers import API_SOCKET,TS_SOCKET,PG_PATHS,Unknown,need,proc_stat,unix_rows,tcp_listener,tcp_connections
from read_process import ReadProcess

ENACTED = False
EXPECTED_IMAGES = {'api':None,'daemon':None,'postgres':None}
SS = (('/usr/bin/ss','-xnpH'),('/usr/bin/ss','-xlnpH'),('/usr/bin/ss','-ntlpH'),('/usr/bin/ss','-ntpH'))
MAX_FDS=4096
MAX_PG=16
CONTROL_PATHS=(TS_SOCKET,'/run/tailscale/tailscaled.sock')
JOURNAL_PATH='/run/systemd/journal/stdout'
JOURNAL_PARENTS=('/','/run','/run/systemd','/run/systemd/journal')


def read_regular(path,limit):
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_CLOEXEC)
    try:
        info=os.fstat(fd);need(stat.S_ISREG(info.st_mode),'METADATA_NOT_REGULAR')
        out=bytearray()
        while len(out)<=limit:
            block=os.read(fd,min(65536,limit+1-len(out)))
            if not block:break
            out.extend(block)
        need(len(out)<=limit,'METADATA_BOUND');return bytes(out)
    finally:os.close(fd)


class ProcView:
    def observer_namespaces(self):
        return {key:os.readlink('/proc/self/ns/'+key) for key in ('pid','net','mnt','time')}

    def process(self,pid):
        base=Path('/proc')/str(pid)
        first=proc_stat(read_regular(base/'stat',8192),pid)
        exe=os.readlink(base/'exe');need(not exe.endswith(' (deleted)'),'DELETED_IMAGE')
        # Deliberately follow only this selected /proc/PID/exe magic link.
        image=read_regular(base/'exe',67108864)
        ns={key:os.readlink(base/'ns'/key) for key in ('pid','net','mnt','time')}
        uid=(base/'stat').stat().st_uid
        last=proc_stat(read_regular(base/'stat',8192),pid)
        need(first==last,'PROCESS_RACED')
        return dict(first,exe=exe,image_sha256=hashlib.sha256(image).hexdigest(),uid=uid,namespaces=ns)

    def fds(self,pid):
        selected={}
        names=[]
        with os.scandir('/proc/'+str(pid)+'/fd') as entries:
            for entry in entries:
                need(len(names)<MAX_FDS,'FD_BOUND');names.append(entry.name)
        for name in names:
            need(name.isdigit(),'FD_NAME')
            target=os.readlink('/proc/'+str(pid)+'/fd/'+name)
            match=re.fullmatch(r'socket:\[(\d+)\]',target)
            if match:selected.setdefault(int(match[1]),[]).append(int(name))
        return selected

    def socket(self,path):
        # /var/run may be the system /run alias; only the selected final socket is read.
        info=os.lstat(path);need(stat.S_ISSOCK(info.st_mode),'CONTROL_NOT_SOCKET')
        return {'dev':info.st_dev,'inode':info.st_ino,'uid':info.st_uid,'mode':info.st_mode&0o777}

    def journal(self):
        parents=[]
        for path in JOURNAL_PARENTS:
            info=os.lstat(path)
            need(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022,'JOURNAL_PARENT_GUARD')
            parents.append({'path':path,'dev':info.st_dev,'inode':info.st_ino,'uid':info.st_uid,'mode':info.st_mode&0o777})
        endpoint=self.socket(JOURNAL_PATH)
        need(endpoint['uid']==0,'JOURNAL_SOCKET_OWNER')
        return {'socket':endpoint,'parents':parents}


def control_aliases(view):
    aliases={path:view.socket(path) for path in CONTROL_PATHS}
    a,b=(aliases[path] for path in CONTROL_PATHS)
    need(a['uid']==b['uid']==0 and (a['dev'],a['inode'])==(b['dev'],b['inode']),'CONTROL_ALIAS_DIFFERENT')
    return aliases


def control_owner(listeners,pid,fds):
    rows=[r for r in listeners if r['address'] in CONTROL_PATHS]
    need(len(rows)==1 and rows[0]['state']=='LISTEN' and rows[0]['pids']==[pid] and rows[0]['inode'] in fds,'LISTENER_OWNER_UNKNOWN')
    return {'path':TS_SOCKET,'observed_path':rows[0]['address'],'inode':rows[0]['inode'],'owner_pid':pid}


def selected_owner(listeners,path,pid,fds):
    rows=[r for r in listeners if r['address']==path]
    need(len(rows)==1 and rows[0]['pids']==[pid] and rows[0]['inode'] in fds,'LISTENER_OWNER_UNKNOWN')
    return {'path':path,'inode':rows[0]['inode'],'owner_pid':pid}


def observe(api_pid,daemon_pid,reader,view,deadline):
    need(type(api_pid) is int and type(daemon_pid) is int and api_pid>1 and daemon_pid>1 and api_pid!=daemon_pid,'PID_INPUT')
    observer_ns=view.observer_namespaces()
    api=view.process(api_pid);daemon=view.process(daemon_pid)
    need(api['uid']==1000 and daemon['uid']==0,'PROCESS_OWNER')
    need(EXPECTED_IMAGES['api'] is not None and EXPECTED_IMAGES['daemon'] is not None and api['image_sha256']==EXPECTED_IMAGES['api'] and daemon['image_sha256']==EXPECTED_IMAGES['daemon'],'HELPER_IMAGE_UNBOUND_OR_CHANGED')
    daemon_fds=view.fds(daemon_pid)
    api_socket=view.socket(API_SOCKET);aliases=control_aliases(view);control_socket=aliases[TS_SOCKET]
    need(api_socket['uid']==1000 and api_socket['mode']==0o600 and control_socket['uid']==0,'SOCKET_OWNER_MODE')
    connected=unix_rows(reader.run('unix-connected',SS[0],b'',deadline,262144))
    listeners=unix_rows(reader.run('unix-listeners',SS[1],b'',deadline,262144))
    tcp=tcp_listener(reader.run('tcp-listeners',SS[2],b'',deadline,262144),api_pid)
    ingress_count=tcp_connections(reader.run('tcp-connections',SS[3],b'',deadline,262144),api_pid)
    # Post-SS inventory: never skip a late API-attributed socket.
    api_fds=view.fds(api_pid)
    api_listener=selected_owner(listeners,API_SOCKET,api_pid,api_fds)
    control_listener=control_owner(listeners,daemon_pid,daemon_fds)
    pg_listeners=[r for r in listeners if r['address'] in PG_PATHS]
    pg_parent_ids={pid for r in pg_listeners for pid in r['pids']}
    need(len(pg_parent_ids)==1,'PG_LISTENER_UNKNOWN');parent=next(iter(pg_parent_ids))
    pg_parent=view.process(parent)
    need(EXPECTED_IMAGES['postgres'] is not None and pg_parent['image_sha256']==EXPECTED_IMAGES['postgres'],'HELPER_PG_IMAGE')
    parent_fds=view.fds(parent)
    need(any(r['inode'] in parent_fds for r in pg_listeners),'PG_LISTENER_FD_UNKNOWN')
    # Duplicate inode descriptions are ambiguous; do not select an arbitrary row.
    by_inode={}
    for row in connected:
        need(row['inode'] not in by_inode,'DUPLICATE_SOCKET_INODE');by_inode[row['inode']]=row
    pairs=[];backends={};journal_endpoint=None
    for row in connected:
        if api_pid not in row['pids'] and row['inode'] not in api_fds:continue
        if row['address']==API_SOCKET:continue
        need(row['inode'] in api_fds and api_fds[row['inode']],'API_FD_VISIBILITY_UNKNOWN')
        peer=by_inode.get(row['peer_inode'])
        # Holder PIDs are not authority; the fixed root path and stdio relation are.
        if peer and peer['address']==JOURNAL_PATH:
            need(peer['peer_inode']==row['inode'] and bool(api_fds[row['inode']]) and set(api_fds[row['inode']])<={1,2},'JOURNAL_STDIO_RELATION')
            if journal_endpoint is None:journal_endpoint=view.journal()
            continue
        need(peer is not None and peer['address'] in PG_PATHS and peer['peer_inode']==row['inode'] and len(peer['pids'])==1,'API_UNIX_PEER_UNKNOWN')
        pid=peer['pids'][0]
        need(len(backends)<MAX_PG or pid in backends,'PG_BOUND')
        backend=view.process(pid)
        need(backend['image_sha256']==EXPECTED_IMAGES['postgres'],'HELPER_PG_IMAGE')
        fds=view.fds(pid)
        need(backend['ppid']==parent and peer['inode'] in fds,'PG_BACKEND_OWNER_UNKNOWN')
        need(all(backend['namespaces'][k]==api['namespaces'][k] for k in ('net','time')),'PG_NAMESPACE_DIFFERENT')
        backends[pid]=backend
        pairs.append({'api_inode':row['inode'],'pg_inode':peer['inode'],'pg_pid':pid,'pg_path':peer['address']})
    need(pairs,'PG_PAIRS_ABSENT')
    need(all(api['namespaces'][k]==daemon['namespaces'][k]==pg_parent['namespaces'][k]==observer_ns[k] for k in ('net','time')),'CONTROL_NAMESPACE_DIFFERENT')
    need(view.process(api_pid)==api and view.process(daemon_pid)==daemon and view.process(parent)==pg_parent,'PROCESS_RACED')
    need(all(view.process(pid)==old for pid,old in backends.items()),'PG_RACED')
    need(view.socket(API_SOCKET)==api_socket and control_aliases(view)==aliases,'SOCKET_RACED')
    if journal_endpoint is not None:need(view.journal()==journal_endpoint,'JOURNAL_RACED')
    return {'schema':'cairn.F1-metadata/2','observer_namespaces':observer_ns,'api':api,'daemon':daemon,'pg_parent':pg_parent,
            'pg_backends':list(backends.values()),'api_listener':api_listener,'control_listener':control_listener,
            'api_socket':api_socket,'control_socket':control_socket,'control_aliases':aliases,'journal_endpoint':journal_endpoint,'api_tcp_listener':tcp,
            'postgres_pairs':pairs,'api_loopback_connections':ingress_count,'children':reader.receipts,
            'limits':'selected non-atomic metadata; no global census, query audit or clock guarantee'}


def main():
    if not ENACTED:raise SystemExit('F1 METADATA HELPER NOT ENACTED')
    # Future enablement must bind a root-owned closure and authorized privilege route.
    need(all(type(v) is str and re.fullmatch(r'[0-9a-f]{64}',v) for v in EXPECTED_IMAGES.values()),'HELPER_BINDING_UNAVAILABLE')
    need(os.geteuid()==0,'HELPER_PRIVILEGE_UNAVAILABLE')
    need(len(sys.argv)==5 and sys.argv[1]=='--api-pid' and sys.argv[3]=='--tailscaled-pid' and
         all(re.fullmatch(r'[1-9][0-9]{0,9}',v) for v in (sys.argv[2],sys.argv[4])),'HELPER_ARGUMENTS')
    reader=ReadProcess(SS);deadline=time.monotonic()+2.0
    try:value=observe(int(sys.argv[2]),int(sys.argv[4]),reader,ProcView(),deadline)
    except Unknown as exc:value={'schema':'cairn.F1-metadata/2','status':'UNKNOWN','reason':str(exc),'children':reader.receipts}
    except OSError:value={'schema':'cairn.F1-metadata/2','status':'UNKNOWN','reason':'METADATA_ACCESS_OR_RACE','children':reader.receipts}
    raw=json.dumps(value,separators=(',',':')).encode();need(len(raw)<=65536,'HELPER_OUTPUT_BOUND')
    sys.stdout.buffer.write(raw+b'\n')

if __name__=='__main__':main()
