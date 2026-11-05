# Cairn Lifecycle & Extension Slices Reference

This document catalogs the architectural roadmap slices CAIRN-01 through CAIRN-21, detailing their functional scope, core methods, and acceptance criteria.

---

## 1. Foundational Slices (CAIRN-01 through CAIRN-10)

The foundational slices establish Cairn's transactional core, observation channels, receipts, indexing, and recovery primitives:

| Slice | Name | Focus Area | Key Invariants |
|-------|------|------------|----------------|
| **CAIRN-01** | Core Relational Store | PostgreSQL storage engine | Schema migrations 001..020, transactional ACID semantics |
| **CAIRN-02** | Observation Channels | Channel-authenticated evidence | Provenance separation: self-testimony vs observed channel |
| **CAIRN-03** | Immutable Receipts | Audit trail for searches | Fixed candidate snapshots, token allowances, caller binding |
| **CAIRN-04** | Expiring Handles | Bounded note expansion | Time-limited, destination-bound handles preventing context bloat |
| **CAIRN-05** | CAS Revisions | Optimistic concurrency control | Expected version checks on all record mutations |
| **CAIRN-06** | Lexical Ranking | BM25 ranking engine | Term frequency scoring, exact quoted substring matching |
| **CAIRN-07** | Destination Isolation | Local vs Hosted sensitivity | Automatic redaction of local notes from hosted model calls |
| **CAIRN-08** | Evidence Lifecycle | Cryptographic verification | SHA-256 digest checks on inline and binary evidence |
| **CAIRN-09** | Restore Admission | Restore fencing (`local-restore/1`) | Invalidation of stale handles and pause of active transactions |
| **CAIRN-10** | Retraction & Supersession | Orderly deprecation | Explicit superseding pointers preserving historical audit |

---

## 2. Completed Lifecycle & Extension Slices (CAIRN-11 through CAIRN-21)

Implemented in `core/roadmap_lifecycle_slices.go` and `core/roadmap_extensions.go`, these ten slices provide enterprise governance, retention, and integration:

### CAIRN-11: L4 Evidence Freshness
- **Core Method**: `AuditEvidenceFreshness(ctx, repo, maxAge)`
- **Functionality**: Audits inline vs managed storage tiers (threshold: 64 KiB), tracks check generation age, and identifies invalidation candidates when source files change.

### CAIRN-12: E2 Context Budgeting
- **Core Method**: `EvaluateTaskContextBudget(ctx, repo, taskID, runID, maxTokens)`
- **Functionality**: Enforces aggregate task context limits across receipts and usage signals, with pre-launch interlock refusal.

### CAIRN-13: L2/L3 Conflict Governance
- **Core Method**: `InspectGovernanceConflictAndWaiverStatus(ctx, repo)`
- **Functionality**: Inspects open and resolved conflicts, tracks scope authorization waivers, and surfaces policy refusal traces.

### CAIRN-14: L7 Import Quarantine
- **Core Method**: `ValidateImportQuarantine(ctx, repo, recordID)`
- **Functionality**: Prevents sensitivity laundering across record relations (specializing, contradicting, derived) and destination constraints.

### CAIRN-15: L6 Retention Floor
- **Core Method**: `EvaluateUseRetentionFloor(ctx, repo, minDays)`
- **Functionality**: Enforces the 90-day retention floor protecting active Class B claims, Class C instructions, unresolved conflicts, and proposals.

### CAIRN-16: L9 Restore Integrity
- **Core Method**: `DetectOutOfProcedureRestore(ctx, repo)`
- **Functionality**: Detects out-of-procedure database restore anomalies via generation mismatch and checkpoint verification.

### CAIRN-18: X1 H1 Conformance
- **Core Method**: `InspectH1HandleProfile(ctx, repo, handleID)`
- **Functionality**: Validates expiring index handles, byte credits, and expansion reader binding for H1 driver conformance.

### CAIRN-19: X3 Session Consolidation
- **Core Method**: `PreviewOfflineSessionConsolidation(ctx, repo)`
- **Functionality**: Previews offline consolidation proposals across stopped sessions for review without automatic authority gain.

### CAIRN-20: X4 Decay Experimentation
- **Core Method**: `EvaluateDecayExperiment(ctx, repo, halfLifeDays)`
- **Functionality**: Opt-in half-life ranking decay comparator modeling rank shifts with active incident lockout.

### CAIRN-21: X5 Runtime Mediation
- **Core Method**: `CheckH3RuntimeMediation(ctx, repo, destination)`
- **Functionality**: Selective runtime mediation for high-stakes destinations and purposes across candidate records.

---

## 3. Token Efficiency & Coordination Slices (CAIRN-213 through CAIRN-227)

Developed in October 2026, these slices optimize token efficiency and event routing:

- **CAIRN-215**: Shadow capture observation metrics.
- **CAIRN-216**: Compact inbox bindings (`compact_inbox_version: 1`).
- **CAIRN-219**: Compact recall views (`recall_view: compact/1`).
- **CAIRN-220**: Deferred routine notices (`notice-defer-seconds`, `notice-flush`).
- **CAIRN-221**: Bounded notice batches (up to 3 waiting notices per turn).
- **CAIRN-224**: Codex compact lifecycle memory (`context_lifecycle: compact/1`).
- **CAIRN-225**: Compact MCP tool profiles (`--tool-profile compact/1|compact/2`).
- **CAIRN-227**: One-account canary preflight gate.

---

## Related Documentation

- [Roadmap](../roadmap.md): Complete project roadmap and milestones.
- [Architecture Guide](../developer/architecture.md): Core transactional design.
- [Testing Guide](../developer/testing.md): Lifecycle test suites.
