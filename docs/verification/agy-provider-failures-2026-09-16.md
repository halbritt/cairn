# Agy provider-failure verification — 2026-09-16

## Native evidence and resulting contract

Agy 1.2.4 emits `event`/`result` JSON. Earlier review inferred `type`/`data`
from the binary; the actual loopback capture disproved that inference.
A 260-second HTTP 429 probe timed out without a terminal result. A later parent
probe used the supported `--print-timeout 10s` and retained raw stdout separately
from its request/status summary.

| Loopback response sequence | Native observation | Cairn behavior |
| --- | --- | --- |
| Repeated 429 | Print timeout, exit zero, latest step `error_message`, result `ERROR` with native HTTP 429 diagnostic | Record `native-diagnostic/rate_limit`, suspend owning slot |
| 400 with quota-like words in its message | Exit one, HTTP 400 diagnostic | No provider classification |
| 429 then 400 | Result carries the later 400 error | No provider classification |
| 429 then successful model response | Exit zero and response text, but result still says `ERROR` with the earlier 429 | Latest `agent_response` step prevents suspension |

Result status alone is insufficient. Cairn requires a matching initialized
conversation, the greatest step index marked `DONE/error_message`, and the
anchored retry diagnostic. Step text does not classify failures. Older updates
cannot revive an old candidate, and another conversation cannot replace the
stream owner. A later result with a different diagnostic clears the candidate.
Unknown wording remains unclassified. No raw diagnostic is stored in the provider
observation; existing atomic reporting, cgroup holds and explicit recovery apply.

This is an observation of rate limiting. It neither establishes subscription
quota exhaustion nor task completion, and it does not heal availability on a
clock. An early hard deadline or host failure may prevent any result emission.

## Verification scope

`TestAgyLatestStepControlsRateLimitObservation` failed before implementation and
passed afterward. Focused race tests cover stale final errors, out-of-order
updates, another conversation, subsequent rate limiting and clearing by a
different failure. Negative cases cover missing indexes, running steps, tool text,
quoted diagnostics and non-quota errors.

The installed executable probe uses `bwrap` to mount a disposable Gemini settings
home, preserves `HOME`, and points the provider at loopback with a fake key.
Only fixture hooks are enabled against the disposable Cairn API. The real
supervisor owns process cleanup. The first runtime fixture failed before a
provider request because `--new-project` needed writable Gemini project metadata;
the corrected fixture isolates the whole Gemini directory.

Selected local evidence: `/tmp/cairn-agy-provider-qeyibo48` holds the raw native
captures; `/tmp/cairn-agy-provider-runtime-2` holds API/systemd probe artifacts.
No real provider account was exhausted. These cases do not prove every Agy
backend's error wording or full coordination v1 acceptance.

Completed checks: `go test -race ./internal/wakeup`, `make test`, `make check`,
and `scripts/check_worker_pools.py --agy` passed. `make test` does not establish
database coverage without a test DSN; the native pool probe used a disposable
PostgreSQL database. No store schema or durability behavior changed.

The [decision receipt](../plans/agy-provider-failures-review.json) selects explicit
failure policy from validated Pincite release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`.
Its material obligations are satisfied; unrelated interface/design obligations
remain classified in that receipt rather than supporting broader claims.

The offered agent reviewed the parser and the raw recovery capture. Its reported
supervisor leak was not supported: the pool probe's outer `finally` stops the
supervisor and tracked units on assertion failure. Its empty-init concern also
does not apply after initialization, where mismatched conversation IDs are
ignored. These review claims were checked against source rather than accepted
as findings. The native fixture was verified using a disposable directory under
`/tmp`; its writable mount is limited to that location and its isolated settings.

## Deployment

Clean CLI/API `1f2f388f8d79441161fca25214f59e5ea0e5faf5` is installed with
unchanged migration 047. Before rollout both unfinished wake and native-attempt
counts were zero. Backup `cairn-20260916T104146-696421.dump` passed catalog digest
and archive-read checks; production data was not restored for tests. Consumers
stopped before the API, all main PIDs reached zero, and the expected clean API
revision was verified before restarting consumers and Hermes gateway.

Agy's print timeout is now `9m30s` within its 600-second supervisor timeout.
At 10:43 UTC all eleven services were active/running with zero restarts; all
seven slots were online/available. Availability permits admission and does not
certify remaining provider capacity. Existing semantic-worker arguments remain.

Skillpack `b21848e` was pushed and installed. Its Cairn event-reference SHA-256
`a07f2c4e455f82dfe95595ae3c91f1f50e9e8a41f5bbac6be3c5de23240d9464`
matches both Codex homes, both Claude homes, OpenCode, both Agy skill locations
and Hermes. Validation reported 49 skills, zero failures and two existing
warnings. Selected installation evidence is
`/tmp/cairn-agy-provider-install-verification.json`. Rhumb's busy Codex process
was left untouched as the owner requested; its live routing trial remains open.
