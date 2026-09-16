# Live Codex/Rhumb routing — 2026-09-16

Status: request published while the addressed session was busy; handling and
reply remain pending. This is not yet a successful delivery claim.

## Current session and owner constraint

The owner required waiting until the original Rhumb process became idle before
restarting it. That process, native conversation
`01a0a067-0b37-7d11-94c3-1266df7b2e6f`, remained busy during the earlier checks.
This rollout did not restart or interrupt it.

At 14:13 UTC, after the goal resumed, Herdr pane `wV:p1` instead contained native
Codex process 1202003 and conversation
`01a0aa90-5ee8-73a1-a322-7f729e458623`. The conversation identifier came from an
open native session-file path; transcript contents were not read. The process
change happened outside this rollout. This is a different conversation, not
evidence that the original one resumed.

Cairn's installed host state associated the current PID, start time and boot
identity with `agent-5` (`a3836aca-26be-4be0-993f-705f10149272`), execution
`7efe53a0-ff36-4829-b729-8a2b5d657aeb`. Native delivery was enabled. The directory
reported project `rhumb`, worktree `/home/halbritt/git/rhumb-runtime`, and a task
summary about completing backup/restore integration and history storage. The
native process remained working. No identity or project metadata was fabricated
to create a match.

## Addressed request

The owner's example, “the Codex agent working on Rhumb,” was translated to exact
selectors `harness=codex, project=rhumb`. `agents resolve` returned one live
candidate. Publication used that resolution, including its execution and context
revision, rather than a worker-slot address.

At 14:15:02 UTC, request `1756491b-c125-40f8-b8c0-3867edaa7b9c` was accepted for
this conversation's inbox. Its selected source is
`b0fec4c2-afae-4917-bca0-3e9a2d46716a/1`. The request asks the actual Rhumb worker
to review its current directory metadata and any inbox ownership problem, return
useful current task context, complete the native delivery and explicitly reply.
It requests no Rhumb source changes or interruption of ongoing work.

Delivery `d41b70ce-1f9f-49af-8b90-749afa34690f` was observed pending with zero
attempts while the native session was busy. The response group expects this one
recipient, uses `all` policy and has a 14:35:02 UTC collection deadline. That
deadline does not stop the Rhumb task or expire its inbox request.

Selected local observations are in `/tmp/cairn-rhumb-live-acceptance`. No parent
process claimed the recipient's inbox or prompted its terminal to force a turn.
The native adapter's supported Stop boundary is responsible for delivering the
queued request. Pending publication does not establish receipt, handling,
response collection or acceptance.

## Review and remaining evidence

The offered agent independently reviewed the plan against this changed runtime
state. Its selected result `6298967a-b245-43a2-90b0-c96883afa1d6/1` was read and
acknowledged. It agrees that a completed exchange with this genuine current Rhumb
conversation can satisfy the live-target requirement; the identity rules do not
require targeting an obsolete process. It found no other mandatory gap within
its inspected scope. This review does not establish that the pending message was
handled.

The parent rechecked the milestone contracts, source tests and retained successful
integration/lifecycle reports. The earlier tests cover resume identity, isolation,
fencing, retry freshness, independent account health, ownership, queue bounds,
watch recovery, explicit reissue, scheduling/control races and fixed response
groups. Their scope and limitations remain in the
[milestone reports](coordination-v1-acceptance-2026-09-16.md).
Installed CLI/API still reported clean `1846f95`; all eleven services were running
and all seven slots were online/available at 14:19 UTC. Availability permits
admission and does not prove provider capacity.

Remaining evidence is an actual native claim and completion by this addressed
session, followed by reading its explicit reply and checking the result against
the request. Until then, the live trial and full v1 acceptance remain open.
