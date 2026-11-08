"""B7-style operational sequencing; deliberately not the protected controller work."""
from dataclasses import dataclass
from effects import Action, ClockClaimFault, SECOND
from claims import ClaimFailure, ObservedLeaseLapse, UnboundEvidence, check_timing
from domains import clock_receipt, integer


class EffectSafetyRefusal(Exception): pass
class StructuralRace(Exception): pass
class PhysicalOperationalFailure(EffectSafetyRefusal): pass


@dataclass(frozen=True)
class UnitPoint:
    pid: int
    start_ticks: int | None
    restarts: int
    active: str
    sub: str
    exit_code: int
    exit_status: int
    result: str


@dataclass(frozen=True)
class JoinRead:
    before: UnitPoint
    after: UnitPoint
    old_absent: bool
    cgroup_members: tuple[int, ...]
    lock_released: bool
    boot_id: str
    preservation_digest: str
    journal_interval_qualified: bool
    journal_flags: tuple[str, ...]
    root_receipt: dict  # Untrusted structural payload field; E is read from Root's clock port.
    identity_stable: bool = True


@dataclass(frozen=True)
class OldIdentity:
    pid: int
    start_ticks: int
    restarts: int
    boot_id: str
    preservation_digest: str


def normal_join(read, old, *, allow_transition):
    if read.boot_id != old.boot_id or read.preservation_digest != old.preservation_digest:
        raise EffectSafetyRefusal('boot_or_preservation_changed')
    if read.journal_flags:
        raise PhysicalOperationalFailure('journal_contradiction')
    if read.journal_interval_qualified is not True:
        raise EffectSafetyRefusal('journal_unknown_or_contradictory')
    for point in (read.before, read.after):
        if point.pid not in (0, old.pid) or point.restarts != old.restarts:
            raise EffectSafetyRefusal('unexpected_restart_or_PID')
        if point.pid and point.start_ticks != old.start_ticks:
            if not (allow_transition and read.identity_stable is False and point.start_ticks is None):
                raise EffectSafetyRefusal('old_process_identity_unknown')
        if point.active == 'inactive' and (point.exit_code, point.exit_status, point.result) != (1, 0, 'success'):
            raise PhysicalOperationalFailure('abnormal_old_exit')
    before = (read.before.pid, read.before.start_ticks, read.before.restarts)
    after = (read.after.pid, read.after.start_ticks, read.after.restarts)
    if before != after or read.identity_stable is not True:
        if allow_transition: raise StructuralRace('old_transition_raced')
        raise EffectSafetyRefusal('postproof_identity_race')
    p = read.after
    return (read.old_absent is True and p.pid == 0 and p.active == 'inactive' and p.sub == 'dead'
            and (p.exit_code, p.exit_status, p.result) == (1, 0, 'success')
            and not read.cgroup_members and read.lock_released is True)


class Sequence:
    def __init__(self, effects, clock, old, evidence=None):
        self.effects = effects; self.clock = clock; self.old = old
        self.evidence = evidence if evidence is not None else UnboundEvidence()
        self.deadline_ns = None; self.claims = []; self.joined = False
        self.E = None; self.S = None; self.new_identity = None
        self.operational_failure = False; self.lapse_observations = []

    def invalidate(self, reason): self.claims.append(reason)

    def monotonic(self):
        return integer(self.clock.monotonic_ns(), 'monotonic_ns')

    def signal(self, read):
        def prelaunch():
            self.evidence.verify(read)
            receipt = clock_receipt(self.clock.receipt())
            check_timing(read, receipt, 75, complete_pass=True)
            return receipt
        prelaunch()
        self.deadline_ns = self.monotonic()+30*SECOND
        outcome = self.effects.perform(Action.SIGNAL_MAIN, signal_deadline_ns=self.deadline_ns, prelaunch=prelaunch)
        if outcome['status'] != 'POSITIVE_OUTCOME': return outcome
        self.claims.extend(outcome['claim_errors'])
        try:
            if outcome['after'] is None: raise ClaimFailure('postsignal_clock_unknown')
            check_timing(read, outcome['after'], 75, complete_pass=True)
        except ClaimFailure as error: self.invalidate(str(error))
        except Exception as error: self.invalidate('postsignal_claim_unknown:' + type(error).__name__)
        return outcome

    def claim_read(self, observe):
        try:
            read = observe()
            self.evidence.verify(read)
            check_timing(read, self.clock.receipt(), 30)
            if self.monotonic() > self.deadline_ns:
                self.invalidate('overall_claim_budget_overrun')
            return read
        except ObservedLeaseLapse as error:
            self.operational_failure = True
            self.lapse_observations.append({'source': error.source, 'subject': error.subject,
                                            'explicit_observed_lease_lapse': True})
            self.invalidate('claim_read_unknown:ObservedLeaseLapse'); return None
        except Exception as error:
            self.invalidate('claim_read_unknown:' + type(error).__name__); return None

    def join(self, observe_structure, observe_claim, pause):
        if self.effects.stopped or not self.effects.signalled:
            raise EffectSafetyRefusal('no_known_signal_outcome')
        raced = False
        # R1: inspect structure before the deadline branches; no new top-of-loop veto.
        while True:
            try:
                read = observe_structure()
                normal = normal_join(read, self.old, allow_transition=True)
            except StructuralRace:
                raced = True; normal = False; self.invalidate('preproof_structural_race')
            except PhysicalOperationalFailure:
                self.operational_failure = True
                self.effects.stop('physical_operational_failure')
                raise EffectSafetyRefusal('physical_operational_failure')
            except Exception:
                self.effects.stop('structural_join_unknown')
                raise EffectSafetyRefusal('structural_join_unknown')
            if normal:
                if raced:
                    try: proof_time = self.monotonic()
                    except Exception:
                        self.effects.stop('raced_join_budget_unknown')
                        raise EffectSafetyRefusal('raced_join_budget_unknown') from None
                    if proof_time > self.deadline_ns:
                        self.effects.stop('raced_join_proved_after_deadline')
                        raise EffectSafetyRefusal('raced_join_proved_after_deadline')
                try: self.E = clock_receipt(self.clock.receipt())
                except Exception:
                    self.E = None; self.invalidate('join_clock_receipt_unknown')
                # Physical proof is known here. An additional claim-clock failure
                # cannot undo a non-raced join or a raced proof already in budget.
                try:
                    if self.monotonic() > self.deadline_ns:
                        self.invalidate('postjoin_claim_budget_overrun')
                except Exception as error:
                    self.invalidate('postjoin_claim_clock_unknown:' + type(error).__name__)
                self.joined = True
                # The returned complete claim remains unconverted; F2 supplies full D_join.
                return self.claim_read(observe_claim)
            try: waiting_time = self.monotonic()
            except Exception:
                self.effects.stop('old_join_budget_unknown')
                raise EffectSafetyRefusal('old_join_budget_unknown') from None
            if waiting_time >= self.deadline_ns:
                self.effects.stop('old_join_unknown_at_deadline')
                raise EffectSafetyRefusal('old_join_unknown_at_deadline')
            pause(.1)
            self.claim_read(observe_claim)

    def postjoin_effect(self, action, fresh_structure):
        if action not in (Action.RELOAD_MANAGER, Action.START_SELECTED):
            raise EffectSafetyRefusal('postjoin_action_not_selected')
        if not self.joined or self.effects.stopped:
            raise EffectSafetyRefusal('postjoin_effect_blocked')
        # F4 must separately bind/prove the selected recipe; no file selector exists here.
        try: normal = normal_join(fresh_structure, self.old, allow_transition=False)
        except PhysicalOperationalFailure:
            self.operational_failure = True; normal = False
        except Exception: normal = False
        if not normal:
            self.effects.stop('normal_join_no_longer_proven')
            raise EffectSafetyRefusal('normal_join_no_longer_proven')
        outcome = self.effects.perform(action)
        if outcome['status'] == 'POSITIVE_OUTCOME':
            self.claims.extend(outcome['claim_errors'])
            if action is Action.START_SELECTED: self.S = outcome['after']
        return outcome

    def bind_started_identity(self, identity):
        if self.S is None or self.effects.stopped: raise ClaimFailure('start_outcome_unknown')
        boot, pid, ticks = identity
        if boot != self.old.boot_id or type(pid) is not int or pid <= 1 or type(ticks) is not int or ticks <= 0:
            raise ClaimFailure('new_process_identity_unknown')
        if (pid, ticks) == (self.old.pid, self.old.start_ticks): raise ClaimFailure('old_identity_not_new_start')
        if self.new_identity is not None and self.new_identity != identity:
            raise ClaimFailure('new_process_identity_changed')
        self.new_identity = identity
        # S stays the earliest positive start receipt, never a later PID-read timestamp.

    def first_readback_budget_open(self):
        # A necessary condition only; F2/F5 and complete C4/C5 acceptance are UNBOUND.
        if self.deadline_ns is None or self.claims or self.effects.stopped: return False
        try: return self.monotonic() < self.deadline_ns
        except Exception as error:
            self.invalidate('first_readback_clock_unknown:' + type(error).__name__)
            return False
