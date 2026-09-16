# Fresh-work pool dispatch

Status: queue, assignment, supervisor presence and explicit availability are in
implementation. See [the implemented contract](../worker-pools.md). Deployment
and native runtime acceptance are not claimed by this design note. Automatic
structured quota ingestion remains outstanding. Native wake-session association
is a separate deployed slice.

## Queue and assignment

Add `pool` as an explicit event destination for requests. Keep the original pool
destination in event history. Publication creates a queued pool request with its
exact source reference; it does not create a delivery to an invented session or
choose a slot from a client-side directory snapshot.

Each configured supervisor reports its own slot's pool memberships, harness,
configured model, workspace and selected capabilities through its existing
profile. These values describe the operator's launcher configuration. They do
not establish task authority or provider capacity. A heartbeat shows supervisor
liveness separately from account availability.

Extend the existing wake claim transaction to reserve an eligible queued pool
request, create its first delivery under the selected slot, and create the wake
attempt together. A database uniqueness constraint permits one assignment per
request. Existing direct-slot requests keep their current path. The native hook
links the actual launched conversation afterward, preserving both the slot
delivery and the conversation's continuing UUID.

Eligibility requires a live supervisor, enabled/available binding, matching pool
and requested exact capabilities, and the configured workspace. Source text
cannot supply a command, credentials or a workspace override. Membership in a
pool is not a live agent address. Sending to an existing UUID never enters this
queue or silently launches a replacement.

## Failure and recovery

The existing durable attempt hold and cgroup cleanup rules apply from assignment
onward. An uncertain launch or executed attempt never moves to another account.
This implementation does not enable automatic reassignment; any future
pre-launch fallback must have a proven never-started condition and a retained
assignment history. Existing bounded pre-launch retries remain within the
selected slot.

Keep account health distinct from service liveness. Explicit provider quota
failures mark that binding unavailable; generic process failures retain their
actual classification and launch backoff rather than being labelled quota.
Heartbeat must not clear an unavailable account. Record the reason, observation
time and any known retry time. Operator recovery is explicit and inspectable.
The two accounts per harness have independent health and launch state.

Queued work survives an API/supervisor restart without a second publication.
Claim retries return the original assignment after an uncertain commit. Missing
notifications may delay polling but cannot remove accepted work.

## Bounds and acceptance

Bound admission by pool and publisher before accepting new work; return an
explicit full-queue error without an event when a configured bound is reached.
Do not discard already accepted work. Keep one active attempt per slot and add
per-binding launch spacing/backoff. Selection must account for publisher
fairness; a high-volume sender must not indefinitely exclude another sender.
Add priorities only if observed contention calls for them, as the v1 plan states.

Disposable database tests must prove concurrent publication/claim uniqueness,
exact retries, no eligible slot versus unavailable account, workspace/capability
matching, independent account failures, bounds/fairness, restart and restore.
Native runtime tests must observe a real conversation association and explicit
handling after pool assignment. Operator inspection must distinguish queued,
assigned, never launched, possibly executed, handled and independently accepted.

Rollout must preserve legacy direct inboxes and topic fanout, migrate additively,
verify backup/restore, and provision memberships from existing owner-controlled
bindings. It must not infer capacity from seven running service units.
