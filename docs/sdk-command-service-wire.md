# Python command-service wire prerequisite

This slice adds Python Codec profile 4 and a pure endpoint transcript. It
does not open an endpoint, launch a service or CLI, mint a resource grant, debit
the memory gate, authenticate a peer, or certify a wait. Profiles 1, 2 and 3
retain their encodings. In particular, profile 3 kind 67 remains its signed
four-byte return code. The Node codec/addon does not implement profile 4.

Use [ServiceTranscript](../scripts/trial_sdk_command_service_wire.py) with the
existing [Codec](../scripts/trial_sdk_ipc.py) and
[Channel](../scripts/trial_sdk_parent_storage.py). The existing 32-byte header
and sequence rules apply. Profile 4 has directions P2S (parent to service) and
S2P. Each endpoint starts at sequence one. All operation messages correlate a
nonzero operation ID; READY, SHUTDOWN and BYE require operation zero.

## Bodies

All integers are big-endian and unsigned except STATUS's signed return code.
A digest is exactly 32 SHA-256 bytes. Fields appear in the listed order.
The Python `STRUCTS` mapping and `pack_into` write fixed bodies into storage
already charged by the caller. They do not authorize sending.

| Kind | Name | Direction | Body bytes and fields |
| --- | --- | --- | --- |
| 1 | READY | S2P | 0 |
| 2 | PREPARE | P2S | 65: operation enum u8, original deadline ns u64, allocation lease u64, raw quote u32, working quote u64, request length u32, request digest |
| 3 | REQUEST | P2S | 1–512 request bytes |
| 4 | REQUEST_END | P2S | 0 |
| 5 | PREPARED | S2P | 40: allocation lease u64, request digest |
| 6 | START | P2S | 8: allocation lease u64 |
| 7 | TURN | P2S | 21: grant ordinal u64, turn ordinal u64, maximum CLI bytes u32, final bit u8 |
| 8 | PROGRESS | S2P | 24: grant ordinal u64, turn ordinal u64, stdin/stdout/stderr deltas u16 each, lifecycle bits u8, outcome enum u8 |
| 9 | OUTPUT | S2P | 192: raw stdout/stderr sizes u64 each, encoded stdout/stderr sizes u64 each, raw stdin/stdout/stderr digests, encoded stdout/stderr digests |
| 10 | DELIVER | P2S | 11: grant ordinal u64, stream u8 (1 stdout, 2 stderr), maximum text bytes u16 |
| 11 | STDOUT | S2P | 1–512 strict UTF-8 bytes |
| 12 | STDERR | S2P | 1–512 strict UTF-8 bytes after the service's replacement decoding |
| 13 | STATUS | S2P | 8: raw CLI wait status u32, return code i32 |
| 14 | REFUSE | S2P | 1: closed refusal code |
| 15 | CANCEL | P2S | 1: closed cancellation reason |
| 16 | RELEASE | P2S | 8: allocation lease u64 |
| 17 | RELEASED | S2P | 9: allocation lease u64, unknown bit u8 |
| 18 | SHUTDOWN | P2S | 0 |
| 19 | BYE | S2P | 0 |
| 20 | STARTED | S2P | 8: allocation lease u64 |

Operation enums are 1 search, 2 pull, 3 pull-evidence and 4 history. Refusal codes
are 1 deadline, 2 unknown, 3 policy, 4 output, 5 UTF-8, 6 cancellation and
7 resources. Cancellation reasons are 1 deadline, 2 parent failure and 3 shutdown.
These are fixed diagnostic labels, not authority.

## Request and start ownership

The parent calls `observe(frame)` only after a known send or an authenticated,
validated receive. It may use `Token.visit`; it must not observe an EAGAIN retry
twice. The transcript checks the exact profile, direction-derived sequence,
operation, length, order, counters and digests. It returns the immutable scalar
tuple `(kind, sequence, operation)`. Snapshot dictionaries are fresh and contain
no mutable internal graph, hash object, decoder or raw view.

The service may maintain its own serialized transcript over its known sends and
authenticated receives. The two endpoints can observe opposite orders across
directions; sequences remain fixed within each direction. This duplicate local
validation is not a second resource ledger or a peer-authentication mechanism.

A successful sequence is READY, PREPARE, REQUEST chunks, REQUEST_END, PREPARED,
START, STARTED. REQUEST_END requires the exact declared count and SHA-256. Request length
is at most the existing 128 KiB document bound. The transcript hashes borrowed
chunks and does not assemble a request graph or raw document.

The separate `parse_request(text, raw_bytes, operation_enum)` helper uses the
existing strict `parse_document`. It accepts exactly
`{operation, args, body}`: the matching operation name, a list of strings, and
a string or null body. Its immutable `ServiceRequest` converts args to a tuple
only for `request.plan(fixed_policy)`, which requires the actual
`FixedCommandPolicy` and reuses all its validation. These engine-dialect args
must never become direct executable argv.

The future builder must bind the parser's exact text bytes and operation to the
held request digest and enum, validate the fixed plan, and validate current-attempt
offered receipt/evidence/history membership **before PREPARED**. PREPARED merely
reports that source convention; this transcript cannot authenticate it. The
parent supplies executable/socket/token/scope/context through fixed policy.
No such routing fields, PIDs, FDs or credentials are wire authority. A valid
plan remains inert.

The parent must retain the prepared operation before its sole gate debit and
known START send. START is once-only. The gate's forward return is a pending
lifecycle fact; API completion and service CLI completion remain later facts.

START enters start_wait. Only the matching operation/lease STARTED response
permits TURN. A known launch refusal uses REFUSE instead. STARTED records that
the owned job's launch method actually returned successfully; it is not a CLI
exit, API success, executable authentication or adoption of a reported PID.
The parent must await STARTED or REFUSE, never infer launch success from silence
or socket readiness. Duplicate, unbound or late STARTED responses refuse.

## Original resources, turns and deadline

Raw/working quotes are correlation with an original-owner allocation, never a
new pool or remote grant. The raw quote cannot exceed 128 KiB, and raw plus
working cannot exceed the same original 512 MiB. This is a reported subset
check, not measured process memory. Original resources must reserve frames,
copies, request decoding/graph/tuple creation, normalized Unicode, canonical
encoding, planning allocations and service/CLI memory **before** those effects
or helper/plan calls. The transcript itself and temporary decoder/hash results
also require reservations.

Every RPC header, control, request, text chunk, receipt and CLI byte remains
subject to the original aggregate 4 MiB accounting. The source transcript's
known CLI total bound is not a second allowance and excludes no RPC edge.
The original owner must bind actual edge charges and settle held ticket
identity. The standalone exact-4-MiB CLI fixture does not prove this RPC fits.
Parent storage's local Root and wire quote fields do not establish that binding.

At most one CLI turn and one output delivery can be outstanding; their phases
do not overlap. Grant ordinals increase across the transcript, while turn
ordinals increase within an operation. A normal turn offers 1536 bytes: at most
512 stdin, 512 stdout and 512 stderr. A smaller offer requires the explicit final
bit. A missing turn is `None`, distinct from an actual zero offer.

The first final offer fixes `final_remaining`. Known receipts subtract only
actual reported CLI deltas. Subsequent explicit final offers may be at most the
remaining amount; they cannot replenish the ceiling or revert to normal mode.
Thus partial/EAGAIN progress preserves known-unused allowance. Once remaining
is zero, only explicit zero-final turns remain possible. They may report actual
zero-byte EOF, or zero progress followed by another later readiness turn. The
scheduler must park on actual readiness/deadline; this module provides no loop
and proves no readiness. Parent final selection requires reserved mandatory
control/terminal headroom and cannot be inferred from missing credit or historical
`available_at_issue`.

The original absolute monotonic deadline is retained unchanged throughout the
operation. It must be derived from the original whole five-second callback.
The coordinator/service must enforce it around actual effects, preparation,
waiting, copying, delivery and cleanup without resetting the clock here.
Late or unknown jobs must poison the sole parent gate. There is no clock in this
pure validator.

PROGRESS lifecycle bits are 1 stdin closed, 2 stdout EOF, 4 stderr EOF, 8 actual
CLI wait known and 16 release known. Bits are monotonic; release requires all
five bits. Subsequent positive bytes after the corresponding close/EOF refuse.
Unknown progress retains its pending turn commitment and known prefix counts,
marks the attempt failed and cannot yield OUTPUT. A limit-detecting output
prefix may report the existing one excess byte before REFUSE; it can never
qualify as an outcome. These are service reports, not independent observations.

The final PROGRESS byte is the closed outcome enum: 0 healthy, 1 unknown, or
2 known refusal pending. Its body remains 24 bytes. Outcome 2 preserves known
counts, consumes that known turn and subtracts actual final allowance; it enters
refusal_pending and forbids another TURN or OUTPUT before the existing REFUSE.
It does not invent uncertainty, an exit status or an API result. That phase
survives a prior refusal crossing CANCEL, which still requires REFUSE6 before
normal RELEASE. Outcome 1 keeps the original unsettled turn unchanged; outcome
0 retains the normal progress behavior. Other enum values refuse before counts
change. The latest outcome is selected scalar diagnostic metadata, not authority.
These are explicit matching-source profile-4 changes; old source peers cannot
silently reinterpret outcome 2 or missing STARTED. Profiles 1/2/3, Node framing
and all original traffic/allocation/deadline limits remain unchanged. STARTED's
40-byte record and every other RPC edge require original traffic commitment.

## Output, cancellation and release

OUTPUT requires known reported CLI wait, both EOFs, stdin close and release,
with no unsettled turn. Its raw output counts must match progress and each be
at most 1 MiB. Strict stdout re-encoding preserves raw length/digest; stderr
replacement permits encoded length from raw length through three times raw
length. Each DELIVER authorizes correlation with exactly one already reserved
text frame of up to 512 bytes. No default text allowance exists.

Incremental strict decoding retains only a UTF-8 carry, not a complete output
string. It still makes bounded temporary decode allocations which need prior
reservation. The coordinator's text consumer must separately hold its actual
Unicode/graph aliases and reservation through transfer. It cannot refund the
service's alias ownership merely because metadata arrived. STATUS requires exact
delivered counts and encoded digests, and validates the reported terminal raw
wait status against its signed return code, including legitimate nonzero exits.
Raw digests are reported service facts; encoded digests also validate delivered
bytes. No hash establishes service origin or API success. The parent-observed
service wait is a separate owner fact, absent from STATUS.

CANCEL is observed once, before RELEASE. It makes attempt failure and the
cancellation acknowledgement obligation sticky and admits no new grants.
PREPARED, STARTED, a prior non6 REFUSE, or STATUS may already have been sent when CANCEL
crosses in the opposite direction. Either observation order retains those same
facts without restoring START or result eligibility:

- PREPARED may arrive after CANCEL exactly once only when CANCEL interrupted
  prepared_wait. Its original lease and request digest must match. It cannot
  follow another refusal or cancellation acknowledgement.
- STARTED may similarly cross exactly once only when CANCEL interrupted
  start_wait, with the same operation/lease; it never restores TURN eligibility.
- One prior non6 REFUSE may cross CANCEL; it is a diagnostic, not the required
  cancellation acknowledgement. Another non6 refusal refuses. A prior refusal
  cannot appear after an already observed STATUS. This narrowly supersedes the
  earlier rule that every non6 refusal after CANCEL was itself invalid.
- CANCEL may follow the already observed STATUS or prior non6 REFUSE while
  RELEASE remains unconsumed. It cannot follow REFUSE6 or another CANCEL.

The ordinary cancelled phase remains cancelling. A consumed unknown turn stays
failed_operation even if a prior refusal was observed before CANCEL.
An unknown progress receipt changes the phase to failed_operation to prevent
repeating its retained unsettled turn, but cannot clear the cancellation
obligation. Its known prefix and pending commitment remain diagnostic facts.
A crossed already-granted progress/text receipt or already-produced
OUTPUT/STATUS may be retained for accounting, but cannot become a usable result.
The service must acknowledge CANCEL with REFUSE code 6 even if it just sent
STATUS. Normal RELEASE waits for that acknowledgement, including from
failed_operation or after a retained prior refusal. Neither that diagnostic nor
a crossed PREPARED/STATUS sets cancel_acknowledged. The sender must retain an
already encoded/EAGAIN-held report unchanged through its known send before
queuing REFUSE6; an unsent loan cannot be overwritten or declared delivered.
Mandatory acknowledgement and release traffic needs original-budget headroom
before effects; peer death, deadline or uncertainty cannot fabricate it.

REFUSE prevents another operation. RELEASED unknown retains an unusable
release_unknown state. Known RELEASED only reports remote alias cleanup; it is
not an automatic refund, CLI wait, local alias retirement or admission.
Unsettled turns/deliveries remain diagnostic references for owner quarantine.
After a successful known release, a higher operation ID may begin; no history
queue grows. SHUTDOWN/BYE are only an idle shutdown handshake. Actual service
wait and release still belong to its NativeProcessOwner.

Protocol errors and unexpected observation exceptions permanently poison the
transcript. Its cleanup is inside the exception guard. Pure tests inject a
required line-trace exception at the explicit observation return after busy
cleanup; they do not prove all asynchronous interpreter return events are
catchable. The coordinator must keep its own unknown-outcome guard across
observe return, delivery and receipt settlement, retain original commitments,
and never catch an uncertain effect and replay it. Invalid protocol state may
prevent a normal wire release; actual owner cleanup remains independently
required and may itself be unknown.

## Validation scope

Pure checks use the actual Codec, Channel, Root, strict parser and fixed policy.
They cover order/replay, request digest binding, correlated grants, final
allowance carry, explicit zero EOF turns, unknown progress, output count/hash and
UTF-8 checks, cancellation crossings, cleanup uncertainty and unchanged framing
profiles. No subject, endpoint, service, CLI, API or SDK was executed for this
slice. Authenticated transport composition, actual resource tickets/allocations,
current-attempt read authority, engine/API completion, capture, capacity and full
trial admission remain unavailable.

