# Ordinary agent request help — 2026-09-10

During the Codex guide correction, `cairn agent edit --help` returned an invalid
JSON-request error and source inspection was needed to locate the replacement
request shape. The installed `replace --help` reproduced the same gap.

`cairn agent --help` now lists ordinary operations. Operation `--help` or `-h`
explains `edit`, `revise`, `append`, `replace`, `cite`, `history`, `assessments`,
`assess-run` and `recompile`, with JSON examples. This includes the difference
between API full-draft `edit` and the native `cairn_edit` tool's combined interface.
See [request help](../local-api.md#find-an-ordinary-request-format).

Help is handled by the CLI before home-directory lookup, credential access,
connection or stdin. Only explicit help results use readable text; normal calls
retain their JSON envelopes and API checks. Unknown flags, unknown help operations
and extra help arguments remain invalid. No API or schema change is needed.

## Verification

The new offline-help test failed before implementation and passed after it.
Actual subprocess checks leave stdin open, unset HOME/CAIRN_HOME, and verify
successful readable help with no local state. The examples shown by the binary
are then parsed, supplied with fixture identities and submitted to a disposable
hosted API. They complete note revision, append, exact replacement, full-draft
edit, citations, historical inspection, qualitative review and reconstruction.
The workflow checks original retries, stale-version refusal, preserved earlier
text and unknown testimony.

All CLI race tests, `make check` and the full disposable integration suite passed.
The first full run passed the examples but failed a later evidence-count assertion:
the new citation reused that test's support. The corrected fixture captures its
own evidence, and the full rerun passed. This was test interference, not a product
failure. The [manifest](agent-help-2026-09-10.json) retains source and log hashes.

This removes a concrete request-discovery obstacle. It does not establish net
memory benefit or a completed independent model task. A retrieved connection
lesson reinforced preserving execution defaults, alongside the same rules in
current source. Installation is separate from this source checkpoint.
