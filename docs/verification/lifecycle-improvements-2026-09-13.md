# Lifecycle memory improvements — 2026-09-13

The owner authorized five ordered improvements: selective retrieval, separate
reusable notes, workstream continuity, unchanged-capture suppression, and OpenCode
lifecycle integration followed by a Codex/Agy hook assessment. Landing and host
deployment remain part of the task. This report records tested slices, not full
design acceptance or measured long-term usefulness.

## 1. Selective retrieval

Claude hooks now use task keywords, quoted paths/phrases and recent tool filename
or diagnostic hints. Startup searches filter for decisions/preferences so a newer
session note cannot consume the optional preview budget first. Optional previews
must meet a concrete lexical or file-association rule. Unchanged delivered bodies
are suppressed within the current context; resume/compaction restore delivery.
Mandatory context remains unaffected. Empty results inject no boilerplate.

Validation: 17 focused lifecycle tests; all 65 Python tests; `make check`;
installed Claude 2.1.270 against the real Cairn API and a disposable PostgreSQL
cluster with a scripted provider. The native fixture checks separate startup and
file-directed task delivery, hosted-only eligibility, native MCP pull, resume,
manual compaction and exit capture. Automatic compaction shares the handler but
was not forced by token exhaustion. Fixture artifacts: outside the checkout,
`/tmp/cairn-lifecycle-selective-3`.

These filters can miss a relevant note. The preview budget can omit candidates;
explicit search/pagination remain available. Deduplication means the body was
offered as hook context, not proof the model read or applied it.

## 2. Separate durable notes and checkpoints

Selection now returns up to three durable notes plus an optional unfinished-work
checkpoint. Complete relevant existing notes are supplied for updates. Validation
rejects unsupplied edit targets before writing; revisions preserve store metadata
and use CAS. Stable new topic titles and identical-body checks avoid exact duplicates.
This is bounded lexical discovery, not semantic deduplication across the collection.

Validation: 20 focused tests; real configured Claude model with three synthetic
cases (separate decision/checkpoint, lookup skip, exclusion skip) and no database
writes; native Claude fixture with disposable PostgreSQL revised an existing
lesson, retained its previous text, and created a separate session checkpoint.
Artifacts remain outside git: `/tmp/cairn-lifecycle-durable-1` and
`/tmp/cairn-durable-selection.log`. Partial multi-note saves are not transactional.
An unconfirmed write reports failure rather than claiming the batch was saved.

## 3. Workstream continuity

Named `Handoff: PROJECT / TOPIC` records replace new session-UUID checkpoint
titles. Relevant existing handoffs are read for selection; current session state
remembers a retrieved/saved title. Matching supplied workstreams revise the same
record. Unrelated task topics create separate records. Legacy session checkpoints
remain available for context; they are not bulk-rewritten.

Validation: 22 focused tests and the native fixture using a fresh Claude UUID
continued and revised the same workstream record, then the original session
compacted against its latest version. The real-model synthetic selection checks
passed with the new topic schema. Candidate pulls are bounded by attempts, and
queries remain below the API's 4096-byte limit without cutting quoted phrases.
Artifacts: `/tmp/cairn-lifecycle-workstream-3`. Cross-harness native continuation
will be checked with OpenCode in step 5; semantic matching and long-term usefulness
are not established by this fixture.

## 4. Skip unchanged capture

A successful selection, including an intentional null, records a digest of the
bounded dialogue and selector contract. Subsequent identical content requires no
lookup, selector call or save. Host compaction summaries, local command wrappers
and duplicate UUID records are excluded. Failed selection/writes remain retryable.

Validation: 24 focused tests include unchanged compaction/exit, new content, null
selection and failed-write retry. Native Claude added a meaningful progress turn,
saved it, then performed manual compaction and exit with zero further selector
requests and no revision. Artifacts: `/tmp/cairn-lifecycle-unchanged-1`.
This reduces calls for the tested unchanged boundary; it is not a latency benchmark.

## 5. OpenCode integration and Codex/Agy assessment

OpenCode 1.18.21 now has an optional lifecycle plugin using the same Python
engine. Task-request transforms retain bounded memory across tool continuations
without persisting injected messages or reaching title requests. Native idle
events queue capture; disposal waits for pending work. Compaction hooks save and
reset context, with the compactor itself excluded from retrieval. Tool file/error
hints and opt-out behavior use the shared engine.

The native fixture launches Claude, continues its named handoff in a fresh
OpenCode session, pulls the injected source through OpenCode's ordinary tool and
revises the same record at idle. It checks two task requests retain memory,
auxiliary requests omit it, and capture completes before process disposal. All
provider responses in this fixture are scripted; PostgreSQL and both harnesses
are real. `/tmp/cairn-lifecycle-cross-host-3` retains the standalone result.

The Node plugin check covers context retention/eviction metadata, child-session
exclusion, compactor exclusion, filtered dialogue, idle/disposal completion and
opt-out. OpenCode pre-compaction behavior is checked at the plugin boundary and
against pinned host source; the native fixture does not force OpenCode compaction.
The [Codex/Agy assessment](../lifecycle-hook-assessment.md) records installed
versions, supported routes and remaining native adapter verification. No Cairn
hooks were installed for those two harnesses.

## Combined limits

The implementation does not guarantee semantic deduplication, concurrent first
creation uniqueness, capture after abrupt kill, or accurate model selection.
Multi-note writes are individually confirmed, not one transaction. Optional
preview budgets and lexical filtering can miss relevant notes. The shared engine
is not a continuous transcript collector. Full design acceptance and sustained
benefit remain open; the owner has already exercised the real-work handoff journey.

## Final checks and deployment

All 74 Python tests, both Node plugin checks and `make check` pass. The plugin
also type-checks against the installed OpenCode SDK. `make test-integration` passes
with both native lifecycle binaries enabled, using its disposable PostgreSQL
cluster. Final Claude artifacts are under `/tmp/cairn-lifecycle-final-native`;
the combined integration log is `/tmp/cairn-lifecycle-final-integration.log`.

Both Claude profiles and the user-level OpenCode plugin were updated. Their
shared engine SHA-256 is `f555414f0964a96d8c4841ee063bd38193db5417c90e65c9796aae5d210d41a0`. The installed OpenCode plugin matches
source byte-for-byte. The installer preserves existing tool configuration and
unrelated host settings. Fresh host processes load the updates. No Cairn CLI/API
upgrade or database migration was required. The previously deployed 472-word
Cairn skill is unchanged.

## Engineering decision provenance

[Decision receipt](lifecycle-improvements-decision-2026-09-13.json):
`pkt-ec2306dd7a6036ea`, SHA-256 `ec2306dd7a6036ea6fefe95ff8278de1f24587309bb14b20dea648c0ef69f54c`,
`doctrine-f6bbb5196a3f8bf9`, `retriever-ec995ecdd083b2c8`. The validated
release is `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`. Owner authorization and
repository contracts govern; the packet supplied integration/test guidance.

The remaining packet obligations are nonmaterial to the bounded tested claim:

- Combined cancellation/shutdown/cleanup testing and retention/contention
  measurement: normal disposal and timeout cases are covered; abrupt-kill and
  throughput claims are explicitly excluded.
- Exception/cancellation behavior and nesting order: structured resource owners
  are inspected and ordinary failures are tested; no general cancellation audit
  is claimed.
- Immediate-failure classification and externally observed database tables:
  these adapters use the existing API, and tests use a disposable managed store;
  no store/transaction contract changes are proposed.
- Current cost/risk interval, intervention cost, latent-risk audit and a formal
  leave-alone procedure: the owner explicitly requested these behaviors; this
  work does not claim a benchmark result or a broad operational audit.
- Formal preservation matrix/procedure and a consolidated ADR/API/build-contract
  inventory: the allowed semantic changes are stated in the five slices, existing
  interfaces remain protected by regression checks, and no store contract changed.

Stop conditions were failed host-boundary checks, unknown mutation targets or
unconfirmed writes. Failures found during implementation were repaired before
deployment. Future native Codex/Agy integration requires its own verification.
