# Parent command-service record custody

`ServiceLink` retains an actual `NativeProcessOwner`, a factory-created
`BrokerListener`/`RecordSocket`, Python profile4 Codec/Channel loans and the
original parent's `AttemptResources`. It provides bounded nonblocking record
steps and a one-shot prestart mechanism. It does not yet schedule a complete
operation or admit a native/API route. The command-service entrypoint, fixed
source/credential/namespace builder and end-to-end scheduler remain required.

The builder supplies the original absolute deadline and standing raw/working
quotes. The ledger debits them before local backing creation or service startup.
Parent transport has three disjoint backings:512 body +544 TX +544 RX, plus480
auxiliary raw scratch on this Linux ABI. `RAW_STORAGE` derives the latter from
the existing peer module. A complete standing quote must additionally cover the
child's transport/reply storage and other simultaneous standing allocations;
the parent's1600 backing bytes alone do not cover the child. No independent
128-KiB or512-MiB child pool is created. These logical quotes describe a known
subset, not whole-process physical fit or a replacement for aggregate controls.

`WireCredit` consumes one control ticket from that same ledger. The ordinary
ticket belongs to the held service domain; the optional emergency ticket uses
the original local control slot, which must therefore be available. At most two
live credits and two simultaneous record promises per credit bound bookkeeping.
Each promise covers32..544 bytes before authorizing its directed transfer. A
known receipt consumes that promise once and retains the actual byte count.
`finish` settles the shared ticket only after all promised transfers complete;
only unused commitment returns. A known send and its matching receive are one
edge; a relay or new encoding is another. The parent is the designated observer
of parent-to-service sends and service-to-parent receives. CLI counts require a
separate original data ticket and authenticated PROGRESS reconciliation.

Before prestart, reserve and arm the READY receive. Keep the actual owner before
calling its one-shot start; the selected fixed service uses only the record
channel, so the parent closes service stdin after launch. Listener acceptance
binds the exact actual child and its kernel credentials/lifetime. Both sides'
existing dumpable-zero/path-protection prerequisites still apply. Source, route,
credential and outer containment authority comes from the builder, not argv,
domain ordinals or a successful handshake. No public supplied-PID adoption or
default subprocess/model fallback is added.

`queue` retains one body and encoded token. `send_step` makes one actual
nonblocking send. EAGAIN retains the identical bytes, sequence and original
promise; it does not observe the transcript or refund the credit. Schedule
another step only after actual readiness, exposed as borrowed `interests` FDs.
`arm_receive` reserves before the peer is permitted to emit its bounded reply;
arming only after that emission would violate the builder's precommit contract.
`receive_step` consumes one authenticated record into held storage, charges its
actual count even if framing subsequently refuses, and offers only a temporary
frame borrow. Consumers must retain no views. The record hold ends before reuse.
Transcript observations occur only after actual known sends or authenticated
receives, never on an EAGAIN retry.

Some commands produce several automatic replies. `credit.burst(maxima)`
precommits one bounded receive cursor before its triggering send: one to four
record maxima, each32..544 bytes, with their sum at most544. It consumes one
of that credit's two promise slots, not another ledger ticket or backing.
For example, a TURN can produce PROGRESS56, OUTPUT224, empty-output STATUS40,
and a crossed cancellation acknowledgement33. Its353-byte cursor and53-byte
TURN therefore require406 original control bytes before triggering the turn.
The separate CLI data ticket is still required before that TURN.

`adopt_receive_burst(credit, cursor)` attaches an already committed cursor to
the existing RX backing. One authenticated record advances it; EAGAIN retains
the same cursor and loan. Each actual record is charged before validation.
`finish_receive_burst()` may end it early only after the coordinator validates
source state that excludes every remaining automatic reply. Silence, elapsed
time or a guessed reply count cannot justify that call. It ends the local
promise; unused original commitment returns only at known `credit.finish()`.
Cursor identity is exact and cannot be used with another credit or after ending.

A future cursor may be committed while another credit occupies RX, then adopted
after the first receive loan ends. This handles a maximum544-byte text reply
followed by STATUS40 and a possible crossed acknowledgement33: the text reply
and terminal cursor need two original credits committed before DELIVER. There
is still only one RX backing and at most two live credits. A partial or unknown
burst retains its known prefix and unresolved remainder under the original
shared freeze; it cannot be ended to obtain another allowance. These ports do
not themselves decide whether an automatic-reply branch has ended. That remains
the actual operation scheduler's responsibility.

Unknown transport, post-effect accounting, codec/consumer or final cleanup
interruptions freeze this link and retain commitments/loans. Known positive
traffic remains spent, and unobserved remainder remains reserved. Other held
credits cannot issue after shared uncertainty. The guard covers selected local
cleanup/return lines; it does not promise recovery from every possible Python
return-event interruption. The outer owner must retain its unknown-outcome
guard through handoff/settlement. There is no catch/replay/refund path.

`AttemptResources.freeze` also covers higher-layer handoff uncertainty after a
ticket was already settled. It is parent-bound, sticky and idempotent: it changes
no counters, tickets or spent observations. Subsequent ledger completion cannot
clear that freeze. This closes the interrupted outer `WireCredit.finish` return
case without fabricating a replacement grant.

RecordSocket retains a bounded scalar `last_transfer` tuple: fresh sequence,
direction, offered maximum and actual count (or `None` if no count was observed).
The count survives post-syscall peer/ancillary/cleanup failure; zero is recorded
only from an actual zero return or known EAGAIN. Parent failure handling matches
a fresh generation and exact direction to its held promise, then quarantines the
actual prefix while leaving the promise/loan and unobserved remainder unresolved.
An older receipt after a pre-effect refusal cannot count again. Consumed invalid
or unauthenticated bytes are traffic, never usable protocol. No count is inferred
from an exception, endpoint disappearance or a reported PID.

The link also retains one pre-effect I/O context through the count-to-credit
handoff. If that return interval is interrupted, poisoning reconciles only a
fresh matching RecordSocket count against a still healthy, unchanged credit.
An already consumed or quarantined count is not charged again. This covers the
demonstrated post-return/pre-receipt line interruption for ordinary receive,
burst receive and send. Any reconciliation failure is retained as a fixed label
and the original ledger freezes; it does not replay the syscall or invent zero.

`close_channels` attempts actual endpoint cleanup independently of healthy
protocol state. It proves neither service nor CLI exit and returns no resources.
`retire` requires no outstanding promises/loans, known channel closure and the
actual service owner's known release before dropping local backings and the
standing ticket. The service's reported CLI wait remains distinct. A service
crash while its CLI lives requires outer descendant containment; waiting for the
service alone cannot establish command wait/EOF. Shutdown must retain the
service through final BYE consumption because RecordSocket refuses I/O after
actual peer death.

The parent still must reserve child preparation/graph/plan and Job allocations
before decoding, bind current-attempt read eligibility, use the sole pending
memory gate before START, allocate exact mandatory RPC/cancel/release headroom
before CLI turns, and preserve output aliases through actual delivery/release.
This module does not implement those obligations or certify full107 usefulness.
The original limits and SDK acquisition/replay restrictions remain unchanged.

Tests exercise real ledgers/Codec/Channel/RecordSocket paths with inert socket
effect boundaries, shared credit arithmetic, EAGAIN custody, consumed invalid
frames, uncertainty and unlaunched cleanup. They establish no kernel peer,
actual service/CLI wait, provider effect, native delivery or runtime admission.
