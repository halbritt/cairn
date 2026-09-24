# OpenCode real-model cancellation trial, 2026-09-24 (CAIRN-2)

Run by agent-100 following [the trial plan](opencode-cancel-trial-plan.md).
**Result: C1 to C5 passed.** In no case did Cairn confirm cancellation or release a
hold while the independent `/proc` observer reported a survivor. This covers
one owned session, one real model and marker-preserving fixture workloads. It
does not establish general tool-process cleanup, and it does not change any
production profile: `opencode-one` keeps cancellation disabled.

## Setup

- OpenCode fork `0.0.0-atomic-session-fence-202609240914`, binary SHA-256
  `8648eedb7844f82bf10328b14b4f1ae352e265bd0dfb0a6873bff3b80e83e5ed`.
- Cairn `main` at `9d171af`: the coordination files, plugin and scanner came
  from that checkout, and the trial `cairn` was built from it.
- Model: local Qwen through llama.cpp (`llamacpp/qwen3.6-27b` alias).
- Store: a disposable PostgreSQL cluster and `cairn serve` created by
  `scripts/opencode-cancel-trial-env.sh up /tmp/cairn-oc-trial`, with
  trial-only session and sender identities for a unique trial collection.
  The run used the pre-hardening script (`f3c113b`). Its teardown was later
  made to validate a process-identity manifest (`524029b`); that fix was not
  the version used during this run.
  The production database, API socket, profile, watcher and `opencode-one`
  were not used.
- Binding: `opencode-trial`, installed with `--root`, `--settings`,
  `--socket`, `--token-file`, `--repo` and `--no-service` into scratch
  locations. `idle_wakeup` and `opencode_cancel_enabled` were then set in the
  scratch binding by hand, because the installer ties `--idle-wakeup` (and so
  `--opencode-cancel-trial`) to Herdr's integration in `~/.config/opencode`.
  The trial watcher ran by hand in an owned tmux window.
- OpenCode ran in an owned tmux window with `XDG_CONFIG_HOME`,
  `XDG_DATA_HOME`, `XDG_STATE_HOME` and `XDG_CACHE_HOME` all scratch, so only
  the trial plugin loaded. The scratch `opencode.json` allowed `bash` and
  denied `edit` and `webfetch`. The first wake asked to read the scratch inbox
  directory; "Allow always" was granted in the TUI for that scratch session.
- One owner prompt registered the session (trial agent-1). Each case published
  one request whose body named a single fixture command. Cancellation used
  operator `work-cancel` against the trial store.

In every case the model first read the inbox context and the request through
bash, as the inbox protocol directs, then ran the fixture command. All
captured tools were `bash`. Before each cancel, every fixture process carried
`CAIRN_REQUEST_ID`, `CAIRN_NATIVE_TURN_ID` and `CAIRN_TOOL_CALL_ID` for the
pinned turn, and the marker scan matched the ledger with complete coverage.

## Results

| Case | Workload | Outcome |
| --- | --- | --- |
| C1 targeted interruption | `tree` | Pass. Processes were gone between +18.5 s and +28.7 s after `work-cancel`, and OpenCode showed the turn as interrupted. `turn_stop_state=ended`, then a clear scan. The delivery failed as `operator_cancelled` with native reason `cancel_confirmed` at +49.1 s. No survivors. |
| C2 queued owner work | `tree`, owner prompt submitted mid-tool | Pass. The owner prompt was not created while the request ran (the fork's owner gate). After the interruption it ran as its own turn and answered. `turn_exclusive` stayed true, and the reason was `cancel_confirmed`, not `exclusivity_revoked`. No survivors. |
| C3 escapee | `escaped` | Pass. The tool's group was gone within about 3 s. The escapee ran in its own session with the marker and survived. The host reported `terminal_scan=unknown_remaining`, and for over 100 s the store kept the delivery leased and held with the native state running and no confirmation. After observer `cleanup`, the next tick scanned clear and reconciled as `cancel_confirmed`. |
| C4 SIGTERM ignored | `stubborn` | Pass (escalation path). The TERM-ignoring child outlived its siblings by about 3 s, until OpenCode's shell tool escalated to SIGKILL. `turn_stop_state=ended` was recorded while it was still alive, but release waited for the next clear scan and then reconciled as `cancel_confirmed`. |
| C5 stop refused | `tree`, bridge socket moved aside after admission | Pass (held). The watcher's one-shot native stop returned `refused:unavailable` without sending, and nothing fell back to success. The workload kept running and the delivery stayed leased and held. After observer `cleanup`, the marker scan was clear with no OpenCode child processes. OpenCode never emitted TurnEnd, so there was still no positive stop, and the hold remained, `native running`, at +363 s. No stop was manufactured, and the held attempt was disposed of with the store. |

## Evidence

Identifiers, all from the disposable store (collection
`opencode-cancel-trial:04eaf0ab-513f-4603-9a17-7c6e67f6025b`, agent
`b0204d82-2a14-47d3-9703-3b40ee32e4b8`):

| Case | Event | Delivery | Attempt | Pinned turn |
| --- | --- | --- | --- | --- |
| C1 | `da740183-a421-4a7a-b7fd-a3bf7f107a9e` | `30f5fd36-965e-48f2-9bea-f5f62acfd8f2` | `85fb83fc-6188-4167-a698-2c9a6b098562` | `msg_0d35910f80012sfiRLVNqxTlFW` |
| C2 | `b30a1b63-bf6e-4cb6-8045-57dfcb8b0b92` | `950c19af-2b6e-4d8d-80d5-7782ba5623dc` | `572b6978-6a7e-42d0-9afd-159be1905b65` | `msg_0d3614e3a001r2Har1H63J314k` |
| C3 | `8069cfdf-8e11-4c71-a0e5-74c080b67805` | `24c2cc08-3c37-4718-8164-e6c4837f5687` | `260b7c0e-93d4-4b51-9115-0843ac5be266` | `msg_0d36322f6001fgw4WvRq8QaQhI` |
| C4 | `c52e949a-d375-4c37-a0b5-3d1910e7f070` | `d622127b-b1c5-48f4-97ac-a7a852670cfc` | `fbd943c8-afbb-4d13-9b3d-e9be0e77ece1` | `msg_0d365e250001VqHMfivMkVBlnW` |
| C5 | `7d2aab2a-4bfc-4756-8247-8b731cedd816` | `1b904765-90d5-4bbb-9fb7-da72b6018f1c` | `669a8ee0-42aa-4f5a-a6b5-3b985b91c8ad` | `msg_0d367b6e3001Sy5ihv07i3wB7b` |

Every `work-cancel` returned `held: true` with native state `cancel_pending`.
In the tables below, the attempt column is the watcher's local view of the
store attempt, shown as (cancel recorded, `turn_stop_state`,
`terminal_scan`). Times are seconds after `work-cancel`.

`observe` survivors (independent `/proc` ledger):

| Case | Before cancel | During | After |
| --- | --- | --- | --- |
| C1 | tool, child-a, child-b, grandchild | same through +18.5 s | none from +28.7 s |
| C2 | tool, child-a, child-b, grandchild | same through +9.2 s | none from +21.3 s |
| C3 | tool, child-a, child-b, grandchild, escapee | escapee only from +3.1 s | none after `cleanup` |
| C4 | tool, child-a, child-b, grandchild | child-b only at +22.4 and +24.5 s | none from +26.5 s |
| C5 | tool, child-a, child-b, grandchild | all four at +118 s | none after `cleanup` |

Attempt transitions:

| Case | Transitions |
| --- | --- |
| C1 | (no, -, -) to (yes, ended, -) at +28.7 s; released at about +51 s |
| C2 | (no, -, -) to (yes, ended, -) at +21.3 s; released at +45.6 s; `turn_exclusive` true throughout |
| C3 | (yes, ended, -) at +3.1 s; `unknown_remaining` from +42.5 s to at least +103 s, with tools `captured`; released 6.1 s after `cleanup` |
| C4 | (yes, -, -) at +22.4 s; (yes, ended, -) at +24.5 s; released at +53.1 s |
| C5 | (yes, -, -) with submission `refused:unavailable` at +18.3 s; `turn_stop_state` never set; not released |

`coordination-review` delivery excerpts:

| Case | Final excerpt |
| --- | --- |
| C1 | `failed`, `operator_cancelled`, `held: false`, native `finished`, reason `cancel_confirmed`, finished 05:25:04.762 |
| C2 | `failed`, `operator_cancelled`, `held: false`, native reason `cancel_confirmed`, finished 05:27:34.752 |
| C3 | While the escapee lived: `leased`, `held: true`, native `running`, no reason. After: `failed`, `operator_cancelled`, `held: false`, native `finished`, reason `cancel_confirmed`, finished 05:30:34.776 |
| C4 | `failed`, `operator_cancelled`, `held: false`, native reason `cancel_confirmed`, finished 05:32:34.812 |
| C5 | `leased`, `held: true`, native `running`, no reason, at +118 s and again at +363 s |

`event-status` was read for C1 only. It showed the same terminal delivery
state (`failed`) and `operator_cancelled` control as `coordination-review`.
The other cases were read through `coordination-review`.

C3's transition from held to `cancel_confirmed` went as follows:

- The positive pinned stop, `turn_stop_state=ended` from the pinned TurnEnd,
  was recorded at +3.1 s.
- While the escapee lived, each scan reported `unknown_remaining`, and the
  captured bash tools stayed `captured`.
- After `cleanup`, the next watcher tick's marker scan was clear. In the same
  tick the host sent `session-tool-stop` with `terminal_scan=clear` and each
  captured tool marked `terminated`, then reconciled.

That tick's intermediate reports were not captured separately, because the
local attempt was released within the tick. The evidence for them is the
store's acceptance of `cancel_confirmed`: the schema 049 contract accepts it
only after a positive turn stop, terminal captured tools and a clear
terminal scan newer than the cancellation decision and every later capture.

## Observations

- Stop latency is set by the watcher's 30-second poll. The native stop goes out
  on the first tick after `work-cancel`, and reconciliation happens on the
  next, so cancellation was confirmed about 45 to 50 s after the request.
  Faster polling while a cancel is pending would shorten this.
- The turn stop and process cleanup are recorded separately. C4 shows a stop
  recorded before the last process died, with release still correctly gated
  on the scan.
- The fork's bash tool kept showing its command as running after its whole
  process tree was killed from outside (C5), so no TurnEnd arrived. Cairn
  failed closed. Recovery for a real session needs an explicit operator
  decision, as the plan says.
- The installer cannot express a scratch OpenCode binding with idle wake (see
  Setup). The trial edited the scratch binding by hand.
- The owned tmux server, which held the trial OpenCode session and watcher,
  was stopped from outside the trial after the C5 held state had been
  recorded. It did not affect any recorded result. The trial store was then
  disposed of with `down`.

## Cleanup

`observe` reported no survivors for any ledger before disposal. `down` removed
the cluster, API and scratch directory, and no trial process or bridge socket
remained. Production `cairn-api` and `cairn-presence` stayed active
throughout.
