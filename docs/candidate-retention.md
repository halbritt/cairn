# Candidate detail retention

Every compile writes one `retrieval_candidate` row for each record it evaluated,
including records that did not match. On a store with thousands of notes this is
thousands of rows per search, so this table can dwarf all other storage. Retention
bounds it. Memory records, receipts, seals, saved package bytes and `record_use`
are not touched.

The operator chooses how long detail stays available. **There is no default
window.** Record the selected value and its rationale in your deployment
configuration so operators can assess which diagnostic history will expire.

## What each path needs

| Reader | Needs candidate rows? | After expiry |
| --- | --- | --- |
| `replay`, saved package bytes, seal | No | Unchanged |
| Compile retry with the same request ID | No | Same receipt; no rows recreated |
| `expand`, index handles and pull budget | No | Unchanged; handles expire on their own schedule |
| `record_use`, usage, impact and reports | No | Unchanged |
| `explain`, `explain-page`, `recompile` | Every row | Refuse with `CANDIDATE_DETAIL_EXPIRED` |
| `verify-restore` fixtures | Every row | Reported as `FIXTURE_CANDIDATE_DETAIL_EXPIRED:RECEIPT` |
| Recall-miss capture | Rows of the search it observes | Unaffected at normal timing; an expired receipt yields no proposal |
| Docket `ESCALATION_BLOCKED` | Blocked rows | Blocked rows are retained |
| Ordinary deletion's foreign key | Any row | See below |

The refusal is HTTP 410 and CLI exit 4. Ownership, destination and payload checks
run first, so an unauthorized caller learns nothing about expiry. A receipt marked
in `candidate_detail_expired_at` never returns an empty candidate list. Explanation
version 0 keeps its existing meaning: a legacy receipt that never had detail.

## What survives a receipt's expiry

- **Blocked demand and gate refusals.** Rows with `escalation_blocked` or a gate
  reason (`AUTHORITY_INACTIVE`, `OPEN_CONFLICT`, `CURRENTNESS_MISMATCH` and the
  rest) stay. The sealed package counts omissions by reason but does not name the
  records, so these rows are the only record of which ones a gate refused. They are
  not readable through `explain`; they back the docket and direct operator queries.
- **Run-bound receipts** keep all rows with no automatic expiry: a claimed launch,
  run binding, outcome, assessment, managed context, delivery receipt, run-retrieval
  link or wake attempt. Their decisions stay reviewable with no defined end, and
  their volume follows real runs rather than searches.
- **Restore fixtures.** `verify-restore` requires a fixture that selected content,
  can only recompile receipts its own channel owns, and cannot compile new ones while
  paused. So the newest 100 body-selecting receipts owned by the operator channel
  running retention keep their rows, per repository, matching its 100-fixture limit.
  Override with `keep_selected_per_repo`, and run retention as the same operator that
  would run a restore. Agent receipts are not usable fixtures and get no protection.

## Ordinary deletion

A candidate row's foreign key is what stops `cairn delete` removing a note that a
retained compilation considered. That protection now lasts as long as the read set
does: once every referencing receipt has expired, a note with no other references
can be deleted. Any in-window receipt, `record_use` row or retained gate row still
blocks it. Saved packages do not depend on a note they merely considered.

Binding and launching a run (`bind-run`, `ClaimRun`) refuse an expired receipt: a run's
decisions stay reviewable, which an expired receipt could not support. Observations
about something that already happened, such as a delivery or an outcome, are still
recorded, because they are worth more than the diagnostics. Recording one does not
restore detail: protection applies to receipts bound before they expired.

## Expire detail

```sh
cairn candidate-retention <<'JSON'
{"window_days": 14, "dry_run": true}
JSON
```

`window_days` is required (1 to 36500). A dry run reports eligible and protected
receipts and writes nothing. Each receipt expires in its own transaction: the marker
and the row deletion commit together, so a crash leaves it either untouched or
completely expired. Repeat the request to resume. Receipts locked by another
transaction are skipped and counted, not waited for. Only one candidate maintenance
run can be active; another refuses with `MAINTENANCE_ACTIVE`.

| Field | Default | Stops the run when |
| --- | --- | --- |
| `max_receipts` | 20000 | That many receipts expired |
| `max_runtime_seconds` | 3000 | Time elapsed |
| `pause_ms` | 0 | Sleep between receipts |
| `limits.max_run_wal_bytes` | 16 GiB | WAL written during this run exceeds it |
| `limits.max_wal_dir_bytes` | 32 GiB | `pg_wal` exceeds it |
| `limits.max_archive_backlog_segments` | 256 | Segments awaiting archive exceed it |
| `limits.min_free_bytes` | 40 GiB | The data filesystem has less free |

`stopped_by` names the reason, and `remaining_receipts` shows what is left. These
checks read `pg_ls_waldir()`, so the role needs `pg_monitor`, and the command must run
on the database host. It refuses rather than continuing unobserved. It stops while
a restore is paused and during a storage rewrite. Deleted space is reused by later
inserts after vacuum; migration 074 lowers this table's autovacuum threshold to 2%.
It does not return that space to the filesystem.

## Reclaim existing space

`DELETE` and `VACUUM` do not shrink this table, because rows are written in time
order and the expired ones are at the front. Deleting most of it would also log
each heap page twice, once for the delete and again for vacuum.
`cairn candidate-reclaim` instead copies what retention keeps into a new relation and
swaps the names. Dropping the old relation returns its space immediately, and WAL
is proportional to the retained rows, not the deleted ones.

Run the phases in order. Each reads one JSON request and can be repeated after an
interruption. `status` is always available.

| Phase | Effect | Locking |
| --- | --- | --- |
| `plan` (`window_days` required) | Records the cutoff and the expiring receipts; creates schema `cairn_candidate_reclaim` | Brief |
| `copy` | Marks expiring receipts, one per transaction; scans for rows that outlive them; copies every receipt left unmarked, one per transaction | None on live tables |
| `build` | Builds the primary key and index, then adds and validates foreign keys | Brief on `retrieval_receipt` and `record_version` when adding foreign keys |
| `swap` | Catches up, drains transactions participating in `cairn.restore_admission`, copies at most four newly arrived receipts, renames the relations | Transactions entering `restore_admission` or touching `retrieval_candidate` wait while the drain is held |
| `verify` | Compares every receipt's row count, and byte digests of a random sample, with the old relation | None; scans both relations |
| `finish` (`confirm_drop_old: true`) | Drops the old relation and working schema. **Irreversible** | Brief |
| `abort` | Before the swap only: clears the markers `copy` set and drops the working schema | Brief, on the referenced tables |

Marking first keeps every state truthful. A marked receipt refuses, so it needs no
rows; an unmarked one has all of them. A receipt that cannot be marked because it
became run-bound or is locked is simply copied, and expires later. After `abort` every
planned receipt is whole again, because its rows stayed in the live relation.

`copy` waits out WAL and archive pressure; run budgets and disk headroom stop it, and
a later run resumes. Set `limits.max_run_wal_bytes` above the retained size. The disk
needs room for the retained rows, their indexes, sort space and WAL until `finish`.
`build` checks headroom first and reserves 30% of the copied size for sorting and the
index.

The swap writes the admission row without changing it, as `begin-restore` does, so a
repeatable-read or serializable transaction already waiting there fails with a
serialization error (`40001`). Its snapshot predates the swap, and it could otherwise
read an incomplete list from the new relation. Compiles and retrying privileged writes
retry this automatically; non-retrying repeatable-read calls return a store error for
that one call. Only transactions that enter `cairn.restore_admission` (or touch
`cairn.retrieval_candidate` during the rename) wait on the swap; the few direct-pool
helpers that bypass `restore_admission` (such as quiet-notice expiry and work-feature or
advice-control status reads) do not touch `retrieval_candidate` and do not wait on the
admission row. Waiting for the drain does not delay ordinary work, and
`lock_timeout_ms` (default 3000) bounds that wait per attempt. Holding it delays
participating transactions, so each statement under it has a fixed three-second limit,
and a client that stalls is disconnected by the server. After three attempts that held the drain
and failed, the phase stops with `stopped_by: swap_lock_unavailable` and nothing
changed. Repeat it when whatever holds the table has finished. The hold does not grow
with the number of expiring receipts. Statements elsewhere that need strong locks
(`build`, `abort`, `finish`) stop after three attempts in the same way.

`plan` and `swap` refuse if the table has anything a swap would lose: grants, triggers,
rules, views, policies, extended statistics, extra indexes or constraints, or another
owner. `swap` also refuses if columns or storage options changed after `build`. Expiry refuses until the rewrite has finished or been aborted. If `verify`
reports a mismatch, the state stays `swapped`, `finish` stays refused and the old
relation remains; investigate before doing anything else.

**Install the new binary and restart the API before expiring anything, and do not roll
back to a binary older than migration 074 afterwards.** Older binaries do not know the
marker and would report an expired receipt as having no candidates. The migration
itself is safe under live traffic and with older binaries still running: it adds a
nullable column and takes one short exclusive lock, on `retrieval_receipt`, last.
Bound it with a `lock_timeout` in the connection options and retry if it times out.

## Recovery

A receipt's rows can be restored from any archive of them. Use a temporary table,
because a receipt may have kept some rows:

```sql
BEGIN;
CREATE TEMP TABLE restored (LIKE cairn.retrieval_candidate) ON COMMIT DROP;
\copy restored FROM 'rows.copy'
INSERT INTO cairn.retrieval_candidate SELECT * FROM restored ON CONFLICT DO NOTHING;
UPDATE cairn.retrieval_receipt SET candidate_detail_expired_at = NULL
 WHERE receipt_id IN (SELECT receipt_id FROM restored);
COMMIT;
```

`explain` and `recompile` then work again, and the seal check still applies. Until
`finish`, the old relation holds the rows. Afterwards use an archive or a PostgreSQL
restore; follow [restore admission](restore-admission.md) for the latter. Backups keep
expired rows until they rotate out, so expiry is not erasure. Use
[forgetting](deletion.md) for sensitive content.

## Design choices

Age-based expiry was chosen over reducing what each compile writes:

| Option | Bounds storage? | Cost |
| --- | --- | --- |
| Age-based expiry (chosen) | Window times write rate | Old diagnostics refuse explicitly; in-window behavior is unchanged |
| Omit non-matching rows at write | No: slower growth only | Recompilation verifies the whole evaluated set and IDF cohort, so new receipts could not be recompiled, and `retained_evaluated_set` would be false |
| One compressed value per receipt | No: 10–15× smaller only | Rewrites three readers and loses the foreign key that protects considered notes |
| Column compression | No | Measured no effect: PostgreSQL only compresses tuples above 2 KB, and rows are about 700 bytes, whatever `toast_tuple_target` says |
| Time partitions | Yes | Partition lifecycle, a changed primary key, and copying protected rows out before each drop |

Write volume still grows with both search rate and note count, so the bound moves
with them. If that matters, the next step is a smaller window, then one of the write
reductions above.

Reclamation options considered:

- `VACUUM FULL` holds an exclusive lock for the whole rewrite, which is an outage.
- `pg_repack` is mature and needs no code here, but it copies live rows, so the
  expired rows must be deleted first. That logs their heap pages for the delete and
  again for vacuum before the copy logs the retained rows: roughly two to three times
  the WAL of the rewrite. It also needs a superuser-installed extension in this
  database, and it discards the old relation at the end, so verification and rollback
  have to come from a backup.

`candidate-reclaim` is isolated in `core/candidate_reclaim.go`. Expiry does not depend
on it, so it can be replaced by `pg_repack` if that trade-off changes.

The pinned design proposes compacting durable receipts after 90 days "only if
dependencies and required fields survive" and a 90-day floor for `record_use`
([§10.2](sources/agent-memory/design/agent-memory-design.md)). Receipts and
`record_use` are retained here without an expiry. The candidate rows beyond the
required use, enforcement and correction fields are diagnostics whose window is an
operator policy.
