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
