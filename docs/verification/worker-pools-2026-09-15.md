# Fresh-work pool dispatch

Status: queue, assignment and explicit availability validated and deployed.
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
At this rollout, automatic quota observation remained to be implemented; health
observations and recovery were explicit. Retry time remains informational.
Generic unsuccessful handling applies a minimum 30-second binding delay without
guessing a quota classification from stderr text. The current seven launchers
map one slot to one independent account binding.

Migration 042 later added [selected native provider observations](provider-failures-2026-09-16.md).

The review proposed timed automatic recovery and fencing every cleanup call.
Those suggestions were not adopted: recovery remains explicit, and blocking
cleanup after restore would strand retained holds. Current exact incarnation
checks govern new launches, while finish remains available after confirmed
process termination.

These tests do not establish the full natural-language routing trial, automatic
idle wakeups, watch/recovery views, schedules, cancellation or response groups.
Those remain required by the active coordination v1 plan.


## Rollout

Implementation `1536e14ba1c2966efc48734a7a83d7c0bb1a8536` is pushed and deployed.
The clean release build repeated the generic and actual OpenCode pool trace
against a separate disposable database. Retained rows show a handled pool
delivery, finished attempt, supervisor incarnation, native OpenCode UUID and
stopped native presence.

No active production wake or native inbox attempts were present before the
upgrade. Watcher and seven supervisors stopped. Backup
`cairn-20260916T062931-3985371.dump` passed catalog, SHA-256 and archive checks.
The prior binary is retained as `cairn-before-worker-pools`. Migration 041 was
applied and the existing seven launch configurations gained matching pool
metadata. The `coding` pool is enabled for the Cairn workspace, with 100
outstanding requests total, 20 per publisher, and three-second claim spacing.
All seven slots advertise configured `code` and `review` capabilities.

CLI and running API both report clean revision `1536e14`. API, presence watcher,
seven supervisors and Hermes gateway are running. Each supervisor restarted
once when it reached the API before its socket was ready, then registered
successfully. Wait for an actual API response before starting supervisors on
future coordinated upgrades; systemd starting a simple service does not mean its
socket is ready. The API's semantic-worker settings were preserved. No native
plugin changed, and Hermes did not require another conversation restart.

All seven slots report live supervisor presence and configured availability.
That is admission state, not seven successful provider capacity probes.
Skillpack `e4539d3` is pushed and deployed across its existing harness targets;
installation checks report 49 skills, zero failures and two pre-existing warnings.
The three reviewer responses were published from its native UUID and read and
acknowledged. Production received selected real engineering requests/results;
synthetic pool traces remained in disposable databases.
