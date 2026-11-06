# Glossary

Cairn uses these terms in its memory, retrieval, and coordination contracts.

## Memory and retrieval

**Collection**: The repository identity used to scope stored memory. It can be
shared across projects and does not necessarily identify the current checkout.

**Record**: A stored item with an ID, retained revisions, scope, sensitivity,
and attribution. A revision identifies an exact version of its contents.

**Class A**: Ordinary testimony, such as a saved lesson or decision. Its source
can be known while its claim remains unverified.

**Class B**: A qualified claim with supporting evidence and the authority required
for promotion. Qualification does not make every possible use appropriate.

**Class C**: An authorized instruction governed by live grants and applicability.
Saving an ordinary note with instructional wording does not create Class C.

**Scope**: Collection, task, and run labels that restrict applicability. These
labels are not execution attestation.

**Destination**: The delivery context, such as local or hosted. Sensitivity and
the authenticated profile restrict what can reach that destination.

**Receipt**: Retained retrieval state identifying a context selection, its
conditions, and budget. Exposure does not prove use or usefulness.

**Handle**: An opaque reference for expanding a source under a receipt. Handles
expire and expansion rechecks current eligibility.

**Evidence**: Explicitly captured source material with retained identity and
digest. A citation identifies support; it does not establish that support is
sufficient for a claim.

## Coordination

**Event**: A durable publication referring to an exact source record version.
Requests, responses, and notices have different handling contracts.

**Delivery**: One recipient's handling state for an event. A lease grants a
bounded opportunity to handle that delivery.

**Execution UUID**: The current registered execution of an agent session. A new
execution fences stale session activity; the UUID itself is not a credential.

## Lifecycle integration

**Lifecycle bundle**:
A versioned release of Cairn's host lifecycle integration, selected and upgraded
as one complete set.

**Inbox recall binding**:
The explicit association between a memory engine, recall bridge, and compatible
memory and coordination configurations, with pinned files and an activation
state. A callback pair retains its first activation decision for the same native
turn or Claude prompt owner.

**Hook admission**:
The one decision whether a lifecycle hook runs: environment guards, the account
home, the event vocabulary, the session identity shape and the privacy-marker
walk. Memory, coordination and bound inbox recall consume the same decision.

**Native session record**:
The host's `cairn.native-session/1` file for one conversation: its registration,
owning process, held inbox attempt and settlement evidence. The NativeSession
module owns its attempt-lifecycle transitions (intend, claim, renew, release,
settle, retire); the key layout is implementation, not interface.

**Native attempt cleanup**:
The Store's interpretation of host observations about a native inbox attempt's
turn, captured tools and terminal scan. Confirmed cancellation and revoked
exclusivity use the same cleanup evidence but remain distinct settlement outcomes.

## Native verification

**Native tool result**:
The result emitted by a harness tool invocation, including its execution status
and output. Fixtures interpret this result separately from the Store state used
to verify delivery and settlement.

## Integration fixtures

**Disposable host**:
A fixture's private database, owner-only home with agent identities, and local
API, created inside a disposable cluster and torn down with it. The host owns
the cluster precondition, the envelope contract and process cleanup; a fixture
keeps only its scenario and assertions.

## Work Coordination

**Work Projection**:
Read-only cache and local view of an external tracker (such as Plane) project
and work items, bound by external tuple.

**Work Association**:
Typed durable link between a work item and a Cairn coordination target (agent
session, execution, event, record version, or request). It does not infer a link
from matching task or project labels.

**Work Annotation**:
Local operator-authored progress report or blocker note attached to a projected
work item without mutating the external issue tracker.

**Metadata Exclusion**:
Pattern-based redaction rule preventing sensitive external metadata from entering
projection storage or resurrection across refreshes.

**Work Artifact**:
An explicitly registered deliverable identity with exact revisions: a Git commit,
report, CI attempt or Cairn result version. Registration does not fetch, execute
or verify the external content.

**Work Artifact Link**:
An attributed, revisioned relationship from a work item to one registered
artifact revision, with role input, output or evidence. Removing the link does
not remove the deliverable.

**Coordination Wait**:
A durable subscription owned by an authenticated agent, with its creating
execution recorded, to a supported predicate on an exact request or response
group. Its terminal state and
notification delivery do not establish acceptance of the underlying task.

**Wait Notification Outbox**:
The durable reservation used to publish a wait's terminal notification after its
state transition. Publication retries retain the original event/source identity.

**Quiet Notice**:
A routine notice queued for a recipient's next supported owner-input boundary.
It does not wake that conversation. Admission requires a verified native
observation; ordinary user-role messages and caller-selected origin fields do
not establish one.
