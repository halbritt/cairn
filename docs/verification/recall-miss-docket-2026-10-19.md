# Recall-Miss Candidate Docket and 7-Day Review Summary (2026-10-19)

## Implemented Slice

- **Schema (`core/schema/061_recall_miss_candidates.sql`)**:
  - Adds `cairn.recall_miss_candidate` keyed by `candidate_id` with a unique constraint on `(repo, prompt_sha256, record_id, version)` and indexes on `(repo, record_id, version)` and `(observation_id)`.
  - Stores only bounded metadata (`observation_id`, `receipt_id`, `prompt_sha256`, `record_id`, `version`, `lexical_matches`, `idf_score`, `created_at`); raw prompt, query, and note text are never stored.
- **Observation Recording (`core/recall_observation.go`, `core/recall_miss.go`)**:
  - Extends `RecallObservationRequest` with optional `PromptSHA256` (`^[0-9a-f]{64}$`) and `DeliveredRecordIDs` (`[]string`, up to 64 UUIDs) and exposes `Store.ObserveRecall` alongside `Store.RecordRecallObservation`.
  - On `UserPromptSubmit` observations with attached `ReceiptIDs`, `recordRecallMissCandidates` inspects active candidates on the caller's receipts with `lexical_matches >= DefaultRecallMissMinLexicalMatches` (`3`).
  - Excludes any note delivered for the prompt (present in `DeliveredRecordIDs`, expanded/cited in `cairn.usage_observation`, expanded via `cairn.record_use` with `purpose = 'context-expansion'`, or delivered as a body selection in `cairn.record_use` outside `agent_tools` mode).
  - Logs remaining omitted (`OPTIONAL_BUDGET`, `TOTAL_BUDGET`, `WHOLE_PULL_BUDGET`) or unexpanded (`agent_tools`, `ambient`, or index-only) candidates into `cairn.recall_miss_candidate` with `ON CONFLICT (repo, prompt_sha256, record_id, version) DO NOTHING`.
- **Demand Docket (`core/docket.go`)**:
  - `Store.Docket` queries active `cairn.recall_miss_candidate` rows deduplicated by `(record_id, version)` (retaining the latest `receipt_id`) and emits `DocketItem` entries with `Reason: "RECALL_MISS_CANDIDATE"` at priority `5`.
- **7-Day Candidate Review & Precision Summary (`core/recall_miss.go`)**:
  - `Store.RecallMissReport(ctx, repo, days)` computes a bounded UTC window summary (default 7 days, max 90 days) reporting `Observations`, `ObservationsWithCandidates`, `CandidateCount`, `DistinctNotes`, `ConfirmedCount`, `UnconfirmedCount`, `PendingCount`, `Precision`, and up to 100 `Candidates` cross-referenced against subsequent expansions (`usage_observation`, `record_use`) or rejected `run_assessment` outcomes.

## Idempotency Guarantee

- Candidate insertion uses `UNIQUE (repo, prompt_sha256, record_id, version)` with `ON CONFLICT DO NOTHING`.
- When `PromptSHA256` is omitted by the caller, the store derives the deterministic 64-character lowercase hex `request_digest` from the observation's receipt.
- Exact `RequestID` replays return the original observation via `mutate`, and repeated hook observations carrying the same prompt digest across retries preserve the exact candidate set (`3 -> 3`, never `3 -> 6 -> 9`).

## Tested Claims vs. Full Design Acceptance

- **Tested in disposable PostgreSQL (`core/recall_miss_test.go`)**:
  - Budget-omitted (`OPTIONAL_BUDGET`) and unexpanded (`agent_tools`, `ambient`) high-overlap notes (`>= 3` lexical matches) are logged in `cairn.recall_miss_candidate`.
  - Delivered and expanded notes are excluded from `cairn.recall_miss_candidate`.
  - Repeated retries with the same prompt digest do not duplicate candidates (`3 -> 3`).
  - `Store.Docket` surfaces `RECALL_MISS_CANDIDATE` items and `Store.RecallMissReport` computes the 7-day candidate and precision summary.
- **Boundary**:
  - Recall-miss candidates are review proposals in the demand docket, not confirmed misses or automatic authority promotions.
