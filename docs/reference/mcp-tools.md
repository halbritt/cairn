# MCP tool reference

Cairn exposes ten ordinary-memory tools through `cairn mcp`. The configured
profile determines collection access, identity, and destination; the tool call
cannot choose a different authenticated principal. Use the [MCP setup guide](../mcp/overview.md)
to connect a client and [memory search](../guides/memory-search.md) for a walkthrough.

The server's `tools/list` schemas are the complete argument contract. The source
is [mcpapi/server.go](../../mcpapi/server.go), with preparation in
[prepare_note.go](../../mcpapi/prepare_note.go). Compact tool profiles shorten
presentation without widening permissions.

## Search and inspect

### `cairn_search`

Search returns required context and optional previews with complete
`pull_arguments`. Start with a query:

```json
{"query":"database test procedure"}
```

| Argument | Meaning |
| --- | --- |
| `query` | Search text; ASCII double quotes prefer exact case-sensitive passages without excluding every other lexical match |
| `semantic` | Request semantic discovery for a nonempty query; inspect `discovery.state` for labelled lexical fallback |
| `browse` | Browse eligible previews without a query when vocabulary is unknown |
| `entities` | At most 16 file/symbol hints, such as `[{"kind":"file","name":"core/store.go"}]` |
| `kinds` | Filter fallible labels; required instructions still apply |
| `offset` | Omit for unpaged search; set zero to begin ranked pagination, then follow `page.next_offset` |
| `context` | Declared current applicability context, subject to configured host values |
| `error_signature_sha256` | Prefer notes linked to a known reviewed failure signature |
| `advisory_conflicts` | Include eligible competing advisory positions as a complete group |
| `request_id` | Optional UUID for an identical retry |

The configured collection, task, and run define search scope. There is no
per-call `scope` argument. A nonempty entity or failure signature can support
lexical discovery without query text; semantic discovery requires text.
Browsing uses `browse.next_offset`, and each page has its own budget.

### Budget arguments

`available_tokens` is the known free input room, expressed using Cairn's
conservative UTF-8 byte upper bound and limited by host configuration.
`memory_budget_bytes` narrows the allowance for the search response **and its
receipt expansions**, including envelopes. Neither measures cumulative
conversation usage.

`min_pull_bytes` reserves charged expansion room; it does not automatically pull
a source or guarantee that a particular source fits. Alternatively,
`inspection_policy` reserves the cost of a complete source/group. Both require
`memory_budget_bytes` and cannot be combined. `/1` retains the legacy ceiling;
`first-fitting-whole/2` permits expansion within declared room when supported.
See [inspection allocation](../inspection-allocation.md) for eligibility and
compatibility conditions.

### `cairn_pull`

Pass the complete `pull_arguments` from the selected preview: `request_id`,
`receipt_id`, and `handle`. Do not reconstruct a handle from the record ID.
A whole pull returns the selected source and, for a marked conflict, its
competing positions. A stale handle requires a fresh search.

`span: {"offset": N, "length": N}` selects a byte excerpt. `opening_bytes`
optionally changes the separately returned prefix for a span; it requires a
span and is limited to 1–768 bytes when supplied. A fragment or opening is not
sufficient context for broader claims about the note. See
[note spans and openings](../note-span-openings.md).

### `cairn_pull_evidence`

Read selected supporting evidence with `request_id`, `receipt_id`, `handle`,
`evidence_id`, and `expected_sha256`. An optional `span` requests a byte range.
Use the original source handle and the digest returned by its expansion. This reads
captured bytes; it does not fetch a live URL or verify that the cited claim is true.
See [evidence citations](../evidence-citations.md).

## Save and correct

### `cairn_prepare_note`

Search for possible predecessors before saving a correction or replacement.
Supply a short `query`, optionally known `entities`, current `context`, and the
search budget arguments. Pull complete current predecessors before deciding
whether to edit an existing note or save distinct knowledge. Preparation creates
a retrieval receipt, not a note, and an empty result does not prove no predecessor
exists.

### `cairn_remember`

Required arguments are `request_id` and `body`. Choose a UUID before writing
and preserve it with the same arguments for retries.

| Optional argument | Meaning |
| --- | --- |
| `kind` | `note` (default), `observation`, `claim`, `lesson`, `procedure`, `decision`, or `preference` |
| `scope` | `repository` (default), `task`, or `run`, within the configured collection |
| `shareable` | `true` permits hosted delivery; the default `false` keeps the note local |
| `entities` | File/symbol associations with the same `{kind,name}` shape used by search |
| `pins` | Explicit applicability restrictions; search context is not inherited automatically |

Save selected reusable knowledge with its source and verification context.
The result identifies the new record and version. The note is Class A testimony;
capture does not promote a claim or grant authority.

### `cairn_edit`

Required arguments are `request_id`, `record_id`, `expected_version`, and exactly
one edit mode:

| Mode | Effect |
| --- | --- |
| `body` | Replace text while preserving draft metadata |
| `append` | Append exact text, including caller-supplied whitespace |
| `replace` | Replace one unique exact passage using `old_text` and `new_text` |
| `set_sections` | Update or append named Markdown `##` sections |
| `draft` | Supply a complete replacement draft within the allowed edit contract |
| `evidence_citations` | Replace citations; an empty list clears current references |

Read the complete current note before replacing its body. `VERSION_CONFLICT`
means another revision won: reread, reconcile, and use a new request UUID.
Ordinary edits cannot widen scope or change authority-bearing classes.

## History, assessment, and diagnostics

### `cairn_history`

Supply `record_id`. Omit `version` for metadata pages (default limit 20, maximum
100); follow `next_before_version` with `before_version`. Supply a positive
`version` for one exact historical body, optionally with a byte `span`. The tool
also accepts `available_tokens` and `memory_budget_bytes` for that read.
Historical wording does not establish current eligibility. See
[record history](../record-history.md).

### `cairn_assess` and `cairn_assessments`

Read assessment history with `cairn_assessments` and the owned `receipt_id` before
writing a review. `cairn_assess` takes that receipt, `expected_version` (zero for
empty history), `request_id`, outcome/failure fields, `evidence_ids`, and `reason`.
For a qualitative review with uncertain acceptance, use `task_outcome: "unknown"`,
`failure_domain: "unknown"`, `failure_kind: ""`, and `evidence_ids: []`. Explain
observations, costs, and uncertainty in the 8–4000-character reason. Other outcomes
require selected evidence. Reviews remain testimony and do not establish general
memory benefit. See [use and outcome](../use-outcome-loop.md).

### `cairn_client_info`

Accepts `{}` and reports the running facade's build, active presentation options,
and declared capabilities separately from the authenticated API. Unknown
capability support is not compatibility. See [build identity](../build-identity.md).
