# Architecture follow-ups — 2026-10-27

The owner authorized deployment and assessment/implementation of the exploratory
follow-ups after CAIRN-259 completed. The bounded review selected four further
code changes and retained the other candidates according to their actual
contracts. This is follow-up triage, not a new exhaustive architecture audit.

## Implemented

- Claude refusal settlement keeps an unflushed journal visibly pending until the
  watcher settles it. The repair is separate from the structural changes.
- Native and manual-drain inbox delivery share construction of their persisted
  context and journaled commands. Admission, ownership checks, native result
  scope, and manual renewal remain with their respective callers.
- CLI, HTTP, and API-contract generation share refusal classifications. Eight
  existing HTTP conflicts now retain their public diagnostic and exit 4 in the
  CLI instead of losing their message and exiting 7. Unknown CLI errors still
  suppress possibly private text; HTTP classification is unchanged.
- Weekly executors select the pinned execution-profile interface from an explicit
  versioned capability. Older pinned harnesses retain their historical arguments.
  Invalid capability declarations and failed profile execution never fall back.

The weekly source migration does not create a schedule or choose a new study.
No weekly unit is installed. The latest selected historical occurrence expired;
its capacity override and writer cgroup are not current deployment inputs.
On 2026-10-28, the owner explicitly chose to keep the real-account weekly trial
unlaunched. The source migration does not change that decision.

## Remaining candidate decisions

| Candidate | Decision and condition for reconsideration |
| --- | --- |
| NativeSession wake/presence keys | Keep the existing transition owners. Generic setters would conceal transport, process, and reconciliation responsibilities. Move a complete transition when an actual change requires it. |
| Complete harness adapter seam | Characterize one harness's queue, process ownership and recovery contract before moving it. A complete extraction changes the pinned bundle and native lifecycle boundaries. |
| wake_join `_inside()` | Retain the explicit observer ownership transfer. More fixture extraction needs a second consumer of the same response/evidence protocol. |
| Native-session Store preamble | Establish the lock and first-refusal order across read, claim and cancellation paths before sharing their preamble. |
| Destination in Store | Keep explicit destination on trusted operator calls. A profile-bound view would be a public API and authority migration. |
| CompileRequest validation | Characterize normalization, first-failure order and original/effective budgets before extracting pure common rules. SearchInput and CompileRequest have different contracts. |
| Roadmap proof methods | Retain exported proof slices. Removal or relocation requires a compatibility and executable-proof preservation plan. |
| LockedStateFile migration | Characterize lock name, wait, ownership, backup and cursor-byte contracts. The existing plain-text watch cursor is not a JSON journal. |
| Recovery directory reapplication | Keep the documented pinned, per-part, stop-on-failure loop. A directory command requires a new partial-application/resume contract. |
| RankingSpec validator methods | Existing parsing already owns sealed identity. Forwarding field checks into methods would add navigation without another shared rule. |
| Operator CLI documentation from the table | A command/limit inventory is feasible; full request schemas require explicit metadata for custom readers. Do not expose operator mutations through the public API to obtain documentation. |
| Shared semantic-discovery slot | Keep distinct ranker/retriever contracts, coverage, projection and constructor capability gates. |
| Generic Python import helper | Retain distinct ordinary-fixture loading and validated, isolated bundle loading. Common syntax does not imply common provenance policy. |
| Bounded diagnostic vocabulary | Preserve separate JSONL, sanitization, locking and capped-snapshot contracts. Reconsider only when one real diagnostic rule changes across writers. |
| Python envelope client | Characterize output caps, timeout/error policy and returned shapes before consolidation. Bounding coordination output is a separate behavioral repair. |
| Typed attempt record | CAIRN-267 already owns ExecutionProfile, Attempt, AttemptResult and persisted row construction; preserve historical readers. |

## Evidence and limits

Lifecycle validation passed 315 focused tests and a nine-case old/new comparison
of rendered context, persisted state and refusal behavior. Weekly validation
ran 119 tests successfully, with one existing disposable-store test skipped without its
explicit test DSN/binary. The CLI conflict tests exercise actual subprocesses
against a synthetic Unix-socket API; HTTP checks exercise the real handler.
Local validation and final release details are recorded in the deployment report.
No live provider quality or token-saving claim follows from these checks.

Detailed selected evidence is retained locally under
`~/.local/share/cairn-reviews/followups-20261027/`,
`~/.local/share/cairn-reviews/core-followups-20261027/`, and
`~/.local/share/cairn-reviews/followups-683/`. These contain authored assessments
and verification records, not imported private sessions.
