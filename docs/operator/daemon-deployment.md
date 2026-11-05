# Daemon & Multi-Machine Deployment Guide

This guide covers advanced daemon configuration, background worker pools, the wake supervisor, and multi-machine administration across distributed Cairn instances.

---

## 1. Background Worker Pools

Cairn uses worker pools to execute background tasks, process event subscriptions, and perform asynchronous memory compilation.

### Configuring Worker Pools

Configure worker pool concurrency and routing:

```sh
cairn worker-pool-configure < config.json
```

Where `config.json` specifies:

```json
{
  "request_id": "NEW_REQUEST_UUID",
  "pool": "default",
  "concurrency": 4,
  "harness": "claude-code",
  "capabilities": ["build", "test"]
}
```

Run the scheduler daemon:

```sh
cairn schedule-serve --socket "$HOME/.local/share/cairn/api.sock"
```

---

## 2. Wake Supervisor Daemon

The Cairn wake supervisor manages scheduled wakeups, turn timeouts, and delayed event releases.

### Running Wake Supervisor

```sh
cairn wake serve --socket "$HOME/.local/share/cairn/api.sock"
```

### Running Wake Workers

Run an active wake worker process to execute scheduled wakeup triggers:

```sh
cairn wake worker --socket "$HOME/.local/share/cairn/api.sock"
```

### Checking Wake Health

Inspect the status of the wake supervisor and pending wakeup queues:

```sh
cairn wake check --socket "$HOME/.local/share/cairn/api.sock"
```

---

## 3. Multi-Machine Administration

Cairn allows multiple physical or virtual machines to coordinate memory and session state across a shared operational cluster.

### Machine Provisioning

Provision an identity certificate for a new machine:

```sh
cairn machine provision --machine-id "node-02"
```

### Machine Enrollment

Enroll the new node in the cluster directory:

```sh
cairn machine enroll --machine-id "node-02"
```

### Key Rotation

Rotate machine credentials periodically:

```sh
cairn machine rotate --machine-id "node-02"
```

### Listing Enrolled Machines

View all registered machines and their active network presence:

```sh
cairn machine list
```

Inspect a specific machine:

```sh
cairn machine status --machine-id "node-02"
```

---

## 4. API Relaying (`cairn relay`)

For environments where worker nodes cannot connect directly to PostgreSQL or the primary localapi socket, `cairn relay` forwards authenticated API calls securely across network boundaries:

```sh
cairn relay --socket "$HOME/.local/share/cairn/api.sock" --listen "10.0.0.2:8443"
```

---

## 5. Semantic Embedding Daemons

To enable vector semantic search without adding latency to interactive turns, Cairn can be connected to local embedding workers:

```sh
cairn serve \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --semantic-command "/opt/cairn/bin/semantic-scorer" \
  --semantic-idle-timeout "60s"
```

For persistent high-throughput embeddings, use `--embedding-command` or `--semantic-stream-command`.

---

## Related Documentation

- [Deployment Guide](deployment.md): Production deployment and systemd units.
- [Configuration Guide](configuration.md): Environment variables and tokens.
- [CLI Reference](../reference/cli.md): Full command catalog.
