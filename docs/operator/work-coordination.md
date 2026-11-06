# Operate work coordination

Cairn's work view combines a read-only Plane projection with selected local
progress, blockers, exact deliverable links, and agent coordination observations.
Plane remains the owner of issue state and assignment. A reply, a satisfied wait,
a handled notice, and an accepted task result remain separate facts.

These commands use the operator's configured PostgreSQL connection unless they
explicitly select an authenticated agent profile. The browser uses a private
operator session on loopback, with Host, Origin and CSRF checks. Work-view access
does not make its contents eligible for hosted memory or model input.

## Build and upgrade together

Use one complete source revision for the CLI, API, scheduler and browser. The
work-coordination release includes migrations 075 through 078 together. Do not
install a branch containing 077 without 076: migration history uses the ordered
file position and digest. Installing that incomplete set makes later insertion
of the missing migration incompatible with the recorded history.

Follow the [deployment procedure](deployment.md) and
[restore admission contract](../restore-admission.md) for the selected store.
Stop incompatible processes before an operator-authorized upgrade, preserve the
backup and recovery witness, migrate with the selected binary, and restart the
compatible processes. A source checkout or passing test does not install that
binary, migrate a running store, or update a native harness.

Inspect admission and outstanding obligations with:

```sh
cairn work features status
```

The feature names are `work-view/1`, `work-artifacts/1`,
`coordination-waits/1` and `next-turn-notices/1`. Enable a selected feature with
`cairn work features enable --feature NAME --request-id UUID`. Use a new UUID
for new intent, and preserve the exact UUID and arguments after an uncertain
response. `disable` stops new admission while preserving the feature's maintenance
path. `retire` checks outstanding required state and is irreversible for that
feature version.

## Bind and inspect Plane work

Configure the existing Plane CLI profile first. Supply the actual workspace and
project UUIDs; the local slug is a Cairn name for that explicit binding. The
uppercase identifiers below are placeholders.

```sh
cairn work project add --slug example --workspace WORKSPACE_UUID \
  --project PROJECT_UUID --collection COLLECTION --request-id REQUEST_UUID
cairn work refresh --project example --collection COLLECTION
cairn work list --project example --collection COLLECTION
cairn work show --project example --item ITEM_UUID --collection COLLECTION
cairn work serve --project example --collection COLLECTION
```

Open the launch URL printed by `work serve`. Keep that URL private: it establishes
the browser's operator session. Use Refresh to request a new Plane observation;
ordinary page reads and coordination polling do not refresh Plane. A stale
snapshot or failed current read is labelled. Auth rejection removes protected
copied Plane fields from joined views; it does not silently serve an older
authorized-looking copy.

Use explicit associations to connect a work item to a session, event, result or
other supported target. Select exact UUIDs and versions from the existing
coordination context. A matching task label, project name or process presence
does not create an association. Progress and blockers are local annotations;
their revisions and history do not change Plane's issue state.

## Register exact deliverables

Register a revision, then link it to an item with role `input`, `output` or
`evidence`. Registration alone creates no work-item relationship.

| Kind | Required identity | Meaning |
| --- | --- | --- |
| `git` | Repository locator, object format and full commit object ID | Exact commit; branch is only a display hint. |
| `report` | HTTPS locator and revision label | A locator can remain mutable; preserve the declared provenance and optional digest. |
| `ci` | Provider, repository, run ID and attempt | An observed run/attempt; its status is not task acceptance. |
| `cairn_result` | Exact record UUID and positive version | Reads remain subject to current source scope and visibility. |

For example, with a selected report URL:

```sh
cairn work artifact register --kind report \
  --url https://reports.example.com/review --revision review-1 \
  --display-name 'Selected review' --collection COLLECTION \
  --request-id REGISTER_REQUEST_UUID
cairn work artifact link --project example --item ITEM_UUID \
  --revision REVISION_UUID --role evidence --collection COLLECTION \
  --request-id LINK_REQUEST_UUID
cairn work artifact impact --revision REVISION_UUID --collection COLLECTION
```

Use the returned revision UUID in the link command. Cairn does not fetch a report,
clone a repository, execute an artifact or infer that its claims are verified.
The browser supports revision selection, links, retained history and reverse
impact. Pagination performs a new visibility check for each page.

Unlink removes the relationship at its expected revision and preserves its
history. It remains a maintenance operation after admission is disabled or its
source has been forgotten; unavailable rationale is not returned. Purge removes
selected local artifact payload and fences resurrection. Neither operation
deletes the external deliverable.

## Wait for an exact coordination fact

Use `request_response` for the supported response predicate, or
`response_group_closed` for closure under a group's own policy and deadline.
Registration belongs to the actual authenticated agent/execution. A caller flag
or another agent's UUID is not permission to impersonate that owner.

```sh
cairn wait create --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id AGENT_UUID --execution-id EXECUTION_UUID \
  --repo COLLECTION --request-id WAIT_REQUEST_UUID \
  --predicate request_response --dependency REQUEST_EVENT_UUID \
  --deadline DEADLINE_RFC3339
cairn wait list --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id AGENT_UUID --execution-id EXECUTION_UUID --repo COLLECTION
```

The agent can finish its turn after registering. The configured scheduler settles
waits and drains their durable notification outbox; `schedule-serve --repo COLLECTION --once`
performs one operator sweep. Keep scheduler operation available while accepted
waits or outbox work remain, even when new wait creation is disabled.

Inspect a wait with `wait get --wait UUID`, or cancel it with
`wait cancel --request-id UUID --wait UUID`, retaining the same profile/session
flags. Cancellation does not undo the underlying request. Wait satisfaction
does not establish task success. The work-item panel shows only explicitly
associated dependencies and distinguishes predicate state, notification
publication and recipient handling.

## Deliver a notice at the next owner input

Quiet delivery is for routine notices. It requires the supported pinned Codex
native package, a verified launch and a signed observation of owner input. See
[native owner input](../native-owner-origin.md) for reproduction, protected
artifacts, launcher arguments and exact lifecycle binding. Other harnesses and
unpatched Codex have no supported quiet-turn capability.

Publish an exact selected source using the existing sender profile:

```sh
cairn publish --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id SENDER_AGENT_UUID --execution-id SENDER_EXECUTION_UUID \
  --request-id NOTICE_REQUEST_UUID --to agent/RECIPIENT_UUID \
  --kind notice --notice-delivery next_organic_turn \
  --notice-expires-at EXPIRY_RFC3339 --version SOURCE_VERSION SOURCE_UUID
```

An unsupported recipient refuses quiet publication. A queued quiet notice does
not wake the conversation. Only a fresh, verified owner input admits its bounded
batch; an API-created user message or an automatic queue turn is not that signal.
The native integration reads and acknowledges each admitted exact source through
the normal lifecycle. Late notices remain queued for a later qualifying input.

Use `notice-pending` to inspect retained queue state. The original publisher can
use `notice-flush --request-id UUID EVENT_UUID` to release a still-deferred notice
to ordinary delivery. Supply the same connection and sender/session flags as
publication. Expiry is a recorded outcome, not an acknowledgement. Disabling quiet
admission does not turn the remaining queue into a wake request.

## Disable, settle and retire

Settle dependencies before removing feature support. Keep compatible readers,
maintenance commands and additive schema after retirement.

| Feature | Stop new work | Settle retained work |
| --- | --- | --- |
| Work view | Disable new projection admission/writes. | Disable active project bindings with `work project disable`; retain inspectable history and purge maintenance. |
| Artifacts | Disable registration and new links. | Unlink active relationships at exact revisions; preserve history and purge fences. Dependent advice must also be settled before artifact retirement. |
| Waits | Disable creation. | Complete or explicitly cancel accepted waits, drain their original notification outbox and reconcile actual delivery. |
| Quiet notices | Disable new admission. | Inspect queued notices, explicitly flush or expire them, and reconcile exact materialized native holds. |

Check `pending_count` and `unsafe_downgrade_reason` after each step. A count is
outstanding required state, not necessarily the number of work items. Retirement
must refuse while accepted obligations remain. A compatible binary that omits a
feature may operate only after its admission is disabled and its required state
is settled; a binary whose schema ceiling is older still refuses. Reverting code
without preserving these readers is not a rollback procedure.

For restore, apply the latest recovery witness before reopening reads. Restore
generation changes fence old work cursors, waits, launch observations and native
admission. Inspect the actual retained state and use the
[event recovery procedures](../event-recovery.md) for held attempts. Do not create
replacement requests, registrations or conversations to bypass an uncertain
outcome. A UI reload after a restore fence is not proof that the physical restore
and deletion reapplication were completed.
