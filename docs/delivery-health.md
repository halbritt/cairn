# Delivery health observations

Presence and delivery health answer different questions. An online, idle session
can still lack an automatic wake transport. A submitted wake can remain unclaimed.
Delivery health makes those host observations available alongside delivery
progress so an owner can investigate without first reconstructing the watcher
journal.

These are diagnostics, not delivery guarantees or instructions to replay work.
The host uses the existing authenticated profile and session association. The
profile may also be available to the agent, so the report is not independent host
attestation. Ordinary session metadata remains reported context.

## Host reporting

The lifecycle watcher calls `session-delivery-observe` through its existing
profile. The store checks profile ownership, session execution and database
generation. An observation names a closed condition, its observation time,
and the delivery it covers when applicable; the store adds the receipt time.
One current row per session is retained, without a mutation receipt per poll.

Persisting observations refresh within 60 seconds on the usual 30-second
watcher cadence. They become stale after 120 seconds measured against either
the host observation time or store receipt time. Expired session presence,
replacement execution and restore fencing also invalidate applicability.
Retries of the same observation cannot renew its freshness; clearing retains
an ordering marker so a delayed older report cannot resurrect the old cause.

The reporting call has a short timeout and fails soft if the installed CLI or
API lacks the route. Ordinary delivery still uses its existing wake, claim and
reconciliation rules. An empty readiness hint records unknown rather than
available. No journal text, request body, native process ID or credential is
included in the observation.

## Reading a diagnosis

Check the delivery state first. A claim is progress; explicit handling is
completion of the inbox delivery. A separately published reply and acceptance of
the requested work remain separate facts. An earlier host refusal must not
override later progress.

Check which session execution and delivery the observation covers, its age, and
whether it is current. A report about one pending delivery does not establish
the cause of another delivery's delay. Missing or stale observations mean
unknown. An empty readiness hint does not establish an empty inbox: busy
sessions, retained holds, and admission rules can prevent a new claim.

### Terminal wake target conditions

The Herdr terminal route admits a wake only when exactly one pane is both this
session's own terminal (harness, native session or Codex rollout association,
and foreground ownership by the observed process) and idle/done and unfocused.
Anything else is refused with one of these conditions:

| Condition | Host observation |
| --- | --- |
| `terminal_target_missing` | No pane identifies this session. |
| `terminal_target_not_idle` | Only panes that are not idle/done (working, blocked or unknown) identify it. |
| `terminal_target_focused` | Only idle panes that are focused identify it. |
| `terminal_target_ambiguous` | More than one eligible pane matches. |
| `terminal_host_unavailable` | The native process has no Herdr host environment. |

The watcher log reports only pane counts. Pane IDs, terminal content and
process environments are not included in the observation. Before 2026-10-21
hosts reported every count other than one as `terminal_target_ambiguous`; those
stored rows remain readable, but an older ambiguous report may describe zero
matching panes. None of these conditions causes a pane to be selected, a prompt
to be injected, work to be replayed or a session to be restarted. With exactly
one eligible pane, the watcher still revalidates pane, terminal, revision,
state sequence, process ownership and Cairn readiness before submission; a
target that changed in between defers to a later cycle.

Waiting is not explicit acknowledgement. A delivery that stays pending behind
any of these conditions has not been claimed or handled, however long it waits.
Only a native claim and explicit handling record progress.

A missing transport suggests inspecting the recipient's configured native
delivery route. A retained submitted or uncertain wake suggests inspecting that
exact wake and native handling. Neither observation authorizes a new wake,
replacement request, a claim of another session's inbox, or deletion of a
retained marker. The recipient session itself may drain its own inbox when
native delivery has stalled; see [the waiting-work cue](native-inbox.md#waiting-work-cue-during-long-turns).

### Missing retained Codex queue item

For a confirmed native queue submission, the watcher retries only that item's
start. The exact native `-32600` response `queued submission not found: <item ID>`
now stops those retries. It retains the original delivery, session, queue item
and attempted timestamp with `status: refused`, `last_start: missing` and
`reconciliation_required: queue_item_missing` in protected host state. The next
watcher observation reports the existing `wake_retained` / `refused` labels,
and the host logs that reconciliation is required. The API does not expose the
more specific local reason. A later ready delivery cannot replace this marker.

This response establishes absence at the native observation only. The item may
already have been admitted or removed; it does not establish task outcome or
authorize replay, cancellation, release or replacement. A concurrent hook's
admission or closure supersedes the pending start observation. A genuine late
native delivery can still claim through its exact wake binding.

Busy, other structured refusals, unknown responses and unavailable transports
remain distinct `last_start` values and retain the existing exact-item retry
rule. None authorizes another queue add. Offset-paged queue listings are not an
atomic absence proof and are not used by this diagnostic. An ordinary owner
prompt does not claim work when the Codex native queue owns inbox admission.
There is no new operator reconciliation command in this change.

## Opt-in attention on the operator review

Add `attention` to the existing local operator `coordination-review` request:

```sh
cairn coordination-review <<'JSON'
{
  "repo": "/home/halbritt/git/cairn",
  "state": "pending",
  "limit": 100,
  "attention": {
    "older_than_seconds": 300,
    "state_file": "/absolute/private/directory/cairn-attention.json"
  }
}
JSON
```

The parent directory must already exist. `state_file` is optional; without it,
each invocation is an independent snapshot. With it, `attention.changed` shows
new request conditions, `attention.cleared` shows conditions that no longer
apply, and an unchanged scan produces empty arrays for both. The ordinary
review sections and the current `attention.groups` remain available for
inspection. No summary runs unless explicitly requested; this installs no
poller and triggers no agent turn.

Groups show recipient, cause, freshness, applicability, oldest waiting age and
a sample delivery. Request, response and notice counts remain separate. A new
notice or an increasing age alone does not produce a changed request condition.
Busy recipients and scheduled availability do not produce attention items.
A route reported as available still warrants attention if the request remains
unclaimed beyond the cutoff: route availability does not prove progress.
An aged request with missing,
stale or inapplicable host information is labelled `unknown`, not healthy.
The cutoff defaults to 300 seconds and accepts 1 through 604800 seconds.

Coverage is limited to the returned delivery page; this summary does not
inventory queued pool requests. Follow the normal delivery cursor to inspect
additional pages. A partial page can clear a condition when it explicitly
shows progress for that delivery, but absence alone cannot clear it. Only a
complete scan starting at the first page can clear conditions by absence. This
is deliberately conservative: inspect an individual delivery or narrow the
review filter if old conditions remain outside the current page.

The local cache contains selected IDs and closed diagnostic labels, not request
text. It is bounded to 1000 requests and written with owner-only permissions.
Use separate cache paths for different repositories, database connections,
filters or age thresholds; a scope mismatch is refused. Keep normal request
handling and recovery separate from clearing this presentation cache.

## What to measure

The OpenRig comparison found pending requests whose refusal conditions were
already logged but difficult to connect to the request's status. The proposed
benefit is earlier recognition of wanted, stalled work with less journal
inspection. More diagnostics alone do not establish that benefit.

Use a bounded trial with representative wanted requests. Record when a useful
condition first appears, when the owner notices it, and which action it informs.
Count repeated unchanged warnings and false alarms from intentional waiting.
Check that progress clears or supersedes the condition, and that incomplete
coverage stays visible. Keep the feature if it improves recognition at an
acceptable attention cost. Synthetic tests establish the mechanics only.

Complete-and-reply, automatic recovery, and changes to team structure are outside
this change.

The [verification report](verification/delivery-health-2026-09-27.md) records
the inspected commits, executed checks, cross-review corrections and rollout
limits. See [stopped-session backlog](stopped-session-backlog.md) for stranded
inbox reporting and opt-in publisher notifications when a session stops.

