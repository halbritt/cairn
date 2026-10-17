# Fixed callback outer lifetime candidate

This extends isolated 5e351854, not main. `FixedCallbackDriver` exclusively owns
progress of one already bound callback. It takes the exact coordinator, held
FixedServiceLaunch and unchanged command quote. It does not launch, bind a new
callback, admit a native port, or certify physical storage. Its constructor
requires the actual installed service custody/policy relationship. The binding
caller's event/output/pending/result aliases must have ended before construction;
Python identity is not proof of that external source obligation.

The sequence is concrete: pump the original producer/RPC/session; authorize each
pending command through existing custody; on a not-ready handoff pump one fair
turn; consume the producer graph once; apply fixed `engine_context`; enqueue the
session reply; return from the projection frame; observe actual positive writes
and the bound `reply_written`; detach the coordinator callback; return from that
frame; on a subsequent step retire the original RPC ticket. The driver returns
only fixed progress strings. It does not return event, output, context or pending
request objects. No new callback/document/terminal may be silently consumed in
this callback-only sequence: unexpected session events refuse.

All steps preserve the original callback deadline, including after detach.
Any exception freezes the original bank and poisons both coordinator and the
independently retained RPC. Partial detach never removes that recovery authority.
Projection, write, detach or pre-retirement interruptions cannot retry or release
the held ticket. An interruption after a completed release cannot undo that
release; the bank becomes unknown and cannot reuse it. Cleanup uncertainty is
recorded without replacing the primary. There is no error-to-empty-context path.

## Simultaneous backing inventory

These are source lifetime facts, not a complete numerical physical quote.

| Phase/process | Actual overlapping backing | Custody and end event |
|---|---|---|
| Parent input/binding | callback graph via coordinator event, producer callback and bootstrap; config graph; canonical generator scratch/chunk; producer slabs | Original callback ticket plus standing source/session custody. Input iterator ends at actual exhausted/closed stdin; callback/bootstrap remain until fixed service retirement. Outer event ends on detach. |
| Producer load/invoke | parsed bootstrap graph, Unicode decoder fragments/join, module/compiler/interpreter storage, client regions, engine Memory/response/state graphs | Original parent-held callback allocation. OS child wait/release precedes parent output completion; compiler caches and complete combined working peak remain unproved. |
| Parent output finish | decoder text and parsed graph coexist in `_finish`; then ProducerOutput owns graph; coordinator and short projection frame borrow output | Original callback allocation remains held. `take` clears stored graph; successful returning projection frame ends graph/context locals. Failure traceback can retain them and quarantines allocation. |
| Parent reply encoding | projected Unicode context, temporary UTF-8 `content`, complete encoded reply wire, canonical JSON temporaries | `SDKControl.reply` checks exact wire budget first. Context bytes and wire coexist, each bounded by existing 8500 wire envelope; allocation-partition sufficiency is NOT derived from the traffic budget. No quote decrease or new allocation asserted. |
| Parent reply write | queued complete wire and at most512-byte offered suffix/view; controller/writer/native counters | Queue entry ends only at final positive write bookkeeping; writer local/view ends when advance returns. Stream observes bound completion before coordinator detach. `_expected_replays` retains only length/hash, not raw wire. Ledger metadata remains session working. |
| Parent detach/retire | coordinator event/output/producer/RPC aliases; fixed service producer slot; original RPC regions and phase state | Detach ends coordinator aliases. Later RPC retire ends stream, regions, fixed producer bootstrap/callback/slabs, phases and read custody before original release. Outer still holds retired RPC and coordinator bookkeeping shells; their standing working attribution remains to be proved. |
| Parent error/interrupt | traceback frames may retain graph/context/input, partial writer and custody objects | Frozen original tickets remain; no assumed garbage collection, context loss, or successful remote/native delivery. |

Known full standing composition still refuses: callback raw136353 exceeds131072
by5281; command raw153325 exceeds131072 by22253. Original cumulative command
working transfer stays intact. The new owner is not evidence that all parent
64MiB transfers represent physically retained text, nor permission to lower them.
No full successful fixture replaces these unchanged bounds.

## Acceptance and test boundaries

Eight named new tests exercise partial writes and exact context digest; invalid
projection/no reply/no refund; retained traceback; six interruption locations;
original deadline after detach; real delayed producer input while a handoff is
pending; and original selected engine.handle result through the paired invoke,
real authored client/service, stdout decoding and outer reply/retirement sequence.
Process/network/signal edges are inert. The test session uses real control,
stream, writer and NativeProcessOwner bookkeeping over synthetic syscalls, but
omits preparation admission and standing SDK/session allocation. The engine case
uses the same specifically authorized authored source inherited from5e351854.
It is outside341's generic bounded reader scope. Fixtures retain inspection
aliases deliberately; they do not prove global absence of aliases in a deployed
builder. They assert production-owned references at the actual release boundary.

A future fixed run builder must own callback discovery, endpoint/launch custody,
this driver's construction/returning handoff, terminal/task result consumption,
actual process cancellation/wait, source closure and standing bookkeeping.
This candidate closes the local callback completion sequence, not that whole
session builder or the missing supported native contract. DO_NOT_ADOPT remains
until composition and complete physical inventory support an adoption decision.
