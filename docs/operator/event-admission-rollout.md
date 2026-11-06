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
recovery are never gated by admission. A refusal never manufactures success, drops a
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
2. Stop consumers, the scheduler, wake supervisors and dispatch. Leave the old API
   running only until in-flight requests finish.
3. Run `cairn migrate` with the new executable. Migration 080 first locks the
   `cairn.restore_admission` row, and 084 takes it again. That lock waits for
   transactions that already passed the old binary's compatibility check, so the new
   schema commits only after they finish.
4. Restart the API, then the scheduler, supervisors and consumers.

An older binary cannot keep running against the upgraded store. Its schema ceiling is
the number of migrations it embeds; a build without 080 has a ceiling of 79. Every
transaction it starts refuses with `SCHEMA_MISMATCH` (`database schema version N
exceeds supported version 79`), and `cairn migrate` with it refuses with `database is
newer than this binary`. Upgrade every process; a mixed fleet fails closed.

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

Observe mode keeps no shadow rate accounting. It never debits token buckets, never
refuses and creates no state that a later enforce run would inherit. The 60-second
counts are observed rates, not bucket balances, so the rate forecast stays
`unavailable` in observe. Do not infer an exact rate refusal from these averages.

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
   with a dimension, the policy revision and a retry hint. The hint is backoff
   guidance, not a promise of admission.
4. Widen to other collections only with separate authorization.

Enforcement uses persistent token buckets (migration 083). The first enforced run
starts from real balances, not from anything observe mode measured. A later policy
revision changes limits; it does not reset balances.

A refusal is not lost work. The publisher retries the same request UUID with
unchanged intent; a committed publication returns its original result without a
second charge.

## 5. Disable

Set `mode: disabled` at the current revision to stop refusing new work. Accepted
deliveries, replies, wait notices, closure reconciliation and the maintenance
commands keep working, and reservations keep being recorded. Bucket balances are kept;
re-enabling enforcement resumes from them instead of granting a fresh burst. Disabling
is the rollback. Do not remove migrations, delete reservations or purge history to roll back.

## Restore and generations

A physical `pg_restore` alone changes nothing about generations or admission. Follow the
supported [restore procedure](maintenance.md#restore-into-isolation) and
[restore admission](../restore-admission.md) in full: apply current migrations, run
`begin-restore` (which fences and advances the restore generation) before any ordinary
consumer connects, reapply newer recovery records, rebuild and `verify-restore`, then
`resume-restore`. Reconciliation (below) is a further mandatory step before enforced
admission accepts new obligations.

`begin-restore` fences open response groups and waits and their closures released with reason `restore_fenced`. Direct reply
obligations survive with their original generation, and their replies still close
them. The new generation has no reconciliation, so closure counts are unavailable.
Under enforce, new obligations refuse with `CLOSURE_UNRECONCILED` until an operator
completes a reconciliation for that generation. Run step 2 after every resumed
restore.

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
  `event-pressure-collection` until every reservoir is zero: pending and leased
  deliveries, queued quiet notices, unassigned pool requests, pending schedule
  occurrences, waiting waits and the pending wait outbox. Expire, cancel or flush
  through the supported commands; never delete rows;
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
