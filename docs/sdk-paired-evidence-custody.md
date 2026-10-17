# Prepared weekly selection and private evidence custody

This candidate connects the existing original weekly preparation to the fixed
run/file owners. It does not admit CAIRN-107 runtime or replace its evaluator.
Main and the prior frozen candidate remain unchanged.

`WeeklyPreparationOwner` is retained before its `prepare` call. It retains the
original SDK preparation allocation and cleanup owner. Preparation calls the
existing weekly `_checked` validator and the factored, unchanged publication
body `_publish`; occurrence/request registry consumption and partial-publication
semantics remain unchanged. The generic paired request `/2` API, including its
supplemental instrument, is unchanged. A generic request cannot select this
original-weekly-only owner. The weekly loader import is deferred until actual
validation, so merely importing this custody module does not import the large
legacy evaluator.

The owner copies the actual published request, schedule, source and preparation
files into an independently held snapshot. It retains scalar scheduled rows,
full request/campaign/occurrence identifiers, whole and inner schedule digests,
case/seed/wording/arm, and a deterministic run UUID derived from occurrence,
whole schedule digest and row index. Run UUIDs are also the SDK session IDs.
An EXCL-published selection file binds those fields and selected prompt hashes.
Before binding, both the held files and the current in-memory selection are
checked against their original hashes.

The original frozen corpus digest and the resolved corpus digest are retained
separately. A private `selected-overlay.json` serializes the prepared original
notes plus generated distractor notes, using the existing canonical transport
(no final LF). Its digest identifies those exact bytes. This is NOT evidence
that the service loaded that overlay, nor the historical evaluator's possibly
different serialization. `observed_corpus_sha256` therefore stays null. The
missing interface is an actual service corpus-load/observation owner bound to
this selected artifact, not a caller assertion that its hash matches.

`bind_run` compares the actual SDK preparation's request, occurrence, case, arm,
session ID and copied prompt with the held selection. It also compares the
original copied MCP arguments with the actual fixed service configuration's
socket/token-file/repo/task/run scope. It retains preparation cleanup custody
before the row claim, retains the run before the move, and checks the identical
allocation after the existing session handoff. Successful transfer clears the
obsolete preparation reference. Any exception freezes original charges and
consumes the selection state; cleanup remains reachable through the selected
owner and run. Literal prepared-wording equality does not prove the old weekly
runner's complete prompt/setup parity; that compatibility remains untested.

`FixedRunOwner.publish_evidence` prepublishes a one-shot controller. It requires
the actual linked preparation and retention owners, original held tickets,
completed run source cleanup and actual child wait/detach receipts. It rechecks
the still-held selection snapshot, scalar join and moved session identity before
publication; close the occurrence owner only after its runs finish publication. Unlaunched
children remain explicitly unlaunched. For sealed streams it reopens through
held directory edges, verifies original inode plus size/hash, copies complete
bytes privately, readbacks and closes all output/source descriptors. Empty
streams have a separate exact-zero path with regular-file/UID/mode/link-count,
original inode, EOF and empty-hash checks. Missing files remain missing. The
general nonempty source-file policy has not changed.

An unknown stream exports an explicit unknown-prefix record with its original
private directory, counters and failure state. It does not become zero calls,
closed failure evidence or an authenticated delivery report. Partial files,
pending borrowed slabs, original result graphs and error frames remain owned
and charged. Output paths and controller attempts are consumed on uncertainty;
no retry, overwrite, release or refund is provided. Successful local publication
means only that these private bytes and metadata were sealed and closed.

The private receipt is explicitly JSON-compatible: cleanup tuples are projected
to lists. Request/schedule canonical hashes have no LF; original preparation
selected JSON has its own encoding; a future blind comparison hash must still
cover the exact canonical comparison bytes including its final LF. No terminal
or evidence digest is substituted for that domain.

The controller leaves containment and work-product export unavailable and grade
ungraded. `trial_work_product.export_attempt_evidence` requires a trusted actual
containment-release and closed-failure-persistence verifier. No such owner exists
in the fixed run, so this candidate does not invoke or counterfeit that seam.
Raw streams preserve full call IDs/status/span provenance for later inspection;
the terminal graph alone cannot reconstruct them. There is no evaluator/grader
invocation, native launch, workspace inspection or oracle read here.

## Bounds and validation scope

The original tickets/caps/quotes are unchanged. The original first-hook raw
reservation remains 136353 and command phase 153325 against 131072; complete
standing admission continues to refuse. The table includes 56 phases and 21
backings, all physical bounds unproved and all transitions disabled.

Selection uses the existing offline validator/publication path (including its
8 MiB file ceiling), retained bundle graphs, overlay list aliases and source
snapshot reads. That ceiling is not a live reservation proof. Export retains
only bounded metadata (at most 65536 encoded bytes), streams within the existing
3 MiB stdout/4 MiB combined bounds, and uses the existing snapshot copier's
8192-byte raw block and canonical piece/chunk traversal. Readback, generator,
allocator, metadata/error-frame and kernel/file-page overlaps remain explicit;
8192 is not a whole-process bound. No quote is reduced to accommodate this work.

Tests use synthetic checked-selection data at the `_checked` boundary and real
preparation publication, copied SDK sources, original resource bank, owned
handoff, file persistence/readback and cleanup. Native edges are inert and real
process/network/signal effects are denied. The legacy evaluator and actual
weekly cases/oracles are not imported/read. The original weekly validation and
prompt/setup/corpus service compatibility still need independent source proof;
the synthetic boundary is not such proof.
