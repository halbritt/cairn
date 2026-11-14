"""Private non-consequential current-observation collection; never starts Agy.

The owning fixture calls capture_owned_generation when starting/attaching its
trace, before requesting any scripted turn. This module never requests a hook,
refreshes a client, registers a session, calls an API or rewrites trace bytes.
"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    'r06_existing_unix_peer_transport', ROOT/'integrations/lifecycle/unix_peer_transport.py')
_process = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_process)


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


def pin(path):
    path=Path(path)
    info=path.lstat()
    if (not path.is_absolute() or str(path)!=os.path.normpath(str(path))
            or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_uid!=os.getuid()):
        raise ValueError('owned observation requires an absolute controlled regular file')
    return dict(Path=str(path),SHA256=sha(path))


def capture_owned_generation(pid):
    """Read an owned descendant at trace start/attach, without wait/signal/API."""
    if type(pid) is not int or pid<=1 or pid==os.getpid():
        raise ValueError('an owned child PID greater than one is required')
    current=pid
    for _ in range(128):
        if current==os.getpid():
            break
        if current<=1 or Path(f'/proc/{current}').stat().st_uid!=os.getuid():
            raise ValueError('process is not this collector\'s owned descendant')
        current=_process.process_reference(current)['parent']
    else:
        raise ValueError('owned ancestry is incomplete')
    if current!=os.getpid():
        raise ValueError('process is not owned by this collector')
    first=_process.process_reference(pid)
    if first['state'] in ('Z','X'):
        raise ValueError('process is not live at capture')
    executable=Path(os.readlink(f'/proc/{pid}/exe'))
    argv_raw=Path(f'/proc/{pid}/cmdline').read_bytes()
    if not argv_raw.endswith(b'\0'):
        raise ValueError('incomplete native argv')
    argv=[part.decode('utf-8') for part in argv_raw[:-1].split(b'\0')]
    image=pin(executable)
    last=_process.process_reference(pid)
    if last['state'] in ('Z','X') or any(first[key]!=last[key] for key in ('pid','start','boot')):
        raise ValueError('process generation changed during capture')
    return dict(Process=dict(PID=pid,Start=str(last['start']),Boot=last['boot'],
                             Parent=last['parent'],PGID=os.getpgid(pid)),
                Client=image,ClientArgv=argv)


def write_generation_pair(destination,before,after):
    """Seal two actual start/attach captures; timestamps/mode flags are absent."""
    destination=Path(destination)
    if destination.parent.stat().st_mode & 0o077:
        raise ValueError('private 0700 observation directory required')
    body=dict(Schema='cairn.agy-collector-generation/1',Before=before,After=after)
    with destination.open('x',encoding='utf-8') as output:
        output.write(json.dumps(body,sort_keys=True,indent=2)+'\n')
    destination.chmod(0o400)
    return pin(destination)


def trace_interval(path,start,end,callbacks):
    """Pin exact complete raw bytes; offsets and references are only locators."""
    path=Path(path)
    if path.lstat().st_size>256*1024*1024:
        raise ValueError('raw trace exceeds 256 MiB')
    selected=pin(path)
    raw=path.read_bytes()
    if not 0<=start<end==len(raw)<=256*1024*1024 or raw[end-1:]!=b'\n' or start and raw[start-1:start]!=b'\n':
        raise ValueError('complete line-aligned raw interval required')
    if hashlib.sha256(raw).hexdigest()!=selected['SHA256']:
        raise ValueError('trace changed during collection')
    return dict(Trace=selected,Start=start,End=end,Callbacks=callbacks)
