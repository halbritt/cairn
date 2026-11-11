"""Receipt-selected /3 caller. The /2 loader and its installed bytes stay separate."""
import errno
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
import types


MEMBERS = ('lifecycle_bundle.py', 'memory.py', 'coordination.py', 'inbox_recall.py',
           'inbox_binding.py', 'grant_ledger.py', 'transcript_recovery.py',
           'unix_peer_transport.py', 'wake_transport.py', 'process_scan.py',
           'codex_queue.py', 'claude_channel.py', 'opencode_queue.py', 'hermes_queue.py',
           'recall_dispatch.py', 'hook_admission.py', 'native_session.py',
           'lifecycle_compatibility.py')

# This stdlib-only literal is pinned in the external launch vector. It verifies
# every byte before executing the receipt's bootstrap, including this module.
VERIFIER_LITERAL = r'''
import time
t0=time.monotonic()
import argparse,contextlib,hashlib,io,json,math,os,re,stat,sys
role='coordination' if '--role' in sys.argv and sys.argv[sys.argv.index('--role')+1:][:1]==['coordination'] else 'memory'
def refuse():
    prefix='Cairn coordination' if role=='coordination' else 'Cairn lifecycle'
    sys.stderr.write(prefix+' diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
    raise SystemExit(1)
def unique(pairs):
    out={}
    for k,v in pairs:
        if k in out: raise ValueError('duplicate receipt key')
        out[k]=v
    return out
def canonical(p):
    return type(p) is str and p.startswith('/') and os.path.normpath(p)==p and '\0' not in p
def digest(s): return type(s) is str and re.fullmatch('[0-9a-f]{64}',s) is not None
def descriptor(d,keys=('path','sha256')):
    if type(d) is not dict or set(d)!=set(keys) or not canonical(d['path']) or not digest(d['sha256']): raise ValueError('descriptor')
def opened(path,limit,binary=False):
    if not canonical(path): raise ValueError('path')
    parts=path[1:].split('/'); parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    walked='/'
    try:
        for part in parts[:-1]:
            fd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
            os.close(parent); parent=fd; walked=os.path.join(walked,part)
            s=os.fstat(parent)
            if s.st_uid not in (0,os.getuid()) or (s.st_mode&0o022 and not (s.st_uid==0 and s.st_mode&stat.S_ISVTX)): raise ValueError('ancestor')
        s=os.fstat(parent)
        if s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700: raise ValueError('private directory')
        fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
    finally: os.close(parent)
    try:
        s=os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_mode&0o022 or (binary and (not s.st_mode&stat.S_IXUSR or s.st_mode&0o7000)): raise ValueError('file')
        if limit is not None and s.st_size>limit: raise ValueError('size')
        chunks=[]; count=0; h=hashlib.sha256()
        while True:
            raw=os.read(fd,65536)
            if not raw: break
            count+=len(raw)
            if limit is not None and count>limit: raise ValueError('size')
            h.update(raw)
            if not binary: chunks.append(raw)
        end=os.fstat(fd)
        identity=lambda x:(x.st_dev,x.st_ino,x.st_mode,x.st_uid,x.st_gid,x.st_size,x.st_mtime_ns,x.st_ctime_ns,x.st_nlink)
        if count!=s.st_size or identity(s)!=identity(end): raise ValueError('changed')
        os.lseek(fd,0,0)
        if binary: return fd,h.hexdigest()
        os.close(fd); return b''.join(chunks),h.hexdigest()
    except BaseException:
        os.close(fd); raise
try:
    p=argparse.ArgumentParser(add_help=False)
    for key in ('receipt','receipt-sha256','role','entrypoint','config','hook-timeout','launch-root','shell'): p.add_argument('--'+key,required=True)
    p.add_argument('tail',nargs=argparse.REMAINDER)
    with contextlib.redirect_stderr(io.StringIO()): a=p.parse_args()
    if a.role not in ('memory','coordination') or not canonical(a.config) or not canonical(a.launch_root) or not canonical(a.shell): raise ValueError('selection')
    H=float(a.hook_timeout)
    if not math.isfinite(H) or H not in ((13.,150.) if a.role=='memory' else (2.,15.)): raise ValueError('timeout')
    raw,sha=opened(a.receipt,1048576)
    if not digest(a.receipt_sha256) or sha!=a.receipt_sha256: raise ValueError('receipt pin')
    r=json.loads(raw.decode('utf-8'),object_pairs_hook=unique)
    fields={'schema','source_revision','snapshot_root','members','bootstrap','loader','roles','aliases','binary','binding','memory_config','coordination_configs'}
    if set(r)!=fields or r['schema']!='cairn.lifecycle-compatibility/1' or r['bootstrap']!='lifecycle_compatibility.py' or r['loader']!='lifecycle_bundle.py' or not canonical(r['snapshot_root']): raise ValueError('receipt')
    if type(r['source_revision']) is not str or any(0xd800<=ord(c)<=0xdfff for c in r['source_revision']): raise ValueError('provenance')
    names={'lifecycle_bundle.py','memory.py','coordination.py','inbox_recall.py','inbox_binding.py','grant_ledger.py','transcript_recovery.py','unix_peer_transport.py','wake_transport.py','process_scan.py','codex_queue.py','claude_channel.py','opencode_queue.py','hermes_queue.py','recall_dispatch.py','hook_admission.py','native_session.py','lifecycle_compatibility.py'}
    if r['roles']!={k:k+'.py' for k in ('memory','coordination','inbox_recall','inbox_binding')} or type(r['aliases']) is not dict: raise ValueError('roles')
    for name,target in r['aliases'].items():
        if type(name) is not str or not name or name in ('.','..') or '/' in name or '\\' in name or '\0' in name or name in names or target not in ('memory.py','coordination.py'): raise ValueError('alias')
    if type(r['members']) is not dict or set(r['members'])!=names|set(r['aliases']): raise ValueError('closure')
    for d in (r['binary'],r['memory_config']): descriptor(d)
    descriptor(r['binding'],('path','sha256','schema'))
    if r['binding']['schema']!='cairn.inbox-recall-binding/3' or type(r['coordination_configs']) is not list or not 1<=len(r['coordination_configs'])<=8: raise ValueError('binding')
    for d in r['coordination_configs']: descriptor(d)
    selected=[r['memory_config']] if a.role=='memory' else r['coordination_configs']
    if a.config not in [d['path'] for d in selected]: raise ValueError('config role')
    if a.entrypoint!=a.role+'.py' and r['aliases'].get(a.entrypoint)!=a.role+'.py': raise ValueError('entrypoint')
    sources={}; hashes={}
    for name,d in r['members'].items():
        if type(d) is not dict or set(d)!={'sha256','bytes'} or type(d['bytes']) is not int or not 0<=d['bytes']<=2097152 or not digest(d['sha256']): raise ValueError('member')
        data,h=opened(os.path.join(r['snapshot_root'],name),2097152)
        if h!=d['sha256'] or len(data)!=d['bytes']: raise ValueError('member pin')
        sources[name]=data; hashes[name]=h
    identity=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
    if os.path.basename(r['snapshot_root'])!=identity: raise ValueError('snapshot')
    for name,target in r['aliases'].items():
        if sources[name]!=sources[target]: raise ValueError('alias pin')
    fd,h=opened(r['binary']['path'],None,True)
    if h!=r['binary']['sha256']: os.close(fd); raise ValueError('binary pin')
    tail=a.tail
    if tail and tail[0]!='--': raise ValueError('tail separator')
    a.tail=tail[1:] if tail else []
    original=sys.orig_argv
    vector=original[:original.index('--')] if '--' in original else original[:]
    token=object()
    namespace={'__name__':'cairn_checked_bootstrap','__file__':os.path.join(r['snapshot_root'],r['bootstrap']),'_CAIRN_COMPATIBILITY_CONTEXT':token}
    exec(compile(sources[r['bootstrap']],namespace['__file__'],'exec'),namespace)
except BaseException: refuse()
try:
    raise SystemExit(namespace['run_checked'](a,r,sources,fd,t0+H,vector,opened))
finally: os.close(fd)
'''


class CompatibilityPreflightError(Exception):
    pass


class ParentSignal(BaseException):
    def __init__(self, number): self.number = number


def guarded_sources(source):
    """Return bytes for a NEW immutable /3 snapshot; never rewrite a /2 bundle."""
    result = {}
    for name in MEMBERS:
        role = 'Cairn coordination' if name=='coordination.py' else 'Cairn lifecycle'
        prefix = ('if not globals().get("_CAIRN_COMPATIBILITY_CONTEXT"):\n'
                  '    import sys\n'
                  '    sys.stderr.write(' + repr(role+' diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n') + ')\n'
                  '    raise SystemExit(1)\n').encode()
        result[name] = prefix + (Path(source) / name).read_bytes()
    return result


def launch_vector(python, receipt, receipt_sha256, role, entrypoint, config, timeout, launch_root, shell='/bin/sh'):
    return [str(python), '-I', '-S', '-c', VERIFIER_LITERAL, '--receipt', str(receipt),
            '--receipt-sha256', receipt_sha256, '--role', role, '--entrypoint', entrypoint,
            '--config', str(config), '--hook-timeout', str(timeout), '--launch-root', str(launch_root), '--shell', shell]


def launcher_bytes(vector, shell='/bin/sh'):
    if not os.path.isabs(shell) or any(c.isspace() or ord(c)<32 for c in shell):
        raise ValueError('launcher shell requires an absolute single pathname')
    if len(vector)<4 or not os.path.isabs(vector[0]) or vector[1:4]!=['-I','-S','-c']:
        raise ValueError('launcher requires the isolated approved Python vector')
    return ('#!' + shell + '\nexec ' + shlex.join(vector) + ' -- "$@"\n').encode()


class InvocationContext:
    def __init__(self, args, receipt, sources, binary_fd, deadline, vector, opened):
        self.args, self.receipt, self.sources = args, receipt, sources
        self.binary_fd, self.deadline, self.vector, self.opened = binary_fd, deadline, vector, opened
        self.current_raw = self.current = self.manifest = self.memory = self.coordinators = None
        self.enabled = None
        self.bundle = None
        self.hook = not (args.role == 'coordination' and args.tail[:1] == ['inbox'])
        self.launcher_raw = launcher_bytes(vector, args.shell)
        self.launcher = Path(args.launch_root) / hashlib.sha256(self.launcher_raw).hexdigest() / 'inbox'

    def config(self, path):
        if str(path) != self.args.config:
            raise CompatibilityPreflightError()
        if self.current is None:
            self.current_raw = self.controlled_data(path)
            self.current = json.loads(self.current_raw.decode('utf-8'))
        return self.current

    def controlled_data(self, path, private=False):
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & (0o077 if private else 0o022):
                raise ValueError('binding data is not owner-controlled')
            raw = source.read(1048577)
            after = os.fstat(source.fileno())
            identity = lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
            if len(raw)>1048576 or len(raw)!=info.st_size or identity(info)!=identity(after):
                raise ValueError('binding data changed or exceeds limit')
            return raw

    def effective_config(self, config, event, raw):
        if config is not self.current or type(raw) is not bytes:
            raise ValueError('checked parent observation required')
        if self.enabled is not None:
            raise RuntimeError('activation already resolved')
        name = event.get('hook_event_name')
        # H is selected in the external vector. Check the installed row when
        # one exists; an unknown/excluded callback still reaches source
        # admission without using its event data to choose a fresh timeout.
        rows = ({'SessionStart':13,'UserPromptSubmit':13,'PreCompact':150,
                 **({'Stop':150} if config.get('harness')=='codex' else
                    {'SessionEnd':150,'PostToolUse':13,'PostToolUseFailure':13})}
                if self.args.role=='memory' else
                {'SessionStart':15,'UserPromptSubmit':15,'Stop':15,'SessionEnd':2,'PostToolUse':2,
                 **({'Interrupt':2} if config.get('harness')=='codex' else {})})
        expected = rows.get(name)
        if expected is not None and float(self.args.hook_timeout) != expected:
            raise CompatibilityPreflightError()
        self.enabled = self.activation(raw)
        if not self.enabled:
            result = dict(config)
            result.pop('inbox_recall_binding', None)
            return result
        self.checked_binding()
        return config

    def activation(self, raw):
        argv = [self.receipt['binary']['path'], 'lifecycle', 'binding', 'activation',
                '--entrypoint', self.args.role, '--config', self.args.config,
                '--config-sha256', hashlib.sha256(self.current_raw).hexdigest(),
                '--binding-sha256', self.receipt['binding']['sha256'],
                '--receipt', self.args.receipt, '--receipt-sha256', self.args.receipt_sha256,
                '--deadline', str(self.deadline)]
        out, err, code = self.child(argv, raw)
        return self.activation_result(out, err, code)

    def memory_start(self, config, event, raw):
        """The selected /3 Codex start cell is wholly executed by checked Go."""
        if not (self.args.role == 'memory' and config.get('harness') == 'codex'
                and config.get('recall_mode') == 'agent_tools'
                and config.get('inbox_recall_binding')
                and event.get('hook_event_name') in ('SessionStart', 'UserPromptSubmit')):
            return None
        if config is not self.current or type(raw) is not bytes or self.enabled is not None:
            raise ValueError('checked parent observation required')
        if float(self.args.hook_timeout) != 13:
            raise CompatibilityPreflightError()
        argv = [self.receipt['binary']['path'], 'lifecycle', 'memory', 'recall',
                '--entrypoint', 'memory', '--config', self.args.config,
                '--config-sha256', hashlib.sha256(self.current_raw).hexdigest(),
                '--binding-sha256', self.receipt['binding']['sha256'],
                '--receipt', self.args.receipt, '--receipt-sha256', self.args.receipt_sha256,
                '--deadline', str(self.deadline)]
        out, err, code = self.child(argv, raw, stdout_limit=1024*1024)
        if code != 0:
            # Valid source exceptions reach _main's read-only requirement guard.
            # Malformed transport frames remain SubprocessError and never promote.
            self.activation_result(out, err, code)
            raise subprocess.SubprocessError('invalid memory reply')
        try:
            frame = json.loads(out.decode('utf-8'))
            if (err or set(frame) != {'stdout', 'stderr', 'exit_code', 'trace'}
                    or type(frame['stdout']) is not str or type(frame['stderr']) is not str
                    or type(frame['exit_code']) is not int or frame['exit_code'] not in (0, 1, 2)
                    or len(frame['stdout'].encode()) > 512*1024
                    or len(frame['stderr'].encode()) > 4096):
                raise ValueError('invalid memory result')
            trace = frame['trace']
            if (type(trace) is not dict
                    or set(trace) - {'phase','stage','outcome','error_class','cause','bound','context_bytes'}
                    or any(type(v) is not str or len(v) > 64 for k,v in trace.items()
                           if k not in ('bound','context_bytes'))
                    or type(trace.get('bound')) is not bool
                    or ('context_bytes' in trace and (type(trace['context_bytes']) is not int
                        or not 0 <= trace['context_bytes'] <= 1024*1024))):
                raise ValueError('invalid memory trace')
            if frame['exit_code'] == 2 and not (
                    event.get('hook_event_name') == 'UserPromptSubmit'
                    and frame['stdout'] == '' and trace.get('outcome') == 'required_refused'
                    and frame['stderr'] == ('Cairn lifecycle diagnostic: phase=handle error_class='
                         + trace.get('error_class', '') + '\n'
                         + 'Cairn: current required context could not be restored.\n')):
                raise ValueError('invalid required memory result')
        except (ValueError, TypeError, UnicodeError):
            raise subprocess.SubprocessError('invalid memory reply') from None
        if frame['trace']['bound']:
            self.checked_binding()
        self.enabled = True  # this invocation has consumed its sole Go dispatch
        return frame

    def activation_result(self, out, err, code):
        if code == 0 and err == b'' and out in (b'{"enabled":true}\n', b'{"enabled":false}\n'):
            return out == b'{"enabled":true}\n'
        if code != 1 or out or len(err) > 4096:
            raise subprocess.SubprocessError('invalid activation reply')
        try:
            frame = json.loads(err.decode('utf-8'))
            error = frame['error']
            if set(frame) != {'error'} or set(error) != {'class', 'reason', 'errno'} or error['reason'] not in ('activation', 'compatibility_receipt'):
                raise ValueError()
            if json.dumps(frame, separators=(',', ':'), ensure_ascii=True).encode() + b'\n' != err:
                raise ValueError()
            if error['errno'] is not None and (type(error['errno']) is not int or error['errno'] < 0):
                raise ValueError()
            classes = {name: getattr(__import__('builtins'), name) for name in (
                'ValueError', 'TypeError', 'KeyError', 'AttributeError', 'OSError', 'FileNotFoundError',
                'PermissionError', 'BlockingIOError', 'FileExistsError', 'NotADirectoryError', 'IsADirectoryError', 'TimeoutError')}
            if error['class'] == 'CompatibilityPreflightError' and error['reason'] == 'compatibility_receipt' and error['errno'] is None:
                raise CompatibilityPreflightError()
            if error['class'] in ('JSONDecodeError', 'UnicodeDecodeError', 'UnicodeEncodeError'):
                # Preserve the actual source family without forwarding child text.
                classes.update(JSONDecodeError=json.JSONDecodeError, UnicodeDecodeError=UnicodeDecodeError, UnicodeEncodeError=UnicodeEncodeError)
            cls = classes[error['class']]
        except CompatibilityPreflightError:
            raise
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise subprocess.SubprocessError('invalid activation reply') from None
        if issubclass(cls, OSError):
            raise cls(error['errno'], 'lifecycle activation unavailable')
        if cls is json.JSONDecodeError:
            raise cls('lifecycle activation input', '', 0)
        if cls is UnicodeDecodeError:
            raise cls('utf-8', b'\xff', 0, 1, 'lifecycle activation input')
        if cls is UnicodeEncodeError:
            raise cls('utf-8', '\ud800', 0, 1, 'lifecycle activation input')
        raise cls('lifecycle activation input')

    def child(self, argv, raw, *, stdout_limit=64):
        if time.monotonic() >= self.deadline:
            raise TimeoutError(errno.ETIMEDOUT, 'lifecycle activation deadline')
        if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
            # Popen maps ECHILD to status zero when SIGCHLD is ignored. That
            # cannot establish the private protocol's required successful exit.
            raise subprocess.SubprocessError('activation wait status unavailable')
        child = None
        previous = {}
        interrupted = None
        failure = None
        result = None
        cleaning = False
        def interrupt(number, frame):
            nonlocal interrupted
            interrupted = number
            # During Popen construction retain the signal until its owned PID
            # is available, then stop/reap it before preserving the host signal.
            if child is not None and not cleaning: raise ParentSignal(number)
        try:
            if threading.current_thread() is threading.main_thread():
                for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
                    old = signal.getsignal(number)
                    if old in (signal.SIG_DFL, signal.default_int_handler):
                        previous[number] = old
                        signal.signal(number, interrupt)
            if interrupted is not None: raise ParentSignal(interrupted)
            child = subprocess.Popen(argv, executable='/proc/self/fd/' + str(self.binary_fd),
                                     pass_fds=(self.binary_fd,), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if interrupted is not None: raise ParentSignal(interrupted)
            result = self.child_io(child, raw, stdout_limit=stdout_limit)
        except ParentSignal as stop:
            interrupted = stop.number
        except BaseException as error:
            failure = error
        finally:
            cleaning = True
            if child is not None:
                if child.poll() is None: child.kill()
                child.wait()
                for pipe in (child.stdin, child.stdout, child.stderr): pipe.close()
            for number, old in previous.items(): signal.signal(number, old)
        if interrupted is not None:
            os.kill(os.getpid(), interrupted)
            raise SystemExit(128 + interrupted)
        if failure is not None: raise failure
        return result

    def child_io(self, child, raw, *, stdout_limit=64):
        streams = {child.stdout: (bytearray(), stdout_limit), child.stderr: (bytearray(), 4096)}
        selector = selectors.DefaultSelector()
        for pipe in streams:
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ)
        os.set_blocking(child.stdin.fileno(), False)
        selector.register(child.stdin, selectors.EVENT_WRITE)
        sent = 0
        try:
            while selector.get_map():
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(errno.ETIMEDOUT, 'lifecycle activation deadline')
                for key, _ in selector.select(remaining):
                    pipe = key.fileobj
                    if pipe is child.stdin:
                        if sent == len(raw):
                            selector.unregister(pipe); pipe.close()
                        else:
                            try: sent += os.write(pipe.fileno(), raw[sent:sent+16384])
                            except BrokenPipeError:
                                selector.unregister(pipe); pipe.close()
                    else:
                        data, maximum = streams[pipe]
                        chunk = os.read(pipe.fileno(), maximum + 1 - len(data))
                        if not chunk:
                            selector.unregister(pipe)
                        else:
                            data.extend(chunk)
                            if len(data) > maximum:
                                raise subprocess.SubprocessError('activation reply exceeds bound')
            remaining = self.deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError(errno.ETIMEDOUT, 'lifecycle activation deadline')
            try: code = child.wait(timeout=remaining)
            except subprocess.TimeoutExpired: raise TimeoutError(errno.ETIMEDOUT, 'lifecycle activation deadline') from None
            return bytes(streams[child.stdout][0]), bytes(streams[child.stderr][0]), code
        finally:
            selector.close()

    def checked_binding(self):
        if self.manifest is not None: return
        self.config(self.args.config)
        path = self.receipt['binding']['path']
        raw = self.controlled_data(path, private=True)
        if hashlib.sha256(raw).hexdigest() != self.receipt['binding']['sha256']:
            raise ValueError('inbox recall binding changed')
        self.manifest = json.loads(raw)
        if (not isinstance(self.manifest,dict) or self.manifest.get('schema')!='cairn.inbox-recall-binding/3'
                or type(self.manifest.get('enabled')) is not bool):
            raise ValueError('unsupported inbox recall binding')
        for key in ('binary', 'memory_config', 'coordination_configs'):
            if self.manifest[key] != self.receipt[key]: raise ValueError('mixed binding and receipt selection')
        configs = []
        for descriptor in [self.receipt['memory_config'], *self.receipt['coordination_configs']]:
            raw = self.current_raw if descriptor['path'] == self.args.config else self.controlled_data(descriptor['path'])
            if hashlib.sha256(raw).hexdigest() != descriptor['sha256']: raise ValueError('inbox recall binding changed')
            configs.append(self.current if descriptor['path'] == self.args.config else json.loads(raw.decode('utf-8')))
        self.memory, self.coordinators = configs[0], configs[1:]
        self.bundle.load('inbox_binding').validate_configuration_profiles(self.current, path, self.memory, self.coordinators)

    def remaining(self, limit):
        if not self.hook: return limit
        remaining = self.deadline - time.monotonic()
        if remaining <= 0: raise TimeoutError(errno.ETIMEDOUT, 'lifecycle hook deadline')
        return min(limit, remaining)

    def cap_deadline(self, deadline):
        return min(deadline,self.deadline) if self.hook else deadline

    def coordinates_wake(self, config, event):
        self.checked_binding()
        admission = self.bundle.load('hook_admission')
        active = admission.active_home(config['harness']).resolve()
        matches = [c for c in self.coordinators if not c.get('config_home') or active == Path(c['config_home']).resolve()]
        if len(matches)>1: raise ValueError('ambiguous coordination profile')
        if not matches or admission.disabled('inbox_recall'): return False
        if admission.privacy_exclusions(admission.privacy_roots(event, explicit_project=False), (admission.COORDINATION_MARKER,)): return False
        return not admission.other_account_home(matches[0])

    def validate(self, config, require_enabled=False):
        self.checked_binding()
        enabled = self.enabled if self.enabled is not None else self.manifest['enabled']
        if require_enabled and not enabled: raise ValueError('inbox recall binding is disabled')
        if config not in [self.memory, *self.coordinators]: raise ValueError('incompatible inbox recall profile binding')
        return self.bundle.load('memory'), self.memory

    def issued_argv(self, tail):
        raw, _ = self.opened(str(self.launcher), 65536)
        if raw != self.launcher_raw: raise CompatibilityPreflightError()
        if stat.S_IMODE(os.stat(self.launcher,follow_symlinks=False).st_mode)!=0o500:
            raise CompatibilityPreflightError()
        result = [str(self.launcher), *map(str, tail)]
        if len(shlex.join(result).encode()) > 512: raise ValueError('issued lifecycle command exceeds budget')
        return result

    def control(self, text):
        if len(text.encode()) > 2048: raise ValueError('lifecycle control exceeds budget')
        return text

    def context_commands(self, context):
        rendered = {key:shlex.join(context[key]) for key in
                    ('read','completion','acknowledgement','response','finish_reply') if key in context}
        if any(len(text.encode())>512 for text in rendered.values()) or sum(len(text.encode()) for text in rendered.values())>2048:
            raise ValueError('issued lifecycle command presentation exceeds budget')
        context['shell_commands'] = rendered

    def bind_lookup(self, root, binding=None, config_path=None):
        self.checked_binding()
        selected = Path(self.current['state_dir']).resolve()
        if root.resolve() != selected.parent.parent:
            raise CompatibilityPreflightError()
        if binding is not None and binding != self.current['binding']:
            raise CompatibilityPreflightError()
        if config_path is not None and config_path.resolve() != Path(self.args.config).resolve():
            raise CompatibilityPreflightError()

    def bind_context_path(self, path):
        self.checked_binding()
        if path.resolve().parent != (Path(self.current['state_dir'])/'inbox').resolve():
            raise CompatibilityPreflightError()

    def bind_context(self, context):
        if context.get('state_dir') != self.current['state_dir']:
            raise CompatibilityPreflightError()
        for index, command in enumerate([context.get('read'), *context.get('commands', {}).values()]):
            if not isinstance(command, list) or not command or command[0] != self.current['cairn']:
                raise CompatibilityPreflightError()
            for flag,key in (('--socket','socket'),('--token-file','token_file')):
                if command.count(flag)!=1 or command[command.index(flag)+1:][:1] != [self.current[key]]:
                    raise CompatibilityPreflightError()
            # Inner store argv keep their retained machine form. The model
            # executes the bounded outer wrapper, or the direct read command.
            if index == 0 and len(shlex.join(command).encode())>512:
                raise ValueError('issued lifecycle command exceeds budget')


def run_checked(args, receipt, sources, binary_fd, deadline, vector, opened):
    context = InvocationContext(args, receipt, sources, binary_fd, deadline, vector, opened)
    loader = types.ModuleType('cairn_checked_loader')
    loader.__file__ = str(Path(receipt['snapshot_root']) / 'lifecycle_bundle.py')
    loader._CAIRN_COMPATIBILITY_CONTEXT = context
    exec(compile(sources['lifecycle_bundle.py'], loader.__file__, 'exec'), loader.__dict__)
    bundle = loader.Bundle(receipt['snapshot_root'], sources={name: sources[name] for name in MEMBERS if name != 'lifecycle_compatibility.py'})
    bundle.compatibility_context = context
    context.bundle = bundle
    try:
        tail = args.tail or (['--config', args.config] if args.role == 'memory' else ['hook', '--config', args.config])
        for index, arg in enumerate(tail):
            if arg == '--config' and (index + 1 == len(tail) or tail[index+1] != args.config): raise CompatibilityPreflightError()
            if arg.startswith('--config=') and arg != '--config='+args.config: raise CompatibilityPreflightError()
            if arg.split('=',1)[0] in ('--receipt','--receipt-sha256','--role','--entrypoint','--hook-timeout','--launch-root','--shell'):
                raise CompatibilityPreflightError()
        if args.role=='memory' and tail!=['--config',args.config]: raise CompatibilityPreflightError()
        if args.role == 'coordination' and tail[0] not in ('hook', 'inbox'): raise CompatibilityPreflightError()
        role = bundle.load(args.role, entry_path=Path(receipt['snapshot_root']) / args.entrypoint)
        sys.argv = [role.__file__, *tail]
        return role.main()
    except CompatibilityPreflightError:
        prefix = 'Cairn coordination' if args.role == 'coordination' else 'Cairn lifecycle'
        sys.stderr.write(prefix + ' diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n')
        return 1
