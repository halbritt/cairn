# Agent API contract

[`api/openapi.json`](api/openapi.json) is the machine-readable contract for
Cairn's agent API: the local Unix socket and the central network listener that
relays forward to. It is an OpenAPI 3.1 document using JSON Schema 2020-12. It is
generated from source; do not edit it by hand.

```sh
make contract   # regenerate docs/api/openapi.json
make check      # fails if the committed contract is stale
```

`go test ./internal/apicontract` also runs the drift check. With
`CAIRN_TEST_DATABASE_URL` set (as `make test-integration` does), it also runs
conformance tests against real handlers.

## How it is generated

`internal/apicontract` parses the non-test files of `localapi` that the default
build selects (`go/build` file matching, so build-tagged variants such as
`cairn_protocol_min2` are excluded). It type-checks them with `go/types`. No
hand-kept inventory is involved.

- **Routes.** Every `case "/v1/OPERATION"` clause must contain exactly one
  `serveJSON` call. The operation's request type is the handler's second
  parameter, and its response type is the handler's first result. If a handler
  returns `any` (as `recompile` does), the contract uses the single concrete
  type of its return values. A route that breaks these rules stops generation.
- **Schemas.** Go types are mapped by `encoding/json` rules: json tags,
  `omitempty`/`omitzero`, `-`, embedded-struct promotion and conflict
  resolution, and `,string`. `time.Time` is a `date-time` string,
  `json.RawMessage` and interfaces accept any value, and `[]byte` is base64. Nil
  pointers, slices and maps are `null`. A type with a custom JSON or text
  marshaler stops generation until someone writes an explicit schema for it.
- **Errors.** `x-cairn-errors` combines serveJSON's code-to-status switch with
  every literal `writeError(w, status, code, ...)` call, including the relay's
  transport errors. `x-cairn-store-codes` lists every literal store error code in
  `core` and `localapi`. A store code that serveJSON does not map is returned with
  `x-cairn-default-error-status` (422). Some codes are built at runtime, so the
  set is open: clients must treat any unknown code as a refusal with that code.
- **Classifications.** For each operation the contract records:
  - `x-cairn-remote`: the live remote allowlist, read through
    `localapi.RemoteOperation`.
  - `x-cairn-remote-roles`: the roles a remote profile may call it with.
  - `x-cairn-requires-local-destination`: set when the handler has an
    `if !...AllowLocal { writeError(403) }` guard.
  - `x-cairn-session-headers`: whether session headers are optional or
    forbidden.
  - `x-cairn-request-limit-bytes`: the value of `localapi.RequestBodyLimit`.
  - `x-cairn-retry`: the retry class; see below.
- **Headers.** `x-cairn-request-headers` lists every header the server reads
  from an incoming request, whether by literal or constant name, including
  names passed to helpers with `r.Header`. Generation fails if one of them is
  not documented as a parameter or as the bearer scheme.

## Two request views

The request body schema describes what the server actually **accepts**. Every
field is optional and nullable. Unknown fields are refused, and a top-level
`null` decodes as an empty request. `x-cairn-canonical-request` is the
**canonical** shape that clients should send. Three decoder behaviors are not
expressible in the schema:

- keys match case-insensitively (`{"RECORD_ID": ...}` works);
- if a key appears twice, the last value wins;
- an absent field and `null` both decode as the Go zero value, and then fail or
  succeed on the operation's own validation.

The schema describes structure. Domain constraints are not schema guarantees,
and they are checked after decoding by core validators and store state. Examples
are canonical UUIDs, length bounds, enumerated strings such as `kind`,
`sensitivity` and `state`, record existence, leases, sessions, collection and
destination eligibility, and restore fences. A request that matches the schema
can still be refused, typically with `INVALID_REQUEST` (400) or a 409 state
code.

Responses use the canonical view. Fields without `omitempty` are always present.
Response schemas stay open to additional properties, because new response fields
are compatible additions and clients must ignore fields they do not know.

## Requests

- **Method and path.** Every operation is `POST /v1/OPERATION` with one JSON
  body and `Content-Type: application/json`. Requests without POST get 405.
- **Authorization.** `Authorization: Bearer TOKEN`, at most 512 bytes. The
  token's configured profile fixes the principal, collection, role (`agent` or
  `observer`) and destination (`local` or `hosted`). No request field changes
  them.
  - A hosted destination never returns local-only records; `get` answers
    `NOT_FOUND`.
  - Operations marked `x-cairn-requires-local-destination` refuse hosted
    profiles with 403.
- **Remote profiles.** A profile marked remote may call only operations whose
  `x-cairn-remote` is `allowed`, and only with a role listed in
  `x-cairn-remote-roles`. This is enforced on both the network listener and the
  Unix socket. The network listener refuses every profile that is not remote.
- **Session headers.** `Cairn-Agent-ID` and `Cairn-Execution-ID` together act as
  a registered session's inbox. A replaced or restored execution gets
  `STALE_SESSION`. The headers are forbidden on `agent-*` directory operations,
  which return 400.
- **Limits.** Request bodies are limited per operation. Responses are limited to
  `x-cairn-limits.response-body-bytes` (8 MiB). The server spends at most 30 s
  per request.

## Retry classes

Relays never replay a request. If a call's outcome is unknown, the relay
answers `UPSTREAM_UNCERTAIN` (504). `UPSTREAM_UNAVAILABLE` (502) means the relay
sent nothing. After a failure, the caller applies the operation's
`x-cairn-retry` class:

| Class | Meaning |
| --- | --- |
| `read` | No durable effect; repeat freely. |
| `request-id` | Keyed by `request_id`. Repeat only with the same ID and identical arguments; a committed result is returned again. A different body under the same ID gets `IDEMPOTENCY_CONFLICT`. |
| `idempotent` | Repeating has the same effect as one call (presence heartbeat, leave, worker heartbeat). |
| `lease` | Keyed by delivery and lease. A repeat after success gets `STALE_LEASE`, which does not prove failure; inspect event status. |
| `no-key` | Durable effect with no retry key (`event-next`, `claim-run`). Do not repeat automatically; inspect state first. |

An operation whose request has `request_id` is classified `request-id`. Every
other operation must be listed in `internal/apicontract/retry.go`, or
generation fails.

## What the tests establish

The tests with no database check that the committed file matches a fresh
generation, that generation is deterministic, and that every operation has a
remote, retry and limit classification. They also pin decisions that matter for
retry safety and check the two request views on sample messages.

With a disposable database, `conformance_test.go` checks the contract against
the real handlers. It also checks the protocol header, relay header and body
guard cases against both the server and the contract, and confirms that the
same `request_id` sent with and without protocol metadata commits one record.

- It validates real success and error replies from representative operations
  strictly against the generated schemas; the error code must also be in the
  inventory. The operations are version, create, get, history, index, agent
  register, heartbeat, directory and resolve, event publish, list, metrics,
  subscribe and watch, session readiness and retraction preview.
- For every operation, it checks that an unknown field is refused by the
  decoder, that `null` and `{}` pass decoding, that a body over the documented
  limit is refused, and that case-insensitive keys work.
- For every operation, it calls the real server to check the remote allowlist
  (agent and observer), the refusal of local profiles on the network handler,
  the local-destination guards and the session-header rule.

The tests do not establish that every field of every response type occurs in
practice, or that every documented error is reachable. They also do not model
the domain constraints described above.

## Protocol version

`x-cairn-protocol` records the default build's supported range (`min`, `current`),
the `Cairn-Protocol` and `Cairn-Relay-Protocol` headers and the reserved
`cairn_protocol` body guard. Every envelope, success and error, carries the
replying party's `protocol` range. The accepted request view of every operation
includes the optional guard field (an integer of at least 1), because the server
verifies and removes it before strict decoding. The canonical views omit it: only
a client whose minimum protocol is above 1 sends it. [api-compatibility.md](api-compatibility.md)
defines the compatibility policy and the change rules; this document does not
repeat them.

A test-only protocol build (`-tags cairn_protocol_min2`) serves a different
range, so the contract tests skip under it rather than describe it.

## Validation

The tests validate with `github.com/google/jsonschema-go` (draft 2020-12), not a
validator written for this generator. They pass the component schemas as `$defs`.
Conformance tests close generated struct schemas (`additionalProperties: false`)
so a reply field the contract does not describe fails. The published contract
keeps responses open.

Compatibility and versioning rules are defined in
[api-compatibility.md](api-compatibility.md).
