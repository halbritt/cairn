# Use memory across agent workflows

Cairn helps a later agent continue useful work without rereading an entire
conversation. Shared notes retain decisions and procedures; session inboxes carry
specific requests and results. These serve different purposes and can be used
independently.

## Start with relevant context

Before investigating a problem, search for the project, affected files, and known
errors. Pull the relevant notes, then compare them with current source and the
current request. A saved preference may explain a decision, but it cannot expand
the user's authorization. See [searching memory](memory-search.md).

When several agent hosts use the same collection, they can retrieve eligible
notes written by one another. Configure each host with the intended destination
profile and collection. Tool installation alone does not make an agent search or
save automatically; lifecycle hooks are separate integrations.

## Delegate a specific piece of work

Choose a task with an observable result, such as reviewing one change or
investigating a failing test. State the relevant project, scope, constraints,
expected result, and validation. Resolve an existing session through the directory
or address a configured fresh-work pool. See
[session coordination](session-coordination.md) and [worker pools](../worker-pools.md).

Save one-off assignments under a dedicated coordination task and publish the
exact source version. Keep reusable decisions in repository scope. A recipient
reads the exact event source before acting; completion records reported handling.
The requester still checks the result against the task's acceptance criteria.
Cairn does not independently verify work because a worker says it is complete.

## Leave a useful handoff

A handoff should let another session resume without guessing. Include:

- The objective and current constraints.
- What changed and where the changes can be found.
- Checks already run and their results.
- Unfinished work, unresolved questions, and the next concrete step.

Use actual paths, revisions, or issue identifiers when they help locate the work.
Keep secrets and raw transcripts out of the handoff. Label uncertain conclusions
as uncertain. Save reusable lessons separately when they are useful beyond this
assignment. See [saving notes](memory-capture.md).

## Close the loop

After the work, record only findings likely to help a future task. Correct a stale
procedure instead of appending contradictory advice. A successful search or
capture shows that the mechanism worked; it does not show that memory improved
the task. For evaluation, Cairn can retain use and outcome observations through
the [use/outcome loop](../use-outcome-loop.md).
