# Selected-file evidence capture, 2026-09-09

The existing binary API needed callers to construct base64 JSON themselves.
`agent evidence --file PATH --source LABEL` and operator `capture-evidence` with
the same flags now read and encode a selected regular file. Both reach the existing
capture operation with canonical base64; no backend, schema, attribution or
sensitivity policy changed. [Commands and limits](../evidence-refresh.md#capture-a-selected-file).

The initial CLI test failed because `agent evidence` refused command arguments.
After implementation, the authenticated request contains exact binary bytes and
the chosen source label, with no automatically inserted input path. Stdin is not
consumed. Local sensitivity and a caller-supplied retry ID survive unchanged.
Invalid files/flags refuse before API contact. Regular-file symlinks, text CRLF,
explicit sharing and generated canonical UUIDs are covered.

The first full integration passed its Go/race phase but failed at operator file
capture: a generic argument-count check ran before the new handler. The operator
file branch now runs before that check. The final disposable CLI/API sequence
passes for both entry points, including existing JSON capture and stdio MCP.
The failed integration log is retained. Static checks, final CLI race tests and
the earlier Go/40-Python suite also pass.

## Placement and compatibility

The two CLI routes share file-to-evidence request preparation. Bounded regular-file
reading is shared with task-file startup; each caller retains its own size and
content rules. Startup still requires nonblank UTF-8 without NUL and keeps its
budget error. Evidence supports arbitrary bytes, rejects empty or over-limit files,
and always uses base64 so its representation is stable across file contents.
No file path, mtime or inode is sent as implicit provenance. Files can change while
being read; the evidence identifies the bytes read, not a filesystem snapshot.

No-argument JSON capture remains unchanged. Operator file capture supports the
full 1 MiB source directly; its JSON mode retains the earlier 128 KiB encoded
limit. Agent file capture uses the existing 8 MiB API envelope. Request identity
includes source bytes/representation, label, sharing and repository, not input
path. Repeating a committed UUID after changing the file refuses. A new invocation
without a supplied UUID is a new capture, not an automatic retry.

## Verification and task meaning

Targeted CLI and startup checks cover the shared reader and input boundaries.
The real CLI/API integration reads an exact 1 MiB binary file through each entry
point, verifies retained bytes, chosen labels, local/explicit-shareable sensitivity,
identical retries and changed-file conflicts. The agent runs with an invalid client
DSN. Existing JSON binary capture and qualified hosted pulls remain covered.

This removes a manual preparation step in selected evidence capture. No reduction
in task time, better downstream decision or general memory benefit is claimed.
The existing evidence guide was retrieved before implementation and informed
encoding, limit and retry choices; current source and the preceding conversation
were also available. L4 remains partial for larger managed artifacts, upstream
freshness and broader lifecycle work.

[Metadata](file-evidence-capture-2026-09-09.json) retains verification and decision
provenance. The API already runs binary-capable `386eae1`; only the CLI needs an
update. Installation is recorded separately. No native adapter or model task
was changed or launched for this work.
