# Hermes CLI and gateway integration — 2026-09-13

The native provider passes interactive CLI and Slack gateway verification against
Hermes v0.20.5, source `13f4cfebfafbce8ac9d1bf29f66731858ed638b5`, using its
Python 3.11 environment. Cairn CLI/API are `d9c0884`; this adapter needs no Go
upgrade or database migration. [Setup and limits](../hermes-lifecycle.md) describe
the installed contract. The [original plan](../plans/hermes-integration.md) and
[implementation receipt](hermes-integration-decision.json) retain scope and evidence.

## Native event map

| Boundary | Adapter behavior | Verified route |
| --- | --- | --- |
| Owner turn | Search once, including bare continue; inject into request copy | HermesCLI.chat and Slack parser → GatewayRunner |
| Tool loop | Reuse one bounded bundle; preserve original content and sidecars | Real progressive tool discovery and MCP search/pull/remember/edit |
| Completed turn | Synchronous selected capture before return | CLI and gateway; separate decision and workstream checkpoint |
| Compression | Checkpoint original dialogue, coalesce unchanged capture | CLI `/compact`, native compressor and gateway `!compress` |
| Gateway manual compression | Observe pre_command on live provider before memory-disabled helper | Actual persisted native compaction summary |
| New/resume | Bind content using actual turn label, tolerate stale queued switches | Immediate CLI new → chat → resume; gateway reset |
| Interruption/exit | Retry bounded pending original turn; dispose owned hooks | Native in-flight interrupt, shutdown and recovery |
| Routing | Check session labels and context-local profile | Concurrent Slack conversations; identical labels in two native profile contexts |
| Gateway lifecycle | Keep checkpoints current across session recreation | Duplicate events, idle expiry, captured-transport reconnect, runner restart |

Hermes's MCP connection uses profile labels. Lifecycle operations use actual
session labels; neither is authentication. The shared hosted collection remains
repository-scoped across agents. Native memory files coexist and are not imported.

## Verification

The final fixture, `scripts/check_hermes_lifecycle.py`, passed with disposable
PostgreSQL, real Cairn API/MCP, native Hermes and Claude executables, scripted
model endpoints and synthetic Slack ingress. Outbound messages were captured
inside the fixture; no real Slack message was sent. Artifacts remain outside Git
under `/tmp/cairn-hermes-release-native`.

Native Claude created the initial handoff; Hermes continued it through CLI and
gateway entry points, saved a separate decision, updated that decision after an
owner correction, and refreshed the handoff through gateway restart. A fresh
CLI received the gateway revision. Native Claude then searched and pulled the
current Hermes correction. Explicit Hermes tools created and revised the same
record through ordinary MCP.

The fixture made 97 model requests and 17 successful selector calls. Seventy-eight
requests carried exactly one Cairn bundle, 1,579–1,625 UTF-8 bytes, below the
12,000-byte ceiling. Nineteen successful recall operations took 43–99 ms
(median 51 ms); seventeen completed-turn operations took 352–769 ms (median
403 ms). Five unchanged pre-compaction operations took 36–37 ms. These are
local scripted-fixture timings, not real-model inference measurements.

Two actual hanging selectors hit their 35-second deadlines. Both CLI and gateway
tasks completed within the fixture's 50-second ceiling, emitted a diagnostic,
and retried successfully after selector recovery. Focused tests also kill the
outer process group, reject malformed output without exposing private payloads,
check stale session callbacks, release registrations, and discard opted-out
pending dialogue before memory is enabled again. Service unavailability leaves
the main task usable. Extraction excludes tool results, reasoning, summaries,
native memory and recalled sidecars. Original dialogue is bounded to 24,000 bytes.

Final checks passed:

- All 90 Python tests, including 13 Hermes and 29 shared lifecycle tests.
- `make check`.
- `make test-integration` with disposable PostgreSQL/race, CLI/API/MCP, native
  Claude and native OpenCode lifecycle checks enabled.
- Existing Node OpenCode lifecycle check.
- Final native Hermes fixture after the opt-out fix.
- Installer repeatability, preservation of unrelated configuration/native memory,
  and schema validation of the implementation receipt.

The Pincite receipt uses the validated release `d3e0c0d`, doctrine
`doctrine-f6bbb5196a3f8bf9` and retriever `retriever-ec995ecdd083b2c8`. Its source
locator records the final packet hash. Six unmet dependency-inversion obligations
are explicitly nonmaterial: this change implements an existing extension interface
and makes no claim for a new abstraction or economic optimization.

## Deployment

Installed the clean adapter commit `beabd9286c439eb08f3897241fa6b85d136be7c2`
in the default `~/.hermes` profile. Repeated installation preserved all unrelated
configuration values and the hashes of both native memory files. Existing
`wigolo`, plugins, model and native-memory settings remain present. The gateway
restarted and logged Slack Socket Mode connected at 15:27:37 PDT. No external
test messages were sent. CI passed for that exact commit:
[run 34786855951](https://github.com/halbritt/cairn/actions/runs/34786855951).

A fresh Hermes CLI using the configured OpenRouter DeepSeek model searched and
pulled the current Cairn handoff. A native GatewayRunner with the installed
profile and model did the same through a captured outbound adapter; only its
local session index/SQLite path was redirected to scratch space. Both discovered
eight Cairn tools. The model recovered an initial explicit pull-budget refusal
by obtaining a new search receipt. The one-shot `hermes -z -t mcp-cairn` launcher
rejected the dynamic toolset before discovery; the verified CLI path explicitly
performed native MCP discovery before constructing HermesCLI.

These live lookups exposed a shared candidate-discovery bug: a second optional
candidate could exhaust a receipt's expansion bytes after the first had been
read, aborting all selection. The repair distinguishes `BUDGET_REFUSED`, stops
expanding optional candidates at that bound, and preserves already-read records.
It does not swallow transport failures or permit blind revision of an unsupplied
matching topic. Three regression tests cover those boundaries. Repeating the
chosen lookup against the live collection then completed real selection in
3.489 seconds and correctly returned no writes.

The repair passed all 90 Python tests, `make check`, the native Hermes fixture
(`/tmp/cairn-hermes-budget-native`) and the full disposable integration suite
with native Claude/OpenCode enabled.

Final installed code is clean `db530a17a23b17f81715af1083cf928c0d3b4a81`.
The provider hash is
`e91b999799d36812a6ca4cd1a980bc667614910c190a4e7beacea702f1597ad5`;
the shared engine hash is
`a0fa48e188449f8273685b477c2280f91c96a319cb9b26a509cd01f1d59e8836`.
The same engine bytes are installed in both Claude profiles and OpenCode.
Hermes's installed manifest records the clean source revision and all file hashes.
No Cairn API restart or migration was needed; CLI/API remain `d9c0884`.
The code revision is tracked by
[CI run 34787317902](https://github.com/halbritt/cairn/actions/runs/34787317902).

The final gateway restart reconnected Slack Socket Mode at 15:36:45 PDT;
`hermes-gateway.service` is active/running with zero restart retries. Fresh CLI
and captured GatewayRunner lookups using the installed profile and configured
model both passed after the repair. Each discovered eight Cairn tools and
completed search/pull; both session states contain a confirmed capture digest
and no new workstream. Final recall took 142 ms (CLI) and 113 ms (gateway);
completed-turn capture took 5.849 and 5.046 seconds respectively. Neither final
lookup reported a capture failure. The gateway probe sent zero external messages.
These are two bounded ordinary lookup observations, not a generalized task-value
measurement. Metadata and local probe logs remain under `/tmp/cairn-hermes-live-*`.

## Limits

This verifies the installed chat-completions path and configured Slack gateway.
Other gateway transports and API request formats require their own native probe.
Profile isolation was exercised through native context-local provider callbacks;
the owner's configured host currently uses the default profile. Reconnect checks
substitute the external transport; they do not prove Slack message delivery.
Compression tests exercise manual commands and the native compressor path, not a
long conversation naturally exhausting its token budget. Abrupt process death
can lose pending work. No raw transcript spool or new daemon was added.

Selection is fallible and synchronous capture adds real inference latency.
Passing these checks does not establish full Cairn design acceptance, durable
selection quality or incremental task benefit. Normal use remains the source of
that evidence.
