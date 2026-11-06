# Roll out event admission

Event admission bounds how much new event work a collection accepts: topic fanout,
outstanding deliveries per recipient and collection, closure capacity for accepted
obligations, and producer, owner and collection rates. This runbook covers upgrading
a store, observing load, choosing limits, an authorized canary, disabling and
retirement. Every collection's policy starts disabled. Nothing here enables
enforcement for you; that takes an explicit, authorized operator edit.

The contracts live in [event pressure and admission](../event-admission.md) and
[closure reservations](../closure-reservations.md). Keep them open while you work.

## What changes and what does not

Migrations 080 through 082 add the policy table, closure reservations and refusal
diagnostics. Migration 083 adds persistent rate buckets, and 084 marks the complete
writer contract that enforcement requires. An absent policy is `disabled` at
revision 0. With admission disabled or in observe mode, publication,
handling, retry, renewal, cancellation and recovery keep their existing behavior.
Closure reservations are recorded in every mode. Only `enforce` uses them to refuse
new obligations.

Ack, complete, retry, renew, cancel, wait cancellation, closure reconciliation and
accepted-work recovery (`native-hold-release`, `stopped-inbox-expire`) are never gated
by admission; explicit `event-reissue` publishes a new request under normal admission.
A refusal never manufactures success, drops a
recipient or splits a topic publication.

## 1. Upgrade the store

Use one source revision for the CLI, API, scheduler, wake supervisors and native
lifecycle hooks. Install migrations 080 through 084 together. Migration history records
each file's version by its position in `core/schema/` together with its digest. You can
later append newer migrations unchanged, but never omit, reorder, edit or cherry-pick a
file: a different file at a recorded position refuses with `SCHEMA_MISMATCH` (`migration
checksum differs`), and a missing file cannot be inserted later.

1. Back up the store and keep the recovery witness, as described in
   [maintenance](maintenance.md#upgrade-the-schema).
2. Stop consumers, the scheduler, wake supervisors and dispatch. Let the old API finish
   its in-flight requests, then stop it too. No old process may hold a connection
   while you migrate.
3. Run `cairn migrate` with the new executable. Migrations 080 and 084 each begin by
   updating the `cairn.restore_admission` row in place (`SET paused = paused WHERE
   singleton`). Its conflicting row-level tuple lock waits for already-admitted
   `FOR SHARE` transactions to finish, and its new tuple version causes waiting
   repeatable-read or serializable snapshots that started through `restore_admission`
   before the migration committed to abort with SQLSTATE `40001` — surfacing
   `SCHEMA_MISMATCH` (exit code 4) on paths that retry serialization failures (such as
   `Bootstrap`) and `STORE_ERROR` (exit code 7) on non-retrying repeatable-read paths
   (such as `coordination-review`), rather than committing against a pre-migration
   snapshot.

   This barrier covers transactions that pass through that admission check. It is not
   a connection fence. An old process that already holds an open connection pool can
   still run the few operations that use the pool directly, such as quiet-notice
   expiry and work-feature or advice-control reads. That is why step 2 is mandatory:
   stop every old process before migrating, and restart only upgraded binaries.
4. Restart the API, then the scheduler, supervisors and consumers.

An older binary started after the upgrade cannot use the store. Its schema ceiling is
the number of migrations it embeds; a build without 080 has a ceiling of 79. When it
opens the store, any command or API process, including `cairn migrate` and operator
commands such as `coordination-review`, refuses with exit code 4 and `SCHEMA_MISMATCH`
(`database schema version 84 exceeds supported version 79`). That check runs when a
process opens the store, not on every later call, so it does not stop a process that
was already running. Stop and upgrade every process.

Check what is actually running before you continue:

```sh
cairn version
cairn agent --token-file "$CAIRN_TOKEN_FILE" version
```

A revision stamp identifies a build; it does not prove that a capability is enabled.

## 2. Reconcile closure reservations

Obligations accepted before migration 081 have no reservation. Reconciliation adopts
or releases them and records completion for the current restore generation. Run
batches until one reports `complete: true`:

```sh
printf '%s\n' '{"request_id":"RECONCILE_UUID","repo":"COLLECTION","limit":500}' | \
  cairn event-closure-reconcile
printf '%s\n' '{"repo":"COLLECTION","limit":50}' | cairn event-closures-collection
```

Use a new request UUID for each batch, and keep the same UUID to retry a batch whose
response was lost. Until a reconciliation completes for the current generation,
`counts.available` is `false` with reason `unreconciled`, and counts are absent
rather than zero.

## 3. Observe

Select a finite policy for the workload you actually run, in observe mode. Every
numeric field is required. The [observation fixture](../../fixtures/event-admission-observe.json)
is a small disposable example, not a recommended default:

```sh
cairn event-admission-configure < observe-policy.json
printf '%s\n' '{"repo":"COLLECTION"}' | cairn event-admission-policy
```

The request carries `request_id`, `repo`, `expected_revision` (0 for a new policy) and
the `policy` object. A changed edit needs the current revision; a conflicting edit
refuses with `VERSION_CONFLICT`. Then watch the collection under normal work:

```sh
printf '%s\n' '{"repo":"COLLECTION","limit":50}' | cairn event-pressure-collection
printf '%s\n' '{"repo":"COLLECTION","after":0,"limit":50}' | cairn event-fanout-collection
printf '%s\n' '{"repo":"COLLECTION","destination":{"type":"topic","name":"TOPIC"}}' | \
  cairn event-admission-preview-collection
printf '%s\n' '{"repo":"COLLECTION"}' | cairn event-admission-refusals
```

Pressure and fanout show outstanding work, queue ages, per-event recipient counts and
the publications and delivery fanout of the last 60 seconds. Preview reports which
count-based dimensions would refuse a publication right now.

Observe mode keeps no shadow rate accounting. It never creates or debits token buckets
and never refuses on admission limits, so no rate-bucket balances are created in observe
mode for a later enforce run to inherit. Accepted work and closure reservations are still
recorded in every mode, so outstanding deliveries, queued quiet notices, unassigned pool
requests, and open closures created during observe mode count normally once enforcement is
enabled. The 60-second counts are observed rates, not bucket balances; do not infer a rate
refusal from them. `rate_evaluation` reports what real rate state exists, with distinct
rules for pressure and preview:

| Value | In `event-pressure` / `event-pressure-collection` | In `event-admission-preview` / `event-admission-preview-collection` |
| --- | --- | --- |
| `retained_buckets` | At least one current-generation bucket row exists for the caller's `producer` identity (inbox scope) or the `collection` identity (`""`, collection scope), regardless of policy mode. | Policy is `observe` or `enforce` and **every** non-zero-cost charge (`publication`, plus `delivery` when recipient count > 0) across `producer`, `owner`, and `collection` has a current-generation bucket row; those balances are refilled in memory without debiting and checked for `would_refuse`. |
| `uninitialized` | Rate tables exist, and no current-generation bucket row exists for that caller `producer` or `collection` identity. | Policy is `observe` or `enforce` and at least one non-zero-cost `producer`, `owner`, or `collection` unit bucket is missing or from an older restore generation. Preview adds `retained_rate_state` to `unevaluated`, while still checking `Cost > Burst` and any current-generation buckets that do exist. |
| `disabled` | Not emitted by pressure views (pressure still reports `retained_buckets` or `uninitialized` from stored rows). | Policy is `disabled` (or has no limits); preview adds `disabled_policy` to `unevaluated` and runs no forecast. |
| `unavailable` | Rate tables are absent from this store. | Rate tables are absent from this store (`rate_buckets` is added to `unsupported`). |

A collection that has never run in `enforce` mode has no rate bucket rows and reports
`uninitialized` on both pressure and active-policy preview.

Size limits from three sources: the workload you declare in the policy, the counts you
observe here, and disposable test runs with enforcement on. Record why each value was
chosen. A value that passes a fixture is not evidence that it suits production.

## 4. Authorized canary

`event-admission-policy` reports `enforcement_ready`. It is `true` only when the
running binary implements the complete writer contract and the store has migration
084. Otherwise setting `mode: enforce` refuses with `FEATURE_UNAVAILABLE`. Setting
enforce also refuses with `CLOSURE_UNRECONCILED` until a reconciliation is complete
for the current restore generation. Enable enforcement only with the operator's
explicit authorization for a named collection:

1. Confirm that reconciliation is complete for the current generation.
2. Raise that collection's policy from observe to enforce at its current revision.
3. Watch `event-admission-refusals`, pressure and closure counts. A refused publisher
   receives `EVENT_ADMISSION_REFUSED` (or `CLOSURE_RESERVE_FULL` for closure capacity)
   with `admission.dimension`, `admission.policy_revision`, `admission.retryable`, and
   `admission.retry_after_seconds` (always present in JSON, including `0` on
   non-retryable refusals; positive values provide backoff guidance and emit the HTTP
   `Retry-After` header on the central API, not a promise of admission).
4. Widen to other collections only with separate authorization.

Enforcement uses persistent token buckets (migration 083) across `producer`, `owner`,
and `collection` identities for both `publication` and `delivery` units. New wait
registrations (`wait create`) also require current-generation closure reconciliation
(`CLOSURE_UNRECONCILED`), reserve a `wait_notice` closure (`CLOSURE_RESERVE_FULL`), and
charge one publication and one delivery rate unit under `enforce`. The first enforced
run starts from real balances, not from anything observe mode measured. A later policy
revision changes limits; it does not reset balances.

Refusal diagnostics in `cairn.agent_event_admission_refusal` are bounded telemetry
retained outside the rolled-back refusal transaction. If recording a refusal diagnostic
fails during background sweeps (`schedule-serve` / `schedule-tick`) or operator
stopped-delivery publisher notifications (`coordination-review` with
`attention.notify_publishers`), accepted schedule/wait work still advances and
`NotifiedFingerprint` state for notices already `sent` or `duplicate_suppressed` in the
batch is preserved (while the deferred stopped-delivery notice itself leaves the original
delivery untouched and records no notified fingerprint, so a later pass retries it). The
response surfaces `unrecorded_refusal_diagnostics` (or `refusal_diagnostic_unrecorded`)
and `diagnostic_warning` (`schedule-serve` logs
`schedule refusal_diagnostics_unrecorded=N warning="..."` to stderr).

A refusal rolls back without partial side effects. When `admission.retryable` is `true`,
the publisher retries the same request UUID with unchanged intent after capacity drains
or backoff elapses (`topic_recipients` requires topic membership or policy to change
first); a committed publication returns its original result without a second charge. When
`admission.retryable` is `false` — because rate demand exceeds the configured `burst` or
requested closures exceed `max_closure_reserved` / `max_recipient_closure_reserved` —
retrying unchanged intent under the same policy cannot succeed; change the workload or
policy instead.

## 5. Disable

Set `mode: disabled` at the current revision to stop refusing new work. Accepted
deliveries, replies, wait notices, closure reconciliation and the maintenance
commands keep working, and reservations keep being recorded. Bucket rows are kept;
when a later policy edit re-enables enforcement at a new revision, refill anchors at
`max(refill_at, policy.updated_at)`, so disabling and re-enabling neither grants a
fresh burst nor credits token refill across the disabled interval (and never repairs a
`restore_generation` mismatch). Disabling
is the rollback. Do not remove migrations, delete reservations or purge history to roll back.

## Restore and generations

A physical `pg_restore` alone changes nothing about generations or admission. Follow the
supported [restore procedure](maintenance.md#restore-into-isolation) and
[restore admission](../restore-admission.md) in full: apply current migrations, run
`begin-restore` (which fences and advances the restore generation) before any ordinary
consumer connects, reapply newer recovery records, rebuild and `verify-restore`, then
`resume-restore`. Reconciliation (below) is a further mandatory step before enforced
admission accepts new non-closure work.

`begin-restore` fences open response groups, pending schedules, waiting waits, pending
wait-outbox entries, and queued quiet notices with `restore_fenced`, and releases the
closures of fenced groups and waits with reason `restore_fenced`. Direct reply
obligations survive with their original generation, and their replies still close
them. The new generation has no reconciliation, so closure counts are unavailable.
Under enforce, new non-closure publications and new waits refuse with
`CLOSURE_UNRECONCILED` until an operator completes a reconciliation for that
generation. Run step 2 after every resumed restore.

Rate buckets must not refill on restore. The restore fence itself empties every existing bucket and sets its refill
time to the fence's database time in the new generation, in the same transaction. A
restore therefore never grants a fresh burst; buckets refill only as database time
passes after the fence. A refused publication leaves that state unchanged, and a
committed retry never debits twice. A bucket whose generation does not match the
current one is refused with `RATE_UNRECONCILED` rather than silently reset.

This applies to buckets present in the backup. A bucket first used after the backup
does not exist in the restored database. Once the operator has reconciled the new
generation, its first use receives the normal initial burst, as any new bucket does.
That is bounded initialization, not a record of what the lost post-backup work spent.
Owner and collection buckets restored from the backup still bound producers that
appear under new sessions. A restore therefore does not preserve exact rate-budget
continuity across the backup point. It is an explicit operator action, never an
automatic loop, so it cannot be used to refill budgets repeatedly.

## Retire

Retirement means leaving admission disabled with nothing outstanding. Before
declaring it retired:

- set every collection to `disabled`;
- let accepted work settle or end through its own contract, then check
  `event-pressure-collection` until every collection reservoir is zero:
  `pending_deliveries`, `leased_deliveries` (which include native holds and expired
  leases), `stopped_deliveries` (unresolved deliveries on stopped sessions, also counted
  in `pending_deliveries`/`leased_deliveries`), `queued_organic_notices`,
  `unassigned_pool_requests`, `pending_schedules`, `waiting_terminal_notices` (waiting
  waits), `pending_wait_outbox`, and (when `closure_state` is `available`)
  `normal_outstanding`, `closure_reserved`, and `closure_delivered_outstanding`. These
  are collection totals, not paged recipient rows. Expire, cancel or flush through the
  supported commands; never delete rows;
- check `event-closures-collection` until counts are available, `reserved` is zero
  and `outstanding` is zero, so no reserved closure or undelivered consumed reply or
  notice remains;
- keep the schema, the compatible readers and the maintenance commands.

## Limits of this runbook

This rollout does not change the token-efficiency rollout decision in
[CAIRN-227](../plans/token-efficiency-2026-10-23.md), which stays in force. Durable
acceptance is not a wake promise. For sessions that wait a long time on a retained
wake, see the existing investigation
[CAIRN-275](https://proximal.tail0ecc2e.ts.net:10000/proximal/projects/afa5d2cc-95d4-4377-8cea-769f6b479c84/issues/820e6579-781b-4be0-a90b-df8d46bfd4d7/)
instead of renudging sessions. Bounded fanout limits delivery work per event; it does
not bound total workflow cost or prevent loops. No latency, cost or efficiency
improvement has been measured.

## Verification

`scripts/check_event_scaling_restore.py` checks the restore behavior above. It uses an
actual `pg_dump` and older `pg_restore` in a disposable cluster, a scripted local model
endpoint and a native Codex package:

```sh
python3 -B scripts/check_event_scaling_restore.py /path/to/cairn \
  --native-manifest /path/to/native-package/manifest.json \
  --report-directory /path/to/new-private-report-directory
```

The default `--admission-mode enforce` is the evidence run; it fails if enforcement is
unavailable in the candidate. `--admission-mode observe` checks only the mode-independent restore
behavior and exits 3, so it cannot be mistaken for a pass.
