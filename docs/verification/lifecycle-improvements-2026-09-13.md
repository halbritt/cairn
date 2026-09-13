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
