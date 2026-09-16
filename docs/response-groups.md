# Response groups

Migration 047 adds bounded reply collection to request publication. A group fixes
its recipient list when the request is published and records explicit replies.
It does not evaluate their conclusions or infer task acceptance from handling.

## Create a group

Add both flags to ordinary request publication:

```sh
cairn publish --profile NAME --request-id REQUEST_UUID \
  --topic reviews --kind request --version VERSION \
  --response-deadline 2026-09-17T15:00:00Z --response-policy all RECORD_UUID
```

Use real identifiers and an absolute future RFC 3339 deadline. JSON publication
accepts `response_group: {deadline, partial_policy}`. The policy is `all` or
`partial`; it must be explicit. Direct agents and topics are supported. A pool
has no recipient until assignment, so it cannot supply this publication-time
snapshot. Use an explicit agent or topic when a fixed recipient set is required.

The group ID is the request event UUID. If no correlation UUID is supplied,
publication sets it to that UUID. Explicit correlation IDs remain usable, but
correlation alone never identifies a group member or a reply's causal request.
The group contains the 1–100 deliveries actually created by that publication.
Later subscription changes cannot add or remove members.

`GROUP_EMPTY`, `GROUP_LIMIT` and `GROUP_EXPIRED` refuse empty snapshots, more than
100 recipients and an already-passed response deadline. The entire publication
rolls back, including fanout. An identical successful publication retry returns
the original event and snapshot. Changing intent requires a new request UUID.
Scheduled groups use the subscriptions at firing; these three refusals retain a
skipped occurrence with the refusal code. Scheduling does not move an absolute
response deadline forward.

## Reply explicitly

Use an ordinary `response` publication with:

- `--causation-id` equal to the grouped request event UUID;
- `--correlation-id` equal to the request's correlation UUID;
- `--to` equal to the original publisher;
- an exact, eligible selected-result reference.

The publisher must be the store-associated consumer in the fixed snapshot.
Do not substitute a harness or account name for the addressed session UUID.
Native inbox context already supplies a response argument array. For grouped
fresh workers, `CAIRN_WAKE_CONTEXT` now supplies `response` and `response_group`.
After completion, append `--version RESULT_VERSION RESULT_RECORD_UUID` from its
result reference to that response array. It retains the slot's ownership and a
fixed reply request UUID. Retry unchanged arguments after a lost response.

Each member contributes at most one reply. Ordinary publication and the group's
observation commit together. Later explicit publications from that member remain
events but receive `duplicate` observations. Retrying the same publication UUID
does not create another observation. A first reply arriving after closure is
`late`. A response with the wrong member, destination or correlation is
`unmatched`; it remains an ordinary event and does not fill a member.

The classification order is unmatched, duplicate, then late. Thus a second reply
from an already-counted member remains duplicate even after the deadline.
Ordinary event visibility still applies to observations; naming a group as a
causal parent does not expose an otherwise private response to the group owner.

## Collection states

| State | Meaning |
| --- | --- |
| `open` | Waiting for explicit responses. |
| `collected` / `all_responded` | Every snapshotted member supplied a counted response. |
| `partial` / `deadline` | Deadline reached under `partial` policy with at least one counted response. |
| `incomplete` / `deadline` | Deadline reached without satisfying the selected policy. |
| `incomplete` / `restore_fenced` | An open group was restored; later reply history may have been lost. |

With `all`, any missing reply at the deadline leaves the group incomplete. With
`partial`, one or more replies are retained as a partial collection. Neither
policy closes early on the first reply. Both become collected when everyone
replies before closure.

A handled acknowledgment is not a reply. Failed or ignored request deliveries
remain visible on their members, but do not fill them or automatically retry work.
A member can explicitly reply with a failure report; collection does not interpret
that report as success. `reported_task_outcome` remains `unknown`.

The response deadline limits collection; it does not cancel member processes.
[Request controls](request-controls.md) govern admission, process deadlines and
cancellation separately. The existing scheduler sweeps at most 100 due groups
per control transaction. Response publication also checks the database deadline
and closes a due group before classifying its reply. If the scheduler is stopped,
a read can show `open` with `overdue: true`; reading alone never claims to have
persisted closure. No late reply reopens a terminal group.

## Inspect results without copying payloads

`cairn response-group --profile NAME EVENT_UUID` returns the owner's group,
members and a page of response observations. `--after POSITION --limit N` pages
observations in event insertion order. `cairn response-groups --profile NAME`
lists the owner's groups, with optional `--state`, `--after` and `--limit`.
Limits default to 50 and are bounded to 100. Group lists show current state;
their position cursor is not a stream of later state changes.

The raw agent operations are `event-group` (`event_id`, optional `after`, `limit`)
and `event-groups` (optional `repo`, `state`, `after`, `limit`). Existing profile
and registered-session association checks apply. A recipient cannot use these
operations to read siblings' responses through someone else's group.

Group tables retain event/delivery references, not source or response bodies.
Member reads check current sensitivity, lifecycle and exact-version availability.
An unavailable member response has `payload_available: false` and omits its
result reference and member response event ID. Ordinary visible event metadata
can remain in the observation history. A later privacy change or forgetting does
not rewrite the historical fact that a response arrived, reopen a group, or imply
that the payload is still readable. Inspect current availability before using a
result; `collected` is not a content-validity or task-acceptance assertion.

## Restore and rollout

All publication paths acquire the restore generation lock before the collection
insertion lock. Membership, response observation and group closure belong to the
same PostgreSQL transactions as their corresponding events. Restore preserves
retained group history and closes open groups as incomplete, since a backup can
omit later replies. Review before creating new intent. Reissuing an individual
failed request does not copy the old group's membership or collection policy.

Apply migration 047 with the matching API/CLI and supervisor binary. Stop the
scheduler and consumers before migration, keep the existing profiles and trusted
host model, then start API before consumers. No new credentials, general workflow
engine, quorum policy or automatic replacement worker is introduced.
