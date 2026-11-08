"""Reuse the accepted physical predicate; actual observation/selection ports are absent."""
from dataclasses import asdict
from backend_binding import BackendUnknown
from effects import Action, canonical, digest
from sequence import OldIdentity, normal_join


class ProofGate:
    def __init__(self,binding,old_identity=None,observer=None,selection=None,signal=None):
        self.binding=binding;self.old=old_identity;self.observer=observer
        self.selection=selection;self.signal=signal
    def check(self,action,request):
        if not isinstance(self.old,OldIdentity) or self.selection is None:raise BackendUnknown('proof_binding_UNBOUND')
        if digest(canonical(asdict(self.old)))!=self.binding.old_identity_digest:raise BackendUnknown('old_identity_binding')
        # F4 supplies an exact source-owned guard, not a claimed digest from the wire.
        self.selection.check(action,self.binding.selection_digest)
        if action is Action.SIGNAL_MAIN:
            if self.signal is None:raise BackendUnknown('signal_evidence_UNBOUND')
            self.signal.check(self.binding,self.old,request)
        else:
            if action not in (Action.RELOAD_MANAGER,Action.START_SELECTED) or self.observer is None:
                raise BackendUnknown('postjoin_observer_UNBOUND')
            read=self.observer.read_structure()
            if not normal_join(read,self.old,allow_transition=False):raise BackendUnknown('fresh_normal_join_unknown')
