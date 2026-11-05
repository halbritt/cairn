# Model Context Protocol (MCP) Tools Reference Catalog

Cairn registers 10 specialized tools over the Model Context Protocol (MCP) stdio facade (`cairn mcp`). This document provides detailed specifications for each tool, including input arguments, schemas, and return structures.

---

## 1. `cairn_search`

Searches scoped memory using lexical, quoted exact, entity, or semantic discovery modes.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `query` | string | No (unless browse=false) | Search query text. Enclose in quotes for exact matching. |
| `browse` | boolean | No | When true, browse eligible topics without a query. |
| `offset` | integer | No | Pagination offset (default: 0). |
| `available_tokens` | integer | No | Remaining input context room in the model prompt. |
| `memory_budget_bytes` | integer | No | Maximum bytes allowed for the returned preview index. |
| `min_pull_bytes` | integer | No | Automatic whole-body pull threshold. |
| `inspection_policy` | string | No | `first-fitting-whole/1` or `first-fitting-whole/2`. |
| `entities` | array | No | Explicit file or symbol entities (`[{"kind": "file", "name": "path"}]`). |
| `kinds` | array | No | Filter by memory kinds (`["decision", "preference"]`). |
| `scope` | string | No | Filter by scope (`repo`, `task`, `run`). |

### Output

Returns an index result containing `receipt_id`, `page` metadata (`next_offset`), and `index` entries. Each entry contains a preview snippet, relevance score, `receipt_id`, and complete `pull_arguments`.

---

## 2. `cairn_pull`

Pulls the full or bounded body of a candidate note discovered in search results.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `request_id` | string (UUID) | Yes | Client-chosen UUID for idempotent retries. |
| `receipt_id` | string (UUID) | Yes | Search receipt UUID returned by `cairn_search`. |
| `handle` | string | Yes | Time-bounded handle returned in the index entry. |
| `span` | object | No | Byte range slice: `{"offset": N, "length": N}`. |
| `opening_bytes` | integer | No | Optional prefix opening length (1-768 bytes). |

### Output

Returns the complete record body, metadata, kinds, attribution, and version information. When `span` is specified, returns byte excerpts and cryptographic hashes.

---

## 3. `cairn_pull_evidence`

Pulls exact supporting evidence bytes referenced by an expanded memory.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `request_id` | string (UUID) | Yes | Unique request UUID for idempotent retries. |
| `receipt_id` | string (UUID) | Yes | Search receipt UUID. |
| `handle` | string | Yes | Expansion handle from search index. |
| `evidence_id` | string (UUID) | Yes | Evidence identifier. |
| `expected_sha256` | string (hex) | Yes | Expected SHA-256 digest of full evidence object. |
| `span` | object | No | Optional byte range slice: `{"offset": N, "length": N}`. |

### Output

Returns verified evidence bytes, source label, storage tier (`inline` vs `managed_threshold`), and verification digest.

---

## 4. `cairn_remember`

Saves an explicitly selected note as ordinary Class A testimony.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `body` | string | Yes | Selected reusable knowledge with source and verification context. |
| `request_id` | string (UUID) | Yes | A UUID chosen before capture; reuse exactly for retries of this note. |
| `kind` | string | No | Ordinary record kind (`decision`, `preference`, `fact`, `procedure`, `observation`, `reflection`); defaults to `note`. |
| `scope` | string | No | Capture applicability: `repository` (default), `task`, or `run`. |
| `shareable` | boolean | No | Explicitly allow this content to reach hosted models. Defaults to `false` (local). |
| `entities` | array | No | Explicit file or symbol associations (`[{"file": "...", "symbol": "..."}]`), at most 16. |
| `pins` | object | No | Explicit applicability restrictions. |

### Output

Returns the created `record_id`, initial `version` (1), and stored metadata.

---

## 5. `cairn_edit`

Revises an existing Class A note with Compare-And-Swap (CAS) version checks.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `record_id` | string (UUID) | Yes | Record identifier to revise. |
| `expected_version` | integer | Yes | Current version number (must match stored version). |
| `request_id` | string (UUID) | Yes | Unique request UUID for idempotent retries. |
| `body` | string | Mutually Exclusive | Complete replacement text body. |
| `append` | string | Mutually Exclusive | Text suffix to append to existing body. |
| `replace` | object | Mutually Exclusive | Substring replacement: `{"old_text": "...", "new_text": "..."}`. |
| `set_sections` | object | Mutually Exclusive | Map of Markdown `##` section titles to new section bodies. |
| `draft` | object | Mutually Exclusive | Full replacement draft object. |
| `evidence_citations` | array | Mutually Exclusive | Replace supporting citations list. |

### Output

Returns updated `record_id` and new incremented `version` number.

---

## 6. `cairn_prepare_note`

Discovers possible predecessor notes before saving new knowledge to prevent duplicate or conflicting entries.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `query` | string | Yes | Short subject query describing the planned note. |
| `entities` | array | No | Codebase entities relevant to the planned note. |
| `memory_budget_bytes` | integer | No | Byte cap for predecessor search previews. |

### Output

Returns eligible predecessor notes and complete pull arguments to inspect before deciding whether to call `cairn_edit` or `cairn_remember`.

---

## 7. `cairn_client_info`

Inspects the running MCP facade build stamps and capability declarations.

### Input Arguments
*None (accepts empty object `{}`).*

### Output

Returns facade build version, active `tool_profile`, active `search_view`, and declared memory budgeting capabilities.

---

## 8. `cairn_assess`

Appends a qualitative evaluation or task outcome review to a search receipt.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `receipt_id` | string (UUID) | Yes | Receipt UUID being evaluated. |
| `expected_version` | integer | Yes | Latest review version (0 for initial review). |
| `request_id` | string (UUID) | Yes | Unique request UUID. |
| `task_outcome` | string | Yes | `success`, `failure`, `unknown`. |
| `failure_domain` | string | Yes | Domain of failure or `unknown`. |
| `failure_kind` | string | No | Specific failure categorization. |
| `evidence_ids` | array | Yes | Evidence UUIDs supporting the review. |
| `reason` | string | Yes | Explanatory rationale (8 to 4000 characters). |

---

## 9. `cairn_assessments`

Reads the complete assessment review history for an owned receipt.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `receipt_id` | string (UUID) | Yes | Receipt UUID to inspect. |

### Output

Returns array of assessment records in ascending version order, including observer, method, task outcome, and reason.

---

## 10. `cairn_history`

Inspects version history or reads an exact earlier body of a record.

### Input Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `record_id` | string (UUID) | Yes | Record identifier. |
| `version` | integer | No | If specified, returns exact body of that version. |
| `limit` | integer | No | Maximum versions to list (default: 20, max: 100). |
| `before_version` | integer | No | Pagination cursor for older versions. |
| `span` | object | No | Byte excerpt slice: `{"offset": N, "length": N}`. |

---

## Related Documentation

- [MCP Overview](../mcp/overview.md): Architecture and tool profiles.
- [CLI Reference](cli.md): Full CLI commands and options.
