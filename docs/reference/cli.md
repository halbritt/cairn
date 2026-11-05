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
- `--task TASK`: Task scope (defaults to "*").
- `--run RUN`: Run scope (defaults to "*").
- `--shareable`: Allow delivery to hosted models.

### `cairn agent inbox`
Inspects pending inbox events.

```sh
cairn agent inbox next --repo "$HOME/git/cairn"
```

### `cairn agent publish`
Publishes an event to an agent inbox.

```sh
cairn agent publish --request-id "REQ_UUID" --to "agent-1113" --kind "task" --version 1 --repo "$HOME/git/cairn" "RECORD_UUID"
```

### `cairn agent complete`
Marks an inbox event complete and records a result note. Requires `--stdin` with the result text provided via standard input.

```sh
echo "Task result description" | cairn agent complete --request-id "REQ_UUID" --lease "LEASE_UUID" --stdin "DELIVERY_UUID"
```

- `--request-id UUID`: Stable idempotency UUID for retrying mutations.
- `--lease UUID`: Active event lease UUID.
- `--stdin`: Mandatory flag indicating result body is read from standard input.
- `--shareable`: Mark result draft as shareable with hosted models (defaults to local).
- `--kind KIND`: Result record kind (defaults to `note`).
- `--task TASK`: Task scope label for the result draft (defaults to `*`).
- `--run RUN`: Run scope label for the result draft (defaults to `*`).

### `cairn agent ack`
Acknowledges receipt of an inbox event.

```sh
cairn agent ack --request-id "REQ_UUID" --lease "LEASE_UUID" "DELIVERY_UUID"
```

### `cairn agent renew`
Renews an active event lease.

```sh
cairn agent renew --lease "LEASE_UUID" --lease-seconds 300 "DELIVERY_UUID"
```

### `cairn agent retry`
Releases an event lease for retry.

```sh
cairn agent retry --lease "LEASE_UUID" "DELIVERY_UUID"
```

### `cairn notice-flush`
Releases pending routine deferred delivery for an event published by the authenticated caller.

```sh
cairn notice-flush --request-id "REQ_UUID" "EVENT_UUID"
```

- `--request-id UUID`: Stable idempotency UUID.
- `EVENT_UUID`: Target deferred event UUID.

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
- `cairn recovery-export FILE` (or `--directory DIR`): Export surviving audit records to a recovery file or directory bundle.
- `cairn recovery-inspect FILE...` (or `--directory DIR`): Validate recovery snapshot integrity and authority root grants.
- `cairn begin-restore < request.json`: Place database into restore fence.
- `cairn recovery-reapply --request-id UUID --expected-sha256 DIGEST --reason TEXT FILE`: Reapply newer deletions and retractions from a recovery file.
- `cairn resume-restore < request.json`: Resume normal transactions after restore.

### Daemons & Infrastructure

- `cairn wake serve --config /absolute/binding.json`: Run wake supervisor daemon.
- `cairn wake worker --config /absolute/binding.json --attempt UUID`: Run wake worker.
- `cairn wake check --config /absolute/binding.json`: Check wake supervisor status.
- `cairn schedule-serve --repo REPO`: Run background worker pool.
- `cairn worker-pool-configure < config.json`: Configure worker pools.
- `cairn machine provision --machine "node-1" --upstream "https://HOST:PORT" --collection REPO --out FILE`: Provision node enrollment file.
- `cairn machine enroll --file "node-1.enroll"`: Enroll node.
- `cairn machine rotate --machine "node-1" --upstream "https://HOST:PORT" --out FILE`: Rotate credentials.
- `cairn machine list`: List cluster nodes.
- `cairn machine status`: Query local node status.
- `cairn relay --upstream "https://HOST:PORT" [--socket PATH]`: Relay API calls across network boundaries.
- `cairn candidate-retention < request.json`: Inspect diagnostic candidate rows.
- `cairn candidate-reclaim < request.json`: Purge expired candidate rows.

---

## Related Documentation

- [Quickstart Guide](../quickstart.md): 5-minute onboarding.
- [MCP Tools Reference](mcp-tools.md): MCP tools parameter catalog.
- [REST API Reference](api-rest.md): Localapi endpoints specification.
