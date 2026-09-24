# Ambient memory in Codex

Cairn's optional Codex hooks give Codex the automatic retrieval and checkpoint
capture that Claude Code already has. They use the shared lifecycle engine,
`integrations/lifecycle/memory.py`, in the same shared collection.

| Codex event | Behavior |
| --- | --- |
| `SessionStart` | Retrieves project guidance on startup or clear. On resume or after compaction, it prefers this session's checkpoint. |
| `UserPromptSubmit` | Searches with the prompt's task terms, quoted phrases and hints, and adds matches as additional context. |
| `PreCompact` | Selects and saves a checkpoint before manual or automatic compaction. |
| `Stop` (async) | Offers a checkpoint in the background once at least six new top-level messages exist since the last capture. |

Codex caps `SessionEnd` at a one-second budget. That is too short for checkpoint
selection, so the Codex adapter does not capture at exit. The background Stop
hook covers ordinary sessions without delaying the turn. The engine records a
digest of the newest captured message rather than a count, because it reads a
bounded recent window of the rollout. A failed capture leaves the marker where
it was, so the next Stop retries. A Stop continuation (`stop_hook_active`) is
not treated as a new boundary.

The transcript reader keeps only top-level `user` and `assistant` message
items from the Codex rollout. It omits developer instructions, injected
`AGENTS.md` and environment context, reasoning, tool calls and their output.
Codex now performs edits through its generic `exec` tool instead of
`apply_patch`, so the adapter does not collect file hints from tool use.
Quoted identifiers and task terms still guide retrieval.

Checkpoint selection uses the same Claude Code selector as the Claude,
OpenCode and Hermes adapters. The installer defaults its model to
`claude-sonnet-5`, and each selection runs one isolated `claude --print` call
with no tools. Retrieval never calls a model.

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
