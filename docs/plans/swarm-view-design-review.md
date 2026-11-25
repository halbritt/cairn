# Swarm view design review

Reviewed 2026-11-25 against Cairn `072964ed`. The owner approved the [swarm view design](swarm-view-design.md) under CAIRN-827. The findings and extensions below are review recommendations. The review used source inspection and independent design exercises. The acceptance cases describe behavior to test.

**Recommendation.** Define campaign attribution before building the reconnecting stream. For the first extension, show affected waiters before the owner cancels a delivery. The corrected selection produced three equally scored finalists. The cancellation preview is my first implementation choice because it makes an already approved operator verb safer and has a small, decisive test. [Corrected shortlist](#corrected-shortlist)

## What the view should help a swarm do

Keep the campaign page, the separate loopback process, link-only Plane integration, and the split between event history and current state. These choices let the owner inspect a campaign without adding a wake or spending any agent’s context. The broader goal is for workers and leads to keep each other moving when the owner is away. A useful test is whether a replacement lead can find an outstanding decision, identify a missing reply, and release a blocked peer using state Cairn already retains.

## Findings on the approved design

### 1. Specify when an event belongs to a campaign

The event lock establishes publication order within a collection. A campaign filter adds a second question. At what point can the reader determine that a particular event belongs on this page? `CampaignPublish` reserves a journal entry, publishes the event, then finalizes the link to the event ID. Suppose event 41 commits, event 42 commits and is already linked, and the browser receives 42. If the campaign query first sees 41 after its link is finalized, a reconnect asking for positions above 42 will miss it. Filtering on today’s roster creates a related problem when the coordinator hands off. [Stream contract](swarm-view-design.md#live-updates) · [Publication and linkage](../../core/campaign_ledger_ops.go) · [Handoff](../../core/campaign_handoff.go)

Use a campaign association knowable at publication for the resumable log. If an association can become known later, include it in a reconciled snapshot or give the association its own explicit delivery rule. Document where an ordinary direct message appears when campaign attribution is unknown. For CAIRN-829, delay the link for 41 until after the browser receives 42, reconnect, and check the result against that rule. Repeat across a coordinator handoff.

### 2. Reserve “Needs you” for work the owner can advance

An open ledger decision may belong to a lead. An operator cancellation records a failed delivery, so a rule that surfaces every failure would send a resolved cancellation back to the owner. An agent-board request has a lifetime, and a board response can record an unattended default. For each row, show who can take the next action and what that action is. Put lead-owned decisions with the responsible lead and group delivery exceptions by cause. [Proposed attention sources](swarm-view-design.md#what-needs-the-owner) · [Ledger ownership](../../core/campaign_ledger.go) · [Cancellation outcomes](../request-controls.md#stop-decisions-and-process-cleanup) · [Board answer and lifetime contract](https://github.com/halbritt/agent-board/blob/a058d378b29991746aa1d6f1a016c187a22ec524/docs/agents.md#answers)

For CAIRN-831, put a pending board question, a lead-owned decision and an operator-cancelled delivery in the same campaign. Only the live human question should ask the owner to take a turn. Let it expire or receive its configured default and verify the label accurately describes the outcome.

### 3. Give “Settle” two explicit verbs

Use **Acknowledge my delivery** for the owner’s inbox and **Cancel this recipient’s delivery** for operator cancellation. One topic request can have several deliveries. Cancellation targets one of them, can be refused for a live lease, and leaves a counted group reply intact. Show the exact recipient and any known dependent wait or group before the click. Reuse the same request UUID on a browser retry. For CAIRN-833, let a worker finish between page render and cancellation. Display the store’s winning outcome and verify the other recipients are unaffected. [Proposed actions](swarm-view-design.md#operator-actions) · [Cancellation, retries and races](../request-controls.md#cancel-accepted-work)

### 4. Report the age of each independent observation

A Herdr call can take four seconds against a two-second page refresh. Give the database snapshot and pane observation separate timestamps. A slow or failed Herdr read should leave current delivery data usable and label the pane observation stale or unavailable. For CAIRN-829, delay only Herdr while a delivery changes state, then verify that the browser shows both facts correctly. [Polling](swarm-view-design.md#live-updates) · [Host-local pane reads](swarm-view-design.md#herdr-pane-state)

## Original shortlist

The first review recommended the following three extensions. Their proposed acceptance cases remain useful independently of their ranking.

| Extension | What it gives the swarm | First acceptance case |
| --- | --- | --- |
| A bounded, on-demand “what needs me?” read for a lead | Decisions assigned to the authenticated lead and escalated blockers it owns. Eligible inbox counts can appear alongside them with an explicit session-wide label. | Give two leads a decision each and add a local-only delivery. The hosted caller’s own rows, counts and truncation indicator stay identical when the sibling’s work or local delivery changes. A second read leaves the inbox claimable. |
| Ask for the smallest usable piece of an artifact | A downstream parser author can start from pinned column names and one validated row while the producer finishes the export. The producer sends the sample as a notice and the final result as a causal response. | Use a direct grouped request with both reply and group-closure waits. On the sample notice, the group has zero counted replies and both waits remain open. After the final response, the group counts one reply and both waits resolve. Check when the consumer actually uses the sample, since digesting or routing can delay delivery. |
| Show counted replies alongside recipient delivery state | A lead can expand “3 of 5 replied” to see the two outstanding recipients, each delivery’s state, and the separate group and wait deadlines. A handled delivery may still owe a reply. | Use a topic group with two campaign recipients and an outsider. Check visibility as publisher, ordinary recipient and operator before exposing identities or counts. Exercise a partial close with a missing reply and a counted reply whose result later becomes unavailable. |

The ledger currently computes campaign-wide totals before applying its owner filter. Session pending counts are bounded and session-wide. The direct-wait predicate recognizes a qualifying response or failure. A group has a publication-time recipient set, tracks counted replies, and restricts detail reads to its publisher or an explicitly authorized successor. These are the contracts that carry most of the implementation risk. [Ledger query](../../core/campaign_ledger.go) · [Pending inbox read](../../core/agent_session_pending.go) · [Direct waits](../../core/agent_waits.go) · [Response groups](../response-groups.md) · [Group read authorization](../../core/agent_response_groups.go)

## How the alternatives were reviewed

Five isolated branches generated six ideas apiece using logistics, inversion, ant-colony coordination, a 3 a.m. on-call engineer, and removal of a load-bearing assumption. The first review chose its three finalists before it finished scoring the pool. Its published weights reproduce that shortlist, but the order of operations made the original selection retrospective.

For the corrected selection, a fresh evaluator received the design and the 30 original proposals in mixed order, without the prior scores or finalists. It scored each proposal on novelty, viability and fit, assigned a cluster and flagged traps. Weighted scores use 35% novelty, 40% viability and 25% fit. Flagged proposals stay in the table for inspection and are ineligible for the shortlist. The three highest remaining proposals go to separate focus passes. The scores express engineering judgment.

## The 30 alternatives, independently scored

N, V and F are novelty, viability and fit on a ten-point scale. The last column flags proposals excluded as written. For example, the original lead-query recommendation narrowed the proposal to a pull under the lead’s own authority. The independent evaluator scored the generated proposal to reuse the operator feed. That distinction changes both its safety and its ranking.

### Wait and blocking visibility

| Proposal | N | V | F | Weighted | Trap |
| --- | ---: | ---: | ---: | ---: | --- |
| Identify explicit waits on the oldest eligible, unclaimed delivery. | 5 | 8 | 8 | 6.95 | — |
| Show which agents have registered relevant waits before an operator cancels a delivery. | 5 | 8 | 8 | 6.95 | — |
| Follow a circular wait and show what each participant holds. | 5 | 7 | 8 | 6.55 | — |
| Show orphaned waits and cycles, with a control to cancel the inferred weakest edge. | 5 | 7 | 8 | 6.55 | — |
| Show the latest wake observation beside an unclaimed delivery. | 5 | 8 | 6 | 6.45 | — |
| Show the visible end of a dependency that crosses campaigns. | 5 | 7 | 6 | 6.05 | — |
| Make a wait graph the primary campaign view. | 5 | 5 | 6 | 5.25 | Sparse wait registration can hide most of a campaign in a graph-first view. |

### Fan-out and reply tracking

| Proposal | N | V | F | Weighted | Trap |
| --- | ---: | ---: | ---: | ---: | --- |
| Pair a lead’s incoming replies with the upstream request they serve. | 6 | 7 | 7 | 6.65 | — |
| Show each recipient of a grouped request and whether their reply counted. | 4 | 8 | 8 | 6.60 | — |
| Trace a superseded notice to open requests that cited it. | 6 | 7 | 6 | 6.40 | — |
| List the recipients whose replies are outstanding. | 3 | 8 | 8 | 6.25 | — |

### Handoff and continuity

| Proposal | N | V | F | Weighted | Trap |
| --- | ---: | ---: | ---: | ---: | --- |
| Let a replacement lead retrieve unanswered requests, holds and decisions together. | 5 | 8 | 8 | 6.95 | — |
| Ask the successor to state its first move while the outgoing agent can still reply. | 6 | 6 | 6 | 6.00 | — |
| Use durable role slots for all agents and transfer holds, waits and backlogs to successors. | 7 | 3 | 7 | 5.40 | Generalizing the root-role handoff would require ownership and transfer rules for every role. |
| Pull the exact handoff source when a prerequisite arrives. | 4 | 6 | 5 | 5.05 | — |

### Agent working conventions

| Proposal | N | V | F | Weighted | Trap |
| --- | ---: | ---: | ---: | ---: | --- |
| Record the condition under which a failed approach is worth retrying. | 6 | 7 | 5 | 6.15 | — |
| Return a misaddressed request with its original ID, reason and suggested recipient. | 5 | 7 | 6 | 6.05 | — |
| Each consumer states the smallest piece of an artifact that would let it resume. | 6 | 6 | 6 | 6.00 | — |
| When a request chain returns to its origin, contribute the part that can proceed independently. | 6 | 6 | 5 | 5.75 | — |
| Attach the first downstream example that successfully used a result. | 6 | 6 | 5 | 5.75 | — |
| When agents duplicate a start, give one the assumption to check independently. | 7 | 5 | 5 | 5.70 | — |

### Owner attention and view trust

| Proposal | N | V | F | Weighted | Trap |
| --- | ---: | ---: | ---: | ---: | --- |
| Show separate freshness for the database, Herdr and session counters. | 4 | 9 | 6 | 6.50 | — |
| Group exceptions by stopped session or shared cause and offer bulk cancellation or reissue. | 6 | 6 | 8 | 6.50 | — |
| Maintain a view-server heartbeat and notify an external channel of new owner actions. | 5 | 4 | 5 | 4.60 | Adds writes and unsolicited external delivery to a browser view. |

### Automated control and delegation

| Proposal | N | V | F | Weighted | Trap |
| --- | ---: | ---: | ---: | ---: | --- |
| Expose the operator snapshot and stream to a lead as a machine-readable feed. | 6 | 4 | 7 | 5.45 | Reusing the operator feed would expose data beyond a lead’s read authority. |
| Detect high message rates or repeated exchanges and optionally pause the campaign. | 6 | 4 | 6 | 5.20 | Automatic pausing would turn a diagnostic threshold into a control decision. |
| Infer team membership from communication patterns. | 7 | 4 | 4 | 5.05 | Traffic is insufficient evidence for durable campaign membership. |
| Pause campaign deliveries and drain leases under an expiring control. | 5 | 4 | 6 | 4.85 | Needs a new delivery control with explicit drain and resume semantics. |
| Use Cairn-injected byte counts to defer or reroute work. | 6 | 3 | 5 | 4.55 | Cairn-injected bytes measure only one contributor to context use. |
| Let agents lease the owner’s authority for named decisions. | 7 | 2 | 5 | 4.50 | Delegated owner authority requires an identity and grant contract of its own. |

Two implementation traps also apply to proposals the evaluator kept. A registered wait is nonblocking, so a cycle alone gives no basis for choosing which wait to cancel. Grouping failures for display does not make bulk reissue safe. Each delivery retains its own identity and recovery rules. [Wait semantics](../guides/scoped-coordination.md#3-wait-instead-of-polling) · [Recovery and cancellation](../request-controls.md)

## Corrected shortlist

The three highest eligible scores tied at **6.95**. The next score was 6.65. None of the three ideas in the original shortlist made this cutoff. Each finalist below received a separate focus pass after the scores and clusters were fixed.

### Preview the effect of cancelling one recipient’s delivery

When the owner selects a delivery, show any registered direct-reply wait on its request. For a group, show the group-closure wait, the number of replies counted and whether this recipient’s reply already counts, subject to operator read permissions. A worker can publish a reply while the request delivery is still pending. In that case the owner can withdraw an eligible delivery while the reply continues to count. Show the waiter’s deadline separately from the group deadline. [Cancellation semantics](../request-controls.md#cancel-accepted-work) · [Group collection](../response-groups.md#collection-states)

**Risk and first step.** Correlation alone cannot identify a waiter or a group member. Join the wait by its dependency event UUID and the member by its exact delivery ID. Build a three-recipient fixture with one active group wait and a counted reply from the selected recipient. Cancel that delivery and verify the counted reply, wait and sibling deliveries survive. Variations include opening the same dependency from the waiter’s row, explaining a wait deadline that arrives before group closure, and distinguishing an empty lookup from an unavailable one.

### ★ Give a replacement lead a single read of outstanding commitments

A root successor needs to know which replies are still owed, which holds remain active and which decisions were already settled. Cairn already writes a handoff note and transfers specific state for the stable root role. A bounded read can place current ledger items beside campaign-linked requests visible to the caller, in one consistent snapshot. Show what has changed since handoff and include exact references for follow-up. An ordinary lead appointed to a subtree has different access from the recorded root successor. [Coordinator role and handoff](../guides/scoped-coordination.md#coordinator-role-inbox-and-ledger-driven-session-handoff) · [Handoff request visibility](../handoff-request-status.md)

**Risk and first step.** Acting as a lead does not grant access to the predecessor’s inbox. Extend the root-handoff fixture with an active hold, a settled decision and a handled request still missing its reply. Read it as the recorded root successor and as a newly appointed ordinary lead. Check permissions before counts or truncation and verify repeat reads leave delivery state alone. Variations include comparing the handoff note with current holds, looking up a settled decision by ledger key, and attaching visible ledger keys to an ordinary reassignment.

### Show the oldest pending delivery and where a peer is actually waiting

An agent row might say “Oldest pending item: informational notice, 34 minutes. Agent 7 awaits a reply to a later request.” Match an active direct-reply wait to the exact request, then show its owner and age. Label group-closure waits separately. This helps the owner distinguish an old queue entry from the request whose delay a peer cares about. [Eligible inbox items](../../core/agent_session_pending.go) · [Priority and digest selection](../../core/agent_session_digest.go) · [Wait predicates](../../core/agent_waits.go)

**Risk and first step.** An older notice can wait for a digest while a later request qualifies for priority delivery. Queue order does not establish a blocking dependency. Put an old notice, a newer request with a registered wait, and another request without one in the same fixture. Verify the view identifies the notice and the correct waiter while preserving all three deliveries. Variations include showing queue age and wait age together, linking a starvation finding to the exact request, and identifying a priority-eligible request behind an older notice.

## End-to-end question

Close the browser during a five-agent campaign and hand the root role to a fresh session. Can the successor identify a missing reply, find the decision that already settled the question, and let a peer resume without asking the owner to reconstruct the conversation? Then reopen the page. The owner should be able to see which work still needs a human decision and what would happen if they cancelled one delivery. This would exercise coordination alongside CAIRN-837’s prompt-growth check.
