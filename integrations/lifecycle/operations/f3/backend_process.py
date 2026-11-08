"""Concrete fixed subprocess adapters and bounded output custody. No default wiring."""
from dataclasses import dataclass
import os
import selectors
import signal
import subprocess
from backend_binding import BackendUnknown, ssh_argv, systemctl_argv
from domains import integer

MAX_STDOUT=8192
MAX_STDERR=2048


def require_wait_policy():
    """Refuse incompatible wait policy; never change process-global handlers."""
    try:disposition=signal.getsignal(signal.SIGCHLD)
    except Exception:raise BackendUnknown('SIGCHLD_disposition_unknown') from None
    if disposition is not signal.SIG_DFL:raise BackendUnknown('SIGCHLD_default_required')


class WaitOwnedChild:
    """Retain Popen custody and guard each observed poll, including positive reap.

    The source-owned process must retain default SIGCHLD and have no competing
    waiter. Endpoint observations cannot certify intervening signal changes.
    """
    def __init__(self,child):
        self.child=child;self.pid=child.pid
        self.stdin=child.stdin;self.stdout=child.stdout;self.stderr=child.stderr
    def poll(self):
        require_wait_policy();result=self.child.poll();require_wait_policy()
        return result


class SSHProcess:
    def spawn(self,binding,intent_directory):
        if os.geteuid()!=binding.uid:raise BackendUnknown('root_account_changed')
        argv=ssh_argv(binding);require_wait_policy()
        return WaitOwnedChild(subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,cwd=intent_directory,
                                env=dict(binding.root_environment),shell=False,close_fds=True))


class SystemctlProcess:
    def spawn(self,binding,action):
        if os.geteuid()!=binding.uid:raise BackendUnknown('remote_account_changed')
        argv=systemctl_argv(binding,action);require_wait_policy()
        return WaitOwnedChild(subprocess.Popen(argv,stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                cwd=binding.remote_run_parent,env=dict(binding.remote_environment),
                                shell=False,close_fds=True))


@dataclass(frozen=True)
class Completion:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    eof: bool


class BoundedCompletion:
    """Local remote-executor wait only; never converts its clock into Root/store time."""
    def __init__(self,monotonic_ns):self.monotonic_ns=monotonic_ns
    def collect(self,child,budget_ns):
        require_wait_policy()
        try:
            integer(budget_ns,'budget_ns')
            if budget_ns<=0:raise BackendUnknown('completion_budget')
            previous=integer(self.monotonic_ns(),'monotonic_ns');deadline=previous+budget_ns
        except Exception:raise BackendUnknown('completion_clock_or_budget_unknown') from None
        selector=selectors.DefaultSelector();output={'stdout':bytearray(),'stderr':bytearray()}
        try:
            for name in ('stdout','stderr'):
                stream=getattr(child,name)
                if stream is None:raise BackendUnknown('missing_command_pipe')
                os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,name)
            while True:
                current=integer(self.monotonic_ns(),'monotonic_ns')
                if current<previous:raise BackendUnknown('command_clock_order_unknown')
                previous=current;remaining=deadline-current
                if remaining<0:raise BackendUnknown('command_outcome_timeout')
                require_wait_policy();code=child.poll();require_wait_policy()
                if not selector.get_map() and code is not None:
                    return Completion(code,bytes(output['stdout']),bytes(output['stderr']),True)
                if remaining==0:raise BackendUnknown('command_outcome_timeout')
                for key,_ in selector.select(min(remaining/1_000_000_000,.01)):
                    data=os.read(key.fileobj.fileno(),4096)
                    if not data:selector.unregister(key.fileobj);key.fileobj.close();continue
                    output[key.data].extend(data)
                    cap=MAX_STDOUT if key.data=='stdout' else MAX_STDERR
                    if len(output[key.data])>cap:raise BackendUnknown('command_output_limit')
        except BackendUnknown:raise
        except Exception:raise BackendUnknown('command_observation_unknown') from None
        finally:
            selector.close()  # Pipes/child retained by executor on uncertainty; no kill/reap inference.
