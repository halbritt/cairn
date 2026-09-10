# Quoted memory search verification — 2026-09-10

Cairn now prefers eligible notes containing exact quoted query text before ordinary
lexical overlap or semantic score. This makes a known path, identifier or error
message useful through existing CLI, MCP and native OpenCode search fields.
The [contract](../quoted-search.md) specifies case-sensitive substring matching,
bounds, previews, fallback and compatibility.

## Evidence

The first disposable database test failed on the previous ranking: a newer note
containing the separate words “core,” “currentness” and “Go,” plus more query
words, ranked ahead of a note containing `core/currentness.go`. The new body and
index ranking put the exact quoted path first while retaining the lexical note.
A second failing test showed that the old preview could hide the path even when
the correct note ranked first. Previews now show the earliest literal occurrence
with a bounded source byte span.

The core checks cover mandatory instruction precedence; currentness, kind and
hosted destination exclusions; zero lexical terms in a quoted phrase; semantic
scoring and unavailable-backend fallback; ranked pages; duplicate and bounded
phrases; case-sensitive paths, error messages and digests; and a 256-byte UTF-8
literal with a truncated but valid preview. Editing a source invalidates its pull
handle while historical recompilation reproduces the original seal and preview.

The full disposable PostgreSQL integration suite passed with Go's race detector.
The final additional source/edit cases also passed with the race detector in a
fresh disposable cluster. `make check`, Go tests and all 40 Python tests passed.
The actual CLI/API preserves quoted query text without client database access,
returns the expected ordering, and pulls exact saved bytes. The independent
stdio client and real OpenCode custom tools perform the same capture, search,
preview and pull sequence. OpenCode uses fixture configuration; no answering
model was invoked for these checks.

The installed previous binary, clean `5946c5d`, generated body and index receipts
with quoted queries under lexical v4. The new binary recompiles those packages
exactly. A current retry whose package would now change refuses `STALE_PACKAGE`,
requiring a new request ID. Unquoted retrieval retains its existing profiles.
There is no database migration.

## Interpretation

This establishes an available search preference and its preservation boundaries.
The ordinary operational query for `core/currentness.go` already ranked the intended
note first before this change. The disposable collision case is therefore evidence
of a capability gap, not a claimed production task failure or measured downstream
benefit. No real-task advantage, general ranking-quality improvement or performance
gain is inferred from the tests.

E1 remains partial. Quoted substrings do not resolve entities, canonicalize paths,
inspect touched files, associate recorded failure assessments automatically, or
supply structured host intent. The existing note bodies support this step without
requiring new capture metadata. Further matching work should follow real retrieval
needs and the remaining accepted requirements.

## Decision provenance

The accepted design's section 15.4 separates eligibility from optional relevance.
Hard applicability pins were rejected for this purpose. Stored entity metadata is
deferred; this implementation uses existing query and body text with bounded work.
The source changes are a feature, with old receipt behavior explicitly preserved.

Pincite release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f` passed its retrieval
state gate. Final packet `pkt-0043519a7387403a` records typed requirements, source,
test and review evidence. The validated decision receipt and closed citation trace
are retained outside Git. Remaining obligations concern performance claims,
asynchronous workflow architecture and Go interface ownership; they are classified
as nonmaterial to this bounded feature conclusion, with reasons in the
[verification manifest](quoted-search-2026-09-10.json). Task value remains unproved
by this capability check.


## Local installation

Clean commit `7dcf3dc8815791aa6027ecf49dd0fa3e9dfaf5f4` is installed in the CLI,
API and ignored project binary. SHA-256:
`24eed8586c50067858f352b11d7a0458681b9e383cbdac48d9628a157ddb87da`.
The native OpenCode tool file was refreshed for its query description. Connection
settings, identities and semantic worker source retain their previous hashes.
API PID 1513779 serves the new build; PostgreSQL PID 163669 is unchanged and the
schema remains at migration 031.

The ordinary hosted profile searched the existing applicability-precedence note
with and without quotes. Quoted lexical ranking reports v5, unquoted ranking v4,
and actual local semantic scoring reports v2 with `discovery.state: ready`.
Receipt `73bec888-288c-49a2-b7d3-28295edff0ce` returned the existing note
`81751203-8768-49ee-bfce-895efb69889d` v1; its exact pull matched the recorded body
digest and preview span. The client had no database access. No operational note
was changed and no model task was started. This confirms installed behavior,
without adding a task-value claim.

[CI 34451418062](https://github.com/halbritt/cairn/actions/runs/34451418062)
completed successfully for installed `7dcf3dc`, including PostgreSQL/race tests,
40 Python tests, static/build checks and authenticated CLI/stdio workflows.
