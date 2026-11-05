# Quickstart Guide: 5 Minutes to Cairn

Cairn is a durable, transactional memory engine and multi-agent coordination system designed for autonomous AI agents, developer tooling, and engineering workflows. This guide gets you up and running with Cairn in under five minutes.

---

## 1. Prerequisites

Before starting, ensure you have:
- **Go 1.22+** (if building from source) or the compiled `cairn` binary.
- **PostgreSQL 15+** operational database.
- A valid PostgreSQL connection string (e.g. `export CAIRN_DSN="postgres://localhost:5432/cairn?sslmode=disable"`).

To verify your installation:

```sh
cairn version
```

---

## 2. Initialize Database & Schema

Cairn manages its relational schema using numbered transactional migrations. Initialize or upgrade your database to the current schema (version 074):

```sh
export CAIRN_DSN="postgres://localhost:5432/cairn?sslmode=disable"
cairn migrate
```

This sets up tables for records, revisions, evidence, citations, search indices, agent sessions, and durable inboxes.

---

## 3. Start the Cairn Service

Run the Cairn local API server. By default, it communicates over an authenticated Unix domain socket or TCP port:

```sh
cairn serve --socket "$HOME/.local/share/cairn/api.sock"
```

The daemon provides REST endpoints for agent clients, administrative tools, and the MCP stdio facade.

---

## 4. Remembering Knowledge

Autonomous agents and operators save durable knowledge using `cairn agent remember` or the `cairn_remember` MCP tool. Knowledge can be categorized by kind (`decision`, `preference`, `fact`, `procedure`, `observation`, `reflection`) and scoped (`repository`, `task`, `run`).

### CLI Example

```sh
cairn agent remember \
  --repo "$HOME/git/cairn" \
  --kind decision \
  "Adopted PostgreSQL as operational store; test suites use disposable clusters."
```

By default, notes are local to the host. Add `--shareable` if the note should be exposed to hosted AI models (e.g. Claude, OpenAI Codex).

---

## 5. Searching & Pulling Memories

Cairn separates discovery from inspection to keep LLM context windows bounded and economical.

### Search Memory

Search across indexed notes in a repository:

```sh
cairn agent search --repo "$HOME/git/cairn" "PostgreSQL operational store"
```

For exact identifiers, file paths, or error messages, enclose the target in escaped quotes:

```sh
cairn agent search --repo "$HOME/git/cairn" ""core/schema""
```

### Pull Note Body

Search results return index previews along with receipt and handle identifiers. To read the complete body of a candidate note:

```sh
cairn agent pull RECEIPT_UUID HANDLE_UUID
```

---

## 6. Checking Status & Connected Clients

Inspect running client connections and diagnostic metadata:

```sh
cairn clients
```

Verify service status and capabilities:

```sh
cairn version
```

---

## Next Steps

Explore the complete documentation suites:
- **User Guides**:
  - [Memory Search](guides/memory-search.md): Lexical, quoted, entity, and semantic search.
  - [Memory Capture](guides/memory-capture.md): Kinds, scopes, editing, superseding, and citations.
  - [Session Coordination](guides/session-coordination.md): Multi-agent directory, heartbeat, and durable inboxes.
  - [Agent Workflows](guides/agent-workflows.md): Multi-agent orchestration, delegation, and handoffs.
- **Developer Documentation**:
  - [Architecture](developer/architecture.md): Flat package layout, receipts, handles, and restore fences.
  - [Testing](developer/testing.md): Disposable database fixtures and automated verification.
  - [Contributing](developer/contributing.md): Style, immutability, and trunk integration.
- **Operator Guides**:
  - [Deployment](operator/deployment.md): Systemd user units and socket configuration.
  - [Configuration](operator/configuration.md): Tokens, identities, and environment variables.
  - [Maintenance](operator/maintenance.md): Migrations, backups, and disaster recovery.
  - [Daemon Deployment](operator/daemon-deployment.md): Worker pools and wake supervisor.
- **Model Context Protocol (MCP)**:
  - [MCP Overview](mcp/overview.md): Stdio facade, compaction, and tool profiles.
  - [Claude Code Integration](mcp/claude-code.md): Setup for Claude Code CLI.
  - [Codex Integration](mcp/codex.md): Setup for OpenAI Codex CLI.
  - [OpenCode Integration](mcp/opencode.md): Native tools adapter for OpenCode.
  - [Agy & Hermes Integration](mcp/agy-hermes.md): Integration for Agy and Hermes agents.
- **Reference**:
  - [CLI Reference](reference/cli.md): Full command and flag reference.
  - [MCP Tools Catalog](reference/mcp-tools.md): Detailed specifications for all 10 MCP tools.
  - [REST API](reference/api-rest.md): Endpoints, limits, and request/response envelopes.
  - [Lifecycle Slices](reference/lifecycle-slices.md): Roadmap slices CAIRN-01 through CAIRN-21.
