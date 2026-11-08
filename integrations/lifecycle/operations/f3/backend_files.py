"""Pinned regular-file checks and exact private intent readbacks; no host selection."""
import hashlib
import json
import os
from pathlib import Path
import stat
from backend_binding import BackendUnknown, absolute
from effects import Intent, canonical, unique_object


def regular_bytes(path,uid,mode,limit):
    path=Path(absolute(str(path)))
    for component in (path,*path.parents):
        if stat.S_ISLNK(component.lstat().st_mode):raise BackendUnknown('symlink_component')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid!=uid or stat.S_IMODE(before.st_mode)!=mode or before.st_size>limit:
            raise BackendUnknown('file_metadata')
        data=bytearray()
        while len(data)<=limit:
            chunk=os.read(fd,min(65536,limit+1-len(data)))
            if not chunk:break
            data.extend(chunk)
        after=os.fstat(fd)
        signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_uid,s.st_mode)
        if len(data)>limit or signature(before)!=signature(after):raise BackendUnknown('file_changed_or_oversize')
        return bytes(data)
    finally:os.close(fd)


def private_directory(path,uid):
    p=Path(path);s=p.lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_uid!=uid or stat.S_IMODE(s.st_mode)!=0o700:raise BackendUnknown('private_directory')
    for part in p.parents:
        if stat.S_ISLNK(part.lstat().st_mode):raise BackendUnknown('symlink_parent')


class PinVerifier:
    def verify(self,pin):
        pin.validate();data=regular_bytes(pin.path,pin.uid,pin.mode,pin.max_bytes)
        if hashlib.sha256(data).hexdigest()!=pin.sha256:raise BackendUnknown('file_pin_changed')

    def source_closure(self,binding):
        binding.validate();directory=Path(binding.remote_executor.path).parent
        private_directory(directory,binding.uid)
        expected={Path(p.path).name for p in binding.remote_source_files}
        if {p.name for p in directory.iterdir()}!=expected:raise BackendUnknown('source_directory_closure')
        for pin in binding.remote_source_files:self.verify(pin)


def exact_private(path,value,uid):
    if regular_bytes(path,uid,0o600,8192)!=canonical(value)+b'\n':raise BackendUnknown('intent_readback')


def root_intent(intent,value,binding):
    if not isinstance(intent,Intent) or intent.run_id!=binding.run_id or intent.binding_sha256!=binding.digest():
        raise BackendUnknown('root_intent_UNBOUND')
    private_directory(intent.directory,binding.uid);private_directory(intent.directory.parent,binding.uid)
    if list(intent.directory.glob('*-uncertain.json')):raise BackendUnknown('root_run_uncertain_no_reopen')
    exact_private(intent.directory/'0001-intent.json',{'run_id':binding.run_id,'binding_sha256':binding.digest(),'replay_authorized':False},binding.uid)
    matches=list(intent.directory.glob('*-effect-requested.json'))
    selected=[]
    for path in matches:
        raw=regular_bytes(path,binding.uid,0o600,8192)
        try:body=json.loads(raw,object_pairs_hook=unique_object)
        except (ValueError,UnicodeError):raise BackendUnknown('requested_intent_JSON') from None
        if body.get('effect_id')==value['effect_id']:selected.append((path,body))
    if len(selected)!=1 or selected[0][1]!=value:raise BackendUnknown('durable_requested_intent_missing')
    exact_private(selected[0][0],value,binding.uid)
