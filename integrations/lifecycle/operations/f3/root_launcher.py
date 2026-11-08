"""Concrete v4 ChildTransport launcher with fixed SSH grammar and no default binding."""
from backend_binding import BackendUnknown
from backend_files import root_intent
from backend_wire import request
from effects import Action, ExchangeUnknown


class FixedSSHLauncher:
    def __init__(self,binding=None,intent=None,verifier=None,admission=None,process=None):
        self.binding=binding;self.intent=intent;self.verifier=verifier
        self.admission=admission;self.process=process;self.used=set();self.children={}
    def launch(self,value):
        key=None
        try:
            if any(x is None for x in (self.binding,self.intent,self.verifier,self.admission,self.process)):
                raise BackendUnknown('launcher_dependencies_UNBOUND')
            value=request(value,self.binding);key=(value['run_id'],value['effect_id'])
            if key in self.used:raise BackendUnknown('SSH_session_consumed')
            self.used.add(key)
            root_intent(self.intent,value,self.binding)
            for pin in (self.binding.ssh,self.binding.ssh_config,self.binding.known_hosts):self.verifier.verify(pin)
            # Rechecks after adapter preparation. It is source-owned evidence work,
            # never a wire boolean or a new grant. The v4 gate and wait remain authoritative.
            if Action(value['action']) is Action.SIGNAL_MAIN:self.admission.check(value)
            child=self.process.spawn(self.binding,self.intent.directory)
            self.children[key]=child
            return child
        except Exception as error:
            # Popen/SSH may have delegated work before an error. Never infer cancellation.
            raise ExchangeUnknown('SSH_launch_unknown:'+type(error).__name__) from None
