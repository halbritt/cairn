# Native-history coverage trial — 2026-09-07

The opt-in `TestStriatumHistoryRecurrenceCoverage` reads committed Striatum-next
clauses D0005.C10, D0008.C1 and D0013.C2, explicitly captures the source evidence,
and promotes their exact text in a disposable Cairn store. It uses 12 native
commit subjects affecting decisions, LLM supervision or scheduling as transient
queries, with current source-revision pins. It compares retrieval with an empty
memory baseline and recompiles every retained read set to verify its seal.

These are **later recurrence scenarios**: the notes were captured now. The trial
does not supply them to original incidents as if they had existed at the time.
Commit subjects provide native queries but are not full task prompts or failure
traces. Selected-clause coverage is not a relevance judgment, avoided failure,
accepted task, or comparison against native-memory/search/model baselines.

The [machine-readable result](striatum-history-coverage-2026-09-07.json) retains
source commit/file hashes, scenario commit IDs, query digests and selected clause
IDs. It retains no raw task/query text. All 12 historical read sets reproduced
their original seals. The first pass exposed overly broad common-word matches;
lexical ranker v2 excludes a fixed function-word set and rejects common-word-only
queries as relevant demand. Historical ranker v1 remains available for replay.
Precision and causal usefulness remain unmeasured.

Reproduce against a dedicated temporary PostgreSQL cluster:

```sh
CAIRN_HISTORY_SOURCE=/home/halbritt/git/striatum-next \
CAIRN_HISTORY_REPORT=/tmp/cairn-striatum-history-coverage.json \
make test-integration
```

The source tree is read through committed Git objects; unrelated working-tree
changes are neither consumed nor modified. The test is skipped when those explicit
variables are absent. No model endpoint, live Striatum dispatch or operational
Cairn database is used.
