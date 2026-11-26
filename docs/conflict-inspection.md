# Inspect a conflict

Find open conflicts in a repository, optionally restricted to a member record:

```sh
cairn conflicts --record RECORD_UUID REPO
cairn conflict CONFLICT_UUID
```

The list defaults to 100 open groups. `--include-resolved` includes closed
history; `--limit` accepts 1–200 and `--offset` follows the returned `next_offset`
while `more` is true. Lists include the opening actor, time, reason, current
group version and member count, without member bodies. Pagination uses a
snapshot per page; changes between requests can affect later offset pages.

The detail response contains the exact versions named when the conflict opened,
their bodies when available, observed writers, attributed producers, testimony
or instrumented witness, and original scopes/classes. Current version, class and
lifecycle are separate fields. Editing a note cannot rewrite its earlier
disputed position. Attributed producer and witness fields remain observations,
not verification that a position is true.

Resolved details include the retained resolution event ID, actor, reason and
time. Original members and opening context remain visible even when the resolver
also opened the conflict or wrote a member. Inspection neither selects a winner
nor changes eligibility; the existing authorized `resolve` operation remains
separate. Resolution does not retract either member.

After audited forgetting, a member remains identifiable but has `body: null`
and `payload_available: false`. Other positions remain readable. Opening and
resolution reasons and attribution/scope metadata are retained; their redaction
is separate unfinished work. Previously returned bytes cannot be recalled.
Details refuse more than the supported 16 members instead of returning a partial
set of positions.

Local agent profiles can call `conflicts` with required `repo` and `limit`
(1–200), and optional `record_id`, `include_resolved`, and `offset`, or
`conflict` with `conflict_id`. Both operations enforce repository scope and
refuse hosted profiles because conflict context can contain private material.
These are read-only operations and require no operator grant.

[Qualified advisory positions](advisory-conflicts.md) are available through
explicit ordinary retrieval. Acted-under-conflict outcomes and richer resolution
semantics remain on the [roadmap](roadmap.md).
