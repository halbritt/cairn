# Native host capture verification

Striatum commit
[`5ea87ca65c1bd25f228c0447110d991a3f0de8c8`](https://github.com/halbritt/striatum-next/commit/5ea87ca65c1bd25f228c0447110d991a3f0de8c8)
adds a host capture tool and offline reader for the proposed native Cairn input.
The [how-to](https://github.com/halbritt/striatum-next/blob/5ea87ca65c1bd25f228c0447110d991a3f0de8c8/docs/how-to/capture-cairn-context.md)
documents its commands and trust boundary. It is preparatory tooling: it produces
retained world data, not an admitted artifact or a provider invocation.

## Implemented behavior

The trusted host invokes Cairn's authenticated CLI to compile once and confirm
the exact receipt through `run-package`. Cairn owns the stored semantic seal and
eligibility checks. Striatum compares the full returned package and the requested
repository/task/execution scope, query digest, destination, applicability and
memory budget. A changed response or refusal produces no successful capture.

The capture preserves all package bytes and readable context, including evidence,
authority and mandatory selections. It retains a query digest rather than another
raw-query copy, and excludes credential and connection paths. A complete capture
is published as a new owner-only file; existing captures cannot be overwritten.
The full capture hash distinguishes receipt identity, while the rendered-context
hash distinguishes content changes from a new receipt over the same selection.

Offline reading requires an independently retained full capture hash, verifies
the context/rendering relationships and performs no live calls. It does not
authenticate an arbitrary replacement file and replacement hash, re-evaluate
current eligibility or confer execution authority. The observer label is host
attribution, not a service-signed principal identity.

## Checks and integration

The cross-repository check used Cairn code
`93bc66a972222fa47dec97e87beecdb5e82b8a82`, a disposable PostgreSQL cluster and a
real Unix API. Local and hosted observers captured a saved procedure. The exact
rendering matched Cairn's real child-process input, including escaped characters
and Unicode. A new receipt over the same memory preserved its content hash.
Ordinary-agent and missing credentials were refused. After stopping the API,
the tool still rendered the exact pinned capture; a wrong hash refused.

The first integration run failed because the test directory did not meet Cairn's
owner-only socket requirement. Correcting the fixture to mode 0700 made it pass;
the service's permission check was preserved.

Unit race tests cover fourteen context mismatches before confirmation, changed
receipt readback, stale refusal, complete mandatory content, malformed retained
relationships, empty versus unavailable service and output replacement refusal.
The real-service check above also passed under the race detector. Full Striatum
`make check` passed under Go 1.23.4, including formatting, build, all accepted
decision-source pins and the whole test suite. No Go dependency or version
requirement changed.

The source was merged and pushed after the live-work guard passed. No accepted
catalog, schema or generated decision changed. No Striatum deployment, graph
request, timer, production regression fixture or model invocation was performed.
The [metadata](native-capture-2026-09-08.json) retains source and log hashes.

## Remaining native work

The trusted producing path still needs to pin this acquisition before sealing
the observation run. The observation must produce the proposed ECR through its
accepted contract; build must resolve and consume the admitted version named by
its packet. The existing supervisor then needs the authenticated Cairn checks
and actual host correspondence at its provider launch boundary. No second
process wrapper or memory copy should be introduced outside ordinary input
rendering.

Native admission, real build execution and task benefit remain unproved. This
component closes the acquisition-tooling gap while those full-path requirements
remain open. It does not close U1 or establish general memory usefulness.

Validated doctrine packet `pkt-dac63b90e6813393` informed ownership, reproducible
input handling and behavior preservation. Its decision record classifies thirteen
unmatched obligations relative to this component; full native completion and
real-task requirements remain explicit outstanding work.
