"""One trusted parent ledger. No allocation, transport, wait or admission effects.

Held tickets bind accounting identity, not an authenticated remote caller or proof
that memory/aliases ended. The fixed coordinator must supply those observations.
Never create one ledger per child and call the independent limits shared.
"""
from dataclasses import dataclass
import os

from trial_sdk_native_process_owner import NativeProcessOwner


RAW=128*1024
WORKING=512*1024*1024
TRAFFIC=4*1024*1024
MAX_ORDINAL=2**53-1
MAX_DOMAINS=32  # Bounded bookkeeping; does not admit 32 additional tasks.


class ResourceError(ValueError):
    pass


@dataclass(frozen=True,eq=False)
class _Ticket:
    ordinal:int


def _guard(method):
    def guarded(self,*args,**kwargs):
        acquired=False
        try:
            try:
                self._check()
                if self._busy:raise ResourceError('resource_busy')
                self._busy=True;acquired=True;self._mutating=False
                answer=method(self,*args,**kwargs)
            finally:
                if acquired:self._busy=False
            return answer
        except BaseException:
            if acquired and self._mutating:self._unknown=True
            raise
    return guarded


class AttemptResources:
    """Serialized parent-only accounting for the ORIGINAL fixed three limits.

    Each held process domain has standing/operation allocation slots and
    control/data traffic slots. Quotes aggregate all simultaneous copies within
    that lifetime. This selected one-operation-per-domain profile needs workload
    validation; its bookkeeping bound is not a new callback/concurrency grant.
    """
    def __init__(self):
        self._creator=os.getpid();self._next=1
        self._busy=self._mutating=self._unknown=self._closed=False
        self._frozen=False
        # _spent is settled traffic; _reserved is the gross live commitment.
        # Terminal cumulative prefixes stay in their held rows. Public totals
        # derive from those rows, so interrupted prefix updates cannot half-apply
        # a spent increment and later apply it twice.
        self._raw=self._working=self._spent=self._reserved=0
        self._raw_peak=self._working_peak=self._allocation_peak=self._committed_peak=0
        self.local=_Ticket(0)
        self._domains={self.local:{'owner':None,'memory':{},'traffic':{}}}
        self._memory={};self._traffic={}

    def _check(self):
        self._check_owner()
        if self._unknown or self._frozen:raise ResourceError('resource_unknown')

    def _check_owner(self):
        if os.getpid()!=self._creator:raise ResourceError('resource_wrong_process')
        if self._closed:raise ResourceError('resource_closed')

    def _domain(self,domain):
        if type(domain) is not _Ticket or domain not in self._domains:
            raise ResourceError('resource_domain')
        return self._domains[domain]

    def _lookup(self,registry,ticket):
        if type(ticket) is not _Ticket or ticket not in registry:
            raise ResourceError('resource_ticket')
        return registry[ticket]

    def _can_issue(self):
        if self._next>MAX_ORDINAL:raise ResourceError('resource_sequence_exhausted')

    def _ticket(self):
        token=_Ticket(self._next);self._next+=1
        return token

    def _begin(self):
        self._mutating=True;self._unknown=True

    def _known(self,value=None):
        self._unknown=self._frozen
        return value

    def freeze(self):
        """Retain all counters/tickets after higher-layer handoff uncertainty.

        Parent-only, sticky and idempotent, including after settlement/close.
        It does not fabricate a live ticket, receipt or uncertain byte count.
        """
        if os.getpid()!=self._creator:raise ResourceError('resource_wrong_process')
        self._frozen=self._unknown=True

    @_guard
    def bind(self,owner):
        if type(owner) is not NativeProcessOwner:raise ResourceError('resource_owner')
        if any(row['owner'] is owner for row in self._domains.values()):
            raise ResourceError('resource_owner_already_bound')
        if len(self._domains)>=MAX_DOMAINS:raise ResourceError('resource_domains_full')
        self._can_issue();self._begin()
        token=self._ticket()
        self._domains[token]={'owner':owner,'memory':{},'traffic':{}}
        return self._known(token)

    @_guard
    def allocate(self,domain,lifetime,raw,working):
        row=self._domain(domain)
        if type(lifetime) is not str or lifetime not in ('standing','operation'):
            raise ResourceError('resource_lifetime')
        if lifetime in row['memory']:raise ResourceError('resource_slot_busy')
        if (type(raw) is not int or type(working) is not int or
                not 0<=raw<=RAW or not 0<=working<=WORKING or raw+working==0):
            raise ResourceError('resource_quote')
        if self._raw+raw>RAW or self._raw+self._working+raw+working>WORKING:return None
        self._can_issue();self._begin()
        self._raw+=raw;self._working+=working
        self._raw_peak=max(self._raw_peak,self._raw)
        self._working_peak=max(self._working_peak,self._working)
        self._allocation_peak=max(self._allocation_peak,self._raw+self._working)
        token=self._ticket()
        self._memory[token]=(domain,lifetime,raw,working)
        row['memory'][lifetime]=token
        return self._known(token)

    @_guard
    def release(self,ticket):
        # Only the trusted source-owned lifetime handler may call this AFTER
        # actual aliases/allocations ended or a bound charge transfer completed.
        domain,lifetime,raw,working=self._lookup(self._memory,ticket)
        self._begin()
        del self._domains[domain]['memory'][lifetime]
        del self._memory[ticket]
        self._raw-=raw;self._working-=working
        return self._known()

    @_guard
    def grant(self,domain,lane,maximum):
        row=self._domain(domain)
        if type(lane) is not str or lane not in ('control','data'):
            raise ResourceError('resource_lane')
        if lane in row['traffic']:raise ResourceError('resource_slot_busy')
        if type(maximum) is not int or not 0<=maximum<=TRAFFIC:
            raise ResourceError('resource_grant')
        available=TRAFFIC-self._spent-self._reserved
        if maximum==0 and available!=0:raise ResourceError('resource_zero_grant')
        if maximum>available:return None
        self._can_issue();self._begin()
        self._reserved+=maximum
        self._committed_peak=max(self._committed_peak,self._spent+self._reserved)
        token=self._ticket()
        self._traffic[token]={'domain':domain,'lane':lane,'maximum':maximum,
                              'available_at_issue':available,'known':0,'unknown':False,
                              'settling':False}
        row['traffic'][lane]=token
        return self._known(token)

    def grant_info(self,ticket):
        self._check()
        row=self._lookup(self._traffic,ticket)
        return {key:row[key] for key in ('maximum','available_at_issue')}

    def _count(self,ticket,known):
        row=self._lookup(self._traffic,ticket)
        if type(known) is not int or not 0<=known<=row['maximum']:
            raise ResourceError('resource_count')
        return row

    @_guard
    def settle(self,ticket,known):
        # Known receipt belongs to this exact held grant; an unknown report must
        # use quarantine, never settle(0) or refund alleged unused capacity.
        row=self._count(ticket,known)
        self._begin()
        # A failed settlement may leave this row present after its counters
        # changed. Its receipt must never be reclassified as another prefix.
        row['settling']=True
        self._spent+=known;self._reserved-=row['maximum']
        del self._domains[row['domain']]['traffic'][row['lane']]
        del self._traffic[ticket]
        return self._known()

    def quarantine(self,ticket,known=0):
        """Record an exact held grant's cumulative prefix, even after freeze.

        This terminal parent-only port never settles or returns unused credit.
        Equal observations are idempotent; decreasing observations refuse.
        """
        acquired=False
        try:
            try:
                self._check_owner()
                if self._busy:raise ResourceError('resource_busy')
                row=self._count(ticket,known)
                if row['settling']:raise ResourceError('resource_settlement_unknown')
                if known<row['known']:raise ResourceError('resource_count_regression')
                self._busy,acquired=True,True
                # No normal operation can run once a prefix update may occur.
                self.freeze()
                # One assignment is the prefix's linearization point. Never
                # increment aggregate counters separately from this value.
                row['known']=known
                row['unknown']=True
            finally:
                if acquired:self._busy=False
            return None
        except BaseException:
            if acquired:
                self.freeze()
                self._busy=False
            raise

    @_guard
    def retire(self,domain):
        row=self._domain(domain)
        if domain is self.local:raise ResourceError('resource_local_domain')
        if row['memory'] or row['traffic']:raise ResourceError('resource_domain_live')
        self._begin();del self._domains[domain]
        return self._known()

    @_guard
    def close(self):
        if len(self._domains)!=1 or self._memory or self._traffic:
            raise ResourceError('resource_attempt_live')
        self._begin();self._closed=True
        return self._known()

    def snapshot(self):
        prefixes=sum(row['known'] for row in self._traffic.values())
        return {'raw_reserved':self._raw,'working_reserved':self._working,
                'raw_peak':self._raw_peak,'working_peak':self._working_peak,
                'known_allocation_reserved':self._raw+self._working,
                'known_allocation_peak':self._allocation_peak,
                'traffic_spent':self._spent+prefixes,
                'traffic_reserved':self._reserved-prefixes,
                'traffic_available':TRAFFIC-self._spent-self._reserved,
                'committed_peak':self._committed_peak,'unknown':self._unknown or self._frozen,
                'closed':self._closed,
                'domains':{ticket.ordinal:{'memory':tuple(sorted(row['memory'])),
                                           'traffic':tuple(sorted(row['traffic']))}
                           for ticket,row in self._domains.items()},
                'qualification':'logical_accounting_only'}
