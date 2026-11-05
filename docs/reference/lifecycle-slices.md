# Lifecycle capability map

Use this map to find implementation and validation for lifecycle features.
A store inspection helper can report a condition without enforcing it in every
client. Integrators must connect a decision to the action it is intended to gate.

## Store inspection and evaluation helpers

The following methods live in
[roadmap_lifecycle_slices.go](../../core/roadmap_lifecycle_slices.go).

| Method | Inputs after context | Result and boundary |
| --- | --- | --- |
| `AuditEvidenceFreshness` | repository, maximum age | Reports retained evidence freshness and affected claims; does not watch external files |
| `EvaluateTaskContextBudget` | repository, task ID, byte ceiling | Totals recorded receipt and expansion bytes and reports `InterlockRefused`; does not itself block a launch or measure an entire model conversation |
| `InspectGovernanceConflictAndWaiverStatus` | repository | Reports conflicts, authority waivers, and refusal trace coverage |
| `ValidateImportQuarantine` | repository, record ID | Assesses the record and related sensitivity restrictions |
| `EvaluateUseRetentionFloor` | repository, current time | Reports retention eligibility and protected references; does not delete records |
| `DetectOutOfProcedureRestore` | expected generation, expected latest checkpoint ID | Compares retained state with supplied expectations; does not discover an independent trusted recovery catalog |

These are Go store APIs. Their existence does not imply that every method has an
ordinary-agent CLI or MCP operation. Read the method's authorization checks and
its callers before integrating it.

## Extension helpers

[roadmap_extensions.go](../../core/roadmap_extensions.go) contains:

| Helper | Use |
| --- | --- |
| `InspectH1HandleProfile(ctx, receiptID)` | Inspect a receipt's handle and expansion profile |
| `PreviewOfflineSessionConsolidation(ctx, repo)` | Propose consolidation candidates for review without applying changes |
| `EvaluateDecayExperiment(candidates, now, halfLifeDays, incidentActive)` | Compare ranking with an explicit decay experiment; it does not enable decay in ordinary retrieval |
| `CheckH3RuntimeMediation(ctx, repo, destination, purpose, recordIDs)` | Return an eligibility decision for selected records; callers must enforce that decision at the consequential action boundary |

## User-facing workflows

For actual operation and integration contracts, use:

- [Evidence refresh](../evidence-refresh.md) and [retention](../candidate-retention.md).
- [Restore admission](../restore-admission.md) and [restore fencing](../restore-fencing.md).
- [Index and pull](../index-and-pull.md) and [managed context](../managed-context.md).
- [Lifecycle bundles](../lifecycle-bundles.md), [compact inbox](../compact-inbox.md),
  and [native inbox](../native-inbox.md).

The [roadmap](../roadmap.md) and dated
[verification reports](../verification/README.md) retain development and
acceptance evidence. They should not be read as a promise that every optional
integration is enabled in a deployment.
