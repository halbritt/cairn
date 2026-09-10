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
load its tool schema. The existing API and database need no upgrade.
