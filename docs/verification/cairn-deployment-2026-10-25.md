# Cairn deployment — 2026-10-25

## Release

The primary CLI and API were upgraded to source revision
`99745c35417c7467683329afcc4dcdd008aaa09a`, built with Go 1.25.0 and a clean
VCS stamp. The installed binary SHA-256 is
`e3a4b3a552185c7e94ec99f22417ba24266c90a336a22368723b1ac074836fd4`. The
previous `19f39f9` executable was retained with SHA-256
`272e2ead0fe53d52fd1daa75fffb6ecf03aadf5a1d0030e73e4d18f8532e92c1`.

The production PostgreSQL 17 database advanced from migration 068 to 070 in one
transaction. The stored migration digests match the source:

| Migration | SHA-256 |
| --- | --- |
| 069 — native poll without a preselected delivery | `ff025d618323485c073ad0fe5e476a84a28a51e2d99742d54f6fb148efd48d74` |
| 070 — transcript unavailable capture outcome | `5713c8dc59f124525fd7fa1b73a71acdaa07fe455392ae6040105898c462e059` |

The migration set applies transactionally. Schema 069 relaxes poll constraints;
schema 070 adds an allowed capture-observation outcome. The migration does not
remove stored rows or payloads.

## Recovery point

Before migration, pgBackRest completed differential backup
`20261018-010500F_20261025-000929D` from the retained full backup
`20261018-010500F`. The differential ran from 2026-10-25 07:09:29 to 07:14:37
UTC and covered the complete database. Its recorded LSN range was
`1A1/3B0001E0`–`1A1/3BE78600`, with archived WAL segment
`00000001000001A10000003B`. pgBackRest reported a 109.0 GB data delta and a
13.4 GB repository delta; no tables or data were excluded. The stop WAL segment
was archived and the backup completed successfully. The PostgreSQL archiver
continued successfully after migration, with zero recorded failures during
post-deployment verification.

The backup ran under the configured retention policy, which expired the
2026-10-19 and 2026-10-20 differential backups after the newer differential
completed. The full backup and new differential remain in the restore chain.
An earlier isolated restore rehearsal successfully applied migrations 069 and
070 to a clone. No test data was created or cleaned up in production; the
post-deployment service smoke checks are described below.

## Quiesce, migration and service recovery

Before quiescing the main services, read-only database checks found zero active
session attempts, zero wake attempts and zero unexpired delivery leases. The
scheduler and seven wake workers were stopped first, followed by presence and
the primary API. The separate `cairn267-distinct-api-4884f6a.service` was left
running. It uses build `4884f6a`, its configured executable path is absent, and
its existing process answered a version request before and after the migration.
It was not restarted.

The candidate `cairn migrate` command applied migrations 069 and 070. A
read-only query confirmed exactly 70 stored migrations and the exact checksums
above before the primary executable was replaced atomically. The primary API
then started on revision `99745c3`; its client and server version reports
matched. The first immediate API version probe raced socket startup and failed
with `API_CONNECTION_FAILED`. After three seconds, the socket was present with
owner-only permissions and the authenticated version request succeeded. The
ordinary hosted search and exact-source pull smoke check then succeeded, with
the pulled record/version and recomputed body SHA-256 matching the search
result. Presence, scheduler and all seven wake workers were restarted and
reported active. A final database check found zero active attempts and leases.

The read-only `check_effective_recall_deployment.py --compact` inventory
returned `ok=true`, four ordinary hooks, three already-enabled inbox bindings,
and no issues. Its `live_observed` field remains false. The selected and peer
account configurations were not edited. Capture observations, compact recall,
compact context lifecycle, compact inbox, notice batching and compact MCP
profiles remain unconfigured; existing full/default paths remain available.

## Validation and limits

`make test-integration` passed against its disposable PostgreSQL cluster and
`make check` passed before deployment. The direct production search/pull was a
post-deployment service smoke check, not a test suite or provider task. No model
provider was called for deployment validation.

This release installs the current primary Cairn binary and schema. It does not
establish token savings, equal task quality, live use of the new opt-in
behaviors, or full design acceptance. The CAIRN-227 22-situation, four-arm
acceptance panel remains **Defer**. The secondary `4884f6a` API remains on its
older build and should not be restarted until its missing executable path is
resolved.

The database migrations are forward-only. The retained old binary is available
for a binary rollback if needed; do not run its `migrate` command against schema
070 or restore an older database without the restore-admission and recovery
reconciliation procedure.

## Schema 071 deployment — 2026-10-25

The primary binary and production database now include the recovery-action
observation slice from runtime commit `9cdc593`. The candidate was built from
clean `main` revision `6aec920d9c5c9ec7b8253e9bcfd13c9cd10cec32`; after that
build, `c4c192c` added a regression test only. Runtime and schema sources after
`9cdc593` were unchanged. The installed binary reports Go 1.25.0, that exact
clean VCS stamp, and SHA-256
`ee3e8d7afa2eb4e864437456ebc9cfc764a8a2979937cc4f9f44750637971402`.
The previous `99745c3` binary remains at
`~/.local/bin/cairn-before-schema071-20261025`, with SHA-256
`e3a4b3a552185c7e94ec99f22417ba24266c90a336a22368723b1ac074836fd4`.

Before quiescing services, read-only production checks found schema 070, 70
migration rows, zero active wake attempts, zero active session attempts, and
zero unexpired delivery leases. The pgBackRest differential completed
successfully as `20261025-010451F_20261025-020026D`, based on full backup
`20261025-010451F`. It covered the full database, with WAL start/stop
`00000001000001A100000074` and LSN start/stop `1A1/740000D8`–`1A1/74D814A0`.
pgBackRest reported 19.2 GB database backup size and 1.9 GB repository size;
the stop WAL was archived. The stanza remained `ok`. Retention expired the
2026-10-23 differential; the current full and new differential remain in the
restore chain. At verification, `/nvr/pg-backups` had 8.0 TB free and `/` had
396 GB free.

The scheduler and seven wake workers were stopped, then presence and the
primary API. The separate `cairn267-distinct-api-4884f6a.service` stayed active
and was not restarted. Before migration, `pg_stat_activity` and `pg_locks`
showed no idle-in-transaction session or lock on `cairn.agent_delivery`; the
old side API had no conflicting transaction. A five-second PostgreSQL
`lock_timeout` bounded migration lock acquisition. The candidate migration
completed successfully. Production now records 71 migrations; migration 071
matches source SHA-256
`e912563357774216fccd1980984133319c9be76133c71da9d27ab949e8b6cafb`. The
`recovery_revision` column is `bigint NOT NULL DEFAULT 0`, both immutability
triggers are enabled, and the new ledger has zero rows. Attempts and unexpired
leases remained at zero after migration. No production test data was created
or removed.

The candidate replaced the primary executable atomically. Authenticated client
and server version requests both report revision `6aec920`; authenticated
search and exact pull succeeded, and the pulled note body SHA-256 matched the
search index. The local operator `coordination-review` / `ReviewEvents` smoke
read succeeded against an existing delivery and returned an empty
`recovery_transitions` list for that delivery. This exercised the new review
query without writing a transition or otherwise changing production data.
Presence, scheduler and all seven wake workers were restarted and reported
active with `NRestarts=0`. The existing distinct API also remained active with
`NRestarts=0`. `pg_stat_archiver.failed_count` was zero; the latest archived WAL
was `00000001000001A100000076` at 2026-10-25 09:04:44 UTC. Final read-only
checks found 71 migrations, zero active wake/session attempts and zero
unexpired delivery leases.

The read-only effective-recall inventory returned `ok=true` with four ordinary
hooks, three existing inbox bindings, no issues and `live_observed=false`. No
token-efficiency opt-in or account configuration was changed; the CAIRN-227
acceptance decision remains **Defer**. The runtime release installs schema 071
and its metadata-only endpoint observations; it does not establish measured
recovery outcomes, token savings, or full design acceptance. Migrations remain
forward-only. Keep the previous binary for recovery, but do not treat a binary
rollback as a schema rollback; use a forward fix or the documented restore and
reconciliation procedure.

## Schema 072 deployment — 2026-10-25

The primary binary and production database now include migration 072 from
`76bdaf8a9d662bd7a492e550dec20eb94831d0b8`. It extends the
`capture_observation.admission_candidate` check to allow the shadow label
`confirmed_duplicate` alongside `transport_only`. The capture hook records this
classification when the current snapshot matches its previously captured
digest. It remains observational: it does not skip capture or enable a
token-efficiency optimization. Existing production observation rows were not
changed by the migration; all seven have a null admission candidate. No
token-efficiency opt-in or account configuration was changed, and CAIRN-227
remains **Defer**.

The candidate was built from clean `main` revision
`76bdaf8a9d662bd7a492e550dec20eb94831d0b8`, with Go 1.25.0 and
`vcs_modified=false`. Its SHA-256 is
`66cf9b7f9bba6afe58978a8753f032d2ea64e44c18d147446efc4edcfb1242b6`.
`make test-integration` passed against a disposable PostgreSQL cluster and
`make check` passed before deployment. No production database was used for
tests, and no model provider was called. The previous `6aec920` binary remains
at `~/.local/bin/cairn-before-schema072-20261025`, SHA-256
`ee3e8d7afa2eb4e864437456ebc9cfc764a8a2979937cc4f9f44750637971402`.

Before the backup, production was at schema 71 with the exact 071 digest
`e912563357774216fccd1980984133319c9be76133c71da9d27ab949e8b6cafb`;
migration 072 was absent. There were zero unfinished wake attempts, zero
unfinished session attempts, zero unexpired delivery leases, and seven capture
observations. No idle-in-transaction sessions or locks on
`capture_observation` were present at the pre-migration checks. The distinct
`cairn267-distinct-api-4884f6a.service` was left running throughout deployment.

A fresh pgBackRest differential completed as
`20261025-010451F_20261025-024442D`, based on full backup
`20261025-010451F`. It ran from 02:44:42 to 02:46:10 PDT, with start and stop
WAL `00000001000001A100000084` and stop LSN `1A1/846BFA88`. The backup reported
a 19.3 GB database delta and a 1.9 GB repository delta. The stop WAL was
archived, stanza status was `ok`, and PostgreSQL archive failures remained at
zero. Retention expired the older 2026-10-24 differential; the full backup and
current differential remain in the restore chain. After backup,
`/nvr/pg-backups` had 8.0 TB free and `/` had 398 GB free.

The scheduler, seven wake workers, presence service and primary API were
stopped; the separate distinct API remained active and was not restarted. The
candidate migration ran as schema-owner role `halbritt` over the production
PostgreSQL socket with a five-second `lock_timeout`. Production now records 72
migrations. Migration 071 still matches the digest above, and migration 072
matches source SHA-256
`f06908576d17685db0d371aefae7c290e79126ce9ac1b46222beaeb01a7138fe`. The
constraint allows exactly `transport_only` and `confirmed_duplicate` (while
continuing to permit null). The capture row count remained seven. No production
test data was created or removed.

The candidate replaced the primary executable atomically. Authenticated client
and server version checks both report `76bdaf8`, Go 1.25.0 and a clean VCS
stamp. Authenticated search and exact pull succeeded; the pulled note body
SHA-256 matched the search index. The read-only operator `coordination-review`
/ `ReviewEvents` smoke returned one failed delivery with an empty
`recovery_transitions` list. The effective-recall deployment inventory
returned `ok=true`, four ordinary hooks, three existing inbox bindings, no
issues and `live_observed=false`. Presence, scheduler, all seven wake workers,
the primary API and the distinct API are active with `NRestarts=0`. Final
read-only checks found 72 migrations, zero unfinished wake/session attempts,
zero unexpired leases, zero archive failures, and latest archived WAL
`00000001000001A100000085` at 09:47:57 UTC.

This deployment makes the duplicate-capture label available for observation;
it does not establish duplicate-admission usefulness, capture skipping, token
savings, measured task quality, or full design acceptance. The CAIRN-227
acceptance panel remains **Defer**. Migrations are forward-only. Keep the
previous binary for recovery; use a forward fix or the documented restore and
reconciliation procedure rather than attempting a schema downgrade.
