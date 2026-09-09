# Codex maintains shared Cairn notes

The actual Codex MCP client can capture and revise an ordinary Cairn note, then
retrieve its current version in a fresh task. The operational store now retains
a selected Codex setup procedure with guidance for retrieval, capture, correction,
per-task scope and retry handling. This makes the existing memory interface usable
for maintenance from another harness; no Cairn server change was needed.

Using Codex CLI 0.153.4, the probe started its own app-server and an ephemeral
thread with the existing hosted agent profile. Its one configured server exposed
all five Cairn tools. The client called `cairn_remember` to save the selected
procedure, repeated that exact request and received the same record/version,
then searched and pulled it. It copied the fetched Draft fields and called
`cairn_edit` with the expected version to add the verified maintenance guidance.
The result was version 2 of the same ordinary A record.

A pull through the old handle returned `STALE_HANDLE`. A new ephemeral thread
started the server with different task/run IDs, searched again, and retrieved
the exact version-2 body. The procedure remained shareable A testimony. This
check did not promote it or change its repository, applicability, sensitivity,
relations or attribution fields. The native edit call needed no custom transport
or operator database access.

The [Codex setup example](../mcp.md#codex-example) now includes capture and edit
alongside the three retrieval tools. Removing those two names retains the earlier
retrieval-only configuration. A static server configuration still needs updated
task/run arguments and a fresh server for each task; capture itself creates
reusable notes with repository-wide task/run scope.

[Verification metadata](codex-maintenance-2026-09-08.json) records the checks and
private evidence hashes. Raw procedure bodies, record identifiers and protocol
results remain under `/tmp/cairn-codex-maintenance-a`. The calls were explicitly
chosen by this coding agent and executed through the native client; no model
turn or delegated task was started. This establishes native maintenance behavior,
not independent model judgment, task improvement or resume/compaction interlocks.
The [earlier connection check](codex-mcp-2026-09-08.md) and its official upstream
references describe the transport and configuration used here.
