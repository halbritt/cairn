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


@dataclass(frozen=True,eq=False)
class _StreamTicket:
    ordinal:int


@dataclass(frozen=True,eq=False)
class CallbackMemory:
    ticket:object
    row:tuple
    generation:int
    phase:str


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
        self._memory={};self._traffic={};self._streams={}
        self._callback_memory={}
        self._memory_transition=None

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

    def _stream_row(self,ticket):
        if type(ticket) is not _StreamTicket or ticket not in self._streams:
            raise ResourceError('resource_stream_ticket')
        return self._streams[ticket]

    def _stream_committed(self):
        return sum(row['initial_maximum']+row['extra'] for row in self._streams.values())

    def _available(self):
        return TRAFFIC-self._spent-self._reserved-self._stream_committed()

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
        self._callback_memory.pop(ticket,None)
        self._raw-=raw;self._working-=working
        return self._known()

    @_guard
    def bind_callback_memory(self,ticket):
        row=self._lookup(self._memory,ticket)
        if row[1]!='operation' or row[0] is self.local or ticket in self._callback_memory:
            raise ResourceError('resource_callback_binding')
        state=CallbackMemory(ticket,row,0,'input_request')
        self._begin()
        self._callback_memory[ticket]=state
        return self._known(state)

    @_guard
    def transition_callback_memory(self,expected,raw,working,phase):
        """Exact-ticket publication; original owners prove the backing endings.

        Retain one old/proposed publication record, including pre-publication
        totals/peaks. A partial update never makes capacity reusable: _guard and
        _begin keep it unknown, and snapshot exposes conservative totals/peaks.
        """
        if (type(expected) is not CallbackMemory or
                self._callback_memory.get(expected.ticket) is not expected or
                self._lookup(self._memory,expected.ticket) != expected.row):
            raise ResourceError('resource_callback_stale')
        allowed={'input_request':'command','request':'command',
                 'command':'return','return':'request'}
        if phase!=allowed.get(expected.phase) or expected.generation>=MAX_ORDINAL:
            raise ResourceError('resource_callback_phase')
        if (type(raw) is not int or type(working) is not int or
                not 0<=raw<=RAW or not 0<=working<=WORKING or raw+working==0):
            raise ResourceError('resource_quote')
        domain,lifetime,old_raw,old_working=expected.row
        if (self._domain(domain)['memory'].get(lifetime) is not expected.ticket or
                working<old_working or
                phase=='command' and raw>old_raw or
                phase=='return' and raw!=old_raw or
                phase=='request' and raw<old_raw):
            raise ResourceError('resource_callback_transition')
        next_raw=self._raw-old_raw+raw
        next_working=self._working-old_working+working
        if next_raw>RAW or next_raw+next_working>WORKING:return None
        proposed=CallbackMemory(expected.ticket,(domain,lifetime,raw,working),
                                expected.generation+1,phase)
        evidence=(expected,proposed,(self._raw,self._working),
                  (next_raw,next_working),
                  (self._raw_peak,self._working_peak,self._allocation_peak))
        self._begin()
        self._memory_transition=evidence
        self._raw=next_raw
        self._working=next_working
        self._memory[expected.ticket]=proposed.row
        self._callback_memory[expected.ticket]=proposed
        self._raw_peak=max(self._raw_peak,next_raw)
        self._working_peak=max(self._working_peak,next_working)
        self._allocation_peak=max(self._allocation_peak,next_raw+next_working)
        return self._known(proposed)

    @_guard
    def grant(self,domain,lane,maximum):
        row=self._domain(domain)
        if type(lane) is not str or lane not in ('control','data'):
            raise ResourceError('resource_lane')
        if lane in row['traffic']:raise ResourceError('resource_slot_busy')
        if type(maximum) is not int or not 0<=maximum<=TRAFFIC:
            raise ResourceError('resource_grant')
        available=self._available()
        if maximum==0 and available!=0:raise ResourceError('resource_zero_grant')
        if maximum>available:return None
        self._can_issue();self._begin()
        self._reserved+=maximum
        self._committed_peak=max(self._committed_peak,TRAFFIC-self._available())
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

    @_guard
    def stream_grant(self,domain,maximum):
        """One producer-lifetime DATA commitment in the original bank/slot."""
        row=self._domain(domain)
        if 'data' in row['traffic']:raise ResourceError('resource_slot_busy')
        if type(maximum) is not int or not 1<=maximum<=TRAFFIC:
            raise ResourceError('resource_stream_grant')
        available=self._available()
        if maximum>available:return None
        self._can_issue();self._begin()
        token=_StreamTicket(self._next);self._next+=1
        self._streams[token]={'domain':domain,'initial_maximum':maximum,'extra':0,
                              'available_at_issue':available,'known':0,'unknown':False,
                              'settling':False}
        row['traffic']['data']=token
        self._committed_peak=max(self._committed_peak,TRAFFIC-self._available())
        return self._known(token)

    def stream_info(self,ticket):
        self._check()
        info=self.stream_snapshot(ticket)
        return {key:info[key] for key in ('initial_maximum','maximum','available_at_issue')}

    def stream_snapshot(self,ticket):
        """Copied held facts remain observable after terminal uncertainty."""
        self._check_owner()
        row=self._stream_row(ticket)
        return {'initial_maximum':row['initial_maximum'],
                'maximum':row['initial_maximum']+row['extra'],
                'available_at_issue':row['available_at_issue'],
                'known':row['known'],'settling':row['settling'],
                'unknown':self._unknown or self._frozen}

    @_guard
    def grow_stream(self,ticket,maximum):
        row=self._stream_row(ticket)
        current=row['initial_maximum']+row['extra']
        if row['settling']:raise ResourceError('resource_settlement_unknown')
        if type(maximum) is not int or not current<=maximum<=TRAFFIC:
            raise ResourceError('resource_stream_growth')
        if maximum==current:return True
        if maximum-current>self._available():return False
        self._begin()
        # One scalar publishes the additional commitment. Availability derives
        # from it; there is no separately incremented aggregate to half-apply.
        row['extra']=maximum-row['initial_maximum']
        self._committed_peak=max(self._committed_peak,TRAFFIC-self._available())
        return self._known(True)

    @_guard
    def settle_stream(self,ticket,known):
        row=self._stream_row(ticket)
        if type(known) is not int or not row['known']<=known<=row['initial_maximum']+row['extra']:
            raise ResourceError('resource_count')
        self._begin();row['settling']=True
        # Spend BEFORE removing commitment. An interrupted intermediate state
        # can conservatively overcount, never expose new spendable capacity.
        self._spent+=known
        del self._streams[ticket]
        del self._domains[row['domain']]['traffic']['data']
        return self._known()

    def quarantine_stream(self,ticket,known=0):
        """Terminal cumulative observation; never returns a stream remainder."""
        acquired=False
        try:
            try:
                self._check_owner()
                if self._busy:raise ResourceError('resource_busy')
                row=self._stream_row(ticket)
                if row['settling']:raise ResourceError('resource_settlement_unknown')
                if type(known) is not int or not row['known']<=known<=row['initial_maximum']+row['extra']:
                    raise ResourceError('resource_count')
                self._busy,acquired=True,True
                self.freeze()
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
        if len(self._domains)!=1 or self._memory or self._traffic or self._streams:
            raise ResourceError('resource_attempt_live')
        self._begin();self._closed=True
        return self._known()

    def snapshot(self):
        prefixes=sum(row['known'] for row in self._traffic.values())
        stream_prefixes=sum(row['known'] for row in self._streams.values())
        stream_committed=self._stream_committed()
        raw,working=self._raw,self._working
        raw_peak,working_peak,total_peak=self._raw_peak,self._working_peak,self._allocation_peak
        transition=None
        if self._memory_transition is not None:
            old,new,before,after,peaks=self._memory_transition
            transition={'ticket':old.ticket.ordinal,'old_phase':old.phase,'phase':new.phase,
                        'old_generation':old.generation,'generation':new.generation,
                        'old_quote':old.row[2:],'quote':new.row[2:],
                        'before':before,'after':after,'prior_peaks':peaks}
            raw_peak=max(raw_peak,peaks[0],before[0],after[0])
            working_peak=max(working_peak,peaks[1],before[1],after[1])
            total_peak=max(total_peak,peaks[2],sum(before),sum(after))
            if self._unknown or self._frozen:
                raw=max(raw,before[0],after[0])
                working=max(working,before[1],after[1])
        return {'raw_reserved':raw,'working_reserved':working,
                'raw_peak':raw_peak,'working_peak':working_peak,
                'known_allocation_reserved':raw+working,
                'known_allocation_peak':total_peak,'memory_transition':transition,
                'traffic_spent':self._spent+prefixes+stream_prefixes,
                'traffic_reserved':self._reserved+stream_committed-prefixes-stream_prefixes,
                'traffic_available':self._available(),
                'committed_peak':max(self._committed_peak,TRAFFIC-self._available()),
                'unknown':self._unknown or self._frozen,
                'closed':self._closed,
                'domains':{ticket.ordinal:{'memory':tuple(sorted(row['memory'])),
                                           'traffic':tuple(sorted(row['traffic']))}
                           for ticket,row in self._domains.items()},
                'qualification':'logical_accounting_only'}
