# Native wake claim binding — September 16

Migration 048 adds optional paired `delivery_id` and `native_turn_id` fields to
native inbox claims. The explicit Codex Unix-listener adapter supplies them only
when its retained wake marker and complete native prompt match. Session UUIDs
remain identifiers under the existing trusted-host profile.

## Verified contracts

- A queued wake whose delivery is no longer eligible returns an empty poll,
  rather than acquiring another pending delivery.
- Poll retries preserve the original empty or acquired result. Changing the
  selected delivery or turn returns `IDEMPOTENCY_CONFLICT`.
- The native turn survives an API restart and appears in the inbox context and
  operator review.
- On the explicit Codex queue route, ordinary owner prompts do not claim inbox
  work. Another turn's Stop cannot end the retained request context.
- Other harnesses retain their existing native boundary behavior.

The store tests use disposable PostgreSQL. The CLI/API fixture runs the actual
Python hook with a simulated native queue process and hook events. It checks
that the owner prompt leaves delivery pending, the matching wake acquires it,
the wrong Stop leaves it leased, and completion followed by the matching Stop
releases the hold. This fixture does not itself establish native Codex RPC use.

## Deadline cleanup defect found during verification

An integration run and a focused reproduction stopped a deadline worker but
left its hold retained after the supervisor exited. The original command error
was hidden by the CLI's generic error envelope. Diagnostic reruns passed; that
alone did not explain the failure.

A separate real systemd probe demonstrated a concrete race in the cleanup path:
an owned transient unit was active at the first check, reached `RuntimeMaxSec`,
and was collected before `systemctl stop`. Stop returned exit 5 for the unloaded
unit; a subsequent check showed `not-found` and `inactive`. Previously Cairn
returned immediately on that stop error, without making the final observation.
This is a reproduced defect consistent with the original failure, not proof of
the hidden original error.

The supervisor now checks final unit/cgroup state even after a failed stop.
Confirmed cleanup can finish the hold; a running unit or failed inspection
retains it. A command-boundary regression failed before the fix and passes
afterward for collected, running and unreadable outcomes. Existing real cgroup
tests cover detached-child cleanup, cancellation fences and supervisor death.
The integration fixture now reports supervisor exit immediately and retains its
structured result instead of timing out with the useful error discarded.

The final source passed `make test-integration`, including the real cgroup
control suite, `make check`, all 136 Python tests, and the wakeup package's race
tests. Test databases were disposable; production was not used for testing.

## Deployment and native exchange

Clean build `6fdffe0` and migration 048 were deployed after a database backup.
The API, presence watcher, scheduler and seven idle worker services restarted
successfully. CLI and API reported the same clean revision. Hermes' gateway
remained active; its adapter invokes the installed coordination script for each
hook. No busy Rhumb conversation was restarted.

At 11:27:45 PDT, request `b817f63d-0b93-4cc8-a34f-647a64c95d63` was published to
the owned native Codex conversation. The parent sent no prompt after publication.
The presence watcher queued the wake, and its hook claimed delivery
`57db7709-31f0-417b-9143-f818bb74cd75` once. Result
`0b778f35-d16b-459b-8aea-f27da088aab3/1` reported matching identity, cwd, delivery
and native turn `01a0ab79-9d02-75d3-84f1-2fe9c58db2b1`.

The parent read the result and independently matched that turn against both
operator review and native `thread/read`, which reported it completed. The hold
finished at 11:28:19, the reply collected at 11:28:26, and the parent acknowledged
it at 11:29:26. The original process/start/boot and execution remained live and
idle, and the unsubmitted composer draft remained unchanged. Selected local
evidence is under `/tmp/cairn-native-turn-live-acceptance`.

The offered OpenCode agent's separate queue review identified a liveness gap:
a queue start refused while busy can leave an item paused after interruption,
while the retained wake marker suppresses a new submission. The repair is
delegated; this deployment does not claim that gap resolved.

## Limits

Native turn association is a prerequisite for interactive request control.
It neither stops native tools nor enables cancellation: interactive cancellation
still returns `UNSUPPORTED_CONTROL`. The installed Codex interrupt operation
left a background command running in an owned probe, and historical item listing
did not expose that active command immediately after interruption. Durable tool
ownership, stop confirmation and recovery remain required work. The overall
coordination v1 goal remains incomplete.
