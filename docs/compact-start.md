# Start a harness with compact memory

`cairn agent start` retrieves a fresh scoped index and starts a harness with that
index and the task delivered through stdin or one literal argument. It makes
memory available in the first task input, before the model chooses whether to
search. This is an explicit preload authorized by the command's caller.

Use the ordinary hosted-destination profile already configured for the harness's
Cairn tools. Those tools must use the same API principal as the launcher: another
profile cannot expand its receipt. Keep observer credentials separate. The command
refuses a local-destination index and never includes connection paths or token
bytes in generated pull instructions.

## OpenCode

Install the [native tools](opencode-tools.md#install-in-a-project) in the project,
then run from that project with your OpenCode executable on `PATH`:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" start \
  --repo "$HOME/git/cairn" --task storage-review --run storage-review-1 \
  --query 'storage tests' \
  --prompt 'Review the storage changes and explain what needs testing.' \
  --carrier stdin \
  --pull-tool cairn_pull --search-tool cairn_search \
  -- opencode run --format json
```

Use your canonical repository identity and actual task/run labels. These are
declared launcher scope, not a fabricated OpenCode session ID or observed execution
attempt. Later native searches use OpenCode's actual session scope. Existing
OpenCode configuration selects the model; you can pass its normal flags after
`--`. Cairn does not edit model settings or tool permissions.

Use stdin for OpenCode 1.18.21. Its argv path adds quotes and escapes quotation
marks, including those in the index JSON. The native stdin check verifies intact
delivery. `--carrier argv` is the default for commands that preserve their final
argument. In argv mode, the original stdin is inherited; in stdin mode, the
prepared memory/task input replaces it. Do not supply a second task argument to
OpenCode when you want the prepared input to be its complete initial message.

The preload includes full mandatory `selected` context, bounded optional previews,
source identities and complete `pull_arguments`. It labels ordinary notes as
fallible and names the supplied pull/search tools. The model can call `cairn_pull`
directly using an entry's arguments. `cairn_search` remains available for a different
topic or a fresh receipt. Configure permissions for the intended work. Denying a
pull tool still prevents that tool from being offered; it does not undo the
separately requested initial preload. A declared tool name does not prove that the
harness has loaded or authorized it.

For unfamiliar vocabulary, replace `--query ...` with `--browse`. Optional repeated
`--kind` flags narrow labels while retaining required instructions. `--semantic`
can accompany a nonempty query, using the existing optional semantic backend and
labelled lexical fallback. It cannot accompany browsing. The existing `--revision`,
`--workspace-sha256`, `--task-class`, `--task-phase`, `--binding` and `--capability` declarations are
also accepted. They describe context; they do not inspect the workspace themselves.

## Budget and lifecycle

For a saved task specification, replace `--prompt ...` with
`--prompt-file ./task.md`. Exactly one task source is required. The file is read
as UTF-8 without trimming, newline conversion or shell expansion, so trailing
blank lines and literal quotation marks are preserved. The file must be regular;
symlinks to regular files work. Paths are resolved from the launch directory.
File input leaves the original stdin available in argv mode; stdin mode still
supplies the combined memory and task to the harness. Neither the task file's
path nor its body is sent to the Cairn API. Its body is sent to the harness as
requested. Cairn leaves the source file in place.

`--tokens` defaults to 32,000 and bounds the combined initial guidance, serialized
memory and task input using Cairn's UTF-8 byte upper bound. Startup reserves room
for the task before compiling memory and checks the final presentation. The
accepted range is 256–131,071 bytes, with the upper bound keeping one argument
within Linux's conservative per-argument limit. A nonempty UTF-8 task without NUL
bytes is required. Very small budgets can refuse even when the optional index is
empty because required context and metadata still need room.
File reads are capped at this limit plus one byte to detect overflow; an
oversized file refuses rather than truncating the task. Missing/unreadable files,
directories, pipes, empty text, invalid UTF-8 and NUL bytes prevent retrieval and
launch. Correct the task source and start again; errors do not echo its contents.

The index retains its existing expiration, expansion credits and byte budget.
A long source may need an explicit span through the normal pull tool. Startup
refuses an expired index before executing; pulls recheck current source eligibility.
Each invocation retrieves with a fresh request identity. This supplies current
context at retrieval's snapshot, not continuous validity through the task.

The bound does not cover the harness's system prompt, previous conversation,
auxiliary model requests or future searches. Normal harness processing can include
initial task input in title generation. Combined budgeting across the whole task
remains open; use additional retrieval selectively.

After preparation, `start` replaces its own process with the requested executable.
The harness owns its supplied stdin, stdout/stderr, exit status and signal handling. Cairn uses
no shell to interpret arguments and removes `CAIRN_*`, `PGPASSWORD`, `PGPASSFILE`
and `PGSERVICEFILE` from the inherited environment, retaining ordinary harness
configuration. For stdin, a Linux anonymous memory file supplies the prepared
bytes without a pipe-writer process or named context file. The descriptor lives
until the harness closes it or exits; this does not promise physical memory
erasure. Unsupported kernels refuse without a disk fallback.
Cairn adds no outcome/acceptance claim.
Each invocation starts a new command; it is not an idempotent execution or recovery
interface. Use the [authenticated observer runner](authenticated-runner.md) when
host observations are required. Its [explicit index mode](observed-index.md)
keeps observer ownership while designating an ordinary reader for pulls.

Invalid intent, an unavailable executable, insufficient room, API refusal or a
local-only destination prevents execution. Preparation errors use the normal
Cairn JSON error envelope and omit prepared task/memory text. After successful
replacement, output and exit status are the harness's own.

Native OpenCode verification uses a scripted provider to check initial delivery,
direct pulls, denied tool permissions and source changes. It does not establish
model-selected use, task acceptance or memory benefit. Other harnesses with an
argv input route can use their configured tool names, but are not qualified by
this OpenCode check. See the [verification report](verification/compact-start-2026-09-09.md).
