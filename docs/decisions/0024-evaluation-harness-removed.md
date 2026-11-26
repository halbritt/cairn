# 0024 — The paired task-evaluation harness is removed

Status: accepted by the owner, 2026-11-25.

## Question

Should the repository keep the paired task-evaluation harness: the `trial_*`
scripts and their tests, the SDK process-owner trials, `trials/task-eval` and
the weekly runner (CAIRN-107)?

## Decision

No. The owner removed it: "remove the eval harness, it was pointless."
CAIRN-107 is cancelled.

The harness was about 580 files. Its last substantial change was on
2026-10-27, the weekly timer was never installed, and its own documentation
had already been removed. It also held the last September run reports in the
repository through a ledger of cited sources.

## What was removed

- `scripts/trial_*`, `test_trial_*`, `check_trial_*` and `guard_trial_*`,
  `scripts/selected_native/`, the weekly paired check and the SDK phase table.
- `trials/task-eval`, the September recurrence trials and the source-fit trial.
- `integrations/sdk-ipc`, `internal/sdkcontract`, the MCP SDK contract test,
  the weekly systemd unit and timer, and the `test-sdk-*` Makefile targets.
- The task-evaluation plan and the reports it cited.

Four modules that other code imports stay under their old names:
`trial_host.py`, `trial_codex_turn_deadline.py`,
`trial_codex_app_server_sandbox.py` and `trial_source_delivery.py`. The
sandbox helper they needed is now `scripts/fixture_sandbox.py`, and the
disposable PostgreSQL wrapper is `scripts/with-disposable-postgres.sh`.

The last commit that holds the removed files is `e286631b`.

## Options not taken

- Keep the harness dormant. It cost about 5 MB of source and half of the
  Python unit tests for no result.
- Replace it with a smaller evaluation. Nothing is planned.

## Reopen when

The owner asks for a measurement of whether memory changes agent outcomes.
Start from the question, not from this harness.
