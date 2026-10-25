# CAIRN-220 watcher eligibility probe, 2026-10-24

`scripts/check_notice_watcher_eligibility.py` runs a real `coordination.py
watch --once` process against a fresh disposable PostgreSQL database and local
Cairn API. Its registered existing-session Codex owner is a synthetic live
child process. The child has no native queue endpoint. The binding enables
idle wake, so the watcher must record `native_transport_unavailable` for an
eligible delivery instead of submitting a model prompt.

The publisher opts one routine notice into a 30-second fixed deferral. The
first watcher cycle records no ready delivery or wake attempt before that
deadline. An immediate request then becomes the ready delivery; the next
watcher cycle records its exact delivery ID and coded missing-transport
refusal while the routine notice is still deferred. The probe explicitly
settles the synthetic request as failed and reconciles its hold. It then
checks that an unrelated profile cannot flush the routine notice, and that
the original publisher can release it before the fixed deadline using a
stable request ID. A final watcher cycle observes the original routine
delivery. Event and delivery IDs, source ref, policy owner/version, and
pending state survive the flush.

The extended probe also publishes an explicitly urgent notice while the routine
notice is still deferred. The watcher observes that urgent notice's exact
delivery ID at the eligibility boundary, and the synthetic claim and failed
settlement retain its event/delivery identity. After reconciliation, readiness
still hides the original routine notice until its publisher flushes it. This
checks urgent-notice bypass separately from request bypass.

`TestRoutineNoticePublicationRollsBackWhenDeliveryInsertFails` injects a
PostgreSQL trigger failure after the routine notice event insert. It verifies
that publication leaves neither an event nor a delivery, then retries the same
request after removing the trigger and checks that exactly one delivery commits
with the original fixed deadline.

The same disposable run settles those initial fixture deliveries, reports the
synthetic owner busy, and publishes another routine notice with a fixed
60-second deadline. It restarts the local Cairn API process and verifies that
the original delivery and deadline survive. The watcher reports `busy` without
a wake. The probe advances only that disposable delivery's availability to
simulate deadline expiry: the original delivery remains pending, but the busy
session's readiness hint stays empty and the watcher still reports `busy`.
Once the owner reports idle, Store readiness names the original delivery and
the next watcher process reports
`native_transport_unavailable` for that exact delivery. No notice is claimed
or replaced by the watcher.

This joins Store eligibility, local API authentication, and an actual watcher
cycle. The first request and notice settlements are fixture cleanup; no actor
handled them. The absent Codex queue endpoint means no native wake or model
turn occurred. The deadline acceleration is a disposable test mutation, not
an observed 60-second wait. The probe does not establish measured latency,
wake reduction, provider usage, focused-host behavior, or one-account canary
acceptance.

The watcher probe still does not submit to a native Codex queue or observe a
Codex queue receipt. The added urgent-notice case ends at the same explicit
`native_transport_unavailable` boundary as the earlier request case. Store
rollback and successful retry are covered by the PostgreSQL test, not by the
native handoff path. The CAIRN-220 acceptance gap therefore remains open for a
queue-accepted wake, paired provider-accepted outcome, and measured latency.

Validation on 2026-10-24: the full `make test-integration` run passed, including
the race-enabled Store suite and the 10-second form of this probe. After
increasing this probe's deadline to 30 seconds for extra concurrency margin, a
fresh focused disposable-cluster run also passed. `make check` passed on the
candidate branch after the code changes.

## Opt-in real-clock arrival observation, 2026-10-24

The optional `--real-clock-pilot` leg adds one real-clock 60-second routine
notice after the deterministic state-transition cases. It publishes to a fresh
disposable PostgreSQL/API and uses a registered synthetic idle Codex owner plus
the real `watch --once` process. It records each watcher cycle's UTC start/end,
elapsed time, condition, and exact delivery ID. No database deadline is edited
during this observation. The normal `make test-integration` path does not pass
this flag and retains only the deterministic cases.

Run the deterministic watcher probe by itself with:

```sh
bash scripts/run_notice_watcher_probe.sh
```

Opt into the additional 60-second wait with:

```sh
bash scripts/run_notice_watcher_probe.sh --real-clock-pilot
```

The wrapper creates and removes a private PostgreSQL cluster, applies migrations
to its dedicated database, builds a local CLI, and invokes
`check_notice_watcher_eligibility.py` with the matching optional flag. Without
the flag, only deterministic checks run. Both modes make no provider call and
change no production database or global setting.

Observed example: the durable deadline was 60.000 seconds after `created_at`.
The watcher recorded 12 pre-deadline cycles with no ready delivery, then observed
the original delivery 60.151 seconds after publication, 151 ms after its stored
deadline. There was one eligible wake decision, zero accepted native wakes, zero
provider calls, and no model turn. The source was synthetic and
`actionability=unassessed_no_actor_or_task_grade`.

The retained timestamps were `created_at=2026-10-24T19:33:45.94744-07:00`,
`deadline_at=2026-10-24T19:34:45.94744-07:00`, and
`observed_eligible_at=2026-10-25T02:34:46.098405Z`.

After the real-clock leg became opt-in, a second standalone pilot run recorded
`created_at=2026-10-24T19:39:33.535112-07:00`,
`deadline_at=2026-10-24T19:40:33.535112-07:00`, and
`observed_eligible_at=2026-10-25T02:40:33.657252Z`: 60.122 seconds from
publication and 122 ms after the deadline. It again observed 12 pre-deadline
cycles, one eligible decision, zero accepted native wakes, and zero provider
calls; actionability remained unassessed.

This is a timestamped arrival-to-eligibility latency sample for one notice. It
does not compare a multi-arrival baseline with a treatment, and therefore does
not demonstrate fewer wakeups. The synthetic owner has no native queue endpoint.
Busy, idle, API restart, request bypass, urgent-notice bypass, and publisher
flush remain covered by the earlier disposable state-transition probe.
Focused-host behavior and starvation under sustained arrivals remain untested.
CAIRN-220 stays open for arrival-based wake reduction and native-host outcomes.
