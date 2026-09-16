# Fresh-work pools

A pool accepts a request for a fresh worker. It does not identify a continuing
conversation. Send to `agent/UUID` or use `agents resolve` for an existing session.

## Configure and inspect

The existing local operator configures a pool with `cairn worker-pool-configure`
and JSON on stdin:

```json
{"request_id":"NEW_UUID","repo":"/home/halbritt/git/cairn","name":"coding","enabled":true,"max_pending":100,"max_pending_per_publisher":20,"expected_revision":0}
```

Revision zero creates a pool. Later changes require its current revision. Limits
count queued requests and assigned deliveries still pending or leased. Lowering
a limit retains accepted work and refuses new work until below the limit.
Disabled pools refuse new publication and pause new claims, including assigned
pre-launch retries. They do not terminate already running work.

Add a `worker` object to the existing wake supervisor configuration:

```json
{"name":"worker-02","harness":"codex","model":"gpt-6-astra","workspace":"/home/halbritt/git/cairn","pools":["coding"],"capabilities":["review"],"launch_spacing_seconds":3}
```

Its name and workspace must match the enclosing launcher's name and directory.
The supervisor reconciles unfinished systemd units before registering a new
incarnation UUID. It refreshes liveness every 30 seconds; presence expires after
90 seconds. The UUID fences obsolete supervisors using the existing profile;
it is not another credential. Existing configurations without `worker` retain
the direct-inbox path. Once a slot is registered, claims require its incarnation.

Inspect with `cairn agent --token-file SLOT_TOKEN worker-list` or `pool-list`,
passing `{}` on stdin. Lists contain at most 128 entries per collection.
They expose selected launcher metadata and admission state, not token paths or
commands. Harness and configured model are reported metadata, not demonstrated
provider capacity. The linked native conversation reports its observed model
separately.

## Publish

Save a shareable source note, then publish its exact version:

```sh
cairn publish --profile codex --request-id NEW_UUID --pool coding   --workspace /home/halbritt/git/cairn --capability review   --kind request --version 1 RECORD_UUID
```

Workspace is mandatory and exact. Optional `--harness`, `--model`, and repeated
`--capability` selectors must match configured launcher metadata. The source
does not supply a command or change a slot's workspace. Only shareable request
sources can enter the current hosted worker pool.

Publication records a queued request with no delivery. An eligible slot's claim
creates its first delivery, wake attempt and assignment in one transaction.
The original event destination remains the pool. Reusing a publication or claim
request UUID after a lost response returns the original committed result.
`POOL_FULL` refuses admission without creating an event. No eligible slot leaves
accepted work queued.

The pool gives the least recently served eligible publisher the next turn, then
takes that publisher's oldest request. Between that candidate and existing slot
deliveries, the older event wins. Each slot has one unfinished wake attempt.
Claims respect configured launch spacing.

## Availability and recovery

Slot liveness and account availability are separate. Health is `available`,
`paused` or `unavailable`. Initial availability permits admission; it does not
assert that a provider call succeeded. The current deployment maps each of the
seven slots to one independent account binding.

The existing slot profile can record an observation or perform explicit recovery
using `worker-health`:

```json
{"request_id":"NEW_UUID","supervisor_id":"CURRENT_UUID","expected_revision":1,"health":"unavailable","reason":"operator observed quota exhaustion","retry_at":"2026-09-17T00:00:00Z"}
```

Use `worker-list` for the current UUID and revision. Heartbeats and supervisor
re-registration preserve health. A retry time is informational; passing it does
not automatically recover the account. Recover with a new request, current
revision, `health:"available"`, a selected reason and no retry time.

An unfinished or failed worker imposes a minimum 30-second delay on subsequent
launches from that binding. Existing bounded never-started retries also keep
their delivery delay and maximum attempt count. Health changes and their reasons
are retained by the ordinary mutation ledger. Generic process failures are not
classified as quota failures from stderr keywords. Automatic ingestion of
structured provider quota observations remains a separate implementation step.

## Failure boundaries

A request is assigned once. Uncertain or executed work never moves to another
account automatically. Confirmed pre-launch retries stay with their original
slot and recheck pool enablement, workspace, capabilities, harness and model.
A replacement launcher that does not match leaves the assigned request pending.

Database restore fences supervisor liveness. The retained attempt still holds
its delivery. A restarted supervisor confirms cgroup termination, finishes the
old attempt, and only then registers a new incarnation. Cleanup is permitted
after restore; new launch transitions from the old incarnation are refused.

`event-status` distinguishes queued and assigned pool requests. Its delivery
and `wake-attempts` distinguish pending, attempted, process outcome and reported
handling. Explicit completion and a successful process exit do not establish
independent task acceptance. Native conversation UUID linkage uses the existing
[wake contract](agent-wakeups.md).

These contracts do not provide scheduled publication, deadlines, cancellation,
automatic idle-session wakeups or response groups. Those remain in the
[coordination v1 plan](plans/agent-coordination-v1.md).
