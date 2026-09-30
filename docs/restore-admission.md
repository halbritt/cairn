# Restore sessions and explicit admission

A restore session pauses normal Cairn transactions while the operator reconciles
an isolated database. It closes the gap where old receipts were fenced but a
fresh compile could still serve revived memory.

First restore PostgreSQL and coordinated artifacts into an isolated environment,
with external execution disabled. Apply current migrations, then run
`begin-restore` before connecting ordinary consumers. A raw `pg_restore` outside
this procedure is not automatically detected. Migrating an existing store does
not declare that a restore happened or pause its normal operation.

```sh
# All JSON commands below read one request from stdin.
# CAIRN_DATABASE_URL must select the isolated restored database.
cairn begin-restore < begin.json
cairn restore-status
cairn recovery-inspect /private/latest-known-recovery.json
cairn recovery-reapply --request-id UUID --expected-sha256 DIGEST \
  --reason 'Reapply known withdrawals to this isolated restore' \
  /private/latest-known-recovery.json
cairn rebuild-restore < rebuild.json
cairn purge-deletion DELETION_UUID
cairn verify-restore < verification.json
cairn resume-restore < resume.json
```

`begin.json` contains a fresh `request_id`, bounded `target` identifying the
intended restore point, and a `reason`. Beginning requires an unscoped operator
who owns the current live root grant. It drains ordinary database transactions,
creates a restore session, advances the delivery fence and pauses service in one
transaction. Retry the same begin request only while that session is still
paused. A completed or superseded begin request returns `STALE_RESTORE`; use a
new UUID for another restore. An unrelated begin request during a pause returns
`RESTORE_IN_PROGRESS`.

## What the pause enforces

Normal core transactions hold a shared admission lock through commit. Beginning
and resuming take that lock exclusively before request or authority locks.
Ordinary reads, writes, fresh compilation and cached mutation responses return
`RESTORE_PAUSED` while paused, including calls through an operator store.
Existing and newly opened API clients see HTTP 503 with that error. There are no
agent endpoints for beginning, verifying, rebuilding or resuming a restore.

Explicit unscoped operator recovery methods remain available: recovery
capture/inspection/reapplication, checkpoints, fencing, deletion status and
purging, evidence checks and historical recompilation. They retain their existing
access checks. Owner-only process status and authenticated late outcome/terminal
observations also remain available; terminal handling can retain its usual
derived failure note. These exceptions do not authorize new launches or serving
packages. An API may remain listening while paused; socket availability is not
admission.

The pause drains database transactions. It cannot recall already-returned bytes,
stop a process or cancel a tool effect. External isolation is required before
beginning. [Delivery fencing](restore-fencing.md) preserves historical seals and
requires a fresh compile after resume.

## Rebuild and verify

`rebuild.json` contains a fresh `request_id` and the returned `session_id`, and
optionally `limit` (1 to 10,000 forgotten sources per call, default 1,000) and
`after` (the previous call's `next_after`). Rebuild adds missing
deletion-dependency exclusions from retained exact relations and advances
affected use generations. It processes forgotten sources in record-ID order, one
committed page per call, and computes each source's exact version-qualified
closure inside PostgreSQL. There is no ceiling on the number of deletion
requests, on source/version references, or on a source's retained descendants
(the ordinary 1,000-version bound applies to impact previews, not to restore).
The response reports `sources`, `added_exclusions`, `affected_records`,
`complete`, and `next_after` while pages remain; counts describe that call only.
Repeating the same request returns the original counts; a new request on an
intact projection adds zero, so an interrupted pass restarts from the beginning
or from the last `next_after` without skipping a source. The session stays paused
between pages. A reapplication that adds a forgotten source with a record ID
below the cursor needs a new pass; verification's dependency check for every
forgotten source is the backstop if that pass is forgotten.

Attribution is a SQL view and ranking is recomputed. Historical impact snapshots,
receipt observations and authority rows are retained inputs, not projections to
regenerate from today's graph. Rebuild does not invent lost relations, events,
exposures or file custody. Use [reapplication](recovery-reapplication.md) and the
ordinary instrumented deletion worker for known newer restrictions and files.

A verification request contains:

- `session_id` naming the current session.
- `checkpoint` with `checkpoint_id`, `expected_sha256` and
  `expected_export_id` from the independently retained backup catalog.
- `recovery`, the complete latest-known trusted external recovery record.
- `fixtures`, up to 100 `{receipt_id, query}` objects from retained compiler
  fixtures. Original query bytes must be supplied; their hashes cannot recover
  them. A store with active records requires at least one fixture that selected
  content. An empty fixture list is accepted only when there are no active records
  and no unpurged receipt history. Fixture queries are transient; admission
  retains receipt IDs, seals and selection counts.

Verification checks migration membership/checksums, current version/class links,
current and historical authority correspondence, grant parent/depth/capability/
scope/expiry and revocation state, B evidence references, inline evidence bytes,
known deletion exclusions, external checkpoint metadata and recovery expectations.
Evidence bytes are scanned in bounded pages within one verification snapshot;
there is no fixed evidence-object count ceiling. For widespread unmarked divergence,
the report names the first 100 objects and counts the rest while still checking
every object.
Unmarked divergent evidence refuses; `check-evidence` records the actual observed
state and invalidates affected previews. The supplied fixtures must reproduce
their retained semantic seals through historical compilation.

The external recovery record is merged with already-retained expectations before
inspection. The merged expectation set has no count ceiling and is checked in
full; only the report is bounded. `problems` names at most the first 100 gaps
and adds `RECOVERY_GAPS_ADDITIONAL:N` for the rest, and
`reapplied_missing_events` names at most 100 events with `reapplied_additional`
counting the rest, so a large damaged restore cannot hide behind the bound or
produce unbounded output. If any supplied or retained record is a segment of an
exported set, every position of that set must be retained or supplied;
otherwise verification reports `RECOVERY_SEGMENTS_INCOMPLETE:SET:HAVE/COUNT`
and refuses. An older or incomplete input cannot erase known gaps. An original
missing withdrawal event can be covered for admission only through the exact
retained reapplication mapping and current restricted state; its original
`AUDIT_MISSING` finding remains in recovery inspection. Unknown missing audit,
altered audit, revived state, incomplete custody, unsafe projections and pending,
running or failed deletion effects refuse admission. The verifier does not waive
an unknown missing event that might represent a lost policy change.

`verify-restore` is read-only. It returns `ready`, `problems`, covered original
withdrawal-event IDs, fixture proofs and the residual count. Failure exits 7 with
`RESTORE_INCOMPLETE`. A successful report is a snapshot, not permission to serve.

## Stores beyond one recovery record

This is a partial CAIRN-49 implementation, not a bounded-memory restore solution.
Rebuild pages and recovery applications can commit progress between calls, and
exports can span records. Verification, checkpoint construction and expectation
capture still materialize the complete union in process memory. They cannot
resume midway through a verification snapshot. The tested operator path above
the previous 10,000-entry limit uses these steps:

1. **Export.** `recovery-export PATH` writes one file while the expectations fit
   one record (10,000 audit members, withdrawals and context references each,
   16 MiB). Beyond that it writes every segment of one snapshot, named
   `PATH.part-0001-of-0003.ext`. An ordinary write failure removes and syncs the
   files created by that call; process or machine failure can leave an incomplete
   set, which inspection refuses. Keep the previous complete export until the new
   command succeeds and every new segment has been retained. Segments carry a set identity and position, stay independently
   valid (a context segment repeats the forget withdrawal that anchors its
   records) and merge back to exactly the captured union. Retain every segment
   outside the database; a missing position is detectable, never silent. The
   library call `CaptureRecovery` still refuses a set that needs several records
   instead of exporting part of it; `CaptureRecoverySet` returns the segments.
2. **Inspect.** `recovery-inspect FILE...` accepts every segment file and refuses a
   list that names a set but omits a position before inspecting anything.
3. **Reapply.** Run `recovery-reapply` once per segment, each with its own request
   UUID and `--expected-sha256`. Each segment is one serializable transaction of at
   most 2,000 withdrawals and retains its source record, so progress is committed
   segment by segment. After an interruption, rerun the segments not yet applied;
   an already restricted subject is reported `already_restricted`, never applied
   twice, and an identical request UUID returns its original result. Retain every
   original segment this way before exporting another recovery set, including the
   segment passed to verification: a verification input alone is not a retained
   application. Recapture refuses a retained incomplete set or conflicting content
   at the same set position; it never strips that obligation into a new export.
4. **Rebuild.** Page `rebuild-restore` with `limit` and `after` until `complete`.
5. **Verify and resume.** Supply any one segment as `recovery`; the others are
   already retained by their applications, and verification refuses unless every
   position of each referenced set is retained or supplied. An abandoned partial
   set keeps refusing until its missing segments are applied from their files.
   Checkpoints have no member ceiling either, so a store above 10,000 audit members
   can be backed up and verified.

Remaining limit: process memory grows with all recovery expectations, and capture
also builds the full exported segment array. Encoding the segment files holds one
encoded file at a time, but does not make their decoded source array bounded.
Evidence bytes are scanned in pages; audit members, withdrawals, custody, retained
applications and checkpoint manifests are not. Larger stores still need a
streamed or staged expectation/checkpoint contract before CAIRN-49 can be closed.
Do not raise another aggregate ceiling, omit records or clear the pause as a
workaround. Per-withdrawal checks are individual queries and can also be slow.
Pause/fence checks and the exclusive resume lock remain in force; a failed or
interrupted verification does not authorize service to resume.

## Operations policy and resume

`resume.json` contains a fresh `request_id`, the complete `verification` request,
`policy: "local-restore/1"`, and a `reason`. The current root owner chooses this
policy by explicitly invoking resume. That action affirms that the external
expectation is the latest known trusted one, the intended restore point is
correct, and external execution/artifact recovery has been isolated and handled.
Cairn verifies the retained data; it cannot independently establish those
operator-owned facts from a database restored to an older point.

`local-restore/1` requires all executable checks and all recoverable deletion
effects to pass. It accepts the existing explicit `not_possible` categories:
storage/backups, unmanaged copies, retained metadata, historical unregistered run
artifacts, provider delivery, separately captured evidence, and related records.
Their count remains in the admission record. This policy does not claim physical
erasure. An unknown residual category refuses until its handling is defined by
a supported policy.

Resume reruns verification while holding the exclusive admission lock. An earlier
green report cannot hide later changes. A new authority event commits the policy
and verification metadata, and the admission row changes atomically. Failure
leaves the store paused. Retry identical intent after a lost response; a stale
session or changed request intent refuses. Inspect `restore-status` for current
state rather than treating a historical resume response as current status.

The external isolation and freshness requirements remain operational boundaries.
These checks cover the named retained structures and supplied fixtures; they do
not prove complete historical reconstruction, arbitrary unobserved corruption,
all possible compiler behavior, or general memory usefulness.
