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

Capture events serialize on a separate session `.capture.lock`. They hold the
ordinary session lock only to snapshot state and commit capture metadata, so a
prompt can recall while background selection and note writes run. The commit
preserves newer recall, hints and grant state; changed binding or capture fields
refuse stale metadata, and a newer workstream is retained. Short session-lock
contention can still refuse a bound recall; known required-context failures keep
their existing refusal behavior. Unbound Codex recall still skips on contention.

Final capture commit alone can wait in at most 50 ms sleeps for up to five
seconds. It also stops waiting five seconds before the known 150-second capture
budget, measured from the original handler start. That budget applies to the
installed Codex Stop/PreCompact and Claude PreCompact/SessionEnd hooks, and the
Hermes/OpenCode capture adapters. Snapshot acquisition and recall remain
nonblocking. Codex SessionEnd is not installed and has a shorter native limit;
it and unknown-budget routes retain immediate commit refusal. This bounds lock
waiting, not filesystem latency or arbitrary process startup time.

Note writes retain the earlier event's snapshot scope. They are not rolled back
if a later write or local state commit fails; only fully confirmed capture
advances the marker. The hook does not internally repeat selection or note
writes. Identical writes
retain deterministic request IDs and revisions retain expected-version checks,
but a new selector result is not an exactly-once capture. During upgrade, let old
capture invocations finish before switching engines: old writers do not know the
new capture lock. Field comparisons detect conflicting capture metadata; they
are not a general mixed-version fencing protocol.

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

An opt-in Codex `agent_tools` lifecycle trial restores one current unfinished
workstream checkpoint after a compact or resume `SessionStart` when that turn's
optional grant was already consumed. Enable it for a fresh session with:

```sh
python3 scripts/install-codex-hooks.py --recall-mode agent_tools --context-lifecycle compact/1
```

Reruns preserve the setting. Use
`--context-lifecycle full` to return to the prior path. The trial uses current
hosted search and an exact whole-source pull. It never credits a partial span,
an older version, a completed checkpoint, or a body that fails the emission
budget. When a named checkpoint cannot be confirmed, it gives a short current
search cue. Required instructions still refresh whole first. The ledger records
`required_refresh_bytes` and `checkpoint_refresh_bytes` separately; these are
serialized delivery bytes, not provider tokens or proof the model retained the
content. On a bound Codex request, the same opt-in emits the native
`finish_reply` command instead of two result-parsing commands. Existing native
context retains the separate commands for rollback and recovery.

For Codex `agent_tools`, an ordinary `UserPromptSubmit` with canonical native
session and turn UUIDs can issue one optional recall allowance per native turn.
`SessionStart` never opens an allowance: by default startup, resume and
compaction deliver only required context, or no additional context when none is
required. A wake
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

## Optional invocation trace (diagnostic window)

The lifecycle engine (`integrations/lifecycle/memory.py`, used by every harness) can record each invocation's fixed metadata before it reads its configuration or takes the session lock. Tracing is **off** unless the directory `invocation-trace/` exists beside the configuration file passed with `--config`, owned by you, with mode `0700`. The engine never creates it.

- **Enable for a bounded window only.** Run `mkdir -m 0700 <config dir>/invocation-trace`, and write down the time you enabled and removed it. Nothing else records that tracing was on.
- **One file for every session using that configuration.** `trace.jsonl` (mode `0600`) has `entry`, `parsed` and `exit` lines joined by a random `inv`. When reporting, filter to your own session and turn, and do not copy other sessions' lines.
- **What it holds.** Fixed labels (`outcome`, `cause`, `phase`, `stage`, `error_class`), canonical session and turn UUIDs, timings, pid/ppid, `main_return_code` (the value `main()` returned, not the process exit status), and the delivered context byte count. It never holds prompts, paths, record text, tokens or exception text.
- **Missing lines are unknown.** Writes are dropped, never retried or waited for, on trace-file contention, on failure, and when the size plus the record would exceed 8 MiB. Near the cap, any absence may be a cap drop.
  - An entry with no exit, or no entry at all, does not show that a process died or was never started.
  - `cause: lock_busy` proves only that a non-blocking session-lock attempt failed; it does not identify the holder.
  - `engine_completed` means only that the engine's normal path returned; it is not evidence of useful recall or host receipt.
- **No rotation or pruning.** To reset, remove or rename the file between windows. Filesystem latency is not bounded, so tracing adds a little time per invocation.

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
