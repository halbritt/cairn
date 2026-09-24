# Draft preservation and busy-submission trials — September 23

## Result

Twelve live trials passed on six native wake routes. On every route an
unsubmitted composer draft survived an automatic Cairn wake. It was never
submitted, altered or cleared, and each delivery was handled after exactly one
native claim. A notice published while the session was busy with an owner
turn was never merged into that turn. It was handled in a separate wake turn
after the owner turn finished.

| Route | Account / build | Idle draft | Busy race |
| --- | --- | --- | --- |
| Claude channel | `~/.claude`, Claude Code 2.1.280 | pass, 12 s | pass, 58 s |
| Claude channel | `~/.claude-harm`, 2.1.280, Sonnet 5 | pass, 12 s | pass, 48 s |
| Codex native queue | `~/.codex`, Codex 0.156.1 | pass, 43 s | pass, 77 s |
| Codex native queue | `~/.codex-harm`, Codex 0.156.1 | pass, 34 s | pass, 48 s |
| Hermes queue | `hermes-one`, installed plugin `2fedbf0` | pass, 22 s | pass, 45 s |
| OpenCode queue | `opencode-one`, installed plugin | pass, 34 s | pass, 51 s |

The times run from publication to explicit acknowledgment. They include the
watcher's 30-second idle cycle and model latency. Agy is not covered. Its
Herdr terminal route has no atomic idle-only prompt, and
[the idle-wakeup contract](../plans/idle-session-wakeups.md#herdr-terminal-route)
already records that it cannot guarantee draft preservation.

## Method

Every trial used an owned throwaway session under `/tmp/cairn-draft-trial`,
launched through the owner's ordinary launchers. Claude used the channel flag
in its `=` form, and Codex went through the installed launcher shim. No live
owner conversation was touched. Each session received a setup prompt that
authorized acknowledging Cairn notices and nothing else. The sessions ran in
a private tmux server. The trial scripts drove keystrokes and read the screen
through tmux.

- **Idle draft.** The script typed a unique canary into the idle composer
  without pressing Enter, confirmed it was visible, and published one notice
  (source note `f9713998-fb4d-4f2b-a5ad-413a91b9d240`) to the session's agent
  UUID. It then waited for explicit handling.
- **Busy race.** The script cleared the composer and submitted an owner prompt
  that runs `sleep 20`. It waited until the directory showed the session busy,
  typed a second canary, and published a notice during the busy window.

Each trial was judged against the native store, not only the screen. Claude
and Codex submitted prompts render with the same prefix as the composer.

- Zero submitted user messages contained the canary. The checks covered the
  Claude transcript JSONL, the Codex rollout, Hermes `state.db` and OpenCode
  `opencode.db`. The idle canaries were rechecked after the later owner
  prompts, and still had zero occurrences.
- The canary was still in the input area when the check ran.
- Each wake became its own native turn. The owner prompt and the wake carried
  distinct Claude `prompt_id` values, and distinct Codex `task_started`
  records.
- The event status showed `handled` with `attempts=1`.

## Observations

- On Codex account two, the busy trial's owner prompt was submitted while the
  preceding wake turn was still finishing. Codex queued the prompt as a
  separate turn after the wake completed. The next wake followed the owner
  turn. Input that arrives during a wake turn was not merged into it.
- Claude account two was out of Fable 5.1 credit, and Codex account one had
  reached its usage limit until September 26. The trials switched those
  throwaway sessions to Sonnet 5 and to Codex's no-cost fallback model. No
  credits were bought and no usage reset was spent.
- `--dangerously-load-development-channels` accepts several values. With a
  space before its value, a following positional prompt is consumed as a
  second channel entry, and Claude exits with "entries must be tagged". The
  owner's aliases, the installer hint, the watcher refusal text and
  [the native inbox guide](../native-inbox.md) now use the `=` form.
- This was the first live exercise of the `claude-two` channel directory,
  which was added on September 22.

## Limits

These trials cover the check-to-submission race at the granularity of a busy
owner turn. They do not cover a keystroke that lands in the final
milliseconds before a queue submission. Draft preservation depends on each
harness's native queue API, which the trials exercised and did not replace.
Interactive per-request cancellation (CAIRN-1 and CAIRN-2) is unaffected.
Trial agents 113 to 117 and 119 left the directory when their sessions exited.
Raw native transcripts were read locally and were not committed.
