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
