# Search cost at 1,000, 5,000 and 10,000 notes (CAIRN-116)

agent-235, 2026-09-30, base `1392257`. Disposable PostgreSQL 17 only; no
production data, real notes or provider calls.

## Result

In this generated fixture, context search grows roughly linearly with collection size. The
suspected redundant body hash in `makeIDFMember` is real but costs under 1% of
search CPU, so it was left unchanged. The measured bottleneck was
`rankingTerms`, 61% of search CPU at 10,000 notes. It re-split its two fixed
stop-word lists with `strings.Fields` and deleted every stop word from a fresh
map on each call. Preview construction calls it once per word of every
candidate body. Building the stop lists once and filtering the smaller side
reduced the pooled sample median by about 40–45% in these runs. The change
preserves the stop-word sets and the resulting token sets; equivalence tests
and the disposable integration suite check the existing ranking/replay behavior.

| Notes | Sample median before | Sample median after | Sample p95 before | Sample p95 after |
|---:|---:|---:|---:|---:|
| 1,000 | 327 ms | 182 ms | 754 ms | 442 ms |
| 5,000 | 1,610 ms | 892 ms | 2,519 ms | 1,551 ms |
| 10,000 | 2,939 ms | 1,761 ms | 3,559 ms | 2,501 ms |

Returned index entries (1 per matching query, 0 for no-match) and IDF cohort
size N (equal to the note count) were identical before and after.
Each row pools 30 measurements across six fixed queries, five per query. The
estimator selects sorted index `round(p * (n - 1))`: the median is the upper
middle sample, and p95 is sample 29 of 30. These are fixture sample summaries,
not production service percentiles or a query-traffic distribution.

## Procedure and corrections to the frozen description

- **Path.** `core.Store.Index`: the compile, receipt and handle transaction used
  by API and MCP search. Purpose `context`, fixed 8,000-byte fixture room, hosted
  destination, trusted store principal `scale-agent235`. The current source's
  recall search default is 32,000 bytes; this fixture did not use that default.
  The socket, token authentication, embedding worker and provider wake path are
  not measured. This is store latency, not authenticated end-to-end API latency.
- **Corpus.** A deterministic generator (PCG seed 235, 116): Zipf (s=1.1) words
  from 269 fixed word entries (261 unique) plus 3,000 synthetic rare tokens. Body sizes:
  40% 200–600 B, 35% 600–1,500 B, 20% 1,500–4,000 B, 5% 4,000–12,000 B. The
  10,000-note cohort has median body 851 B, p95 3,994 B and max 11,963 B. Notes are
  created cumulatively through `Store.Create`.
- **Queries.** Six fixed queries: common terms, task-like, rare synthetic terms,
  quoted literal, a 48-keyword hook-style query, and no match. One discarded
  warmup, then 5 measured runs each: 30 searches per size.
- **Harness.** `core/scale_measure_test.go`, run with
  `CAIRN_SCALE_MEASURE=1 CAIRN_SCALE_OUT=DIR` and a disposable
  `CAIRN_TEST_DATABASE_URL`, `go test -run TestScaleMeasure -timeout 15m ./core`.
  A run takes about 2.5 minutes.
  The 13-minute guard checks during note creation; the 15-minute Go test timeout
  can stop later phases without writing an incomplete row. Inspect test exit
  status and require all three completed rows before accepting a run.
- **Environment.** Intel i5-11400F (12 threads), Go 1.25.0, PostgreSQL 17.11
  with fsync off. Recorded one-minute load average was 5.91 before the baseline
  and 5.12 before the changed run. The shared host, fixed query order and single
  before/after run limit generalization; no confidence interval was measured.
- **Attribution.** CPU profile over six warmups and 30 timed searches at 10,000 notes,
  before the change. Total 102 s of samples:
  - `rankingTerms`: 61% cumulative, of which `indexPreview` token scoring was
    about 53%.
  - `strings.Fields`: 21%.
  - map deletes: 14%.
  - GC mark workers: 22%.
  - `collectCandidates` body tokenization: 15%.
  - All SHA-256: 1.26%.
  - `makeIDFMember`: 0.81%, including its duplicate hash.
  - Candidate reads from PostgreSQL: 1.2%.

The pre-run procedure is retained at
`/tmp/cairn-agent235-idf-scale/procedure.frozen.md`. Review corrected its stated
vocabulary count, hook room, load averages and profiling window against source
and saved artifacts. The corpus, query set and recorded measurements were not
changed. Raw samples and environment records are in that directory and `after/`.

## Remaining cost and next step

After the change, the 10,000-note fixture still takes about 1.8 s sample median. The
largest remaining cost is still preview scoring: `indexPreview` builds a map
for every word of every matching candidate. The candidate loop computes a
preview for each candidate before budget packing rejects all but one or two.
Two options come next, each needing its own review:
- score tokens without per-token maps, keeping exact output;
- defer preview construction until an entry can still fit the optional budget.
The second changes when previews are computed, not what they are.

The duplicate `makeIDFMember` hash could reuse `CandidateFacts.BodySHA256`. At
under 1% of CPU it is not worth a change on its own.
