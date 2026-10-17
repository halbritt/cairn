# Command service execution core

`scripts/trial_sdk_command_service.py` implements one serialized service core
using the existing `CommandJob`, `FixedCommandPolicy`, strict incoming decoder,
and profile-4 `ServiceTranscript`. It creates no endpoint, process supervisor,
credential route, original resource pool, or admission capability. Construction
is inert. A subsequently authorized `START` invokes the real job's synchronous
launch method. The intended parent scheduler therefore uses this core inside a
prestarted, independently owned service; calling it in the parent would retain
the existing synchronous-launch scheduling problem.

This slice has pure boundary tests. They exercise the real core, job, policy,
Codec and Channel with an inert NativeProcessOwner constructor boundary. They
do not launch a service or command, authenticate a peer, perform a consequential
read, or establish native/capture/task success. The full runtime gates remain
unavailable. See [command wire](sdk-command-service-wire.md),
[fixed command policy](sdk-command-policy.md), and
[command job](sdk-memory-command-job.md) for their separate contracts.

## Builder and transport contract

The fixed builder constructs:

```python
budgets = ChildSublease()  # Inactive: no allocation or traffic allowance.
service = CommandService(
    policy, cwd, env, budgets, preparation_raw, preparation_working,
    reply_storage, clock=monotonic_ns,
)
```

`policy` is the exact fixed policy type; `cwd` and `env` are trusted service
configuration. The builder retains exclusive custody of these dependencies
and the mutable environment for the service lifetime. `reply_storage` is one
writable contiguous 512-byte memoryview in already reserved standing storage.
Construction borrows it. The transport/builder, rather than this core, ends that
standing allocation after all reply and frame aliases end. There is no implicit
reply buffer or production fake-job factory.

The transport calls `accept(frame)` only for an authenticated, validated,
known-received parent frame. `pending()` reports `(kind, operation, size)` or
`None`. `copy_reply(destination)` copies that one report into an already
reserved writable view of exactly `size` bytes. The transport encodes it using
the actual profile-4 Channel, retains the same pending loan/token across
EAGAIN, and calls `sent(frame)` only after the exact frame was known sent.
The core checks its identity and digest before advancing its transcript.
Calling `sent` is not itself send evidence. No method accepts a caller permit
boolean, arbitrary executable, PID, descriptor or credential route.

There is at most one pending reply. A known incoming CANCEL may be observed
while that reply is retained. The reply is not overwritten: its known send is
followed by a separate REFUSE6 acknowledgement. This requires the crossed-report
wire correction `d4b652a6cdd9af472302fb402eb2bcb0a33f035d` (isolated dependency
`04707d3` here), including crossed PREPARED and prior refusal handling. CANCEL
does not restore START or result eligibility. Missing/unknown send, receive or
observation returns must be quarantined by the owning coordinator; it must not
catch an uncertainty and replay the operation.

## Preparation and original resource ownership

PREPARE names an already-held parent allocation by correlation lease, raw quote,
working quote, exact request length/digest, operation and original monotonic
deadline. Scalars do not authenticate that allocation. The parent must validate
the exact request's current-attempt receipt/evidence/history membership and
fixed binding before authorizing this sequence. Policy reconstruction supplies
shape and fixed routing, not read authority. The child never obtains such
authority merely by producing PREPARED.

`ChildSublease.open(lease, raw, working)` mirrors that parent-held allocation.
It has no default full pool. Raw is at most 128 KiB and raw plus working is at
most the same original 512 MiB; these are ceilings, not new allowances. The
core first calls `reserve(preparation_raw, preparation_working)`, then creates
the decoder and later parses, builds the argument tuple, and invokes the actual
fixed policy. The exact real job is retained before `prepare` or `start` can
have effects. Its existing raw/text/body reservation is a second disjoint live
suballocation under the same parent quote. The mirror permits at most those two
live allocations, checks exact releases, and retains uncertainty rather than
refunding it. Reservation refusal constructs no job or parser graph.

The explicit preparation quote is a builder dependency, not a measured or
universally sufficient heap bound. It must cover the request decoder text and
parts, parsed graph, argument list/tuple, canonical policy text and chunks,
temporary normalized Unicode, output hashing/encoding slices, metadata and
bounded reply assembly copies for the selected request. Encoding uses slices
of at most 128 characters (at most 512 UTF-8 bytes), never a second whole-output
byte string. Delivery can temporarily hold two streams' encoded carry slices,
a 512-byte assembly buffer and bounded slice copies. These belong in the
preparation reservation. Standing reply/frame/RX backings, interpreter objects
and actual process working sets require their own original owner accounting.
Neither this mirror nor a scalar quote measures physical memory or proves that
the selected aggregate fits.

REQUEST frames are counted and hashed by the existing transcript. Early credit,
deadline or decoding refusal is held until the declared finite request and
REQUEST_END arrive; the remaining frames need no parser/graph allocation.
This preserves already-promised bytes without changing the wire protocol.
A same-direction CANCEL may interrupt that drain. Malformed length/hash/order
still poisons the transcript rather than receiving a fabricated completion.
The outer scheduler enforces the original deadline while the core is parked,
including a parent that never sends END.

## One explicit CLI turn and one output delivery

START is accepted once after PREPARED and the parent's real gate debit. It uses
only reconstructed argv and trusted cwd/env. The original absolute monotonic
deadline is passed to the job unchanged. The service checks it before launch,
preparation, output hashing and delivery; the real job checks its own actual
effects. Successful return from the actual job's start queues STARTED, carrying
the exact lease. A known refusal queues REFUSE instead; a partial/unknown launch
retains its independent owner facts and must not fabricate STARTED. The parent
must receive STARTED before sending TURN; silence is not a success response.
STARTED may cross CANCEL, preserving its report before separate REFUSE6 without
restoring job eligibility. It does not attest CLI exit, API success or source
authority. Its 40-byte record spends the same original traffic budget.
No-arrived TURN means no `job.step` call. Interest queries only borrow
the existing job's readiness handles; this core does not duplicate or close
them.

Each authenticated TURN calls `begin_turn(grant, maximum)` then exactly one
actual `job.step()`. The mirror supplies `stream_room` and `charge_stream` to
that job. The service compares known input/stdout/stderr deltas against the
actual charged bytes before `finish_turn`. PROGRESS carries those deltas,
actual owner stdin-close state, each observed EOF, job-owned CLI wait and known
release. A premasked close attempt is not a closed-stdin fact. The transcript
enforces ordered turns and the finite final allowance: known-unused bytes can
be granted again under a new ordinal without replenishing that allowance;
zero-final readiness/EOF turns cannot revive positive room. The scheduler must
park on actual readiness rather than spin on zero progress.

Known CommandJobError paths produce a bounded PROGRESS/REFUSE sequence when a
turn was consumed. The existing final progress byte is outcome 0 for healthy,
1 for unknown, and 2 when a known CommandJobError requires the next REFUSE.
For example, a known stream limit or deadline may retain an accepted prefix
without setting job outcome_unknown. Outcome 2 therefore consumes the known
turn/actual allowance and prevents the parent granting another turn before that
refusal; it is not mislabeled healthy or uncertain. Unknown progress retains
the unsettled turn and allocation. Both forms preserve cancellation obligations.
Unexpected exceptions poison the core and propagate, preserving the independently
held job and known snapshot prefix; they do not invent a successful receipt.
The parent must quarantine the full outstanding ticket on such a missing or
unknown receipt, then independently collect actual service cleanup facts.

Only actual job wait, stdout/stderr EOF and known owner release permit taking
the held outcome. OUTPUT retains original raw counts/hashes and bounded strict
UTF-8 text counts/hashes; stdout is strict and stderr uses the existing job's
replacement semantics. Each explicit DELIVER produces at most its granted
512 bytes, even across a multibyte character boundary. Text aliases remain
charged until release. STATUS uses the actual job's raw wait status and return
code, including nonzero exits; it is neither API confirmation nor the parent's
independently observed service wait.

CLI bytes are counted by the child's explicit sublease turns. RPC bytes,
including framing, request, output, cancellation and release messages, remain
the parent transport's responsibility under the same original cumulative
4 MiB. No control byte is exempt and no extra pool is created here. Mandatory
control/terminal headroom must be reserved before effects. A prior exact-4-MiB
standalone CLI fixture does not demonstrate room for this RPC flow.

## Cleanup and retained uncertainty

`cleanup_step()` is an explicit abandonment/cleanup action, not ordinary polling.
It preserves the real job's once-only signal/wait/release behavior and remains
available after core poisoning. The scheduler supplies later actual readiness;
the core does not wait in a loop. Cleanup does not infer missing EOF or success.

RELEASE consumes local retirement once actual job cleanup has supplied its
release fact (or no job was constructed). Known release first ends the core's
outcome, decoder, plan and text aliases, retires the real job reservation, and
releases the preparation allocation. `end(lease, unknown)` records only the
local mirror's ending. RELEASED with unknown set retains unresolved allocations
and grants. A retirement interruption propagates and leaves consumed cleanup
and unknown state; it is not swallowed as a known release or retried.

The parent retires its original allocation only after authenticated known
RELEASED, its own aliases have ended, and all separately required actual owner
facts hold. A missing release or peer death needs the parent's independent
service lifecycle cleanup. The pure mirror's counters are not proof that another
process has freed storage. After any unknown release, a new operation is
unavailable. Diagnostics are detached fixed-size scalar structures; snapshots
do not export held output text or credentials.

Tests include required real `sys.settrace` line-interruption triggers across
sublease return cleanup, service cleanup, and retirement. The guards cover the
demonstrated source intervals, not every asynchronous VM return event. The
coordinator must retain ownership before calls and quarantine ambiguous returns
even when the callee's diagnostic could not record the interruption.
