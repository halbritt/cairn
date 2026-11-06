# HTTP API reference

The authenticated API lets clients store and retrieve memory and coordinate
agent sessions. Use the generated [OpenAPI contract](../api/openapi.json) for
complete request and response schemas. This page explains how to navigate it;
[local API](../local-api.md) documents the access rules and detailed operations.

## Transport and identity

The default local transport is a Unix socket under Cairn's data directory,
usually `~/.local/share/cairn/api.sock`. A network listener requires explicit
TLS or an explicitly configured loopback backend behind an HTTPS proxy. Network
profiles have a restricted operation set; exposing a listener does not grant
local operator access. See [multi-machine deployment](../multi-machine.md).

Operations use `POST` with one JSON request and `Authorization: Bearer TOKEN`.
The server rejects unknown request fields and trailing JSON values. Clients also
negotiate the [protocol and capabilities](../api-compatibility.md).

`Cairn-Agent-ID` and `Cairn-Execution-ID` select a registered session under the
authenticated profile. They must be supplied together; they do not replace the
bearer token or confer another session's authority.

## Responses and failures

Responses use the `cairn.response/1` envelope. A successful response has
`ok: true`, `status: "OK"`, and operation-specific `data`. A refused response
has `ok: false`, a machine-readable code in `status`, and a `message`.
`refusal_id` is present when the refusal has a retained inspection reference.
An `EVENT_ADMISSION_REFUSED` or `CLOSURE_RESERVE_FULL` refusal can also carry
`admission`: the refused `dimension`, the `policy_revision`, `retry_after_seconds`
and `retryable`. It never names other recipients or reports their counts or
remaining capacity. The API server also sends a positive hint as `Retry-After`;
the current relay forwards the JSON envelope but does not forward that header.
Use `admission.retry_after_seconds` through a relay. It is backoff guidance, not
a promise of admission; `retryable: false` requires changing the request or
policy instead of a timed retry. No other code carries `admission`. The
`protocol` field declares the responding peer's supported range.

| Status | HTTP status | Client action |
| --- | --- | --- |
| `INVALID_REQUEST` | 400; 405 for an unsupported method | Correct the request or use POST |
| `AUTHORITY_DENIED` | 401 for authentication failure; 403 for denied access | Check the configured identity and permitted operation |
| `NOT_FOUND` | 404 | Check the identifier and caller visibility |
| `VERSION_CONFLICT` | 409 | Read current state, reconcile, then submit a new mutation |
| `IDEMPOTENCY_CONFLICT` | 409 | Do not reuse a request UUID for changed intent |
| `EVENT_ADMISSION_REFUSED` | 429 | Inspect the dimension and `retryable`; retry unchanged intent with the same UUID only when capacity can recover; see [event admission](../event-admission.md) |
| `CLOSURE_RESERVE_FULL` | 429 | Drain or explicitly close obligations; if `retryable` is false, change the request or policy; see [closure reservations](../closure-reservations.md) |
| `CLOSURE_UNRECONCILED` | 409 | Wait for an operator to reconcile closures for the current restore generation, then retry |
| `RATE_UNRECONCILED` | 409 | Rate state does not match the current restore generation; report it to the operator, then retry |
| `BUDGET_REFUSED` | 422 | Reduce the requested output or make a deliberate new request with sufficient room |
| `PROTOCOL_UNSUPPORTED` | 426 | Use a compatible client/server pair |

The full mapping is in [refusal classification](../../internal/refusal/classification.go).
An error response after a transport failure does not prove that an earlier write
failed to commit. For request-ID operations, retry the same UUID and arguments.
Lease-based and read operations have separate retry contracts in the catalog.

## Find an operation

Paths are `/v1/` followed by the operation name. These are selected entry points;
the OpenAPI contract contains the complete catalog and encoded request limits.

| Task | Operations |
| --- | --- |
| Inspect builds and clients | `version`, `clients` |
| Create or revise ordinary records | `create`, `edit`, `revise`, `append`, `replace`, `set-sections`, `cite`, `delete` |
| Retrieve context | `index`, `expand`, `expand-evidence`, `compile`, `explain-page` |
| Inspect or change supersession | `preview-retract`, `supersession`, `supersede` |
| Register and locate sessions | `agent-register`, `agent-context`, `agent-heartbeat`, `agent-leave`, `agent-directory`, `agent-resolve` |
| Publish and process events | `event-publish`, `event-next`, `event-complete`, `event-retry`, `event-renew` |
| Observe delivery | `event-watch`, `event-list`, `event-inspect`, `event-group`, `event-groups` |
| Inspect own pressure and publication fanout | `event-pressure`, `event-fanout`, `event-admission-preview` |
| Inspect and close own reply obligations | `event-closures`, `event-close-replies` |

For example, session registration is `/v1/agent-register`, and publishing an
event is `/v1/event-publish`. The CLI's `ack` and `complete` commands both map
to `event-complete` with different dispositions. Some listed operations require
a local destination or are unavailable to remote profiles; check the operation's
access contract before using it.

Admission policy configuration, collection-wide diagnostics, and closure
reconciliation are operator CLI operations, with no agent API routes. The
scoped inspection routes above do not accept another recipient's identity or a
collection-wide selector. See [local API](../local-api.md#agent-event-operations)
for their request fields.

Most encoded request bodies are limited to 128 KiB. Record text operations and
event completion have larger limits; `explain-page` has a smaller limit. Limits
apply to encoded JSON, separately from decoded content and response budgets.

## Developing a client

Use [offline operation help](cli.md) to inspect a request without contacting a
server. The [Go operation catalog](../../localapi/operations.go) binds request
and response types, retry classes, and access restrictions. Run `make check` when
changing it; [API contract maintenance](../api-contract.md) describes regeneration
and compatibility rules.
