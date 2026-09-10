# Run a harness through an authenticated observer

`cairn agent run` runs the existing H0 process wrapper through Cairn's Unix API.
The wrapper needs an observer token and socket access. It does not open PostgreSQL
or use operator authority. The child process runs in the invoking host; the API
server never executes a command supplied over HTTP.

Use a provisioned `observer` identity scoped to the repository and destination.
Keep its token separate from an ordinary agent's token. The server derives the
principal, observation role and destination from its private configuration; flags
cannot choose a different principal or elevate an agent profile.

The owner's installation has separate `observer.token` (local destination) and
`hosted-observer.token` (hosted destination) profiles. A hosted OpenCode launch
uses the latter with `--destination hosted`. Its MCP process uses the separate
ordinary `hosted-agent.token`; do not pass the observer token to the model.
The [native MCP task report](verification/mcp-host-use-2026-09-08.md) records this
setup and the resulting retrieval/outcome associations. Hosted observer access
does not expose the protected `use-report`; operator inspection remains separate.

```sh
cairn agent --token-file ~/.local/share/cairn/observer.token run \
  --repo /path/to/repository --dir /path/to/repository \
  --task TASK_ID --run RUN_ID --request-id REQUEST_UUID \
  --task-class repair --binding YOUR_BINDING --capability YOUR_CAPABILITY \
  --destination local --prompt 'The task to execute' -- COMMAND ARGS...
```

Token/socket flags precede `run`; the usual run flags follow it. Defaults and
carrier behavior match `cairn run`. Process output stays on stdout/stderr; the
final receipt envelope goes to stderr. A nonzero child exit returns CLI exit 1,
a timeout 124 and cancellation 130. These are process observations, not task
acceptance. Use stable task/run IDs from the host and retain the receipt beside
its own attempt identity. If the host has already recorded that exact attempt,
use [host attempt linkage](host-attempt-link.md) with `--attempt-id ATTEMPT_UUID`.

For memory captured before dispatch, use [retained execution](retained-execution.md)
with `--receipt-id` and `--seal`. It consumes the exact owned package without
recompiling, while preserving the same launch and outcome checks.

OpenCode still requires `--destination hosted`, even when its current provider
is local. The API profile must also be configured `hosted`. The wrapper compares
the compiled destination against the declared run destination before launch;
it cannot turn a local-profile package into hosted input. For OpenCode 1.18.21,
use `--carrier stdin` to preserve literal JSON. The positional prompt route adds
quotes and escapes. [Observed index mode](observed-index.md) supplies bounded
previews with ordinary-profile pulls; body compilation remains the default.

The existing [command and delivery digests](use-outcome-loop.md#command-and-delivery-digests)
identify different parts of the invocation. The command digest excludes the
combined memory/task input added by the selected carrier.

Use repeatable `--artifact LABEL=PATH` to [fingerprint selected task files](run-artifact-evidence.md)
after the process stops. The manifest retains labels, sizes and digests, with
local sensitivity by default. Task assessment remains separate.

## Read a saved task

Use `--prompt-file path/to/task.md` in place of `--prompt` for either
`cairn agent run` or `cairn run`. This works with fresh and retained body packages
and observed indexes. For example, replace the prompt in the command above with:

```sh
--prompt-file docs/tasks/storage-review.md --query 'storage tests'
```

The file must be regular, nonblank UTF-8 without NUL bytes, at most 131,072 bytes.
Symlinks to regular files are accepted. Relative paths resolve from the CLI's
working directory; `--dir` selects the child's working directory. Cairn preserves
CRLF, Unicode and trailing newlines without shell expansion. Supplying both task
flags refuses, even if the inline prompt is empty. Invalid files refuse before
memory retrieval or launch binding.

File input defaults to an empty retrieval query. Choose `--query` explicitly, or
use `--browse` with index mode; Cairn does not implicitly send the task file's
contents or path to the memory API. Existing inline prompt query defaults remain.
Retained execution still requires matching retrieval intent. The task text is
forwarded to the child and included in the delivery digest; `context.txt` retains
only the canonical memory package. This option does not capture task evidence.

| Delivery | Input limits |
| --- | --- |
| Body package through stdin | Task up to 131,072 bytes; memory has its separate compiler budget. |
| Body package through argv | Task limit above, plus combined memory, separator and task at most 131,071 bytes. |
| Observed index through either carrier | Combined guidance, index and task at most 131,071 bytes; initial memory also has its compiler budget. |

Oversized combined argv input now refuses before binding or claiming the launch.
An unused retained body receipt can therefore be retried through stdin. The check
bounds the argument Cairn adds; other command arguments and environment limits
can still cause process creation to fail. These are initial delivery limits, not
aggregate accounting across later searches, expansions and model turns.

The file option and argv preflight require a CLI update only. They use the
existing API and database contracts. [Verification](verification/run-task-file-2026-09-09.md).

## Custody and failures

Before claiming a launch, Cairn [rechecks the retained memory](launch-freshness.md)
against current eligibility and required context. A stale package refuses before
the child starts; obtain a fresh package with a new request ID. Newly optional
notes do not rerank or change the retained package.

The host registers its locked, owned context directory through observer-only
`register-context` before writing `context.txt`. The existing core gates require
an owned, claimed, current receipt and available payload. Normal operator purge
uses that registered copy and its ownership marker. The new endpoint records
filesystem observations; it does not write or remove files on the host's behalf.
This remains a cooperative same-UID boundary, not isolation from hostile local
processes that can access the owner's files.

The local context file contains the rendered memory package, excluding the task
prompt. The host forwards the combined package and transient prompt to the child,
records delivery/output digests and process outcome, and strips `CAIRN_*` and
PostgreSQL credential environment variables before execution. No plaintext API
token is placed in the child's command or environment by this wrapper. A caller
must still avoid placing its own credentials in command arguments or task text.

The client never automatically retries an API request. If the launch-claim
response is lost, it returns the known receipt/seal without launching a child.
The same run request cannot authorize a second process. Inspect the receipt with
[owner-only run status](run-status.md), available to both local and hosted profiles.
A status read is historical evidence, never permission to launch; creating a fresh
request ID is a new launch, not transport recovery. After a process finishes, an unconfirmed outcome remains in
`outcome.pending.json`. Retry that exact JSON under the same observer profile:

```sh
cairn agent --token-file ~/.local/share/cairn/observer.token outcome \
  < /path/to/receipt/outcome.pending.json
```

A confirmed retry returns the original observation ID if the server had already
committed it. Preserve that confirmation with the host's recovery evidence.
Historical outcomes remain valid after a restore fence or policy change; fresh
launch, binding and context registration still obey those gates.

This provides authenticated H0 process observation and delivery for ordinary
host invocations. It does not wire Striatum's sealed inputs, infer internal harness
tool events or compaction, prove instruction obedience, or turn process exit into
an acceptance decision. See [verification](verification/authenticated-runner-2026-09-08.md)
and [local API](local-api.md).
