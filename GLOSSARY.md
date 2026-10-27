# Cairn

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
