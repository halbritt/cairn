# Agy & Hermes Integration Guide

This guide explains how to connect Agy agent workflows and Hermes messaging gateways to Cairn for persistent memory and multi-agent coordination.

---

## 1. Overview of Agy & Hermes

- **Agy**: High-performance local autonomous agent runner supporting specialized tools and parallel worker execution.
- **Hermes**: Messaging and event gateway connecting agent sessions to terminal interfaces, notification hubs, and background supervisors.

Cairn acts as the central memory and coordination plane for both systems, ensuring agents share decisions across sessions and communicate via durable inboxes.

---

## 2. Configuration for Agy Agents

Agy agents connect to Cairn via the stdio MCP facade. Configure `cairn mcp` in your Agy agent environment or plugin definition:

```sh
cairn mcp \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" \
  --tool-profile compact/1
```

Agy agents inherit repository scope by default, enabling them to search existing codebase decisions and capture new findings.

---

## 3. Session Registration & Hermes Gateway

When Hermes initiates an agent session, it registers with Cairn to maintain presence:

```sh
cairn agents register \
  --request-id "REQ_UUID" \
  --binding "hermes" \
  --native-session "sess-001" \
  --harness "hermes" \
  --model "local" \
  --project "cairn" \
  --workspace "$HOME/git/cairn"
```

### Event Routing via Hermes

Hermes monitors the agent inbox for pending notifications and wake events:

```sh
cairn agent inbox next --repo "$HOME/git/cairn"
```

When work completes, Hermes emits completion status:

```sh
cairn agent complete --lease "LEASE_UUID"
```

---

## 4. Multi-Agent Collaboration Patterns

1. **Memory Sharing**: Agy workers search prior decisions before modifying source code.
2. **Durable Handoffs**: Hermes captures turn-end checkpoints into Cairn memory before stopping sessions.
3. **Event Coordination**: Long-running tasks use Cairn inboxes to stream progress updates to supervisory agents.

---

## Related Documentation

- [MCP Overview](overview.md): Architecture and tool profiles.
- [Session Coordination Guide](../guides/session-coordination.md): Agent sessions and inboxes.
- [Agent Workflows Guide](../guides/agent-workflows.md): Multi-agent patterns.
