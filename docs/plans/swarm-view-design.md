# Swarm view design

Status: **proposed, awaiting owner approval** (CAIRN-827, parent CAIRN-826).
Baseline `4b310f9e`. This document is the design pass that the owner required on
2026-11-06 before further work on a browser view. It records no implementation.

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
| Label, harness, model, task | `agent_session.ordinal`, `metadata` (`harness`, `observed_model`, `task_summary`) | Reported context, not attestation. |
| Online, offline, stopped | `agent_session.stopped`, `expires_at`, `database_generation` | Computed as the agent directory computes it. |
| Busy or idle | `agent_session.metadata.state` | Set by lifecycle hooks. |
| Pane state and label | Herdr, read live | See "Herdr pane state". Never stored. |
| Holds | `cairn.campaign_ledger_item` where `owner` is the member and the status is open; leased rows of `cairn.agent_delivery` | The lease on ledger items is CAIRN-835. |
| Waits on, waited on | `cairn.agent_wait`; pending and leased `agent_delivery` rows | Needs `coordination-waits/1` active. See gap 6. |
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

The server reads every 2 seconds, and only while at least one browser is
connected. The cursor is bound to the collection, the campaign and the restore
generation; a cursor from before a restore is refused and the page reloads.

Rejected: a change-log table for state transitions. It is a new table, it
duplicates state the store already has, and it would put short-lived state
(leases, presence) into a durable log.

Required tests in CAIRN-829: two concurrent publishers with a reconnecting
client that receives every event exactly once; and a guard that fails when a
new `INSERT INTO cairn.agent_event` site does not take the collection lock.
Delivery positions are not used as a cursor, because the organic-notice path
and coordinator handoff do not keep them in commit order for a consumer.

## Where it is served

Recommended: a new command, `cairn swarm serve`, in the same binary. It is a
separate process that listens on loopback and opens the store with the local
operator channel, as `cairn work serve` does today.

It is not put inside `cairn serve` for three reasons. The view needs
host-local reads (Herdr, `/proc`) that the store daemon must not make. It
needs operator authority, which the hosted API does not give. It can be
stopped or replaced without touching memory and delivery.

The host and origin checks, the session cookie and the CSRF check of
`cmd/cairn/work_serve.go` are kept. The single-use launch code is replaced: it
allows one browser per process lifetime, which fails for a view that stays up.
The server reads a token from a file with owner-only permissions, and any
browser that presents it gets a session. The view runs as a user unit so that
it is active without a manual start. Exposure beyond loopback stays an owner
decision and is not part of this design.

## Herdr pane state

On each read, the server runs `herdr agent list` once (the wake path already
bounds this call to 4 seconds and 1 MiB). It matches a pane to a session where
Herdr's `agent_session.value` equals the session's `native_session_id`. It
shows `agent_status` (`working`, `idle`, `done`), the pane ID and the terminal
title. A session with no match shows "no pane on this host"; sessions on other
machines always show that.

Herdr has no URL for a pane. The "link" is the pane label with a copy control
for `herdr agent focus <pane>`. The page does not send keys or prompts to a
pane and does not start, stop or focus anything. Nothing read from Herdr is
written to the store.

## What needs the owner

The list is built from signals the store already has. No new rule is added.

| Entry | Source |
| --- | --- |
| A question for the owner | A `request` event published to the `agent-board` topic with no `response` whose causation is that request. This is the existing convention of the agent-board skill. |
| A reply to the owner | An event addressed to the operator principal, once CAIRN-833 lets the owner send from the page. |
| A decision or escalation | A ledger item of kind `decision` with status `open`, or kind `blocker` with status `escalated`; a `workstream_status.decision_needed` that is not empty. |
| The owner must take a turn | The host observation condition `owner_turn_required`. |
| A failed delivery | `agent_delivery.state = 'failed'`, with its code. |
| A stalled agent | The existing `pending_starved` and `hold_stalled` findings. |
| Work sent to a dead session | The stopped-session backlog. |
| A wait that timed out | `agent_wait.state = 'timed_out'`. |
| A missed checkpoint | `cairn.agent_overdue_checkpoint`. |

"Stopped making progress" means `pending_starved` or `hold_stalled`, with the
thresholds those findings already use (1800 s busy, 600 s idle, 3600 s held).

## Operator actions

The proposal lists four verbs. Each maps to an operation that exists. The
server performs them with the local operator channel.

| Action on the page | Existing operation | Limit |
| --- | --- | --- |
| Send a request or notice to an agent | `event-publish` | Published by the operator principal. It goes through normal delivery, so digest, `action` and auto-settle apply. |
| Supersede | `event-publish` of a notice with `--supersedes` | Only the owner's own earlier notices. |
| Settle | `event-complete` for deliveries addressed to the owner; `work-cancel` for another agent's delivery | The owner cannot mark another agent's delivery handled. Cancel records `operator_cancelled`. |
| Cancel a wait | `wait-cancel` | Operator authority. |

CAIRN-833 must first confirm that the operator principal can receive a
response, so that a reply to an owner request is visible on the page. If it
cannot, the owner's requests are published without an expected reply until
that is fixed in the event fabric, not in the view.

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

Recommended for CAIRN-837: remove `web/workview/` and the `cairn work serve`
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

## Decisions for the owner

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

Deferred, with no work: whether to revisit orchestration inside Cairn after
Striatum-next lands.

## Changes to the work items on approval

- CAIRN-828: the two columns of decision 6, on `cairn team`.
- CAIRN-829: `cairn swarm serve`, the token-file login, the stream and its two
  tests, the Herdr read.
- CAIRN-830: activation of `coordination-waits/1`.
- CAIRN-831: the table in "What needs the owner".
- CAIRN-832: the two requirements on CAIRN-816.
- CAIRN-833: the verb table and the operator-reply check.
- CAIRN-837: the removals in "The Work View prototype".
