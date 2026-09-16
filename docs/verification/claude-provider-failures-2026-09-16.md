# Claude provider-failure verification — 2026-09-16

## Observed defect and repair

Installed Claude Code 2.1.273 emitted a `system.api_retry` with HTTP 429 and
`error: "rate_limit"`. When the loopback provider next returned HTTP 400, Claude
emitted no new retry event. It instead emitted an error assistant message and a
terminal result with `is_error: true`, `terminal_reason: "api_error"` and
`api_error_status: 400`. The previous parser retained the earlier 429, so the real
supervisor incorrectly marked its worker slot unavailable.

The repair requires terminal `api_error_status: 429` to retain an earlier retry
candidate. Different or missing terminal status clears it. The terminal result
cannot create a new classification by itself. A managed timeout before a result
still retains the most recent observed retry failure. Process exit and task
completion remain separate from provider classification.

## Reproduction and verification

- The first native API/systemd probe failed on the 429-then-400 case against
  installed Cairn `1f2f388`; selected log:
  `/tmp/cairn-claude-provider-runtime.log`.
- `TestClaudeTerminalErrorSupersedesEarlierRateLimit` failed before the repair
  for both differing and missing terminal status; selected log:
  `/tmp/cairn-claude-provider-red.log`.
- `go test -race ./internal/wakeup`, `make test` and `make check` passed.
  The general suite ran 109 Python tests; its database tests skip without a test
  DSN. No store implementation or migration changed.
- The corrected full API/systemd pool probe passed all three native cases:
  `/tmp/cairn-claude-provider-runtime-2.log`. The final fixture revision narrows
  the writable mount to its test directory and also passed:
  `/tmp/cairn-claude-provider-runtime-3.log`.
- A separate installed-native probe exhausted ten retries and emitted terminal
  `api_error_status: 429`, `is_error: true`, `terminal_reason: "api_error"`.
  It finished naturally in about 174 seconds. This confirms the retained-status
  envelope, separately from the supervised timeout case:
  `/tmp/cairn-claude-terminal-probe.log`.

The optional `scripts/check_worker_pools.py --claude PATH` probe uses explicit
native hooks, a private Claude config directory, fake loopback credentials,
disabled model tools and an explicit empty MCP configuration. Read-only host
mounts protect installed configuration. The supervisor controls the real process
unit; hooks associate the actual Claude conversation with the attempt.
The API and PostgreSQL database are disposable. No real provider is called.

The probe checks retry retention through supervisor timeout, retry recovery after
HTTP 200, and HTTP 429 followed by HTTP 400. It inspects actual retained failure
and slot-health fields, then explicitly recovers the suspended slot. These
model responses deliberately do not complete the Cairn task: delivery failure
is expected and does not imply a provider failure.

The offered agent reviewed the parser, regression and fixture. Its selected
result, Cairn `22d84d51-4e25-42d5-88d7-636421c68966/1`, was read and acknowledged.
It confirmed the parser repair and identified the `/tmp` mount assumption,
which was corrected. A thread-start failure now closes the fixture listener.
Its claimed supervisor leak was not confirmed: the existing outer `finally`
terminates the supervisor, tracked units and API. A failed assertion exits the
whole probe, so restoring the local timeout setting on that path is unnecessary.

## Limits

These cases do not establish provider capacity, all Claude error envelopes or
successful task execution. Older native versions without a terminal HTTP status
leave that result unclassified. Both Claude homes use this parser but retain
separate slot health; this loopback test does not charge or exhaust either
account. The live Rhumb session still awaits idle before its authorized restart.

The [decision receipt](../plans/claude-provider-failures-review.json) records
the bounded repair, verified evidence, alternatives and remaining limitations.

## Deployment

Clean CLI/API build `1846f95` was installed and verified at 11:07:51 UTC.
Migration 047 and its digest were unchanged. API, presence, scheduler, all seven
worker services and the Hermes gateway were running with zero restart counters.
All seven slots were online and available for admission; this is not a provider
capacity measurement. Semantic-worker arguments and Agy's `9m30s` print timeout
were preserved. Selected verification:
`/tmp/cairn-claude-provider-install-verification.json`.

Before replacement, the backup catalog checksum and archive inventory were
verified, and no unfinished wake/native holds were present. Backup:
`~/.local/share/cairn/backups/cairn-20260916T110418-785935.dump`.
Consumers and API were stopped and their zero process IDs checked before atomic
binary replacement. The Rhumb process was not restarted.
