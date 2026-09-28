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

A missing transport suggests inspecting the recipient's configured native
delivery route. A retained submitted or uncertain wake suggests inspecting that
exact wake and native handling. Neither observation authorizes a new wake,
replacement request, manual claim, or deletion of a retained marker.

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
Busy recipients, scheduled availability, and currently available automatic
delivery routes do not produce attention items. An aged request with missing,
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
