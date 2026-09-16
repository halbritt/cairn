# Agent coordination v1

Status: implementation plan, 2026-09-15. The owner requested planning for the
deferred work and corrected the initial account-based interpretation of identity.
The existing [event fabric v1](../agent-event-fabric.md) is a messaging contract;
its title does not mean that the coordination work below is implemented.

Implementation progress: [session registry contract](../agent-sessions.md) covers
identity, presence and explicit directory reads. Native adapters, resolution,
existing-session delivery and remaining operational milestones are still pending.
Deployment evidence is recorded separately from this plan.

## Required user behavior

“Send a message to the Codex agent working on Rhumb” should find the live agent
from its current context and send to its durable address. One eligible match is
enough. Multiple matches must return useful distinguishing context. No match
must report that outcome, without choosing a stale agent or silently starting a
replacement. A model may translate the sentence into selectors; the directory
and the publication transaction decide which identity those selectors denote.

There are two Codex accounts and two Claude accounts on this host. Accounts are
launch resources. Agents must remain interchangeable without renaming them when
their account, model, harness or project changes. Adding another account must
not require a new identity scheme.

## Terms and identity

| Term | Meaning and lifetime |
| --- | --- |
| Agent | A continuing conversation/session with a server-assigned UUID and a short display ordinal such as `agent-42`. Its inbox follows that identity. |
| Execution | One process incarnation or wake attempt, identified by another UUID. A resumed agent can have a new execution without losing its inbox history. |
| Worker slot | A configured place to launch fresh work, currently `worker-01` through `worker-07`. A slot is not proof that a live agent exists. |
| Account binding | Operator-owned authentication/configuration home and launch settings. Kept outside public addressing; credentials never enter directory records. |
| Presence | A bounded claim that an agent is currently reachable, with its execution generation, context revision and expiry. |
| Project | The actual work project, such as Rhumb. It is separate from the shared Cairn collection named `/home/halbritt/git/cairn`. |

An agent directory entry carries `agent_id`, display ordinal, registered profile,
harness, configured model, observed model when available, project ID/name,
workspace, native session reference, concise task summary, state, delivery mode,
execution ID, context revision, last-seen time and expiry. Account binding IDs are
operator-only. Models and capabilities reported by agents are labelled as such;
they confer neither authentication nor permission. Unknown metadata stays unknown.
Task descriptions contain selected context, never copied transcripts.

Native session IDs are namespaced by their launcher binding.
Identical provider IDs from two account homes must not merge agents. A normal
resume retains the agent UUID; a fork is a new agent. A fresh worker for unrelated
work is also a new agent. Ordinals are display aids, allocated without reuse within retained database
history. A restore can discard later ordinals; UUIDs are canonical references.

## Current baseline and migration

PostgreSQL owns events, fanout, leases, results and durable wake attempts.
Migration 036 and the systemd supervisor already handle request-triggered fresh
workers, renewal, process-tree cleanup and uncertain-execution failure. Ordinary
result completion and process receipts do not establish task success.

The account-coverage correction configures seven independent worker-slot inboxes.
It does not create a live session directory or route into interactive sessions.
Existing `agent/codex`, `agent/claude`, `agent/opencode`, `agent/agy` and
`agent/hermes` addresses and histories remain valid for manual consumption;
their former wake services are retired after checking for active attempts and
pending requests. They are not aliases that silently redirect old messages.

Preserve the current authenticated producer and observation-channel fields,
exact source versions, idempotent mutation responses, terminal delivery history,
and wake holds. Introduce identity/routing behavior in separate additive
migrations. Do not relabel historical publishers or retarget existing deliveries.

## Delivery sequence

Each milestone must ship with its contract, tests, upgrade procedure and observed
deployment result. The sequence below gives dependencies and acceptance criteria.
Milestones 1–4 deliver the Rhumb example; 5–7 close the operational follow-ups.

### 1. Session identity on the trusted host

Add PostgreSQL agent-instance records with immutable UUID/ordinal, collection,
existing profile, lifecycle and native-session binding. Registration is
idempotent. Cairn allocates the UUID and records the binding. Keep the current
trusted-host boundary: no new per-session credential, provider login or claim
that mutually hostile local agents are isolated. The owner explicitly selected
this on 2026-09-15 after questioning the earlier authentication proposal.

Requests use the existing local profile plus an explicit session UUID and
execution generation. Cairn verifies the registered association and fences an
obsolete execution before using its inbox. These are routing/consistency checks,
not a new security boundary. A caller-supplied UUID alone is not authentication;
the existing local profile remains the authenticated channel. Ordinary shared
memory continues using its existing profile and collection.

Do not append credentials or restart the API for every conversation. Registration,
resume, leave and retirement are ordinary PostgreSQL operations. Restore fences
old execution generations; explicit re-registration resumes the same session
identity. Namespace native session IDs by launcher binding so identical IDs in
the two account homes remain distinct.

Acceptance: two concurrent sessions using the same account get different
inboxes; retries do not duplicate registration; selecting one session cannot
accidentally consume the other's deliveries; an obsolete execution cannot renew
or complete after a resume; existing profile scope checks remain effective;
a resumed session and a fork follow the rules above; existing profile deliveries
and attribution remain unchanged.

### 2. Presence and context adapters

Add register/update/heartbeat/leave operations and paged directory reads.
Use database time for leases and a generation token to fence a replaced process.
Context updates compare revisions; heartbeats cannot overwrite newer project or
task metadata. Start with a 30-second heartbeat and 90-second expiry, as targets
to verify under load rather than measured guarantees. Offline means expired or
explicitly stopped, not “has no recent chat text.”

Integrate supported session/turn lifecycle surfaces in Codex, both Claude
configurations, Agy, OpenCode and Hermes CLI/gateway. A host watcher may maintain
process liveness, but only the session integration knows its task context.
Read current adapter interfaces before selecting hooks. Skill-driven registration
can bootstrap metadata; it cannot promise continuous presence. Unsupported
adapters must show that limitation rather than invent heartbeat coverage.

Acceptance: project/task changes appear without changing identity; expired
sessions disappear from live matches; a late old-process heartbeat cannot revive
an old generation; resume/fork, provider model changes, API outage and restore
are covered. No account secrets or private workspace contents appear in listings.

### 3. Resolve and publish by metadata

Add structured selectors for harness, project, workspace, model and state, with
exact matching and explicit project aliases. Task text may help present
candidates; fuzzy similarity must not silently select a recipient. Proposed CLI:
`cairn agents list` and `cairn agents resolve --harness codex --project rhumb`.
These commands are planned, not installed interfaces.

Resolve to a concrete agent UUID plus context revision and execution generation.
Revalidate freshness and the selected context in the publication transaction.
A lost-response retry returns the original publication; it must not resolve to
a different recipient. A context change before a new publication returns a
structured stale-resolution result. Direct UUID publication remains available
for intentional offline delivery, without pretending that the agent is online.

Acceptance: one Codex/Rhumb match routes correctly; two return both ordinals,
workspaces and task summaries; zero or stale matches produce no message; a
project switch between resolve and publish cannot deliver to the wrong context;
collection/privacy boundaries and pagination remain enforced.

### 4. Deliver to the addressed session or to a fresh-work pool

Separate `existing-session` delivery from explicitly requested `fresh-worker`
dispatch. Addressing a live agent must reach its session through supported
hooks/inbox checks, with one consumer owner and a defined busy-session policy.
Default to queuing at a supported turn boundary; interrupting active work needs
an explicit host capability and policy. Responses and notices must be visible
without recursively launching request workers.

Fresh-work dispatch selects an eligible slot by recorded capabilities and
operator policy, then registers the actual launched agent. Harness/account
configuration stays replaceable behind the slot. Preserve durable wake holds
and require explicit completion. Account quota exhaustion marks the binding
unavailable; any permitted pre-launch fallback is recorded. Never replay work
on another account after execution may have started.

Acceptance: the Rhumb message reaches the existing conversation, including when
it is busy; no competing fresh worker consumes it; a pool request starts once
under concurrent dispatch; both accounts per harness are independently usable
and independently report provider failures. Observe one real cross-agent task
from natural-language target selection through receipt, handling and result.

### 5. Wake context, watch and operator recovery

Expose a structured, versioned wake context containing agent/execution IDs,
delivery ID, source reference, lease and completion contract. Pass it through
an owner-only file or equivalent bounded environment reference, with no secret
values in public output. The current prompt completion command remains compatible.
Add resumable `watch`/long-poll over durable cursors; reconnects read PostgreSQL
state. Lost notifications cannot lose deliveries.

Add an operator view for pending/leased/held/failed deliveries, stale agents,
quota-blocked bindings, attempt receipts and failure reasons. Failed attempts
already remain durable; expose a dead-letter/review view over that history.
Explicit retry creates a traceable new request and warns about uncertain prior
effects. Add per-binding launch limits and backoff for confirmed launch failures.

Acceptance: API restart resumes watch without gaps, slow consumers stay bounded,
logs exclude credentials, and failure review distinguishes “never launched,”
“may have executed,” “handled” and “task accepted.”

### 6. Scheduled wakeups, deadlines and cancellation

Add durable one-shot `not_before` scheduling first. A scheduler transaction
creates at most one event per occurrence using a stable occurrence ID; repeated
ticks and restart retries preserve that identity. Extend to recurring schedules
only with an explicit timezone, DST behavior and misfire policy. Default to
skipping missed occurrences after the configured grace period, with an audit
record; never create an unbounded catch-up burst.

Distinguish admission TTL (do not start after expiry), task deadline (stop work
at the deadline), and operator cancellation. Expiry does not delete history.
For running work, cancellation goes through the supervisor and confirms cgroup
termination before releasing the hold. Admission TTL alone does not revoke an
already-running task. Cancellation racing with completion has one durable winner.

Acceptance: simultaneous schedulers, restart after an uncertain commit, missed
ticks, clock/timezone transitions, expiry while pending, cancellation during
launch and cancellation/completion races all run on disposable databases.

### 7. Policy, fairness and bounded coordination

Add configured publish/subscribe/instance-registration policy before supporting
new trust boundaries or remote hosts. Authenticated identity, collection scope
and sensitivity rules already apply; capability labels do not grant authority.
Add bounded priorities with aging only when queue contention is observed. Rate
limits must distinguish account limits from per-agent fairness and preserve
already-accepted durable work.

Add response-group records with explicit recipient snapshots, correlation IDs,
deadlines, partial-result policy and terminal states. Do not infer “all replied”
from topic membership that can change after publication. Keep general workflow
state machines outside this slice unless a concrete repeated workflow needs them.

Acceptance: no priority starvation, bounded backlog behavior, forbidden routing
creates no deliveries, and aggregation handles duplicate, failed, late and
forgotten responses without claiming task success from acknowledgment alone.

## Disposition of previously deferred features

| Feature | Placement or explicit trigger |
| --- | --- |
| Presence and agent capabilities | Milestones 1–2; identity and routing metadata, with provenance labels. |
| Push wakeups and automatic harness invocation | Fresh-worker request path already implemented; existing-session routing is milestone 4. |
| Lease-based work claiming | Already implemented; preserve it through every milestone. |
| Environment-based wake context and watch | Milestone 5. |
| Dead-letter queues | Milestone 5 review surface over retained failed deliveries; do not duplicate payload storage. |
| Rate limits | Binding launch protection in 5; account policy and fairness in 7. |
| Scheduling, TTL and request deadlines | Milestone 6, with distinct semantics. |
| Cancellation | Milestone 6; process termination precedes releasing the wake hold. |
| ACLs | Milestone 7 before broadening the trust boundary; retain current checks throughout. |
| Priority | Milestone 7 after evidence of queue contention. |
| Response aggregation | Milestone 7 with a fixed recipient set and explicit completion rule. |
| Broadcast destinations | Use existing explicit topic subscriptions first; add snapshot broadcast only for a demonstrated operator use case. |
| Wildcard subscriptions | After an actual topic hierarchy requires them; define fanout, duplicates, unsubscribe and authorization first. |
| Message batching | Only after profiling shows publication/poll overhead matters; retain per-event retry identity and completion. |
| Workflow state machines | After a repeated workflow supplies states, transition ownership and compensation rules. |
| NATS/other broker transport | After measured polling latency/load exceeds a selected target; PostgreSQL stays authoritative and broker delivery stays recoverable. |
| Distributed transactions / exactly-once external effects | No general promise; require effect-specific idempotency or compensation contracts. |
| Cross-host leadership | Only when deploying workers on multiple hosts; add fencing and old-host termination before enabling it. |
| Retention/pruning | Separate policy for events, results, receipts, sources and operational logs, with restore/forgetting tests before deletion. |

No item is implicitly complete because it appears in this plan. Triggered later
features need their own bounded implementation slice and acceptance evidence.

## Checks and rollout

For store changes, run `make test-integration` and `make check`, then the relevant
backup/restore lifecycle checks. Use disposable PostgreSQL clusters exclusively.
Include real CLI/API tests for ownership and retry boundaries, lifecycle fixtures
for each supported harness, and selected native trials for the Rhumb scenario.
Passing fixtures establishes mechanics; native trials must report provider limits
and actual delivery separately from usefulness.

Back up the operational store before additive migrations. Stop affected workers
only after inspecting their active attempts, apply compatible binaries/schema,
restart the API with its existing semantic-worker arguments, and verify the
installed revision. Preserve legacy inboxes and current memory profiles. A failed
upgrade leaves dispatch disabled until holds and process state are reconciled;
do not downgrade to code that ignores new ownership or fencing rules.

## Design review and open evidence

The owner’s current corrections govern this plan. Relevant source is
`core/agent_events.go`, `core/agent_wakeups.go`, `internal/wakeup/wakeup.go`,
`localapi/server.go`, and the two existing contracts. The initial spec's section
30 and the event contract's deferred-feature paragraph supply the inventory.

Alternatives: keeping harness names as identity fails the two-account and
fungibility requirements; static ordinal slots alone cannot find an interactive
session; using fuzzy memory search as a directory cannot establish fresh routing;
adding a broker does not resolve identity. Retain the existing transport and add
the smallest session directory and delivery contracts before dispatch changes.

Adapter feasibility, session/profile association details, native-session resume
behavior, live task benefit, contention baselines and multi-host fencing remain
unverified. They are not required to record this sequence; each is a blocking
implementation/acceptance obligation at its named milestone. Do not report
directory routing or interactive delivery as installed until those checks pass.


### Review provenance and remaining obligations

Pincite's validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`
supplied planning packet `pkt-765b7a8a388d1d8f`,
content SHA-256 `765b7a8a388d1d8fbf140e80927eaf5d9d9652b78edba19333d4b39ad00f8e1d`,
corpus `corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`,
retriever `retriever-ec995ecdd083b2c8`. Its recommendation ceiling supports this
plan; the owner's instructions supply the design and deployment authority.
The selected concepts were stable domain identity, repository-contract
precedence, preserving observed behavior, and separating semantic changes into
verified checkpoints. The [decision receipt](agent-coordination-v1-review.json)
records the selected scope and alternatives.

Remaining packet obligations are classified below. None certifies a future
implementation; each implementation milestone retains its own blocking checks.

| Unmet obligation | Classification for this planning decision |
| --- | --- |
| Affected implementer/operator perspectives | Nonmaterial to recording owner requirements and this sequence; native adapter feedback is required in milestones 2–4. |
| Current carrying cost; later migration/reversal cost; reversal-cost estimate | Nonmaterial to planning additive slices; no measured cost reduction is claimed. Estimate actual migration effects before each rollout. |
| Model expressed in executable behavior | Nonmaterial to a plan, material to accepting milestones 1–4. No live-directory implementation is claimed. |
| Business differentiation/product lifespan; business-value/expert-access evidence; feedback cadence; team capacity | Nonmaterial: no separate domain framework or staffing proposal is selected. Keep the implementation in existing Cairn modules and revise the plan from owner feedback. |
| Current cost/risk over an interval; latent security/data/compatibility check; no-change procedure | Nonmaterial to documenting the demonstrated account/routing gap; profile association and execution fencing are material checks before enabling session routing. No general reliability improvement is claimed. |
| Preservation-boundary procedure | Nonmaterial to the plan's explicit preservation rules; material to implementation, which must add characterization for legacy inboxes, attribution, retries and restore fencing. |

Reopen the identity design if actual native resume/fork semantics contradict the
session rules. Reopen dispatch only if supported session delivery or measured
availability requires a different mechanism. Stop a slice if its session association,
restore or process-termination contract is unresolved.
