# Compact native notices and responses

CAIRN-216 adds an optional `compact_inbox_version: 1` binding capability for
Codex and Claude native delivery. Install it with
`scripts/install-agent-coordination.py --native-delivery --compact-inbox` and
the binding's usual arguments. The installer places `cairn-inbox` on the
account's `~/.local/bin` path. Existing bindings keep the full context unless
they opt in. Removing the capability restores full context for new admissions;
an already issued handle remains usable under its original checks until it
expires or its attempt ends.

If the binding uses [inbox recall](inbox-recall.md), first disable that
manifest and let current callbacks settle. The installer refuses to alter a
configuration while its manifest is enabled. After installing, repin the
changed coordination configuration, validate the complete manifest, then
reactivate it under that document's staged procedure. An active native session
keeps its loaded hook code; start a fresh session for the new capability.

A compact notice or response names its kind, sender and exact source version.
The hook reads the selected historical version through the existing hosted
profile, checks its identity, full body length and SHA-256, and includes the
source when it fits 4,096 UTF-8 bytes and the overall hook budget. A larger
source says why it is omitted.
The agent then runs `cairn-inbox read HANDLE` for that exact body. Reading does
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

This capability is an additive local implementation. The retained audit's
metadata projection is a representation baseline, not measured model-token
savings. Compare complete sender, recipient and capture workflows with
CAIRN-214's `scripts/workflow_model_report.py` on frozen paired inputs before
accepting a rollout claim. No live binding is enabled by this source change.

## Bounded notice batches (CAIRN-221)

Add `--notice-batch` to an installation that also uses `--compact-inbox` to opt
that Codex or Claude binding into `notice_batch_version: 1`. This requires
schema 067 and its preceding migrations. Existing bindings continue to admit
one item per native turn. A new native session must load the changed hooks;
if inbox recall is enabled, use the manifest repinning procedure above.

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
its session hold until the normal turn-end reconciliation. This is an
implemented opt-in path; paired model and task outcomes remain to be measured
before accepting the rollout.
