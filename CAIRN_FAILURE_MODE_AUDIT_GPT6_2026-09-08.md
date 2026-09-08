# Cairn restore and deletion failure audit

## 0. Audit basis

Target: `/home/halbritt/git/cairn`, initially clean main at
`4eb9d7dcf7de12a80964a7e96b8facabc99aca16`. Scope is restore reconciliation and
adjacent deletion dependencies. This is a bounded failure audit, not full design
acceptance. The user delegated Cairn roadmap implementation and routine decisions.
The audit itself traced source. Separate implementation work subsequently added
regression probes in disposable PostgreSQL; no operational corruption or restore
was performed. The full roadmap objective remains unchanged. This report describes the baseline; subsequent FMA-001 repair and verification are recorded in [the implementation result](docs/verification/deletion-dependencies-2026-09-08.md). New authorized withdrawal reapplication and imported custody for FMA-002 are now covered by [the recovery implementation](docs/verification/recovery-reapplication-2026-09-08.md). It preserves missing original history rather than reconstructing events from hashes. FMA-003 restore admission remains open.

Sources: `core/recovery.go`, `core/checkpoint.go`, `core/deletion.go`,
`core/relations.go`, `core/retraction_preview.go`, `core/compiler.go`,
`core/store.go`, `core/restore_fence.go`, `core/authority.go`,
`core/transitions.go`, `cmd/cairn/recovery.go`, `cmd/cairn/commands.go`,
`localapi/server.go`, schemas 013/016/018, recovery/deletion tests,
`scripts/check-recovery.py`, and the recovery/restore contract documents.
Accepted design §16.2 requires isolated restoration, verification, newer
withdrawal reapplication, projection rebuilding, delivery fencing and explicit
resume only after those checks. These are distinct steps.

| Boundary | Depth | Reason and evidence | Residual risk |
| --- | --- | --- | --- |
| PostgreSQL records, relations, deletion exclusions | deep-trace | Determines which retained content can enter compilation; source and existing tests read | Concurrent insertion and partial projection loss need targeted execution |
| External recovery export and audit comparison | deep-trace | Only separate expectation can identify later withdrawals; core/CLI/file code read | Export freshness and lost event reconstruction remain unavailable |
| Restored delivery and local API startup | deep-trace | Governs whether revived state can be served; fence, open and API paths read | Process isolation is still operational responsibility |
| File publication and context custody | survey | Recovery export uses exclusive creation and fsync; context descriptors are compared | Physical paths are not inspected by recovery-inspect |
| Locks, jobs and purge effects | survey | Deletion outbox state and effect handlers read | Full worker crash surface was not re-audited |
| Subprocesses and local IPC | survey | Existing runner/API lifecycle checks identified | No new model or live runner experiment |
| Idempotency and cached payloads | survey | Mutation transactions and exclusion selection read | Not a full enumeration of future response shapes |
| Migrations and backup transitions | survey | Checksummed migration and dump/catalog procedures inspected | No migration introduced by this audit |
| Evidence providers, remote transport and scheduling | unread | Outside selected restore/deletion boundary | No general provider availability or scheduler conclusion |

Verification route identified before remediation: focused recovery/deletion
PostgreSQL tests; `make test-integration check`; `make test-lifecycle` for actual
dump/restore and effect custody. Existing lifecycle code is source evidence,
not a claim that those commands ran during this audit. The added focused restore
regression ran in an owned temporary cluster and failed against the initial
implementation: a removed transitive exclusion still produced `consistent:true`.
Retained log: `/tmp/cairn-recovery-dependencies-red.log`. Other new regression
results and repairs belong to the subsequent verification record.

## 1. Verdict

**HIGH_RISK_FAILURES**, medium confidence. One blocker and two serious boundaries remain in the
inspected baseline. Cairn detects several externally specified withdrawals and
can fence old delivery capabilities, but detection is not reapplication or
admission. In addition, deletion-dependent eligibility is a materialized
projection that recovery inspection does not verify. Source tracing identifies
an ordinary relation-creation path that can miss the same restriction. This is
not evidence of an operational incident or a hostile-agent attack.

## 2. Failure boundary inventory

The operational store is PostgreSQL. Go core methods own transactions; the local
operator CLI supplies the OS principal, and the Unix API establishes scoped
agent/observer channels. Record versions and typed relations remain retained;
`deletion_dependency` marks versions that must not be selected after a source
is forgotten. The compiler consumes those rows directly.

Forgetting advances the source to a tombstone, emits its authority event,
enqueues known effects and fills dependency exclusions in one privileged
transaction. Export captures audit metadata hashes, withdrawal identities and
selected file custody under one snapshot. The export is independently retained;
inspection is read-only. A restore fence advances delivery generation but does
not establish that the restored content or authority is current.

## 3. Ranked failure-mode ledger

### FMA-001 — BLOCKER: deletion-dependent eligibility can lose its restriction

Subsystem/trigger: relation projection; later writes or incomplete restore.
Invariant: a version that depends on forgotten support must retain the compiler's
exclusion, including through explicit transitive links. Failure points:
`Forget` populates `deletion_dependency` using the then-retained
`dependentVersions`; `linkRelations` checks direct target lifecycle but does not
inherit target dependency exclusions; compiler candidate evaluation consults
only the materialized exclusion. `inspectWithdrawal` checks direct payload
exclusions but does not reconstruct the expected dependency set.

Before forgetting, source and dependents are active. Forgetting flags known
dependents, leaving their bodies/history available for review. Afterward, a new
version can link to a retained dependent whose own lifecycle remains active.
That creation path inserts the relation without a matching dependency exclusion.
Separately, removing an existing transitive exclusion leaves audit and source
payload exclusions intact, and baseline inspection returns consistent.

Blast radius: dependent content may regain compilation eligibility despite
forgotten upstream support. Detectability: not reported by baseline recovery
inspection; source tracing and the isolated projection-loss test expose the gap.
Recovery: no dedicated projection rebuild path exists. Repeating Forget is not
a repair operation because the record is already tombstoned. Evidence:
static-traced plus executed isolated inspection and ordinary-write regressions. The latter selected the new descendant through the public Compile API; see `/tmp/cairn-recovery-dependencies-live-reproduction.log`. Smallest next step:
verify the ordinary creation path and establish ordering/propagation and bounded
projection checks without converting inspection into a mutation.

### FMA-002 — SERIOUS: external recovery evidence is insufficient for event replay

Subsystem/trigger: audit and withdrawal recovery after restoring an older dump.
Invariant: newer revocations/deletions must be reapplied with auditable provenance
before service resumes. `CaptureRecovery` exports audit event UUIDs and metadata
digests, plus withdrawal kind/subject/repository/event UUID. It does not export
the missing authority events' original metadata or the full state transitions.
`InspectRecovery` reports missing audit and revived restrictions. There is no
`recovery-apply` command or core mutation.

An internally valid old backup can therefore lack newer withdrawal events and
state. Its old checkpoint can still match that old set; the later external
record detects the difference. Ordinary revoke/retract/forget operations can
create new authorized events for some still-existing subjects, but cannot
reconstruct the missing original audit bytes from a digest. Replaying them also
does not recover post-backup receipts and file custody automatically.

Blast radius: incomplete restore recovery, or revived authority/content if an
operator treats a manual state change as complete reconciliation. Detection is
loud when the trusted newer export is supplied; absence of a newer export cannot
be inferred. Recovery remains an explicitly isolated operator procedure with
unfinished reapplication. Evidence: static-traced, and existing
`scripts/check-recovery.py` is source evidence for the older-dump scenario.
Smallest next step: define retained recovery action provenance separately from
lost original history; do not invent original actors, timestamps or transaction
identities, and do not erase missing-history findings merely because a new
restrictive action is committed.

### FMA-003 — SERIOUS: delivery fencing is not a restore admission gate

Subsystem/trigger: service startup against a restored database.
Invariant: affected service remains paused until freshness, restrictions,
projections and outstanding effects have been reconciled. `FenceRestore`
advances delivery generation and expires index sessions. `core.Open` establishes
a pool and validates channel fields; API construction opens stores for configured
identities. Neither path consumes a recovery inspection/admission decision.

Old receipts are refused after an explicit fence, but fresh compilation can use
whatever state the restored database presently considers eligible. Starting an
API does not itself establish that the database was restored or that a newer
external expectation exists. An old database and its matching old checkpoint
cannot reveal commits absent from both.

Blast radius: fresh delivery of revived content/authority when operational
isolation is skipped. Detectability is external-procedure dependent; fence
success is not the relevant complete signal. Recovery: documented isolation and
fencing exist, but automated admission and fresher withdrawal reapplication do
not. Evidence: static-traced. Smallest next step: specify an operator-owned
restore session and admission boundary that covers direct core consumers as
well as the local API; retain unknown freshness as unknown.

## 4. Recovery and idempotency

Mutation UUIDs distinguish transport retry from new intent. Privileged
operations use bounded serializable retries. Recovery export refuses to replace
an existing file, so recreating an export from an old database cannot silently
overwrite the operator's retained expectation at that path. Inspection has no
mutation retry behavior because it is read-only. Fencing has its own idempotent
request key; retry does not fence newly compiled work a second time.

A replay of a revoked grant is not equivalent to reconstructing its missing
historical event. Likewise, a new tombstone is not evidence that a post-backup
context file was found and purged. Any later reconciliation implementation must
preserve those distinctions and report remaining effects/history separately.

## 5. Concurrency and partial writes

Forgetting is serializable, while ordinary Create/Edit use read-committed
mutation transactions. Relation insertion locks direct target records and
advances target use generations. A prospective fix must order descendant
creation against forgetting of an ancestor, including the case where the
relation is added while forgetting is computing its closure. A check of the
current exclusion table alone is insufficient if it can race an uncommitted
forget. This needs an executable interleaving, not a timing assumption.

Recovery export syncs the created file and parent directory and refuses
symlinks/existing leaf paths. An interrupted failed export can require a new
path; checksum and strict decoding detect incomplete content. That loud failure
is not ranked as a silent success defect here.

## 6. What fails loudly or safely

Preserve checksum validation, external-root matching, unscoped operator access,
bounded export sizes and retained snapshot semantics. Inspection already checks
revoked mutable state even if its audit event remains, forgotten version/package/
mutation-response exclusions, and exported context descriptors. It returns
structured gaps rather than silently modifying data. Pending effects and
irreducible residuals are separate from logical consistency.

Fencing preserves historical semantic seals and late observations while refusing
old delivery capabilities. Its documentation explicitly denies cancellation,
complete recovery and admission claims.

## 7. Verification limits

No live fault injection, operational restore, deletion, service restart or model
run was performed for this audit. Further reproduction and repairs use the
user-authorized disposable test workflow. Full integration/lifecycle results
must be recorded after those changes; the audit does not substitute static
tracing for those results. No additional user approval is needed for the already
delegated local implementation and disposable verification work.

## 8. Residual risk and unread areas

This report does not verify all state/audit relations, evidence object recovery,
backup retention or physical erasure. Recovery export freshness and trusted
custody remain operational inputs. A cryptographic digest authenticates content
against an expectation, not the authority of a self-recomputed expectation.

Rejected candidates: no claim of forged production admission, live forgotten
payload exposure, or missing operational backup is made; those were not
observed. No broad timing or performance conclusion is made. Audit findings
identify the next concrete work; full L9 remains open.
