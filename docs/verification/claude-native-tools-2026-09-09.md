# Native Claude memory tools — 2026-09-09

Claude Code 2.1.265 executed Cairn's five ordinary tools through the generated
MCP configuration in two distinct native sessions. Exact note capture, body-only
correction, fresh retrieval, supporting-evidence passage and retry responses
passed. A disallowed edit had no stored effect; stale handles and versions were
refused, and local notes stayed outside hosted search.

This verifies native execution beyond the earlier connection check. The native
client ran `--bare --print` with scripted loopback Anthropic messages, isolated
HOME/configuration, the generated MCP server, no built-in tools and explicit
Cairn permissions. No model inference occurred. These results establish neither
model-selected use nor a useful Claude task, and they do not provide automatic
per-call session attribution. U8 remains partial.

## Checked behavior

The fixture advances from actual tool results and checks the resulting state;
it does not grade prompt wording, final assistant text or backend request count.
Its HTTP request size/count and process lifetime remain bounded.

- All five tool schemas are available to the first native session. Capture
  returns an ordinary shareable note; search/pull returns its exact body.
- A body edit returns version 2. A fresh search/pull returns the corrected body.
  Original capture and edit retries return their exact original identifiers.
- Cached-handle reuse after editing returns `STALE_HANDLE`; a new edit request
  with version 1 returns `VERSION_CONFLICT`.
- A supporting-evidence pull returns the exact selected passage, `supporting`,
  through the fifth tool using its indexed handle and full-source digest.
- Default-local capture is excluded from hosted search. Independent operator
  reads confirm its stored sensitivity and body.
- A second native process retrieves the corrected note under a different
  explicitly supplied run scope. Native init events have distinct session UUIDs.
  Its search/pull-only policy denies an edit; an independent store read confirms
  the body and version stayed unchanged.

## Argument normalization finding

The first 16-case run passed. A second run added an expectation that a model-side
`shareable: "false"` string must be refused. That expectation failed: Claude
created a note. The failed run is retained; it was not a demonstrated server bug.

The final 18-case check compares the actual boundaries. Direct MCP rejects the
same string against the boolean schema. Through Claude, textual `"false"` is
accepted and the note is stored as local, excluded from hosted search. An
unrecognized string, `"not-a-boolean"`, reaches a schema refusal. The subsequent native conversation sent back to the scripted endpoint contains
the boolean `false` in that tool call. This locates an observed adjustment in
Claude; the check does not capture the exact MCP wire transformation or depend
on its internal mechanism. Callers should
supply real booleans rather than rely on coercion. No production validation change
was warranted by the observed behavior.

## Verification and retained evidence

The completed optional Claude integration passed the full disposable PostgreSQL
suite with race checks and the existing CLI/API/MCP checks. Go tests, 30 Python
tests and vet/format checks also passed. The final native check completed 18
scripted cases across two sessions. These are fixture coverage counts, not a
measure of memory value. Operational stores and owner harness settings were not
used as test fixtures. No binary installation, API restart or schema change is
needed for these test/documentation changes.

[Metadata](claude-native-tools-2026-09-09.json) retains the original, failed and
completed runs, native init evidence, exact result artifacts and source hashes.
Raw requests/results remain outside Git. The driver follows the
[Anthropic streaming protocol](https://platform.claude.com/docs/en/build-with-claude/streaming);
only its scripted responses replace inference. Interactive trust dialogs,
other Claude modes and exhaustive Unicode/binary conformance are outside this
check. Existing API/MCP coverage remains separate.

The doctrine review used `pkt-e69d4b75b4b4bde0`, one typed-evidence pass and a
schema-valid decision receipt with a closed citation trace. Six nonmaterial
obligations retain the unclaimed annotation/checker, character-set and task-cost
work and separate refactoring procedures. Test-guard review kept independent
state checks and removed an initial backend-call-count assertion before execution.
