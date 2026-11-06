# Hybrid runtime shared contracts

Campaign: CAIRN-301. Setup gate: X-01 / CAIRN-302. Revision: `shared/1`,
draft for cross-team acceptance on 2026-11-06. The overall lead owns this
index. Domain leads own their area documents and independent reviewers assess
the criteria against source before new implementation consumes them.

X-01 is **open**. Publishing a contract does not accept an implementation or
authorize a production cutover. Read-only discovery and characterization of
existing behavior can proceed. The overall lead sends an explicit X-01
completion notice after the closure checklist below is satisfied.

The [implementation plan](../hybrid-runtime-implementation.md) supplies the
full delivery sequence. Its tables remain authoritative for dependencies;
domain splits must retain those gates. Source and operational observations
have separate identities and timestamps. Installed version strings alone
cannot certify running clients or model coverage.

## Five contracts

| Contract | Accountable owner | Consumers | Required specification and fixtures |
| --- | --- | --- | --- |
| Lifecycle command inputs, streams and exits | Foundation, with Coordination contributing the full inbox and watcher command grammar | Coordination, Release, native harness adapters | Admission, recall/capture, native-session and ledger commands; required-context refusal versus optional degradation; input/output bounds, exact exit behavior; ordinary explicit retries and already issued live-session commands. |
| State schemas, locking and persistence order | Foundation for shared file primitives, ledgers and native-session state; Coordination for the sole journal transition algorithm | Both lifecycle teams and Release rollback | Existing schemas, modes, atomic replacement, lock scope, crash boundaries, write-before-send, stable request IDs, completion/reply references and pending-only automatic recovery. No duplicate journal transition algorithm. |
| Process identity and wake envelopes | Foundation for peer/process primitives; Coordination for wake adapters and supervision | Coordination, Release and supported harnesses | Ownership and stale-process rejection, bounded framing, active-attempt gates, busy/refusal/cancellation, process-tree shutdown and real process-boundary fixtures. |
| Semantic protocol and derivation identity | Semantic | Go supervisors, Release packaging and cutover | All three modes, original UTF-8 spans, tokenizer/pooling behavior, exact discrete score parity, vector tolerance, deterministic identity, fixed executables, offline packaging and database-wide writer exclusion. |
| Installation, pins, drain and rollback | Release | Foundation bindings, Coordination native state, Semantic package and installed clients | Immutable `/2` versus `/3` selections, actual drain, staged activation, unrelated-config preservation, races/crashes, supported live refresh, reverse lifecycle selection and semantic reverse rebuild. |

The area specifications are `foundation.md`, `coordination.md`, `semantic.md`
and `release.md` in this directory. Their exact accepted revisions and commits
will be recorded here after independent review. No draft revision is silently
promoted to accepted. A changed boundary must name every consumer, updated
fixture and review before dependent code lands.

## Shared-file allocation

| Surface | Sole writing assignment | Handoff |
| --- | --- | --- |
| `cmd/cairn/main.go`, `cmd/cairn/commands.go` initial lifecycle registration | Foundation F-B1 / F-01 | Coordination implements separate handlers and supplies later registration requirements. Overall grants a later shared-file slot after Foundation lands. |
| Shared state-file and process-peer primitives | Foundation F-B1 | Coordination supplies behavior requirements and consumes the same implementation. Extract only with exercised consumers. |
| Journal execution, recovery and inspection | Coordination J-B1 / J-02, J-03 | Native-session integration consumes named journal operations/facts, with no copied transition logic. |
| J-04 shared peer-client addition and first wake consumer | Foundation F-B1 owns the shared client; Coordination J-B2 owns separate consumer files | Name nonoverlapping files and one combined candidate before coding. F-R1 and J-R2 review independently; integrate the exercised pair together, without an unused helper-only trunk commit. |
| F-04 compatibility caller and original-input plumbing | Foundation F-B3 after the F-01 handoff | Narrow `inbox_binding.py`, `memory.py` and `coordination.py` changes require Coordination agreement before its overlapping frontend rewrites. Journal/watcher transition ownership remains with Coordination. |
| `scripts/install-semantic.sh` | Semantic S-B2 / S-04 | Release supplies host/architecture requirements and reviews staging/activation compatibility. A later writer requires explicit transfer. |
| `Makefile` initial Cargo and required real-worker targets | Semantic S-B2 | Other teams submit target requirements. After these targets land, Overall explicitly transfers ownership to Release for R-05 and final test wiring. |
| Service configuration, lifecycle installation and final activation | Release | Semantic supplies the binary/model/native-library manifest and fixed launchers. No production switch before R-06/R-07 gates. |
| This index, campaign dependency reconciliation and overall plan corrections | Overall | Domain leads edit only their assigned area contracts. |

Ownership covers concurrent writes, not unilateral interface changes. Each
builder still owns review fixes, local checks, queued integration, pushing main
and removal of their temporary branch/worktree. Contract-only commits can land
after their own review while X-01 remains open; they do not release dependent
runtime work.

## Decisions that all consumers must preserve

1. **The semantic fence covers the shared database queue.** `OpenIndex` and
   `Step` in `semantic/index.go` seed and claim `cairn.semantic_job` without a
   collection predicate. Publishing a derivation replaces that record's old
   document. Stop and join every writer sharing that queue before changing
   model generations. Include inactive but restartable alternate service
   definitions in the cutover inventory. Keep the current schema and test a
   reverse rebuild; neither uninterrupted dense coverage nor instant rollback
   is promised.
2. **Readiness and coverage are distinct.** Rebuild completion compares every
   eligible active current source's version and body hash with the selected
   derivation and passage rows. A ready query or empty queue is insufficient.
   Report partial coverage and observed fallback behavior accurately; do not
   infer per-record lexical fallback merely because the index is ready.
3. **A `/2` pin authenticates its Python bundle.** It cannot authenticate a Go
   executable. Preserve the legacy executor or explicitly drain and repin a
   reviewed `/3` descriptor. Retain immutable rollback artifacts and compatible
   state. New state versions require their own decision, tests and review.
4. **Foundation state support precedes memory consumers.** The
   [F-04 consumer decision](f04-consumer-decision.md) selects real binding
   activation through an independently pinned compatibility caller. F-04
   precedes F-02/F-03 and J-02 without waiting for J-05. Remaining grant
   operations land with their memory consumers; J-05 delivers native
   orchestration and its compiled journal adapter together. HC-03 includes
   full memory grant behavior; HC-04 accepts native-session transition policy.
   Domain contracts must agree on the exact adapter, failure order and
   compatibility receipt before dependent implementation.
5. **Explicit retries differ from automatic recovery.** Preserve ordinary
   unchanged explicit commands that retry through the authenticated API,
   including locally committed/refused slots. Ordinary watcher replay sends
   pending entries, with refused outcomes final for that recovery path. Preserve
   the existing `finish_reply` exception: when completion committed but its
   exact result reference is missing and no response exists, recovery retries
   that same completion to recover the reference before creating its reply.
   This is not permission to replay arbitrary settled commands. Existing
   live held/failed deliveries are never test material or replay mechanisms.
6. **The installed interface is the compatibility surface.** Preserve the full
   inbox grammar, issued commands and multi-binding watcher `--config-dir`.
   Semantic flags select one absolute executable without argv; fixed launchers
   select mode and pinned assets. Keep host-required adapters and frozen trials.

## X-01 closure checklist

- Current source/schema baseline and installation inventory are recorded,
  including managed hosts, supported operations, models, pins and live-client
  gaps. Observations identify their scope and time.
- Actual required models, efforts and subscription selectors are verified.
  Quota observations are fresh enough for allocation; percentages are not
  summed across overlapping windows or treated as reservations. Auth/quota
  failure stops the affected assignment without OpenRouter fallback.
- All five contracts have named owners, consumers, fixtures, exact revisions
  and independent Opus 5.5 Max criteria review. Blocking findings are resolved
  and changed criteria are re-reviewed.
- Foundation and Coordination agree on the callable journal/native-session
  boundary, without an F-04/J-05 cycle or duplicate journal state machine.
- Semantic and Release agree on package manifests, supported architectures,
  fixed launchers, the database-wide cutover fence and reverse rebuild.
- CAIRN-276 characterization is retained. CAIRN-277/278 are revised or
  explicitly superseded into the Go scope with evidence/history preserved;
  no duplicate Python rewrite is scheduled or falsely completed.
- Domain items have stable plan-key IDs and the campaign parent. Required
  dependencies are checked against the implementation tables. Native blocking
  edges are read back when available; otherwise explicitly unverified edges
  are enforced in the item bodies and campaign map.
- Shared-file allocations, initial six-builder cap and landing order are
  acknowledged. Independent existing-behavior work is separated from code
  that consumes pending contracts.

## Acceptance record

Release setup criteria `release/2` were independently accepted at candidate
`58a83eb91d1693b06a7c44c5d725bb3c318ea9a2` and landed as
`aa0c26fa1a8ab8faeb93c183c112f21dcbfa7ab9` with an identical contract blob.
R-02 qualifies available F-01/F-04 staging, pins and state controls. R-06
qualifies complete watcher/config-dir/shared-user transitions after native
lifecycle prerequisites; the original release dependency edges remain.
Unresolved delivery-free `reply_possible` drain disposition and admission/writer
visibility are explicit gates before dependent settlement/barrier code review
or R-06 qualification, not claims of completed implementation. Default
post-retirement rollback retains an executable, offline pinned `/2` Python and
Python semantic closure; retirement removes active requirements, not that
rollback material.

Semantic S-04 also requires R-01's nonempty supported host/CPU inventory.
The separately classified S-04R / CAIRN-355 repair qualifies a telemetry-disabled
retained Python semantic launcher/rollback closure. It depends on X-01, S-01
and R-01, and gates S-05 rollback acceptance and R-06 qualification. Semantic
owns its implementation and S-R1 review; Release independently reviews rollback
compatibility. Keep the frozen numerical oracle, installed venv/model cache and
current production selection unchanged during characterization. Prefer a new
immutable launcher selection; if source changes alter derivation identity,
record that transition explicitly. Prove the selected closure's numerical
behavior and absence of telemetry-store writes or connection attempts through
isolated process observation. Rust-only evidence does not qualify Python.

The exact 65536-punctuation reference case retains its observed 90-second
supervised timeout. A separately labelled offline numerical-oracle capture of
the identical frozen input may use one external deadline of at most 180 seconds
to obtain missing reference vectors, with independent reviewer agreement.
This does not change or pass the production deadline. Protect the owner's
telemetry store and network through actual isolation, including OS home lookup;
missing isolation stops the experiment. Another timeout remains a model-evidence
gap, without repeated deadline inflation. Candidate numerical comparison and
execution through the unchanged real supervisor remain separate requirements.

Other domain reviews and the F-04 decision's delta agreements remain pending.
X-01 / CAIRN-302 remains open.
No runtime implementation, production switch, burn-in or retirement has been
accepted by this record.
