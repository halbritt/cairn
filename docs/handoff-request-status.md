# Saved handoffs and request status

A saved handoff is a versioned note. Reading that note does not grant access to
coordination events mentioned in its body. A UUID in prose is not a typed link
or a capability. Event status uses the caller's existing publisher/consumer,
collection and destination checks; recipients see only their own deliveries.

A request delivery marked `handled` does not establish task completion. It can
have no result, and a response group can remain open with task outcome unknown.
Cancellation, failure and collected replies likewise do not prove that the
handoff's work is finished. Check current task evidence before explicitly
revising a saved handoff. Existing lifecycle capture requires an explicit report
that all work in that handoff is complete.

Cairn does not currently infer handoff closure, change its ranking, or rewrite
its history when a referenced request reaches a terminal delivery state.
CAIRN-115's original automatic-closure proposal requires a corrected contract:
any future status annotation must preserve event visibility, distinguish request
handling from task outcome, and leave retained note bodies unchanged. The
caller-scoped inspection endpoint below uses existing event permissions; it
establishes no new status-sharing permission.

The regression `TestHandoffLinksDoNotConferEventAccessOrTaskClosure` exercises two
handled requests with no replies, a shareable handoff readable by a third
principal, denied event/group reads for that principal, and unchanged retained
note history. It characterizes existing behavior; it does not validate a future
link or annotation feature.

See [agent events](agent-event-fabric.md), [response groups](response-groups.md)
and [record history](record-history.md) for the implemented contracts.

## Inspect linked request status

`handoff-request-status` checks several handoff items in one read-only operation.
The caller supplies the handoff's exact current record and version and one
explicit `item_id`/`event_id` pair per item. It answers one narrow question:
what do my existing event permissions show about how each linked request was
handled? It never answers whether the work is done.

```sh
cairn agent --socket SOCKET --token-file FILE handoff-request-status <<'JSON'
{"handoff":{"record_id":"HANDOFF_UUID","version":1},
 "items":[{"item_id":"review","event_id":"REVIEW_REQUEST_EVENT_UUID"},
          {"item_id":"deliver","event_id":"DELIVERY_REQUEST_EVENT_UUID"}]}
JSON
```

The HTTP operation is `POST /v1/handoff-request-status` with the same JSON and the
usual `cairn.response/1` envelope. It uses the authenticated profile's principal,
collection and destination; no request field supplies identity, scope or a lease.
No request UUID is needed and a repeat is safe. `cairn agent handoff-request-status
--help` shows the example without credentials.

### Request

The request has exactly the fields shown; unknown fields refuse. `version` is a
positive integer. There are 1–16 items. An `item_id` is 1–64 ASCII letters, digits,
underscores or hyphens; an `event_id` is a nonzero canonical lowercase UUID. Item IDs
and event IDs must each be unique. Malformed, duplicate and over-limit input refuses
with `INVALID_REQUEST` before any database read.

The links are **caller assertions**. Cairn does not read UUIDs from the handoff
body, check that an item mentions its event, store the links, or require the note
to be of any particular kind. The handoff must be active, readable to this profile
and destination, and exactly at the supplied current version.

### Response

```json
{"schema":"cairn.handoff-request-status/1",
 "handoff":{"record_id":"UUID","version":1,"body_sha256":"64 lowercase hex"},
 "links":"caller_asserted",
 "items":[{"item_id":"review","event_id":"UUID","availability":"available",
           "task_outcome":"unknown",
           "deliveries":[{"delivery_id":"UUID","state":"handled"}],
           "deliveries_more":false,
           "response_group":{"state":"open","expected":1,"responded":0}}]}
```

- `handoff` attributes the view to the exact version read. The hash describes that
  version's body; the body is not returned. A later revision is a different version.
- Items keep request order and IDs. `links` is always `caller_asserted`.
- `availability: "unavailable"` returns only `item_id`, `event_id`, `availability` and
  `task_outcome`. An absent event, an event this caller cannot read, an event in
  another collection, a local-only event under a hosted destination and an event that
  is not a request are indistinguishable.
  A readable handoff grants nothing here.
- For an available request, `deliveries` lists up to 16 deliveries ordered by delivery
  UUID, with `deliveries_more` true when there are more. The publisher sees every
  recipient's delivery; a recipient sees only its own. A delivery has only its ID
  and its actual state: `pending`, `leased`, `handled`, `ignored` or `failed`. No
  lease, consumer, result, failure code or message is added.
- `response_group` is `null` when the request has no group or the caller is not its
  publisher. Otherwise it has exactly the stored `state` (`open`, `collected`,
  `partial` or `incomplete`) and the snapshot's `expected` and `responded` counts.
  An `open` group can be past its deadline: a read never sweeps or persists
  closure. Members, observations and reply references are never returned.
- `task_outcome` is always `unknown`.

A handled request may have no result and an open group. A reply, a collected or
closed group, `failed`, `ignored`, cancellation, an elapsed deadline and a process
exit are observations of handling or collection. None is an assertion that a
handoff item is complete, and this view does not assess the work. Missing, partial
and inaccessible information stays explicit: an empty `deliveries` list means no
delivery is visible to this caller, not that none exists. Check current task
evidence before explicitly revising a saved handoff.

### Refusals and bounds

| Code | Meaning |
| --- | --- |
| `NOT_FOUND` | The handoff is absent, in another collection, local-only under a hosted destination, or no longer active. These are not told apart. |
| `VERSION_CONFLICT` | The supplied version is not the handoff's current version. Read the current handoff and resubmit its links. |
| `PAYLOAD_UNAVAILABLE` | The current handoff payload was excluded by deletion. |
| `INVALID_REQUEST` | Closed-field, ID, uniqueness or count validation failed. |
| `BUDGET_REFUSED` | The combined response would exceed the bound below. |
| `STORE_ERROR` and transport errors | The service or database failed. No partial rows or manufactured `unavailable` items are returned. |

The complete UTF-8 JSON response envelope, final newline included, is at most
16,384 bytes. Sixteen items with one delivery each always fit. A request with more
than 16 visible deliveries returns its 16-row prefix and `deliveries_more: true`.
If the combined response cannot fit, the whole operation refuses with
`BUDGET_REFUSED`; Cairn never drops an item, changes an availability or erases
required status. Check fewer items per call. An unavailable item has a fixed size,
so a refusal does not reveal whether an inaccessible event exists.

### Read-only and limits

The operation runs in one read-only PostgreSQL transaction. It claims, leases,
acknowledges, retries or completes nothing; it changes no event, delivery, group,
cursor, note, history, link or ranking, and persists no sharing policy. A refusal
may use the existing refusal audit. It makes no model call.

- No consistency claim spans independent reads. Do not rely on one status call matching a
  separate `event-status`, `response-group` or `history` read, and expect later calls
  to observe transitions.
- The view is not stored or shared; each caller must supply its own links.
- Remote machine profiles do not yet have this operation.
- It does not close, annotate or re-rank a handoff. The original automatic-closure
  proposal remains unimplemented, and passing tests here do not show that the view
  improves retrieval or handoff quality.
