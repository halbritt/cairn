"""Fixed remote command executor. CLI/default binding remains unavailable.

Run directories must be prepared separately under a future reviewed grant. Each
action has an exclusive parent-fsynced intent. Incomplete on-disk prior actions
refuse, but visible completion bytes can survive a durability error. The exclusive
Root caller's uncertainty fence governs the composed run; no standalone reopening
or recovery is supplied. This is not same-UID adversarial attestation.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
from backend_binding import BackendUnknown
from backend_files import exact_private, private_directory, regular_bytes
from backend_wire import decode, outcome
from backend_process import Completion, MAX_STDOUT, MAX_STDERR, require_wait_policy
from effects import Action, Intent, SECOND, canonical, unique_object


ORDER=(Action.SIGNAL_MAIN,Action.RELOAD_MANAGER,Action.START_SELECTED)


class RemoteRun:
    def __init__(self,binding):
        binding.validate();self.binding=binding;self.directory=Path(binding.remote_run_parent)/binding.run_id
    @classmethod
    def prepare(cls,binding):
        obj=cls(binding)
        if os.geteuid()!=binding.uid:raise BackendUnknown('remote_account_changed')
        # Separate source-only preparation method, not a wire action or an enabled CLI.
        Intent(obj.directory,binding.run_id,binding.digest())
        return obj
    def verify(self):
        private_directory(self.directory,self.binding.uid);private_directory(self.directory.parent,self.binding.uid)
        exact_private(self.directory/'0001-intent.json',{'run_id':self.binding.run_id,'binding_sha256':self.binding.digest(),'replay_authorized':False},self.binding.uid)
        allowed={'0001-intent.json'}|{a.value for a in ORDER}
        if any(p.name not in allowed for p in self.directory.iterdir()):raise BackendUnknown('run_state_unknown')
    def completed(self,action):
        folder=self.directory/action.value;private_directory(folder,self.binding.uid)
        names={'0001-intent.json','0002-remote-requested.json','0003-remote-complete.json'}
        if {p.name for p in folder.iterdir()}!=names:raise BackendUnknown('prior_action_incomplete')
        value=json.loads(regular_bytes(folder/'0002-remote-requested.json',self.binding.uid,0o600,4096),object_pairs_hook=unique_object)
        from backend_wire import request
        value=request(value,self.binding)
        if value['action']!=action.value:raise BackendUnknown('prior_action_identity')
        exact_private(folder/'0001-intent.json',{'run_id':self.binding.run_id,'binding_sha256':self.binding.digest(),'replay_authorized':False},self.binding.uid)
        exact_private(folder/'0003-remote-complete.json',outcome(value),self.binding.uid)
    def reserve(self,value):
        self.verify();action=Action(value['action']);index=ORDER.index(action)
        for prior in ORDER[:index]:self.completed(prior)
        if any((self.directory/later.value).exists() for later in ORDER[index:]):raise BackendUnknown('action_already_consumed_or_out_of_order')
        intent=Intent(self.directory/action.value,self.binding.run_id,self.binding.digest())
        intent.write('remote-requested',value)
        return intent


@dataclass(frozen=True)
class Reply:
    returncode: int
    stdout: bytes
    stderr: bytes


class RemoteExecutor:
    def __init__(self,binding=None,run=None,verifier=None,proof=None,process=None,completion=None):
        self.binding=binding;self.run=run;self.verifier=verifier;self.proof=proof
        self.process=process;self.completion=completion;self.children={};self.stopped=False
    def execute_wire(self,data):
        try:
            if self.stopped:raise BackendUnknown('executor_uncertain_no_reopen')
            if any(x is None for x in (self.binding,self.run,self.verifier,self.proof,self.process,self.completion)):
                raise BackendUnknown('executor_dependencies_UNBOUND')
            value=decode(data,self.binding);action=Action(value['action'])
            if self.run.binding!=self.binding:raise BackendUnknown('run_binding_mismatch')
            intent=self.run.reserve(value)
            for pin in (self.binding.remote_python,self.binding.remote_executor,self.binding.remote_systemctl):self.verifier.verify(pin)
            self.verifier.source_closure(self.binding)
            # The qualified signal/physical evidence comes from a trusted local
            # observer port. F2/F4/F6 are still UNBOUND, not caller payload fields.
            self.proof.check(action,value)
            child=self.process.spawn(self.binding,action);self.children[action]=child
            result=self.completion.collect(child,2*SECOND)
            require_wait_policy()
            if not isinstance(result,Completion) or type(result.returncode) is not int or result.returncode!=0 or result.eof is not True:
                raise BackendUnknown('command_exit_or_EOF_unknown')
            if type(result.stdout) is not bytes or type(result.stderr) is not bytes or len(result.stdout)>MAX_STDOUT or len(result.stderr)>MAX_STDERR or result.stdout or result.stderr:
                raise BackendUnknown('command_output_unqualified')
            positive=outcome(value);intent.write('remote-complete',positive)
            return Reply(0,canonical(positive)+b'\n',b'')
        except Exception as error:
            self.stopped=True
            # No raw command output, exception text, credentials or remote time.
            return Reply(2,b'',canonical({'status':'UNKNOWN','reason':type(error).__name__})+b'\n')
