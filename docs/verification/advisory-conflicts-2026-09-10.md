# Qualified advisory conflict delivery — 2026-09-10

Cairn now has an explicit route for retrieving unsettled advisory guidance.
Before this change, A/B positions in an open conflict were omitted even when an
agent wanted to inspect the competing advice. With `advisory_conflicts: true`,
the existing search/pull tools deliver complete eligible positions together,
without revealing the conflict's private opening context. Default omission and
binding C refusal remain unchanged.

The accepted requirement is design §8.4 and roadmap L2. This closes the qualified
advisory-delivery gap, not richer resolution or observed outcomes for tasks
performed under disagreement. There were no operational conflicts created or
resolved for these checks, and no model calls. Task benefit remains unmeasured.

## Verification

The baseline was clean `16122b0`. The initial disposable-store regression
returned no positions for an explicit advisory request. Its draft index assertion
was then refined to require compact marked previews and whole-group expansion;
forcing full bodies into the index would defeat compact retrieval for long notes.

`core/advisory_conflicts_test.go` exercises actual PostgreSQL behavior:

- Default omission, explicit full-body delivery, paired previews, pulling either
  position, exact serialized retry results, and per-position usage observations.
- Private, out-of-scope, inapplicable and edited counterpart omission; no private
  IDs, opening reasons, bodies or extra private-member omission counts.
- Overlapping groups, equal-text positions, optional budgets and browse offsets.
  A mandatory-context fixture also forces final envelope trimming and confirms
  that both positions are dropped together while required context remains.
  A 17-position connected component is omitted whole under the 16-position limit.
- Kind/entity/failure-signature relevance, semantic scoring and lexical fallback,
  plus historical reconstruction without a model call.
- Freshness checks on cached pulls and retained launch after an edit, resolution,
  newly connected conflict or restored restricted metadata. Historical seals
  still reproduce after live state changes.
- B evidence/authority qualifiers and evidence pulls, consequential opt-in refusal,
  unchanged C conflict refusal, all-body budget charging, refusal without spending
  budget or recording exposure, and purge of cached companion bodies.

Review found one additional defect: when a private counterpart caused complete
omission, the excluded visible candidate retained a failure-signature reference
without its qualifying facts. A new test reproduced the historical replay
failure. Clearing that reference with the excluded facts fixed it; the final
advisory tests and complete integration suite passed.

`make test-integration` passed on disposable clusters with the race detector,
including CLI/API/MCP and retained-run checks. `make check` passed. `make test`
passed, including 40 Python tests; its database-skipping Go results are not the
store evidence above. The extended native suite passed with OpenCode 1.18.21:
`scripts/check_advisory_conflicts.py` exercised ordinary capture, operator-created
synthetic disagreement, default omission, explicit filtered search, both complete
pull directions and exact retries through MCP stdio and native OpenCode tools.
Existing native permission/refusal, history, Unicode and recent-file checks passed.

The native run also checked retained requests produced by the installed previous
CLI (`670cf19`): quoted-query replay, unfiltered compile/index retry and
reconstruction, whole-body/evidence cached response identity, and ordinary
mutation retries. The subsequent final source correction touched only excluded
failure-signature facts; the final full store/MCP suite includes that regression.

## Decisions and limits

The opt-in extends the existing compiler, index and pull boundaries. Qualified
competing positions accompany a matching source, but cannot bypass eligibility
or silently replace its originally disputed version. Complete groups get optional
allocation priority after mandatory instructions. Their descriptors and all
returned bodies count toward the existing context budgets. Semantic schema 14
marks the new contract; no migration beyond 034 is needed.

Connected delivery components are capped at 16 positions and 16 groups. Larger
components remain omitted whole. Full bodies can still exceed the shared
24,000-byte expansion ceiling. The change adds neither conflict detection nor
automatic resolution. It does not infer that an agent read or correctly used a
position from exposure alone. The useful next evidence is actual work whose
judgment benefits from seeing disagreement; this test suite cannot establish it.

Pincite packet `pkt-72f7d6c8f27d0e4c` supported contract precedence, explicit
semantic-change boundaries, complete historical derivation and the no-change
alternative. Its SHA-256 is
`72f7d6c8f27d0e4ca730928ddf6c7dea9e3ebb0cfaecb508eb4c3135080aef70`.
Corpus `corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`,
and release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f` were used. The typed
decision receipt is validated and both citation loops are closed in the private
scratch directory. Remaining performance/index and Go-interface obligations are
nonmaterial: no database index, optimization, performance claim, new consumer
interface or interface-valued absence behavior is proposed. Every unmet
requirement and rationale is retained in the [verification record](advisory-conflicts-2026-09-10.json).

Private check logs and provenance are under
`/tmp/cairn-advisory-conflicts-20260910/`. Their checksums are recorded in the
verification JSON. These are verification records, not task-value acceptance.


## Local installation

Clean source `4df17afa97d2c3d457932ebb2bc7373f2d0fe83a` is installed in both CLI
locations and the running API; the installed OpenCode adapter matches source.
A current backup preceded replacement. The API restarted as PID 3338966, while
PostgreSQL remained PID 163669 on migration 034. No migration command ran.
All stored record versions retained their count and digest. Credential, host,
semantic-worker, Codex/OpenCode configuration and recent-file plugin hashes
remained unchanged.

The installed hosted profile searched with explicit advisory intent and file
association `core/currentness.go`, received schema 14 and pulled the existing
applicability guide v2 with its unchanged SHA-256. No operational conflict was
created or resolved, and no operational note was changed. The installation
manifest is retained privately; selected build and preservation evidence is in
the verification JSON.

[CI for the installed source](https://github.com/halbritt/cairn/actions/runs/34518961970)
completed successfully. Both PostgreSQL and plugin jobs, including every recorded
step, were inspected.
