# CAIRN-220 watcher eligibility probe, 2026-10-24

`scripts/check_notice_watcher_eligibility.py` runs a real `coordination.py
watch --once` process against a fresh disposable PostgreSQL database and local
Cairn API. Its registered existing-session Codex owner is a synthetic live
child process. The child has no native queue endpoint. The binding enables
idle wake, so the watcher must record `native_transport_unavailable` for an
eligible delivery instead of submitting a model prompt.

The publisher opts one routine notice into a ten-second fixed deferral. The
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

The extended probe passed against a fresh disposable cluster, followed by
`make check` on the candidate branch.
