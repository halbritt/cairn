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
