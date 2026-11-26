# Roadmap

Cairn is the memory and coordination kernel for agents that work across
sessions and tools. **It stores and delivers; it does not act.** Herdr starts,
watches and wakes agent panes. Plane tracks work. Cairn holds what agents
remember and carries what they say to each other.
[Decision 0022](decisions/0022-kernel-boundary-and-swarm-view.md) records this
boundary and what it excludes.

This page lists direction and open work. Plane holds the work items and their
state. See [capabilities and limits](implementation-status.md) for what the
source does today.

## Now

### Larger agent teams within the context limit (CAIRN-807)

The limit on swarm size is agent context, so this work comes first. Compact
inbox delivery, digests and auto-settle are on by default (CAIRN-808), and
recall no longer re-injects records already in context (CAIRN-809). Tool
output entering agent context is bounded on the host (PROXIMAL-15). Open:

- Compact delivery on the Agy, OpenCode and Hermes adapters (CAIRN-811) and
  the remaining batch and overflow gaps (CAIRN-812).
- Less coordination fan-out, and an action on every notice (CAIRN-813,
  CAIRN-814).
- No wakes for retired recipients and no interruption of a peer in the middle
  of a task (CAIRN-815).
- Capture observations and delivery accounting per session (CAIRN-816). The
  swarm view's context column depends on this.
- Compact views for MCP and hooks (CAIRN-810).
- Every remaining opt-in capability is switched on or removed (CAIRN-817), per
  [decision 0021](decisions/0021-activation-is-part-of-done.md).

### Hybrid runtime (CAIRN-301)

Go takes over the lifecycle logic that Python held, and a Rust worker serves
semantic inference ([decision 0020](decisions/0020-hybrid-runtime.md)). Source
for both is published. The remaining gates are qualification of each complete
watcher unit, the production transition (R-07) and retirement of the Python
runtime (R-08, CAIRN-785). The
[campaign status](plans/hybrid-runtime-campaign-status.md) lists what is open,
and the [release contract](plans/hybrid-runtime-contracts/release.md#work-items-and-release-gates)
holds the criteria.

## Next: the swarm view (CAIRN-826)

One live page per swarm over state that Cairn already holds. It answers four
questions: who is in the swarm and what each agent is doing; who is talking to
whom and what is pending; what needs the owner; which agents are running out
of context room. The owner starts a swarm exactly as today, and no agent's
prompt grows because the view exists. The
[design](plans/swarm-view-design.md) was approved on 2026-11-25.

| Step | Work item | Result |
| --- | --- | --- |
| V1 | CAIRN-828 | A swarm is recorded as a campaign from the owner's description. |
| V2 | CAIRN-829 | Roster: read API, live stream and a page that shows who is in a swarm. |
| V3 | CAIRN-830 | Message flow and pending work; waits activated on the live store. |
| V4 | CAIRN-831 | What needs the owner, at the top of the page. |
| V5 | CAIRN-832 | Context room for each agent. Needs CAIRN-816. |
| V6 | CAIRN-833 | Operator actions, limited to verbs Cairn already has. |
| V7 | CAIRN-834 | Single-wake routing: an unaddressed post wakes one agent. |
| V8 | CAIRN-835 | Single-holder custody with a lease on campaign ledger items. |
| V9 | CAIRN-836 | A byte budget and a record for every model-visible injection. |
| V10 | CAIRN-837 | Activate the view, remove the Work View page, verify on a real swarm. |
| V11 | CAIRN-840 | Decide the fate of the remaining work features. |

The prerequisites (CAIRN-808, CAIRN-809 and PROXIMAL-15) have landed, so V1
can start.

## Later

- Compact communication contract (cd1): repair validation, references and
  examples, then adopt it by default (CAIRN-819 to CAIRN-822).
- Isolated Agy print-mode sessions without accidental enrollment (CAIRN-818).
- A fast full test run (CAIRN-804) and the `/proc/locks` race (CAIRN-788).
- A single read of outstanding commitments for a replacement lead. This is the
  strongest candidate from the swarm view reviews; it has no work item yet.

## Not planned

Cairn does not start or stop agent sessions, manage worktrees, control
compaction, choose models, poll GitHub or CI, or become the task tracker of
record. A swarm controller, a native task board, model failover, a chat
product and multi-user rooms are out. The owner may revisit orchestration
inside Cairn when Striatum-next lands (forecast March to May 2027).

## Constraints

- PostgreSQL is the operational store.
- Cairn targets a trusted, single-user host. Project names and session
  metadata do not create authentication boundaries.
- Validation is local; nothing depends on GitHub CI
  ([decision 0018](decisions/0018-local-validation-without-github-ci.md)).
- Anything that wakes an agent goes through the event fabric.
- The repository holds current contracts and decisions, not run records
  ([decision 0023](decisions/0023-repository-holds-current-contracts.md)).
