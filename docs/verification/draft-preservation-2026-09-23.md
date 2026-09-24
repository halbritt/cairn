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

- On Codex account two, the busy trial's owner prompt followed the idle
  trial's wake turn closely. The rollout shows the wake's `task_complete` at
  01:01:54.870Z, the owner `task_started` at 01:01:55.476Z, and the next
  wake's `task_started` at 01:02:29.330Z, so these were three separate turns.
  The Enter keystroke has no retained timestamp, so this trial does not show
  whether the input arrived before the wake finished. An earlier Codex probe
  on September 18 observed owner steering into an active turn. This trial
  does not establish a general no-merge guarantee for input sent during a
  wake turn.
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

## Selected identifiers

These identifiers support independent audit without raw transcripts. The
times are PDT, and each event reached `handled` with `attempts=1`.

| Route | Native session | Idle event | Busy event |
| --- | --- | --- | --- |
| Claude one | `6bf0b557-9742-4013-a680-072816725054` | `d2c62eab-4b35-4e27-b7c1-e7a3f4eefa12` | `171f9b67-ee0e-4f00-a6bc-8e9344b0ab2d` |
| Claude two | `f60f97e4-a2e4-4c5d-aaad-d338bdfe05b0` | `85f33cdd-d51e-4fa6-989e-4d85ead46526` | `2ef47c74-868c-48e7-819f-69fde77f499a` |
| Codex one | `01a0d0ea-3941-74b1-812a-ec349e878da4` | `6e99b776-3c36-4623-8e90-57cf5e1199d3` | `73fae46c-4169-4192-91d1-e1939b346355` |
| Codex two | `01a0d0ee-3665-76b2-88b3-dba6a521d733` | `a22cfc64-0c32-4fb2-895a-adc4adf0500f` | `dfb7177b-53a3-4401-8aa6-24fd437c3e1e` |
| Hermes | `20260923_180907_4741b6` | `6c5f807d-d138-490a-bfc2-319905db7fff` | `3a911ab2-33bc-4689-bef0-c6a4086738e6` |
| OpenCode | `ses_f2f05a23effeulpkKfXAbz3UM0` | `2b4aeb82-f692-4a5b-853e-c342ba1b7160` | `2f176b94-a249-4e8b-9b0e-175803ecb427` |

In the Claude account-one transcript the prompts have distinct `prompt_id`
prefixes: the first channel wake is `1f12dc7a`, the owner busy prompt is
`758f9a02`, and the second channel wake is `39c5d0c8`.

## Limits

These trials cover the check-to-submission race at the granularity of a busy
owner turn. They do not cover a keystroke that lands in the final
milliseconds before a queue submission. Draft preservation depends on each
harness's native queue API, which the trials exercised and did not replace.
Interactive per-request cancellation (CAIRN-1 and CAIRN-2) is unaffected.
Trial agents 113 to 117 and 119 left the directory when their sessions exited.
Raw native transcripts were read locally and were not committed.
