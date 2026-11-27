# Swarm view design

Status: **approved by the owner on 2026-11-25, with every recommendation accepted** (CAIRN-827, parent CAIRN-826).
Baseline `4b310f9e`. This document is the design pass that the owner required on
2026-11-06 before further work on a browser view. It records no implementation.

Revised on 2026-11-25 after two reviews
([design review](swarm-view-design-review.md),
[independent review](swarm-view-adhd-review.md)), with their source claims
checked at `b868d1a8`. The revisions correct and tighten the approved design.
None reverses one of the seven owner decisions. [What changed](#revisions-after-review)
lists them, and [extensions](#extensions-from-the-reviews) records what was
adopted from the reviews and what was not.

The source is the counter-proposal "A swarm view for Cairn, not a swarm
workspace". Its boundary rule holds here: Cairn stores and delivers; it does not
act. The view reads state that Cairn already stores and uses verbs Cairn already
has.

## What the page is

One page per campaign. The subject of the page is the agents, not Plane items.
That is the main difference from the Work View prototype, which is a board of
Plane items with agent state hidden two clicks down.

| Region | Content |
| --- | --- |
| Header | Campaign name, the owner's prose description, age, member count, a live indicator. |
| Needs you | Everything that waits on the owner, at the top of the page. Empty when nothing waits. |
| Agents | One row per member: label (`agent-N`), role text, harness and model, state, current task, what it holds, what it waits on, what waits on it, context room, pane label, Plane issue. |
| Message flow | Requests, responses and notices between members, newest first, each with its delivery state. Selecting an agent filters the flow to that agent. |
| Agent detail | Opens from a row: ledger items held, open deliveries in and out, waits, recent events, and the operator actions. |

A landing page lists the active campaigns of the collection and the online
sessions that belong to no campaign, so the page is useful before every swarm
has a campaign record.

## Source of each element

| Element | Stored state | Notes |
| --- | --- | --- |
| Members and roles | `cairn.agent_team_campaign`, `cairn.agent_team_member` | Membership is the principal string `agent/<uuid>`; it joins to `cairn.agent_session.agent_id`. |
| Role address and coordinator lineage | `cairn.campaign_role_binding`, `cairn.campaign_coordinator_handoff` | The current holder's row shows its role address (`role/<campaign>/root`). Coordinators that handed off collapse into a lineage list. |
| Label, harness, model, task | `agent_session.ordinal`, `metadata` (`harness`, `observed_model`, `task_summary`) | Reported context, not attestation. |
| Online, offline, stopped | `agent_session.stopped`, `expires_at`, `database_generation` | Computed as the agent directory computes it. |
| Busy or idle | `agent_session.metadata.state` | Set by lifecycle hooks. |
| Pane state and label | Herdr, read live | See "Herdr pane state". Never stored. |
| Holds | `cairn.campaign_ledger_item` where `owner` is the member and the status is open; leased rows of `cairn.agent_delivery` | The lease on ledger items is CAIRN-835. |
| Waits on, waited on | `cairn.agent_wait`; pending and leased `agent_delivery` rows | Needs `coordination-waits/1` active. See gap 6. A wait joins to a request by `dependency_event_uuid`, never by correlation alone. |
| Oldest pending item, and where a peer waits | The oldest eligible `agent_delivery` of the session; active direct-reply waits on its requests | Shown as two facts. Queue age does not prove that a peer is blocked: an old notice can wait for a digest while a newer request has a registered waiter. |
| Message flow | `cairn.agent_event` joined to `agent_delivery` | Envelope, `summary` (at most 280 bytes) and state. The page does not load record bodies by default. |
| Needs you | See "What needs the owner" | All from existing signals. |
| Context room | `agent_session.started_at`, `compaction_count`, and the CAIRN-816 counters | See "Context room". |
| Plane issue | Identifier text in the ledger item or task summary | See "Plane". |

## Gaps between the proposal and the stored state

The proposal assumed some state that does not exist. Each gap has a proposed
answer. None needs a new table.

1. **A campaign has no description.** `agent_team_campaign` has a name, a root
   agent and policy fields only. Member `role` is the enum `root`, `lead`,
   `worker`. Proposed: add `description` (bounded text) to
   `agent_team_campaign` and `role_label` (bounded free text) to
   `agent_team_member`. Reason: the owner's prose and the free-text roles have
   no other place, and a column on the existing row is the smallest change.
   `cairn team set-campaign` and `set-member` accept them. This is CAIRN-828.
2. **A pane ID is not stored.** Cairn resolves a session to a Herdr pane on the
   host at wake time, from `/proc` and the Herdr CLI, and records only a
   condition code in the database. The view therefore must run on the host and
   read Herdr itself.
3. **There is no owner address.** No principal or inbox means "the owner". See
   "What needs the owner" for the proposed definition.
4. **State changes have no ordered record.** `agent_event.position` is ordered
   by commit, but a delivery that becomes leased, handled or superseded leaves
   no ordered row. See "Live updates".
5. **No per-session window counter exists.** `compaction_count` is incremented
   only by `cairn ledger compact`; no lifecycle hook reports a compaction.
   `recall_observation` has no session key. See "Context room".
6. **`coordination-waits/1` is disabled on the live store.** It was turned off
   with the Work View on 2026-11-06. The waits column needs it active, and
   agents need it active to create waits at all.
7. **A session links to a Plane issue only through `cairn.work_association`**,
   which needs the operator, a Plane binding and the cached Plane projection.
   See "Plane".

## Live updates

The page holds one server-sent event stream to the view server. The stream
carries two things, and they have different rules.

- **Events are a log.** The server sends each `agent_event` row of the campaign
  with `position` as the stream ID. A reconnecting client sends the last
  position it received and continues from there. `position` is an identity
  column, but every insert into `agent_event` takes the transaction advisory
  lock `agent-events:<repo>` and holds it to commit (publication, reissue, wait
  outbox, stopped-backlog notice). Positions therefore become visible in
  order, and a reader that asks for `position > N` cannot skip a row that
  commits later with a lower position. This is the commit-ordered cursor the
  proposal requires, and it already exists.
- **State is a level.** Roster, deliveries, waits, ledger items and the
  needs-you list are current state, not a log. The server reads them in one
  read-only `REPEATABLE READ` transaction and sends the whole snapshot when it
  differs from the last one sent. A client that reconnects gets the current
  snapshot. There is nothing to skip, because no transition is replayed.

**Which events belong to a campaign.** `agent_event` has no campaign column,
and the campaign journal links an event to a ledger item in a later
transaction than the one that publishes it. The log therefore uses only facts
known when the event commits. An event is in the campaign's log when its
publisher or one of its delivery consumers is a member of the campaign, active
or not, or when `routing_info` names the campaign, and it was created after the
campaign. Inactive members count, so a coordinator's events survive its
handoff. The journal link is never a log filter; it is an annotation in the
snapshot. When `agent_team_member` changes, the server sends a resync marker
and the browser reloads the log window, because a new member's earlier events
lie behind the cursor. A direct message between two sessions that are in no
campaign appears on the landing page only.

The server reads every 2 seconds, and only while at least one browser is
connected. Each independent observation carries its own time: the database
snapshot, the Herdr read, and the session counters. The page shows the age of
each and marks one stale without hiding the others. The cursor is bound to the collection, the campaign and the restore
generation; a cursor from before a restore is refused and the page reloads.

Rejected: a change-log table for state transitions. It is a new table, it
duplicates state the store already has, and it would put short-lived state
(leases, presence) into a durable log.

Required tests in CAIRN-829: two concurrent publishers with a reconnecting
client that receives every event exactly once; a guard that fails when a
new `INSERT INTO cairn.agent_event` site does not take the collection lock
(four insert statements today, and the overdue-checkpoint path that takes the
same lock); a campaign
publication whose journal link is delayed until after a later event was
delivered and the client reconnected; the same across a coordinator handoff;
and a worker that joins mid-run.
Delivery positions are not used as a cursor, because the organic-notice path
and coordinator handoff do not keep them in commit order for a consumer.

## Where it is served

Recommended: a new command, `cairn swarm serve`, in the same binary. It is a
separate process that listens on loopback and opens the store with the local
operator channel, as the prototype's `work serve` did.

It is not put inside `cairn serve` for three reasons. The view needs
host-local reads (Herdr, `/proc`) that the store daemon must not make. It
needs operator authority, which the hosted API does not give. It can be
stopped or replaced without touching memory and delivery.

The host and origin checks, the session cookie and the CSRF check of
`cmd/cairn/work_serve.go` are kept. The single-use launch code is replaced: it
allows one browser per process lifetime, which fails for a view that stays up.
The server reads a token from a file with owner-only permissions, and any
browser that presents it gets a session. The token is compared in constant
time. Cookies on loopback are shared across ports, so the session cookie name
carries the bound port. The stream is a GET with no `Origin` header, so the
stream route also checks `Sec-Fetch-Site`. The view runs as a user unit so that
it is active without a manual start. Exposure beyond loopback stays an owner
decision and is not part of this design.

## Herdr pane state

The Herdr read runs on its own loop with its own cache and timestamp. It is not
inside the 2-second database read: the call is bounded to 4 seconds and 1 MiB,
and a slow or failed Herdr must leave delivery data current and mark only the
pane data stale or unavailable.

A user unit starts outside Herdr and has no `HERDR_SOCKET_PATH`. The server
finds the socket as the wake path does, from the environment of a live session
process. It matches panes with the wake path's own resolver, not a simpler
rule: Claude matches on Herdr's `agent_session.value`, Codex panes report no
session and match through the rollout file the process holds open, and Agy
matches through its parent session. The page shows `agent_status` (`working`,
`idle`, `done`), the pane ID and the terminal title. A session with no match shows "no pane on this host"; sessions on other
machines always show that.

Herdr has no URL for a pane. The "link" is the pane label with a copy control
for `herdr agent focus <pane>`. The page does not send keys or prompts to a
pane and does not start, stop or focus anything. Nothing read from Herdr is
written to the store.

## What needs the owner

The list is built from signals the store already has. It holds only work that
the owner can advance, and each row names the action the owner can take.

| Entry | Source |
| --- | --- |
| A question for the owner | A `request` event published to the `agent-board` topic with no `response` whose causation is that request. This is the existing convention of the agent-board skill. |
| A reply to the owner | An event addressed to the operator principal, once CAIRN-833 lets the owner send from the page. |
| A decision or escalation | A ledger item of kind `blocker` with status `escalated`, or of kind `decision` with status `open` whose owner is the root or the operator; a `workstream_status.decision_needed` that is not empty. A decision owned by a lead is shown on that lead's row, not here. |
| A coordinator due for handoff | The existing handoff check: session age or compactions past the campaign's thresholds. |
| The owner must take a turn | The host observation condition `owner_turn_required`. |
| A failed delivery | `agent_delivery.state = 'failed'`, with its code, grouped by cause. Deliveries that the operator cancelled (`operator_cancelled`, `operator_released`, `consumer_retired`) are outcomes of the owner's own action and are not listed. |
| A stalled agent | The existing `pending_starved` and `hold_stalled` findings. |
| Work sent to a dead session | The stopped-session backlog. |
| A wait that timed out | `agent_wait.state = 'timed_out'`. |
| A missed checkpoint | `cairn.agent_overdue_checkpoint`. |

"Stopped making progress" means `pending_starved` or `hold_stalled`, with the
thresholds those findings already use (1800 s busy, 600 s idle, 3600 s held).

Scope: the collection is shared by every project on the host, and the
`agent-board` topic and `workstream_status` carry no campaign. A campaign page
lists a row only when its publisher, owner or writer is a member of that
campaign. Rows that belong to no campaign appear on the landing page.

A board question has a lifetime and may take an unattended default. Once it
expires or the default is recorded, the row leaves the list and the flow shows
the outcome that was recorded, not "answered".

## Operator actions

The proposal lists four verbs. Each maps to an operation that exists. The
server performs them with the local operator channel.

| Action on the page | Existing operation | Limit |
| --- | --- | --- |
| Send a request or notice to an agent | `event-publish` | Published by the operator principal. It goes through normal delivery, so digest, `action` and auto-settle apply. |
| Supersede | `event-publish` of a notice with `--supersedes` | Only the owner's own earlier notices. |
| Acknowledge my delivery | `event-next` then `event-complete`, as the operator principal | Completion needs a live lease, so the server claims the delivery and completes it. Two existing operations, no new verb. `event-next` claims the oldest eligible delivery, so the owner's inbox is acknowledged in order. |
| Cancel this recipient's delivery | `work-cancel` | One delivery of one recipient. The owner cannot mark another agent's delivery handled. Cancel records `operator_cancelled` and can be refused while the recipient holds a live lease. Other recipients of the same request, and a reply that already counts in a group, are not affected. |
| Cancel a wait | `wait-cancel` | Operator authority. |

"Settle" is not a label on the page. The two verbs above replace it.

Before a cancel is confirmed, the page shows what it affects: the exact
recipient, any direct-reply wait registered on the request, and for a grouped
request the group-closure wait, the replies counted and whether this
recipient's reply already counts. An empty lookup and an unavailable one are
shown differently. A browser retry reuses the same request UUID. If the
recipient finishes between render and click, the page shows the outcome the
store recorded.

An action takes effect in the store at once, but an agent sees it only at its
next turn boundary. Until the recipient claims the delivery, the page shows the
action as sent and not yet seen. It does not show it as applied.

CAIRN-833 must first confirm three things about the operator principal as a
recipient: that a reply reaches it, including a late reply after the task
deadline or one with no correlation; that the claim-then-complete path works
for it; and that an unread reply to the owner does not hold the sender's
admission budgets. Anything that fails is fixed in the event fabric, not in
the view.

## Context room

The true fill of an agent's window is known only to its harness. Cairn does not
store it. The view shows what Cairn can state truthfully:

- compactions so far, and time since the last one;
- bytes that Cairn injected into the session since the last compaction
  (deliveries, recall, cues), with the count of deliveries;
- session age.

This needs two things from CAIRN-816: counters keyed by session
(`native_session_id` or agent ID), and the pre-compaction hook reporting the
compaction so that `compaction_count` and a last-compaction time are real. The
column is labelled "Cairn-injected since compaction", not "window used". A
session with no data shows "unknown".

Reading the harness transcript on the host would give the true fill
(`scripts/coordinator_load_report.py` does this offline, in Python). It is not
in the first version. It is an owner decision below.

## Plane

Recommended: link only. Plane stays the tracker of record, and the page shows
no Plane status. An identifier such as `CAIRN-829` in a ledger item or a task
summary becomes a link to that issue in Plane.

Mirroring Plane status would need the Plane binding and the cached projection
of the Work View, which the owner had disabled, and it would rebuild part of
Plane inside the page.

## The Work View prototype

The swarm view replaces the browser page of the Work View. Kept from it: the
loopback security shell and the read-only coordination queries over waits. Not
kept: the Plane board, the item detail modal, annotations, artifact pages and
advice in the browser.

Recommended for CAIRN-837: remove `web/workview/` and the `work serve`
routes when the swarm view is active. `coordination-waits/1` becomes active,
because the view and the agents use it. The remaining work features
(`work-view/1`, `work-artifacts/1`, `work-advice/1`, `next-turn-notices/1`) are
CLI and store features outside this design; their retirement is an owner
decision below and needs its own item.

## No prompt growth

The view server reads the store and Herdr. It publishes nothing unless the
owner uses an action. It adds no hook, no subscription and no recall source.
An owner action produces one ordinary event to the addressed agent, which the
owner chose to send. CAIRN-837 measures this on a real swarm.

## Revisions after review

All from the two reviews, each checked against source.

1. Campaign attribution of events is defined, with a resync on membership
   change. The journal link is not a filter.
2. "Needs you" holds only what the owner can advance, is scoped to the campaign,
   excludes the owner's own cancellations, and follows a board question's
   lifetime.
3. "Settle" is split into two verbs. Acknowledging the owner's own delivery
   claims it first, because completion needs a lease.
4. A cancel shows the affected recipient, waits and group before it is
   confirmed.
5. The Herdr read has its own loop, socket discovery and timestamp, and uses
   the wake path's resolver so Codex and Agy panes match.
6. Role address, coordinator lineage and the handoff warning are shown.
7. The session cookie is named per port, the stream checks `Sec-Fetch-Site`,
   and the token compare is constant-time.
8. An owner action is shown as sent and not yet seen until the recipient
   claims it.
9. An agent row shows its oldest pending item and, separately, the request a
   peer has a registered wait on.

## Extensions from the reviews

Adopted, because each is a read over existing state and makes an approved
action or panel correct:

- Preview of a cancel (revision 4). CAIRN-833.
- Oldest pending item beside the registered waiter (revision 9). CAIRN-830.
- Order "Needs you" by the number of sessions with a registered wait directly
  on the row. One hop only. CAIRN-831.

Not adopted in this work. Each is a possible later item for the owner to
choose; none has a work item.

| Idea | Why not now |
| --- | --- |
| One read of outstanding commitments for a replacement lead | A good agent-side read under the caller's own authority, but it is not part of the owner's view, and its permission rules (root successor versus ordinary lead) need their own design. The strongest candidate for a next item. |
| Rank the whole page by transitive waiting cost | It needs waits to be registered widely and a new link from waits through intermediate coordinators; a delivery is handled at the first hop while the wait stays open, so the chain breaks. Waits are disabled on the live store today. Revisit with data after CAIRN-830. |
| Control epochs that refuse stale writes, fencing tokens on assignments | These make Cairn refuse memory writes and gate `git-land`. That is Cairn acting, which the boundary rule excludes. The display half is adopted as revision 8. |
| Delivery classes with receiver credits | A redesign of admission and delivery built on a context figure that Cairn does not have. It belongs with the CAIRN-807 work, after CAIRN-816 produces counters. |
| A repository-wide integration slot | Trunk contention is real, but it is a `git-land` concern and not coordination state for this view. |
| Transient storage for coordination payloads outside memory search | Real: every event needs a memory record. A storage change to the event fabric, outside this view. |
| Bulk cancel or reissue by cause | Grouping for display is adopted. Each delivery keeps its own identity and recovery rules, so bulk recovery is not safe by grouping alone. |

Two limits that the independent review names and this design accepts:

- The view is only as full as the agents make it. Campaigns, ledger items and
  waits are recorded by choice. The page stays useful without them: sessions,
  deliveries and events need no ceremony, and the landing page shows sessions
  in no campaign. CAIRN-828 includes the lead's instructions for this reason.
- Every publication in the shared collection takes one advisory lock. The view
  adds no writer and takes no lock on read. Contention between swarms is a
  kernel question outside this design.

## Owner decisions (accepted 2026-11-25)

1. **Where it is served.** Recommended: `cairn swarm serve`, a separate
   loopback process in the same binary, run as a user unit.
2. **Plane.** Recommended: link only.
3. **Work View.** Recommended: remove the browser page and its routes in
   CAIRN-837; decide the fate of the other work features in a separate item.
4. **The owner's address.** Recommended: the `agent-board` topic plus the
   operator principal.
5. **Context room.** Recommended: Cairn-attributed counters first. Decide
   later whether the view server may read harness transcripts on the host for
   the true window fill.
6. **Schema.** Approve two columns: `agent_team_campaign.description` and
   `agent_team_member.role_label`.
7. **Activate `coordination-waits/1`** on the live store.

The fate of the other work features (decision 3) is CAIRN-840.

On 2026-11-27 the owner settled the open half of decision 5: the view server
reads harness transcripts on the host for the true window fill, in the first
build ("read the transcripts"). The reader takes only token counts, model
names, record types and times from a transcript; no message text leaves it and
nothing is stored. The Cairn-attributed figures stay on the page beside it as
a proxy. [Swarm view](../swarm-view.md#context-room) describes the result.

Deferred, with no work: whether to revisit orchestration inside Cairn after
Striatum-next lands.

## Changes made to the work items

The revisions after review were added to CAIRN-829, CAIRN-830, CAIRN-831 and
CAIRN-833 on 2026-11-25.

- CAIRN-828: the two columns of decision 6, on `cairn team`.
- CAIRN-829: `cairn swarm serve`, the token-file login, the stream and its two
  tests, the Herdr read.
- CAIRN-830: activation of `coordination-waits/1`.
- CAIRN-831: the table in "What needs the owner".
- CAIRN-832: the two requirements on CAIRN-816.
- CAIRN-833: the verb table and the operator-reply check.
- CAIRN-837: the removals in "The Work View prototype".
