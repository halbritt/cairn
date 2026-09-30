# Authenticated retrieval at synthetic scale (CAIRN-116)

Measured against source base `e218a5e` using the real CLI, authenticated Unix API,
PostgreSQL, and unmodified lifecycle `memory.py` in `agent_tools` mode. No runtime
optimization was made. The fixture passes its observed 5-second hook and
9,500-byte delivered-context targets; this is not a production latency guarantee
or evidence of task usefulness.

## Corrected exact-count run

Sizes include three synthetic controls: one mandatory shareable instruction,
one local-only note and one note scoped to another task. The ordinary generated
body counts are therefore 997, 4,997 and 9,997. This distinction matters at the
compiler's 10,000 matching-record scan limit.

Each size has six fixed queries, one retained warmup and three measured repeats
per query. Timing includes subprocess execution, token authentication, socket
transport and the actual operation. Table entries are sample median / nearest-rank
sample p95 in milliseconds. The median uses the upper middle observation. With
18 observations, nearest-rank p95 is the maximum; these are not service percentiles.

| Total records | Authenticated search (18) | Authenticated pull (15) | Full hook process (18) | Local CLI version control (18) |
|---:|---:|---:|---:|---:|
| 1,000 | 222 / 315 | 61 / 73 | 291 / 380 | 3.9 / 4.9 |
| 5,000 | 1,134 / 1,961 | 359 / 607 | 1,345 / 2,088 | 3.9 / 6.7 |
| 10,000 | 1,889 / 2,886 | 438 / 751 | 2,313 / 3,040 | 3.8 / 10.2 |

All 54 measured searches succeeded. Nine returned zero optional entries (three
no-match repeats at each size), still preserving required context. All 45
attempted measured pulls succeeded; nine pull slots were explicitly not run
because no handle was available. All 54 measured hooks succeeded, with no empty
context, missing required instruction, observed excluded sentinel, inner/outer
timeout or 5-second exceedance. Every hook delivered 2,680 UTF-8 bytes, below
9,500. All warmups also succeeded. The measured maximum hook time was 3,039.896 ms.

The parent-side Popen interval had a pooled measured median of 0.379 ms. It
measures process creation, not completed executable initialization. The separate
local `cairn version` control includes executable startup and trivial command
work but no API call. Hook state also retains its internal recall duration;
the full hook column includes Python startup, hook dispatch, search subprocess,
state persistence and serialization. Do not subtract the search column to claim
authentication or hook-only overhead: hook query transformation changes the work.

## Frozen workload and paths

The harness writes `plan.json` before building/seeding/measurement, pinning source,
generator/hook/harness hashes, query prompts, counts, controls, room and bounds.
It uses the existing `scaleBody` PCG(235,116)/Zipf generator. Bodies are deterministic;
record UUIDs/timestamps are fresh. Corpus-prefix hashes are retained per stage.

The CLI search strings are the prior six-query fixture: common terms, a migration
procedure, three rare tokens, a quoted literal, 48 fixed keywords, and no match.
Each stage uses identical strings/order/counts. The hook uses those strings as
prompts in an empty fixture project named `project`; its real `retrieval_intent`
adds that project name, sorts/removes terms and retains quoted anchors. For
example `retry backoff scheduler` becomes `project backoff retry scheduler`, and
`zzqx nothingmatches` becomes `project nothingmatches zzqx`. All six derived
strings are retained in the review artifacts. The latter is not necessarily a
no-match search after transformation.

Both paths use the current 32,000-byte search room, **not** the earlier store
fixture's 8,000. The isolated hook config sets 9,500 delivered bytes and
`recall_mode=agent_tools`; the published hook's search timeout is 5 seconds and
its overall internal recall deadline is 11 seconds. The harness gives each
subprocess an 8-second outer timeout. The 5-second assessment includes the full
hook process and is not inferred from timeout configuration.

`agent_tools` performs the real search and delivers required context plus the
native-tool delegation cue. It does not run an ambient selector or pull optional
notes inside that hook. Optional pull is measured separately against the first
real returned handle. No task model, provider wake, native host hook launch,
embedding worker or ambient model selector is included. The context check uses
one short mandatory instruction, not an adversarial near-cap instruction set.

## Failures retained and procedure amendments

The first frozen run accidentally meant “ordinary notes plus three controls.”
At the largest stage there were 10,003 total records and 10,002 matching the
search task before sensitivity filtering. All 18 measured searches and all
18 hooks failed there (plus six warmups each). Hook diagnostics reported budget
refusal; source inspection found `collectCandidates` refuses over 10,000 matching
identities. The initial harness did not retain nonzero CLI stdout, so the exact
initial search error envelope is unavailable; exit codes, hook diagnostics and
all timings remain. The amended harness retains error envelopes.

Before another measurement, the recorded amendment defined sizes to include
controls, preserving prompts, counts, room and timeouts. An authentication
negative control then stopped that attempt before seeding because its token file
was not owner-only: the CLI correctly returned INVALID_REQUEST. After correcting
only that fixture permission to 0600, the final negative control reached the API
and returned AUTHORITY_DENIED; valid credentials then accessed the API. The
aborted run and both plans remain. This is fixture correction, not a performance
before/after comparison or runtime limit increase.

The final run took 199.600 seconds. From the first frozen plan through final
cleanup, including both amendments and the aborted setup, elapsed time was
418.09 seconds, below the initial 15-minute bound. All clusters and API processes
were fixture-owned and stopped. No production database, exported notes,
production credentials, installed configuration or service was used/changed.

## Reproduction and limits

From the repository, with Go, Python3 and PostgreSQL tools available as a
non-root user:

```sh
python3 scripts/measure-auth-retrieval.py --output /tmp/cairn-auth-scale-new-run
```

The output directory must not exist. The harness owns a fresh temporary cluster,
API socket, random token and config; it overrides inherited Cairn configuration.
It builds a CLI and opt-in seeder, creates notes through Store.Create/Issue,
and measures real authenticated agent search/pull. A deliberately invalid token
checks API authentication. The client environment cannot access the database
directly. The seeder is setup, not an authenticated latency measurement.
`TestAuthScaleSeed` is skipped in ordinary test runs and is intended only for
this disposable harness.

Every sample, including warmups, failures, empty results, timeouts and skipped
pulls, is flushed to samples.jsonl. Summary timings include all attempted
outcomes, not only successes; a timeout remains a censored observation at its
configured bound. Missing planned samples are shown by recorded counts. A zero
harness exit means measurement completed, not that all target checks passed.
Setup failures produce failure.txt and a summary of recorded/planned counts.

The final environment was Go 1.25.0 and PostgreSQL 17.11 on a shared Linux host.
PostgreSQL fsync was disabled (`-F`), matching the earlier disposable fixture;
production durable-write latency may differ. One-minute load rose from 3.29 to 9.67.
The first, superseded run also overlapped a local make check at its 5,000 stage;
final-run validation ran after measurement. Fixed query order, a small sample,
warm caches, shared load and generated text limit generalization. Do not compare
the table directly to Store.Index before/after numbers: room, controls, call paths
and query transformations differ.

Changed action from prior guidance: measure the actual authenticated paths and
separate the published agent_tools hook from task-model inspection, rather than
relabeling Store.Index time or reopening the closed pilot. Search dominates the
measured cost; pull also grows with corpus size. `prepareExpansion` calls
`collectCandidates` to revalidate current eligibility, a concrete next profiling
lead. No new CPU profile attributes that cost, and no speculative cache or
eligibility shortcut is proposed. The matching-record scan limit is a separate
capacity boundary exposed by the first run. The broader retrieval-usefulness
and live native/provider latency questions remain open.

Artifacts: `/tmp/cairn-agent225-auth-scale-final/` (corrected fixture measurement),
`/tmp/cairn-agent225-auth-scale-run/` (first run),
`/tmp/cairn-agent225-auth-scale-exact/` (aborted authentication setup), and
`/tmp/cairn-agent225-auth-scale-review/` (amendment, derived queries and validation).
The harness and seed helper are reviewable source; raw runtime artifacts are not
committed. No push or deployment was performed.

Validation: the complete opt-in fixture ran against disposable PostgreSQL with
real API authentication; every planned sample and required-context/byte/time
assertion was checked, including warmups. `make check` passed after measurement.
No core runtime/store implementation changed, so the full unrelated integration
suite was not rerun.
