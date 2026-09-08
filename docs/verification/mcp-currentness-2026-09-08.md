# MCP retrieval context, 2026-09-08

MCP hosts can now supply the same revision, workspace, task-class, binding and
capability declarations supported by `agent search`. Previously the facade
omitted context from its index request, so it could not retrieve memories that
required these pins. See the [startup flags](../mcp.md).

The change forwards existing `core.ContextPins`; the core continues to validate
values and decide applicability. The server copies the startup declarations,
and tool arguments cannot override them. With all five flags empty, requests
continue to omit the context object. No database migration or API change is needed.

The public stdio check was written before the implementation. Against baseline
`af8caaf`, existing MCP checks and missing-context withholding passed, then the
first context-configured launch failed. With the implementation:

- All five matching declarations appear in the returned semantic context and
  permit retrieval of the exact pinned memory body.
- Omitting each declaration produces `CONTEXT_MISSING`; changing each one
  produces `CURRENTNESS_MISMATCH` and withholds the optional memory.
- An invalid revision returns `INVALID_REQUEST`; a tool-supplied context field
  is rejected by input validation.
- A mandatory pinned instruction refuses a context-free search with
  `POLICY_UNENFORCEABLE`, and appears in full when declarations match.
- The existing capture, pull, retry, destination-filtering and clean-EOF checks
  still pass without HOME or usable client database access.

`make test-integration` passed using disposable PostgreSQL and the race detector;
`make check` and all 17 Python unit tests passed. The stdio checks use an
independent JSON-RPC client against the compiled executable and authenticated API.
These are synthetic applicability checks. No new model trial, task improvement,
host-run association or physical-workspace attestation is claimed.

The [metadata](mcp-currentness-2026-09-08.json) records the bounded doctrine packet
and residual obligations. Repository precedence, existing behavior preservation
and evidence before intervention informed this additive change. Remaining
obligations concern broader boundary, interface and cost claims outside this
change; the local decision record classifies each one.

## Installed CLI

Code commit `cda676257538b9f5b6ca415ea7caacd57ae5e243` passed
[CI run 34277793158](https://github.com/halbritt/cairn/actions/runs/34277793158).
Its clean build is installed at `~/.local/bin/cairn`, SHA-256
`e2b67b2b7061db1991f2d25ebe58e5b61ab57d80f26137f4d6be3da7ed4ab3e9`.

Against the running API, an independent stdio client verified the declared
revision and task class in the returned context, retrieved the exact existing
MCP procedure and checked an identical pull retry. It ran without HOME or a
usable client database address and finished with clean EOF. The API remains on
its earlier `3491974` build; no service restart, identity-profile change or
database migration was needed. Both services remained active.
