# Selected task-file fingerprints — 2026-09-09

The runner can now capture the byte identity of explicitly selected files after
an observed process. A reviewer can reference the resulting evidence in a task
assessment. The fingerprint alone establishes neither task acceptance nor memory
benefit. [Usage and limits](../run-artifact-evidence.md).

## Implemented behavior

`run --artifact LABEL=PATH` works through both the local CLI and authenticated
observer client. It selects up to 16 regular files and 64 MiB total. After the
process outcome commits, the runner captures a manifest containing run IDs,
labels, sizes and SHA-256 values through the existing evidence endpoint. Default
sensitivity is local; `--share-artifact-evidence` explicitly permits hosted
delivery. File contents and actual paths are omitted from the manifest.

The metadata request is retained beside the existing outcome request, so a lost
capture response can be retried without running the process again. Failed reads
return the already observed process result and submit no partial manifest. Files
may predate the run; no atomic snapshot or process-created provenance is claimed.

## Verification

`make check && make test && make test-integration` exited zero after the final
test additions. PostgreSQL tests used an invocation-owned disposable cluster;
no operational database was used for fixtures. Go race checks and the Python
suite passed. The retained [metadata](run-artifact-evidence-2026-09-09.json) pins
the logs, decision receipt and doctrine packets.

The new runner tests check exact hashes of binary data written by a process,
local/shared sensitivity, exclusion of file paths and contents, separately supplied
assessment evidence, exact retry identity after a lost capture response, and a
failed command with a pre-existing empty file. Missing files, directories, final
symlinks, FIFOs, oversized files and combined selections above the limit preserve
the committed process outcome and leave no capture-ready partial request.

The real CLI/API fixture runs a child that copies binary bytes, then independently
reads the stored manifest through the operator interface and compares its digest,
byte count and run IDs. The observer client has no usable database connection.
Existing no-selection runs retain their result shape and process behavior.

These are process, capture and transport checks. No native model task, reviewer
acceptance of a real task, or memory contribution is established by these fixtures.
U2 remains partial for convincing task-value evidence, including qualitative and
cumulative assessments.

## Decision and remaining uncertainty

Reuse of the existing evidence operation keeps witness, sensitivity and retry
identity with the store. The runner owns filesystem observation; the CLI only
parses selection. Manual capture remains available. Automatic workspace capture
and a second evidence store were outside the selected contract.

Pincite packet `pkt-f0b5e687ba6d7e40` informed error handling, placement and
preservation boundaries. Two typed evidence passes, a schema-validated decision
receipt and both citation closures are retained. Six unmet obligations are
classified nonmaterial to this bounded feature: recurring change, representation
volatility/leakage, interface substitution pressure, a pre-implementation failing
test, and measured current cost. No refactoring, test-first process, time-saving
or causal-value claim is made. Runtime and in-repository consumer compatibility
were inspected; external Go consumers were not inventoried.

The reader checks size and modification time around streaming reads. Concurrent
writers can still invalidate a snapshot interpretation. The five-second finish
context cannot promise interruption of a blocked filesystem syscall. Selected
files remain caller-owned and are not retained or removed by Cairn.
