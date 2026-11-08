"""Pure numerical claim checks. F1/F2/F4/F5 evidence adapters remain UNBOUND."""
from dataclasses import dataclass
from effects import SECOND
from domains import DomainError, clock_receipt, integer


class ClaimFailure(Exception): pass
class ExternalInputsUnbound(ClaimFailure): pass


class ObservedLeaseLapse(ClaimFailure):
    """F2 must raise only for an explicit true API/local lease_lapsed observation."""
    def __init__(self, source, subject):
        if source not in ('api', 'local') or not isinstance(subject, tuple) or len(subject) != 4:
            raise ValueError('lapse diagnostic identity')
        if any(not isinstance(value, str) or not 0 < len(value) <= 128 for value in subject):
            raise ValueError('lapse diagnostic subject')
        self.source = source; self.subject = subject
        super().__init__('explicit_observed_lease_lapse')


@dataclass(frozen=True)
class OwnerCoverage:
    identity: tuple[str, str, str]
    last_seen_ns: int
    prior_last_seen_ns: int
    expires_ns: int
    # A leased deadline is never synthesized for TD-0 or missing attempts.
    lease_deadline_ns: int | None = None
    attempt_identity: tuple[str, str] | None = None
    td0: bool = False
    native_ttl_seconds: int | None = None


@dataclass(frozen=True)
class ClaimRead:
    earliest_root_rt_ns: int
    finished_root_rt_ns: int
    epoch: int
    control_durations_ns: tuple[int, ...]
    owners: tuple[OwnerCoverage, ...]
    # References identify externally verified evidence, not self-authenticating approvals.
    evidence_references: tuple[str, ...]


class UnboundEvidence:
    def verify(self, read):
        raise ExternalInputsUnbound('F1/F2/F4/F5 host and control evidence UNBOUND')


def check_timing(read, receipt, minimum_seconds, *, complete_pass=False):
    # Dataclass annotations do not validate callers. Validate every numeric input
    # before comparison or arithmetic; NaN, floats and bools must never qualify.
    try:
        if not isinstance(read, ClaimRead): raise DomainError('claim_read_shape')
        receipt = clock_receipt(receipt)
        for field in ('earliest_root_rt_ns', 'finished_root_rt_ns', 'epoch'):
            integer(getattr(read, field), field)
        if type(minimum_seconds) is not int or minimum_seconds not in (30, 75):
            raise DomainError('claim_margin_domain')
        if type(complete_pass) is not bool: raise DomainError('complete_pass_domain')
        if not isinstance(read.control_durations_ns, tuple) or not isinstance(read.owners, tuple):
            raise DomainError('claim_rows_shape')
        for duration in read.control_durations_ns: integer(duration, 'control_duration_ns')
        for owner in read.owners:
            if not isinstance(owner, OwnerCoverage): raise DomainError('owner_shape')
            if not isinstance(owner.identity, tuple) or len(owner.identity) != 3 or any(type(s) is not str or not s for s in owner.identity):
                raise DomainError('owner_identity_shape')
            for field in ('last_seen_ns', 'prior_last_seen_ns', 'expires_ns'):
                integer(getattr(owner, field), field)
            if owner.lease_deadline_ns is not None: integer(owner.lease_deadline_ns, 'lease_deadline_ns')
            if owner.native_ttl_seconds is not None: integer(owner.native_ttl_seconds, 'native_ttl_seconds')
            if type(owner.td0) is not bool: raise DomainError('TD0_domain')
            if owner.attempt_identity is not None and (not isinstance(owner.attempt_identity, tuple) or
                    len(owner.attempt_identity) != 2 or any(type(s) is not str or not s for s in owner.attempt_identity)):
                raise DomainError('attempt_identity_shape')
    except DomainError as error:
        raise ClaimFailure(str(error)) from None
    if not read.owners or len({r.identity for r in read.owners}) != len(read.owners):
        raise ClaimFailure('missing_or_duplicate_owner_inventory')
    if len(read.control_durations_ns) != len(read.owners) or any(type(n) is not int or n < 0 for n in read.control_durations_ns):
        raise ClaimFailure('control_brackets_incomplete')
    eps = max(read.control_durations_ns) + SECOND//2
    now = receipt['rt_ns']
    if receipt['epoch'] != read.epoch or not (read.earliest_root_rt_ns <= read.finished_root_rt_ns <= now):
        raise ClaimFailure('clock_epoch_or_read_order')
    if now-read.earliest_root_rt_ns > min(2*SECOND, 15*SECOND-eps):
        raise ClaimFailure('C3_stale_composite_read')
    for owner in read.owners:
        if owner.td0:
            if owner.lease_deadline_ns is not None: raise ClaimFailure('TD0_must_not_have_deadline')
            raise ClaimFailure('TD0_requires_wait_or_acceptance_unknown')
        if (owner.lease_deadline_ns is None) != (owner.attempt_identity is None):
            raise ClaimFailure('lease_identity_disagreement')
        deadlines = [owner.expires_ns]
        if owner.lease_deadline_ns is not None:
            if owner.native_ttl_seconds != 90: raise ClaimFailure('native_TTL_unknown_or_long')
            deadlines.append(owner.lease_deadline_ns)
        for deadline in deadlines:
            if deadline-now <= 0: raise ClaimFailure('coverage_reaches_zero')
            if deadline-now < minimum_seconds*SECOND+eps: raise ClaimFailure('coverage_margin')
            if deadline-now > 90*SECOND+eps: raise ClaimFailure('store_plausibility')
        if owner.last_seen_ns > now+eps: raise ClaimFailure('presence_plausibility')
    if complete_pass:
        seen = [r.last_seen_ns for r in read.owners]
        if max(seen)-min(seen) > 3*SECOND or any(r.last_seen_ns <= r.prior_last_seen_ns for r in read.owners):
            raise ClaimFailure('C2_incomplete_whole_pass')
    return eps
