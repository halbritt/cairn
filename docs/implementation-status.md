# Capabilities and limits

Cairn is an untagged alpha with a PostgreSQL store, an operator CLI, an
authenticated agent API, and native memory integrations. This page describes the
source tree's supported surfaces. An installation's behavior also depends on its
binary, configuration, and running host integrations; use
[build diagnostics](build-identity.md) when those disagree.

## Memory

Ordinary memory supports capture, versioned correction, lexical search, browsing,
file and symbol associations, and optional local semantic discovery. Search can
return an index with handles for later reads, keeping full bodies out of the
initial response. Pulls recheck eligibility and enforce their receipt's limits.

Start with [capture](guides/memory-capture.md) and [search](guides/memory-search.md).
The [index and pull contract](index-and-pull.md) covers budgets, expired handles,
and partial reads. [Deletion](deletion.md) and [ordinary record deletion](ordinary-delete.md)
cover different removal paths and their retained dependencies.

A saved note is a fallible claim. Retrieval and producer attribution do not
verify its truth. Evidence-backed records and authorized instructions have
additional qualification, authority, and currentness checks; see
[architecture](developer/architecture.md), [scope authorization](scope-authorization.md),
and [governed policy](governed-policy.md).

## Agent integrations

[MCP](mcp/overview.md) exposes ordinary memory tools to compatible clients.
Codex, Claude Code, OpenCode, Agy, and Hermes have documented integration routes;
follow the guide for your client, since configuration and lifecycle support vary.

[Lifecycle integrations](lifecycle-bundles.md) support selective recall, capture,
and continuity around session boundaries. An installed bundle must be upgraded
explicitly. A client that can call memory tools does not thereby have automatic
recall, inbox delivery, or every lifecycle hook enabled.

## Coordination

The [event system](agent-event-fabric.md) stores requests, notices, and responses
against exact source versions. [Session discovery](agent-sessions.md),
[worker pools](worker-pools.md), [scheduling](event-scheduling.md), and
[response groups](response-groups.md) support routing and tracking work.

[Native inbox delivery](native-inbox.md) and [worker wakeups](agent-wakeups.md)
require host configuration. An online session does not prove a message was
handled; acknowledgment and task acceptance are separate. Use
[delivery health](delivery-health.md) and [recovery](event-recovery.md) to inspect
stalled or failed work. Cancellation has host-specific gates described in
[request controls](request-controls.md); it is not universally available for
arbitrary running agent tasks.

The [work view](operator/work-coordination.md) projects an explicitly bound Plane
project and combines it with local annotations, exact artifact revisions and
selected coordination associations. It does not update Plane's issue state or
infer task acceptance from presence or message handling. Artifact registration,
link history and reverse impact remain subject to current scope and source
visibility.

Durable waits support the request-response and response-group-closure predicates,
with a terminal notification outbox. Quiet notices can wait for a verified owner
input without waking a conversation. Quiet admission currently requires the
[carried Codex native package](native-owner-origin.md); other harnesses and
unpatched Codex remain unsupported for this delivery mode. Neither a source
checkout nor a successful probe installs that package in a running session.

Model suggestions use a separately pinned [native subscription
adapter](native-work-advice.md) and explicit project input policy. One bounded
request produces a retained proposal for local progress, blockers or exact
artifact links. An operator reviews the captured input and selects actions;
generation itself changes no work item. Application receipts support guarded
Undo without overwriting later edits. Generation and application have separate
controls, and retained requests remain inspectable after admission is disabled.
See the [operator workflow](operator/work-coordination.md#review-model-suggestions).

Versioned feature controls distinguish admission, retained maintenance and
retirement. Use the [operator runbook](operator/work-coordination.md) for the
combined migration boundary and settlement requirements before changing binary
support. A physical restore also requires the normal recovery witness and
admission procedure. The [five-feature acceptance record](verification/work-coordination-acceptance-2026-11-05.md)
identifies the tested revisions, local checks and remaining deployment limits.

## Operations and developer interfaces

The store provides forward migrations, backup checkpoints, explicit restore
admission, and recovery inspection/reapplication. Follow the
[maintenance guide](operator/maintenance.md) before changing an existing store.
[Remote deployments](multi-machine.md) connect clients through an enrolled relay
with a narrower operation set than local operator access.

Restore checks depend on retained expectations; they cannot recreate missing
external evidence or unknown lost state.

The [API contract](api-contract.md) describes the checked machine-readable
interface. [CLI](reference/cli.md), [API](reference/api-rest.md), and
[MCP](reference/mcp-tools.md) references identify their respective access paths.

Several [lifecycle and extension helpers](reference/lifecycle-slices.md) compute
reports or evaluate supplied inputs. A helper that reports a budget refusal,
restore mismatch, or runtime-mediation decision is not proof that every launch,
restore, or tool call is intercepted. Read its caller obligations before using it
as an enforcement boundary.

## Evaluation boundaries

Local tests establish specific contracts, including transactional behavior and
refusal paths. Dated native probes establish behavior for their tested client
versions and configurations. Neither demonstrates general improvements in agent
quality, token cost, or task completion.

[Use and outcome records](use-outcome-loop.md) support evaluation of selected
workflows, including qualitative, delayed, and cumulative benefits. Preserve the
observations, alternative explanations, and unknown costs behind each conclusion.
The [roadmap](roadmap.md) lists remaining acceptance work.

## Implementation history

The [implementation and deployment log](history/implementation-log.md) preserves
the earlier chronological status page. It records historical builds, tests,
failures, corrections, and installation observations. The
[changelog](../CHANGELOG.md) summarizes development changes; neither is a live
inventory of your installation.
