# Authorized successor event continuity

When an operator resumes or replaces a stopped or failed agent session in a new
session, the new session does not automatically inherit the predecessor's inbox,
delivery leases, or event visibility. A readable handoff note, matching project,
or self-declared successor metadata confers no access to predecessor events.
Ordinary causal-parent checks still require the publisher or an addressed
delivery consumer (`NOT_FOUND` otherwise).

Migration 060 adds an explicit, bounded operator authorization in
`cairn.agent_successor_grant` so a designated successor execution can inspect
selected predecessor events and publish linked follow-ups attributed to itself.

## Grant contract and lifetime

An unscoped local operator (`cairn session-successor-authorize`) records a grant
binding:

- `repo`: the exact event collection repository;
- `predecessor_agent_id`: the registered predecessor session UUID, together with
  its observed `predecessor_execution_id` at authorization time;
- `successor` (`agent_id`, `execution_id`): the exact live successor session and
  current execution UUID, which must be distinct from `predecessor_agent_id`,
  registered in `repo`, not `stopped`, and at the current `database_generation`;
- `event_ids`: 1 to 64 distinct event UUIDs in `repo` where
  `agent/<predecessor_agent_id>` is either the event publisher or an addressed
  delivery consumer;
- `ttl_seconds`: bounded grant lifetime (default `3600`, minimum `1`, maximum
  `86400`), persisted as `expires_at = granted_at + ttl`;
- `granted_by`, `granted_at`, `reason`, and idempotent `request_id`.

Authorization requires the predecessor session to have no unfinished native or
wake delivery attempt. It does not move deliveries, create credentials, mark old
deliveries handled, or execute predecessor work.

## Fencing, predecessor resumption, and revocation

A successor grant is valid only while all of the following hold inside the
caller's transaction:

1. The caller is authenticated as an active session channel (`ForAgentSession`)
   whose `(agent_id, execution_id)` matches `(successor_agent_id,
   successor_execution_id)` on the grant. Base profile calls without a pinned
   session execution gain no successor access.
2. The grant's `database_generation` equals the current `cairn.retrieval_generation`
   (`fenceRestore` advances the generation and fences all prior grants).
3. `revoked_at IS NULL` and `expires_at > clock_timestamp()`.
4. The target `event_id` is listed in `grant.event_ids` and belongs to `repo`.
5. The predecessor session row still has `execution_id =
   grant.predecessor_execution_id` and `stopped OR expires_at <=
   clock_timestamp()`. If the predecessor resumes registration (new
   `execution_id`) or refreshes live presence, successor access under the grant
   ceases immediately without deleting retained grant history.
6. The event and source satisfy the caller's read/publication `Destination`
   (`sensitivity = 'shareable'` or `AllowLocal`).

An operator can revoke a grant early with `cairn session-successor-revoke`
(`grant_id`, `request_id`, `reason`). Revocation stamps `revoked_at`,
`revoked_by`, and `revoke_reason` in place; retrying the same `request_id` and
input is idempotent. Competing or sequential successors may hold separate
operator grants, each independently bounded by its own execution, event list,
expiry, and revocation state.

## Inspection and linked follow-up semantics

For an authorized successor session, `readEvent` and `AgentEventStatus`
(`event-status` / `event-inspect`) can read the selected predecessor event:

- If `agent/<predecessor_agent_id>` was the event publisher, the successor sees
  the event and its deliveries under the same destination sensitivity rules as
  the predecessor.
- If `agent/<predecessor_agent_id>` was a delivery consumer, the successor sees
  the event and the predecessor's delivery row (plus any delivery addressed to
  the successor itself), with lease tokens omitted.
- Original delivery states (`failed`, `handled`, `pending`, etc.), failure codes,
  attempt histories, receipts, and publisher fields remain unchanged.

When publishing a follow-up (`PublishEvent`) with `causation_id` naming an
authorized predecessor event:

- The new event is published by `agent/<successor_agent_id>` (never impersonating
  `agent/<predecessor_agent_id>`), links `causation_id` to the predecessor event,
  and enforces normal collection and local/shareable causal-parent disclosure
  checks.

## Response-group preservation

Response groups fix their member snapshot (`cairn.agent_response_group_member`)
at request publication time by exact consumer principal
(`agent/<predecessor_agent_id>`).

- A successor follow-up is attributed to `agent/<successor_agent_id>`.
- If the successor publishes a `notice` (or a `response` when the successor is
  not itself a distinct member of that response group), `observeGroupResponse`
  never matches `agent/<predecessor_agent_id>`'s member slot, never overwrites
  or fills `response_event_id` on the predecessor's member row, and never
  reopens a closed (`collected`, `partial`, or `incomplete`) response group.
- Any `response` published by a non-member successor with `causation_id`
  pointing to a grouped request is recorded in `cairn.agent_response_observation`
  with disposition `unmatched`, preserving the original group state and member
  history while retaining the causal link on the follow-up event.

## CLI usage

Authorize a successor session:

```sh
cairn session-successor-authorize <<'JSON'
{
  "request_id": "NEW_UUID",
  "repo": "/home/halbritt/git/cairn",
  "predecessor_agent_id": "PREDECESSOR_AGENT_UUID",
  "successor": {
    "agent_id": "SUCCESSOR_AGENT_UUID",
    "execution_id": "SUCCESSOR_EXECUTION_UUID"
  },
  "event_ids": ["PREDECESSOR_EVENT_UUID"],
  "ttl_seconds": 3600,
  "reason": "Owner resumed oversight session in successor agent"
}
JSON
```

Revoke a grant:

```sh
cairn session-successor-revoke <<'JSON'
{
  "request_id": "NEW_UUID",
  "grant_id": "GRANT_UUID",
  "reason": "Takeover follow-up completed"
}
JSON
```
