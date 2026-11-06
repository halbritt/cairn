# Roadmap

Cairn's direction is persistent, inspectable memory and dependable coordination
for agents working across sessions and tools. The core workflows are available;
remaining work concerns their integration, operating limits, and demonstrated
usefulness. See [capabilities and limits](implementation-status.md) for the
current surface and [contributing](developer/contributing.md) to work on it.

## Memory that earns its context cost

Make relevant knowledge available with little repeated setup, while keeping
retrieval bounded and remembered advice correctable. Work includes better
recall for large notes, project applicability, and accounting for memory's cost
across a complete workflow. Source-level optimizations require evaluation before
being enabled broadly; the [token-efficiency plan](plans/token-efficiency-2026-10-23.md)
records its opt-in and acceptance gates.

## Reliable session continuity and coordination

Strengthen recall, selected capture, and handoffs around compaction and session
restart. Coordination needs host-specific evidence for admission, draft
preservation, cancellation, process cleanup, and recovery under provider failure.
A database transition or successful fixture does not close the corresponding
native-host acceptance requirement. The [coordination plan](plans/agent-coordination-v1.md)
and [request-control guide](request-controls.md) retain those contracts.

The source now supports [bounded event admission](event-admission.md), separate
reply capacity and persistent producer rates. Remaining operating work includes
choosing limits for real workloads, observing pressure before enforcement, and
verifying upgraded writers and native integrations on each installation. The
[rollout guide](operator/event-admission-rollout.md) describes those gates.
Synthetic overload and restore fixtures do not establish a production workload's
safe limits or demonstrate reduced model cost.

## Lifecycle and recovery

Keep evidence current, make retention and deletion effects inspectable, and
verify restores against independently retained expectations. Cover the callers
that must act on inspection results: [library helpers](reference/lifecycle-slices.md)
for budgeting, quarantine, restore detection, and runtime mediation are bounded
parts of these workflows. Their existence does not establish end-to-end
integration or deployment.

## Assess task value without narrowing it to an evaluator

Evaluate whether memory helps agents finish useful work and reduces repeated
mistakes. Evidence can include controlled comparisons, reviewed task outcomes,
qualitative improvements, or benefits accumulated across sessions. Record the
source, reasoning, alternative explanations, and unknown costs.

Retrieval, citation, or successful capture alone does not establish benefit.
Task-level measurements should include the cost of retrieval, maintenance,
recovery, and auxiliary model work. The [use/outcome guide](use-outcome-loop.md)
and [task-evaluation plan](plans/task-evaluation.md) describe the available
records and evaluation approach.

## Design constraints

The operational store remains PostgreSQL. Cairn targets a trusted, single-user
environment; project names and session metadata do not create new authentication
boundaries. Preserve store-owned attribution and explicit authority contracts.
Keep ordinary memory inexpensive to correct and remove, with stronger retention
and audit rules where evidence or authority requires them.

Automatic promotion, universal immutable history, and broad runtime enforcement
are not assumptions a client may make. Future adapters and ranking experiments
must preserve the current eligibility, privacy, and refusal contracts. Use
local disposable validation for changes; do not depend on GitHub CI.

## Detailed plans and history

[Plans](plans/README.md) retain individual requirements, dependencies, and
acceptance criteria. The [roadmap development record](history/roadmap-log.md)
preserves the earlier requirement IDs and chronological discussions. Its status
claims must be read with the dated evidence and current source; it is not an
installation guide or a blanket completion statement.

Keep this page about direction and remaining acceptance work. Document current
behavior in feature guides, user-visible changes in the [changelog](../CHANGELOG.md),
and specific test or deployment observations in [verification records](verification/README.md).
