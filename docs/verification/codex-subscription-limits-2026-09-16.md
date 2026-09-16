# Codex subscription-limit verification — 2026-09-16

Codex 0.154.0 emits subscription-limit diagnostics that the original two-string
classifier did not recognize. The parser now recognizes four observed
plan-specific templates within terminal `turn.failed` events. It records
`native-diagnostic`, `quota`, `codex_usage_limit_reached` through the existing
failure path. See [the contract](../provider-failures.md).

## Evidence

- Installed Codex ran against a loopback HTTP 429 provider returning
  `usage_limit_reached`. Plus, Pro, team and free plan variants and an omitted
  reset time produced the captured templates. An unrecognized workspace-member
  error type instead produced the already-supported exact HTTP 429 diagnostic.
- The new regression failed before the parser change; `go test -race
  ./internal/wakeup` passed afterward. Intermediate error messages and tool
  output do not classify; successful completion clears the candidate.
- `scripts/check_worker_pools.py --codex /home/halbritt/.local/bin/codex` passed
  using the real API, supervisor and installed Codex executable, isolated homes
  and a disposable database. Both Plus and Pro variants registered native
  sessions, retained quota observations and suspended the owning slot. Explicit
  recovery, restart and subsequent blocked admission checks passed.
- `make test` passed all Go packages and 109 Python tests. Without a test DSN,
  this command skips database tests; the native pool probe supplied the relevant
  real database/API verification. `make check` passed. There is no store/schema
  change requiring a new migration or backup/restore campaign.

The first runtime probe's 30-second wait raced a retained 30-second launch
backoff after explicit health recovery. The final bounded wait includes both
admission delay and execution; it passed. The final Plus/Pro fixture also passes
the intended plan to each isolated provider, and retained native output confirms
the two distinct diagnostics.

Local logs are `/tmp/cairn-codex-limit-cases.log`,
`/tmp/cairn-codex-limit-green.log`, `/tmp/cairn-codex-quota-runtime-final.log`,
`/tmp/cairn-provider-coverage-tests.log` and
`/tmp/cairn-provider-coverage-check-final.log`. These contain generated probe
data, not production conversations. No real provider quota was consumed.

## Limits and review

Known templates remain version-sensitive. Displayed reset times lack an
unambiguous timezone and do not become `retry_at`. Unknown wording remains
unclassified. Account availability still means admission is permitted, not that
the provider will accept a request. This does not close the Agy observation or
live Rhumb acceptance gaps.

The [decision receipt](../plans/codex-subscription-limits-review.json) selects
explicit failure policy from validated Pincite packet `pkt-879f059a56816fdd`,
SHA-256 `879f059a56816fddf653dfedd481a7a95cb2442e80fa6188a326ae9912903d10`.
Material obligations are satisfied; broader nonmaterial obligations and
reopening conditions remain recorded there.

## Installed result

Clean CLI/API build `7b1f21e23b62fe2a0162da9985d973d84a903938` is installed.
The parser-only upgrade retains migration 047 and its checksum. Before restart,
unfinished wake and native attempts were both zero. Backup
`cairn-20260916T100700-585391.dump` passed its catalog checksum and archive-read
check. Consumers and API stopped, the binary was replaced atomically, and the
API reported the expected clean revision before consumers restarted.

At 10:11 UTC the API, presence, scheduler, seven workers and Hermes gateway were
active/running with zero restarts. Seven slots were online and available for
admission; provider capacity was not measured. Existing semantic-worker settings
remain. The prior binary is retained as `cairn-before-codex-limits`.

Skillpack `0ee69746a8d5c6c0cf26abb4e9b10677ea997a0a` was pushed and installed.
The event reference SHA-256
`e15bc798a8056c73af2e83ae6163099f699b989092eb92ff39ccb2cd800bf7a5`
matches all eight existing harness locations, including Hermes.
