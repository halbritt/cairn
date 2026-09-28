# Operator review and explicit reissue

These local operator commands use Cairn's existing OS-user store channel. They
are not new agent profiles or session credentials. Set `CAIRN_DATABASE_URL` to
the operational store using the normal local setup.

## Review

```sh
cairn coordination-review <<'JSON'
{"repo":"/home/halbritt/git/cairn","state":"failed","limit":20}
JSON
```

The response has four sections:

- `deliveries`: a bounded page of delivery metadata, current holds, latest native
  and wake attempts, selected failure reasons, linked receipt, latest recorded
  assessment, and any reissue relation.
- `queued_requests`: accepted pool requests awaiting their first delivery, including
  newly reissued pool work. These use publication positions and their own
  `queued_after` and `queued_limit` paging fields.
- `sessions`: a paged directory including offline sessions. Inspect each session's
  `online` and metadata fields to find stale context.
- `workers`: configured slot presence, health, reason, retry time and launch
  backoff. Availability is admission state, not verified provider capacity.

Delivery filters are `state`, `consumer`, and `delivery_id`. `state` accepts
`pending`, `leased`, `held`, `failed`, `handled`, or `ignored`; omit it to see all.
A hold is independent of delivery state: even a handled delivery can retain a
host-owned hold until its process or native turn is reconciled. Paging uses
`deliveries.next_after` as `after`, with `limit` from 1 to 100. Sessions use
`sessions.next_after` as `agents_after`, and `agents_limit` from 1 to 100.
These are current observations; filtering a later scan can produce different
results. Reissue rechecks the selected delivery and its holds atomically.
The optional `attention` request object adds a bounded summary of aged pending
session requests, with changed-condition suppression when given a local state
file. See [delivery health](delivery-health.md) for the invocation, coverage and
freshness rules. This is presentation only; it performs no recovery action.
The latest native attempt includes its turn stop state, cancellation request and
confirmation timestamps, and the count of captured tools not yet reported
terminal. These are selected host observations, not proof that external effects
did not occur; inspect the hold and attempt before reissuing.
`reported_task_outcome` and `assessment` come from the latest wake attempt's
linked receipt only. An earlier wake receipt's assessment, or a delivery handled
only by a native session, is not summarized there; `unknown` does not assert
that no assessment exists elsewhere.

### What the evidence means

`execution` is `never_launched` when no claim exists, or all recorded claims are
wake attempts known to have remained before launch. Otherwise it is
`may_have_executed`. Native delivery and manual claims are conservative: they may
have executed. Older finished attempts without a retained prelaunch report also
remain uncertain.

Delivery `state: handled` records explicit handling. It does not accept the task.
`reported_task_outcome` comes only from the latest assessment of the latest wake
attempt's linked receipt, and defaults to `unknown`. Its `assessment` includes
observer, witness, method, evidence IDs and reason. It is a retained review,
not an independent verification by this view. Exit zero never creates acceptance.

The view excludes source bodies and lease/completion tokens. Use the source
reference or receipt's normal inspection path when the review needs more detail.

## Reissue a failed request

Review prior effects and stop/reconcile any retained host attempt first. Reissue
requires a failed request delivery with no unfinished native or wake attempt.
It never releases a hold or terminates a process for you.

```sh
cairn event-reissue <<'JSON'
{
  "request_id":"NEW_UUID",
  "repo":"/home/halbritt/git/cairn",
  "delivery_id":"FAILED_DELIVERY_UUID",
  "reason":"Reviewed prior effects; repeat the selected task",
  "accept_uncertain_effects":true
}
JSON
```

`accept_uncertain_effects` explicitly acknowledges that the prior attempt may
have changed external state. It is required even when the review reports a
prelaunch failure. Reuse the exact request UUID and JSON after a lost response.
Changed intent with the same UUID is refused. Each failed delivery can have one
successor; review `reissued_as` to find an existing successor.

The new event retains the exact source version, identifies the operator as its
publisher, links the old event as causation, and uses the new request UUID as its
correlation. The original publisher, failed delivery, attempts and results remain
intact. The source must still be available. Privacy and pool admission checks
still apply.

A direct or topic request is sent only to the failed consumer. A topic retry does
not contact subscribers who already handled the first request. An assigned pool
request is reissued to the original pool with its original requirements, allowing
a fresh eligible slot to claim it. The old resolution snapshot is not reused.

`reissued_as` on the old delivery and `reissued_from` on the successor expose the
trace and operator reason. No automatic replay follows a failure or an account
recovery. `cairn retry --lease ...` remains the separate operation for releasing a
currently owned lease; it is not failed-request reissue.

## Release a lost host's native hold

A native attempt holds its delivery until the conversation's own host reconciles
it. If that host never returns, for example a remote machine that was lost or
retired, the hold would block the delivery indefinitely. After reviewing the
attempt, the operator on the central host may release it:

```sh
cairn native-hold-release <<'JSON'
{
  "request_id":"NEW_UUID",
  "repo":"/home/halbritt/git/cairn",
  "attempt_id":"NATIVE_ATTEMPT_UUID",
  "reason":"Archon was lost during the partition; its session never returned",
  "accept_uncertain_effects":true
}
JSON
```

Take the attempt ID from `latest_native` in the review. The command refuses a
session that is still present (`SESSION_LIVE`); a live host must reconcile its
own attempt. Offline presence does **not** prove that the host or its agent
stopped. The work may have run and may still be running, which is why
`accept_uncertain_effects` is required.

Release closes the attempt with reason `operator_released`, `released_by` and
`release_reason`. A delivery still pending or leased becomes `failed` with code
`operator_released` and the operator's control fields. A delivery already
completed keeps its state and result; release then only closes the hold. It
takes the same attempt lock as host reconciliation, and the delivery row lock
that completion uses, so a completion and a release racing each other cannot
both take effect. If the host returns later, its late completion is refused with
`HOLD_RELEASED`. Its journaled result stays in its owner-only intent file for
review, as described in [native inbox](native-inbox.md). Reissue the failed
delivery afterwards as above if the work should run again. Reuse the exact
request UUID and JSON after a lost response.

## Controlled requests

[Request controls](request-controls.md) add expiry and cancellation decisions to
local delivery review, plus a separate `closed_pool_requests` page for requests
closed before assignment. Its `closed_after` and `closed_limit` are independent
of delivery and pending-pool cursors. Reissue does not carry old admission or
task deadline times into the successor. A failed delivery with an unfinished
wake/native hold remains ineligible for reissue.
