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
