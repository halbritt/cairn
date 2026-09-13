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

- All 87 Python tests, including 13 Hermes and 26 existing lifecycle tests.
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

Deployment status will be recorded after installation and gateway restart.

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
