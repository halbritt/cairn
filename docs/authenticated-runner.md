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

OpenCode still requires `--destination hosted`, even when its current provider
is local. The API profile must also be configured `hosted`. The wrapper compares
the compiled destination against the declared run destination before launch;
it cannot turn a local-profile package into hosted input. Use `--carrier argv`
when invoking OpenCode's positional prompt route. See the existing harness probe
and trial reports for the tested OpenCode versions and their limitations.

## Custody and failures

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
