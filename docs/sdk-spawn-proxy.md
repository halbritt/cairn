# SDK process proxy source component

`scripts/trial_sdk_spawn_bridge.cjs` implements the published SDK process seam
with a Writable stdin, Readable stdout, exit/error events, kill, and read-only
exitCode/signalCode/killed. It injects an owner for all process and transport
operations. It imports no SDK and starts no process. The real native adapter,
authenticated IPC, executable mapping, callbacks and runtime qualification are
still unavailable.

Select `createSpawner(owner)` for actual-write callbacks, or the distinct
`createCustodySpawner(owner)` for decoded-custody callbacks. Pass its synchronous
`spawn` function as the custom SDK spawn callback. Each factory accepts one launch attempt. The proxy
returns before its deferred owner launch so callers can attach listeners. Spawn
options are snapshotted; command, argument order, environment and cwd are preserved.
No print flag or command replacement is inserted by this component.

## Owner boundary

The owner supplies launch, write, end, credit, signal, abort, stderr and release.
Launch returns a promise resolving to exactly `{launched:true}`. A rejected or
unknown completion emits an error; it cannot manufacture a process exit.

Write borrows a body of at most 512 bytes until its completion reports exactly
`{accepted:n}`. The count means bytes actually written into native stdin. An IPC
acknowledgement is insufficient. Partial progress preserves the unwritten suffix;
zero progress waits for a separate writable event. Unknown counts fail. A throwing
write retains its uncertain loan until an explicit completion returns. Once a
loan has returned, discarding its local encoded suffix releases that reservation.

The owner calls stdout with credited chunks, stdoutEOF for actual native stdout
EOF, stderr for bounded observations, writable after backpressure, wait with one
actual owned exit result, or error after transport failure. EOF, accepted signal
and actual wait are separate observations. Only the forwarded SDK AbortSignal
triggers abort; this component adds no cancellation grace period or kill timer.
Release ends the owner's subscription after observed wait, native EOF and input
completion/failure with no outstanding write loan. Locally staged text can still
be retained after release and remains charged until consumed or discarded.

The custody factory requires `custody` and `endCustody` instead of `write` and
`end`, with no fallback. `custody(id,body,done)` returns exactly `{custodied:n}`,
using its own field and a bounded integer prefix count. This permits a producer
to release one fragment before a full line is available. The intended receiver
retains original spelling as decoded working text, validates the complete line,
then writes it. Later validation failure can terminate input despite earlier
custody callbacks. A custody receipt never increments `accepted`.

`endCustody(done)` may succeed only with
`done(null,{closed:true,nativeWritten:allCustodiedBytes})`, after actual validated
drain and observed closure of the owned native stdin descriptor. Missing or
mismatched receipts fail finalization. Wait, stdout EOF or a profile2 status bit
cannot substitute for that observation. An unresolved end callback also holds
owner release after transport failure. A throwing call does not return its loan;
the outer deadline/abort owner must obtain definitive completion or retain an
explicit unresolved-cleanup outcome. Never clear the pending flag to force release.

Custody inspection adds `custodied`, `custodyUncertain`, `nativeOutcomeUnknown`
and `endPending`. Native acceptance stays zero until the matching close receipt.
A valid late close can preserve the owner-confirmed total after primary failure
without reporting successful finalization. Unknown receipt bytes are not native
counts. The shared malformed-late-return path preserves an earlier primary error
for both factories. All snapshots remain `qualification:"unqualified"`.

Owner receipt data must be ordinary, nonthrowing data with no getters or proxies.
Each call has one definitive completion and must not throw after reporting success.
Arbitrary asynchronous receipt-object exceptions and contradictory callback/throw
behavior are outside this trusted owner contract. The eventual wire adapter must
construct these plain receipts from authenticated, validated fields; payload
claims of closure or identity are insufficient. Neither factory implements that
adapter or independently observes native writes or closure.

These calls are a source interface, not an RPC implementation. A future adapter
must establish process identity independently of payload fields, validate frames
before allocation, preserve ordering, and report actual write/wait observations.
It must serialize incoming owner events; stdout byte credit does not grant
unlimited concurrent frames. Recursively delivering owner events from a stream
consumer can exhaust the fixed IPC share and refuse. Consumer read/EOF callbacks
remain distinct from recursively injecting another transport frame.
Protected bootstrap must finish before loading the SDK and requesting native
launch. A deferred owner launch may use its requestNative gate; an asynchronous
bootstrap function cannot itself replace the synchronous custom spawn callback.

## Fixed storage and decoding

The controlled Node raw-storage target remains 24576 bytes: offered data 17000,
Readable 2048, IPC 2048, encoder 2048 and framing 1432. Reservations precede the
component's allocations and queue insertion. These are logical storage accounts;
backing allocations, object overhead, GC and opaque SDK/Node implementation memory
are not a measured aggregate-memory guarantee.

UTF-8 mode admits the decoded prefix that fits the queue and stages a bounded
suffix in the encoder share. Reads can fill the complete 1024-code-unit queue,
including a surrogate-pair boundary, without waiting for native EOF. Malformed
UTF-8 uses the selected Node decoder's replacement behavior. Native EOF flushes
that decoder, while Readable EOF waits until all staged text has transferred.

Text credit is at most 124 bytes, binary credit at most 512. The text bound leaves
room for a live 1536-byte outgoing encoder reservation, four raw decoder bytes,
and two 254-byte decoded/slice temporaries inside the existing 2048-byte encoder
share. Existing staging uses a separate bounded append/drain path; no fresh credit
is granted while it awaits consumption. An unused suffix of an existing credit
remains valid.

Switching to UTF-8 accounts for the existing binary queue and its converted text,
preserves decoder carry and refreshes peak diagnostics. Switching during an
outstanding raw credit refuses before changing mode. The implementation captures
Node's private `_readableState.decoder` after setEncoding; this is an explicit
coupling to the selected Node build. Method checks alone do not establish its
semantics or compatibility in the actual sandbox.

## Local checks and limits

Run `make test-sdk-spawn` for four guarded synthetic suites; `make test` includes
them. They use the real Node stream implementation and a synthetic external owner.
It covers partial/zero writes, uncertain loans, resource pressure, split and
malformed UTF-8, exact-capacity reads, queued encoding conversion, EOF, cancellation,
and primary/cleanup distinctions. The preload denies unselected module and process
boundaries before tests load; it is a regression guard, not hostile-code isolation.

The four suites contain 130 cases, including the independent reviewer's malformed
input, encoding-transition and lifecycle regressions. They check behavior on the
installed Node. Ten added custody/error groups cover separate receipt meanings,
partial/zero progress, a callback-gated 128KiB line, closure failures, pending loans,
duplicate replies, explicit factory choice and preservation of primary failure.
The scoped source review is conditional on the trusted owner contracts above;
these tests do not establish actual closure or end/abort teardown liveness.
Actual SDK/native/IPC authentication, context delivery, provider
capacity, fresh grading, measured usefulness and CAIRN-107 admission remain
unverified. All existing trial budgets and capture HOLD remain unchanged.
