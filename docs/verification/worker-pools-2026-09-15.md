# Fresh-work pool dispatch

Status: implementation validated; rollout is recorded below when observed.
Coordination v1 remains incomplete.

Migration 041 adds explicit pool requests, one-time assignment, supervisor
incarnations, configured eligibility, per-slot availability and publisher
fairness. The original destination remains the pool. Continuing session UUIDs,
account homes and slot-owned completion retain their existing meanings.

## Evidence

Disposable PostgreSQL tests cover concurrent claims by the same and different
slots, concurrent bounded publication, exact publication/claim retries, publisher
fairness, workspace/model/harness/capability matching, independent account health,
explicit recovery, launch spacing and old-supervisor refusal. Heartbeat and
re-registration preserve an unavailable account. A passing regression prevents
assigned pre-launch retries from bypassing pool pause or changed workspace.
An injected wake-insert failure rolls back the first delivery and assignment;
the same claim UUID can then succeed.

Restore fencing retains the assignment and unfinished hold. Old incarnations
cannot start work; confirmed cleanup remains available. A replacement supervisor
can retry proven never-started work through the same delivery. API tests cover
publication, queued inspection, health, heartbeat, recovery and claim.

The owner-supplied Agy session reviewed health/recovery and implemented the
isolated runtime probe. Its generic probe uses the actual API, systemd supervisor,
worker context and explicit completion. Its OpenCode probe uses the installed
harness with a loopback provider, asserts a native conversation association and
stopped presence after cleanup, and confirms tool-driven result completion.
Follow-up review required these native assertions before reporting linkage.
Selected results were returned from the reviewer's native UUID and read and
acknowledged by the parent. These are useful engineering exchanges with an
explicit host wake, not evidence of automatic idle-session wake support.

The full integration suite and static checks passed. The real lifecycle test
ran the pool probe, backed up populated pool configuration, slot, assignment and
publisher-service rows, and compared them after restore along with retained
events, deliveries, wake attempts and native sessions.

## Limits

Availability permits admission; it does not demonstrate provider capacity.
Structured automatic quota observation remains to be implemented. Current
health observations and recovery are explicit, and retry time is informational.
Generic unsuccessful handling applies a minimum 30-second binding delay without
guessing a quota classification from stderr text. The current seven launchers
map one slot to one independent account binding.

The review proposed timed automatic recovery and fencing every cleanup call.
Those suggestions were not adopted: recovery remains explicit, and blocking
cleanup after restore would strand retained holds. Current exact incarnation
checks govern new launches, while finish remains available after confirmed
process termination.

These tests do not establish the full natural-language routing trial, automatic
idle wakeups, watch/recovery views, schedules, cancellation or response groups.
Those remain required by the active coordination v1 plan.
