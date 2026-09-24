# OpenCode real-model cancellation trial (CAIRN-2): plan

Status: **prepared, not run.** Execution waits for agent-112's OpenCode
request-cancel bridge (the request-specific cancel route, session-inbox-control
polling, tool capture/stop reports and reconciliation) to be reviewed, merged
and enabled. Until then native cancellation stays `UNSUPPORTED_CONTROL`
([request-controls](../request-controls.md)), and nothing here claims it works.

The core contract already has synthetic and disposable-database coverage. This
trial adds host observation: a real model, a real tool process tree, and an
observer that reads `/proc` without consulting Cairn.

## Fixture

`scripts/opencode_cancel_trial.py` (tests in
`scripts/test_opencode_cancel_trial.py`):

- `workload tree|stubborn|escaped LEDGER` is the command the model runs as its
  tool call. Each spawned process appends its pid, `/proc` start time, role,
  process group and session to the ledger, then waits indefinitely.
  - `tree`: two children and a grandchild in the tool's process group; all stop
    on SIGTERM.
  - `stubborn`: one child ignores SIGTERM; only SIGKILL stops it.
  - `escaped`: adds a TERM-ignoring process that leaves the tool's session and
    process group, so a group kill misses it.
- `observe LEDGER` reports each ledger process as alive or gone, matching on pid
  and start time so a reused pid counts as gone. It exits 1 if anything survives.
- `cleanup LEDGER` sends SIGKILL to the survivors whose identity still matches,
  for the explicit cleanup step.

## Setup

- An owned tmux session running the installed OpenCode build with the bridge
  enabled, in a scratch directory under `/tmp/cairn-draft-trial/`, registered
  through the normal native path. Never a session someone else is using.
- The request is published to that session's inbox with an ordinary `request`
  event. The body asks the model to run exactly one fixture command. Nothing is
  typed into the session to start the work.
- Cancel with `cairn work-cancel` using the attempt's `delivery_id`. Read state
  with `coordination-review` and `event-status`.

## Cases and pass criteria

| Case | Workload | Pass when |
| --- | --- | --- |
| C1 targeted interruption | `tree` | The attempt was admitted with `turn_exclusive` and the tool is running. After `work-cancel`, the native turn reports a positive stop. `observe` shows no survivors. Cairn records `operator_cancelled` with `cancel_confirmed` and releases the hold only after its own clear scan. Late complete/renew gets `REQUEST_CANCELLED`. |
| C2 queued owner work survives | `tree`, plus an owner prompt queued while the tool runs | As C1, and the queued owner prompt then runs in the same session. The owner turn is never cancelled or dropped. If the owner input joins the admitted turn, exclusivity is revoked (`exclusivity_revoked`) and `cancel_confirmed` stays unset. |
| C3 failed termination, escapee | `escaped` | While `observe` reports the escapee, Cairn keeps `cancel_pending` and the hold. There is no `cancel_confirmed`, and no scan reports the host clear. After `cleanup`, a fresh host scan clears, and only then does reconciliation release the hold. |
| C4 failed termination, SIGTERM ignored | `stubborn` | If the stop escalates to SIGKILL, it behaves as C1. If it does not, it behaves as C3: the hold is kept until `cleanup` and a fresh clear scan. |
| C5 stop refused or uncertain | `tree`, with the native stop made to fail (bridge socket removed after admission) | Cancellation stays pending, the hold is kept, and nothing falls back to success. It recovers only through the explicit cleanup and fresh-scan path. |

A run fails if Cairn confirms cancellation or releases the hold while `observe`
reports any survivor. It also fails if the session as a whole is aborted or
killed instead of the one request, or if queued owner work is lost.

## Record

For each case, record in `docs/verification/opencode-cancel-trial-<date>.md`:

- the OpenCode binary SHA-256 and the Cairn commit;
- the delivery, event and attempt UUIDs;
- `observe` output before and after cancellation;
- the relevant `coordination-review` and `event-status` excerpts;
- how long each stop and cleanup step took.

Keep the prompt and model output out of the record beyond the fixture command.
Leave no survivors: run `cleanup` on every ledger before ending the trial.
