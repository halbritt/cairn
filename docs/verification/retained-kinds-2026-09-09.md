# Retained execution must preserve kind intent — 2026-09-09

An acceptance review found an omission in the preceding kind-filter change:
retained execution compared the requested scope, query, context, purpose and
budget, but omitted `kinds`. A caller could therefore declare a different filter
and still launch the old filtered package. The saved receipt/seal and eligibility
checks remained valid; the declared selection intent did not match.

## Reproduction and repair

Two real PostgreSQL/child-process checks reproduced the mismatch before repair.
An unfiltered package launched with a newly declared `note` filter. A filtered
package launched when its declared kinds were omitted, emitting the retained
filtered body. This is rework caused by `2d67181`, not independent task-value
evidence or an operational disclosure incident.

The runner now uses the core's existing normalization and compares the resulting
set before binding or launching retained execution. Unknown labels and changed
sets refuse. Normalization copies the caller's array; equivalent order and
duplicates retain the same intent. Fresh and retained `run` commands expose
repeated `--kind` through their shared trusted/authenticated parser. Valid retained
execution still uses the original receipt and exact rendered bytes without a new
compile. No schema, API protocol, native adapter or dependency changes are needed.

The final disposable PostgreSQL/race integration passed, including no binding,
claim or child output after mismatch, exact retained rendering, caller-array
preservation, duplicate-launch refusal and fresh filtered execution. The actual
CLI/Unix API path ran without client database access. All Go tests, 30 Python
tests, vet and formatting passed. This repair did not run a model task.

## E2 acceptance review

Core bounded index/pull, mandatory bootstrap content, source/evidence expansions,
expiring caller-bound handles, credits, destination checks and stale/revoked
refusals are implemented and covered by the current suite. Existing native
interfaces expose those tools. The previous kind-filter deployment verified
ordinary current source retrieval through CLI/MCP and scripted native OpenCode.

That does not complete compact H0 startup. `runner.Run` explicitly refuses an
index compile, `RunPackage` refuses a retained index, and the CLI has no H0 index
startup route. Existing H0 launches deliver body packages. Native tool availability
also does not automatically arrange startup or enforce aggregate task context.
E2 remains partial; its roadmap row now states these specific gaps. This review
does not close U1, E4, D2 or X2, and does not authorize an unrelated Striatum change.

## Evidence and limits

The old retained-context lesson was retrieved after the defect was found. It
reinforces launch freshness and exact-byte preservation, but it did not cause
this discovery. Any saved follow-up must preserve that ordering. The original
kind-filter report remains a valid account of its narrower retrieval checks;
those checks did not cover retained-run filter intent.

Doctrine packet `pkt-1d53f2ef7cc02b8e` used one typed evidence pass; its decision
receipt validates and citation consumption is closed. Five generic interface/
recurrence obligations remain explicitly nonmaterial. Test and documentation
guards checked the changed behaviors and claims. [Metadata](retained-kinds-2026-09-09.json)
retains hashes of the reproduction, completed checks and decision evidence.
