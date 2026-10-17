# Fixed run custody candidate

This source-only candidate extends frozen6cf4bd0. It keeps original allocations,
source restrictions, deadlines and unsupported native admission. No source test
or `closed` status grants launch, native compatibility, physical fit or task
acceptance. Methods that contain OS effects are for separately selected runtime
use; validation invokes only inert process/socket/signal boundaries.

The initial builder creates FixedRunOwner before calling prepare. Prepare borrows
the exact original preparation and SDKSessionResources; it retains their cleanup
authority before constructing a session. SDKSessionDriver publishes an inert
SDKSessionCustody shell BEFORE its constructor moves ownership. The move captures
the original snapshot/controller/allocation in a shared recovery object before
any mutation. Successful transfer clears obsolete preparation snapshot, launch,
controller, allocation and recovery fields. The old launch accessor refuses.
Only scalar/hash metadata remains in that shell. Failures retain the same
recovery object in the failed receiver and preparation, so either close reaches
the same once-only controller/FD cleanup. The original allocation is poisoned,
not reconstructed from an emptied preparation. A post-constructor interruption
cannot strand a return value before assignment. The legacy direct
consume_for_session API still has its ordinary caller-owned return obligation;
the fixed session path does not use that API.

FixedRunOwner.prepare uses returning construction frames and prepublished session,
service-link and fixed-source objects. FixedServiceLaunch.prepare_owned is
one-shot and prepublishes its Snapshot before source acquisition. A run retains
actual native owners independently of coordinator/callback detach. Failed
construction keeps all original reservations and leaves the run object available
for cleanup. Upstream creation of the preparation declaration and existing
SDKSessionResources remains the original caller's separately charged obligation;
this does not certify arbitrary caller-retained aliases as ended.

The source run has explicit states:

- Service start pre-reserves READY credit, arms receive, then calls the existing
  fixed source launch. Accept and authenticated READY consumption precede use.
- Session progress consumes its event within a returning frame. Callback discovery
  sets binding state without exporting the event. A subsequent frame reserves
  the unchanged aggregate under a fresh original owner, creates the existing
  broker endpoint, binds held service/bootstrap authority, retains the existing
  FixedCallbackDriver, and invokes the fixed producer launch.
- Callback connection parking alternates accept, producer input/output progress,
  and session progress. It does not enter RPC.step before a connection exists.
- FixedCallbackDriver owns projection, reply write, detach and later original
  callback retirement. The run then drops its callback-driver reference.
- Terminal consumption moves TerminalDocument's graph into the run. Only fixed
  progress and sanitized metadata leave public methods. A subsequent bounded
  drain observes actual SDK EOF/wait and native release. No arbitrary terminal
  sink, evaluation hook or declaration boolean is installed.

The retained terminal work product is intentionally NOT discarded or refunded.
A supported task/evaluation evidence sink remains an unimplemented seam. Ordinary
nonterminal documents outside the selected callback/terminal profile refuse;
there is no hidden accumulator or claim that this narrower profile supports all
native output schemas. `require_complete` still refuses hook_interface_unavailable.
No SDK launch port, namespace custody, account/provider admission or native
compatibility has been fabricated.

Cancellation suppresses further step/output before requesting existing protocol
cancellation. cleanup_step remains reachable after failure and performs at most
one wait/stop observation per retained original owner per invocation. It uses
actual NativeProcessOwner receipts, never caller-supplied receipt booleans/PIDs.
TERM is consumed once before the original work deadline; KILL once at/after that
same deadline. No new grace interval or reset is created. This bounds each step,
not eventual kernel death: absent wait remains pending and is never reported as
completed. Native owner failures remain sticky. After waits, cleanup requires
actual handle detachment. Unobserved EOF or close uncertainty remains unknown.

Source cleanup uses per-owner consumed attempts and a whole-step BaseException
guard. Unattempted independent sources remain eligible after an interruption.
Consumed uncertain closes never retry. Snapshot receipts additionally require
closed state, no cleanup faults and all held lease descriptors detached. Broker
listener receipts require closed handles and known connection closure. A failed
source close cannot skip independent session/link/listener cleanup. The complete
source flag is published only after all action dispositions. Final publication
interruption cannot turn the next call into clean completion. No standing
preparation, session or service allocation is released, even on clean closure.
The existing successful callback retirement remains unchanged.

## Actual accounting and limits

Preparation/source/session objects and controller metadata remain under their
original standing/operation tickets. Initial declarations, snapshot graphs,
recurrent source checks, fixed configuration, result graph, retired owner shells
and error tracebacks have overlapping working lifetimes. Old preparation payload
aliases now end on successful move; shared recovery aliases survive failed moves
under frozen tickets. SDK input/body and callback-bootstrap storage are unchanged.
The result graph survives terminal EOF/wait/source closure until the missing
bounded evidence sink owns its consumption. Retained original native-owner and
cleanup bookkeeping also survive closure; their complete working quote remains
unproved. The8192-byte read loop is not used as a total storage estimate.

Full unchanged callback admission still refuses136353raw against131072 (excess
5281); command153325 remains over by22253. The real-preparation test reaches that
same refusal before endpoint creation or producer launch. Successful callback and
terminal fixtures explicitly omit unaffordable preparation/session admission;
they prove only the named source paths, not complete combined fit. Original
cumulative command working charges remain. DO_NOT_ADOPT pending whole physical
inventory and remaining native/evaluation contracts.

Tests cover fourteen move interruption points; after-move/before-receiver-return;
run construction publications; cancellation/once-only stops/actual waits;
source-action construction, between-owner and final-publication interruptions;
independent cleanup on close failure; full unchanged admission refusal; actual
READY codec/credit ordering using a fresh synthetic service; callback connection
parking and existing callback driver through terminal consume/drain; and retained
terminal graph on publication failure. Legacy custody tests now inspect the new
receiver rather than an intentionally emptied obsolete preparation.
