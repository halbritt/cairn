# Memory Capture & Knowledge Management Guide

Cairn allows autonomous agents and human operators to capture, classify, revise, and supersede knowledge with transactional integrity. Every captured note maintains strict provenance, sensitivity classifications, and explicit version history.

---

## 1. Capturing Memories

Knowledge is saved using the CLI command `cairn agent remember` or the `cairn_remember` MCP tool.

### CLI Example

```sh
cairn agent remember \
  --repo "$HOME/git/cairn" \
  --kind decision \
  "Adopted PostgreSQL as operational store; test suites use disposable clusters."
```

### MCP Tool Example

```json
{
  "request_id": "b183ebd3-0bfb-450d-a91b-9c348125c7dd",
  "body": "Adopted PostgreSQL as operational store; test suites use disposable clusters.",
  "kind": "decision",
  "scope": "repository",
  "shareable": false
}
```

---

## 2. Memory Kinds

Cairn categorizes memories into structured kinds to assist retrieval ranking and lifecycle management:

| Kind | Description | Intended Use |
|------|-------------|--------------|
| `decision` | Architectural or policy choices | Recording technology choices, design decisions, invariants |
| `preference` | Operator or project preferences | Working conventions, formatting choices, tool preferences |
| `fact` | Verifiable technical statements | Environment details, verified build outputs, schema configurations |
| `procedure` | Step-by-step technical guides | Setup sequences, deployment steps, recovery workflows |
| `observation` | Direct empirical measurements | Test benchmarks, failure traces, timing measurements |
| `reflection` | Synthesized insights and retrospectives | Post-mortems, evaluation summaries, lessons learned |

---

## 3. Scope & Sensitivity

### Scope

Memories can be scoped at three hierarchical levels:
- **Repository** (`--repo PATH`, with default `--task "*"` and `--run "*"`): Applies across the entire repository and all tasks.
- **Task** (`--task TASK_ID`): Restricted to the active task ID across runs.
- **Run** (`--task TASK_ID --run RUN_ID`): Restricted to a single execution attempt.

In the `cairn_remember` MCP tool, scope is set via the `"scope"` argument (`"repository"` [default], `"task"`, or `"run"`).

Re-usable project guidelines and architectural decisions should always use repository scope.

### Sensitivity

- `local` (default): Stays strictly on the local machine. Never delivered to external or hosted models.
- `shareable` (`--shareable`): Explicitly permitted to be delivered to hosted LLMs (e.g. Claude Code, OpenAI Codex).

---

## 4. Editing & Updating Notes

Cairn preserves prior versions of all modified records. Revisions are performed using `cairn agent edit` or `cairn_edit`. Cairn supports several targeted mutation modes:

### Body Replacement

Replaces the entire text body of an active note while preserving stored metadata:

```json
{
  "record_id": "RECORD_UUID",
  "expected_version": 1,
  "request_id": "NEW_REQUEST_UUID",
  "body": "Updated body text replacing version 1 completely."
}
```

### Text Append

Appends text to the existing body without rewriting the entire content:

```json
{
  "record_id": "RECORD_UUID",
  "expected_version": 1,
  "request_id": "NEW_REQUEST_UUID",
  "append": "\n\nAdditional verified findings appended to version 1."
}
```

### Exact Passage Replacement

Replaces an exact targeted substring within the note body:

```json
{
  "record_id": "RECORD_UUID",
  "expected_version": 1,
  "request_id": "NEW_REQUEST_UUID",
  "replace": {
    "old_text": "schema 049",
    "new_text": "schema 074"
  }
}
```

The `old_text` must match exactly once in the target document.

### Section Updates

Updates specific Markdown `##` sections by title:

```json
{
  "record_id": "RECORD_UUID",
  "expected_version": 1,
  "request_id": "NEW_REQUEST_UUID",
  "set_sections": {
    "Prerequisites": "Updated prerequisite list: Go 1.22+, PostgreSQL 15+."
  }
}
```

---

## 5. Superseding Knowledge

When a new decision or policy replaces an earlier one, do not silently delete the old note. Use explicit supersession:

```sh
cairn supersede < request.json
```

Where `request.json` contains:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "record_id": "OLD_RECORD_UUID",
  "expected_version": 1,
  "replacement_id": "NEW_RECORD_UUID",
  "reason": "Superseded by updated architecture decision after November 2026 review."
}
```

This retains full audit trails while ensuring searches prefer the superseding record.

---

## 6. Attaching Evidence & Citations

Claims can be backed by immutable evidence receipts and citations:

```sh
cairn cite < citation.json
```

Where `citation.json` contains:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "record_id": "RECORD_UUID",
  "expected_version": 1,
  "evidence_ids": ["EVIDENCE_UUID"]
}
```

---

## Related Documentation

- [Memory Search Guide](memory-search.md): Querying and retrieving memories.
- [Session Coordination Guide](session-coordination.md): Agent directory and messaging.
- [MCP Tools Catalog](../reference/mcp-tools.md): Specification for `cairn_remember` and `cairn_edit`.
- [CLI Reference](../reference/cli.md): Command options and arguments.
