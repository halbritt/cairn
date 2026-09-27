#!/usr/bin/env python3
"""Bounded owner-authorized live trial; selected metadata only."""
import importlib.util
import argparse
import atexit
import json
import hashlib
import os
from pathlib import Path
import shlex
import socket
import signal
import subprocess
import sys
import threading
import time
import websocket

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root', type=Path, required=True, help='new private trial directory')
parser.add_argument('--source', type=Path, required=True, help='source matching the deployed Cairn release')
parser.add_argument('--codex', required=True, help='native Codex binary, not its interactive wrapper')
parser.add_argument('--cairn', required=True)
parser.add_argument('--socket', required=True)
parser.add_argument('--token-file', required=True)
parser.add_argument('--auth-file', required=True, help='existing account auth; linked only in the trial home')
parser.add_argument('--collection', required=True)
args = parser.parse_args()
root = args.root.resolve()
os.umask(0o077)
root.mkdir(exist_ok=False)
source = args.source.resolve()
home = root / 'codex-home'
home.mkdir(exist_ok=False)
(home / 'auth.json').symlink_to(Path(args.auth_file).resolve())
# Also remove the link when setup fails before the server's cleanup block.
atexit.register(lambda: (home/'auth.json').unlink(missing_ok=True))
def terminated(signum, frame):
    raise SystemExit(128+signum)
signal.signal(signal.SIGTERM, terminated)
signal.signal(signal.SIGHUP, terminated)
binary, token, api, native = args.cairn, args.token_file, args.socket, args.codex
mcp_args = ['mcp','--socket',api,'--token-file',token,'--repo',args.collection,'--codex-thread']
(home / 'config.toml').write_text('default_permissions = "cairn-trial"\n[permissions.cairn-trial]\nextends = ":workspace"\n[permissions.cairn-trial.network]\nenabled = true\n[mcp_servers.cairn]\ncommand = '+json.dumps(binary)+'\nargs = '+json.dumps(mcp_args)+'\n')
spec = importlib.util.spec_from_file_location('installer', source / 'scripts/install-agent-coordination.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
config = dict(cairn=binary,socket=api,token_file=token,repo=args.collection,harness='codex',binding='archon-latency',process_names=['codex'],config_home=str(home),native_delivery=True,idle_wakeup='/usr/bin/herdr')
engine = root / 'engine'
installed = installer.install(engine, home/'hooks.json', config)
(root/'provenance.json').write_text(json.dumps(dict(
    hostname=socket.gethostname(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
    coordination_sha256=hashlib.sha256((engine/'coordination.py').read_bytes()).hexdigest(),
    source=str(source),
    codex_version=subprocess.check_output([native,'--version'],text=True).strip(),
    cairn_cli=json.loads(subprocess.check_output([binary,'version'],text=True)),
    cairn_api=json.loads(subprocess.check_output([binary,'agent','--socket',api,'--token-file',token,'version'],text=True))
)))
command = shlex.join([sys.executable,str(engine/'coordination.py'),'hook','--config',str(installed)])
installer.trust_codex_hooks(home,home/'hooks.json',command,codex_binary=native)
log = (root/'metadata.jsonl').open('a',buffering=1)
lock = threading.Lock()
def emit(kind, **data):
    with lock:
        log.write(json.dumps(dict(kind=kind,mono_ns=time.monotonic_ns(),wall_ns=time.time_ns(),**data))+'\n')
def save(name,data):
    temp = root/(name+'.tmp')
    temp.write_text(json.dumps(data))
    temp.replace(root/name)
endpoint = root/'queue.sock'
server_log = (root/'server.log').open('w')
server = subprocess.Popen([native,'app-server','--listen','unix://'+str(endpoint)],env=dict(os.environ,CODEX_HOME=str(home)),stdout=server_log,stderr=server_log)
watcher = None
conn = None
stop = threading.Event()
responses = {}
condition = threading.Condition()
sequence = 0
try:
    deadline = time.monotonic()+20
    while not endpoint.exists():
        if server.poll() is not None or time.monotonic()>deadline: raise RuntimeError('app-server startup failed')
        time.sleep(.05)
    transport = socket.socket(socket.AF_UNIX)
    transport.connect(str(endpoint))
    conn = websocket.create_connection('ws://localhost',socket=transport,timeout=1)
    def receive():
        while not stop.is_set():
            try:
                raw = conn.recv()
                if not raw:
                    if not stop.is_set(): emit('reader_error',error='unexpected EOF')
                    break
                msg = json.loads(raw)
            except websocket.WebSocketTimeoutException: continue
            except Exception as e:
                emit('reader_error',error=type(e).__name__)
                break
            if 'id' in msg and 'method' not in msg:
                with condition:
                    responses[msg['id']] = msg
                    condition.notify_all()
                continue
            method = msg.get('method','')
            p = msg.get('params',{})
            item = p.get('item',{})
            turn = p.get('turn',{})
            selected = dict(method=method,thread_id=p.get('threadId'),turn_id=p.get('turnId') or turn.get('id'),item_id=p.get('itemId') or item.get('id'),item_type=item.get('type'),status=turn.get('status'))
            if method.startswith(('turn/','item/','thread/status','error')):
                emit('native',**selected)
            if method == 'turn/started': save('latest-started-turn.json',dict(turn_id=turn.get('id'),mono_ns=time.monotonic_ns()))
            if method == 'turn/completed': save('last-turn.json',dict(turn_id=turn.get('id'),status=turn.get('status'),mono_ns=time.monotonic_ns()))
    reader=threading.Thread(target=receive,daemon=True)
    reader.start()
    def rpc(method,params):
        global sequence
        sequence += 1
        ident = sequence
        conn.send(json.dumps(dict(id=ident,method=method,params=params)))
        with condition:
            if not condition.wait_for(lambda: ident in responses,timeout=30): raise RuntimeError('RPC timeout '+method)
            r = responses.pop(ident)
        if 'error' in r: raise RuntimeError(str(r['error']))
        return r['result']
    rpc('initialize',dict(clientInfo=dict(name='cairn-live-latency',version='1'),capabilities=dict(experimentalApi=True)))
    conn.send(json.dumps(dict(method='initialized')))
    thread = rpc('thread/start',dict(cwd=str(root),approvalPolicy='never',permissions='cairn-trial'))['thread']
    save('started.json',dict(native_session_id=thread['id'],server_pid=server.pid,model=thread.get('model')))
    def observe():
        seen = {}
        state_dir = engine/'state/archon-latency'
        while not stop.is_set():
            for p in state_dir.glob('*.json'):
                try:
                    data=json.loads(p.read_text())
                    a=data.get('agent',{})
                    selected=dict(agent_id=a.get('agent_id'),execution_id=a.get('execution_id'),state=a.get('metadata',{}).get('state'),idle_wake=data.get('idle_wake'),wake_refusal=data.get('wake_refusal'))
                    key=json.dumps(selected,sort_keys=True)
                    if seen.get(str(p)) != key:
                        seen[str(p)]=key
                        emit('state',**selected)
                        if a.get('agent_id'): save('agent.json',a)
                except (OSError,ValueError): pass
            for p in (state_dir/'inbox').glob('*.json'):
                if str(p) in seen: continue
                try:
                    d=json.loads(p.read_text())
                    seen[str(p)]=True
                    emit('context',**{k:d.get(k) for k in ['event_id','delivery_id','attempt_id','native_turn_id','source']})
                except (OSError,ValueError): pass
            stop.wait(.02)
    observer=threading.Thread(target=observe,daemon=True)
    observer.start()
    watcher_log=(root/'watcher.log').open('w')
    watcher=subprocess.Popen([sys.executable,str(engine/'coordination.py'),'watch','--config-dir',str(engine/'bindings')],stdout=watcher_log,stderr=watcher_log)
    save('processes.json',dict(driver_pid=os.getpid(),server_pid=server.pid,watcher_pid=watcher.pid))
    prompt='''You are a dedicated owner-authorized Cairn live latency trial agent on Archon. Work only on bounded synthetic latency requests delivered through your native Cairn context. Do not change services, credentials, source, existing records, or launch agents. Native hooks own registration, claim and renewal; never register or manually claim work.
For each native request: read its exact selected source using its supplied read argv and read_input JSON (or configured cairn_history MCP). Then complete with the exact requested beacon as concise selected result on stdin using the supplied completion argv. Publish one linked response using the supplied response argv and completion result reference. Do not respond recursively to notices. Use the native context's identity and commands exactly. No request retries or replacement conversations. These tiny requests need no repository exploration or unrelated memory searches.
For this initial turn, simply reply READY and finish. The actual host watcher will wake you from idle; do not poll or wait for work yourself.'''
    turn=rpc('turn/start',dict(threadId=thread['id'],input=[dict(type='text',text=prompt,text_elements=[])]))
    save('initial.json',dict(turn_id=turn['turn']['id']))
    deadline=time.monotonic()+3600
    while True:
        if server.poll() is not None: raise RuntimeError('app-server exited')
        if watcher.poll() is not None: raise RuntimeError('watcher exited')
        if not reader.is_alive() or not observer.is_alive(): raise RuntimeError('metadata observer exited')
        if time.monotonic()>deadline: raise RuntimeError('one hour live trial bound reached')
        if (root/'finish').exists():
            # A response can precede native turn completion. Do not stop tools
            # or a retained inbox attempt merely because the sender finished.
            states=[json.loads(p.read_text()) for p in (engine/'state/archon-latency').glob('*.json')]
            last=json.loads((root/'last-turn.json').read_text()) if (root/'last-turn.json').exists() else {}
            latest=json.loads((root/'latest-started-turn.json').read_text()) if (root/'latest-started-turn.json').exists() else {}
            if (states and latest.get('turn_id') and last.get('turn_id')==latest['turn_id'] and last.get('status')=='completed' and
                all(s.get('agent',{}).get('metadata',{}).get('state')=='idle' and
                    not s.get('inbox_attempt') and not s.get('inbox_intent') for s in states)):
                break
        time.sleep(.5)
    emit('finished')
finally:
    stop.set()
    if watcher is not None:
        watcher.terminate()
        try: watcher.wait(timeout=10)
        except subprocess.TimeoutExpired: watcher.kill(); watcher.wait()
    if conn is not None: conn.close()
    server.terminate()
    try: server.wait(timeout=10)
    except subprocess.TimeoutExpired: server.kill(); server.wait()
    installer.engine.watch_once(installer.engine.load_config(installed))
    (home/'auth.json').unlink(missing_ok=True)
    log.close()
