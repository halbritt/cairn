# Compact native notices and responses

CAIRN-216 adds `compact_inbox_version: 1` for Codex and Claude native delivery.
`scripts/install-agent-coordination.py --native-delivery` enables
`compact_inbox_version: 1`, `notice_digest_version: 1`
(`notice_digest_window_seconds: 300`, without `notice_batch_version`), and
`notice_auto_settle_version: 1` by default for Codex and Claude bindings (and
places `cairn-inbox` on the account's `~/.local/bin` path). Pass
`--no-compact-inbox`, `--no-notice-digest` (or `--digest-window-seconds 0`), or
`--no-auto-settle-action-none` to opt a binding out of any of these defaults.
Removing the capability restores full context for new admissions; an already
issued handle remains usable under its original checks until it expires or its
attempt ends.

If the binding uses [inbox recall](inbox-recall.md), first disable that
manifest and let current callbacks settle before changing the shared binding
configuration. The installer refuses to alter a pinned binding configuration
while its manifest is enabled. After installing a binding-wide change, repin
the changed coordination configuration, validate the complete manifest, then
reactivate it under that document's staged procedure.

To enable compact inbox, notice batches, FYI notice digests (`--notice-digest`
with optional `--digest-window-seconds 1..3600`), or `action: none` auto-settle
(`--auto-settle-action-none`) for a single live session on a shared
native binding without modifying the pinned binding
configuration, disabling inbox recall, or restarting the presence watcher, pass
`--session NATIVE_SESSION_ID` or `--agent-id AGENT_UUID` (or run
`configure-session-inbox` with `--config` on the coordination CLI). Per-session
options are stored in owner-only files under `<state_dir>/session-options/`;
other sessions on the shared binding keep their current behavior, and
`--clear-session-options` removes the per-session override.

On a host that uses [staged lifecycle generations](operator/lifecycle-staging.md),
the delivery text gives each command as a full checked launcher path followed by
`inbox compact read|inspect|settle|advance HANDLE`. Run those exact commands
wherever this document writes `cairn-inbox`; the `~/.local/bin/cairn-inbox`
helper is placed only by the unstaged installer and is not required. A session
loads its hooks when it starts, so a session that predates the published
generation carrying these options accepts `configure-session-inbox` but keeps
receiving full contexts until it is replaced.

A compact notice or response names its kind, sender and exact source version,
followed by its structured header (`Summary`, `Action`, and `Due` when declared)
before any source body or preview. High-level send commands (`cairn agent send`
and MCP/OpenCode `cairn_send`) accept `--summary TEXT` / `summary` (`1..280`
single-line UTF-8 bytes) and require it whenever `body` exceeds `280` bytes so
callers state what changed in the header and keep full uncompressed prose in the
body.
The hook reads the selected historical version through the existing hosted
profile, checks its identity, full body length and SHA-256, and includes the
whole source when it fits 4,096 UTF-8 bytes and the overall hook budget. An
oversized source (for example, a 9,579-byte report against a 9,500-byte hook
budget) is truncated on a clean line, word, or UTF-8 rune boundary with an
explicit truncation marker and pull handle (`cairn-inbox read HANDLE`, which
also supports `read` and `inspect` on headered `request` deliveries).
The agent runs `cairn-inbox read HANDLE` when it needs the complete source. Reading does
not acknowledge delivery. The owner-only full context and original journaled
commands remain available with `cairn-inbox inspect HANDLE`.

After processing, the agent explicitly runs one of:

```text
cairn-inbox settle HANDLE handled
cairn-inbox settle HANDLE failed --code processing_failed
cairn-inbox settle HANDLE ignored --code unsupported_kind
```

The two non-handled dispositions also accept the existing
`source_unavailable` code. The helper prints a short confirmed state and
attempt count from the API response. It journals the same immutable request
and sends through the existing `ack` operation; an exact retry uses the same
request ID. A changed disposition for that attempt refuses with
`IDEMPOTENCY_CONFLICT`. A successful acknowledgment records handling, not
accepted task outcome.

Handles are 16 random hex characters, are scoped to one binding and live
native attempt, expire after one hour and occupy at most 128 entries per
binding. Every read, inspect and settlement checks the owner process ancestry,
current registered session and execution, exact attempt and delivery, store
lease, and current source availability under the hosted destination. A stale,
restored, forgotten or cross-session handle refuses. The handle is a lookup
key; the existing profile and store checks remain the authority. The helper
keeps token paths, lease IDs and retry IDs in owner-only files. The native
session is still responsible for handling the message before its turn ends.

The retained audit's metadata projection is a representation baseline, not
measured model-token savings.

<a id="bounded-notice-batches-cairn-221"></a>

## Bounded notice batches

Add `--notice-batch` to an installation (or `--session`/`--agent-id` call) to
opt that Codex or Claude binding or session into `notice_batch_version: 1`.
Batching is opt-in and mutually exclusive with `notice_digest_version: 1`: on
binding installation `--notice-batch` suppresses the default digest (or pass
`--notice-batch --no-notice-digest`). This requires schema 067 and its
preceding migrations. A new native session must load the changed hooks; if inbox
recall is enabled, use the manifest repinning procedure above.

After explicitly settling a compact **notice**, run `cairn-inbox advance HANDLE`.
The helper returns the next notice's compact context, or a reason that the
batch ended. Read and settle each returned notice with its own handle. The
store permits at most three items in one native turn. It only takes notices
published before the first attempt and eligible when the command runs. Any
waiting request or explicitly urgent notice ends the batch so the next turn
can handle it; an ordinary notice with
no policy remains eligible. No advance command acknowledges an item. The
store requires a confirmed `handled`, `ignored`, or `failed` result for the
current item and retains its identity, lease, and outcome separately from
every later item. A remainder ends that turn's batch; later arrivals cannot
reopen it.

If the exact source is unavailable, the batch cue retains that item's handle.
Retry `read`, or explicitly settle it as `failed --code source_unavailable`
before advancing. This disposition does not settle neighboring notices.

An uncertain handoff is saved with one request ID and replayed by the native
hook. A failed or stale handle must not be treated as a confirmed settlement
or as permission to retry with a different session. The final notice keeps
its session hold until the normal turn-end reconciliation.


## FYI notice digests

`scripts/install-agent-coordination.py --native-delivery` enables
`notice_digest_version: 1` (schema 088) with `notice_digest_window_seconds: 300`
by default on Codex and Claude native bindings (pass `--no-notice-digest` or
`--digest-window-seconds 0` to opt out). Pass `--notice-digest` (and optional
`--digest-window-seconds SECONDS` in `1..3600`, defaulting to `300`) alongside
`--compact-inbox` to enable digests for an individual coordinator session.

In digest mode:

- Routine and `action: review` notices are held without waking an idle session
  while the digest window is open.
- Priority arrivals (`request` events, `response` events, `urgency: urgent`
  notices, and `action: decide` notices) are never held behind a digest and
  preempt immediately.
- Held FYI notices become due as one digest turn when the digest window timer
  fires, after a `request`, `urgency: urgent` notice, or `action: decide`
  notice arrives while they were waiting, or at a goal-continuation / unbound
  turn boundary.
- A digest keeps the latest notice per `(sender, topic)` (using explicit
  `supersedes` keys or `topic/<name>` destinations while keeping distinct
  untopiced direct notices up to 64 items), reports `superseded_count`, and
  retains per-delivery leases and native hold protection across all items.
- Inspect or read items with `cairn-inbox read HANDLE` or
  `cairn-inbox read HANDLE --item ORDINAL`. Settle individual items with their
  own outcome (`cairn-inbox settle HANDLE ignored --code unsupported_kind --item 3`),
  and/or settle all remaining available items in one call with
  `cairn-inbox settle HANDLE handled`. Partial settlement followed by a turn
  end or process crash preserves already-settled item states and records only
  unsettled items as `failed` (`processing_failed`).
