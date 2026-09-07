# Cairn

Cairn is a local memory subsystem for agents. It aims to carry useful, current
knowledge between runs while keeping ordinary notes, evidence-backed claims,
and binding instructions distinct.

**Status: experimental foundation; part of Stage 1.** The PostgreSQL store and
local JSON CLI work. The context compiler, authority transitions, evidence
resolver, and OpenCode wrapper are still to be built. This version is for
synthetic fixtures and development, not production memory or secrets.

See the [design evaluation and next steps](docs/design-review.md) and the
[source design](docs/sources/agent-memory/design/agent-memory-design.md).

## Run the verified development path

Requires Go 1.25 or later and PostgreSQL 16 or later, including `initdb`, `pg_ctl`,
and `createdb`. Run as an ordinary user; PostgreSQL refuses to run as root.

```sh
make build
make test-integration
make check
```

`test-integration` creates a private temporary PostgreSQL cluster, runs the tests
with Go's race detector, exercises the CLI, and removes the cluster. It opens no
TCP listener and does not use the host's running PostgreSQL service. Set
`CAIRN_PG_BIN` to a PostgreSQL binary directory if `pg_config` selects the wrong
installation. Dependencies are pinned in `go.mod` and `go.sum`; initial module
and toolchain downloads require an enabled Go module proxy.

`make test` runs unit tests and explicitly skips database tests unless
`CAIRN_TEST_DATABASE_URL` is set. Only point that test variable at a disposable
database: tests install Cairn's schema and retain synthetic rows there.

## Use a dedicated development database

Once you have created a separate development database:

```sh
export CAIRN_DATABASE_URL='host=/var/run/postgresql dbname=cairn_dev'
bin/cairn migrate
bin/cairn create < fixtures/note.json
bin/cairn get RECORD_UUID
```

`create` returns the UUID. Use a new `request_id` UUID for each intended write;
reuse it for a transport retry. Identical retries return the original response,
even if the record has since changed. `get` returns current state.

`edit` accepts JSON containing `request_id`, `record_id`, `expected_version`, and
a complete `draft` of the same shape as the fixture. A competing edit returns
`VERSION_CONFLICT`; a changed body under the same request UUID returns
`IDEMPOTENCY_CONFLICT`. Exact scope is fixed for this initial slice.

The CLI returns `cairn.response/1` JSON. Exit codes are 0 success, 2 invalid
request, 3 missing record, 4 conflict/schema mismatch, 6 authority denial, and
7 database failure. Database failures never return an empty success response.

## Trust and current limits

The CLI is a local development/administration surface. Its writer is the OS
effective UID and its witness is always `testimony`. It has no caller-selected
principal, instrumentation, promotion, or instruction flag. OS identity does
not distinguish several agents running under the same account.

The Go `core` package is for a trusted embedding host. The host supplies a
`Channel` after authenticating its caller, keeps database credentials away from
agents, and uses an instrumented channel only for observed service events.
`Channel` is not an authentication implementation. Never deserialize it from an
agent's request. The production orchestrator transport remains an open contract.

All current records are local Class A advisory material. Reads are local
inspection, with no destination or consequential-use contract. There is no
automatic capture, pruning, model call, background timer, or live harness
integration. Do not expose this CLI or library directly to untrusted callers.

## Implemented

- Transactional, checksummed initial migration and exact current-version links.
- Store-stamped writer, witness, time, and transaction identity.
- Create, retained revisions, optimistic concurrency, and durable retry identity.
- Service-owned spawn and terminal events, with exact attempt/result matching.
- Visible unreconciled/contradicted attribution and an idempotent correction docket.
- One instrumented Class A failure observation per failed attempt, even when
  nobody submits a completion claim.

The integration suite tests real PostgreSQL behavior. Passing it verifies this
slice; the complete Stage 1 acceptance suite and memory usefulness evaluation
remain outstanding.
