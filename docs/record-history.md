# Inspect retained record history

[File and symbol associations](entity-search.md) are explicit versioned metadata.
Native capture and search accept `entities`; body-only edits preserve them, full
draft edits can replace them, and exact-version history reads expose them.

Use history to compare a saved procedure with its earlier wording, inspect who
wrote each version, or review a correction across sessions. The existing record
versions stay in PostgreSQL; this read does not create another archive.

With the ordinary profile provisioned for the repository and destination:

```sh
cairn agent --token-file /path/to/ordinary-agent.token history <<'JSON'
{"record_id":"RECORD_UUID","limit":20}
JSON
```

Replace `RECORD_UUID` with the identifier returned by capture, search or pull.
The response has `historical: true`, the record's current version/class/lifecycle,
and a newest-first `versions` list. Entries include the original version number,
class, kind, scope, authenticated writer, witness, timestamp, payload availability,
and SHA-256/UTF-8 byte size of available bodies. Metadata pages omit body text.

The default page size is 20; `limit` accepts 1–100 (zero also selects the default).
When `more` is true, pass `next_before_version` as `before_version` to continue.
The cursor is exclusive, so appending a newer revision does not shift older entries
between pages. Each page has its own consistent snapshot; its `current_version`
may advance. Missing or pruned versions are not reconstructed, and version numbers
can have gaps. An empty terminal page has `more: false`.

Request one exact retained body when it is needed:

```sh
cairn agent --token-file /path/to/ordinary-agent.token history <<'JSON'
{"record_id":"RECORD_UUID","version":1}
JSON
```

Exact-version mode returns one entry with `body`; do not combine it with nonzero
`limit` or `before_version`. The body limit remains 65,536 bytes. A missing exact
version returns `NOT_FOUND`. The source hash describes the returned original body,
while `current_version` describes the record now. Pull the current note before
editing; an earlier version is useful comparison material, not a replacement for
the current compare-and-swap input.

## Read a passage from an earlier version

For a long earlier note, add an explicit byte span to the exact-version request:

```json
{"record_id":"RECORD_UUID","version":1,"span":{"offset":4096,"length":1024}}
```

The returned version omits `body` and includes `span`. Its `offset` is the requested
start, `end` is exclusive, and `total_bytes` describes the complete historical body.
Follow `end` as the next offset while it is below `total_bytes`. Offsets count
UTF-8 bytes, not characters; the source is the requested version, even when the
current note has a different length. Offset must be 0–65,535 and before the end of
that body; length must be 1–65,536. Only EOF clips the requested range. A span
without a positive exact version, invalid bounds or an offset at EOF refuses with
`INVALID_REQUEST`.

`span.body` contains valid UTF-8 text. When the selected bytes split a character,
`span.body_base64` carries the exact bytes instead. `span.sha256` checks the selected
bytes; the version's `body_sha256` and `body_bytes` still describe the complete
original body. Version, class, attribution and associations remain historical.
No whole-body text is returned alongside an excerpt.

These arguments work through the existing CLI/API and native history tool. Update
the CLI/API and MCP facade or native adapter together before using `span`; older
interfaces reject the new argument. Requests without `span` retain their existing
whole-body and metadata behavior. No database migration is required.

The library method is `Store.History(ctx, RecordHistoryRequest, Destination)`.
The Unix API exposes `POST /v1/history` through `Client.Call`; the direct local
operator command `cairn history` accepts the same request JSON. API readers need
no database credentials. An optional `repo` in CLI/API requests also requires
the record to belong to that repository; it cannot widen profile authorization.

## Native tools

MCP and native OpenCode expose `cairn_history` with the same `record_id`,
`version`, `before_version`, `limit` and optional `span` arguments. For example, after obtaining a
record ID through search or capture:

```json
{"record_id":"RECORD_UUID","limit":1}
```

Read the returned version metadata, then supply the desired positive `version`
without paging fields to inspect its exact body. The tools fix `repo` from host
configuration and refuse caller overrides. OpenCode checks its `cairn_history`
permission. The read requires neither a request UUID nor an index handle.

Update the CLI/API, facade binary/native adapter and any explicit tool allowlist first.
The Codex configuration generator includes the new tool; existing configuration
is not rewritten automatically. Old facades report an unknown tool; old APIs
reject the native request's additional repository constraint. Existing CLI/API
history requests without that optional field keep their behavior.

Each result must fit the host's configured memory room, including the serialized
MCP envelope where applicable. An oversized result returns `BUDGET_REFUSED`
without a saved read receipt. Reduce the metadata page size or obtain sufficient
host-approved room for an exact body, or request an explicit passage. Excerpts
also have to fit the configured room; the tools do not silently truncate results.
Historical inspection does not replace current search/pull before an edit.

## Access and meaning

Current repository authorization and sensitivity govern the history read. A
hosted profile cannot inspect a local-only record, even when it knows the UUID.
The profile supplies the destination; request JSON cannot override it. Each
returned version must belong to the same authorized repository. Tombstoned records
return `PAYLOAD_UNAVAILABLE`. An excluded older payload is listed as unavailable
without a body hash or size; requesting its body also refuses. Restore admission
gates this read as it does ordinary record inspection.

The record is held against concurrent edits/forgetting during the transaction.
An operation that conflicts with that snapshot can fail; retry this read to obtain
current history. Responses are not persistently cached and add no use observation
or mutation receipt. This is direct inspection with per-call limits, like `get`;
it does not consume an index handle's expansion credits. The host must budget
the context it chooses to read across calls.

Version classes are reported as originally written. Neither a historical B/C class
nor an old instruction grants present authority. Use current compilation for
current consequential decisions; this endpoint supplies no grant chain, current
eligibility result, launch permission or task-value judgment.
Historical excerpts may inspect any retained class already available to this
reader. They do not satisfy current instruction delivery: current instruction and
competing-position pulls still require complete bodies.
