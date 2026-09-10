# Task-phase applicability — 2026-09-09

Cairn can now distinguish phases within a task class: validation guidance can be
selected during validation and omitted during implementation. The host declares
the phase. This is a retrieval capability, with task benefit still unassessed.
[Contract and commands](../currentness-and-replay.md#declared-task-phases).

`task_phase` participates in matching, mandatory unknown-context refusal, overlap,
ordinary edit protection, derived-source containment and authorized scope changes.
The runner requires the same phase for retained execution. CLI search/run/start,
MCP and existing harness configuration routes forward `--task-phase`; native
OpenCode accepts `context.task_phase`.

## Verified behavior

The initial PostgreSQL test demonstrated that the previous type representation
could not distinguish validation guidance from implementation within a repair
task. A CLI forwarding test then failed on the unsupported flag. Later, a public
derivation test caught a missing containment comparison in the implementation;
the shared containment check now includes phase.

Final `make check`, `make test` and `make test-integration` passed. Tests cover
matching and missing phase, disjoint mandatory instructions, private mismatch
exclusion, unknown-context enforcement, phase-preserving derivation and refusal
of unauthorized edits/narrowing. Body, index, browse, kind-filtered and semantic
fallback packages recompile with their declared phase. Retained CLI runs refuse
changed or missing phase before binding and launch.

Real CLI/API/MCP checks exercise compact startup and generated configuration with
phase context. The scripted native OpenCode check passed with the updated adapter,
including phase forwarding through both lexical and semantic-fallback searches.
It used a disposable store and no answering-model calls. Final core containment
checks ran afterward; the adapter source was unchanged.

## Compatibility and deployment requirement

A separate disposable old/new binary comparison used the installed `c3256de` CLI
and the new test binary. Existing body (schema 3), index/browse (schema 8) and
filtered (schema 9) packages retry and recompile identically on the new reader.
An empty phase is omitted from JSON/CBOR. Nonempty request phases use schema 10.
The older binary refuses recompilation and retained execution of a new phase
package with `INTEGRITY_FAILURE`, before the marker process starts.

**All database readers must be upgraded before phase-pinned records are used.**
The compatibility fixture confirms that an older direct reader ignores the new
JSON field on a fresh compile; the new reader correctly reports missing context.
No migration is required, but an older reader is unsuitable for new constraints.
This is a limit of backward reading, not a claim that every old binary can safely
serve the upgraded store. API clients reach the upgraded server's applicability
checks. Reverting only a reader after new constraints are written is unsupported.

The [metadata](task-phase-2026-09-09.json) retains hashes for tests, native checks,
binary comparisons and the decision record. No operational memory bodies or
credentials are committed.

## Interpretation

The owner-authorized E1 requirement supplied the reason for adding phases. The
saved applicability lesson was read before implementation and checked against
current source; it informed the missing-context/mismatch tests. Direct source
review found the separate containment omission. Retrieval and tests establish
neither a causal memory contribution nor a net reduction in work.

Phases are declared labels, not observed lifecycle events or workflow controls.
They do not replace retrieval purpose, task class, scope or authority checks.
Existing handles retain their original declared context and normal freshness
rules; they do not automatically follow a changing task phase.
