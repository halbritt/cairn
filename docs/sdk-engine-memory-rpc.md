# Producer memory RPC library

`trial_sdk_engine_memory_rpc_client.ProducerClient` implements the synchronous
`call(operation, args, body, timeout)` channel consumed by `EngineMemoryAdapter`.
It blocks only in the independently owned producer. The parent
`trial_sdk_engine_memory_rpc.ProducerRPC.step()` advances at most one socket
edge, one command-coordinator step, or one local protocol transition. It drives
the existing `CommandOperation`, `ServiceLink`, and attempt `MemoryDispatchGate`.
It creates no replacement gate, process owner, or resource bank.

This is a source library slice. The fixed builder, actual engine entrypoint,
existing producer-driver ownership handoff, and serialized stdio port remain
integration obligations. No native MCP admission or accounting is added: that
path still has a separate bypass. Current-read permission, source provenance,
configuration, credentials, destination authority, and full design acceptance
are not established by this library or its pure tests.

## Fixed construction and handoff

The builder first binds the actual producer `NativeProcessOwner` to the original
`AttemptResources` and holds one operation allocation covering both RPC
endpoints and the producer's complete callback lifetime. It supplies that exact
owner, domain, allocation, shared gate/service link, and actual callback
`StreamEvent` to `ProducerRPC(..., quote=RPCQuote(...))`. The callback's observed
first byte fixes its five-second deadline. No caller role flag or PID supplies
authority. Parent identity checks reject mismatched held capabilities; they do
not replace the builder's source and current-read checks.

`prestart(directory)` creates the listener and commits the initial receive
before the builder launches the actual producer. `accept()` binds the accepted
peer to that held owner. In the child, construct
`ProducerClient(original_callback_deadline, ClientQuote(...))` only after its
parent allocation is held, then `connect(path)`. There is no alternate transport
or default response. One producer has one live call.

The parent receives a complete bounded request and stops at `handoff`.
`pending_request()` borrows immutable ordinal, deadline, `HookRequest`, canonical
size, and SHA-256 facts under the still-held allocation. After the fixed builder
checks the actual request's authority, it constructs and prepares the actual
`CommandOperation` and calls `start_operation(operation)`. The bridge requires
the same service, resources, gate, exact deadline, operation/arguments/body,
canonical size/hash, undispatched `prepare` state, and healthy operation. A
wrong body, duplicate handoff, changed deadline, or already-dispatched operation
fails and retains uncertain custody. No operation is constructed automatically.
Source policy can instead call `cancel(2)` before handoff.

`step()`, `interests()`, and `snapshot()['local_work']` expose bounded scheduling.
The outer coordinator must continue SDK/session progress, producer stdio, command
service work, and actual process wait/release observations within the original
callback deadline. Each call tightens that deadline to
`min(callback_deadline, call_start + timeout)` before canonical request work;
request transport and command preparation do not restart it.

## Additive Python profile 5

The existing 32-byte frame header, sequence/operation checks, and 544-byte record
ceiling apply. Profiles 1–4, profile 3's exact table, signed four-byte kind 67,
and the CJS codec remain unchanged. Fields below use network byte order.

| Direction | Kind | Body | Meaning |
|---|---:|---|---|
| C2B | 35 | `>QI32s` | BEGIN: absolute call deadline, canonical request byte count, SHA-256 |
| B2C | 69 | `>H` | REQUEST_CREDIT: next request body maximum, 1–512; zero permits END |
| C2B | 33 | 1–512 bytes | One credited canonical JSON chunk |
| C2B | 34 | empty | END after exact byte count and hash |
| B2C | 70 | `>QQQQ` | META: original stdout/stderr raw counts, stdout/stderr UTF-8 wire counts |
| B2C | 65/66 | 1–512 bytes | Ordered stdout then stderr text, strict UTF-8 on both wire streams |
| B2C | 67 | `>i` | Actual waited CLI return code after exact advertised text counts |
| B2C | 68 | `>B` | Known refusal 1–5, followed only by session closure |
| C2B | 36 | empty, operation 0 | SESSION_END, idle or after known refusal |
| B2C | 71 | empty, operation 0 | SESSION_CLOSED acknowledgement |

Call ordinals increase across successful calls. Requests are canonical objects
with exactly `operation`, `args`, and `body`; the semantic request limit is
128 KiB. Allowed shapes name `search`, `pull`, `pull-evidence`, or `history`,
without granting those reads. `BEGIN → CREDIT/chunk → CREDIT(0)/END → META →
text → STATUS` is finite. Malformed framing, ordering, hash, decoding, or unknown
transport outcomes permanently fail the channel; no replay occurs.

META uses the actual `CommandTextOutcome` raw counts and the real command-service
transcript's validated wire counts. Stdout is strict UTF-8; stderr replacement
can encode up to three times its original bytes. Each original stream remains
bounded at 1 MiB. The bridge encodes at most 128 text characters per local chunk,
then sends at most 512 bytes; the client decodes incrementally. Neither endpoint
creates a whole-output UTF-8 byte copy. The result preserves nonzero CLI status,
text, and original counts. `EngineMemoryAdapter`'s existing sticky failure path
receives transport/refusal exceptions unchanged.

Known refusals use reasons 1 invalid/request policy category, 2 source policy,
3 traffic capacity, 4 cancellation, and 5 command outcome unavailable. They do
not fabricate a waited CLI result. Cancellation drains any already-granted
single request record and closes a started command through its real coordinator
before refusing. Missing EOF, deadline expiry, or any uncertain boundary retains
the original promises/allocation and freezes further work.

## Original resources and fair stdio

The parent is the sole observer for each producer RPC edge. A known send and its
matching receive count once. CLI bytes, command-service records, and producer
relay records are distinct transfers in the same original 4 MiB traffic bank;
META's counts themselves cause no second CLI debit. The existing original
128 KiB raw and 512 MiB raw-plus-working ceilings remain unchanged.

`RPCQuote` covers the entire callback, including both endpoints, request
canonicalization/decoding, all retained engine results, and simultaneous producer
driver storage. Its minimum shape is child raw 32768+2080 and parent raw 2080
more; working must cover both sides. The 2080 per endpoint includes fixed body,
TX/RX, and auxiliary storage. Shape checks are not physical-fit measurements.
The builder must quote additional driver/input/output working storage before
creating it. Every `CommandOperation` retains its separate explicit operation
and child preparation/job/text quotes until its actual closure and retirement.

After the zero-byte request credit, the client closes its canonical generator
and drops the yielded chunk before sending END. The parent rehashes only after
receiving END, so these canonical scratch lifetimes do not overlap. Failure to
close the generator fails the channel without sending END. This phase ordering
does not establish the builder's complete working-memory or physical-fit quote.

The bridge holds 173 original control bytes before peer emission: initial
BEGIN-or-END receive 76, REFUSE 33, END 32, ACK 32. No more than two promises
are active. Before successful STATUS lets the child emit another call, the next
173-byte control credit and initial receive are installed. The old unused 97
bytes are settled first; at least 76 genuinely global bytes plus any unmet
STATUS 36-byte requirement must be available. Known capacity refusal uses the
still-held old emergency control credit.

`stream_credit()` exposes the one actual producer DATA `StreamCredit`, held for
the **whole producer lifetime**, including finite stdin before RPC, stderr during
RPC, and final stdout/stderr after channel closure. It is never settled between
transactions. The coordinator must not grant another DATA ticket or invoke a
producer-driver method that independently grants one.

Before CREDIT TX34 reaches the child, the bridge protects its exact future RX
maximum (32–544) on this same ticket. Protection is a commitment, not an active
promise or raw loan. While TX is blocked, TX and one stdio promise may coexist.
After known TX, protection transfers directly to the RX promise with no refund
or reacquisition gap; RX and one stdio promise may coexist. Even a reply emitted
before send returns is already backed. The source coordinator serializes one
`promise_stdio(1..512)` action, grows only from genuine original availability
before its actual owner read/write edge, and records the fresh native I/O receipt
before another RPC step. EAGAIN records zero for that action. An uncertain
cleanup records its retained actual prefix and freezes all held custody. Quiet
producer intervals, free stderr drains, and parked stdio for an entire RPC are
not assumed. The fixed stdio adapter itself remains a follow-on integration.

## Closure and retained aliases

`ProducerClient.close()` sends SESSION_END, validates ACK, and **waits for actual
parent channel EOF** under the original callback deadline. It must finish before
engine final stdout or producer exit. Parent completes its actual ACK send and
post-send peer-liveness check while the child waits for EOF, then closes its
channel and listener. ACK alone is insufficient. Zero-call and known-refusal
sessions use the same sequence with the preheld control bytes.

Channel closure does not release the producer lifetime or result aliases. The
same stream credit remains available to the fixed stdio port for final output.
After source callers end or transfer all aliases and actual producer wait/release
is known, `ProducerRPC.retire()` settles the stream, retires backings, and releases
the held callback allocation. The outer owner still controls domain retirement.
An uncertain edge retains reservations and uses the shared record-custody helper
to reconcile fresh actual byte receipts before freezing. Fixed and stream credit
receipt-staging repairs are separate dependencies, not relaxed error handling.

Final busy-state cleanup is inside the failure boundary for parent prestart,
accept, operation handoff and step, and client call and close. An interruption
there fails the client or freezes parent custody even after successful protocol
progress. An earlier recorded parent failure remains primary; cleanup does not
retry poisoning already marked unknown. Known refusal still allows its normal
bounded close handshake when cleanup succeeds. These guards do not establish
safety under repeated interruption of the failure handler itself.

## Validation scope

Pure tests compose real original ledgers, codecs, channels, RecordSocket custody,
CommandOperation, CommandService, fixed command policy, and native I/O receipt
logic with inert OS/process boundaries. They exercise two successful calls,
nonzero status, large replacement text, early reply precommit, EAGAIN plus
charged stderr on the same ticket, cleanup uncertainty, fixed-source refusal,
known capacity exhaustion, wrong-request handoff, deadline expiry, and zero-call
ACK/EOF. These tests do not launch an engine, CLI, service, or socket, and do not
establish runtime admission, source permission, native coverage, or usefulness.

Required-hit cleanup interruption tests also cover the six guarded methods,
client busy-state entry, first-failure preservation and generator cleanup before
END. They assert sticky failure and refusal of further work after the injected
fault, not merely that an exception was raised.

Parent recovery attempts receipt reconciliation, stream quarantine, control-credit
quarantine, command-operation quarantine, and final ledger freeze independently.
A failure at one recovery port cannot replace the primary exception or skip the
remaining ports. Fixed `recovery_faults` codes expose incomplete recovery; local
RPC failure remains terminal even when ledger freeze itself fails. This does not
claim that failed recovery succeeded, refund any reservation, close a live owner,
or authorize a replay. Actual process and descriptor cleanup remains with its
original owner. Repeated interruption of recovery bookkeeping is outside the
trusted serialized contract.

The shared send/receive exception boundary also protects immediate receipt-prefix
recovery, before the outer parent guard runs. A secondary prefix failure sets
`io_recovery_error` and annotates the original exception with
`record_prefix_recovery_unknown`. The original I/O context, promise and loan stay
held for outer recovery. Tests retain the actual 76-byte BEGIN receive and
34-byte CREDIT send while a post-transfer primary and ledger-freeze failure
coincide; neither error is interpreted as zero traffic or permission to replay.
