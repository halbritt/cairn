# Retrieve competing advisory positions

Cairn normally omits notes in an open conflict. When the disagreement itself is
useful context, request `advisory_conflicts: true` on `cairn_search`:

```json
{"query":"connection strategy","advisory_conflicts":true}
```

The CLI equivalent is:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" search \
  --repo "$HOME/git/cairn" --task TASK_ID --run RUN_ID \
  --advisory-conflicts 'connection strategy'
```

The flag also works on `context`, `agent start`, and local or authenticated
`run`. Raw compile/index requests use the same Boolean field. This option is
restricted to `purpose: context`. Retained execution must repeat the original
advisory-conflict intent. Repeat it on search retries and later pages too.

Each delivered position has `conflicts`, containing the conflict ID, group
version, and exact record/version references for every competing position.
Opening reasons, resolver context, and conflict actor fields are excluded from
this delivery descriptor. The record's ordinary authorship, evidence and
authority qualifiers remain visible. Nothing selects a winner, resolves the
disagreement, or turns advisory text into an instruction.

Read the marked previews together. Pull either entry using its complete
`pull_arguments`. The response keeps the requested body in `selection` and
returns the other complete positions in `competing`. Read both fields before
applying the guidance. Each entry has its own handle; to inspect evidence
attached to a counterpart, use that counterpart's handle from the original index.

A group pull spends one credit and charges all returned positions against the
index session's shared byte budget. It refuses when the whole response cannot
fit, without delivering a partial group or spending the credit. Marked entries
omit `summary_span`, and group body pulls refuse `span`. Separate evidence pulls
retain their existing whole-object or byte-span interface and shared budget;
they also recheck the complete conflict group's current eligibility.

## Qualification and allocation

At least one position must match the requested query, kinds, entity hints or
failure signature. Its counterparts need not independently match those relevance
filters, but every position must satisfy repository/task/run scope, destination,
applicability, lifecycle, attribution, evidence and authority gates. Each current
version must still be the version named when the conflict opened. An edited
position cannot silently replace the disputed original.

Overlapping conflicts form one connected group for delivery. If any position is
private, out of scope, stale, or otherwise ineligible, the whole group is omitted.
Hidden counterparts contribute no IDs, bodies, conflict details or extra omission
counts. The visible position may contribute the existing `OPEN_CONFLICT` count.
Binding C conflicts continue to refuse compilation, including with this option.

Mandatory instructions are allocated first, then complete advisory groups, then
other optional notes. A group's best matching position determines its ranking;
a forced counterpart cannot improve that rank. Independently recorded positions
are preserved even when their bodies are identical. Optional and total budgets
omit complete groups. Ranked and browse offsets count a complete group as one
position; a page never starts halfway through it.

Delivery is bounded to connected components of at most 16 positions and 16 open
conflicts. Larger components are omitted whole. The scoped conflict scan is also
bounded; an incomplete snapshot preserves ordinary omission. These are delivery
limits, not automatic resolution or deletion rules. Large complete bodies can
still exceed the shared expansion budget even when their previews fit.

## Current and historical context

Fresh pulls, including retries, recheck the counterpart versions, eligibility and
conflict descriptors before reading a cached response. Resolution, a newly
connected conflict, or an edited/ineligible counterpart requires a fresh search.
Retained execution checks the same facts before launch. A new ordinary optional
note does not by itself invalidate an existing index handle.

Historical recompilation uses the frozen group descriptors and original record
versions. It reproduces the old selection after later edits or resolution when
those payloads remain available; it grants no new delivery or execution authority.
Audited forgetting and purge cover bodies returned in `competing`, including
cached responses. Full [conflict inspection](conflict-inspection.md) remains a
separate local operation because its opening and resolution context can be private.

This opt-in uses semantic schema `cairn.semantic/14`. The API, CLI and native
adapter must support it together; an older retained-package consumer is not
qualified by its support for schema 13. No database migration is required beyond
034. Omitting the option preserves previous formats and default dispute omission.

[Verification](verification/advisory-conflicts-2026-09-10.md) distinguishes tested
retrieval contracts from the still-open question of whether competing guidance
improves real tasks. Richer interested-party resolution and observed outcomes
for work performed under an open conflict remain on the roadmap.
