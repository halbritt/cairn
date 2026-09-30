# Search cost at 1,000, 5,000 and 10,000 notes (CAIRN-116)

agent-235, 2026-09-30, base `1392257`. Disposable PostgreSQL 17 only; no
production data, real notes or provider calls.

## Result

Ordinary context search grows roughly linearly with collection size. The
suspected redundant body hash in `makeIDFMember` is real but costs under 1% of
search CPU, so it was left unchanged. The measured bottleneck was
`rankingTerms`, 61% of search CPU at 10,000 notes. It re-split its two fixed
stop-word lists with `strings.Fields` and deleted every stop word from a fresh
map on each call. Preview construction calls it once per word of every
candidate body. Building the stop lists once and filtering the smaller side
removes about 40% of median search time. Every token set is unchanged, so
rankings, previews and sealed replay are unchanged.

| Notes | Median before | Median after | p95 before | p95 after |
|---:|---:|---:|---:|---:|
| 1,000 | 327 ms | 182 ms | 754 ms | 442 ms |
| 5,000 | 1,610 ms | 892 ms | 2,519 ms | 1,551 ms |
| 10,000 | 2,939 ms | 1,761 ms | 3,559 ms | 2,501 ms |

Returned index entries (1 per matching query, 0 for no-match) and IDF cohort
size N (equal to the note count) were identical before and after.

## Procedure (frozen before execution)

- **Path.** `core.Store.Index`: the compile, receipt and handle transaction used
  by API and MCP search. Purpose `context`, 8,000-byte room (the hook's), hosted
  destination. The socket and token layer is not included.
- **Corpus.** A deterministic generator (PCG seed 235, 116): Zipf (s=1.1) words
  from 400 fixed technical words plus 3,000 synthetic rare tokens. Body sizes:
  40% 200–600 B, 35% 600–1,500 B, 20% 1,500–4,000 B, 5% 4,000–12,000 B. The
  resulting median body is 851 B, p95 3,994 B and max 11,963 B. Notes are
  created cumulatively through `Store.Create`.
- **Queries.** Six fixed queries: common terms, task-like, rare synthetic terms,
  quoted literal, a 48-keyword hook-style query, and no match. One discarded
  warmup, then 5 measured runs each: 30 searches per size.
- **Harness.** `core/scale_measure_test.go`, run with
  `CAIRN_SCALE_MEASURE=1 CAIRN_SCALE_OUT=DIR` and a disposable
  `CAIRN_TEST_DATABASE_URL`, `go test -run TestScaleMeasure -timeout 15m ./core`.
  A run takes about 2.5 minutes.
- **Environment.** Intel i5-11400F (12 threads), Go 1.25.0, PostgreSQL 17.11
  with fsync off. The host was shared: load average about 3.6–4 before the run.
  Treat single-sample differences under about 15% as noise.
- **Attribution.** CPU profile over the measured searches at 10,000 notes,
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

## Remaining cost and next step

After the change, a 10,000-note search still takes about 1.8 s median. The
largest remaining cost is still preview scoring: `indexPreview` builds a map
for every word of every matching candidate. The candidate loop computes a
preview for each candidate before budget packing rejects all but one or two.
Two options come next, each needing its own review:
- score tokens without per-token maps, keeping exact output;
- defer preview construction until an entry can still fit the optional budget.
The second changes when previews are computed, not what they are.

The duplicate `makeIDFMember` hash could reuse `CandidateFacts.BodySHA256`. At
under 1% of CPU it is not worth a change on its own.
