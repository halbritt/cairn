# Delivery-health verification — 2026-09-27

This change makes host-reported delivery conditions visible next to request
progress and provides an opt-in operator attention summary. It does not replay
requests, alter team structure, add complete-and-reply, or establish task
acceptance. The implementation followed the OpenRig comparison's bounded
visibility recommendation.

## Inspected source and collaboration

Base: `9548189d56884217142a5c2aa2b0f60fac3737cb`.
Combined implementation under validation:
`08d0637868dd8ddf1f37ffe5f8eff8e5f6fba52c`.

Agent 210 implemented the Go/store/API observations and diagnosis. Agent 213
implemented the lifecycle reporter. Agent 211 implemented the operator attention
surface, integrated the branches and ran the combined checks. All code work used
separate worktrees; existing main and running services were untouched during
validation.

Cross-review changed the implementation:

- An available route does not prove progress; an aged unclaimed request still
  merits attention.
- A lapsed lease can retain `state=leased`; the store's waiting diagnosis permits
  attention while an active lease does not.
- An old refusal cannot be refreshed merely because the watcher is running or
  the same delivery is ready. The refusal must be observed again on that pass.
- Claims, holds, completion and later retry progress supersede earlier host
  causes. Stale, replaced, withheld and inapplicable observations remain unknown.
- Reports about local-only deliveries are withheld from hosted reads. Request
  status omits another delivery's identifier; operator review retains its own
  broader scope.
- Ordering normalizes timestamps to PostgreSQL microseconds. An exact retry,
  including a nanosecond client timestamp, cannot refresh receipt age. A clear
  retains an ordering marker against delayed reports.

## Static example

This is a synthetic example, not a production measurement. A request is pending
for 15 minutes to an online, idle session. The watcher has observed that its
Claude channel was not launched.

| Surface | Before | With this change |
| --- | --- | --- |
| Request status | Pending, no attempts; no host cause | Waiting; host-reported `channel_not_launched`; current execution, applicability and observation time |
| Watcher journal | Refusal line requiring manual correlation | Existing line remains; the selected condition is also reported |
| Opt-in operator review | Delivery rows require individual inspection | Changed request condition grouped by recipient/cause; separate notice/response counts |
| Next unchanged review | Same investigation repeated manually | `attention.changed` and `attention.cleared` are empty |
| After a claim/completion | Progress in existing delivery fields | Earlier pre-claim cause is superseded; attention clears on observed progress or a complete scan |

The summary is explicitly requested through `coordination-review`; no timer,
model call or native wake is installed. Partial pages retain uncertainty and
cannot clear an absent condition. Busy and scheduled waiting are quiet.

## Validation

All runs below completed against the frozen combined implementation above:

- `make test-integration`: passed. Each Go package ran with the race detector
  against its own disposable PostgreSQL database. The suite also passed the
  real CLI/API delivery-health probe, event/recovery checks, multi-machine and
  protocol-skew probes, native cancellation fixtures, supervised worker/pool
  controls, and existing memory/API checks.
- `make check`: passed, including `go vet`, generated API contract consistency,
  Go formatting and the four pinned design-source hashes.
- `python3 -B -m unittest discover -s scripts -p 'test_*.py'`: passed, with
  429 tests run and 15 skipped (117.545 seconds). The focused lifecycle suite previously
  passed 61 tests after the final producer correction.

The real API/CLI delivery-health probe passed on disposable PostgreSQL:
unknown before a report, current exact diagnosis afterward, foreign profile
refusal, duplicate freshness preservation, unchanged-summary suppression,
partial-page retention, claim precedence and clear after explicit completion.
Store regressions cover execution/generation fencing, stale data, retained
clear ordering, local-only observation withholding, nanosecond retry precision,
and post-claim waiting. Attention regressions cover notice/response separation,
intentional waiting, expired leases, available-but-unclaimed requests and
cache scope/size limits.

These are tests actually run, not merely inspected. No live provider was needed
for this feature's probe. The wider worker probes use fixture processes when a
native harness is not supplied; their passing result is not native-provider
acceptance or a production latency measurement.

## Limits and rollout

No production database migration, binary replacement, lifecycle installation,
service restart, inbox replay or marker removal was performed. Migration 055 and
matching CLI/API/lifecycle source must be installed through the usual Cairn
upgrade process before these observations can appear in the running system.
The reporter fails soft against an older CLI/API.

The existing profile authenticates association with a session, not independent
host truth; an agent sharing that credential could submit a report. The diagnostic
call is capped at 0.5 seconds before native submission, and unchanged reports
refresh within 60 seconds on the usual watcher cadence. Reports expire after
120 seconds or earlier when session applicability is lost. A slow host can have
unknown diagnostics; increasing the timeout trades visibility against wake delay
and needs measurement.

The tests establish mechanics. Earlier owner notice, useful corrective action,
acceptable noise and reduced coordination effort need a bounded live-use trial.
See [delivery health](../delivery-health.md) for the opt-in command and trial
criteria. Neither transport repair nor coordination-v1 acceptance follows from
this change.
