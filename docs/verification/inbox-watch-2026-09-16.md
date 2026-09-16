# Inbox watch verification — 2026-09-16

## Implemented slice

Migration 043 gives deliveries their own arrival sequence. `cairn watch` reads
bounded pages without claiming work and resumes using a cursor tied to the inbox,
filter, destination and database generation. The CLI checkpoints after output,
retries transport failures and stops on explicit store refusals.
See [the contract](../inbox-watch.md) and
[the decision receipt](../plans/inbox-watch-review.json).

## Checks

`make test-integration`, `make check`, and `make test-lifecycle` passed against
disposable PostgreSQL. The lifecycle snapshot includes complete delivery rows,
so its backup/restore comparison covers populated delivery positions.

Store tests cover a pool request assigned after a newer direct publication,
a forced overlap between an uncommitted pool assignment and direct publication,
page boundaries, hosted privacy, changed cursor filters, restore invalidation,
and resumed conversation identity with refusal of the old execution. Watching
leaves the first lease claim and attempt count unchanged.

CLI tests cover failed output without cursor advancement, owner-only atomic
checkpoint and resume, exclusion of a second cursor writer, bounded reads when
stdout stalls, cancellation of a full output pipe, and transport reconnect.
The final real CLI/API probe keeps a watch alive through API restart, publishes
one later arrival, observes it once during the probe, claims it independently,
and restarts the CLI with the same cursor. This does not establish exactly-once
downstream processing.

Local logs: `/tmp/cairn-watch-integration.log`, `/tmp/cairn-watch-check-final.log`,
`/tmp/cairn-watch-lifecycle.log`, `/tmp/cairn-watch-cli-final.log`, and
`/tmp/cairn-watch-runtime-final.log`. The final disposable API fixture is
`/tmp/cairn-watch-runtime-final`.

## Independent review and limits

The offered agent reviewed current delivery creators and lock order, cursor
scope and restore behavior. Its suggested regression cases are covered. Its
suggestion to reissue a failed delivery under the same event is not adopted:
the accepted plan calls for a new request with retained attribution and prior
history. Recovery/reissue remains a separate implementation step.

The arrival feed does not report later state transitions. Cursor checkpointing
can repeat delivery IDs after a crash. No throughput or usefulness measurement
is claimed. Operator recovery, scheduling, cancellation and response groups remain
open in the v1 plan.
