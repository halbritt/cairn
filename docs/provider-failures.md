# Worker provider failures

Migration 042 records a selected provider failure on a wake attempt and changes
its owning worker slot to `unavailable` in the same PostgreSQL transaction.
Both Codex homes and both Claude homes retain independent slot health. The
existing profile and supervisor UUID associate the observation with the slot;
this adds no session credentials or local security boundary.

## Native observations

| Harness | Recognized observation | Limits |
| --- | --- | --- |
| Codex | `exec --json` terminal `turn.failed`, with either of the two exact diagnostics below | Labelled `native-diagnostic`; other wording is unclassified. |
| Claude Code | `system.api_retry` with `error_status:429` and `error:"rate_limit"` | Labelled `native-event`; requires a retry event. A later different retry failure, successful assistant message or successful result clears the candidate. |
| OpenCode | JSON `error` containing `APIError.data.statusCode` 429 or 402 | Labelled `native-event`; 429 means rate limit, 402 means billing. |
| Hermes | Native `api_request_error` hook with classifier reason `rate_limit` or `billing` | Labelled `native-hook`; subsequent successful `post_api_request` or a different error clears the candidate. Unverified billing classifications are excluded. |
| Agy | No verified automatic provider observation yet | Use explicit `worker-health` after an operator observation. |

The two recognized Codex diagnostics are:

```text
Quota exceeded. Check your plan and billing details.
exceeded retry limit, last status: 429 Too Many Requests
```

These are harness observations, not proof of the provider's remaining capacity.
Unknown diagnostics, authentication failures, server overload and tool errors
remain ordinary process outcomes. Codex subscription-limit wording outside the
two exact strings is not classified. `available` permits admission; it does not
certify provider capacity. Parser coverage must be revisited when native versions
change. Claude can exit zero after an API error; process exit alone is not used
to classify it or complete the delivery.

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
HTTP 429 provider. See [verification](verification/provider-failures-2026-09-16.md).
