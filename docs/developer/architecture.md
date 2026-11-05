# Cairn Architecture & Code Layout

Cairn is engineered as a transactional memory engine and multi-agent coordination system. It combines a PostgreSQL operational store, a flat package structure, an authenticated local API, and a Model Context Protocol (MCP) stdio facade.

---

## 1. Package Layout

Cairn follows a flat root package organization. In accordance with project conventions, there is **no `pkg/` directory**.

| Package | Purpose & Responsibilities |
|---------|----------------------------|
| `core/` | Core transactional engine, schema migrations (`core/schema/`), record models, ranking algorithms, compiler, index packaging, and lifecycle slices |
| `artifacts/` | Artifact storage, verification digests, and binary evidence encoding |
| `runner/` | Execution runner, process supervision, and command invocation wrappers |
| `localapi/` | Authenticated HTTP/REST API server, declarative operation catalog, protocol admission, client observation, and session routing |
| `mcpapi/` | Model Context Protocol (MCP) stdio facade, tool profile compaction, and output budgeting |
| `semantic/` | Semantic embedding interfaces, vector comparison, and persistent ranking integration |
| `cmd/cairn/` | Command-line entry points, subcommands, flag definitions, and operator tools |
| `integrations/` | Native harness adapters (e.g. `integrations/opencode/`) |
| `internal/` | Private subsystem utilities (`apicontract`, `buildinfo`, `claudechannel`, `jsontext`, `refusal`, `wakeup`) |

---

## 2. PostgreSQL Operational Store & Schema Migrations

Cairn uses PostgreSQL 15+ as its single operational store. All state transitions—record mutations, index compilations, agent session heartbeats, and inbox events—are fully transactional (ACID).

### Schema Migrations

Database schema migrations are stored as ordered SQL files in `core/schema/`:
- **Current production schema**: `074` (as of November 2026).
- Migrations are strictly sequential, forward-migrating, and checksummed.
- Applied via the CLI:
  ```sh
  cairn migrate
  ```

---

## 3. Core Architectural Mechanisms

### Receipts

When an agent searches memory or a compiler prepares task context, Cairn generates an immutable **Receipt**:
- Fixed candidate set and evaluation snapshot.
- Recorded token allocations and memory budgets.
- Verifiable caller identity and destination permissions (`local` vs `hosted`).
- Permanent audit trail of what knowledge was exposed to a specific model or task.

### Handles

Candidate note bodies are not returned in bulk during initial search. Instead, search results contain preview summaries and opaque **Handles**:
- Bounded time-to-live (TTL).
- Cryptographically bound to the querying principal and destination.
- Single-use or credit-metered expansion.
- Prevents context window explosion by requiring deliberate pulls:
  ```sh
  cairn agent pull RECEIPT_UUID HANDLE_UUID
  ```

### Restore Fences

In the event of database restoration or disaster recovery, Cairn activates a **Restore Fence** (`local-restore/1`):
- Generation mismatch detection immediately invalidates existing uncommitted handles.
- Blocks ordinary transactional mutations until reconciliation is complete.
- Prevents accidental resurrection of forgotten records or replay of stale execution claims.

### Mutation Idempotency & CAS

Every mutating operation requires a caller-specified `request_id` (UUID):
- Retries with the same `request_id` are guaranteed to be idempotent.
- Updates require `expected_version` (Compare-And-Swap).
- Version conflicts return `VERSION_CONFLICT`, requiring the caller to pull the latest state and reconcile changes rather than blindly overwriting.

---

## Related Documentation

- [Testing Guide](testing.md): Disposable test clusters, contract checking, and doc verification.
- [Contributing Guide](contributing.md): Style rules, source immutability, and trunk integration.
- [REST API Reference](../reference/api-rest.md): Declarative localapi endpoints and envelopes.
- [Lifecycle Slices Reference](../reference/lifecycle-slices.md): Details of slices CAIRN-01 through CAIRN-21.
