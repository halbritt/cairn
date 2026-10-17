# Fixed child command-service runtime

`trial_sdk_command_service_runtime.py` supplies the child-side `serve(binding)`
entrypoint and its independently retained `CommandServiceRuntime`. It uses the
real `RecordSocket.connect_parent`, profile-4 Codec/Channels, CommandService,
ChildSublease and CommandJob. There is no production substitute job, endpoint
factory, arbitrary command-line parser, permission boolean or fresh traffic pool.

This is source and pure boundary validation. The tests replace OS/socket/poll
boundaries and, in selected job cases, the native-owner constructor with an inert
owner. They do not run a subject, authenticate a kernel peer, establish capture
custody, invoke an API or prove runtime admission. The actual fixed builder,
parent scheduler, original ticket binding and ordinary subject validation remain
separate integration work. Full CAIRN-107 remains unavailable/HOLD.

## Fixed builder interface

The builder constructs a `RuntimeBinding` containing the exact fixed policy,
cwd, environment, private record-socket path, absolute monotonic service deadline,
child standing raw/working subquote and operation preparation raw/working quote.
This is a source-owned Python object, not an untrusted serialized configuration.
The builder must hold the original parent allocation before the child is started
and maintain exclusive custody of policy, environment and source. Scalars and
the frozen dataclass do not authenticate that parent allocation or prevent hostile
mutation of the borrowed environment.

`CommandServiceRuntime(binding)` is inert and must be retained before `start()`.
Start consumes its only attempt, records the local claim of the parent-held
standing quote, allocates bounded backings, creates the real core, invokes
existing `Peer.protect()` and connects to the actual parent once. Parent PID and
pidfd identity come from the real socket owner; the binding accepts no PID or FD.
The parent's existing prestart closes stdin. This runtime uses only the record
channel and does not read configuration or commands from stdin.

Child raw standing backings are core reply512, encode body512, TX544, RX544 and
the existing auxiliary480: 2592 bytes. They are disjoint from the parent's2080,
giving4672 known raw bytes before operation storage. The local Root is explicitly
bounded to the preclaimed2112 backing bytes; it is not a new128KiB budget.
Auxiliary reserve/release borrows the already quoted480 bytes. The builder's
standing working quote must cover runtime/core/codec/poll metadata and temporary
diagnostic copies as well as any separately owned process/interpreter working
sets. The operation preparation and real-job reservations remain disjoint under
the authenticated operation sublease. Raw and working share the original512MiB
limit. This logical accounting does not measure universal heap or kernel fit.

## Transport and traffic ownership

`step(timeout=0.05)` polls actual readiness for at most50ms, bounded by the smaller
of the service lifetime and active operation deadline. It consumes at most one
RX and one TX record per call. Received frames pass through the actual record
credentials, codec and core. Sent frames advance the core only after a known
actual send. EAGAIN retains the exact same body, Channel token, sequence and core
report; it never re-encodes or repeats a job action. The shared scratch and frame
loans remain held if an outcome becomes unknown.

Before sending a parent trigger, the parent must reserve the entire possible
bounded automatic response burst under original WireCredit. This includes READY
before prestart, preparation/start acknowledgements or refusals, progress/output/
status chains, a crossed immutable report plus cancellation ACK, RELEASED and
BYE. The child has no default or independently replenished traffic allowance.
Its known send/receive counters are observations of the same designated edges;
only the parent's original ledger settles them. Actual positive syscall counts
remain recorded even when a later credential/lifetime/codec/core check refuses.
An interrupted copy can recover the socket's fresh retained receipt without
repeating I/O. Unknown receipts still require parent quarantine of the full
unresolved commitment.

All framing, request and output bytes and mandatory cancellation/terminal
headroom count against the original4MiB aggregate alongside CLI traffic. A
standalone exact4MiB CLI test does not establish room for this record flow.
The runtime's4MiB observation ceiling is only a fail-closed diagnostic bound;
it does not mint grants or discount any edge.

## Progress pacing and deadlines

Without an arrived TURN, the runtime never calls `job.step`. A healthy PROGRESS
with zero input/stdout/stderr deltas and unchanged lifecycle is retained as one
immutable pending report/token. The record socket remains readable for CANCEL;
its writability does not count as CLI readiness. The runtime borrows the actual
job's current stdin/stdout/stderr/pidfd interests, excluding already-closed or
observed endpoints. A CLI readiness event releases that same queued report for
send; it does not perform another job step. The parent can then issue a distinct
explicit turn. New stdin-close/EOF/wait/release bits are actual progress and are
reported immediately. Errors and terminal facts are never paced as healthy zeros.

CANCEL releases a paced original report for its known send, followed by the
separate cancellation ACK. The core retains its existing once-only cancel and
cleanup semantics. If the original active-operation deadline expires while
parked, the runtime poisons and begins independent cleanup. It does not emit a
late healthy report, reset the deadline or introduce a grace period. Parent
pending-dispatch state and unresolved tickets must independently fail/quarantine.

The fixed builder must compose the explicit STARTED acknowledgement and PROGRESS
outcome distinctions selected for profile4. Runtime pacing recognizes only
healthy outcome0; unknown1 and known-refusal-pending2 bypass pacing. These wire
and core changes are an independent source prerequisite, not supplied by this
runtime or inferred from a quiet socket.

## Terminal closure and independent cleanup

After the core's exact BYE frame is known sent, the service stays alive. The parent
must consume that BYE and close its record channel while remaining alive to collect
the service. Only then does the runtime call the socket's distinct terminal
receive method. Ordinary `receive_into` continues to reject zero-length records.
Terminal completion requires an actual zero receive, no ancillary data or semantic
message flags, actual HUP/RDHUP readiness, still-live original parent before and
after the observation, and known auxiliary cleanup. A credentialed zero packet,
unexpected positive record, rights, peer death or unknown cleanup is not EOF.
See [the socket contract](sdk-memory-broker-socket.md) for the Linux assumptions.

On that known terminal observation, the runtime closes its actual owned socket and
parent pidfd, ends core reply aliases and retires local backings. Local
`standing_released` records only that local closure. The parent still owns the
original allocation and must retain it through its independent actual service
wait and all remaining aliases. BYE, EOF and local release are distinct facts;
none is task/API success or a downstream CLI wait.

Refusal/cancellation cleanup begins once and later parks on the actual job pidfd.
This prevents RELEASE from waiting forever merely because no further TURN is
permitted. `cleanup_step()` also remains available after transport failure: it
uses the real job's once-only signal/wait/release path, then polls only within
the remaining original deadline. No missing EOF is invented. `close()` attempts
channel cleanup independently even if job cleanup fails, retains consumed-close
uncertainty, and never retries detached raw descriptor numbers. The fixed outer
owner still provides final wall/descendant containment when the service cannot
collect a known job release.

The exported `serve` loop preserves its original primary exception while
attempting independent cleanup. Source guards and required-injection trace tests
cover demonstrated intervals, not every asynchronous VM return event. The parent
must retain the actual operation and original commitments before calls, and must
quarantine an ambiguous service/transport return rather than replay it.
