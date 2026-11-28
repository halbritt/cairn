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
| `--tailnet-socket PATH` | Unix socket of the [tailnet address](#tailnet-address). Optional; needs the three flags below. |
| `--tailnet-origin URL` | The page's `https` address on the tailnet, with no path. |
| `--tailnet-login LOGIN` | The one tailnet login that may open the page at that address. |
| `--tailnet-clients PATH` | File that lists the tailnet addresses of the devices that may open it. |

The server opens the store with the local operator channel, as other operator
commands do. It needs schema version 96 or later.

## Access

- The listener is loopback only. A request whose `Host` is not
  `127.0.0.1:PORT` or `localhost:PORT` is refused, and so is an `Origin` other
  than the page's own. The optional [tailnet address](#tailnet-address) is a
  second door with its own rules.
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

### Tailnet address

Exposure beyond loopback is an owner decision. One form is provided: an
address on the owner's tailnet behind Tailscale Serve, by the owner's decision
of 2026-11-28. The server itself still listens on no network address. It
opens a second door, a Unix socket, and Tailscale Serve proxies to it.

```sh
cairn swarm serve --collection "$HOME/git/cairn" \
  --tailnet-socket "$XDG_RUNTIME_DIR/cairn-swarm/tailnet.sock" \
  --tailnet-origin https://HOST.TAILNET.ts.net:8087 \
  --tailnet-login OWNER_LOGIN \
  --tailnet-clients "$HOME/.config/cairn/swarm-tailnet-clients"
sudo tailscale serve --bg --https=8087 "unix:$XDG_RUNTIME_DIR/cairn-swarm/tailnet.sock"
```

The four flags come together. The page is then at the `--tailnet-origin`
address and needs no token. It opens for the named login from the listed
devices only. The loopback door and its token do not change.

- The socket has mode 0600. Its directory must exist, belong to this user and
  be closed to everyone else (mode 0700), or the server does not start,
  loopback door included. In a user unit, `RuntimeDirectory=` with
  `RuntimeDirectoryMode=0700` makes it. Only this user and root can connect.
  Tailscale Serve, which runs as root, is the intended client. A lock file
  beside the socket allows one server for each socket.
- tailscaled states the tailnet login of the caller in `Tailscale-User-Login`
  on each request that it proxies. The door admits a request only when that
  header is there exactly once and equals `--tailnet-login`. A request with no
  such header or with another login is refused. On Tailscale 1.104.7,
  tailscaled replaced a value that the client had sent in that header.
- The login is not enough. tailscaled states the same login for every device
  of that user. That includes the serving host itself: any local user or
  container that calls the tailnet address gets the login of the host's
  owner. So the door also needs the device. tailscaled states the tailnet
  address of the caller's device in `X-Forwarded-For` and replaces a value
  that the client sent (measured on Tailscale 1.104.7, IPv4). The door admits
  a request only when that header is there exactly once, holds one address and
  no list, and the address is in the `--tailnet-clients` file. The server does
  not start with a list that names an address of the serving host. Do not
  list other servers either.
- The clients file has one tailnet address per line; `#` starts a comment. A
  device has an IPv4 and an IPv6 address: list both. The file must belong to
  this user and be closed to writes by anyone else, and it must name at least
  one address. It is read at start.
- These headers are trusted at this door only. The loopback listener ignores
  them, because any local process can write them there. This door trusts more
  than the token file does: this user's processes and root on this host,
  tailscaled and the control plane of the tailnet, and every process on a
  listed device.
- The request must have been addressed to the tailnet address. For a socket
  target tailscaled sends `Host: localhost` and states the address that the
  browser asked for in `X-Forwarded-Host`; the door reads that header, which
  must be there exactly once. `Origin`, when present, must be that page; a
  request that can change state must state it. The `Sec-Fetch-Site` check and
  the CSRF token apply as on loopback.
- The page's first question (`GET /api/auth/session`) signs it in, when the
  browser marks the request as the page's own (`Sec-Fetch-Site: same-origin`).
  Another client on a listed device gets no session that way; it can sign in
  with the token. Signing out ends the session; the next visit starts a new
  one.
- A session is good only at the door that issued it, and each door has its
  own limit of 64 sessions, so sign-ins at one door never end a session of
  the other. The tailnet session cookie is `__Host-cairn_swarm_PORT` (`Secure`, `HttpOnly`,
  `SameSite=Strict`). A browser sends it to every `https` service under the
  same host name, whatever the port. Such a service cannot use it at the
  loopback listener, and cannot reach the socket unless it runs as this user.
- The server sends no `Strict-Transport-Security`, because services on other
  ports share the host name.

To turn the tailnet address off, run `sudo tailscale serve --https=8087 off`
and remove the four flags.

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
- it is a topic event that was published to a campaign principal (it has a
  delivery to one, or a queued next-turn notice for one).

The landing page lists the newest messages that no active campaign claims.
It uses the same rule, negated over every active campaign, so a message is on
a campaign page or on the landing page, never on both and never on neither.

The log carries envelopes: sender, recipient, kind and the one-line summary.
A ledger item's summary can repeat a short message text, so do not treat the
page as free of message content.

Campaign principals are the members, active or not, every coordinator in the
handoff lineage, and the campaign's role addresses. Each fact is fixed when
the event commits. The journal link from an event to a ledger item is written
in a later transaction, so it is never a filter; it appears in the snapshot as
`ledger[].event_id`. A pool request gets its delivery at assignment, so a pool
delivery is not used either.

One of these facts can change after the commit. A coordinator handoff moves
the old coordinator's pending deliveries and queued notices to its successor,
so a topic event can enter or leave the log of any campaign that either of
them is a principal of.

The server therefore keeps a membership epoch for each campaign: the set of
its principals, and every handoff in the collection that moves work from or to
one of them. When the epoch changes, events behind the cursor may have entered
or left the campaign. The server then sends a `resync` event and a new log
window, and the page replaces its log. "Exactly once" holds within one window
and its continuation; a new window repeats events on purpose. Making a member
inactive or changing its role does not change the epoch.

### Stream events

| Event | Stream ID | Data |
| --- | --- | --- |
| `hello` | | `schema`, `collection`, `campaign`, `operator_principal`, `tick_ms`, `log_window`, `plane_url`, `server_time`. |
| `resync` | | `reason` (`new_window` or `membership_changed`), `membership_epoch`. The page clears its log; the `log` events that follow are the new window, oldest first. |
| `log` | cursor | One event envelope (`SwarmEvent`). No record body. |
| `snapshot` | cursor | The whole snapshot (`cairn.swarm-snapshot/1`). |
| `herdr` | | The pane observation: `observed_at`, `status` (`ok`, `unavailable`, `pending`), `error`, `stale`, `sessions`, `panes` by agent UUID. Sent when it changes. |
| `window` | | The context-window observation read from the harness transcripts of this host: `observed_at`, `status`, `error`, `stale`, `sessions`, `near_limit_rule`, `windows` by agent UUID (`used_tokens`, `window_tokens`, `model`, `measured_at`, `compactions`, `last_compaction_at`, `source`, `near_limit`) and `unknown` by agent UUID with the reason. Sent when it changes. |
| `tick` | cursor | `db_observed_at`, and `panes` and `window` (`observed_at`, `status`, `error`, `stale` each). Sent after every read. |
| `fault` | | `code`, `error`, `at`. The store could not be read; the server keeps trying. |
| `reset` | | `code`, `error`. The stream cannot continue and the server closes it. |

A stream request from a signed-in session is always answered as an event
stream, also when the first read is refused or fails. A browser's
`EventSource` gives up for good on any other answer and cannot read its status,
so an error status would leave the page stuck until a manual reload. What the
page does on `reset` depends on the code:

| `reset` code | Meaning | The page |
| --- | --- | --- |
| `STALE_CURSOR`, `INVALID_REQUEST` | The cursor cannot continue. | Starts a new stream without a cursor. |
| `NOT_FOUND` | No such campaign. | Says so and offers the landing page. |
| `UNAUTHENTICATED` | The session ended. | Asks the owner to sign in again. |
| any other | The store refuses the read. | Shows the store's reason. |

A stream lives no longer than the session that opened it. It ends at logout,
at the session's lifetime and when the session limit evicts the session, and
the store is not read for it afterwards.

A cursor is bound to the collection, the campaign, the restore generation, the
identity of the restore fence and the membership epoch. A cursor for another
collection or campaign is refused with `INVALID_REQUEST`. A cursor from before
a restore, or from another restore of the same backup, is refused with
`STALE_CURSOR`. The fence identity matters because the generation is a counter
stored in the database: two restores of one backup reach the same number.

The page keeps the newest 300 log events. The snapshot holds every open
delivery of the campaign and every delivery of the newest 300 campaign events.

### Each observation has its own age

The database snapshot, the Herdr read, the transcript read and the session
counters are separate observations. `tick.db_observed_at`,
`herdr.observed_at`, `window.observed_at` and
`snapshot.context_room.observed_at` carry their times. The page shows the age
of each and marks one stale without hiding the others. The Herdr read and the
transcript read each run on their own loop, so a slow or failed one leaves
delivery data current.

## Herdr pane state

The server reads Herdr about every 5 seconds while a browser is connected.
It finds the Herdr socket from the environment of a live session process and
matches panes with the wake path's own resolver, so Codex and Agy panes match.
The page shows the pane's status, ID and terminal title, with a copy control
for `herdr agent focus PANE`. A session with no match shows "no pane on this
host". Nothing read from Herdr is written to the store.

## Message flow

The campaign log shows requests, responses and notices newest first, with the
sender, recipient, summary and delivery state. Selecting an agent filters the
log. The snapshot includes deliveries for the newest 300 campaign events and
open deliveries from older campaign events or to campaign members. It keeps up
to 2,000 deliveries, giving priority to each member's oldest eligible delivery
and other open deliveries when the list is truncated. Agent details show open
deliveries in and out of that inbox.

Event and delivery envelopes identify how Cairn published a message with the
same optional `system` field. A delivery carries the field even after its
event leaves the log window, including when a member's oldest pending item
points to that delivery. `wait_notice` marks a registered wait notification,
`stopped_delivery_notice` a notice to the original publisher about work in a
stopped session's inbox, `overdue_checkpoint` an overdue deadline or checkpoint
notice, and `scheduled_event` a message the scheduler sent from an operator's
one-shot schedule. Direct sends by the operator or an agent omit this field.

A delivery can be pending, leased, held, settled, superseded, ignored, failed,
cancelled or expired. Held means an unfinished wake or native attempt owns the
delivery. Settled means the recipient handled it. An ignored delivery with code
`superseded` appears as superseded. Failed deliveries with operator cancellation,
release or consumer retirement codes appear as cancelled. Admission expiry and
task deadlines appear as expired. Seen means the delivery has been claimed at
least once. A request shows replied when a response from that recipient names
the request as its cause.

Each agent row shows its oldest eligible inbox item and the registered waits
that depend on its reply. An old notice can be first in the inbox while a peer
waits on a newer request. The view shows the waits each agent owns and lists
the recipients still awaited. It joins each wait to its exact dependency event
and labels direct replies and response-group closure separately. The snapshot
includes all scoped waiting waits and the 300 most recently resolved ones. A
wait belongs to the campaign through its owner or its dependency event. A peer
outside the campaign can also appear while its wait is open and an active
member has a pending, leased or held delivery of the dependency. After the
wait resolves, its owner or dependency event must belong to the campaign for
the wait to appear in history.
The waits feature status appears even when registration is disabled, and
existing waits remain visible.

## What needs the owner

The list at the top of the page shows work the owner can advance. Campaign
pages use the event log's campaign principal set, which includes former
members. The landing page shows items whose subjects belong to no active
campaign. Rows with more distinct sessions waiting directly on their events
come first, then older rows. If a source read fails, the page shows the reason
and keeps items from the sources it can read. Copy commands use the local
operator CLI.

When an action involves contacting someone, `refs.principal` identifies
the recipient. The page checks current campaign membership before offering to
send. A handoff row includes `since`, the first moment the handoff was due
under the campaign's current limits, only when Cairn can date every limit that
is reached: the age limit from the session start, and a compaction limit of
one from the recorded first compaction. It is absent when a later compaction
counts toward the limit or when that timestamp is unavailable, because the
undated crossing may have come first. The view derives `since` on each read
and stores nothing: while the limits stay as they are it does not move, and
reaching a second limit does not move it, but changing a limit dates the
handoff again from the new limit. The snapshot's `handoff.age_reached_at`
gives the time the current age limit was reached on its own.

Board questions follow the publisher's campaign affiliation regardless of
when the campaign was created. Both pages read the last 72 hours of requests.
Cairn reads the first fenced `board-card` block and keeps a question at least
as long as the board could keep it within the read horizon. Its three outcomes
are based on Fernside's parser at agent-board commit
`5ccacf9768ff872ad7f6c822d96967b09b153fb7`.

- A certainly valid card uses its absolute expiry, its TTL from publication,
  or 24 hours from publication. A default is declared when the card names a
  verb without a snooze duration.
- A question with no card or a certainly invalid card has a 24-hour lifetime
  from publication.
- An uncertain card stays for at least 24 hours, or its longer readable
  lifetime. When its lifetime fields or JSON block cannot be read reliably,
  it stays for 72 hours. The row says “Expiry not known exactly” and carries
  neither a timestamp nor a default.

Numeric timestamps are certain for zero or plain epoch seconds from 1 up to
the start of 2100 with at most six decimal places. An expiry at epoch zero,
whether numeric or an ISO timestamp, falls back to the TTL.

If a source is withdrawn or unavailable, Cairn keeps an envelope-only row
with a source-unavailable reason until the question is answered or reaches
the 72-hour cap.

A later board event from the same publisher replaces an earlier question only
when both cards are certainly valid and match by normalized slot. For senders
with an unanswered request in the 72-hour horizon, Cairn reads the first
fenced block of later answered requests in that horizon in the same read-only
transaction. Those answered requests contribute only a slot to the comparison.
They supply no snapshot rows or metadata. No note or card text reaches the
snapshot. Scheduled board questions carry their event's system origin.

Cairn measures the 72-hour maximum from publication. The board measures its
cap from the time it reads the event, which the Cairn store does not record.

To regenerate the card conformance fixture, run
`python3 -B scripts/generate_swarm_board_corpus.py ~/git/agent-board`.

Every pending or leased operator delivery has one owner page. If the event's
routing names an active campaign, that campaign owns the delivery. Otherwise
ownership follows the active campaign where the publisher is currently the
root or an active member. Landing owns the delivery when neither rule assigns
a campaign. A closed campaign can retain the event in its historical log. The
delivery's Needs You row appears only on its current owner page.

An operator-delivery row carries the optional `system` value when its event
was published by Cairn. A missed-checkpoint row carries the source event's
`system` value when it shows the publisher of a request or notice. Workstream
checkpoints identify their workstream owner.

| Row | When it appears | Owner action |
| --- | --- | --- |
| Board question | An unanswered request on `agent-board` within its lifetime. The event summary supplies the title when present. The card supplies its lifetime and whether a default was declared. A response with the request as its cause, including the board's recorded default, clears the row. | Open the event for context and answer on the Fernside board. |
| Operator delivery | A pending or leased delivery addressed to the operator, shown on its owner page. Only the collection's oldest eligible delivery has `inbox_head: true`. | Open the event from Needs You to read the delivery. Acknowledge it when it reaches the head. |
| Decision | An open ledger decision owned by the coordinator or operator. A lead's decision appears on the lead's row. | Make the decision and record the outcome in the campaign ledger. |
| Escalation | A ledger blocker marked escalated. | Review the campaign ledger and resolve the escalation with its owner. |
| Workstream decision | The workstream has a nonempty `decision_needed` field. | Give the workstream owner a decision and clear the request. |
| Handoff due | The coordinator reached the campaign's age or compaction limit. | Review the ledger and arrange a handoff with the coordinator. |
| Owner turn | The session has eligible inbox work and the host reported `owner_turn_required` for its current execution within the existing 120-second freshness window. | Find the session in Herdr and take a turn. |
| Failed delivery | Unresolved failed deliveries, grouped by their recorded code. The title names the oldest failed event and its recipient. Operator cancellations, releases, and consumer retirements are excluded. A reissue clears its original failure from the list. | Inspect the individual deliveries and review prior effects before choosing recovery. |
| Stalled agent | A current session meets `pending_starved` or `hold_stalled`. | Inspect the session and its oldest affected delivery before intervening. |
| Stopped session backlog | A stopped session still has eligible pending or leased work. | Review the stranded deliveries before deciding whether to notify the publishers or recover the work. |
| Timed-out wait | A registered wait timed out within the last 24 hours in the current restore generation. | Inspect the wait and follow up with its owner. |
| Missed checkpoint | Cairn emitted an overdue notice and the source still meets the existing unsatisfied checkpoint check. | Open the overdue notice and follow up with the checkpoint sender. |

“Stopped making progress” uses Cairn's existing starvation checks. `pending_starved`
uses 1,800 seconds for a busy session and 600 seconds for an idle session, with
the existing checks for idle host conditions and recent inbox handling.
`hold_stalled` uses 3,600 seconds on a hold with no open tool call recorded.

## Context room

Live observation rows identify the collection, harness and native session ID.
They carry no binding, agent or execution ID. When two registrations with the
same identity have overlapping lifetimes, both sessions show unknown. An
explicit stop closes a registration's interval. Losing the presence heartbeat
leaves the interval open because the registration can resume.

The session start time gives its age. The last observed compaction time
comes from the newest `PreCompact` capture row, or the session's first
compaction timestamp when its counter records exactly one compaction and
there is no `PreCompact` row. The compaction count is the larger of the
number of `PreCompact` reports and the session counter, since both can
record the same compaction. An unreported count or time is unknown.
`PreCompact` is best-effort telemetry. The native post-compaction hook
updates the session counter for active campaign coordinators on bindings
that report a compacted session start.

**Cairn-injected since compaction** is a proxy for context growth. It adds
measured UTF-8 bytes from successful startup and per-prompt recall, rendered
inbox-recall output (including any control text it carried), and measured
delivery rows. When both sources measure a matching delivery ID, the
delivery measurement takes precedence. The delivery count includes reported
full, compact and digest deliveries, or linked inbox-recall output that
carried content. A digest claim alone, an auto-settlement and a superseded
delivery do not establish injection. The recall count includes ordinary
recall invocations that emitted bytes. Unmetered inbox output, standalone
coordination cues and native memory reads are outside the proxy.

With no recorded compaction, the proxy starts at session registration.
With a recorded compaction, it counts rows after the last observed
compaction. If the store records several compactions but has no timestamp
after the first, the current interval is unknown. A value without a
measurement in the interval is unknown. The source timestamp is the newest
counter observation among the campaign's sessions. The proxy flags a session
at 64 KiB of measured Cairn output in the current interval, a fixed
threshold for accumulated output across multiple hooks. Harness-reported
window fill is read independently.

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
| `GET /api/swarm/landing` | Active campaigns, online sessions in no campaign, the newest messages that no active campaign claims, and the owner attention rows that belong to no campaign (`cairn.swarm-landing/1`). |
| `GET /api/swarm/campaign?campaign=NAME` | One snapshot, without a stream. |
| `GET /api/swarm/panes` | One pane observation and one context-window observation (`{"herdr": ..., "window": ...}`), for a page with no stream open. |
| `GET /api/swarm/event?campaign=NAME&event_id=UUID` | One event with its deliveries and note body, read when the owner opens it. |

## Operator actions

The signed-in browser uses the listener's local operator channel. The listener
sets the collection at startup, and its Store channel supplies the operator
principal. Every
`POST` carries the session's CSRF token and a JSON object with `campaign` and
a canonical `request_id` UUID. A retry of a committed request uses the same
UUID and the same fields. Reusing that UUID with a different intent returns
`IDEMPOTENCY_CONFLICT`. The server rejects unknown fields.
Acknowledge and Event detail accept an empty `campaign`, which selects the
landing page. Send, Cancel delivery, Cancel wait and Cancel preview require a
campaign name.
An unknown campaign returns HTTP 404 with `NOT_FOUND` and the message
“team campaign not found” on every route. The server resolves the campaign
before looking up the requested target.

| Route | Other request fields | Successful reply |
| --- | --- | --- |
| `POST /api/swarm/action/send` | `to` (current campaign principal), `kind` (`request` or `notice`), `summary`, `body`. Notices require `action` (`none`, `review` or `decide`) and may carry a `supersedes` key. | `{event, deliveries}`. The store commits one shareable source with the event. |
| `POST /api/swarm/action/acknowledge` | `delivery_id` (the operator's expected delivery). | `{delivery}`, with `delivery: null` when the inbox is empty. The server claims and completes the oldest eligible delivery in one transaction. |
| `GET /api/swarm/action/cancel-preview` | Query `campaign`, `delivery_id`. | `SwarmCancelPreview` (`cairn.swarm-cancel-preview/1`), with an opaque `preview_token` for confirmation. It gives the recipient, sibling deliveries, refusal if any, direct-reply waits and, for a grouped request, the group state, reply count, whether this delivery's reply counts and closure waits. Waits appear while their state is `waiting`. Each lookup reports `found`, `empty` or `unavailable`. For an unavailable lookup the browser receives a fixed message and the server log records the diagnostic. |
| `POST /api/swarm/action/cancel-delivery` | `delivery_id`, `reason` (8–4000 characters), `preview_token` from the preview. | `{outcome, delivery, detail?}`. The outcome is `cancelled`, `cancel_pending` while a supported native turn still has an open delivery, or `already_terminal` when the delivery reached another terminal outcome first. The reply includes the current delivery. |
| `POST /api/swarm/action/cancel-wait` | `wait_id`, `expected_owner` (the wait owner shown in the view). | `{outcome: "cancelled", wait}` with the resolved wait. |
| `GET /api/swarm/event` | Query `campaign`, `event_id`. | `{event, deliveries, body?, body_truncated?, body_unavailable?}`. Returns the exact referenced version up to 16 KiB of UTF-8, with `body_truncated: true` when cut. Every reply has either `body` or `body_unavailable`. |

Event detail uses the same read rule on campaign pages and landing. The event
must be in the page's log or have a pending or leased operator delivery owned
by that page. Log attribution can make an event readable on a second page.
An event outside both parts of the page's scope returns the same `NOT_FOUND`
as an unknown event ID.

Sending requires an active campaign. Both the chosen address and the
resolved destination must be its current root, an active member, or a role
address of that campaign. Historic principals remain in the log. A
supersession replaces eligible notices from the same publisher and recipient
only when every affected notice belongs to a campaign event. Otherwise the
whole send is refused with `AUTHORITY_DENIED` and the message “this notice
cannot replace an earlier one from this page”.

Delivery cancellation and preview require a campaign consumer. The delivery
must belong to a campaign event or be live for an active member. Siblings in
a preview give context about a shared event. Wait cancellation requires a
waiting state and a campaign owner. A wait can be visible through its
dependency even when that owner is outside the campaign. An out-of-scope
target and an unknown ID both return `NOT_FOUND` before confirmation fields
are checked. A matching committed cancellation receipt can read back its
terminal result if the recipient or wait owner still passes campaign scope.

Acknowledge uses collection-wide claim order and the same owner-page rule as
`inbox_head` in Needs You. An active campaign named by
routing takes priority over the publisher's current active campaign. With
neither, the owner is landing (`campaign: ""`). Closed campaigns cannot
acknowledge operator deliveries. A wrong inbox head or a request from the
wrong page returns HTTP 409 with
`{error, code: "VERSION_CONFLICT", expected, oldest}`. The oldest delivery
is unchanged. `oldest` is null on landing or when the expected delivery or
inbox head belongs to another page. A live manual lease is refused
with `UNSUPPORTED_CONTROL`. The preview token binds
the recipient, siblings, group reply counts, wait lookup statuses, and the
ID, owner, predicate and dependency of each listed wait. When those facts
change, HTTP 409 returns
`{error, code: "VERSION_CONFLICT", preview}` with a fresh token. If the
eligible wait's owner changed, HTTP 409 returns
`{error, code: "VERSION_CONFLICT", wait}`. A conflict leaves the target
untouched, and the browser asks for confirmation again.

A delivery cancellation changes one delivery. Siblings and replies that
already count in a response group keep their stored state. A wait on the
request or group keeps its own deadline. The next live snapshot reflects the
action. A published delivery appears as sent and not yet seen until the
recipient claims it.

## Run it as a user unit

The view is meant to be up without a manual start. Install the unit from
`integrations/systemd/cairn-swarm.service`:

```sh
install -m 0644 integrations/systemd/cairn-swarm.service "$HOME/.config/systemd/user/cairn-swarm.service"
systemctl --user daemon-reload
systemctl --user enable --now cairn-swarm.service
systemctl --user status cairn-swarm.service
cairn swarm url
```

The unit serves the collection `%h/git/cairn` on port 8087. To change a flag,
for example to add `--plane-url`, put a drop-in beside it that clears and
replaces `ExecStart`:

```sh
mkdir -p "$HOME/.config/systemd/user/cairn-swarm.service.d"
printf '%s\n' '[Service]' 'ExecStart=' \
  "ExecStart=%h/.local/bin/cairn swarm serve --collection %h/git/cairn --plane-url https://plane.example/WORKSPACE/browse/{id}/" \
  > "$HOME/.config/systemd/user/cairn-swarm.service.d/flags.conf"
systemctl --user daemon-reload
systemctl --user restart cairn-swarm.service
```

A unit that reads the store needs the same `CAIRN_DATABASE_URL` as the API
unit when the store is not at the default socket.

The unit starts after `cairn-store.service`, the managed local store. A host
whose store is a system PostgreSQL has no such unit, and systemd refuses to
start a unit that requires a missing one. A drop-in cannot remove a
dependency, so on such a host install the unit without those two lines and
give it the database in a drop-in:

```sh
sed -e '/^Requires=cairn-store\.service$/d' -e '/^After=cairn-store\.service$/d' \
  integrations/systemd/cairn-swarm.service > "$HOME/.config/systemd/user/cairn-swarm.service"
mkdir -p "$HOME/.config/systemd/user/cairn-swarm.service.d"
printf '%s\n' '[Service]' \
  'Environment="CAIRN_DATABASE_URL=host=/var/run/postgresql port=5432 dbname=cairn sslmode=disable"' \
  > "$HOME/.config/systemd/user/cairn-swarm.service.d/50-managed-database.conf"
systemctl --user daemon-reload
```

## Turn it off

```sh
systemctl --user disable --now cairn-swarm.service
```

Stopping the server changes nothing in the store and nothing for any agent.
Delete the token file to make every earlier login link useless; the next
start creates a new token:

```sh
rm "$HOME/.local/share/cairn/swarm-view.token"
```
