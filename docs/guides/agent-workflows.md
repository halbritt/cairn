# Agent Workflows & Multi-Agent Orchestration Guide

Modern software engineering workflows increasingly rely on multi-agent collaboration. This guide explains how to design, execute, and monitor multi-agent workflows using Cairn as the coordination backbone and shared memory store.

---

## 1. Multi-Agent Workflow Architectures

Cairn supports three core multi-agent interaction architectures:

### Orchestrator-Worker Pattern

An orchestrator agent breaks complex goals into verifiable milestones, dispatches discrete assignments to worker agents, and monitors their outputs:

```text
       +-----------------------+
       |   Orchestrator Agent   |
       +-----------+-----------+
                   |
     +-------------+-------------+
     |                           |
     v                           v
+----+----+                 +----+----+
| Worker  |                 | Worker  |
|   M1    |                 |   M2    |
+----+----+                 +----+----+
     |                           |
     +-------------+-------------+
                   |
                   v
       +-----------+-----------+
       |   teamwork_auditor    |
       +-----------------------+
```

1. **Dispatch**: Orchestrator publishes tasks to worker inboxes using `cairn agent publish`.
2. **Execution**: Workers register sessions via `cairn agents register`, execute genuine implementations, and log progress in persistent working folders.
3. **Auditing**: A dedicated auditor independently reviews changes, verifies tests, and validates claims.

### Peer Delegation Pattern

Autonomous agents collaborate horizontally without a strict hierarchy. Agent A identifies a sub-problem requiring specialized expertise (e.g. database migration, frontend styling), issues a task request to Agent B, and resumes dependent work once Agent B delivers results.

### Specialized Subagents

Parent agents spawn transient subagents with scoped responsibilities (e.g., test runners, documentation checkers, forensic auditors), providing them with isolated task and run scopes.

---

## 2. Structured Agent Handoff Protocol

When tasks transfer between agents—or when an agent pauses across turns—a complete, self-contained handoff must be written.

### The 5-Component Handoff Report

Every handoff report must contain five mandatory sections:

1. **Observation**: What was directly observed (verbatim tool commands, line numbers, error messages, diff outputs).
2. **Logic Chain**: Step-by-step causal deduction connecting observations to conclusions.
3. **Caveats**: Uninvestigated assumptions, alternative interpretations, and boundaries.
4. **Conclusion**: Direct, actionable outcome supported by the logic chain.
5. **Verification Method**: Concrete, independent commands and procedures to verify the work.

### Preserving Handoffs in Cairn

Agents preserve handoffs as durable Cairn memories:

```sh
cairn agent remember \
  --repo "$HOME/git/cairn" \
  --kind decision \
  "Milestone 2 completed: aligned README.md, docs/mcp.md, docs/roadmap.md, and cmd/cairn/commands.go with schema 074."
```

Successor agents recall the latest handoff at startup:

```sh
cairn agent search --repo "$HOME/git/cairn" "Milestone 2 completed"
```

---

## 3. Shared Repository Memory Across AI Harnesses

Engineering teams frequently mix different AI tools and models across tasks:
- **Claude Code** for exploratory coding and architectural design.
- **OpenAI Codex** for code refactoring and targeted repairs.
- **OpenCode** for headless CI automation and background execution.
- **Agy / Hermes** for scheduled maintenance and multi-agent coordination.

Cairn serves as the shared, harness-neutral knowledge layer. A lesson learned or architectural decision made in Codex is immediately searchable and usable by Claude Code or OpenCode without manual context copying.

---

## 4. Context Economy & Token Budgeting

Unchecked multi-agent communication quickly leads to LLM context degradation. Cairn enforces strict token budgeting:

- **Selective Pulling**: Never load entire file trees or documentation corpuses into initial prompts. Search for relevant topics, inspect compact previews, and pull only relevant bodies using `cairn agent pull RECEIPT_UUID HANDLE_UUID`.
- **Byte Span Offsets**: For large documents or evidence files, pull exact byte spans:
  ```json
  {
    "span": {
      "offset": 0,
      "length": 1024
    }
  }
  ```
- **Compact Tool Profiles**: Use `--tool-profile compact/1` or `compact/2` to reduce tool schema overhead in agent system prompts by up to 60%.

---

## Related Documentation

- [Session Coordination Guide](session-coordination.md): Agent directory, presence, and inboxes.
- [Memory Search Guide](memory-search.md): Querying and retrieving memories.
- [Memory Capture Guide](memory-capture.md): Storing and updating decisions.
- [MCP Overview](../mcp/overview.md): Model Context Protocol integration.
