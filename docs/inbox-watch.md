# Watching inbox arrivals

`cairn watch` reads an inbox without claiming work. It emits one JSON page per
line, containing delivery IDs, current state, event metadata, an opaque cursor
and `more`. Source bodies remain behind the existing exact-version read path.

```sh
cairn watch --profile worker-01 --cursor-file /absolute/path/worker-01.cursor
```

Use your configured profile. For a registered conversation, add its current
`--agent-id UUID --execution-id UUID`; these select the session using its existing
profile. UUIDs are session identifiers, not additional credentials.

`--once` emits one page, including an empty page, then exits. `--limit` sets a
page size from 1 to 100 (default 50). `--topic NAME` restricts observations to
that topic's deliveries. `--cursor VALUE` resumes directly without a cursor
file. The raw `cairn agent event-watch` operation accepts the equivalent JSON
request and returns one page in the normal response envelope.

## Progress and recovery

The cursor records **delivery arrival order**. Pool requests receive a delivery
position when assigned, even when their publication predates other inbox work.
Publication history from `cairn events --after` uses a different sequence and
must not be used as an inbox watch cursor.
Migration 043 assigned positions to deliveries that already existed when watch
was introduced. Their first scan is retained inventory; those backfilled
positions do not reconstruct historical arrival order. New deliveries use their
assignment order.

The CLI reads one page at a time. It polls every second when caught up, reads
additional pages immediately while `more` is true, and retries transport failures
with the same cursor. A slow stdout consumer prevents the next fetch. SIGINT or
SIGTERM interrupts a blocked output pipe. API restart preserves cursor progress.

A cursor file is replaced atomically with owner-only permissions and synced only
after successful page output. One process holds its companion `.lock` file while
watching. The directory must already exist. Do not remove the lock file while a
watch is running. A crash after output but before checkpoint can repeat delivery
IDs; consumers must tolerate duplicates. Successful output does not establish
that a downstream program processed it. Watching does not acknowledge, renew,
retry or otherwise change a delivery.

Each cursor is bound to its collection, inbox, topic filter, read destination and
database generation. Changing those fields, or restoring and fencing the database,
returns `STALE_CURSOR`. Stop and reconcile; explicitly remove or replace the
cursor file to rescan retained deliveries. The CLI never silently resets it.
A resumed native conversation can reuse its cursor with its new execution UUID;
the old execution is refused by the existing session checks.

This is an arrival feed, not a stream of every state transition. A delivery's
state is its state at page-read time. Use `event-status` for later handling
results. Neither a watch observation nor a handled delivery proves task acceptance.

## Store invariant

Migration 043 adds `agent_delivery.position`. Publication and pool assignment
hold the same per-collection transaction advisory lock through commit, so an
inbox cursor cannot advance past an older uncommitted insertion. Every future
delivery creator must preserve that ordering. Upgrade all API and worker writers
together; do not run old and new delivery-writing code concurrently during rollout.

No broker, notification queue, new credentials or automatic request replay is
introduced. Full-v1 operator recovery, scheduling and coordination work is tracked
in [the implementation plan](plans/agent-coordination-v1.md).
