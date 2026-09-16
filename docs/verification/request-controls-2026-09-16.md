# Request controls verification — 2026-09-16

Scope: migration 046 and the [request-control contract](../request-controls.md).
Admission expiry covers queued/manual/native admission; deadlines and running
cancellation require the managed fresh-worker path. Interactive hard stops,
response groups and full coordination v1 acceptance remain open.

## Checks

`make test-integration`, `make check`, `make test-lifecycle` and focused `-race`
tests passed on the final implementation. All database tests use disposable
clusters. Production is used only for authorized
backup, upgrade and installed-state inspection.

| Invariant | Evidence |
| --- | --- |
| Expired pending work cannot claim; a live manual lease may finish | `TestAdmissionExpiryPreventsClaimButDoesNotRevokeLiveLease` |
| Expired unassigned pool work closes without delivery; operator can page its retained audit | `TestRequestSweepExpiresUnassignedPoolWithoutConsumingCapacity` |
| Cancellation fences completion while retaining the wake hold | `TestCancelWorkFencesCompletionButRetainsWakeHold` |
| Deadline fences completion and retains the hold | `TestTaskDeadlineFencesCompletionAndRetainsHold` |
| Cancellation during start prevents enter | `TestCancelWhileWakeStartingPreventsEnter` |
| Admission expiry before enter blocks work; expiry after enter preserves running work | `TestAdmissionExpiryBetweenClaimAndEnterVsAfterEnter` |
| Native/manual active cancellation and unsupported deadline destinations are refused | `TestNativeAndManualCancellationRefusalAndDeadlineValidation` |
| Actual simultaneous completion/cancellation has one winner, with hold retained | `TestConcurrentCancelWorkVsCompleteEventRace` |
| Actual simultaneous pool assignment/cancellation creates at most one delivery and stable cancellation retry | `TestConcurrentCancelWorkVsClaimWakeRace` |
| CLI time validation and exact request wiring | `TestPublishTimingFlagHelpAndValidation`, `TestPublishTimingRequestWiring` |
| Running expiry survival, cancellation, detached child cleanup, deadline, supervisor death and recovery | `scripts/check_request_controls.py` |
| Populated event/delivery/pool control fields survive backup/restore | `scripts/test-local-lifecycle.sh` full-row comparison |

The concurrent tests release separate store calls through a shared barrier, with
eight distinct fixtures each. Sequential tests also exercise both known orderings;
the concurrent tests do not require the scheduler to choose both outcomes.

The process probe launches a disposable Python worker that creates a detached
child. It verifies the child's execution state and systemd control-group emptiness
after the hold finishes. It kills the supervisor during another deadline-bearing
task, observes systemd stop the child while the hold remains, and restarts the
supervisor to reconcile that hold without replay. This proves behavior of the
shared host path with a synthetic worker, not provider-specific task acceptance.

An initial probe completed its behavior assertions but failed cleanup when
`systemctl stop` targeted an already-collected transient unit. Cleanup now first
confirms unit/group emptiness and always closes its API in a `finally` block.
The complete corrected probe passed. The old API-outage test now describes the
control/renewal failure path; it does not attribute shutdown solely to renewal.

## Review and limits

The offered agent `d48af4ab-adc6-4d35-8dbd-8e11b72f1617` contributed CLI wiring,
store tests and read-only review. Its initial “race” report described sequential
orderings. The parent inspected that gap and requested actual concurrent tests,
then ran them with the race detector. A review report is fallible evidence;
passing checks do not establish task usefulness or full design acceptance.

Stop decisions commit separately from confirmed process termination. Delivery
failure therefore coexists with a hold until cleanup. Systemd stop grace and host
scheduling mean deadline shutdown is not an exact real-time guarantee. Native
interactive sessions and a shared Hermes gateway have no per-request cgroup;
their active cancellation is explicitly refused. The existing trusted-host model
and store-owned identity fields remain unchanged.

Local logs are `/tmp/cairn-controls-integration-final.log`,
`/tmp/cairn-controls-check-final.log`, `/tmp/cairn-controls-final-focused.log`,
`/tmp/cairn-controls-lifecycle.log` and `/tmp/cairn-controls-runtime-final.log`.
The [decision receipt](../plans/request-controls-review.json) records the bounded
atomicity, resource ownership and context review: packet
`pkt-e217731eb10aad03`, SHA-256
`e217731eb10aad032591d549a77e9baf5fc7709a3558b9fd4091fc017a912137`.
The selected concepts have no unmet material obligations. Other packet
obligations are classified in the receipt; they do not support broader claims.

## Deployment

Deployment verification will be recorded after the final checks and clean build.
