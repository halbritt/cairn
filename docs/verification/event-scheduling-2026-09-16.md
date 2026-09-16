# One-shot scheduling verification — 2026-09-16

Scope: migration 045 and the [one-shot scheduling contract](../event-scheduling.md).
This is the first part of coordination milestone 6. Admission TTL, running task
deadlines/cancellation, recurrence and full v1 acceptance remain outside this result.

## Verified behavior

`make test-integration`, `make check` and `make test-lifecycle` passed against
disposable clusters. No production database was used for tests or cleanup.
The integration suite ran every Go package with the race detector and exercised
the real CLI/API, worker processes and scheduler. Backup/restore compared complete
schedule rows populated in pending, fired, skipped and cancelled states.

| Check | Evidence |
| --- | --- |
| No early delivery; exact source reference and store-owned publisher | `TestSchedulePublishesOnceAtDueTime` |
| Concurrent schedulers create one event and one delivery | `TestScheduleSimultaneousTicksProduceOneEvent` |
| Failure of terminal schedule update rolls back publication and fanout | `TestScheduleInjectedFailureRollsBackAndRetries` |
| Retracted/deleted source produces a retained skip with no delivery | `TestScheduleSourceRetractionOrDeletionSkipsEvent` |
| Exact creation retry and changed-intent conflict | `TestScheduleIdempotentRetryAndConflict` |
| Topic recipients selected at firing | `TestScheduleTopicMembershipSnapshottedAtFiring` |
| Pool admission refusal cannot block unchecked due work | `TestScheduleFullPoolDoesNotStarveOtherDueWork` |
| Cancellation and firing have one durable winner | `TestScheduleCancellationRacesWithFiring` |
| Distinct DST offset instants, bounded misfires, restore audit and pagination | `TestScheduleMisfireTimezoneAndRestore` |
| Operator ownership and rejected inputs | `TestScheduleOperatorOwnershipAndInvalidInput` |
| Ready daemon stops before due, resumes after due within grace, emits stable UUID once | `scripts/check_agent_events.py` |
| Full schedule rows survive actual backup/restore | `scripts/test-local-lifecycle.sh` |

After the full integration pass, the test-only failure trigger was restricted to
its fixture occurrence and given a unique name. That focused test passed again
with `-race`, and `make check` passed on the final source. Production code did not
change after the full integration run.

Selected local logs: `/tmp/cairn-schedule-integration.log`,
`/tmp/cairn-schedule-check-final.log`, `/tmp/cairn-schedule-lifecycle.log`, and
`/tmp/cairn-schedule-final-focused.log`.

## Review and limits

The offered agent `d48af4ab-adc6-4d35-8dbd-8e11b72f1617` supplied independent
concurrency, rollback, source, retry and topic tests. A subsequent read-only review
found no concrete defect. The parent read those tests, reran the checks and
reviewed the generation/collection/row lock order. Review testimony alone is not
acceptance evidence for untested behavior.

The [decision receipt](../plans/event-scheduling-review.json) retains the bounded
atomicity and failure-policy review. Ordinary publication still uses the same
validation, pool admission and fanout; a shared transaction helper lets scheduled
publication commit its terminal state with those effects.

The pool-rotation test establishes progress for an unchecked direct publication
behind a paused pool with a one-item tick. It is not a throughput or load result.
Timestamp tests establish explicit-offset one-shot instants, not a recurrence
calendar. Restore fences pending occurrences because post-backup execution may be
unknown; no exactly-once claim is made for external effects.

## Deployment

Clean CLI/API build `215f9424188a835cdaf3c0da97115fffeb5579be` was pushed
and deployed with migration 045, checksum
`523120afd5a1ab202e7fb37e911d133f328fcfbe1658408ef39736b6593f9194`.
The pre-upgrade backup `cairn-20260916T082944-182225.dump` passed catalog,
checksum and archive-read checks. Its production data was not restored for tests.
The previous executable is retained as `cairn-before-one-shot-scheduling`.

No unfinished wake or native attempts remained before stopping services. Presence,
seven workers and the Hermes gateway stopped before the API. After migration and
atomic executable replacement, an actual API version response verified the clean
revision before workers restarted. Existing semantic-worker arguments were retained.
The new `cairn-scheduler.service` is enabled and reported readiness after a tick.
All eleven services were active/running with zero restarts, and all seven slots
reported online/available. Availability describes admission state, not provider
capacity or task acceptance. The scheduler's read-only production list was empty;
no production test schedules or requests were created.

Skillpack `f26b25a` was pushed and installed. Its selected Cairn event reference
matched across both Codex homes, both Claude homes, OpenCode, Gemini/Agy and
Hermes. The validator reported zero failures and two pre-existing warnings.
Full coordination v1 acceptance remains open.
