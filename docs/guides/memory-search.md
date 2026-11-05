# Memory Search Guide

Cairn provides a multi-mode, token-budgeted memory search engine designed specifically for autonomous AI agents and developer workflows. Memory retrieval balances precision, context efficiency, and relevance across repositories, tasks, and execution runs.

---

## 1. Search Modes

Cairn supports four complementary search mechanisms:

### Lexical Search

The default search mode uses lexical token matching, term frequency ranking, and BM25 scoring over indexed memory bodies:

```sh
cairn agent search --repo "$HOME/git/cairn" "PostgreSQL schema migrations"
```

Lexical search is fast, deterministic, and ideal when searching for general technical concepts and procedures.

### Exact Quoted Search

When searching for exact identifiers, source code paths, error messages, or configuration keys, wrap the target terms in escaped ASCII double quotes:

```sh
cairn agent search --repo "$HOME/git/cairn" ""core/schema""
```

Quoted terms are evaluated before optional ranking terms. The search engine scores documents containing the exact verbatim substring first, eliminating ambiguity for paths like `"core/schema"` or exact error codes like `"AUTHORITY_DENIED"`.

### Entity Search

Cairn records explicit, versioned associations between memories and codebase entities:
- **File entities**: `--entity-file <relative/path>`
- **Symbol entities**: `--entity-symbol <qualified_symbol>`

```sh
cairn agent search --repo "$HOME/git/cairn" --entity-file "cmd/cairn/serve.go" "daemon flags"
```

Entity search targets notes explicitly linked to specific source files or code symbols, avoiding false positives from casual mentions in prose.

### Semantic Search

Semantic search uses vector embeddings to retrieve memories conceptually related to a query, even when the exact vocabulary differs:

```sh
cairn agent search --repo "$HOME/git/cairn" --semantic "storage engine recovery procedures"
```

If the local semantic daemon or embedding worker is temporarily offline or unconfigured, Cairn gracefully falls back to lexical matching with a labeled diagnostic (`discovery.state: lexical_fallback`), ensuring searches never fail abruptly.

---

## 2. Pagination

Search results can be navigated incrementally using `--offset`:

```sh
cairn agent search --repo "$HOME/git/cairn" --offset 0 "lifecycle slices"
```

If additional results are eligible within the search scope, the response includes `page.next_offset`. To fetch the next page, pass the returned offset:

```sh
cairn agent search --repo "$HOME/git/cairn" --offset 10 "lifecycle slices"
```

Each page is independently budgeted against the model context limit.

---

## 3. Token Budgeting & Context Economy

To prevent context window exhaustion in LLM agents, Cairn strictly enforces memory budgets:

- `--tokens N`: Declares the maximum token ceiling for the search response.
- `available_tokens`: Total free input room currently remaining in the model conversation.
- `memory_budget_bytes`: Conservative byte limit allocated specifically for search index previews and expanded bodies.
- `min_pull_bytes`: Threshold below which preview bodies are pulled automatically rather than requiring a separate pull turn.

When using MCP tools, declare both `available_tokens` and `memory_budget_bytes` in `cairn_search` calls:

```json
{
  "query": "disaster recovery drill",
  "available_tokens": 16000,
  "memory_budget_bytes": 4000
}
```

This ensures search index previews never overflow the available agent context.

---

## 4. Inspection Policies

When agents need to retrieve complete source bodies rather than partial previews, Cairn provides inspection policies:

- `first-fitting-whole/1`: Automatically pulls the first candidate note whose entire body fits within the allocated memory budget.
- `first-fitting-whole/2`: Advanced policy that packs complete bodies for eligible candidate notes into the response until the budget is saturated.

In MCP `cairn_search`, supply the policy as an argument:

```json
{
  "query": "operational store",
  "memory_budget_bytes": 8000,
  "inspection_policy": "first-fitting-whole/2"
}
```

---

## 5. Pulling Candidate Bodies

Search index results include preview summaries and two critical identifiers: `receipt_id` and `handle_id`. To read the full, unabbreviated body of a candidate note:

```sh
cairn agent pull RECEIPT_UUID HANDLE_UUID
```

Handles are time-bounded, caller-authenticated, and destination-restricted. If a handle expires, reissue a fresh search to acquire a new handle.

---

## Related Documentation

- [Memory Capture Guide](memory-capture.md): Storing and revising memories.
- [Session Coordination Guide](session-coordination.md): Multi-agent directory and inbox.
- [Agent Workflows Guide](agent-workflows.md): Multi-agent delegation and handoff patterns.
- [CLI Reference](../reference/cli.md): Comprehensive CLI reference.
- [MCP Tools Catalog](../reference/mcp-tools.md): MCP tools parameter specifications.
