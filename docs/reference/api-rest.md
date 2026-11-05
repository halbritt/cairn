# Cairn REST API Specification (`localapi`)

The Cairn REST API (`localapi/`) provides a declarative HTTP interface over Unix domain sockets or TLS TCP connections for autonomous agents and external tooling.

---

## 1. Transport & Authentication

- **Transport**: Unix domain socket (`~/.local/share/cairn/api.sock`) or HTTP/HTTPS.
- **Method**: All mutating and query operations use `POST` with JSON bodies.
- **Authentication**: `Authorization: Bearer <token>`.
- **Identity Headers**:
  - `Cairn-Agent-ID`: Registered agent session UUID.
  - `Cairn-Execution-ID`: Current execution attempt UUID.

---

## 2. Request & Error Envelopes

### Request Rules
- Every mutating request must include a caller-scoped `request_id` (UUID) for idempotency.
- Unknown fields are strictly disallowed (`DisallowUnknownFields`).
- Requests must contain exactly one valid JSON object.

### Error Envelope

Errors return standard HTTP status codes with a JSON error payload:

```json
{
  "code": "VERSION_CONFLICT",
  "message": "version mismatch: expected 1, current is 2",
  "refusal_id": "REFUSAL_UUID"
}
```

Standard Error Codes:
- `INVALID_REQUEST` (400 / 405): Malformed JSON, missing fields, or invalid method.
- `AUTHORITY_DENIED` (401 / 403): Missing token, invalid credentials, or forbidden destination.
- `NOT_FOUND` (404): Endpoint or record does not exist.
- `VERSION_CONFLICT` (409): CAS version mismatch.
- `BUDGET_REFUSED` (400): Request exceeds declared context or token limits.

---

## 3. Endpoints Catalog

### System & Diagnostics

| Endpoint | Method | Limit | Description |
|----------|--------|-------|-------------|
| `/v1/version` | POST | 128 KiB | Query server build info, protocol version, and capabilities |
| `/v1/clients` | POST | 128 KiB | List observed client connections and diagnostics |

### Record Lifecycle

| Endpoint | Method | Limit | Description |
|----------|--------|-------|-------------|
| `/v1/create` | POST | 512 KiB | Create an ordinary Class A note |
| `/v1/edit` | POST | 512 KiB | Edit an active note (full draft or body) |
| `/v1/revise` | POST | 512 KiB | Revise active note text |
| `/v1/append` | POST | 512 KiB | Append text suffix to note body |
| `/v1/replace` | POST | 1 MiB | Replace exact targeted substring |
| `/v1/set-sections` | POST | 1 MiB | Update specific Markdown `##` sections |
| `/v1/delete` | POST | 128 KiB | Delete an unreferenced Class A note |
| `/v1/cite` | POST | 128 KiB | Attach evidence citations to a record |

### Memory Retrieval & Indexing

| Endpoint | Method | Limit | Description |
|----------|--------|-------|-------------|
| `/v1/index` | POST | 128 KiB | Generate a sealed, bounded memory index |
| `/v1/expand` | POST | 128 KiB | Expand a candidate body using a search handle |
| `/v1/expand-evidence` | POST | 128 KiB | Expand supporting evidence bytes |
| `/v1/compile` | POST | 128 KiB | Pre-compile complete task context package |
| `/v1/explain-page` | POST | 4 KiB | Paginate index explanation gate facts |

### Governance & Supersession

| Endpoint | Method | Limit | Description |
|----------|--------|-------|-------------|
| `/v1/supersede` | POST | 128 KiB | Supersede an obsolete record with a newer record |
| `/v1/supersession` | POST | 128 KiB | Inspect supersession metadata for a record |
| `/v1/preview-retract` | POST | 128 KiB | Preview blast-radius of retracting a record |

### Agent Sessions Directory

| Endpoint | Method | Limit | Description |
|----------|--------|-------|-------------|
| `/v1/agent-sessions/register` | POST | 128 KiB | Register an active agent session |
| `/v1/agent-sessions/context` | POST | 128 KiB | Update active task context description |
| `/v1/agent-sessions/heartbeat` | POST | 128 KiB | Record session heartbeat |
| `/v1/agent-sessions/leave` | POST | 128 KiB | Gracefully unregister agent session |
| `/v1/agent-sessions/list` | POST | 128 KiB | List registered active agent sessions |
| `/v1/agent-sessions/resolve` | POST | 128 KiB | Resolve presence and status of an agent |

### Durable Agent Inbox & Events

| Endpoint | Method | Limit | Description |
|----------|--------|-------|-------------|
| `/v1/agent-events/publish` | POST | 128 KiB | Publish an event to an agent inbox |
| `/v1/agent-events/inbox` | POST | 128 KiB | Fetch next pending inbox event |
| `/v1/agent-events/ack` | POST | 128 KiB | Acknowledge receipt of an inbox event |
| `/v1/agent-events/complete` | POST | 128 KiB | Complete processing of an inbox event |
| `/v1/agent-events/retry` | POST | 128 KiB | Release event lease for retry |
| `/v1/agent-events/renew` | POST | 128 KiB | Renew event lease duration |

---

## Related Documentation

- [Architecture Guide](../developer/architecture.md): System layout and transactional design.
- [CLI Reference](cli.md): Command line interface reference.
- [MCP Tools Reference](mcp-tools.md): MCP tools specifications.
