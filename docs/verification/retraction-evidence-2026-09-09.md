# Supporting evidence in required retirement previews

The R3 review found that required previews already covered retained versions,
known relation dependents and recorded uses, but omitted their supporting
evidence. A reviewer needed separate reads to inspect the source of each version.
Previews now include a bounded, version-specific evidence inventory in the same
snapshot. This implements a stated roadmap requirement; reduced review burden
and memory's contribution to task outcomes have not been measured.

Baseline: `78b7bd6`, installed CLI `8dd9033` and API `20b7574`. The first test
had a fixture type error (`Cite` returns a revision reference); after correction,
the behavioral test failed because the preview JSON had no supporting evidence.
The implementation reuses `supportingEvidence`, preserving its citation-time
metadata, retained-byte integrity checks and explicit degradation states.

## Requirement audit

| R3 requirement | Evidence in the current implementation |
| --- | --- |
| Require a preview before retraction | `retractRecord` requires a caller-owned, unexpired token after grant and version checks; the Council audit test rejects a request without one. A standalone `impact` read cannot substitute for it. |
| Recheck version, dependency and use state | The preview records root version/exposure generation and a digest of the exact dependent set, current versions, lifecycle and exposure generations. Tests cover root edits, new relations, dependent edits, new root/transitive exposures, evidence refresh and expired/foreign tokens. |
| Show affected retained versions and uses | `dependentVersions` starts with every retained root version and traverses explicit version-qualified links. Transitive impact tests inspect the leaf's actual receipt. Historical relation tests preserve earlier links after edits; supersession tests preserve the old source's consumers and distinguish independent replacements. |
| Include supporting evidence | The new test clears current support while retaining an older citation and a dependent citation. The preview contains both exact versions, source identity, digest, byte passage and persisted check generation. Altered retained bytes are labelled divergent. Captured source bodies/labels are absent. |
| Serialize concurrent compile/retract | The existing concurrency test permits either withdrawal before selection or a stale-preview refusal; it rejects committed exposure alongside a successful obsolete preview. Database triggers advance the relevant generations. |
| Refuse open-conflict erasure | Existing conflict/retraction and durable-refusal tests keep the source active until explicit conflict resolution. The preview adds no authority or bypass. |
| Bound the complete preview | Existing version/use limits remain; the new evidence test accepts exactly 1,000 references and refuses 1,001 without returning or committing a preview token. Deletion shares this inventory. |
| Keep inspection protected | Actual operator and local authenticated CLI calls return matching historical evidence metadata. Hosted inspection refuses without revealing the source ID. The API keeps repository checks and its local-destination gate. |

The source requirement permits optional ancestry; this audit covers recorded
relations and evidence references. Source labels, similar text and replacement
alone do not create dependency edges. The preview does not infer unknown
derivations, certify human review, revoke every dependent instruction, erase
history, or establish broader lifecycle/recovery completion.

## Verification and failure history

Focused disposable PostgreSQL checks passed for the new evidence inventory,
1,000/1,001 boundary, dependency-state changes, existing exposure concurrency
and evidence refresh. Static checks and Go plus 40 Python tests passed. The first
full integration run passed Go race coverage, then failed in the added CLI test
because its helper call omitted a required empty payload. The fixture was fixed
and a clean full run was started; its terminal result is recorded below.

Scratch evidence is retained under `/tmp/cairn-retraction-evidence-*`; the
[machine-readable record](retraction-evidence-2026-09-09.json) retains hashes,
doctrine provenance and executed-check results. No operational record was
retracted, superseded or forgotten for this verification.

## Decision and compatibility

The owner delegated ongoing Cairn implementation decisions. R3 explicitly asks
for evidence in the required preview. Leaving separate inspection as the only
route would leave that requirement open. Reusing the existing evidence reader
keeps integrity and citation semantics under one owner. No new service, endpoint,
credential or database migration is needed.

The response adds `supporting_evidence`. No sources means an explicit empty
array; older responses lack the field. Link count is checked before captured
bodies are read; overflow and the 30-second deadline return no usable partial
preview. The additional cap may refuse an inventory that an older preview could
return without evidence. Existing tokens retain their original lifetime; request
a new preview for the richer inventory. Upgrade the API for authenticated access;
the core and operator CLI use the same implementation.

Revisit the fixed limits if an actual retirement requires a larger complete
inventory. Stop on omitted known citations, stale-token acceptance, disclosure
through a hosted profile or unexpected mutation. Source freshness, unknown
dependencies, broad task value and other roadmap requirements remain separate.

The clean full integration rerun passed, including Go race tests in disposable
PostgreSQL and actual operator/authenticated CLI plus stdio workflows. The final
static check passed after the dependency-state tests were added. No native adapter
changed, so native model/provider tests were not repeated for this local preview.


## Local installation

Installed clean `5946c5d` as CLI/API and project `bin/cairn`; SHA-256
`8199eef737f073289048949fb281306a47a6bbf49d52f9d5575ece4fda76846b`. API PID is 1219742; PostgreSQL remains PID 163669 with its original
executable. Client/server identities and executable hashes match. Native adapter,
semantic worker source, connection settings and identity configuration are
unchanged. This is a binary update with no schema migration; previous CLI/API
executables are retained outside Git for rollback.

An installed preview of the existing evidence guide returns its two captured
sources and exact citation passages on version 9, with 62 recorded uses. Operator
and local authenticated CLI metadata match while the latter has no client database
access. Hosted inspection refuses. The guide stays active at version 9; no
operational record is retired or rewritten. Preview IDs and local evidence paths
are retained in the machine-readable verification record. This checks useful
inspection on real stored data; it does not measure review savings or task benefit.

CI [34442784293](https://github.com/halbritt/cairn/actions/runs/34442784293)
was in progress at installation. Its terminal result is recorded separately.

CI completed successfully for `5946c5d`: Go race tests, 40 Python tests,
static/build checks and authenticated CLI/stdio workflows all passed. The earlier
in-progress installation checkpoint is retained for chronology.
