# Cairn Command-Line Interface (CLI) Reference

This document provides a comprehensive command and flag reference for the `cairn` binary (`cmd/cairn/`).

---

## 1. Global Conventions & Connection Flags

Most client and agent subcommands accept global flags to locate the API daemon and provide credentials:

- `--socket PATH`: Path to the Cairn Unix domain socket (default: `~/.local/share/cairn/api.sock`).
- `--token-file FILE`: Path to the bearer token file (default: `~/.local/share/cairn/hosted-agent.token`).
- `--repo PATH`: Canonical repository identity or collection name.
- `--help`, `-h`: Display command-line usage information.

---

## 2. Server & Service Commands

### `cairn serve`
Starts the Cairn local API daemon.

```sh
cairn serve --socket "$HOME/.local/share/cairn/api.sock"
```

- `--identities FILE`: Path to owner-only identity configuration JSON.
- `--socket PATH`: Private Unix domain socket path.
- `--listen ADDR`: Optional remote TCP address (e.g. `0.0.0.0:8443`).
- `--tls-cert FILE`: Remote listener TLS certificate.
- `--tls-key FILE`: Remote listener TLS private key.
- `--tls-terminated-proxy`: Explicit loopback backend behind reverse proxy HTTPS.
- `--machine-id ID`: Local directory machine ID.
- `--semantic-command PATH`: Local CPU semantic scoring executable.
- `--semantic-stream-command PATH`: Reusable local CPU semantic scoring executable.
- `--embedding-command PATH`: Local embedding executable.
- `--semantic-idle-timeout DURATION`: Idle timeout for semantic worker (default: `30s`).

### `cairn mcp`
Runs the Model Context Protocol (MCP) stdio server.

```sh
cairn mcp --socket "$HOME/.local/share/cairn/api.sock" --token-file "$HOME/.local/share/cairn/hosted-agent.token" --repo "$HOME/git/cairn"
```

- `--tool-profile compact/1|compact/2`: Enable token-saving compact tool definitions.
- `--task TASK`: Scoped task identifier.
- `--run RUN`: Scoped run identifier.
- `--codex-thread`: Inherit scope from Codex thread.

### `cairn version`
Reports client, server, and protocol versions.

```sh
cairn version
```

### `cairn clients`
Lists active observed client connections and diagnostics.

```sh
cairn clients
```

---

## 3. Agent Operations (`cairn agent <subcommand>`)

### `cairn agent search`
Searches scoped memory.

```sh
cairn agent search --repo "$HOME/git/cairn" "operational store"
```

- `--entity-file PATH`: Target notes explicitly associated with a file path.
- `--entity-symbol SYMBOL`: Target notes explicitly associated with a code symbol.
- `--semantic`: Enable vector embedding similarity search.
- `--offset N`: Pagination offset.
- `--tokens N`: Declare maximum memory input room.

### Pulling Memory Bodies (`cairn agent pull RECEIPT_UUID HANDLE_UUID`)
Pulls complete or bounded body for a search preview. Requires positional `RECEIPT_UUID` and `HANDLE_UUID`.

```sh
cairn agent pull RECEIPT_UUID HANDLE_UUID
```

### `cairn agent remember`
Saves an explicitly selected note as ordinary testimony.

```sh
cairn agent remember --repo "$HOME/git/cairn" --kind decision "Note body"
```

- `--kind KIND`: `decision`, `preference`, `fact`, `procedure`, `observation`, `reflection`.
- `--scope SCOPE`: `repo`, `task`, `run`.
- `--shareable`: Allow delivery to hosted models.

### `cairn agent inbox`
Inspects pending inbox events.

```sh
cairn agent inbox next --repo "$HOME/git/cairn"
```

### `cairn agent publish`
Publishes an event to an agent inbox.

```sh
cairn agent publish --to "agent-1113" --topic "task.dispatch" --repo "$HOME/git/cairn"
```

### `cairn agent complete`
Marks an inbox event complete.

```sh
cairn agent complete --lease "LEASE_UUID"
```

### `cairn agent ack`
Acknowledges receipt of an inbox event.

```sh
cairn agent ack --lease "LEASE_UUID"
```

### `cairn agent renew`
Renews an active event lease.

```sh
cairn agent renew --lease "LEASE_UUID" --lease-seconds 300
```

### `cairn agent retry`
Releases an event lease for retry.

```sh
cairn agent retry --lease "LEASE_UUID"
```

---

## 4. Agent Session Directory (`cairn agents <subcommand>`)

### `cairn agents register`
Registers an active agent session.

```sh
cairn agents register --request-id "REQ_UUID" --binding "claude" --native-session "sess-1" --harness "claude" --model "sonnet" --project "cairn" --workspace "$HOME/git/cairn"
```

### `cairn agents context`
Updates active task context.

```sh
cairn agents context --request-id "REQ_UUID" --agent-id "agent-1" --execution-id "exec-1" --expected-revision 1 --harness "claude" --project "cairn" --workspace "$HOME/git/cairn" --task "Testing"
```

### `cairn agents heartbeat`
Emits session liveness heartbeat.

```sh
cairn agents heartbeat --agent-id "agent-1" --execution-id "exec-1"
```

### `cairn agents leave`
Unregisters an agent session.

```sh
cairn agents leave --agent-id "agent-1" --execution-id "exec-1"
```

### `cairn agents list`
Lists all active registered agent sessions.

```sh
cairn agents list
```

### `cairn agents resolve`
Resolves status of a specific agent.

```sh
cairn agents resolve --agent-id "agent-1"
```

---

## 5. Configuration Generators

### `cairn claude-config`
Generates Claude Code and Desktop MCP JSON configuration.

```sh
cairn claude-config --socket "$HOME/.local/share/cairn/api.sock" --token-file "$HOME/.local/share/cairn/hosted-agent.token" --repo "$HOME/git/cairn" --task "dev" --run "1"
```

### `cairn codex-config`
Generates OpenAI Codex MCP TOML configuration.

```sh
cairn codex-config --socket "$HOME/.local/share/cairn/api.sock" --token-file "$HOME/.local/share/cairn/hosted-agent.token" --repo "$HOME/git/cairn" --codex-thread
```

### `cairn opencode-config`
Generates OpenCode adapter configuration JSON.

```sh
cairn opencode-config --socket "$HOME/.local/share/cairn/api.sock" --token-file "$HOME/.local/share/cairn/hosted-agent.token" --repo "$HOME/git/cairn" --task "dev" --run "1"
```

### `cairn opencode-install`
Installs Cairn adapter directly into an OpenCode project.

```sh
cairn opencode-install --project "$HOME/git/cairn" --socket "$HOME/.local/share/cairn/api.sock" --token-file "$HOME/.local/share/cairn/hosted-agent.token" --repo "$HOME/git/cairn"
```

---

## 6. Operator & Lifecycle Commands

### Database & Disaster Recovery

- `cairn migrate`: Apply schema migrations (up to 074).
- `cairn checkpoint < request.json`: Generate cryptographic audit checkpoint.
- `cairn verify-checkpoint < expectation.json`: Verify checkpoint against catalog.
- `cairn recovery-export < request.json`: Export current audit records.
- `cairn recovery-inspect < request.json`: Inspect restored database.
- `cairn begin-restore < request.json`: Place database into restore fence.
- `cairn recovery-reapply < request.json`: Reapply newer deletions and retractions.
- `cairn resume-restore < request.json`: Resume normal transactions after restore.

### Daemons & Infrastructure

- `cairn wake serve`: Run wake supervisor daemon.
- `cairn wake worker`: Run wake worker.
- `cairn wake check`: Check wake supervisor status.
- `cairn schedule-serve`: Run background worker pool.
- `cairn worker-pool-configure < config.json`: Configure worker pools.
- `cairn machine provision --machine-id "node-1"`: Provision node certificate.
- `cairn machine enroll --machine-id "node-1"`: Enroll node.
- `cairn machine rotate --machine-id "node-1"`: Rotate credentials.
- `cairn machine list`: List cluster nodes.
- `cairn machine status --machine-id "node-1"`: Query node status.
- `cairn relay`: Relay API calls across network boundaries.
- `cairn candidate-retention < request.json`: Inspect diagnostic candidate rows.
- `cairn candidate-reclaim < request.json`: Purge expired candidate rows.

---

## Related Documentation

- [Quickstart Guide](../quickstart.md): 5-minute onboarding.
- [MCP Tools Reference](mcp-tools.md): MCP tools parameter catalog.
- [REST API Reference](api-rest.md): Localapi endpoints specification.
