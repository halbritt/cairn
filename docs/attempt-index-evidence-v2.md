# Selected staged-state evidence

`export_attempt_evidence_v2(prepared, output, *, allowed_paths, required_paths,
verify_attempt)` adds an index observation to the existing closed-attempt export.
`export_attempt_evidence()` and `export_work_product()` keep their v1 schemas and
behavior. All three share the existing inspection, selected-file writes and
size checks. No command writes Git objects to inspect the index.

The motivating synthetic case changes a tracked file, exports, stages that same
content, then exports again. The v1 files, three patches and manifest are identical;
the two index observations distinguish unstaged from staged. Files and patches
alone cannot recover this fact.

The returned and written `attempt-evidence.json` uses schema
`cairn.work-product-attempt-evidence/2`. Its additional `index_observation` has:

| Field | Meaning |
| --- | --- |
| `schema` | `cairn.work-product-index-observation/1` |
| `state` | `observed`; inspection failure refuses the export. |
| `binding` | Exact prepared request ID, occurrence ID, request hash and supervisor source hash. |
| `workspace_sha256`, `head_commit` | Prepared workspace and observed HEAD, matching the containing export. |
| `attempt_content_sha256` | `canonical_sha` of this manifest with `index_observation` removed and `schema` restored to `cairn.work-product-attempt-evidence/1`. |
| `staged_paths` | Sorted UTF-8 names from HEAD-relative `git diff --cached --name-only -z`, retaining Git's configured rename behavior. |
| `staged_change_paths` | Separately named list with rename detection disabled; both rename endpoints must be selected. |
| `index_files` | `index: {sha256, bytes}` pins the entire raw regular index; raw bytes are not exported. |

The containing manifest binds the observation to the exact selected content,
terminal and patch pins. Consumers pin the complete serialized manifest as well;
the inner content hash excludes the observation to avoid a self-hash cycle.
This is mechanical correspondence, never runtime authentication. For v2, selected
patch paths are literal filenames, including Git pathspec metacharacters. The
inner v1-shaped projection is a hash definition, not a promise that calling the
legacy exporter on pathspec-shaped filenames reproduces those patches.

An observed empty `staged_paths: []` is distinct from an absent or unreadable
index, which refuses. A valid empty index can stage deletions relative to HEAD.
Staged-only paths are checked even if the working file has been restored or
removed. Rename-aware names differ intentionally from `staged_change_paths`.
NUL decoding preserves spaces and Unicode names; the existing weekly evaluator's
human-format output followed by `.split()` does not. This change neither edits
that evaluator nor claims exact legacy grading equivalence or defines a rubric.
Existing safe-relative-path rules still refuse controls and invalid UTF-8.

## Inspection boundary

V2 supports regular SHA-1 Git indexes with format versions 2–4. It checks the
header, count bound and index checksum, then relies on Git to interpret entries.
It captures the complete raw index digest before Git inspection and rechecks it
and HEAD before creating output. Existing workspace snapshot checks remain.
Missing/malformed index state, Git/read failures, indirection, disallowed staged
paths and observed drift refuse before output mutation. Index reads keep the
existing per-file ceiling; files, patches and all manifest metadata keep the
same 64 MiB total export ceiling.

**Split indexes are unsupported by this entry point.** A synthetic probe showed
Git refreshing shared-index mtime/ctime during reads despite `GIT_OPTIONAL_LOCKS=0`.
V2 therefore refuses any `.git/sharedindex.*` entry before invoking Git inspection,
including unused dependencies. It also checks for their appearance before output.
Missing split dependencies cause inspection refusal rather than incomplete evidence.
Symlinked Git directories/indexes, hardlinked indexes and external Git indirection
remain refused. Supporting split indexes needs a separately reviewed non-mutating
read strategy; callers must not normalize an actor's index merely to obtain evidence.

The trusted callback and quiescence contract are unchanged from
[closed partial work evidence](attempt-evidence-export-v1.md). The caller must
establish terminal/failure-evidence closure and exclusive quiescent inspection.
Before/after checks cannot exclude a hostile concurrent writer or an ABA change.
No callback declaration, UUID, checksum or exported schema authenticates this
boundary. Later output I/O failure can still leave a consumed partial target;
per-file fsync is not atomic batch publication.

## Scope and validation

Focused tests use only disposable synthetic Git repositories. They cover the
public before/after counterexample, v1 preservation, rename/path distinctions,
index corruption/missing state, split-index refusal without timestamp mutation,
staged-only unselected paths, inspection/index/HEAD/dependency drift, exact selected
pins, closure binding, literal filename extraction and total-cap enforcement.

This is one evidence prerequisite. Root owns runtime integration and trusted
closure. Packet construction, case-specific evidence sufficiency, authorized
release, authentic fresh grader sessions, dual grading and campaign success remain
unimplemented here. Privacy/capture HOLD, engineer exclusions and coordinator
dispute authority remain intact. Original 21 × 3 × 2 = 126 attempts / 63 pairs,
1900 distractors and two actual weekly campaigns remain full-goal obligations;
these synthetic tests satisfy none of those operational requirements.
