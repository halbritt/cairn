# Worker provider failures

Migration 042 records a selected provider failure on a wake attempt and changes
its owning worker slot to `unavailable` in the same PostgreSQL transaction.
Both Codex homes and both Claude homes retain independent slot health. The
existing profile and supervisor UUID associate the observation with the slot;
this adds no session credentials or local security boundary.

## Native observations

| Harness | Recognized observation | Limits |
| --- | --- | --- |
| Codex | `exec --json` terminal `turn.failed`, with either of the two exact diagnostics below or the observed subscription-limit templates | Labelled `native-diagnostic`; other wording is unclassified. |
| Claude Code | `system.api_retry` with `error_status:429` and `error:"rate_limit"` | Labelled `native-event`; requires a retry event. A different retry failure or successful assistant message clears the candidate. A terminal result retains it only for `is_error:true`, `terminal_reason:"api_error"`, `api_error_status:429`. |
| OpenCode | JSON `error` containing `APIError.data.statusCode` 429 or 402 | Labelled `native-event`; 429 means rate limit, 402 means billing. |
| Hermes | Native `api_request_error` hook with classifier reason `rate_limit` or `billing` | Labelled `native-hook`; subsequent successful `post_api_request` or a different error clears the candidate. Unverified billing classifications are excluded. |
| Agy | `stream-json` result with an anchored native HTTP 429 / `RESOURCE_EXHAUSTED` retry diagnostic, when the latest step is a completed error | Labelled `native-diagnostic`, kind `rate_limit`. Requires matching conversation and step history; other provider wording remains unclassified. |

The two recognized Codex diagnostics are:

```text
Quota exceeded. Check your plan and billing details.
exceeded retry limit, last status: 429 Too Many Requests
```

These are harness observations, not proof of the provider's remaining capacity.
Unknown diagnostics, authentication failures, server overload and tool errors
remain ordinary process outcomes. Codex 0.154.0 subscription-limit templates
start with `You've hit your usage limit.` and give one of four observed next
steps: upgrade to Pro and purchase credits, purchase credits, contact the team
admin, or upgrade to Plus. The parser requires the full known next-step wording
followed by `try again later.` or a bounded displayed `try again at ...` time.
It retains `codex_usage_limit_reached` with kind `quota`; the human-formatted
reset time is not converted into `retry_at` because it omits an unambiguous
timezone. Intermediate `error` events and tool/model text do not classify.
Successful completion clears the candidate. `available` permits admission; it does not
certify provider capacity. Parser coverage must be revisited when native versions
change. Claude can exit zero after an API error; process exit alone is not used
to classify it or complete the delivery.

Claude 2.1.273 can follow a 429 retry with a non-retryable HTTP 400. Its terminal
result still says `terminal_reason: "api_error"`, but `api_error_status: 400`
supersedes the earlier rate limit. Missing or different final status clears the
candidate; no error-text matching fills that gap. A supervisor timeout before
the terminal result retains the last observed retry classification.

Agy 1.2.4 can return `status: ERROR` and an old 429 diagnostic after a successful
response. The parser therefore tracks the greatest `step_index` in the initialized
conversation and requires its type to be `error_message` with state `DONE` before
classifying `result.error`. A later response or other step clears that condition;
an older step update cannot revive it. Child conversation events and step text
cannot classify a failure. The recognized diagnostic begins `API error (attempt
N): Error 429, Message:` and includes `Status: RESOURCE_EXHAUSTED` and the native
details field. It records `agy_http_429`, not a claim of exhausted subscription
quota. A partial result at Agy's print timeout may carry this observation even
with exit code zero. Unknown or missing envelopes remain unclassified.

Configure Agy's print timeout below the supervisor timeout so it can emit its
result before host cleanup; the installed 600-second worker uses `9m30s`.
This leaves a margin, not a guarantee under arbitrary host stalls. Hard task
deadlines can still stop a process before it emits a provider observation.

## Retention and recovery

`CAIRN_WAKE_CONTEXT` includes `provider_observation_file` and `provider_harness`
for registered slots. The owner-only file holds `cairn.provider-observation/1`,
the attempt UUID, harness and selected failure or null. Writes replace the file
atomically and sync it before the API report. No request, message, raw error,
provider URL or credential is copied into the observation or database.

The stdout parser accepts bounded JSON lines up to 1 MiB and resumes after an
oversized line. It observes the stream independently of the diagnostic log's
4 MiB prefix limit. Hermes writes through its lifecycle hook instead. A child
conversation cannot update an already-linked parent's observation.

After execution, `wake-change` report includes `provider_failure`; supervisor
cleanup can submit the same field with finish if the report was lost. Selected
fields are `harness`, `source`, `kind`, `code`, optional HTTP `status` and optional
`retry_at`. `wake-attempts` exposes the observation and `provider_failure_at`.
Only registered, executed attempts can record it. A harness mismatch is refused.
An identical finish preserves the first observation without repeating the health
change. A different observation after the first returns `VERSION_CONFLICT`.

Cleanup remains possible after restore and lease expiry. The supervisor confirms
the process unit has stopped before finishing. An unreadable local observation
surfaces an error while cleanup still runs; it is never guessed into a quota
failure. Host crashes before an observation is written can still lose it.

Heartbeat, restart and `retry_at` never heal account health. Recovery uses the
existing revision-checked [worker-health operation](worker-pools.md#availability-and-recovery).
Explicit recovery after a recorded failure cannot be undone by replaying that
report or finishing the same attempt. Accepted work stays queued while no slot
is eligible. Executed work is never automatically replayed on another account.

## Verification

Store tests cover rollback of report and health together, separate account
health, exact retries, explicit recovery, and retained failure after restore.
Parser tests cover tool-output isolation, retry recovery, later server errors,
chunk boundaries, oversized lines and persistence failures. The pool runtime
probe exercises the real API/systemd path and retained quota rows through
backup/restore. Its `--hermes` option uses the installed native binary and a local
HTTP 429 provider. Its `--codex` option uses isolated Codex homes and a loopback
`usage_limit_reached` provider for Plus/Pro variants, including native session
association and slot suspension. No real account is charged or exhausted.
The `--agy` option uses an isolated settings mount and loopback provider to check
rate-limit reporting, ordinary errors, a later different error and successful
recovery with a stale final diagnostic. See [Agy verification](verification/agy-provider-failures-2026-09-16.md).
See [verification](verification/provider-failures-2026-09-16.md).
The `--claude` option checks the installed executable's retry event through a
managed timeout, successful recovery and a later non-retryable HTTP 400, including
native session association and actual slot health. See
[Claude verification](verification/claude-provider-failures-2026-09-16.md).
