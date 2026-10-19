# Private session stream custody

This candidate supplies the missing preterminal byte-retention path. It is not
an evaluator, blind packet builder, work-product export adapter or native
admission. `FixedRunOwner.prepare` now requires `stream_output`, a fresh child
of a private, current-user-owned directory. The run publishes the retention
owner before construction and binds it to the original `SDKSessionResources`,
allocation, stream credit and direct-child owner before any read. Existing
fixed builders must supply this path; no legacy or optional sink fallback is
installed in the fixed run.

`SessionStreamRetention` creates exclusive private stdout.bin and stderr.bin
files through held directory descriptors. Its construction syncs the directory
and its private parent. Inputs/path ancestors remain trusted and quiescent:
mode/identity checks are not protection from a hostile same-UID writer. No
restart reader or replay authority is inferred from these files.

For every positive returned read, the stream driver writes the original slab
view before its next clock or parser action. If the original resource wrapper
raises after a known positive native read, the outer read guard recovers that
prefix from the original owner's generation/count receipt and the same slab.
A generation already attempted is not written again. Unknown/unreturned
syscall counts are not invented; those remain existing native I/O uncertainty.
File writes use bounded borrowed views, consume positive partial writes, and
fsync before the normal read path continues. A failed write/sync keeps the
original slab loan, the consumed file target and the original bank charge.
The original exception is preserved if secondary prefix retention also fails.
No future read is allowed after the enclosing driver/session/run fails.

Ordinary nonterminal documents no longer cause `run_unsupported_document` when
the fixed held retention path exists. The parsed graph is consumed in a
returning run frame only after its complete raw bytes were retained. There is
no action extraction, suppression of parser refusals or successful-task inference.
Malformed bytes are retained before parsing refuses. Missing stream is never
reported as zero deliveries. Raw material remains private and ungraded.

After the original native owner reports both EOFs, actual direct-child wait and
handle release, sealing checks the exact pump/binding, byte counts and hashes.
It fsyncs both leaves, reads them back through the ended stderr slab, checks
full digests, stable metadata and named-file inode correspondence, then syncs
directory and parent and closes all four descriptors once. No extra full raw
buffer or whole-file readback is created. Reentrant close during writing/sealing
is refused. Close faults are sticky; independent descriptors are still attempted.
An interruption outside a descriptor attempt leaves unattempted descriptors
reachable for cleanup. The run's cleanup owns this file owner alongside source,
channel and native owners, including a partially initialized shell.

Only after sealing and the original deadline recheck does the run publish the
same terminal graph in `run._evidence._terminal_graph`, then clear its obsolete
alias. A publication interruption leaves at least one owned reference and
freezes the original bank. This is a local ownership move, not task acceptance
or graph release. Ungraded output is present both in the privately retained raw
stream and in the retained original graph. No allocation or traffic remainder
is returned, even after successful file closure. A closed in-memory snapshot is
a local receipt, not a durable batch manifest, external trust proof or export
permission. Failure/unknown targets are never reopened or retried.

## Accounting and admission

Native traffic still passes through the same original pre-effect stream credit.
The extra file copy receives no new native traffic grant. The existing stdout
3 MiB and aggregate stdout/stderr 4 MiB checks remain; these are file payload
ceilings, not a raw-memory allowance. The read slab is borrowed, not charged as
a second distinct backing. The ended stderr slab is reused only after both EOFs
and no outstanding parser batch for readback. Hash objects, metadata, retained
error frames, views, filesystem buffers and page cache remain physical-bound
obligations under the original attempt; they are not free or proven to fit.
Regular-file write/fsync/readback latency is not bounded by the syscall payload
size. The original deadline is rechecked after sealing, but that does not supply
an interruptible bounded file-I/O implementation.

The updated phase table includes private_stream in every post-preparation phase,
including first SessionStart, suspended producer, all cumulative command/reply
states, terminal and interruptions. It has 50 phases, 19 backings and 19 source
pins. Every physical upper remains unknown and every transition remains false.
Callback raw 136353 and command raw 153325 still exceed 131072. Full-standing
admission is still refused; no quote, cap, ticket, behavior profile or callback
coverage was reduced. Partial constructor/output failures remain charged.

## Downstream contracts and the precise next seam

The current source contracts are reconciled, not replaced:

* `trial_task_evidence.completed_calls/analyze_stream/analyze_report` requires
  the original correlated stream: call IDs, failed/incomplete outcomes and
  partial-span payloads cannot be recovered from terminal text. Its projection
  also omits some original IDs/offsets, so counts alone are insufficient. Frozen
  corpus and optional observed overlay have separate authenticated byte digests.
  The offline whole-stream reader is not a bounded live parser. This candidate
  preserves bytes for that later consumer without invoking it on a live run.
* `trial_work_product.attempt_closure/export_attempt_evidence` requires original
  request/occurrence/workspace binding, raw wait/returncode, independently
  established containment release and closed failure-evidence persistence. The
  SDK owner supplies direct-child wait/handles only; its `released` field is NOT
  whole-attempt containment release. This private stream receipt is not the
  existing CodingSink's independently waited/inspected CLOSED failure result.
  A digest or `custody_bound` boolean cannot fill either gap.
* Complete bounded authored units in `trial_blind_packet_draft` (_source,
  _render, _prepare, canonical_bytes) and `trial_grading_custody` (_inventory,
  _packets, _submissions, join_results) define the supported downstream mapping.
  It includes request/campaign/occurrence/schedule/case/seed/wording/arm/run.
  `packet_sha256` for the join is the exact canonical COMPARISON delivered to
  both graders, not a terminal/sample hash. Blind canonical bytes include LF;
  request canonical_sha does not. This candidate emits none of these grades.

The next authored seam is an original prepared-occurrence/schedule owner that
binds this held run and its returned private file observations to those complete
identities, both corpus pins and explicit missing/error evidence; then a bounded
retention/export worker/controller with actual return and publication custody.
Current FixedRunOwner.prepare has no prepared paired request, schedule or corpus
owner input. Adding those facts as caller JSON would not prove their provenance.
This is actionable source integration work, distinct from the unavailable native
containment contract. The legacy 113152-byte trial_task_eval.py was not read
through the generic bounded reader; no parity with its uninspected writer/grade
units is asserted. No original cases, private oracle or grader inputs were read.

## Evidence scope

Tests use original accounting, actual private temporary files and file readback,
with inert native process/read/wait edges. The full-standing callback refusal
remains exercised. Successful private sealing is a scoped persistence test, not
a successful full native composition. Tests cover clock/parser/resource-settle
failure after positive reads, partial file writes, constructor/FD publication
interruptions, close-once uncertainty, leaf/directory/parent sync refusal,
same-size content and path drift, reentrancy and terminal move interruptions.
Boundary tests additionally show equal terminal results with different delivery
histories, distinct partial spans with identical analyzer projections, separate
overlay/frozen identity and missing-versus-empty streams.

The full CAIRN-107 goal remains open: complete physical bounds and bounded I/O,
supported native/containment custody, prepared paired-run provenance, bounded
export/failure persistence, authenticated blind grading and the complete weekly
campaign/report requirements. DO_NOT_ADOPT remains the disposition.
