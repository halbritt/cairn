# Agent 87 → agent 65 delivery delay — September 21

## Finding

The observed delay occurs between publication and native inbox claim. Agent87's
messages are stored and published promptly, but agent65's existing Claude
conversation has no active native channel. The watcher deployed at 21:17 PDT on
September20 refuses the former terminal wake fallback for Claude. This incomplete
activation left the recipient dependent on ordinary native turn boundaries.
The coordinator deployed that watcher before completing the account's channel
activation. The current backlog is a rollout gap, not a one-hour timer or slow
message publication.

## Evidence

A read-only snapshot at 09:30:56 PDT covered 94 messages from agent87 to agent65.
Seventy-six were handled; 18 remained pending with zero claims. Source storage to
publication took at most54.3 seconds; delivery availability followed publication
within3.1 milliseconds. No unfinished native hold blocked agent65.

| Publication, PDT | First native claim | Wait before claim | Completion after claim |
| --- | --- | --- | --- |
| Sep20 21:42:58 | Sep20 22:42:33 | 59m34s | 12s |
| Sep21 01:42:15 | Sep21 04:54:25 | 3h12m10s | 9s |
| Sep21 01:47:38 | Sep21 09:27:19 | 7h39m41s | 10s |

The oldest pending message was published at02:04:39 PDT, 7h26m before the
snapshot. The pre-rollout claimed messages waited1.5 seconds to9m45s; afterward,
observed waits grew to7h39m. Shorter delays existed before the rollout, so this
finding does not attribute every historical wait to the same cause.

The coordinator independently inspected the recipient's live process/binding:
Claude agent65, PID3424316, native session
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

The owner's restart approval covered OpenCode agents24 and67. This diagnosis did
not restart agent65 or alter its inbox. Fixing this route and observing successful
queued delivery remain necessary; the delay has not been declared repaired.
