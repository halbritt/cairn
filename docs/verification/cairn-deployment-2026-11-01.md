# Cairn release — 2026-11-01

Two releases went to the primary CLI and API on 2026-11-01. The first deployed
[candidate detail retention](../candidate-retention.md) and migration 074. The second
deployed the search retry, refusal logging and alert closing from Plane CAIRN-274.
Both are clean source builds with Go 1.25.0.

| Release | Revision | Binary SHA-256 | Installed (PST) |
| --- | --- | --- | --- |
| Retention | `0f2917ae81104fb59d61fcaff3bbf6db1a0ecbd8` | `b40cefdba6f0ca4d2690ba67225c7eeea62618497c6b471d0314406135aa4cc8` | 18:53 |
| Search retry | `d2cccca5a619b4d1c6fefc641814ff3e1751d463` | `d41725a85c22a592055cc4ecd49631390851c5c8ef8971d668d377df902e257d` | 20:07 |

The previous installed revision was `b0ae063`, SHA-256
`984c40170dc7fc77d76caecd537583d3b64a58ab8393926bfdd77c23e067a86d`.

## Recovery point and validation

pgBackRest differential `20261101-010848F_20261101-185001D` completed just before the
migration, with status `ok`. Earlier that evening a full restore of the same chain
succeeded on an isolated `/nvr` target in 768 seconds on PostgreSQL 18, the first
rehearsal on this major version. Twelve sampled receipts matched production. A restore
limited with `--db-include` failed: replay on the zeroed excluded databases panicked
with "WAL contains references to invalid pages", so rehearse with a full restore.

A logical archive of all 267,355,652 candidate rows was taken from that restore, so
production was not scanned. It is 14.7 GB with a SHA-256 sidecar, and 8 of 8 sampled
receipts matched production. It lives at
`/nvr/archive/cairn/retrieval-candidate-archive-20261101/`, local to this host.

`make test-integration` and `make check` passed on both landed revisions through
`git-land`, including race-enabled Go packages and protocol skew against the legacy
binary. Production was not used for tests or test cleanup.

Ordinary tests were not sufficient for this release:

- A rehearsal on the restored production copy found that the CLI's 30-second deadline
  cancelled long operator phases.
- A first independent review found that a repeatable-read transaction waiting at the
  admission row could read the swapped table with an older snapshot and return an empty
  list; that the hold grew with the receipts expiring; and that restore fixtures were
  ranked across channels that cannot use them.
- A second review found that the migration deadlocked under traffic, in 12 of 12 runs,
  and that ranking fixtures per channel let protection grow with the number of agents.

All of these were fixed and have regression tests. The second review's reruns confirmed
the first set of fixes. The fixes from the second review are covered by regression tests
and were not re-reviewed. A trial export that reads a wrapper-owned disposable database
skips the admission row; only a swap inside that disposable database could affect it,
so it was left unchanged.

## Deployed schema and services

The live store advanced from migration 073 to 074 on the first attempt, under a 2 s
lock timeout, while the older API kept serving. Migration 074's stored SHA-256 matches
source: `0d6b8d86a2e076bb1f44b2d0ecbcbe02f19c53285630866552f14b639b56053a`. It adds a
nullable marker and a partial index, sets autovacuum thresholds, and makes the
restore-resume trigger ignore unchanged writes. The second release has no migration.

For both releases there were zero unfinished wake attempts, zero unexpired delivery
leases and no long transactions beforehand. Each worker cgroup held only its main
process. The binary was replaced atomically, and the API, scheduler, presence watcher
and seven wake supervisors were restarted. The services checked after each release
reported active with zero automatic restarts. Client and server report the same clean
revision, and the running API's executable hash matches the release.

## Production verification

- Expired receipts refuse `explain`, `explain-page` and `recompile` with
  `CANDIDATE_DETAIL_EXPIRED` (exit 4). The API test covers the HTTP 410 status.
- A retained receipt's `explain-page` returns `retained_evaluated_set`, and `recompile`
  with its known query reproduced the stored seal, before and after the swap.
- Hosted search and exact pull work, new receipts write to the new relation, and the
  docket still reads.
- The first daily expiry expired 83 receipts, 308,641 rows, with 222 MB of WAL.
- A refused request wrote
  `{"event":"refusal","operation":"explain-page","code":"CANDIDATE_DETAIL_EXPIRED","status":410}`
  to the journal.

The rewrite's measurements, timeline and impact are in the host record,
`hosts/proximal/config/cairn/candidate-retention.md` in the infra repository.

## Search retry measurements

Three runs on a disposable database, 10 seconds each, with every writer searching the
same notes. Before and after are the same build apart from this change.

| Load | Failed before | Failed after | Slowest search before | Slowest search after |
| --- | --- | --- | --- | --- |
| 3 writers, no pause | 15.8% | 1.9% | 120 ms | 1.4 s |
| 8 writers, no pause | 39.5% | 8.1% | 101 ms | 1.3 s |
| 8 writers, 200 ms apart | 1.0% | 0% | 101 ms | 305 ms |

In a separate run on the earlier retry code, writers searching different notes did not
fail at all (0 of 3,997), because the conflict is on the exposed note's row. Production
before the change: at most 3 failures in 3,679 searches over 4.4 days, inferred from
PostgreSQL error bursts because nothing counted them. The refusal log now counts them.

## Limits

- Bounded growth is proven by a plateau test and by the first daily run, not by weeks of
  production operation. The next natural run is at about 04:20.
- The bound is the window times the write rate, and each search writes one row per note,
  so it grows with both search volume and note count.
- The rewritten table holds 87 GB and keeps that allocation. It is reused as older rows
  expire. It would take another rewrite to return the difference once it settles.
- Delivery-alert closing was tested against a stub `plane` CLI. No real condition has
  cleared since deployment.
- Retries reduce search failures but do not remove the contention, which still fails
  searches under sustained saturation.
- `recall_miss_candidate` and receipts still grow without a retention policy. They are
  far smaller: 624 MB and 548 MB today.
- Do not roll back to a binary older than `0f2917a`. Older binaries report an expired
  receipt as having no candidates.

The prior binaries and private logs are under
`~/.local/share/cairn/deployments/candidate-retention-20261101/`.
