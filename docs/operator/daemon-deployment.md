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
cairn schedule-serve --repo "$HOME/git/cairn"
```

---

## 2. Wake Supervisor Daemon

The Cairn wake supervisor manages scheduled wakeups, turn timeouts, and delayed event releases.

### Running Wake Supervisor

```sh
cairn wake serve --config "$HOME/.local/share/cairn/wake-binding.json"
```

### Running Wake Workers

Run an active wake worker process to execute scheduled wakeup triggers:

```sh
cairn wake worker --config "$HOME/.local/share/cairn/wake-binding.json" --attempt "b183ebd3-0bfb-450d-a91b-9c348125c7dd"
```

### Checking Wake Health

Inspect the status of the wake supervisor and pending wakeup queues:

```sh
cairn wake check --config "$HOME/.local/share/cairn/wake-binding.json"
```

---

## 3. Multi-Machine Administration

Cairn allows multiple physical or virtual machines to coordinate memory and session state across a shared operational cluster.

### Machine Provisioning

Provision an enrollment file for a new machine from the central host:

```sh
cairn machine provision --machine "node-02" --upstream "https://central.example.com:8443" --collection "$HOME/git/cairn" --out "node-02.enroll"
```

### Machine Enrollment

Enroll the joining host using the provisioned enrollment file:

```sh
cairn machine enroll --file "node-02.enroll"
```

### Key Rotation

Rotate machine credentials from the central host:

```sh
cairn machine rotate --machine "node-02" --upstream "https://central.example.com:8443" --out "node-02.enroll"
```

### Listing Enrolled Machines

View all registered machines on the central host:

```sh
cairn machine list
```

Inspect local machine enrollment status on a joining host:

```sh
cairn machine status
```

---

## 4. API Relaying (`cairn relay`)

For environments where worker nodes cannot connect directly to PostgreSQL or the primary localapi socket, `cairn relay` forwards authenticated API calls securely across network boundaries to an upstream HTTPS origin:

```sh
cairn relay --upstream "https://10.0.0.2:8443" --socket "$HOME/.local/share/cairn/api.sock"
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
