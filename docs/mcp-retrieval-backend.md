# MCP retrieval backend

`mcpapi.NewReadOnlyServer(backend, config)` registers exactly `cairn_search` and
`cairn_pull`, with the same tool declarations, argument schemas, normalization,
source projection, errors and encoded output limits as the ordinary server.
It requires a non-nil `RetrievalBackend` (including rejection of typed nils),
validates the existing `Config`, and copies its configured context pins.
It neither creates a local API client nor falls back to one.

The ordinary `NewServer(client, config)` still registers all its existing tools.
Its local retrieval implementation borrows that client and the same preview
capability choice used by client diagnostics and note preparation. The CLI
continues to construct this ordinary server; no new launch mode is installed.

## Backend contract

The two methods receive typed core requests and return typed core results:

```go
Search(context.Context, core.CompileRequest) (core.IndexResult, error)
Pull(context.Context, core.ExpandRequest) (core.Expansion, error)
```

The facade validates its search arguments and resolves the configured scope and
context before calling `Search`. The backend owns the complete retrieval
invocation: capability negotiation, capability-selected
`CompactPreviewEntities`, actual index dispatch, and distinct semantic-query
response checks. In the local implementation, negotiation errors prevent the
index call; the first successful capability choice remains latched. A later
rollback refuses without retrying with different semantics. `Pull` forwards the
complete expansion request, including its optional span and opening size.

The facade still applies `presentSearch` and `toolResult` after the backend
returns. Thus successful backend completion does not establish a publishable MCP
result: missing handles, projection errors or the encoded output budget can
still refuse it. Core refusal codes and refusal IDs retain the ordinary error
mapping; arbitrary transport details are sanitized. The backend must not treat
its return as proof of result delivery or native consumption.

The caller owns backend resources and lifetime. Neither constructor closes it.
Methods receive the existing invocation context; the facade does not create an
attempt deadline or extend one. A backend that retains requests/results must
account for their mutable pointer/slice aliases and release them under its own
resource contract. This interface does not supply reservations or a traffic
pool, and does not make an arbitrary backend safe for concurrent use.

## Scope of this prerequisite

Implementations must retain authenticated API and current-read checks. Config,
typed requests, tool names and implementing this Go interface do not authenticate
a principal or establish native MCP launch provenance. The read-only constructor
is a trusted embedding seam, not a containment boundary against a malicious
backend; even read operations can have the API's ordinary receipt effects.

There is no trial gate, native command policy, broker transport, origin binding,
current-attempt receipt ledger or protected credential builder in this change.
An eventual trial backend must debit the sole original attempt gate before any
preview/version/index/expand effects, enforce original deadlines and resource
limits, and bind actual results to that attempt. Store current-read validation
must still run. A separate result-publication contract must establish observed
search provenance before making pull membership usable. The current native
launch remains unavailable for that integration; no SDK control kind or native
launcher behavior is inferred here.

## Verification

The existing MCP unit and synthetic local HTTP fixtures cover ordinary preview
caching across search/preparation/client-info, probe failures, distinct semantic
queries and rollback behavior. New in-memory MCP sessions and synthetic local
HTTP fixtures exercise exact two-tool discovery, absence of other tools,
required-backend refusal, request normalization and fixed-context copying,
source-view/pull round trips, early validation, API-error preservation,
transport-detail sanitization and final envelope refusal. These tests use the
real local backend and HTTP client; synthetic token files belong to temporary
test directories. Database tests are skipped with `CAIRN_TEST_DATABASE_URL`
unset. They do not prove store integration, native origin, runtime admission,
workload fit, task benefit or full CAIRN107 acceptance.
