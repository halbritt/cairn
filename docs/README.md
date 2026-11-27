# Cairn documentation

Cairn preserves knowledge across agent sessions and coordinates work between
agents. Start with the [project overview](../README.md) to understand its purpose,
or the [quickstart](quickstart.md) to try it with a local PostgreSQL store.

## Use Cairn

- [Save and maintain memory](guides/memory-capture.md): choose useful knowledge,
  record it, and correct it as the project changes.
- [Find memory](guides/memory-search.md): search, inspect full sources, and handle
  limited context or expired results.
- [Work across sessions](guides/agent-workflows.md): recall decisions and preserve
  a handoff for unfinished work.
- [Coordinate agents](guides/session-coordination.md): discover sessions and
  exchange requests and results.
- [Coordinate a bounded group of workers](guides/scoped-coordination.md): send one
  review to exact recipients, wait for replies, link selected results and tear down.
- [Operate work coordination](operator/work-coordination.md): inspect Plane work,
  link exact deliverables, wait for responses and manage quiet notices.
- [See a swarm live](swarm-view.md): inspect campaign members, messages, waits
  and context state.
- [Integrate a Git candidate](git-integration.md): land checked changes and
  compare optional development evidence against the final candidate.
- [Inspect a development checkpoint](development-checkpoints.md): install the
  command, pack selected references and inspect their current availability.

## Connect your client

Start with [MCP setup](mcp/overview.md), then choose
[Codex](mcp/codex.md), [Claude Code](mcp/claude-code.md),
[OpenCode](mcp/opencode.md), or [Agy and Hermes](mcp/agy-hermes.md).
The feature references below cover automatic recall, capture, and inbox delivery
where a client supports them.

## Operate a service

Read [deployment](operator/deployment.md), [configuration](operator/configuration.md),
and [daemon setup](operator/daemon-deployment.md) to provision a store and client
access. Use [maintenance](operator/maintenance.md) for upgrades and recovery,
[lifecycle staging](operator/lifecycle-staging.md) to stage immutable lifecycle
generations and hooks, and the
[event admission rollout](operator/event-admission-rollout.md) to upgrade, observe
and enable bounded event admission.

## Develop Cairn

[Architecture](developer/architecture.md) explains the components and trust model.
[Contributing](developer/contributing.md), [testing](developer/testing.md), and
[documentation](developer/documentation.md) describe how to make and validate a
change. The [glossary](../GLOSSARY.md) explains the design vocabulary.

Lifecycle staging and native fixture references:
[retained installed lifecycle packages](developer/retained-lifecycle-package.md),
[checked Agy layer crash recovery](developer/agy-layer-recovery.md),
[native Agy hook fixture](developer/native-agy-hook-fixture.md),
[native Claude hook fixture](developer/native-claude-hook-fixture.md), and
[native Codex hook fixture](developer/native-codex-hook-fixture.md).

Designs include the [swarm view](plans/swarm-view-design.md) and the
[hybrid runtime consolidation](plans/hybrid-runtime-consolidation.md) with its
[organization and implementation plan](plans/hybrid-runtime-implementation.md).
See [plans](plans/README.md).

## Reference

Use the [CLI reference](reference/cli.md), [HTTP API reference](reference/api-rest.md),
and [MCP tool reference](reference/mcp-tools.md) for interface details. The
[lifecycle capability map](reference/lifecycle-slices.md) describes the shared Go
lifecycle mechanisms and checked memory caller boundaries.

The feature references below cover behavior, configuration, and failure paths.

### Memory retrieval

- [Index, body and evidence expansion](index-and-pull.md)
- [MCP memory tools](mcp.md)
- [Prefer exact text in memory search](quoted-search.md)
- [File and symbol associations](entity-search.md)
- [Find notes by meaning](semantic-discovery.md)
- [Persistent semantic retrieval](persistent-semantic-retrieval.md)
- [Inspect retained record history](record-history.md)
- [Declare context for a native memory search](search-context.md)
- [Set the room for one native search](search-room.md)
- [Allocate search room for a whole source](inspection-allocation.md)
- [Source openings beside note spans](note-span-openings.md)
- [Preview boundaries and missing qualifications](preview-qualifier-limit.md)
- [Retrieve competing advisory positions](advisory-conflicts.md)
- [Inspect a conflict](conflict-inspection.md)
- [Recent file hints in OpenCode](recent-file-hints.md)
- [Find a reviewed lesson by failure signature](failure-signature-search.md)
- [Offline Rust semantic package](semantic-package.md)
- [Rust semantic workers](semantic-worker-comparison.md)

### Memory lifecycle and authority

- [Authenticated local access](local-api.md)
- [Governed repository policy](governed-policy.md)
- [Authorize wider applicability](scope-authorization.md)
- [Instruction category limits](instruction-limits.md)
- [Evidence refresh and inspection](evidence-refresh.md)
- [Precise supporting citations](evidence-citations.md)
- [Inspect evidence impact](evidence-impact.md)
- [Replace an obsolete record](supersession.md)
- [Versioned relations and B demotion](relations-and-demotion.md)
- [Delete an ordinary note](ordinary-delete.md)
- [Forgetting retained record bodies](deletion.md)
- [Managed context files](managed-context.md)
- [Candidate detail retention](candidate-retention.md)

### Agent integrations and execution

- [Share memory across projects and agents](shared-memory.md)
- [Agy MCP configuration](agy.md)
- [Ambient memory in Claude Code](claude-lifecycle.md)
- [Ambient memory in Codex](codex-lifecycle.md)
- [Ambient memory in OpenCode](opencode-lifecycle.md)
- [Hermes shared memory](hermes-lifecycle.md)
- [Native OpenCode tools](opencode-tools.md)
- [Installed lifecycle bundles](lifecycle-bundles.md)
- [F-02 inbox recall caller and post-lock report scheduling](f02-inbox-caller.md)
- [Fresh Claude settings owner](native-claude-owner.md)
- [Native owner input for quiet notices](native-owner-origin.md)
- [Native work advice adapter](native-work-advice.md)
- [Start a harness with compact memory](compact-start.md)
- [Run a harness with an observed compact index](observed-index.md)
- [Run a harness through an authenticated observer](authenticated-runner.md)
- [Execute an exact retained package](retained-execution.md)
- [Currentness and historical recompilation](currentness-and-replay.md)
- [Fresh memory at launch](launch-freshness.md)
- [Inspect an owned run receipt](run-status.md)
- [Session-targeted recall dispatch](recall-dispatch.md)

### Coordination and delivery

- [Agent event fabric v1](agent-event-fabric.md)
- [Agent sessions](agent-sessions.md)
- [Native session inbox delivery](native-inbox.md)
- [Recall on an admitted inbox request](inbox-recall.md)
- [Compact native notices and responses](compact-inbox.md)
- [Watching inbox arrivals](inbox-watch.md)
- [Automated agent wakeups](agent-wakeups.md)
- [Fresh-work pools](worker-pools.md)
- [One-shot event scheduling](event-scheduling.md)
- [Request expiry, deadlines and cancellation](request-controls.md)
- [Response groups](response-groups.md)
- [Saved handoffs and request status](handoff-request-status.md)
- [Stopped-session backlog and publisher notification](stopped-session-backlog.md)
- [Authorized successor event continuity](successor-event-continuity.md)
- [Delivery health observations](delivery-health.md)
- [Event pressure, admission limits and rates](event-admission.md)
- [Closure reservations for replies and wait notices](closure-reservations.md)
- [Operator review and explicit reissue](event-recovery.md)
- [Workstream status board](workstream-status-board.md)
- [Worker provider failures](provider-failures.md)
- [Multiple machines](multi-machine.md)

### Backup and recovery

- [Audit checkpoints and the backup catalog](audit-checkpoints.md)
- [Streamed recovery bundles](recovery-bundles.md)
- [Inspecting an older restore](recovery-inspection.md)
- [Reapplying withdrawals after a restore](recovery-reapplication.md)
- [Restore sessions and explicit admission](restore-admission.md)
- [Fence restored delivery capabilities](restore-fencing.md)

### Observations and reporting

- [Observed client implementations](client-observations.md)
- [Identify the installed binaries](build-identity.md)
- [Inspect memory use and task outcomes](use-outcome-loop.md)
- [Evidence-attached review proposals](demand-review.md)
- [Fingerprint selected task files](run-artifact-evidence.md)
- [Link a wrapped execution to its host attempt](host-attempt-link.md)
- [Report coordinator load and bookkeeping share](coordinator-load-report.md)
- [Report model work in a coordination workflow](workflow-model-report.md)
- [Report model work during recovery](recovery-model-report.md)

### API and test contracts

- [Structured command examples](commands.md)
- [Durable policy refusals](refusals.md)
- [Agent API contract](api-contract.md)
- [API protocol compatibility](api-compatibility.md)
- [Embed a read-only MCP retrieval server](mcp-retrieval-backend.md)
- [Prepare search results for an attempt-scoped MCP backend](mcp-search-publication.md)
- [Disposable fixture hosts](disposable-fixtures.md)

## Status, direction and decisions

[Capabilities and limits](implementation-status.md) describes the current source
surface. The [roadmap](roadmap.md) describes direction and open work; the
[changelog](../CHANGELOG.md) summarizes development changes.

[Decision records](decisions/README.md) explain why a direction was chosen.
[Plans](plans/README.md) hold the designs for open work.
[Design sources](sources/README.md) and the [initial design review](design-review.md)
explain the design's origins.

The repository does not keep records of past runs, reviews or deployments; see
[decision 0023](decisions/0023-repository-holds-current-contracts.md). The few
[dated records](verification/README.md) that remain belong to open work.
