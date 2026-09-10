# Saved specifications in observed runs

`cairn run` and authenticated `agent run` now accept `--prompt-file`, including
fresh and retained body/index execution. This closes a mismatch with ordinary
`agent start`, which already accepts saved tasks. It avoids shell substitution's
trailing-newline loss and keeps file contents separate from retrieval intent.
The [command contract](../authenticated-runner.md#read-a-saved-task) records exact
limits and defaults. This is a delivery improvement; task benefit is unmeasured.

## Observations and checks

Baseline source was `552a2b2`, with clean `20b7574` installed. The first real
CLI/API check failed because `--prompt-file` did not exist. The implementation
shares the existing regular-file reader with ordinary startup. Files are bounded
before retrieval, validated without conversion, and forwarded through the existing
runner. API requests, authority, database schema and native tool schemas remain.

The boundary test exposed a second failure: 131,072 task bytes plus memory passed
runner validation, consumed the launch claim and failed process creation through
argv. The runner now rejects a combined argument over 131,071 bytes before
binding. The test then reuses that same retained body receipt through stdin.
This is a deliberate change from late process failure to early budget refusal.
It does not promise to preflight the child's other arguments or environment.

Verification covers:

- Go refusal checks for empty, whitespace, NUL, invalid UTF-8, oversized, missing,
  directory, device and FIFO input, plus conflicting task flags before file open.
- Real children through the authenticated Unix API, with client database access
  disabled: fresh and retained body/index delivery through stdin and argv, exact
  CRLF/Unicode/quotes/trailing newlines, query digests and retained receipt identity.
- Relative paths with a different child directory, symlinks to regular files,
  direct operator execution, canonical context files excluding the task, and the
  maximum-size argv refusal followed by successful stdin execution.
- Native OpenCode 1.18.21 with a scripted provider: observed allowed, denied and
  stale-source cases preserve the selected task's added trailing line breaks.
  Ordinary startup cases also pass. These runs make no answering-model inference.
- `make check`, `make test` (Go and 40 Python tests), full disposable PostgreSQL
  race integration and CLI/stdio workflows. A final API-only rerun includes the
  last operator/symlink assertions added during the integration run.

Scratch logs and native request captures remain outside Git under
`/tmp/cairn-run-task-file-*`. [Evidence metadata](run-task-file-2026-09-09.json)
retains artifact hashes and doctrine provenance. No operational memory bodies,
tokens or raw harness sessions are committed.

## Decision and limits

The owner delegated implementation decisions toward useful cross-harness memory.
The selected change extends the existing command and reader. Leaving the command
unchanged preserves an observed saved-specification mismatch; duplicating file
handling would create two owners of the same checks. Sending a file's contents
as the default retrieval query would entangle task delivery with memory lookup.

Preserved behavior includes inline prompt defaults, retained intent checks,
observer ownership, currentness and destination gates, canonical context storage,
existing index limits, and separate process outcomes. Combined argv overflow is
the intentional exception. The file's bytes are read once but are not a filesystem
snapshot against concurrent writers. Task evidence capture remains explicit.

Only the CLI needs installation for this change; the API can stay on `20b7574`.
Stop or revise the implementation if exact delivery, retrieval separation or
preclaim refusal fails. Revisit input limits when an actual harness requires a
different transport. Aggregate budgets across memory expansions, later searches
and model turns remain open. No score, task assessment or causal memory-value
claim follows from these checks.
