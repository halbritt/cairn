# Ambient memory in OpenCode

The optional native plugin uses Cairn's shared lifecycle engine for selective
retrieval, separate durable notes, named workstreams and unchanged-capture
suppression. Install it beside the existing native Cairn tools:

```sh
python3 scripts/install-opencode-hooks.py
```

It reads `~/.config/opencode/cairn.json`, copies its connection, declared scope
and context settings, and installs `plugins/cairn-lifecycle.ts` plus
`cairn-lifecycle.json`. The Python engine and session metadata live in
`~/.local/share/cairn/opencode-hooks`. Existing tools, permissions and other
plugins remain intact. Rerun after changing the native connection settings.
If `cairn opencode-install --project "$PWD"` created a project-local
`.opencode/cairn.json`, pass `--config-dir "$PWD/.opencode"` to read that
connection instead. `--destination` supports a separate plugin installation.
The selector uses installed Claude with its existing provider credentials; `--claude` and
`--model` select that executable and model. The default model comes from the
primary Claude profile at installation. This does not switch OpenCode's task model.
Start a fresh OpenCode process after installation or update.

Installation explicitly enables ordinary hosted lifecycle reads and selected
writes. They run independently of model-selected memory tools; native tools keep
their normal permission checks. Use the same authenticated profile for lifecycle
retrieval and native pull. Repository-scoped saved notes remain shared across
agents and projects. Session labels use OpenCode's actual session ID; declared
native task/run labels are retained.

## Host boundaries

The plugin transforms task request messages, without modifying persisted user
messages or adding memory to auxiliary title requests. Each new owner message
triggers selective search. A bounded set of memory blocks remains in subsequent
request copies, so tool continuations retain context without accumulating copies.
Combined memory context is at most 12,000 UTF-8 bytes per request. Replaced versions
and evicted blocks become eligible for fresh retrieval. At most 128 owner sessions
are tracked per process; child sessions do not trigger automatic memory.

Successful read/edit/write paths and diagnostic identifiers from failed tools
supply the same short-lived hints as Claude. Before compaction, the plugin selects
a checkpoint and resets its request context. The compaction request itself does
not acquire memory; subsequent task requests can retrieve it again.

`session.idle` queues selected capture at turn completion. Disposal waits for
queued capture to finish. Each session serializes memory operations. Capture reads
up to 64 recent host messages, omits tool/reasoning/synthetic/compaction-summary
content, and passes bounded dialogue directly to the Python process without a
transcript file. The selector and save criteria are shared with
[Claude lifecycle memory](claude-lifecycle.md). Completed selection, including
null, suppresses duplicate extraction for unchanged dialogue. A normal idle turn
can incur a separate model call; an abrupt kill can still lose a checkpoint.

Set `CAIRN_LIFECYCLE_DISABLED=1` for one launch, or create `.cairn-no-memory` in the
working directory or Git root, to disable reads and capture. To uninstall, remove
only `plugins/cairn-lifecycle.ts` and restart OpenCode. Memory-service and selector
failures report a labelled warning; use explicit native tools when needed.

The [earlier system-hook investigation](verification/opencode-startup-hook-2026-09-09.md)
remains valid for that hook. This implementation uses the explicitly authorized
message boundary instead. Pinned OpenCode 1.18.21 sources show the
[task transform](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/session/prompt.ts),
[compaction boundary](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/session/compaction.ts)
and [plugin disposal](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/plugin/index.ts).
The [verification report](verification/lifecycle-improvements-2026-09-13.md)
distinguishes native checks from synthetic plugin checks and measured usefulness.
