# Ambient memory in Claude Code

Cairn's optional Claude Code hooks retrieve memory before a task and preserve
selected context before compaction or exit. Install them once at user scope;
normal sessions in other projects use the same shared collection.

| Event | Behavior |
| --- | --- |
| `SessionStart` | Retrieve project guidance on startup or clear; on resume or after compaction, prefer this session's checkpoint. |
| `UserPromptSubmit` | Search using project, task keywords, quoted phrases and file/error hints; omit weak matches and unchanged bodies already delivered. |
| `PostToolUse` / `PostToolUseFailure` | Retain recent Read/Edit/Write filenames and diagnostic identifiers for the next search; omit tool output. |
| `PreCompact` | Select and save a checkpoint before either manual or automatic compaction. |
| `SessionEnd` | Select remaining useful context before normal exit, clear, or switching sessions. |

Retrieval delivers required selected context, optional previews with native pull
arguments, and the first optional source's full body when it fits. Optional matches must share a file association, quoted phrase or at least two task terms. At startup, project-labelled decisions and preferences also qualify. Hints expire after 15 minutes. A delivered body is suppressed at the same version until startup, resume or compaction resets the context; required selected context is always retained. Empty or weak results add no boilerplate. These lexical heuristics can miss relevant notes, so explicit search remains available. The agent can
pull other sources through its existing Cairn MCP tools. The hook and tools must
use the same authenticated principal for those handles. Session labels identify
host conversations; shared captures remain repository-scoped.

The hooks use the existing authenticated CLI and hosted profile. Installing them
explicitly enables lifecycle reads and selected writes independently of whether
the model chooses a memory tool. Ordinary MCP calls retain their own permissions.
No database or MCP server upgrade is needed.

## Install

From the Cairn checkout, with `cairn`, `claude` and Python 3.11+ installed:

```sh
python3 scripts/install-claude-hooks.py
```

The installer copies the hook into `~/.local/share/cairn/claude-hooks` and merges
its six command hooks into Claude's user `settings.json`. It preserves existing
hooks, permissions and unrelated settings, and keeps the first settings backup
as `settings.json.before-cairn-lifecycle`. Start a fresh Claude session afterward.
Rerunning the same command updates this installation without duplicating hooks.

`--settings` selects another Claude profile. `--destination` selects a separate
hook installation when profiles need different models or connection settings.
The default settings location honors `CLAUDE_CONFIG_DIR`. The extraction model is
the selected profile's configured model at installation; rerun the installer
after changing that setting. If no model is configured, Claude selects its default.
`--socket`, `--token-file` and `--repo` select an existing ordinary hosted profile
and collection. The installer never provisions credentials or changes policy.

To disable hooks for one launch, set `CAIRN_LIFECYCLE_DISABLED=1`. A
`.cairn-no-memory` file in the working directory or nearest Git root disables both retrieval and
capture there. To uninstall, remove this installation's commands from the six
hook lists; leave other hooks intact. Do not restore the entire backup over newer
settings changes.

## Selected capture

Capture makes a separate, tool-free Claude request using the existing provider
credentials and configured model. It reads only a bounded excerpt of top-level
user/assistant text from the host transcript, omitting tool payloads, reasoning
blocks and sidechains. It supplies the previous checkpoint and up to three relevant complete durable notes so the selector can retain useful context and revise superseded guidance. It saves only the selected
note, not the excerpt, transcript, or raw model response.

The selector follows the Cairn skill's criteria: meaningful owner corrections,
decisions and reasons, verified reusable fixes, and unfinished-work checkpoints.
It must skip lookup-only exchanges, routine progress, excluded content, secrets,
and private Council content. A null checkpoint and empty memory list write nothing. Durable decisions, preferences, lessons and procedures are saved separately from unfinished-work checkpoints. Supplied existing notes are revised by record ID and current version; identical bodies are skipped. New notes use stable project/topic titles. A matching topic outside the supplied candidate set requires explicit reconciliation rather than a blind overwrite. Bounded lexical discovery can still miss semantically equivalent notes with different wording. Selection remains
fallible model judgment; this is not a secret-redaction guarantee or an
independent verification of the reported work.

Unfinished work uses an ordinary shareable note named `Handoff: PROJECT / TOPIC`. The selector receives up to two relevant complete workstream notes and must reuse a matching topic across sessions. A retrieved or saved workstream title is retained in session metadata for resume. Fresh agents discover it through ordinary task search. Project identity alone does not join unrelated tasks. Existing session-UUID checkpoints remain readable as migration context. Revisions use the current version and
preserve store-owned metadata. Concurrent capture hooks for the same session
cannot run together. Explicit handoffs through the `handoff` skill use the same searchable naming convention. Matching remains fallible: bounded lexical discovery can miss a workstream, and concurrent first creation is not a uniqueness guarantee.

## Bounds and failures

- Each retrieval reserves 8,000 bytes of Cairn memory room. Delivered context text
  is capped at 12,000 UTF-8 bytes, including guidance and the optional body.
  Mandatory context is never truncated. Retrieval runs on lifecycle events, not
  on every model or tool request; it does not enforce the whole conversation's
  context budget.
- Capture examines at most the final 2 MiB of transcript bytes and supplies at
  most 24,000 bytes of serialized dialogue. A clipped boundary message is labelled.
  Older context can be missing; the previous saved checkpoint helps continuity.
- Selection has 35 seconds; individual Cairn commands have 5 seconds. Capture
  hooks have a 150-second host timeout to cover bounded candidate reads and up to four selected writes. Capture hashes the selected dialogue and selector contract before any memory/model call. An unchanged excerpt after a confirmed save or null selection skips extraction entirely. Host compaction summaries, local-command wrappers and duplicate transcript UUIDs do not count as new work. Failed writes leave the digest unchanged so another event can retry. A changed model/selector contract also invalidates the digest. Nested lifecycle hooks and transcript persistence
  are disabled for that call. Temporary working directories are removed on exit.
- Checkpoints are at most 6,000 body bytes plus their title. Identical selected
  bodies do not create revisions. Request IDs remain stable for an identical
  selected write; a changed model selection is new intent.
- Missing services, timeouts, malformed selections and refused writes report a
  labelled hook failure without preventing the user's task or claiming a save.
  Use native tools or an explicit handoff when capture fails. A stale optional
  body leaves a labelled index requiring a fresh search.

There is no continuous transcript watcher, background groomer, or guarantee of a
checkpoint after an abrupt process kill. Capture can miss tool work that has not
yet been summarized in dialogue. Session locks and bounded metadata files remain in the installed `state` directory. They retain note IDs/versions, recent filenames, diagnostic identifiers, workstream title and capture digest, without transcript or file bodies. If transcript truncation changes the bounded excerpt, an additional selection can occur even without new work.

Claude's [hook reference](https://code.claude.com/docs/en/hooks) defines these
lifecycle events. The installed behavior is checked separately in the
[native verification report](verification/claude-lifecycle-2026-09-13.md).
