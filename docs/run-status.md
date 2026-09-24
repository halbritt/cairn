# Inspect an owned run receipt

Use `run-status` after a lost launch-claim or outcome response to inspect what
Cairn recorded. Both local and hosted profiles can inspect receipts they own.
The principal and repository must match the receipt; operator CLI access does
not override ownership.

```sh
printf '%s\n' '{"receipt_id":"RECEIPT_UUID"}' |
  cairn agent --token-file ~/.local/share/cairn/observer.token run-status
```

Use the same provisioned profile that compiled the receipt. The agent client
needs socket/token access and does not open PostgreSQL. Direct local callers can
use `cairn run-status RECEIPT_UUID` for receipts owned by their local-uid identity.
The HTTP operation is `POST /v1/run-status` with the same JSON request.

The response's `data` contains:

| Field | Meaning |
| --- | --- |
| `receipt_id` | The requested, owned receipt. |
| `observed_at` | Database snapshot observation time. |
| `launch_claimed` | Whether Cairn recorded a launch claim in that snapshot. |
| `binding_observed` | Whether Cairn recorded binding metadata. |
| `outcome` | `null` until an outcome is recorded; otherwise `observation_id`, `process_state`, nullable `exit_code`, `signal` when `process_state` is `signaled`, and `duration_ms`. |

No memory bodies, selected record IDs, command details, task labels, or task
acceptance are returned. Hosted profiles still cannot read protected run reports.
An agent may inspect its own compile receipt without gaining observer authority.

## Recover without authorizing a duplicate

This is a historical observation, never launch permission. A true claim with no
outcome does not prove that a process started or is still running. A false claim
is a snapshot, not a reservation: another request may commit afterward. Always
use the existing launch-claim operation for authorization; do not automatically
retry an ambiguous claim or start a child based on a status read.

For an unconfirmed outcome, compare its observation ID if present, then reconcile
the exact retained `outcome.pending.json` through `outcome` under the same profile
as described in [authenticated runner recovery](authenticated-runner.md#custody-and-failures).
A confirmed idempotent retry returns the original observation ID. Process exit
zero remains separate from task acceptance.

Status remains readable after policy changes, restore fences, and memory body
purge. Those observations do not make an old package deliverable or launchable.
After restoring a backup, status reflects that restored database's evidence;
it cannot recover claims or outcomes absent from the backup. Keep external host
attempt evidence when reconciling such gaps.

## Signal-terminated workers

A worker killed by a signal that the runner did not send is recorded with
`process_state` `signaled` and the `signal` number, and no `exit_code`. Before
migration 050 such a worker was recorded as `exited` with the synthetic exit
code -1. The runner's own timeout and cancellation stay `timeout` and
`cancelled`. `cairn run` exits with 128 plus the signal number, following the
shell convention; for example, SIGKILL gives 137.
