# Claude channel detection and wake refusal logging — September 22

## Finding

The owner reported that Cairn events were not waking Claude Code sessions. A
read-only inspection of the installed watcher, bindings, session state and
channel registries at 14:30 PDT found that no live Claude conversation was
wake-eligible:

- Binding `claude-one` listed exactly one conversation in
  `claude_channel_sessions`, agent 65's striatum-next session. Its process had
  exited on September 21 at 19:27 PDT and its state was retired. The three live
  conversations in that account (agents 10, 99 and 100) were unlisted.
- Binding `claude-two` had no `claude_channel_dir`, so its seven live
  conversations could never be woken.
- Bridge sockets existed for several live processes because `cairn-events` is
  registered in both account configurations, but every production process was
  launched as plain `claude --dangerously-skip-permissions`. Claude Code admits
  channel notifications only from servers named on `--channels` or
  `--dangerously-load-development-channels`; MCP registration alone is ignored.
- `session-inbox-ready` was empty for all ten live Claude sessions at that
  moment. The symptom was therefore delivery only at owner prompts, not a
  stuck backlog.
- `prepare_idle_wake` returned silently when a ready delivery had no transport.
  The presence journal held no Claude entry in 48 hours.

## Change

`integrations/lifecycle/coordination.py` now detects channel activation from
the live process's `/proc` command line, rechecking process identity, and keeps
`claude_channel_dir` only for a process launched with the configured server
(`claude_channel_server`, default `cairn-events`). Unflagged processes retain
ordinary turn-boundary delivery. The optional `claude_channel_sessions` list is
an additional restriction rather than the sole selector. When a ready delivery
has no wake transport, or a prior wake for it is still submitted or uncertain,
the watcher logs one line per delivery and reason, naming the agent, native
conversation, process ID and cause; the record lives in session state and
clears when the inbox empties or a wake is prepared.

Forty-six idle-wakeup and coordination tests pass, including new coverage for
flag forms, an unflagged process keeping owner-prompt claims with a single
logged refusal, a changed delivery logging again, an emptied inbox clearing the
record, an unflagged replacement process being refused, and the standing-marker
reason. The full Python suite passes apart from one Hermes lifecycle test whose
import error under whole-suite discovery predates this change and passes in
module isolation on both trees.

## Deployment

The coordinator installed the byte-verified script (SHA-256
`80b02829c9478c7cc838372b7d05fea2a65794e2565a1c5a801018ec0d3b982c`) at
14:47 PDT, retaining the previous file as
`coordination.before-channel-detection.py`, removed `claude_channel_sessions`
from `claude-one`, added the existing owner-only channel directory to
`claude-two`, and restarted only the presence watcher. Both bindings were
backed up beside the originals. No native conversation, execution or hold was
changed.

The first watcher cycle after the restart immediately logged a refusal that
had been silent before: Codex agent 87 (PID 2675324) had a pending delivery and
no native Codex queue endpoint. That process predates the native queue
rollout and needs a normal same-conversation restart through the installed
launcher; this report does not repair it.

A notice was published to agent 100's own inbox at 14:47:42 PDT to exercise the
Claude refusal path. That conversation was busy writing this change, so the
watcher correctly did not evaluate a wake for it during the turn; the refusal
line is expected on the first idle cycle after the turn ends, and the notice is
then claimed at the owner's next prompt.

## Activation

To make a Claude conversation wakeable, resume it with
`--dangerously-load-development-channels=server:cairn-events` in its own
account environment. (Correction, 2026-09-23: use the `=` form; with a space the
variadic flag consumes a following positional prompt. The owner's `claude` and
`claude-harm` aliases use the `=` form.) Detection then admits it on the next watcher cycle; no
binding edit is needed. Conversations launched without the flag continue to
receive inbox items at owner prompts, and the journal now says why.
