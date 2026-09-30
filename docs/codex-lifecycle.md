# Ambient memory in Codex

Cairn's optional Codex hooks give Codex the automatic retrieval and checkpoint
capture that Claude Code already has. They use the shared lifecycle engine,
`integrations/lifecycle/memory.py`, in the same shared collection.

| Codex event | Behavior |
| --- | --- |
| `SessionStart` | Retrieves required instructions. Fresh startup or clear without a prompt or explicit workstream defers optional recall. Resume and compaction still prefer this session's checkpoint. |
| `UserPromptSubmit` | Searches with the prompt's task terms, quoted phrases and hints, and adds matches as additional context. |
| `PreCompact` | Selects and saves a checkpoint before manual or automatic compaction. |
| `Stop` (async) | Offers a checkpoint in the background once at least six new top-level messages exist since the last capture. |

A taskless `UserPromptSubmit` containing only the complete current Cairn wake
notice also defers optional recall. This recognizes the plain notice and the exact
`cairn-events` Claude channel envelope with matching UUID fields; it classifies
content and does not authenticate a sender or authorize an assignment. Extra
owner text, quoted notices, malformed or unknown renderings retain normal recall.
Explicit workstreams and resume/compact events retain their existing behavior.
The required-context search uses only the project name, without notice UUIDs or
recent file hints, and still delivers required instructions whole or refuses the
whole package when it exceeds budget. No optional semantic search, pull or
selector call runs for a deferred notice, and existing seen-state is retained.
The original host event is unchanged.

The notice does not contain the queued task body. After reading that source,
the agent still needs explicit task-conditioned retrieval through its normal
memory tools; a later actual prompt also uses normal hook recall. Notification
deferral does not itself supply useful task guidance or complete that goal.

Codex `SessionEnd` hooks run synchronously, with a one-second default and a
three-second maximum. That is too short for checkpoint selection, so the Codex
adapter does not capture at exit. The background Stop hook covers ordinary
sessions without delaying the turn. Exit capture is not guaranteed: dialogue
after the last Stop capture can be lost, and Codex may cancel an unfinished
background hook when the session ends. PreCompact can add one selection
alongside Stop. The engine records the rollout
path and the byte offset of the newest message in the snapshot capture actually
considered. Offsets in the append-only rollout stay distinct when the assistant
repeats identical text. They also keep working after the bounded excerpt
window stops growing. Dialogue that arrives while selection runs stays
uncaptured for the next Stop. A different rollout file, or one shorter than
the saved offset, counts all its dialogue as new. A failed capture leaves the marker where
it was, so the next Stop retries. A Stop continuation (`stop_hook_active`) is
not treated as a new boundary.

A prompt submitted while that background capture still holds the session lock
skips optional retrieval for that prompt instead of reporting a hook failure.

The transcript reader keeps only top-level `user` and `assistant` message
items from the Codex rollout. Codex 0.156 labels each content part in
`content_item_kinds`. From user items the reader keeps only `user.text` parts,
which excludes `AGENTS.md`, environment context, plugin and skill blocks, and
this adapter's own `hooks.additional_context`. Cairn's native queue submits its
automatic inbox wake as ordinary `user.text`, so the reader also excludes that
complete reserved envelope, with valid UUID fields, through the same recognizer
used by recall. It retains surrounding owner discussion, quoted wake text and
other owner parts in the same message. This is a capture filter, not proof of
sender identity. Rollouts without those labels
come from Codex versions before 0.156. For those, the reader drops the
`AGENTS.md` block and strips only a closed list of known injected tag blocks,
such as `<environment_context>` and `<recommended_plugins>`. All other text is
kept, including owner-written markup. That fallback covers only these known
forms, and the structural labels are the supported path. Developer items, reasoning, tool calls and their output are always
omitted. The earlier scan of 30 rollouts (277 excerpt messages) found no injected
context, but did not establish that queue wakes were excluded: a later retained
session inspection found a complete wake in capture input. The wake regression
tests cover both labelled and unlabelled rollouts and retained message offsets.
Codex now performs edits through its generic `exec` tool instead of
`apply_patch`, so the adapter does not collect file hints from tool use.
Quoted identifiers and task terms still guide retrieval.

Checkpoint selection uses the same Claude Code selector as the Claude,
OpenCode and Hermes adapters. The installer defaults its model to
`claude-sonnet-5`, and each selection runs one isolated `claude --print` call
with no ordinary tools. Ambient semantic recall can make separate preview and
body selector calls through the shared engine. The opt-in
[`--recall-mode agent_tools` path](claude-lifecycle.md#optional-recall-through-the-task-agent)
delegates optional inspection to the current task agent without those calls;
capture selection is unchanged. An omitted mode retains the existing default.

### Delegated recall across compaction

For Codex `agent_tools`, an ordinary `UserPromptSubmit` with canonical native
session and turn UUIDs can issue one optional recall allowance per native turn.
`SessionStart` never opens an allowance: startup, resume and compaction deliver
only required context, or no additional context when none is required. A wake
notification or a prompt without a valid turn ID cannot open an allowance.

The hook retains grants in `<session_id>.memory-budget.json` under `state_dir`,
using the existing session lock. A separate `.memory-budget.initialized` marker
detects ledger loss even when an older capture engine rewrites ordinary state.
Each grant records its byte limit, emitted hook
bytes and reserved native-tool allowance. The ledger is saved before context is
returned, independently of the ordinary hint/capture state. Binding changes,
seen resets, compaction and replay of an older turn cannot refund its allowance.
An uncertain output or later state-save failure conservatively retains the
reservation. Corrupt ledgers, or a missing ledger after initialization, refuse
renewed recall. A bootstrap failure after saving the marker also refuses renewed
recall until the state is reconciled. Do not delete these files to recover room
in a running turn.

Required startup context is charged to the first ordinary turn. Required replay
must fit whole in the remaining unreserved allowance or the hook refuses it;
it never truncates instructions. The hook reserves the entire allowance issued
for native tools because it cannot observe how much the agent actually spends.
Unused reservations therefore cannot fund required replay after compaction.
With no required context, a repeated event emits nothing.

This contains repeated hook grants. It does **not** enforce aggregate native
tool consumption: the agent must still count search/pull results and envelopes
against its issued allowance. A new native turn can receive another allowance;
a logical assignment spanning several turns is not bounded as one task. Outer
harness framing is not measured. Other harnesses retain the shared behavior
described in [Claude lifecycle](claude-lifecycle.md#optional-recall-through-the-task-agent).
Grants retain prior turn IDs without eviction to prevent old-turn replay;
their metadata grows with the session. They contain no memory bodies or prompts.
Enable this mode at the next ordinary turn boundary. The ledger cannot recover
allowances already delivered by an older engine during an in-flight turn. Its
checks also cannot detect deletion of the entire state directory. Preserve that
directory across resumes. The identity contract is present in the
[Codex 0.157.1 hook schema](https://github.com/openai/codex/blob/rust-v0.157.1/codex-rs/hooks/src/schema.rs#L567).

## Install

From the Cairn checkout, with `cairn`, `claude` and Codex installed:

```sh
python3 scripts/install-codex-hooks.py                 # ~/.codex
CODEX_HOME=~/.codex-harm python3 scripts/install-codex-hooks.py
```

The installer copies the engine to `~/.local/share/cairn/codex-hooks`. It
merges four command hooks into the account's `hooks.json` and preserves
existing hooks, including Cairn's coordination hooks. It keeps the first
original as `hooks.json.before-cairn-lifecycle`. It then trusts exactly those
four definitions through Codex's own app-server protocol, as the coordination
installer does. Rerunning it updates the installation without duplicating
hooks. Start a fresh Codex session afterward. Put `.cairn-no-memory` in a
project to opt it out, or set `CAIRN_LIFECYCLE_DISABLED=1` for one run.

## Verification

Unit tests in `scripts/test_codex_lifecycle.py` cover rollout filtering,
bounding, the Stop threshold (including a session far larger than the excerpt
window), retry after a failed capture, continuation handling, task scope and
installer idempotence. On 2026-09-23, Codex 0.156.1 accepted and trusted all
four hooks in an isolated `CODEX_HOME`. A live session in the Cairn repository
received injected "Cairn lifecycle memory" context at `UserPromptSubmit`. The
background Stop hook then ran one selection after the sixth new message, which
correctly saved nothing from a trivial exchange, and later short turns started
no further selection. That run exercised the mechanics. Whether the retrieved
memory improves Codex's work remains unmeasured.
