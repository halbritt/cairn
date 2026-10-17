# SDK preparation allocation custody

`prepare_sdk_review_with_resources(document, output, resources, raw=..., working=...)`
reserves the caller's original `AttemptResources.local` standing slot before
snapshot creation, declaration freezing, source copying or launch compilation.
It creates no replacement ledger. Insufficient capacity or an occupied standing
slot refuses before an output directory is created. This selected profile owns
the entire local standing reservation; callers with an existing parent standing
allocation need a separately reviewed aggregate binding, not a second ledger.

The reservation must cover every simultaneous preparation/session allocation
charged to it. The function does not derive or validate quote sufficiency. In
particular, positive `working` or a successful reservation is not admission.
Upstream construction of `document` requires earlier custody of its own; this
entry point cannot retroactively fund it. The original limits are unchanged.

The actual allocation object moves with the original snapshot, launch and
controller into `SDKSessionCustody`. A single-use guard prevents starting a
second preparation with that same object. Review and held-source checks verify
that its original ledger row is still live. Preparation, transfer or subsequent
source-check failure attempts to freeze the original ledger and retains the reservation;
failed freeze records `resource_freeze_unknown` and leaves the binding locally
unavailable without claiming that the ledger froze. Cleanup still belongs to
the original descriptor/controller owners and runs despite freeze failure. No failure
path guesses that traceback-held aliases have died and refunds capacity.

Normal source close also retains the reservation: borrowed launch/controller
objects and other aliases can survive file closure. Only the original trusted
attempt owner may call the ledger's existing release operation after all
charged aliases end or a complete charge transfer is established. Same-process
Python identity is not an authentication boundary against that owner. Premature
owner release makes later custody checks fail; it does not create a replacement
reservation or silently repair the accounting.

Legacy offline preparation remains available without this accounting binding.
`require_complete` still refuses. The resource-bound session now checks the
standing allocation before each public operation and uses the original ledger
for receipt-backed I/O. Complete output custody and fair coordination remain
unresolved. Complete numerical quotes, physical memory fit, genuine
native MCP origin, funded provider capacity and weekly evaluation are separate
unresolved requirements. The new entry point grants none of them.

Snapshot copying and raw revalidation now request at most 8192 bytes per read,
including live source rechecks. Each block is released before the next read.
Accepted source lengths, total copy limits, original-byte hashing and identity
checks are unchanged. More read calls replace the former 65536-byte temporary.
Text decoding still has its 8192-byte read plus possible carry concatenation;
legacy collected-byte reads, bootstrap canonical encodes, controller queues and
working-memory graphs need separate coverage. This change reduces one actual
scratch allocation; it does not lower ledger quotes or prove composed fit.

Preparation canonical hashes now use the existing bounded canonical encoder and
SHA-256 counter. Closed preparation documents, launch argument lists and control
metadata keep the same canonical digests without an entire encoded JSON byte
copy. Encoder scratch and graph storage still need coverage; generated control
files, compiler encodes and queued reply bytes are separate paths. This change
does not alter the paired-request canonical serializer or its contract.
