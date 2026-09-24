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
