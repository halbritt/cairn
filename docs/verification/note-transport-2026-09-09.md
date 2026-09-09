# Ordinary note transport — 2026-09-09

Valid ordinary notes could fit the existing 65,536-byte decoded body limit but
exceed the 128 KiB JSON request envelope. For example, Go JSON encoding expands
65,536 `<` bytes to 393,216 bytes before metadata. An authenticated API test
reproduced `INVALID_REQUEST: request exceeds 128 KiB` before the change.

## Implemented behavior

`create`, `edit` and `revise` now accept up to 512 KiB of encoded JSON through
the shared API client/server limit, agent JSON ingress and trusted operator JSON
commands. `agent remember`, MCP and native OpenCode reuse those operations.
The decoded ordinary body limit remains 64 KiB; evidence envelopes remain 8 MiB
and other API operations retain 128 KiB. Qualified operator commands, including
`capture-evidence`, retain their separate default limit. No schema migration,
new tool, permission, retrieval budget or mutation identity change is involved.

The larger envelope accommodates sixfold body escaping with room for metadata.
It remains a finite request limit, not a guarantee for arbitrarily large metadata.
The previous evidence-envelope feature deliberately retained the ordinary cap;
this is a new scoped expansion. Its original report remains historical evidence.

## Verification

- Actual authenticated create, full-draft edit and body-only revision store exact
  maximum-sized escaped bodies. A 65,537-byte body still refuses. Corrected valid
  requests can reuse the refused UUID, showing refusal did not reserve intent.
- Raw HTTP requests bypass client checks: oversized create/edit/revise envelopes,
  including trailing whitespace after valid JSON, refuse before effects. Client
  caps and unchanged evidence/non-body caps also pass.
- Real agent stdin and JSON, trusted operator JSON and MCP capture/edit paths
  preserve stored bytes and retries. Requests committed by the previously
  installed `fe59de9` binary return exact original responses through the new CLI,
  including an edit retried after a later revision.
- A normal OpenCode 1.18.21 session with local scripted completions performs
  maximum escaped capture, body revision, full edit and the earlier retry.
  Independent store reads and retained-version inspection verify exact bytes.
  This uses disposable data and no model inference.
- `make test` passed Go tests and 30 Python tests. Full disposable PostgreSQL
  integration with race checks, CLI/API/MCP and native OpenCode passed, with
  `CAIRN_PREVIOUS_BINARY` set to the installed prior binary. `make check build`
  passed vet, formatting and build.

The first native check used the debug command and failed while parsing its
large JSON output (`Unterminated string`). That run is retained as negative
verification evidence; debug-output truncation is suspected, not established.
The check now uses the existing normal-session fixture. No production workaround
was added for the debug output, and the corrected full integration passed.

## Contribution and limits

The prior evidence procedure was recalled and checked against source. It supplied
analogous guidance about encoded versus decoded limits and shared ownership.
Earlier conversation already nominated this candidate; memory is not credited
with uniquely discovering it. The repair removes a demonstrated storage obstacle.
Its incremental memory contribution and net task benefit remain uncertain.
There were no new model trials or changes to accepted task assessments.

The doctrine review used packet `pkt-7f81487991784826`, two evidence passes and
a schema-validated decision receipt. Nine generic interface, nil and procedure
obligations remain explicitly nonmaterial to this bounded transport change.
[Verification metadata](note-transport-2026-09-09.json) retains packet identity,
artifact hashes, the failed run and completed checks. Local deployment is recorded
separately after installation.
