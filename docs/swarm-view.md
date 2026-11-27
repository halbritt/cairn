# Swarm view

The swarm view is one live page per campaign. It answers four questions: who
is working on what, who is talking to whom and what is pending, what the owner
can advance, and which agents are running low on context room. The
[design](plans/swarm-view-design.md) and
[decision 0022](decisions/0022-kernel-boundary-and-swarm-view.md) give the
reasons. This page describes the running surface.

Cairn stores and delivers; it does not act. The view reads state the store
already holds and the Herdr panes of this host. It does not start, stop or
focus a session, send keys to a pane, manage a worktree, trigger compaction or
choose a model. It sends an event to an agent only when the owner uses an
action on the page.

## Run it

```sh
cairn swarm serve --collection "$HOME/git/cairn"
cairn swarm url
```

`swarm serve` listens on `127.0.0.1` (default port 8087) until it is
interrupted. `swarm url` prints the page address with the login token in the
URL fragment. A fragment is not sent to the server; the page reads it, signs
in and removes it from the address bar.

| Flag | Meaning |
| --- | --- |
| `--collection REPO` | The collection to show. Required. |
| `--port PORT` | Loopback port. Default 8087; `0` picks a free port. |
| `--token-file PATH` | Login token file. Default `swarm-view.token` in the Cairn state directory. |
| `--bindings DIR` | Coordination bindings directory for the Herdr read. Default `coordination/bindings` in the Cairn state directory. |
| `--plane-url TEMPLATE` | Address of a tracker item, with `{id}` for the identifier. Optional. |

The server opens the store with the local operator channel, as other operator
commands do. It needs schema version 96 or later.

## Access

- The listener is loopback only. A request whose `Host` is not
  `127.0.0.1:PORT` or `localhost:PORT` is refused, and so is an `Origin` other
  than the page's own.
- A browser signs in by presenting the token from the token file. The server
  creates a missing token file with mode 0600 and refuses a file that another
  user can read. The token is compared in constant time.
- A signed-in browser holds a session cookie named `cairn_swarm_PORT`
  (`HttpOnly`, `SameSite=Strict`). Loopback cookies are shared across ports,
  so the name carries the port.
- Every request that changes state carries the session's CSRF token in
  `X-CSRF-Token`.
- Every API route refuses a request whose `Sec-Fetch-Site` is present and is
  not `same-origin`. The stream is a `GET` with no `Origin` header, so this is
  the check that keeps another local page from opening it.

Exposure beyond loopback is an owner decision and is not provided.

## Live updates

The page holds one server-sent event stream:
`GET /api/swarm/stream?campaign=NAME`. The stream carries two things with
different rules.

**Events are a log.** Each `agent_event` row of the campaign is sent once, in
commit order, as a `log` event whose stream ID is a cursor. A client that
reconnects sends the last ID it received (`Last-Event-ID`, or `?cursor=`) and
continues after it. Every insert into `agent_event` takes the collection's
advisory lock and holds it to commit, so positions become visible in order
and "position above the cursor" skips nothing.

**State is a level.** Roster, deliveries, waits, ledger items, owner attention
and context room are sent whole as a `snapshot` event when they differ from
the last snapshot sent on the connection. A client that reconnects gets the
current snapshot. No transition is replayed.

The server reads the store every 2 seconds in one read-only `REPEATABLE READ`
transaction, and only while a browser is connected. It takes no lock.

### Which events belong to a campaign

An event is in the campaign's log when it was created after the campaign and
one of these holds:

- its publisher is a campaign principal;
- it is addressed to a campaign principal;
- its routing information names the campaign;
- it is a topic event with a delivery to a campaign principal.

Campaign principals are the members, active or not, every coordinator in the
handoff lineage, and the campaign's role addresses. Each fact is fixed when
the event commits. The journal link from an event to a ledger item is written
in a later transaction, so it is never a filter; it appears in the snapshot as
`ledger[].event_id`. A pool request gets its delivery at assignment, so a pool
delivery is not used either.

When the set of campaign principals changes, events behind the cursor may now
belong to the campaign. The server then sends a `resync` event and a new log
window, and the page replaces its log.

### Stream events

| Event | Stream ID | Data |
| --- | --- | --- |
| `hello` | | `schema`, `collection`, `campaign`, `operator_principal`, `tick_ms`, `log_window`, `plane_url`, `server_time`. |
| `resync` | | `reason` (`new_window` or `membership_changed`), `membership_epoch`. The page clears its log; the `log` events that follow are the new window, oldest first. |
| `log` | cursor | One event envelope (`SwarmEvent`). No record body. |
| `snapshot` | cursor | The whole snapshot (`cairn.swarm-snapshot/1`). |
| `herdr` | | The pane observation: `observed_at`, `status` (`ok`, `unavailable`, `pending`), `error`, `stale`, `sessions`, `panes` by agent UUID. Sent when it changes. |
| `tick` | cursor | `db_observed_at` and `panes` (`observed_at`, `status`, `error`, `stale`). Sent after every read. |
| `fault` | | `code`, `error`, `at`. The store could not be read; the server keeps trying. |
| `reset` | | `code`, `error`. The stream cannot continue; the page starts a new stream without a cursor. |

A cursor is bound to the collection, the campaign, the restore generation and
the membership epoch. A cursor for another collection or campaign is refused
with `INVALID_REQUEST`. A cursor from before a restore is refused with
`STALE_CURSOR` and the page reloads.

The page keeps the newest 300 log events. The snapshot holds every open
delivery of the campaign and every delivery of the newest 300 campaign events.

### Each observation has its own age

The database snapshot, the Herdr read and the session counters are separate
observations. `tick.db_observed_at`, `herdr.observed_at` and
`snapshot.context_room.observed_at` carry their times. The page shows the age
of each and marks one stale without hiding the others. The Herdr read runs on
its own loop, so a slow or failed Herdr leaves delivery data current.

## Herdr pane state

The server reads Herdr about every 5 seconds while a browser is connected.
It finds the Herdr socket from the environment of a live session process and
matches panes with the wake path's own resolver, so Codex and Agy panes match.
The page shows the pane's status, ID and terminal title, with a copy control
for `herdr agent focus PANE`. A session with no match shows "no pane on this
host". Nothing read from Herdr is written to the store.

## Message flow

The flow lists the campaign's requests, responses and notices, newest first,
each with the delivery state of every recipient. Selecting an agent filters
the flow to that agent. (CAIRN-830 completes this section.)

## What needs the owner

The list at the top of the page holds only work that the owner can advance,
and each row names the action. (CAIRN-831 completes this section.)

## Context room

The true fill of an agent's window is known only to its harness. The page
shows what Cairn can state truthfully, labelled as a proxy. (CAIRN-832
completes this section.)

## Plane

Plane stays the tracker of record. The page shows no Plane status. An
identifier such as `CAIRN-829` in a ledger item or a task summary becomes a
link when the server is started with `--plane-url`, for example
`--plane-url 'https://plane.example/WORKSPACE/browse/{id}/'`. Without the
flag the identifier is plain text.

## Other routes

All routes answer JSON. An error is `{"error": "...", "code": "..."}` with the
store's refusal code.

| Route | Purpose |
| --- | --- |
| `GET /api/auth/session` | `{"authenticated": bool, "csrf_token": "...", "collection": "...", "plane_url": "..."}`. The last three only when signed in. |
| `POST /api/auth/login` | Body `{"token": "..."}`. Sets the session cookie. |
| `POST /api/auth/logout` | Ends the session. |
| `GET /api/swarm/landing` | Active campaigns, online sessions in no campaign, and the owner attention rows that belong to no campaign (`cairn.swarm-landing/1`). |
| `GET /api/swarm/campaign?campaign=NAME` | One snapshot, without a stream. |
| `GET /api/swarm/panes` | One pane observation, for a page with no stream open. |
| `GET /api/swarm/event?campaign=NAME&event_id=UUID` | One event with its deliveries and note body, read when the owner opens it. |

## Operator actions

The page performs only operations the store already has, as the local
operator principal. Each `POST` body carries a `request_id` UUID that the page
keeps for a retry.

| Action | Route | Store operation |
| --- | --- | --- |
| Send a request or notice to an agent; supersede the owner's earlier notice | `POST /api/swarm/action/send` | `event-publish` |
| Acknowledge my delivery | `POST /api/swarm/action/acknowledge` | `event-next`, then `event-complete` |
| Preview a cancel | `GET /api/swarm/action/cancel-preview?campaign=NAME&delivery_id=UUID` | read only (`cairn.swarm-cancel-preview/1`) |
| Cancel this recipient's delivery | `POST /api/swarm/action/cancel-delivery` | `work-cancel` |
| Cancel a wait | `POST /api/swarm/action/cancel-wait` | `wait-cancel` |

An action takes effect in the store at once, but an agent sees it only at its
next turn boundary. Until the recipient claims the delivery, the page shows the
action as sent and not yet seen.

## Turn it off

```sh
systemctl --user disable --now cairn-swarm.service
```

Stopping the server changes nothing in the store. Delete the token file to
make every earlier login link useless; the next start creates a new token.
