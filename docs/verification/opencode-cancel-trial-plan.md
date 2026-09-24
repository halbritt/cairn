# OpenCode real-model cancellation trial (CAIRN-2): plan

Status: **prepared, not run.** The request-specific bridge and host cleanup
adapter are merged on `main` at `781c77a`. Execution waits for an owned scratch
OpenCode binding installed with `--opencode-cancel-trial`. Until then native
cancellation stays `UNSUPPORTED_CONTROL`
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
  for the explicit cleanup step. It opens a pidfd, then rechecks the start time
  in `/proc` while the pidfd pins the process, and signals through the pidfd,
  so a pid reused mid-trial is never signalled.

## Setup

- A disposable Cairn PostgreSQL cluster and API socket with a trial-only agent
  profile. Point the scratch binding at this API and profile. Do not use the
  existing production database for the trial or its cleanup.
- An owned tmux session running the installed OpenCode build with the bridge
  enabled and the Cairn binding installed with `--opencode-cancel-trial`, in a
  scratch directory under `/tmp/cairn-draft-trial/`, registered
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
| C2 queued owner work survives | `tree`, plus an owner prompt queued while the tool runs | As C1, and the queued owner prompt runs in the same session only after the cancelled request's turn has ended. The owner turn is never cancelled or dropped. OpenCode's native admission makes owner input wait for the exclusive request, so owner input that joins the admitted turn **fails** the trial. If a join is observed, Cairn must still fail closed: revoke to `exclusivity_revoked` with `cancel_confirmed` unset. That correct revocation does not make the case pass. |
| C3 failed termination, escapee | `escaped` | While `observe` reports the escapee, Cairn keeps `cancel_pending` and the hold. There is no `cancel_confirmed`, and no scan reports the host clear. After `cleanup`, the hold is released only once all three release conditions hold (below). |
| C4 failed termination, SIGTERM ignored | `stubborn` | If the stop escalates to SIGKILL, it behaves as C1. If it does not, it behaves as C3: the hold is kept until `cleanup` and a fresh clear scan. |
| C5 stop refused or uncertain | `tree`, with the native stop made to fail (bridge socket removed after admission) | Cancellation stays pending, the hold is kept, and nothing falls back to success. Process disappearance after `cleanup` does not release the hold alone: without a positive observed turn stop it stays held, and that is the passing outcome. |

Release conditions (schema 049): Cairn releases a hold only when all three are
observed:

1. a positive native turn stop (`interrupted` or `ended`; `ambiguous` holds);
2. terminal captures for the turn's tools;
3. a fresh clear host scan after the cancellation decision and every later capture.

C1, C3, C4 and C5 check each condition separately. Process disappearance
never substitutes for a turn stop that is still running or unobserved.

A run fails if Cairn confirms cancellation or releases the hold while `observe`
reports any survivor, or before all three conditions are observed. It also fails if the session as a whole is aborted or
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
If C5 leaves an uncertain stop and a held attempt, record that held state as
the expected result. After the independent observer confirms every fixture
process is gone, stop the owned scratch session and dispose of the trial
database; do not manufacture a positive turn-stop report or clear a production
hold to make the case look complete. The one-shot native stop can leave this
held state after a lost response, and recovery requires an explicit operator
decision outside the automatic watcher path.

If C2 reports `owner_join` before a positive pinned `TurnEnd`, record the case
as failed and the still-held attempt. A lost forced `TurnEnd` can leave that
hold without automatic release. Confirm fixture cleanup with the independent
observer, then stop the scratch session and dispose of the trial database;
do not manufacture a pinned turn stop to release the hold.

The fixture retains Cairn's shell environment marker. A process that erases
that marker or changes user identity is outside this trial's scan coverage;
passing these cases does not establish general tool-process cleanup.
