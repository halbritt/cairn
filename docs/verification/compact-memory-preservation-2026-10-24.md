# CAIRN-219 compact memory view: model-free preservation check

Date: 2026-10-24. The opt-in MCP `--search-view compact/1` and native
`recall_view: compact/1` were already on main. This check adds one native
presentation correction and extends frozen local tests. No installed setting,
provider route, production receipt, or database was changed.

## Preservation result

| Boundary | Full and compact evidence | Remaining limit |
| --- | --- | --- |
| Whole required and competing context | MCP search test compares both views to the same sealed source with two whole required selections and an advisory conflict group. The bound native compact test retains whole required and optional Unicode bodies, complete pull arguments, and a later candidate refusal. | Synthetic source content does not grade whether a model uses the qualification correctly. |
| Current source and privacy | Both MCP profiles run the same retrieval and pull handlers. Full and compact refusal tests cover scoped `AUTHORITY_DENIED`, redaction of a private transport detail, and `STALE_HANDLE`. | No installed compact provider request was observed. |
| Diagnostics | MCP compact retains explicit zero and future-zero omission reasons, nonzero reasons, and absence distinctly. The native compact view now does the same. Legacy full native output keeps its existing zero filtering. | Detailed scoring and omitted-candidate explanation still require the authenticated scoped `explain-page`/recompile route; no live retained receipt was inspected here. |
| UTF-8 and budgets | MCP whole-source fixture uses Japanese text and checks the final escaped tool envelope at its exact bound and one byte below. Both profiles refuse oversized Unicode output. A bound native compact fixture fits whole Unicode source, current required context, known-zero diagnostics and a later refusal under 9,500 serialized bytes, with one search and one pull. | The fixture does not measure provider context retention, token charges, or accepted task outcomes. |
| Compatibility and reads | Default MCP search remains byte-equivalent to the existing full projection; the compact response keeps receipt ID, source seal, versioned handles, paging and complete pull arguments. The native compact bound fixture needs no extra read for the whole source or its omission state. | A paired relevant-source task panel across ordinary and inbox paths remains required. |

## Native compact stale-handle follow-up

The native eager-candidate path now preserves the fixed `STALE_HANDLE` refusal
class in compact/1 as `candidate_inspection.refusals.stale_handle`. Before this
correction, the public hook surfaced the same response only as
`whole_pull_unavailable`, which erased the re-search cue. A local public-hook
fixture verifies a compact response with the original complete
`pull_arguments`, no candidate body, one pull attempt, and the exact stale-handle
refusal. The paired full/default run retains its previous
`whole_pull_unavailable` category. Neither view exposes the refusal's private
message or any source body. No retry is made against that same receipt-scoped
stale handle; callers must search for a fresh handle.

The focused Python tests (`test_optional_search_view` and
`test_agent_tool_recall`) pass: 23 tests, no skips. This closes the provider-free
native stale-handle presentation gap only; complete workflow acceptance and
provider-backed usefulness remain open.

The concrete correction was in the native opt-in renderer. It previously
removed an explicitly returned integer-zero omission count, making it
indistinguishable from an absent reason. `compact/1` now carries the original
omission map; unknown future reasons remain untouched. The full/default
renderer continues its established zero filtering. The MCP compact view
already retained the full omission map.

The added diagnostic context has a measurable size cost. In a local fixture
with many explicit zero counters and one whole required source, native full
output is 1,916 UTF-8 bytes and compact output is 1,985 bytes. The expanded
MCP conflict fixture is 2,458 bytes full and 2,321 compact. These are
serialized fixture sizes, not provider tokens or workflow savings. Correct
diagnostics take precedence over a blanket smaller-output claim.

`go test ./mcpapi ./cmd/cairn` and 79 relevant Python tests pass (five
pre-existing skips in the Python selection). Those tests include canonical
receipt inspection routes, public native hook output, source access/refusals,
and legacy view behavior. `make check` passes. The installed CLI/API revision
last inspected for this initiative is `37ad2df`, older than the source
capability, so this source check is not a deployment claim.

CAIRN-219 remains open. A frozen paired task panel must still compare actual
relevant-source contact, accepted outcomes, search/pull/pagination and
post-compaction reads, provider input/cache/output tokens, and effective
installed pins. The full canonical path remains available for rollback.
