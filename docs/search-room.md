# Set the room for one native search

The MCP and native OpenCode `cairn_search` tools accept an optional
`available_tokens` argument:

```json
{"query":"storage migration procedure","available_tokens":8000}
```

Cairn's current tokenizer uses UTF-8 bytes as a conservative token upper bound.
The value must be an integer from 256 through the host's configured ceiling:
MCP `--tokens`, or OpenCode's `.opencode/cairn.json` `tokens` setting. Omission
uses that ceiling, normally 32,000. A call cannot raise the ceiling, and its
smaller allowance does not change later calls or host settings.

The API compiles the search with this room and its existing optional-memory
policy. Required instructions and presentation overhead must fit; insufficient
room returns `BUDGET_REFUSED`. Optional previews can be omitted as the allowance
shrinks. The native presentation is also checked before returning it. Nothing is
silently truncated to make a response fit.

The resulting receipt retains its own expansion credits and remaining bytes.
Record and evidence pulls share those limits. For a long source, request an
explicit byte span that fits; instructions and competing positions still require
whole delivery. See [index and pull](index-and-pull.md).

Keep the same room on retries and later pages. Reusing a request UUID with a
different effective room returns `IDEMPOTENCY_CONFLICT`; choose a new UUID for
the changed search. A presentation refusal can occur after the API has created
its receipt, so increasing the room also needs a new search UUID. Invalid room
arguments refuse before API contact and do not reserve that UUID.

This gives callers control of one retrieval's allowance. It does not observe the
harness's remaining model window or account for all calls, retained conversation,
tool schemas, generation and compaction. Whole-task accounting remains open.
Do not treat a supplied allowance as an observed host fact.

Update the Cairn CLI/MCP executable and reinstall the native OpenCode adapter
to expose the new argument. Start a fresh MCP connection or OpenCode session to
load its tool schema. For `available_tokens` alone, the existing API and database
need no upgrade.

## Allocate memory separately from available context

The MCP search, CLI `agent search --memory-budget-bytes`, and API `/v1/index`
accept an optional `memory_budget_bytes` integer from 256 through
`available_tokens` (CLI `--tokens`). For example:

```json
{"query":"storage migration procedure","available_tokens":24000,"memory_budget_bytes":7800}
```

Here 24,000 must be the actual free input-context room after the prompt, tools,
retained conversation and output reservation. The existing optional policy still
uses that context room: its default ceiling is 2,400 accounted preview bytes,
and an operator policy can narrow it. The 7,800-byte allocation separately bounds
the search delivery and successful new expansions on its receipt. Whole required
context must fit. Previews can consume the allowance, so no particular pull is
guaranteed; inspect `bytes_remaining` and use a bounded span where appropriate.
Record and evidence pulls share the same remaining allowance.

This opt-in contract conservatively accounts for MCP JSON string escaping and
native response metadata. The CLI returns complete `pull_arguments` but omits
convenience `pull_command` strings under this contract. Other receipts, repeated
delivery of cached retries, and error responses still require caller accounting.
The native OpenCode adapter does not yet expose this separate allocation.

Omission preserves existing packing, accounting and sealed bytes. An explicit
allocation, including one equal to `available_tokens`, is distinct for request
idempotency. Repeat it on retries; use a new UUID to change it. The receipt seals
the `cairn.memory-budget/1` extension, so historical recompilation uses the same
cap and policy input. Readers predating the extension fail its seal check rather
than silently replaying it without the cap. Existing receipts remain readable.

Update both the API and the CLI/MCP facade before using this field; no database
migration is needed. Do not silently drop an unsupported field while keeping a
larger context declaration. If actual context room or field support is unknown,
use the remaining memory allowance as `available_tokens` with the existing
interface. More previews do not establish useful guidance or task success.
