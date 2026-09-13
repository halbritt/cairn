# Design evaluation and first implementation

Reviewed 2026-09-07 UTC against the operator-supplied design version 1.0.

## Verdict

Accept the architecture as the direction for Cairn and start with an isolated,
testable core. It is sufficiently concrete to build incrementally. It does not
yet specify everything needed to deploy authority-bearing memory or run the
first real OpenCode task.

The user authorized evaluation, starting implementation when acceptable, naming
the project Cairn, placing it in `/home/halbritt/git/cairn`, and creating a Git
remote. This resolves the immediate implementation/repository portion of O1.
The document's statement that its drafting request did not authorize building
does not override that later user request.

## What holds up

1. **Proportional storage.** Relational current state and selected history fit
   the trusted local environment. Separate authority audit avoids making every
   note pay the cost of a global event stream (§§2, 5, 17).
2. **Truth, identity, and attribution are separate.** A known writer can still
   make a false claim. Attempt-specific service receipts catch phantom
   completion without pretending that authentication verifies facts (§6 and
   the operator's attribution correction).
3. **The consequential-use boundary is explicit.** Promotion, supporting
   evidence, live authority, and retained use references belong at the known
   consumer boundary. The H0 limitation is stated honestly (§§7–9, 12–13).
4. **The compiler has enforceable semantics.** Mandatory structured selection,
   whole conflict groups, destination checks, and a separate semantic seal
   keep relevance ranking from creating authority (§11).
5. **Usefulness precedes grooming.** The use/outcome join, replay, and demand
   docket give later model work something measurable to improve (§15).

## Issues that change implementation sequencing

| Finding | Consequence | Resolution in this build |
|---|---|---|
| O4 leaves the authenticated channel and root bootstrap unspecified | A principal string or CLI flag cannot be the production authentication mechanism | Trusted library channel only; local CLI identifies its OS UID and accepts testimony only. No root grant or authority path. |
| §8.3 describes a broad scope algebra without executable kind schemas | Missing bindings and wildcards could silently widen applicability | Initial A records require exact repository, task, and run. Edits cannot change scope. General scope predicates wait for compiler fixtures. |
| O3 leaves destination/capture and unenforceable-policy tables unratified | An H0 wrapper could leak context or promise unsupported enforcement | No production destination delivery or harness launch in this slice. |
| O6 conflicts with the blanket refusal to redact open conflicts | Secret removal and conflict preservation need an explicit precedence rule | No deletion interface until that dependent rule is resolved. |
| §9.2's one-surviving-reference rule establishes availability, not sufficient support | A surviving unrelated artifact could leave a multi-premise claim unjustified | Preserve the accepted default; document O7 before domain-specific automation. Do not infer sufficiency. |
| §7.1 warns against immutable secret payloads while reasons must explain decisions | Audit reasons and locators can themselves need redaction | Keep redaction-sensitive details out of the future immutable audit schema; settle before authority migrations. |
| The full Stage 1 list is much larger than the first useful experiment | A broad first commit could contain many unverified contracts | Deliver a complete ordinary-record/attribution slice; do not label it Stage 1 completion. |

These are refinements and dependent production gates, not a reason to discard
the design. No extra consensus, signatures, per-agent DB roles, or model on the
correctness path is justified by the stated environment.

## Selected implementation decisions

- **Go and PostgreSQL.** Striatum's existing OpenCode backend is Go, so Cairn can
  be embedded without requiring a new cross-machine service. The installed pgx
  dependency requires Go 1.25; this module declares that minimum. The current
  code uses PostgreSQL 16+ features and was exercised on PostgreSQL 17.
- **One core, one CLI.** Shared validation and transactions live in `core`.
  There is no repository interface, queue framework, cache, or harness adapter
  before a concrete use requires it.
- **Request idempotency.** The key is caller, operation, and canonical UUID.
  The canonical typed JSON digest detects changed intent. An advisory lock on
  that key protects lookup, effects, and the durable response in one transaction.
  Hash collisions only serialize unrelated operations; the full key still
  determines identity. No expiry exists in this development slice.
- **Lock order.** Request key, then attempt key when applicable, then record
  row. Terminal updates take attempt lock then attempt row. Migration has its
  own installer lock. Missing-attempt insertion and claim ingestion share the
  same attempt lock, so reconciliation cannot miss their concurrent arrival.
- **Attribution is a projection.** A SQL view derives the state from retained
  versions and the exact service attempt; changing terminal state cannot leave
  a stale copied flag on a record. Contradiction docket rows are retained.
  A successful completion additionally requires a matching nonempty result
  reference. References are correspondence identifiers, not verified evidence.
- **Failure recovery is eager.** Every failed service attempt produces one
  instrumented A observation in the terminal transaction. This preserves the
  failure even when the dispatcher never submits a memory claim. It confers no
  capability conclusion or Class B eligibility.

Alternatives: waiting for every operator parameter would delay reversible
learning; implementing all classes now would encode unresolved authority and
retention policy. A file/SQLite prototype contradicts the selected PostgreSQL
contract. The selected slice exercises the real concurrency substrate while
keeping those dependent decisions open.

## Verification and limits

`make test-integration` uses an isolated PostgreSQL cluster and Go's race
detector. It checks retry identity, intentional resubmission, concurrent edits,
transaction rollback, deferred version references, store stamping, pooled
identity cleanup, absent/failed/completed attempts, exact result/run/delegate/
dispatcher matching, partial output, and failure recovery. The script also runs
the migration and create CLI against that database. `make check` runs `go vet`
and checks Go formatting.

This does not establish complete Stage 1 conformance, crash/PITR recovery,
production access control, authority correctness, compiler behavior, adapter
contact, latency, or memory usefulness. Ordinary revisions and retry responses
currently accumulate; automatic retention is deliberately not enabled before
its dependent policy and reference guards exist.

## Inventory and next steps

The target directory was empty and had no applicable ancestor `AGENTS.md`.
The authenticated GitHub account is `halbritt`. Source artifacts are pinned by
the [manifest](sources/manifest.json); only the supplied design and its three
explicit synthesis/correction sources were copied, not private sessions.

PostgreSQL 17 binaries are installed. A host PostgreSQL service answered a
read-only readiness probe; it was not used for testing or migrated. OpenCode
was not found on this session's PATH or the two checked user binary paths.
That does not prove absence elsewhere. The Striatum source has
`cmd/striatum-backend-opencode/main.go`; `internal/backend/llm/supervisor.go`
passes rendered prompts through configured stdin or argv before
`supervise.StartConfined` and observes process termination. This is a promising
integration point, not proof of installed harness contact or a confirmed
delegation-event contract. No live tasks were started.

1. Finish Stage 0: locate and probe the configured OpenCode binary/input route;
   inventory the actual authenticated host and spawn/terminal APIs; select an
   authorized real-history baseline corpus and capture policy.
2. Resolve the identity/root bootstrap portion of O4 and implement manual B/C
   authority with live grants, independent-author checks, exact audit/state
   matching, bounded serializable retries, and race/crash tests.
3. Add evidence verification and impact/use references, corrections, scope
   predicates, deterministic conflict groups, deletion effects, and open-task
   delegation findings. Complete §18.1 before claiming Stage 1.
4. Implement the canonical compiler and verified OpenCode H0 wrapper under
   resolved O3/O5 policies, then use/outcome joins and historical replay.
5. Add broader adapters and grooming only in the design's dependency order.

Revisit these decisions if the real orchestrator cannot supply an independently
established channel, if the exact-attempt contract differs, or if measured H0
benefit fails to justify context and review cost. Keep all development data
disposable until production retention and recovery are accepted.

## Review method

The design and current workspace observations govern the recommendation.
Pincite's validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`
(corpus `corpus-2026-07-12-a11702cc9217`, doctrine
`doctrine-f6bbb5196a3f8bf9`) supplied a bounded architecture review packet.
Its explicit-invariants, request-idempotence, and authority-bounded-action
concepts were used to frame the review. Full architecture, replication,
compatibility, preservation, and performance evidence obligations cannot be
discharged by a new empty repository; they are nonmaterial to starting this
isolated slice and remain required before corresponding production claims.
The [decision receipt](review-receipt.json) records the exact evidence scope,
packet identity, remaining obligations, and limits.
