# Native OpenCode argument integrity, 2026-09-10

A source review of per-call search context found that a malformed JavaScript
string could change before reaching Cairn's JSON validation. The native adapter
passes queries and context through process arguments. In an actual OpenCode
1.18.21 debug-tool invocation against a disposable API, `binding_id: "\ud800"`
returned a successful receipt with `binding_id: "�"`. The supplied declaration
and the retained declaration differed.

The shared adapter call now refuses lone UTF-16 surrogates in the executable or
process arguments before launching the CLI. The error is `INVALID_REQUEST: CLI
arguments require well-formed Unicode`; it does not echo argument values.
This covers supplied queries, context labels and configured argument values.
JSON stdin keeps its existing validation. No database, protocol, scope, sharing,
permission, context-merge or ranking change is required.

## Verification

The regression first failed because the malformed label succeeded. After the
repair, the full existing native API check passed. Additional native checks
refuse high and low lone surrogates, a surrogate followed by ordinary text,
reversed surrogate order, malformed queries and a malformed configured binding.
A valid search then succeeds with the refused request UUID. Japanese text,
emoji, an intentional replacement character and a literal backslash escape
retain their exact context value and query digest.

A normal OpenCode session with scripted loopback completions separately refuses
the malformed declaration and accepts corrected text with the same request UUID.
This exercises the normal tool route without answering-model inference. The
source checks are in [check_opencode_unicode.py](../../scripts/check_opencode_unicode.py),
called by the existing disposable native API suite. Production fixture notes
were not created.

## Memory contribution and limits

Before inspecting the implementation, this coding session searched Cairn for
`OpenCode argument validation` and pulled lesson
`104519b5-82cd-44b3-ba73-1def2ad42e8c` version 2. It explained the prior runtime
validation defect and the need to check actual execution beyond model-facing
schemas. That guidance informed the boundary review and the normal-session
check. Current code and the earlier JSON-integrity report supplied additional
evidence; the recalled lesson did not describe this Unicode defect.

This is a verified repair during ordinary memory-guided work. There is no
counterfactual showing the defect would otherwise have been missed, independent
model replication, measured time saving or established net memory benefit.
The earlier [JSON integrity report](json-unicode-integrity-2026-09-09.md) explicitly
left upstream replacement outside its claim. This repair closes one observed
native argument path; it does not establish integrity for every upstream SDK or
serializer, or identify previously altered requests.

[Metadata](opencode-unicode-2026-09-10.json) retains the check and decision pointers.

## Installation and retained guidance

Installed clean `6c48411991b55cd766fcb2c738b4750445c04b98` in the CLI/API and
updated the native adapter. CLI and API report the same build. PostgreSQL remains
at schema 033; its process and protected configuration hashes are unchanged.
An isolated native invocation using the installed adapter and actual hosted
profile rejects a malformed query, then retrieves the existing validation lesson
with that request ID. This creates ordinary search receipts, without fixture notes.

The retained validation lesson was revised from version 2 to 3 with this observed
transport boundary and source pointers. Exact mutation retry and a fresh pull
verify its body; scope, sensitivity, applicability and attribution are preserved.
Earlier guidance remains retained. Deployment identities and receipts are in the
metadata manifest.

[CI 34462181728](https://github.com/halbritt/cairn/actions/runs/34462181728)
passed PostgreSQL/race, Python, static/build, use-report and authenticated CLI
checks for the installed commit. Native OpenCode checks ran locally as described
above; hosted CI does not run that opt-in harness.
