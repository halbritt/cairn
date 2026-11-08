"""Source-selected deployment inputs. No wire input can select an endpoint/path."""
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import PurePosixPath
import re
import shlex
from domains import integer
from effects import Action

UNIT='cairn-presence.service'
SOURCE_NAMES=frozenset(('backend_binding.py','backend_entry.py','backend_files.py',
    'backend_process.py','backend_proof.py','backend_wire.py','remote_executor.py',
    'root_launcher.py','claims.py','domains.py','effects.py','journal.py','runner.py',
    'sequence.py','transport.py'))


class BackendUnknown(Exception): pass


def safe_id(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value):
        raise BackendUnknown('identity_domain')
    return value


def absolute(value):
    if type(value) is not str or not value.startswith('/') or any(x in value for x in ('\x00','\n','\r')):
        raise BackendUnknown('absolute_path_UNBOUND')
    if '..' in PurePosixPath(value).parts:raise BackendUnknown('parent_path_component')
    return value


@dataclass(frozen=True)
class FilePin:
    path: str
    sha256: str
    uid: int
    mode: int
    max_bytes: int
    def validate(self):
        absolute(self.path)
        if type(self.sha256) is not str or not re.fullmatch('[a-f0-9]{64}',self.sha256):raise BackendUnknown('file_digest_UNBOUND')
        integer(self.uid,'file_uid');integer(self.mode,'file_mode');integer(self.max_bytes,'file_max_bytes')
        if self.mode>0o777 or self.mode&0o022 or not 0<self.max_bytes<=64*1024*1024:raise BackendUnknown('file_metadata_domain')


@dataclass(frozen=True)
class Binding:
    run_id: str
    uid: int
    ssh: FilePin
    ssh_config: FilePin
    known_hosts: FilePin
    ssh_target: str
    remote_python: FilePin
    remote_executor: FilePin
    remote_systemctl: FilePin
    remote_source_files: tuple
    remote_run_parent: str
    root_environment: tuple
    remote_environment: tuple
    old_identity_digest: str
    selection_digest: str
    def validate(self):
        safe_id(self.run_id);integer(self.uid,'uid');absolute(self.remote_run_parent)
        if len(self.run_id)>80:raise BackendUnknown('run_identity_length')
        for p in (self.ssh,self.ssh_config,self.known_hosts,self.remote_python,self.remote_executor,self.remote_systemctl):
            if not isinstance(p,FilePin):raise BackendUnknown('file_pin_UNBOUND')
            p.validate()
        if type(self.remote_source_files) is not tuple or len(self.remote_source_files)!=len(SOURCE_NAMES):
            raise BackendUnknown('remote_source_closure_UNBOUND')
        directory=PurePosixPath(self.remote_executor.path).parent
        if PurePosixPath(self.remote_executor.path).name!='backend_entry.py':raise BackendUnknown('fixed_executor_entry')
        names=set()
        for p in self.remote_source_files:
            if not isinstance(p,FilePin):raise BackendUnknown('source_pin_UNBOUND')
            p.validate();path=PurePosixPath(p.path)
            if path.parent!=directory or p.uid!=self.uid or p.mode!=0o600:raise BackendUnknown('source_closure_metadata')
            names.add(path.name)
        if names!=SOURCE_NAMES or self.remote_executor not in self.remote_source_files:raise BackendUnknown('source_closure_members')
        if type(self.ssh_target) is not str or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,150}',self.ssh_target):raise BackendUnknown('SSH_target_UNBOUND')
        for value in (self.old_identity_digest,self.selection_digest):
            if type(value) is not str or not re.fullmatch('[a-f0-9]{64}',value):raise BackendUnknown('proof_binding_UNBOUND')
        for env in (self.root_environment,self.remote_environment):
            if type(env) is not tuple or len(dict(env))!=len(env):raise BackendUnknown('environment_UNBOUND')
            for key,value in env:
                if key not in ('PATH','LANG','SSH_AUTH_SOCK','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS') or type(value) is not str or any(c in value for c in ('\x00','\n','\r')):
                    raise BackendUnknown('environment_domain')
    def digest(self):
        self.validate()
        body={'binding':asdict(self),'unit':UNIT,'schema':'cairn.fixed-backend-binding/1'}
        return hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def ssh_argv(binding):
    binding.validate()
    remote=shlex.join((binding.remote_python.path,'-I','-B',binding.remote_executor.path))
    return (binding.ssh.path,'-F',binding.ssh_config.path,'-T','-o','BatchMode=yes',
            '-o','StrictHostKeyChecking=yes','-o','UserKnownHostsFile='+binding.known_hosts.path,
            '-o','ProxyCommand=none','-o','ProxyJump=none','-o','PermitLocalCommand=no',
            '-o','KnownHostsCommand=none','-o','GlobalKnownHostsFile=/dev/null',
            '-o','VerifyHostKeyDNS=no','-o','UpdateHostKeys=no','-o','ForwardAgent=no',
            '-o','ForwardX11=no','-o','ClearAllForwardings=yes','-o','RequestTTY=no',
            '-o','EscapeChar=none',
            '-o','ControlMaster=no','-o','ControlPath=none','-o','ConnectionAttempts=1',
            '-o','ConnectTimeout=2','--',binding.ssh_target,remote)


def systemctl_argv(binding,action):
    binding.validate()
    fixed={Action.SIGNAL_MAIN:('kill','--kill-whom=main','--signal=SIGTERM',UNIT),
           Action.RELOAD_MANAGER:('daemon-reload',),Action.START_SELECTED:('start',UNIT)}
    if not isinstance(action,Action):raise BackendUnknown('fixed_action_required')
    return (binding.remote_systemctl.path,'--user',*fixed[action])


def configured_binding():
    # A future reviewed source selection must supply every pin. No enable flag,
    # binding file, environment switch or caller-supplied principal is accepted.
    raise BackendUnknown('operational_binding_UNBOUND')
