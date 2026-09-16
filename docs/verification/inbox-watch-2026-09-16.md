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

## Deployment

Clean release `12140feb06937d50c3c286a24e1887007d33d0bf` is pushed and deployed.
Production had no unfinished wake or native inbox attempts before the rollout.
Backup `cairn-20260916T073500-2730.dump` passed catalog, checksum and archive
checks; the earlier executable is retained as `cairn-before-inbox-watch`.

The API, presence process, seven worker services and Hermes gateway were stopped
before migration 043. Hermes exited with status 1 during shutdown and had no
remaining main process. After migration, the clean API version responded before
workers and Hermes were started. All ten services subsequently reported
active/running with zero restarts. All seven slots reported current presence
and available admission health; this does not measure provider capacity.

A read-only production watch paged through the hosted profile's retained inbox,
saved an owner-only cursor, and returned an empty page on resume. It neither
claimed nor changed those deliveries. Hermes uses the upgraded shared Cairn
binary; its native coordination plugin did not change in this slice.
