# Operator Configuration Guide

This guide details environment variables, profile tokens, authentication identities, and TLS setup for administering Cairn.

---

## 1. Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `CAIRN_DATABASE_URL` | PostgreSQL connection DSN override | `postgres://localhost:5432/cairn?sslmode=disable` (or scripts/local-store.sh) |
| `CAIRN_HOME` | Base configuration and state directory | `~/.local/share/cairn` |

Socket paths, authentication tokens, and repository identities are configured via command-line flags (`--socket`, `--token-file`, `--repo`), CLI commands, or systemd unit definitions rather than ambient environment variables.

---

## 2. Profile Tokens & Identities

Cairn uses cryptographically random bearer tokens mapped to authenticated principals in `~/.local/share/cairn/identities.json`.

### Identity Roles

1. **`agent`**: Standard autonomous agent profile. Permitted to search memory, pull candidate notes, capture ordinary testimony, and manage session inbox events.
2. **`observer`**: Instrumented runner channel. Permitted to record verified test executions and instrumented evidence.
3. **`operator`**: Administrative profile. Permitted to issue mandatory instructions, promote claims, grant scope authorizations, and run maintenance operations.

### Hosted vs Local Destination Profiles

- **Hosted Profile** (`~/.local/share/cairn/hosted-agent.token`): Used when interacting with hosted AI models (Claude, OpenAI). Content marked `sensitivity: local` is automatically filtered out by the API.
- **Local Profile**: Used on the local machine for unrestricted access to both local and shareable memories.

### Generating Identities Configuration (`identities.json`)

The identities file maps SHA-256 digests of bearer tokens to principals:

```json
[
  {
    "token_sha256": "4b227777d4dd1fc61c6f884f48641d02b4d121d3fd328cb08b5531fcacdabf8a",
    "principal": "hosted-agent",
    "role": "agent",
    "repo": "/home/halbritt/git/cairn",
    "destination": "hosted",
    "remote": false
  },
  {
    "token_sha256": "ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f",
    "principal": "local-operator",
    "role": "operator",
    "repo": "/home/halbritt/git/cairn",
    "destination": "local",
    "remote": false
  }
]
```

Pass the identity file when starting the API service:

```sh
cairn serve --identities "$HOME/.local/share/cairn/identities.json"
```

---

## 3. Remote TLS Configuration

When deploying Cairn across multiple machines, secure communication using mutual TLS:

1. Generate or provision an X.509 server certificate and private key.
2. Launch `cairn serve` with TLS parameters:
   ```sh
   cairn serve \
     --listen "0.0.0.0:8443" \
     --tls-cert "/etc/ssl/cairn/server.crt" \
     --tls-key "/etc/ssl/cairn/server.key"
   ```
3. Remote clients connect using HTTPS and supply their bearer token in the `Authorization: Bearer <token>` header.

---

## Related Documentation

- [Deployment Guide](deployment.md): Systemd units and service management.
- [Maintenance Guide](maintenance.md): Backup, disaster recovery, and migrations.
- [CLI Reference](../reference/cli.md): Flags and commands reference.
