# Session Coordination Guide

Cairn provides a multi-agent coordination subsystem that tracks agent presence, manages live task context, and routes asynchronous, durable messages through agent inboxes.

---

## 1. Agent Session Directory

Autonomous agents register with Cairn to announce presence, declare their runtime environment, and coordinate work across processes.

### Registration

When an agent initializes, it registers a session:

```sh
cairn agents register \
  --request-id "REQ_UUID" \
  --binding "claude-code" \
  --native-session "sess-9428" \
  --harness "claude-code" \
  --model "claude-3-7-sonnet" \
  --project "cairn" \
  --workspace "$HOME/git/cairn"
```

Registration returns an agent session record and establishes an inbox channel.

### Maintaining Task Context

Agents declare and update their active objective and task context:

```sh
cairn agents context \
  --request-id "REQ_UUID" \
  --agent-id "agent-1113" \
  --execution-id "exec-9428" \
  --expected-revision 1 \
  --harness "claude-code" \
  --project "cairn" \
  --workspace "$HOME/git/cairn" \
  --task "Authoring user and operator documentation suite"
```

Other agents and human operators can inspect active task descriptions to prevent duplicate or conflicting work.

### Liveness & Heartbeats

To keep session presence active, agents emit periodic heartbeats:

```sh
cairn agents heartbeat \
  --agent-id "agent-1113" \
  --execution-id "exec-9428"
```

If an agent process terminates or misses heartbeats beyond the expiration threshold, its session is marked stopped.

### Session Termination

When an agent completes its work, it gracefully leaves the directory:

```sh
cairn agents leave \
  --agent-id "agent-1113" \
  --execution-id "exec-9428"
```

### Inspecting Sessions

To view all active and registered agent sessions across the host:

```sh
cairn agents list
```

To inspect or resolve a specific agent session by identifier:

```sh
cairn agents resolve --agent-id "agent-1113"
```

---

## 2. Durable Agent Inbox

The Cairn agent inbox provides transactional, at-least-once messaging between agents.

### Publishing Messages

Agents publish events and work requests directly to recipient inboxes:

```sh
cairn agent publish \
  --to "agent-1113" \
  --topic "task.dispatch" \
  --repo "$HOME/git/cairn"
```

### Checking Inboxes

Agents inspect pending events delivered to their inbox:

```sh
cairn agent inbox next --repo "$HOME/git/cairn"
```

Pending events are returned with event UUIDs, sender attribution, timestamps, and payload envelopes.

### Event Lifecycle

The lifecycle of an inbox event follows strict transactional states:

1. **Publish**: The event is written to `cairn.agent_events` in `waiting` state.
2. **Lease/Claim**: The recipient leases the event for a bounded processing duration.
3. **Acknowledge / Complete**: Upon successfully handling the event, the agent completes it:

```sh
cairn agent complete --lease "LEASE_UUID"
```

Alternatively, acknowledge reception:

```sh
cairn agent ack --lease "LEASE_UUID"
```

4. **Retry / Renew**: If processing takes longer than the lease, the agent renews the lease:

```sh
cairn agent renew --lease "LEASE_UUID" --lease-seconds 300
```

If processing fails or needs to be retried:

```sh
cairn agent retry --lease "LEASE_UUID"
```

### Urgent vs Routine Notices

Cairn differentiates high-priority requests from routine background notices:
- **Urgent / Direct**: Immediately delivered to the agent on its current turn.
- **Routine Deferred**: Notices can be deferred for up to 60 seconds to bundle notifications and reduce turn interruptions.

To immediately release deferred notices:

```sh
cairn notice-flush < request.json
```

---

## Related Documentation

- [Agent Workflows Guide](agent-workflows.md): Multi-agent patterns and handoffs.
- [Memory Capture Guide](memory-capture.md): Storing session findings.
- [CLI Reference](../reference/cli.md): Command flags and options.
- [REST API Reference](../reference/api-rest.md): REST endpoints for agent sessions and events.
