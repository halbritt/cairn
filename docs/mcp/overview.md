# Model Context Protocol (MCP) in Cairn

Cairn exposes its memory and coordination capabilities through the standard Model Context Protocol (MCP), enabling seamless tool integration with leading AI developer harnesses, including Claude Code, OpenAI Codex, OpenCode, and Agy/Hermes.

---

## 1. MCP Architecture & Stdio Facade

The Cairn MCP implementation (`cairn mcp`) runs as a local stdio process spawned directly by the host AI harness:

```text
+-----------------------------------------------------------+
|                      Host AI Harness                      |
|       (Claude Code / OpenAI Codex / OpenCode / Agy)       |
+-----------------------------+-----------------------------+
                              |
                     Standard I/O (JSON-RPC)
                              |
                              v
+-----------------------------------------------------------+
|                     Cairn MCP Facade                      |
|                       (cairn mcp)                         |
+-----------------------------+-----------------------------+
                              |
                    Unix Domain Socket / REST
                              |
                              v
+-----------------------------------------------------------+
|                   Cairn Local API Daemon                  |
|                       (cairn serve)                       |
+-----------------------------+-----------------------------+
                              |
                         PostgreSQL
```

### Key Architectural Characteristics

1. **Security Isolation**: The host process and model never receive PostgreSQL database credentials. They interact solely through an authenticated Unix domain socket using a provisioned bearer token.
2. **Identity & Destination Filtering**: The MCP facade is configured with a repository, task ID, and run ID. The underlying API enforces destination security: memories marked `sensitivity: local` are automatically withheld from hosted models.
3. **Stateless Tool Calls**: Each tool invocation is mapped to a transactional local API call with explicit byte and token budgeting.

### Launching the MCP Facade

```sh
cairn mcp \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --task "documentation-audit" \
  --run "turn-1"
```

---

## 2. Tool Profiles (`compact/1` & `compact/2`)

By default, MCP tool definitions include exhaustive descriptions and complete schemas. In long-running agent sessions, these large descriptions consume significant context window space on every turn.

To conserve context, Cairn provides optimized **Tool Profiles**:

| Profile | Description | Token Reduction | Recommended Use Case |
|---------|-------------|-----------------|----------------------|
| **Standard** | Full, descriptive schemas and comprehensive docstrings | Baseline (0%) | Interactive exploration, single-turn tasks |
| **`compact/1`** | Concise descriptions and streamlined schemas | ~40% reduction | Autonomous coding sessions, multi-turn tasks |
| **`compact/2`** | Minimal parameter signatures and compact descriptions | ~60% reduction | High-frequency agent loops, small-context models |

Enable a profile using `--tool-profile`:

```sh
cairn mcp \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --tool-profile compact/1
```

---

## 3. Search View Compaction

When searching memory via `cairn_search`, Cairn returns compact preview handles rather than dumping entire note bodies into the model prompt.

- The model receives preview summaries, relevance ranks, and `pull_arguments`.
- If an indexed note is relevant, the model pulls the full content deliberately via `cairn_pull`.
- This two-stage pattern prevents thousands of unneeded tokens from cluttering the agent conversation history.

---

## 4. Token Budgeting & Byte Spans

### Dynamic Budget Declarations

Models or host wrappers declare token and byte constraints on every search:
- `available_tokens`: Remaining room in the current model prompt.
- `memory_budget_bytes`: Maximum byte size permitted for the search result payload.
- `min_pull_bytes`: Threshold below which bodies are pulled automatically in the search response.

### Byte Spans

For large documents, historical records, or captured evidence files, models can inspect bounded byte slices using `span`:

```json
{
  "span": {
    "offset": 0,
    "length": 1024
  }
}
```

The response returns exact bytes and SHA-256 verification hashes for the requested slice without loading the entire multi-kilobyte object.

---

## 5. Catalog of Exposed MCP Tools

Cairn registers 10 specialized tools over MCP:

1. `cairn_search`: Search scoped memory with lexical, exact quoted, entity, or semantic modes.
2. `cairn_pull`: Pull complete or bounded memory bodies using search receipts and handles.
3. `cairn_pull_evidence`: Pull exact supporting evidence bytes with cryptographic digest verification.
4. `cairn_remember`: Save newly learned architectural decisions, preferences, and facts.
5. `cairn_edit`: Revise active Class A notes with full CAS version checks.
6. `cairn_prepare_note`: Discover possible predecessor notes before saving new knowledge.
7. `cairn_client_info`: Inspect MCP facade version, build stamps, and active capabilities.
8. `cairn_assess`: Record qualitative reviews and task outcome evaluations.
9. `cairn_assessments`: Inspect prior assessment history for a given search receipt.
10. `cairn_history`: List version history or read exact earlier bodies of a record.

For detailed argument definitions, see the [MCP Tools Reference Catalog](../reference/mcp-tools.md).

---

## Related Documentation

- [Claude Code Integration](claude-code.md): Setup for Claude Code CLI.
- [Codex Integration](codex.md): Setup for OpenAI Codex CLI.
- [OpenCode Integration](opencode.md): Setup for OpenCode adapter.
- [Agy & Hermes Integration](agy-hermes.md): Setup for Agy and Hermes agents.
- [MCP Tools Reference](../reference/mcp-tools.md): In-depth tools parameter catalog.
