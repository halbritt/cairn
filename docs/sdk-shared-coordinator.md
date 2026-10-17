# Shared SDK coordinator source candidate

Base: `467694f359a828970a666d52baeb1e14ba093406`. The coordinator adds no
launcher, native MCP endpoint, provider call or runtime admission. Existing
drivers, resources and their limits are unchanged: 128 KiB raw, 512 MiB combined,
4 MiB traffic, and each callback's original first-read plus five seconds.

`SharedCoordinator` borrows the actual bound `SDKSessionDriver`, original
`MemoryDispatchGate` and `ServiceLink`. It requires the existing
`unsupported_native` forward. The builder exclusively lends these objects; Python
object identity is not protection against a caller retaining and mutating aliases.

## Binding and progress

1. Receive the exact callback event from `step()`. No replacement callback or
   fresh deadline is accepted. One slot remains occupied through final reply write.
2. Supply the actual producer owner/domain and an explicit `AggregateQuote`.
   `reserve_callback` reserves one original operation ticket before constructing
   `ProducerRPC` or `SDKProducerDriver`. `bind_callback` instead borrows an already
   held aggregate ticket. Both components use that same ticket and the RPC's one
   DATA stream. Neither method creates/connects endpoints or launches a process.
3. `step()` runs at most one component action, rotating session, producer and RPC.
   Session and producer polls use zero timeout. RPC alone advances its command and
   ServiceLink; the outer owner must not also step them. No idle loop sleeps or
   restarts a deadline. The original capture deadline also bounds every turn.
4. Inspect the actual `pending_request()`, apply external source authority, then
   explicitly call `prepare_command` with that exact object and the unchanged
   command quote. Reservation precedes preparation/dispatch. The coordinator drops
   its operation alias immediately after RPC takes ownership; failed handoffs keep
   the operation for recovery. A matching request is not authorization by itself.
5. Only a closed RPC channel and the producer driver's actual ready/wait/release
   path permit `take_output()`. The caller supplies its authorized context
   projection to `reply(output, context)`; there is no default success/context.
   The exact returned output object correlates this handoff. It does not attest
   the caller's projection, native delivery, or task correctness.
6. After actual `reply_written`, `detach_callback` ends this coordinator's borrow.
   The outer owner must retain the ports, output and ticket while ending or
   transferring every remaining alias. This method never calls `rpc.retire`,
   releases a ticket, resizes a quote or claims a whole-attempt completion.

The builder must quote the session/source/controller/callback graph and returned
documents, coordinator/event bookkeeping, canonical generator frames and chunks,
producer output/decoder graphs, RPC/client storage, service/command/native/kernel
storage, caller projections, and exception/cleanup aliases for their actual full
lifetimes. One returned event per step is not permission to accumulate documents.
No positive working number or named raw minimum establishes this full quote.

## Cancellation and recovery

`cancel()` suppresses new output/reply handoff and requests the existing RPC
cancellation protocol. Bounded progress may continue to drain it until the
original deadline. Cancellation does not retract an already written reply and
does not prove that a child stopped. Actual signal/wait, service cleanup and
endpoint detachment remain the original outer owner's obligations.

Failure, including interruption in final busy-flag cleanup, quarantines local
state and independently attempts RPC/failed command recovery, producer input
cleanup, session stream/control cleanup, original resource poisoning and freeze.
Fixed cleanup faults preserve the original exception. Failed freeze remains
uncertainty, not a claim that the bank froze. No retry or allocation refund follows.
`close_sources()` remains usable after such failure; it attempts producer-local
cleanup and original session source cleanup independently. The session checks
actual unlaunched ownership or observed wait/detachment before closing custody.
Source close does not prove that every retained object backing ended.

## Evidence and limits

The current base's existing fixture reserves 65536 standing source raw, 2566
session raw, 5184 service raw and 36928 RPC raw: **110214**. The unchanged
CommandOperation parent quote 32768 refuses before child quote 41548; their sum
would be **184530**, exceeding 131072 by **53458**. The new public callback
reservation test uses the existing RPC/client fixture plus independent producer
named raw 25604: aggregate 62532, with 128 MiB RPC working plus 1 MiB producer
working. These incomplete fixture assumptions already total **135818** raw before
any command. The original bank refuses before either callback port is constructed.

These are demonstrated undercounts/refusals, not a complete quote or proof that
all possible permitted implementations cannot fit. Successful full-composition
progress, command/output/reply EOF lifetime and aggregate bind success are not
validated on this over-cap base. Rotation tests use explicitly labeled scheduling
doubles; actual session tests cover short-read JSON callback input, original
receipt totals, deadline/standing refusal and final-cleanup interruption. Existing
RPC/service/producer suites separately cover their inert original boundaries.
They do not turn the scheduling doubles into full integration evidence.

Validation on this candidate: 15 coordinator tests plus the related session,
producer-resource, command, RPC, recovery and receipt suites: **104 PASS** under a
prebyte source guard and an audit hook refusing real subprocess/socket/process
effects. Go vet, API-contract check and gofmt check passed. `make check` then
stopped at its design-source hashing step because
`docs/sources/agent-memory/design/agent-memory-design.md` is 109674 bytes, above
the authorized 65536-byte source-read ceiling; that file was not read. This is
not a full `make check` pass. No frozen candidate/main file was changed.

Root341's inventory is `/tmp/cairn-agent341-coordinator-quote-inventory/REPORT.md`,
SHA-256 `3369f82c118e34dd4605b164d22c7f645bff385f07766a0ec96d24e142dea48f`.
Its phase note `PHASE-TRANSITIONS.md` has SHA-256
`16071d11381f62b721b0a2c32e57b17254c91acb0006eac7dd996921fa71530b`.
The following bullets record limitations of the original `467694f` base, not
the current phase-enabled adoption. The current disposition follows them:

- Producer input scratch ends only at actual exhaustion/cancellation. After
  REQUEST_END, parent consumer frames must return before any proven scratch
  lifetime transition; decoded requests remain in working accounting.
- Current aggregate tickets are immutable. A future transition must preserve the
  original bank/ticket and retained backings, with conservative interruption
  behavior. This module performs no transition.
- Before STATUS reaches the peer, the next request's encoding capacity must be
  precommitted and old command aliases ended/transferred. Current command
  retirement after STATUS does not establish that ordering.
- Root341's separate Snapshot/canonical-source changes are not incorporated here.
  Neither they nor this candidate prove the complete working/backing quote.
- Supported native MCP-child owner/endpoint handoff and hosted dispatch remain
  absent from the reviewed authored source. No SDK inquiry is retried, and no
  runtime/weekly-evaluation admission follows from this implementation.

## Current phase-enabled adoption disposition

The adopted source includes the later original-ticket `CallbackPhases` changes
from `f33559cb` and bounded compiler preflight from `fb592c55`. The old-base
arithmetic and limitations above are retained as historical evidence.

Aggregate callback tickets now support guarded phase transitions on the original
bank/ticket/domain. Actual producer input exhaustion and returned REQUEST_END
consumer custody precede canonical scratch reduction. Before successful STATUS,
`before_status` transfers the complete command-parent working quote cumulatively,
retires command-owned storage and restores next-request canonical capacity.
The RPC path precommits next-request control credit before STATUS permits peer
progress. Failure/interruption retains conservative original/proposed charge
facts; no result-working reset or inferred alias death is introduced.

Snapshot/canonical scratch and compiler corrections are now present. These source
reductions do not authorize a lower standing quote. With unchanged standing65536,
session2566 and service5184, initial callback63067 totals **136353**, exceeding
131072 by5281. Command-phase callback5723 plus parent32768 and child41548 totals
**153325**, exceeding131072 by22253. Full-session composition still refuses;
callback-only successful fixtures do not establish a complete admitted attempt.

See `trial-sdk-callback-phases.md` for the current transition and retained-working
contract, and `coding-compiler-preflight.md` for the compiler scope. Complete
simultaneous backing/working/kernel/native quotes, source-owned termination of
remaining caller/client/result aliases, supported native MCP handoff and full
runtime/evaluation admission remain unresolved. The current coordinator continues
to require `unsupported_native`; no native fallback is added.
