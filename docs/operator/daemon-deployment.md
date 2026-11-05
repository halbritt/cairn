# Add background services and remote clients

Ordinary memory access needs the API and PostgreSQL. Add other processes when a
workflow requires semantic search, scheduled publication, or fresh agent workers.
Each has its own configuration and failure behavior.

## Semantic retrieval

`cairn serve` accepts one semantic backend mode at a time:

| Flag | Backend |
| --- | --- |
| `--semantic-command PATH` | A local scoring executable invoked for requests. |
| `--semantic-stream-command PATH` | A reusable local scoring process. |
| `--embedding-command PATH` | Persistent passage retrieval with a local embedding executable. |

The executable must implement Cairn's protocol; an arbitrary embedding CLI is
not interchangeable. `--semantic-idle-timeout` applies only to
`--semantic-stream-command`. The default reusable-worker idle lifetime is 30
seconds. A persistent embedding backend has a different lifecycle.

Follow [semantic discovery](../semantic-discovery.md) or
[persistent retrieval](../persistent-semantic-retrieval.md) for setup and model
requirements. Clients request semantic discovery explicitly and inspect the
reported state; unavailable scoring can produce a labelled lexical fallback.

## Scheduled publication

`cairn schedule-serve --repo COLLECTION` runs the operator's one-shot schedule
processor. It publishes due events and performs the bounded request-control
sweep. It is not a pool worker and does not execute an assignment itself.

Create schedules through `event-schedule` with an exact source version, due time,
and grace period. Follow [event scheduling](../event-scheduling.md) for request
shapes, late-publication behavior, and restore handling. Recurring schedules are
not part of this contract.

## Wake supervisors and fresh-work pools

A wake binding fixes a worker's profile, workspace, launch command, observation
profile, and deadline. Check and run a binding with:

```sh
cairn wake check --config /absolute/path/to/binding.json
cairn wake serve --config /absolute/path/to/binding.json
```

Prepare that file using the [wake binding contract](../agent-wakeups.md).
`wake worker` is the entry point launched for an admitted attempt by the
supervisor; do not invoke it with an invented attempt UUID. Source text cannot
choose the worker's command or workspace.

A fresh-work pool queues requests for eligible configured slots. Pool admission
limits belong to `worker-pool-configure`; slot routing belongs to each wake
binding's `worker` object. These are separate configurations. Follow
[worker pools](../worker-pools.md) for the exact JSON and revision checks.

The supervisor uses systemd user units to control worker process trees. Process
exit, reported completion, and independent task acceptance remain distinct.
Review provider failures and uncertain execution before retries; a queue does
not make external effects exactly once. See
[provider failures](../provider-failures.md) and [request controls](../request-controls.md).

## Connect another machine

Multi-machine Cairn uses one central API and PostgreSQL store. A joining machine
runs a relay from its private Unix socket to the central HTTPS API. It does not
run an independent replicated memory store.

Use [multi-machine deployment](../multi-machine.md) for provisioning, enrollment,
protocol compatibility, token rotation, and service setup. Provisioning writes a
private enrollment file; transfer it privately and enroll on the joining host.
The central API must reload changed identities before the new credentials work.
Do not expose a remote listener without the documented TLS or HTTPS-proxy
configuration and remote-profile restrictions.

After service changes, check both client and API [build identity](../build-identity.md),
then exercise the specific path you enabled. A running process alone does not
verify memory delivery or worker execution.
