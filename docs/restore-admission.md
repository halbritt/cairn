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

`rebuild.json` contains a fresh `request_id` and the returned `session_id`.
Rebuild adds missing deletion-dependency exclusions from retained exact relations
and advances affected use generations. It refuses more than 10,000 deletion
requests or combined source/version references; each source retains its existing
1,000-version bound. No partial rebuild commits. Repeating the same request
returns the original counts; a new request on an intact projection adds zero.

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
inspection. An older or incomplete input cannot erase known gaps. An original
missing withdrawal event can be covered for admission only through the exact
retained reapplication mapping and current restricted state; its original
`AUDIT_MISSING` finding remains in recovery inspection. Unknown missing audit,
altered audit, revived state, incomplete custody, unsafe projections and pending,
running or failed deletion effects refuse admission. The verifier does not waive
an unknown missing event that might represent a lost policy change.

`verify-restore` is read-only. It returns `ready`, `problems`, covered original
withdrawal-event IDs, fixture proofs and the residual count. Failure exits 7 with
`RESTORE_INCOMPLETE`. A successful report is a snapshot, not permission to serve.

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
