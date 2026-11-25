# 0022 — Cairn is a memory and coordination kernel; a swarm view, not a swarm workspace

Status: accepted by the owner, 2026-11-25. Work: CAIRN-826 and its children.

## Question

The "Collaborative Swarm Workspace Roadmap" (task-179, baseline `70e06493`)
proposed that Cairn grow a swarm controller, a native task board, model
failover and a chat product. Should Cairn become that workspace?

## Decision

No. Cairn stays the memory and coordination kernel: **it stores and delivers;
it does not act.** The roadmap is not adopted. Managing a swarm with a friendly
page is still wanted, and it is delivered as a read-mostly view over state that
Cairn already holds.

Reasons:

1. The stack exists. Herdr starts, watches and wakes panes. Cairn does memory
   and messaging. Plane does tracking.
2. The work is ad hoc. Seeded roles, a card state machine and reviewer routing
   are not wanted.
3. The limit on swarm size is agent context, which CAIRN-807 addresses.

## What is built

- A live swarm view that answers four questions: who is in the swarm and what
  each agent is doing; who is talking to whom and what is pending; what needs
  the owner; which agents are running out of context room.
- A swarm is recorded as a campaign, from the owner's prose description.
- Three ideas kept from the roadmap: single-wake routing, single-holder
  custody with a lease, and a byte budget for model-visible injection.

The [swarm view design](../plans/swarm-view-design.md) holds the detail.

## Boundary

Cairn does not start or stop agent sessions, manage worktrees, control
compaction, choose models, poll GitHub or CI, or become the task tracker of
record. `cairn wake serve` stays as it is.

Not built: the swarm controller and `HarnessDriver`; a native board as the
source of truth; `model_families` failover; escrow, composable waits, cron,
`--watch` and webhooks; both extension tiers; multi-user rooms, chat bridges
and a browser extension; reactions, typing indicators, direct messages and
hosted HTML artifacts.

## Rules for the work

- One delivery path. Anything that wakes an agent goes through the event
  fabric, so it inherits leases, digest, supersession, `action` and
  auto-settle.
- Go only. All live bindings run the Go lifecycle.
- [Activation is part of done](0021-activation-is-part-of-done.md).
- No ephemeral state in durable logs.
- No agent's prompt grows because the view exists.

## Design decisions accepted with the design

1. The view is served by `cairn swarm serve`, a separate loopback process in
   the same binary, run as a user unit. Exposure beyond loopback needs the
   owner's approval.
2. Plane is linked, not embedded.
3. The Work View browser page and its routes are removed in CAIRN-837. The
   fate of the other work features is CAIRN-840.
4. The owner's address is the `agent-board` topic plus the operator principal.
5. Context room uses Cairn-attributed counters first.
6. Two columns are added: `agent_team_campaign.description` and
   `agent_team_member.role_label`.
7. `coordination-waits/1` is activated on the live store.

## Reopen when

Striatum-next lands (forecast March to May 2027). The owner then decides
whether to revisit orchestration inside Cairn. Until then, Herdr plus the lead
agent is the controller.
