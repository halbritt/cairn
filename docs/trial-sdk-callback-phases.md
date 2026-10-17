# Original callback ticket phase handoff

This source candidate adds a ledger transaction to the actual producer/RPC/
command-service composition. It does not establish a complete attempt quote,
physical fit, native MCP origin, hosted dispatch, or runtime admission.
Base: `49bfb5c66ea9d99dd049aaeacf9eb62e17810734`.

## One ticket, actual owner boundaries

`SharedCoordinator._bind_callback` binds its actual `SDKProducerDriver` to its
actual `ProducerRPC`. Both already borrow the same original `AttemptResources`,
native owner, producer domain, operation allocation and DATA stream. The named
phase floor is checked before callback-port construction or reservation. No second
producer domain or operation slot is invented. Legacy unbound RPC users keep
their existing immutable allocation behavior.

`prepare_command` calls `prepare_command_phase` before constructing a command.
It returns False, without allocating or dispatching, while producer input is
incomplete. The caller continues bounded coordinator turns and retries the same
pending request. Entry to the command phase requires all of:

- REQUEST_END's consumer and actual receive loan have returned; RPC and producer
  are outside their busy scopes, with no I/O, queued send/receive, auxiliary
  borrow, pending DATA promise or protected future receive.
- Producer canonical input is exhausted, its iterator closed/dropped and pending
  chunk cleared. Generated and actual written sizes and both hashes match the
  original input binding. The actual original native input reports known closure
  and the same written count. Flags alone do not replace those owner observations.
- The original ledger phase capability is current and its exact allocation row,
  domain and operation slot still match.

Only then can the producer and request canonical scratch partitions end. The
command is prepared and handed to the original gate, operation and service link.
The coordinator drops its operation alias after successful handoff.

Successful output reaches `phase_return` at the end of `_text`. A subsequent
RPC step runs after that frame's final local stderr alias and all send loans
have ended. It adds the command parent's **entire working reservation** to the
callback ticket, then retires the closed command, then restores the next request
canonical reservation. Only afterward can STATUS be queued/sent. A peer starting
its next canonicalization during the STATUS syscall is therefore already funded.
Command retirement clears its retained digest as well as chunks/decoders/results
before releasing its original parent allocation.

Borrowed `PendingRequest`, `CommandTextOutcome`, request graphs and command object
references can survive in callers. Their working charges accumulate across every
command: returning to request phase never subtracts or overwrites the preceding
transfer. This candidate has no API for proving the end of those external aliases
or reclaiming their retained working charge. Final callback retirement retains
its existing source-owner requirement to end/transfer **all** aliases first.
Calling `retire()` or observing Python refcounts is not that proof.

## Retained raw backing inventory

These are named source bounds for healthy paths, **not a complete quote**.
The implementation subtracts only the two explicitly ended canonical scratch
partitions. All initial raw bytes above those partitions stay reserved.

| Partition | Bytes | Lifetime/source |
| --- | ---: | --- |
| Producer canonical scratch | 24576 | Three 8192-byte chunks; driver clears pending before pulling next; iterator is dropped only at verified exhaustion |
| Request canonical scratch | 32768 | Four 8192-byte chunks: `ProducerClient.call` retains old local chunk during `next(chunks)`; client closes/drops before REQUEST_END; parent canonical hash/consumer must return |
| Parent + client transport roots | 4160 | Two sets of body 512, tx 544, rx 544, auxiliary 480; held for callback lifetime |
| RPC output encoding chunk | 512 | `_text_chunk` plus its views; held by callback, not command parent |
| Producer stdout/stderr slabs | 1024 | Two 512-byte slabs; still available after input exhaustion |
| Producer decoder transient | 12 | Old carry 3 + head-copy 3 + concatenated head 6 |
| Client output decoder transient/carry | 15 | One decoder's transient 12 plus other decoder's carry 3 |
| Named surviving raw floor | 5723 | Sum of the five surviving partitions above |

The named initial floor is **63067**, higher than the earlier 62532 aggregate
fixture. The old 36928 RPC-only fixture cannot establish aggregate coverage.
Checking this floor only rejects demonstrated undercounts. Hash state, other
retained bytes, Python allocator/interpreter/exception backings, controller,
caller, kernel/transport and native endpoint storage still need a complete quote.
No minimum or positive working integer is accepted as proof of that quote.
The original aggregate working quote must independently cover client-returned
results and caller inputs across the whole callback; transferring command-parent
working does not prove a bound on those separate retained client aliases.

The active view in `_text -> _result_frame -> _refuse` is a view of the RPC-owned
512-byte chunk, not command-parent raw storage. Immediate refusal retirement is
therefore permitted on the demonstrated path: the whole parent working charge
has transferred, and the callback's surviving raw reservation still covers that
chunk while the caller frame/view unwinds. The regression forces the real
`_result_frame` refusal with a live view, reads that view after command retirement,
and checks unchanged callback raw plus increased working. Cancellation likewise
waits for the actual command to close before transferring and retiring it. This
does not authorize externally retained private command raw buffers; the fixed
builder must not export those aliases as uncharged borrows.

## Transaction and uncertainty

`CallbackMemory` is an immutable identity capability, not a replaceable ordinal.
The ledger permits only input_request -> command -> return -> request -> command.
Every transition retains the exact original ticket/domain/slot, publishes a new
immutable row and phase generation, updates raw/working aggregates and all peaks,
and retains one bounded old/proposed record with totals and prior peaks.
Working never decreases. Command entry cannot grow raw; working transfer cannot
change raw; request restoration cannot shrink raw. Foreign, stale, recreated,
replayed and released phase capabilities refuse before publication.

Denial leaves the prior row/state unchanged. The public RPC boundary freezes on
denied transfer, retaining the command and suppressing STATUS. Mutation uses the
existing `_begin`/`_known`/outer guard protocol; an interrupt during row, aggregate,
peak, return-to-owner or final busy cleanup makes the bank unavailable. No retry
or automatic refund follows. Snapshot retains old/proposed evidence and reports
conservative maxima when unknown, even if a scalar update preceded row publication.
Before evidence publication no new totals/rows have changed; the prior record is
retained. A conservative unknown snapshot can overcount mutually exclusive phases;
it is not usable capacity or a claim about actual physical allocation.

## Tested slice and refusal limits

Inert tests exercise actual SDKProducerDriver input writes/receipts, ProducerClient
calls, ProducerRPC framing/credits, original gate, CommandOperation and ServiceLink/
CommandService. Only OS/readiness/transport edges are synthetic; no process, SDK,
provider, account or native endpoint is invoked. A trace verifies the consumer and
`_text` frames are absent at successful charge transfers. Another test runs a real
next canonical encoder during STATUS send, before send returns.

The successful slice's explicit, incomplete fixture is callback raw 63067 and
working 129 MiB (existing RPC 128 MiB + producer 1 MiB), service standing 5184 / 1 MiB, and
unchanged command parent 32768 / 64 MiB and child 41548 / 64 MiB. Preparation remains
40000 / 32 MiB within the child quote. No per-call quote is reduced.

With that fixture, callback raw is 5723 during command and 38491 after return;
callback working is 193, 257, 321, 385 MiB after successive completed calls. Four calls
complete. The fifth child allocation refuses: callback 385 + service 1 + parent 64
+ child 64 is already 514 MiB, before raw. The fifth parent remains charged and the
bank becomes unknown; there is no fifth dispatch or charge reset. This is a
fixture capacity result, not a promised number of supported production calls.

The complete coordinator still refuses with the existing standing 65536 and
session operation 2566: initial named total 136353 exceeds original 131072 by 5281.
During command the named total would be 153325, exceeding it by 22253. The existing
public full-session over-cap refusal tests remain in place. Snapshot's smaller
actual read block does not by itself prove a lower complete standing quote.

The original limits remain 128 KiB raw, 512 MiB combined raw+working, 4 MiB traffic and
the original five-second callback deadline. Full raw/working/kernel/native quote,
supported original MCP-child handoff and hosted dispatch remain missing. No full
weekly evaluation or runtime admission follows from these tests.

## Doctrine and review scope

Repository/user contracts govern this isolated implementation. The supplied clean
discovery packet `pkt-07a3218e0a4b6217` has content hash
`07a3218e0a4b6217d549a11bf858f45402d7b402d7a35d5d799f7e1ced3fe28a`, doctrine
`doctrine-f6bbb5196a3f8bf9`, retriever `retriever-ec995ecdd083b2c8`.
It is not a final decision receipt. A fresh retrieval-state gate/reassembly cannot
be run within the 65,536-byte authored-source rule: release metadata includes
39 oversized doctrine artifacts (including routing-index.yaml at 464959 bytes). No such
source was read. The candidate is repository/test evidence, not a doctrine-backed
full-integration conclusion.

Material full-integration obligations remain the complete quote and actual
endpoint/capability/end-to-end evidence; they are outside this source-only claim.
Authority, affected state, source owner boundaries and inert verification are
recorded here and in the tests. Database delivery/migration obligations are
nonmaterial to this change because it changes no store, schema or database path.
External runtime verification remains expressly provisional. The AI-failure-mode
pass checks actual call sites, primary-preserving recovery, no mocked production
fallback, truthful qualification and tests at owned boundaries.
