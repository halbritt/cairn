# Producer resource binding candidate

`ProducerResources(resources, owner, domain, allocation, stream)` binds an
existing producer owner, operation allocation and shared DATA `StreamCredit`
from the original `AttemptResources`. It creates none of them. The builder must
reserve the complete simultaneous RPC, producer driver and engine lifetime
before constructing these components. Its raw minimum counts only named
canonical buffers, output slabs and carry; it does not certify a complete quote
or the physical memory bound. A positive working quote is not measured capacity.

`SDKProducerDriver.from_resources(event, binding)` consumes this binding before
creating the canonical iterator, decoder or slabs. It uses that exact owner.
The original constructor remains an unqualified mechanism fixture; a production
builder must select the explicit bound path. Neither path establishes native
origin, configuration authority, funded capacity or admission.

Each bound read or write pledges one stdio action on the same DATA stream used
by RPC. It caps the actual offered view by available original credit, excluding
protected future RPC replies. The actual owner's fresh receipt is checked and
charged before returning to driver hashing, decoding or clock checks. Known
EAGAIN spends zero; unknown cleanup retains any matching positive prefix and
freezes original custody. Failed operations are not retried or refunded.

Occupied promises or protected replies suppress payload I/O interest, while
actual process-wait readiness remains polled. At exhausted original credit with
no outstanding promise, future reply or unused stream allowance, output
readiness may invoke the existing owner's EOF-only probe. A live-empty stream
stays pending; queued bytes refuse without consumption; a drained hangup permits
an actual zero read. No new payload credit is issued. Unexpected positive reads
are recorded as unfunded probe bytes alongside the original receipt and freeze
the attempt; they are never funded by enlarging a grant or reported as success.
Post-ACK producer output continues on the same held DATA stream.
The binding does not finish that stream or release callback storage; those
lifetimes still require the actual producer wait/release and end or transfer of
all retained aliases. Driver cancellation alone is insufficient.

This candidate is incomplete for production: fair outer scheduling, SDK session
resource injection, complete quote validation and the genuine builder remain
pending. Its EOF probe relies on the owner's exclusive reader and unchanged
AF_UNIX descriptor contract; synthetic checks do not attest those live facts.

Pure tests use real owners, receipts, ledgers and credit objects with inert
syscall/process boundaries. They cover mismatched identities, capped syscall
widths, protected reply credit, positive-prefix cleanup failure, post-read clock
failure, EAGAIN, shared RPC/stdio ownership after ACK and wait-only readiness
while parked. EOF tests include exact-cap completion through the public driver,
live-empty/queued/hung-up states, close failure and an unexpected positive probe.
They do not launch native/model/provider workloads or prove a
complete resource quote, physical fit or full evaluation acceptance.
