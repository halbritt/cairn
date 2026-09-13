# OpenCode argument validation repair

The native adapter accepted `shareable: "false"` as truthy and sent a shareable
create request. This is a concrete adapter defect: the documented option is a
JSON boolean and ordinary capture defaults to local. The repaired adapter parses
each tool's declared schema before accessing settings or invoking the CLI.
Malformed arguments now return `INVALID_REQUEST`, without echoing their values.

## Observation and correction

This session retrieved the previously saved defaults lesson
`104519b5-82cd-44b3-ba73-1def2ad42e8c`, version 1. Inspecting the execution paths
revealed that OpenCode's
[debug command](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/cli/cmd/debug/agent.handler.ts#L50)
calls tool execution directly. It cannot establish normal-session schema
validation. A new check therefore uses a normal `opencode run` session, with
scripted tool calls supplied by a local completion endpoint. No inference occurs.

On OpenCode 1.18.21, a minimal custom tool with a declared `kind` default received
no kind when omitted. Explicit kinds survived. Both the defaulted declaration and
an optional declaration also passed numeric kinds into their handlers. This
matches the normal session's
[JSON-schema wrapper and direct execution](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/session/tools.ts#L92).
The minimal default example reconstructs a development mistake; it is not a
released Cairn failure. The malformed sharing flag was a defect in the released
adapter at `a5ee749`.

The existing native-tool/API test, extended to require rejection of the malformed
sharing flag, failed before the repair: `cairn_remember` returned a newly created
record ID. Source inspection shows the truthiness conversion to `shareable`.
This reproduction used a disposable database, with synthetic content. It is not
evidence of an operational disclosure.

The fix uses each existing tool schema at its own execution boundary. This keeps
the model-facing declarations and runtime checks together. Valid requests retain
their existing behavior; unknown top-level fields and malformed argument values
are rejected. Authentication, destination policy and record-edit rules remain
owned by the API. No store migration or service restart is required.

Leaving the adapter unchanged preserves the reproduced malformed-write behavior.
Checking only `shareable` would fix that coercion but leave the other declared
argument contracts dependent on the same missing host validation. The selected
private wrapper reuses those existing schemas for all five handlers. Its scope
is argument acceptance: valid bodies, sharing flags, scopes, versions, retry
semantics and permission checks remain covered by the existing native/API tests.

## Verification

- The normal-session check observes the upstream behavior and verifies argument
  refusal for all five shipped tools before any connection-file access.
- The full native-tool/API integration passes after the fix. It rejects string,
  numeric and null sharing flags, numeric kinds, array bodies and unknown fields.
  A valid retry using the same request UUID creates the expected local note.
  Existing shareable capture, exact pulls, evidence, edits and permissions pass.
- `make check` and `make test` pass. TypeScript executes in the actual harness;
  no standalone TypeScript typecheck is claimed.
- The repaired adapter is installed in the local project. Its native edit tool
  revised the original lesson to version 2, and a fresh native Codex client
  retrieved that exact body and version. Neither client operation started a
  model turn.

The runnable check is [check_opencode_defaults.py](../../scripts/check_opencode_defaults.py).
The [manifest](opencode-validation-2026-09-09.json) retains binary/source hashes,
the failed pre-repair run, the successful checks and their artifact locations.
The initial scripted-endpoint attempt mistakenly consumed a tool case during
OpenCode's title request; its report was rejected and its raw output is retained.
The corrected check separates title requests from tool-bearing requests.

This work applied recalled guidance to discover and fix an adjacent defect. It
does not establish independent model use, a controlled memory advantage or broad
task acceptance. Those usefulness requirements remain open.
