# Agent 87 → agent 65 delivery delay — September 21

## Finding

The original observed delay occurred between publication and native inbox claim.
Agent 87's messages were stored and published promptly, but agent 65's existing Claude
conversation had no active native channel. The watcher deployed at 21:17 PDT on
September 20 refuses the former terminal wake fallback for Claude. This incomplete
activation left the recipient dependent on ordinary native turn boundaries.
The coordinator deployed that watcher before completing the account's channel
activation. That backlog was a rollout gap, not a one-hour timer or slow message
publication. Activation subsequently exposed a separate lifecycle defect,
described below.

## Evidence

A read-only snapshot at 09:30:56 PDT covered 94 messages from agent 87 to agent 65.
Seventy-six were handled; 18 remained pending with zero claims. Source storage to
publication took at most 54.3 seconds; delivery availability followed publication
within 3.1 milliseconds. No unfinished native hold blocked agent 65.

| Publication, PDT | First native claim | Wait before claim | Completion after claim |
| --- | --- | --- | --- |
| Sep 20 21:42:58 | Sep 20 22:42:33 | 59m34s | 12s |
| Sep 21 01:42:15 | Sep 21 04:54:25 | 3h12m10s | 9s |
| Sep 21 01:47:38 | Sep 21 09:27:19 | 7h39m41s | 10s |

The oldest pending message was published at 02:04:39 PDT, 7h26m before the
snapshot. The pre-rollout claimed messages waited 1.5 seconds to 9m45s; afterward,
observed waits grew to 7h39m. Shorter delays existed before the rollout, so this
finding does not attribute every historical wait to the same cause.

The coordinator independently inspected the recipient's live process/binding:
Claude agent 65, PID 3424316, native session
`1ac9ed1b-7184-4551-87bf-fd853e6e05bf`, binding `claude-one`. The process remained
alive, but the binding had no `claude_channel_dir` and no current inbox attempt
or wake. `claude_channel_endpoint` therefore returns no endpoint;
`prepare_idle_wake` then returns without terminal fallback. Heartbeat/presence
availability does not establish notification delivery.

The preserved pre-deployment watcher allowed the guarded Herdr terminal route.
The installed watcher SHA-256 is
`b5f411d25a873175c84118d4b0f7f42e9e27496a824831f6865fb7de2e361caf`;
the previous watcher was
`8e06ed00b3d9956a656f447b93c7ce13479194c3db1b3b785441afa122efc848`.
Selected SQL timestamps, query and route comparison are retained privately under
`/tmp/cairn-agent88-delay-87-to-65/`. No private message bodies or transcripts were
read for this diagnosis. Storage time is not composition-start time; native
claim is not the exact moment the recipient visually reads the message.

## Correction required

Finish the Claude account's native channel activation, resuming the same
conversation with its explicit account configuration and channel flag, enabling
the matching binding, then verify backlog handling. Do not enable the binding
before the channel exists: that would suppress ordinary boundary delivery too.
The prepared configuration and activation limits are recorded in the
[deployment report](coordination-deployment-2026-09-20.md).

The owner's restart approval covered OpenCode agents 24 and 67. This diagnosis did
not restart agent 65 or alter its inbox. Fixing this route and observing successful
queued delivery remain necessary; the delay has not been declared repaired.

## Repair verified at 14:18 PDT

After the owner explicitly approved restarting agent 65, the coordinator resumed
the same conversation and account, enabled its process-bound native channel,
and selected only that conversation for channel admission. Independent checks
confirmed subsequent backlog messages were claimed in `claude-channel:` turns
and explicitly acknowledged at 14:17:58 and 14:18:36, without additional manual
prompts. Other Claude sessions were unchanged. This proved initial channel
delivery, but did not establish sustained backlog handling. See the
[repair and live verification report](native-delivery-repair-2026-09-21.md).

## Follow-up: delivery latch stopped the backlog

A fresh read-only check at 17:31 PDT found 12 pending messages and no completion
since 14:21:06. The next notification had reached Claude at 14:21:47, but no
native inbox attempt was created. Claude reported the missing context and ended
the turn. Its only diagnostic tool listed and searched context filenames.

The lifecycle watcher can release an explicitly completed attempt before Claude's
Stop hook. The native-admission guard then returned before clearing
`delivered_since_idle`. A subsequent fresh channel prompt could not claim its
message because the previous turn's delivery flag remained set. Its submitted
wake marker correctly prevented blind retries, leaving the backlog stalled.

The regression fails on the second sequential delivery before repair. Commit
`ce17d4c` moves existing idle cleanup before the admission guard while retaining
turn-ownership checks and refusal of ordinary unbound prompts. Three sequential
deliveries, watcher/Stop ordering, active-at-Stop cleanup, and uncertain-write
preservation pass. Ninety focused tests and `make check` passed; independent
review reproduced the baseline failure and verified the correction.

The coordinator pushed and installed the repair at 17:38 PDT and restarted only
the presence watcher. Native conversations, their executions, and existing holds
were unchanged. Installed script SHA-256:
`dc80a394833c554ed8e3130322588fb26111aaeb0a01472e0b8639f488388e64`.

One notification was rearmed after independent review and fresh checks of its
exact conversation/process, terminal native outcome, pending delivery with zero
attempts, absent hold, and idle host. Under the lifecycle lock, only its wake
marker and stale delivery flag were cleared. The ordinary watcher and native
hook then claimed it at 17:38:48. No task was replayed, manually claimed, or
completed by the coordinator. Selected evidence and recovery guards are retained
under the private release's `claude65-backlog-repair` directory.

All 12 messages pending at the follow-up snapshot were subsequently handled in
fresh `claude-channel:` turns, each with one claim and no failed delivery. The
last explicit completion was at 17:48:27 PDT. Independent observation confirmed
the same native process/execution and live idle cleanup with the delivery flag
false between turns. This establishes sustained handling of that backlog after
the repair, not universal coverage of unactivated accounts or native cancellation.
