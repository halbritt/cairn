# Verification: Roadmap Slices CAIRN-11..CAIRN-21 (2026-10-19)

This verification note records the bounded core slices implemented and tested against disposable PostgreSQL for `CAIRN-11` through `CAIRN-21`, separating implemented slice behavior from full roadmap acceptance.

## Implemented Slices and Tested Claims

1. **CAIRN-11 — Evidence lifecycle (`Store.AuditEvidenceFreshness`)**:
   - Implemented in `core/roadmap_lifecycle_slices.go` and verified in `TestRoadmapCAIRN11AuditEvidenceFreshness`.
   - Classifies captured evidence in `cairn.evidence` into `inline` (`<= 65536` bytes) vs `managed_threshold` (`> 65536` bytes), checks `cairn.evidence` / `cairn.evidence_check` freshness against `maxAge` and SHA-256 integrity, and enumerates active dependent `B`/`C` records in `cairn.evidence_ref` requiring refresh or demotion.
2. **CAIRN-12 — Aggregate task context budgeting (`Store.EvaluateTaskContextBudget`)**:
   - Implemented in `core/roadmap_lifecycle_slices.go` and verified in `TestRoadmapCAIRN12EvaluateTaskContextBudget`.
   - Aggregates `octet_length(semantic_body)` across `cairn.retrieval_receipt` and `expanded` observations in `cairn.usage_observation` for `(repo, task_id)`, tracking compaction/resume receipts and enforcing `InterlockRefused` when total charged bytes exceed `ceilingBytes`.
3. **CAIRN-13 — Governance conflict outcomes, refusal completeness, and C waivers (`Store.InspectGovernanceConflictAndWaiverStatus`)**:
   - Implemented in `core/roadmap_lifecycle_slices.go` and verified in `TestRoadmapCAIRN13InspectGovernanceConflictAndWaiverStatus`.
   - Reports open vs resolved `cairn.conflict_group` counts, detects `ActedUnderOpenConflict` exposures in `cairn.record_use`, summarizes `trace_complete` coverage in `cairn.refusal`, and lists active `cairn.scope_authorization` waivers.
4. **CAIRN-14 — Import quarantine and inherited restrictions (`Store.ValidateImportQuarantine`)**:
   - Implemented in `core/roadmap_lifecycle_slices.go` and verified in `TestRoadmapCAIRN14ValidateImportQuarantine`.
   - Verifies that `derived_from`, `specializes`, and `contradicts` relations in `cairn.record_relation` (and linked `cairn.evidence_ref` rows) do not launder `local` sensitivity to `shareable` or broaden scope/applicability pins.
5. **CAIRN-15 — 90-day use retention floor and live-decision protection (`Store.EvaluateUseRetentionFloor`)**:
   - Implemented in `core/roadmap_lifecycle_slices.go` and verified in `TestRoadmapCAIRN15EvaluateUseRetentionFloor`.
   - Evaluates `cairn.record_use` against the 90-day retention floor while protecting rows linked to active `B`/`C` records, open conflicts, or pending/deferred `cairn.lesson_proposal` items.
6. **CAIRN-16 — Out-of-procedure restore detection (`Store.DetectOutOfProcedureRestore`)**:
   - Implemented in `core/roadmap_lifecycle_slices.go` and verified in `TestRoadmapCAIRN16DetectOutOfProcedureRestore`.
   - Compares `cairn.retrieval_generation`, `cairn.restore_admission`, `cairn.restore_fence`, and `cairn.audit_checkpoint` against external generation and checkpoint pins.
7. **CAIRN-18 (X1) — H1 handle conformance profile (`Store.InspectH1HandleProfile`)**:
   - Implemented in `core/roadmap_extensions.go` and verified in `TestRoadmapCAIRN18InspectH1HandleProfile`.
8. **CAIRN-19 (X3) — Offline session consolidation preview (`Store.PreviewOfflineSessionConsolidation`)**:
   - Implemented in `core/roadmap_extensions.go` and verified in `TestRoadmapCAIRN19PreviewOfflineSessionConsolidation`. Produces `review_required` proposals with `AutomaticAuthorityGain: false`.
9. **CAIRN-20 (X4) — Opt-in decay ranking comparator (`EvaluateDecayExperiment`)**:
   - Implemented in `core/roadmap_extensions.go` and verified in `TestRoadmapCAIRN20EvaluateDecayExperiment`. Refuses during active incidents and never mutates production ranking.
10. **CAIRN-21 (X5) — Selective H3 runtime mediation (`Store.CheckH3RuntimeMediation`)**:
    - Implemented in `core/roadmap_extensions.go` and verified in `TestRoadmapCAIRN21CheckH3RuntimeMediation`. Enforces `shareable` destination gates, active `B`/`C` authority for `consequential` mediation, and zero open conflicts.
