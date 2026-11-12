# Lifecycle capability map

Use this map to find implementation and validation for lifecycle features.
A store inspection helper can report a condition without enforcing it in every
client. Integrators must connect a decision to the action it is intended to gate.

## Shared Go lifecycle mechanisms

| Source | Consumer and boundary |
| --- | --- |
| [`statefile.File`](../../internal/statefile/file.go) | The CLI's `LockedStateFile` wrapper retains send-journal, delivery-alert and delivery-attention policy. Write/sync errors propagate; a directory-sync failure after rename reports an error while retaining published state. |
| [`procpeer.ProcessRefOf` and `Live`](../../internal/procpeer/process.go) | The retained Claude bridge measures PID/start/boot and preserves its Go measurement-equality policy. Python callers separately exclude Z/X states. |
| [`procpeer.ReadLine` and `WriteLine`](../../internal/procpeer/frame.go) | The bridge retains an 8192-byte request payload excluding newline and two-second socket deadlines. MCP emission keeps its separate deadline. |
| [`procpeer.ConnectClient` and client I/O](../../internal/procpeer/client.go) | Go wake clients validate credentials and expected identity before payload. Callers select process-owned or same-user policy, timing, framing and cumulative read budgets. The connector returns the actual peer identity. |
| [`binding.ValidateBinary`](../../internal/lifecycle/binding/binary_linux.go) | Checked lifecycle entries validate real-UID ownership, safe staging, stable descriptor metadata and matching pinned/running digest and inode before handler execution. Validation does not activate an installed binding. |
| [`memoryhost`](../../internal/lifecycle/memoryhost/checked.go) | The private `memory` entry verifies the receipt, source closure, selected profiles, executable and retained launcher before dispatch. Unbound Codex/Claude/OpenCode/Hermes ambient recall admits either boolean `semantic_fallback`; Claude and Hermes admit their actual post-tool success/failure events through the existing tool handler. |
| [`memoryhook` ordinary provider recall](../../internal/lifecycle/memoryhook/ordinary_provider.go) | Ambient fallback performs lexical/semantic discovery, optional preview admission, checked current whole/span reads and one body applicability selection. Required context remains whole, aliases share actual receipt allowances, and only delivered whole optional bodies become seen. |

See [primitive test boundaries](../developer/testing.md#shared-go-lifecycle-primitives).
Installed executor selection and runtime acceptance are separate from these
shared source mechanisms.

### Checked memory callers and selectors

The checked `memory` caller retains its eight receipt-selection arguments and
JSON input/output contract. Ordinary recall keeps the original thirteen-second
invocation deadline, including stdin and preflight; its eleven-second recall
budget and each selector deadline are capped by that invocation deadline.
The installed Hermes recall callback is `UserPromptSubmit`; engine support for
`SessionStart` does not imply that Hermes installs such a callback.
Unbound Codex `agent_tools` recall uses the existing native-turn budget and
checkpoint implementation. Its explicit ambient override uses the ambient
recall path. Both enter through the checked selection; the bare ordinary Go
entry continues to refuse Codex and Hermes recall.

When ambient `semantic_fallback` is true, discovery reads at most four semantic
pages and 32 semantic previews, alongside ten lexical previews. A preview
selector admits at most eight candidates when their count or receipt allowances
require selection. Checked reads enforce current identity, version and body
hash, with at most four credits per actual receipt shared by channel aliases.
The selector's combined preview/body input is bounded to 24,000 bytes. A fitting
excerpt of an already paid whole body reuses that read. Different partial
passages remain eligible, and partial delivery never marks a whole body seen.

Preview and body selection reuse the capture process helper: a private empty
working directory, disabled hooks/settings/tools/MCP, bounded stdout and stderr,
the existing authentication environment, and direct-child cleanup. Measured
calls, valid requested/reported model names, bounded token counts and reported
cost enter the existing recall meter. Invalid verdicts, provider errors and
timeouts abstain from optional injection while preserving required context;
Source's uncaught input-type errors retain their failure and observation shape.
Missing capture provider configuration retains Source's `KeyError`, and an
explicit null provider retains its `TypeError`, without committing a capture.

Activated bound memory profiles still require `agent_tools`. An enabled bound
Claude ambient profile is therefore a Source activation refusal; disabled
bindings retain Source's fallback behavior. This implementation does not introduce
a new activation mode or a bound Hermes caller. Receipt-checked process fixtures use
synthetic API and selector processes. They do not establish installed rollout,
native callback qualification, a real provider invocation or production acceptance.

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
- [Event admission](../event-admission.md), [closure reservations](../closure-reservations.md),
  and the [admission rollout procedure](../operator/event-admission-rollout.md).
- [Index and pull](../index-and-pull.md) and [managed context](../managed-context.md).
- [Lifecycle bundles](../lifecycle-bundles.md), [compact inbox](../compact-inbox.md),
  and [native inbox](../native-inbox.md).

The [roadmap](../roadmap.md) and dated
[verification reports](../verification/README.md) retain development and
acceptance evidence. They should not be read as a promise that every optional
integration is enabled in a deployment.
