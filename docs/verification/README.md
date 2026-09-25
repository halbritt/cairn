# Reading verification records

Files in this directory describe what a particular run or review established at
the time it was written. They are historical records, not a live status page.
Use [implementation status](../implementation-status.md), the current source and
a fresh local validation run to assess the present build. A later implementation
can supersede one row in an earlier report without changing that report's other
observations.

## Evidence locations

Many records name `/tmp` logs, local home-directory paths or SHA-256 digests.
Those locations identify the original host's artifacts; they do not make the
bytes available to a later reviewer. A digest can verify bytes that are still
held, but cannot reconstruct an artifact that was not retained. Do not count an
unavailable locator as independently reproducible evidence, or recreate a log
and present it as the original run.

An inventory of the dated reports found `/tmp/` references in 133 JSON and
94 Markdown files. This count is based on filenames matched by `rg -l '/tmp/'
docs/verification -g '*.json'` and the same command with `*.md`, excluding this
README from the Markdown count. It does not establish whether any particular
artifact still exists. Two dated JSON receipts,
`file-evidence-capture-2026-09-09.json` and
`json-unicode-integrity-2026-09-09.json`, also contain an `in_progress` status.
Read those statuses as part of their recorded review, not as the state of the
current project. Check their companion reports and current source before
relying on a completion claim.

When revisiting a historical claim, cite the exact retained source, test or
artifact that can still be inspected. If the raw artifact is gone, label that
limit and run a new check under a new date. The repository's normal validation
is local; see [Verify changes](../../README.md#verify-changes).

| Material record | Retained in Git | External evidence and current interpretation |
| --- | --- | --- |
| [Run status](run-status-2026-09-08.md) | Report and named tests | Three `/tmp/cairn-run-status-*` logs/proofs were present on the review host during this inventory, but are not committed. Their paths are not durable evidence for another host. |
| [Task-file runs](run-task-file-2026-09-09.md) | Report, [metadata](run-task-file-2026-09-09.json) and source checks | Three named `/tmp` logs were present on this host and matched the manifest digest. They contain the same suite summary, so their identical bytes do not independently prove that the final rerun included each later assertion. |
| [File capture](file-evidence-capture-2026-09-09.md) and [Unicode integrity](json-unicode-integrity-2026-09-09.md) | Reports and JSON receipts | The receipts captured CI as `in_progress`. The companion reports state their local results; neither historical status is a current CI gate. |
| [Reviewed recurrence](reviewed-recurrence-2026-09-08.md) | Report and [metadata](reviewed-recurrence-2026-09-08.json) | Raw model/session output is intentionally outside Git. M maps to receipt trial `M2`; the assessment script changed after M. The recorded outcome remains the historical claim. |

## Selected supersessions

- The [replay requirement audit](replay-requirements-2026-09-10.md) predates the
  [authenticated recompile route](authenticated-recompile-2026-09-10.md). Its
  original-incident evidence gap remains open.
- The [integrated semantic discovery report](semantic-discovery-2026-09-09.md)
  measures one-shot loading. The [idle cache report](semantic-idle-cache-2026-09-10.md)
  documents later in-memory vector reuse across streaming worker exits.
- The [note-vector reuse report](semantic-vector-cache-2026-09-09.md) says there
  was no snapshot across worker exits in that build. The idle cache report
  supersedes that lifetime statement; neither report claims a disk cache.
