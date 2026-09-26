# Explicit applicability in ordinary capture — 2026-09-09

Ordinary agents can save applicability restrictions through MCP, native OpenCode
and `remember --pins` JSON. Capture previously omitted these restrictions even
though raw Create and retrieval supported them. Unpinned capture remains the default;
search context is never copied automatically. See the [usage contract](../currentness-and-replay.md#saving-guidance-with-explicit-applicability).

## Evidence

- CLI parsing first failed on the unknown flag. MCP stdio first rejected the
  additional `pins` property. Actual OpenCode tool execution first rejected it
  through the native argument schema. All three reproductions are retained.
- Public MCP capture stores every context dimension, retrieves with matching
  context, withholds for each missing/mismatched dimension, and preserves pins
  through body revision. Changed pins conflict under the same capture UUID.
- Native OpenCode 1.18.21 saves a phase-constrained note and pulls it from fresh
  sessions. Missing and mismatched phase withhold it. Default capture stays
  unpinned despite configured startup phase. Invalid fields/types, retry conflicts,
  stale handles, revisions and existing permission/destination checks pass.
- `make check`, `make test` (Go plus 34 Python tests), and full disposable
  PostgreSQL/race integration passed. Native tool checks ran as part of integration.
  A final CLI assertion also verifies forwarding to the authenticated Create route.

This forwards existing applicability to the existing store. No core, migration,
semantic-schema or API-server change is required. The earlier phase-reader upgrade
requirement remains: every direct database reader must support phase before such
records are written. Ordinary edits cannot alter a note's pins.

These checks establish usable capture behavior. They include no answering-model
inference, accepted external task, or incremental memory-value assessment.

## Decision provenance

Repository instructions and the owner's active goal govern this bounded change.
The validated Pincite release supplied guidance on repository precedence, test-first
feedback, preserving defaults, and a minimal coherent API. The selected alternative
is an optional explicit object; automatic context inheritance was rejected because
it would constrain general guidance without selected intent. Raw Create remains
available but did not close ordinary native-tool capture.

Initial packet `pkt-5193bbf4c558a103`; typed-evidence packet
`pkt-313fca60e546c77f`, content SHA-256
`313fca60e546c77fc0987fd74f0bc1fa1d069aba8f60206481fd23cc2d213fc6`.
One evidence pass left six nonmaterial generic interface/recurrence/cost obligations.
No interface abstraction, performance or net-task-value claim depends on them.
The decision receipt passed schema validation and both citation observations closed.
The [metadata](capture-pins-2026-09-09.json) retains local evidence paths and hashes;
operational notes and generated binaries remain outside Git.

## Local installation

Installed clean CLI `215ecbf` and the matching bundled native adapter. Connection
settings retain their prior byte hashes. API `8f6864a`/PID 430775 already supports
the stored fields and remains active alongside PostgreSQL PID 163669. Existing
MCP processes need a fresh session to load the extended tool schema.

The existing ordinary capture procedure was revised from v1 to v2, retried
identically and freshly pulled through the installed CLI and unchanged API.
It remains unpinned; the prior version is retained. This updates reusable guidance
without creating a duplicate note or claiming that maintenance proves task value.
Deployment and procedure hashes are retained in the companion metadata.

[Implementation CI](https://github.com/halbritt/cairn/actions/runs/34425510304)
passed for exact source `215ecbf1e893afc530c31b9f773b243d851ccac3`.

## Correction: explicitly empty CLI pins

The initial CLI implementation distinguished omission by testing whether the
string value was nonempty. Consequently `--pins ''` and `--pins=` were accepted
as absent pins. Public client tests reproduced both forms reaching the Create API;
the parser returned a draft with no applicability instead of refusing invalid JSON.

The shared operator/agent parser now checks whether the flag was supplied and
parses every supplied value. Both empty forms return `INVALID_REQUEST` before
capture. Omitted pins and valid JSON retain their prior behavior. CLI help now
lists `--pins JSON`. The stored capture procedure was read before the repair and
checked against the existing JSON-object contract; no new applicability rule was
introduced.

Targeted red/green tests, `make check`, Go/40 Python tests and a final CLI package
run pass. The store and API are unchanged; this repair exercises the parsing and
pre-request boundary. It adds no task-value assessment or ordinary note revision.
Evidence is retained under `empty_pins_repair` in the companion metadata.

Installed clean CLI `397750b7ba886fc67dfb2159f8b2976e484bbc6b`, SHA-256
`bf680a75cf5165fc76ca6b7c842a564b3df24b5b5c7e7684e1065a2952fdb7bc`.
Both empty forms return exit 2/`INVALID_REQUEST` using a synthetic owner-only token
and an absent isolated socket. The fixture token was removed afterward. API
`8f6864a`/PID430775 and the `d633c68` semantic worker remain unchanged.
[Repair CI](https://github.com/halbritt/cairn/actions/runs/34427884310) passed for exact source `397750b`.
