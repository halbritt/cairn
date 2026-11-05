# Coordinate agent sessions

Use Cairn's directory to find a running conversation and its inbox to send a
durable request. Memory stores reusable knowledge; inbox events refer to exact
versions of selected source notes and track each recipient's handling.

## Find the right session

A registered session has a canonical UUID, an `agent-N` display name, a current
execution UUID, and an inbox named `agent/UUID`. Use UUIDs in commands. Harness,
model, project, workspace, and task are reported metadata, useful for finding a
session but not proof of its capabilities.

With your existing token path in `CAIRN_TOKEN_FILE`:

```sh
cairn agents list --token-file "$CAIRN_TOKEN_FILE" --project example
cairn agents resolve --token-file "$CAIRN_TOKEN_FILE" --project example
```

Resolution returns `unique`, `ambiguous`, `no-match`, or `stale`. Inspect ambiguous
results and narrow your selector. A unique result includes `data.resolution`;
save that exact object for publication when you need Cairn to recheck the
recipient's presence and context. Explicit `--to agent/UUID` also supports offline
mail and does not perform that freshness check.

## Registration and presence

Installed native coordination hooks manage registration and presence. Use the
agent and execution UUIDs they supply; do not register again to bypass stale or
failed native work. If you are building an integration, see the
[session contract](../agent-sessions.md) for `agents register`, `agents context`,
`agents heartbeat`, and `agents leave`.

Presence expires after 90 seconds without a heartbeat. Expiry makes the session
offline; it does not establish that its process stopped. `leave` explicitly marks
the execution stopped. Presence alone does not prove that an inbox reached the
agent's conversation.

## Send a request

A request source should state the task, relevant context, constraints, and expected
result. Use a dedicated coordination task scope so one-off assignments do not
clutter ordinary memory searches. The
[event guide](../agent-event-fabric.md#dedicated-assignment-sources-and-search-scope)
provides a retry-safe `agent send` recipe and the manual save/publish sequence.

To publish an already saved source, supply its exact record ID and version, a
new request UUID, and the intended recipient. These uppercase values are
placeholders:

```sh
cairn publish --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id SENDER_AGENT_UUID --execution-id SENDER_EXECUTION_UUID \
  --request-id PUBLICATION_UUID --to agent/RECIPIENT_UUID \
  --kind request --version SOURCE_VERSION SOURCE_UUID
```

Use `--resolution FILE` instead of `--to` for a saved unique resolution. Keep the
same publication UUID and exact arguments when retrying an uncertain response.
A stale resolution for new intent requires resolving again. Source creation and
publication are separate operations; a failed publication may leave the source
saved.

## Handle an inbox item

Native integrations supply the exact commands for their admitted item. Use those
commands. For a custom polling consumer, claim one item from its own session:

```sh
cairn inbox next --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id AGENT_UUID --execution-id EXECUTION_UUID
```

An empty inbox returns `delivery: null`. Otherwise, read the exact source version
with `cairn agent history` using the returned reference. A later edit does not
change the source of an existing event. The request still needs to be within the
recipient's authorized scope.

For a request, `complete` saves selected result text and records handling in one
transaction. For a notice or response, `ack` records handling without a result
note. Use the returned delivery and lease UUIDs; both commands require a stable
request UUID. A reply is a separate `publish --kind response` with
`--causation-id` set to the original event UUID. Completion alone does not publish
that reply. See `cairn complete --help` and the [event contract](../agent-event-fabric.md).

`renew` extends a current lease; `retry` releases work for another attempt.
`STALE_LEASE` means the old claim can no longer complete. External side effects
need their own retry protection: a delivery lease cannot make them exactly once.

## Delivery and result checks

Use `cairn event-status EVENT_UUID` with your connection and session flags before
the UUID to inspect handling. Saved publication, delivery to a host, a reported
result, and independently verified task success are different observations.

Existing sessions receive native delivery at supported host boundaries. Urgent
notices skip routine deferral; they do not guarantee immediate interruption of a
running turn. Fresh-worker launch requires a configured
[wake supervisor](../agent-wakeups.md) or [pool](../worker-pools.md).
