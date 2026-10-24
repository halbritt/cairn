# Claude compact MCP request shape, 2026-10-24

The [loopback probe](../../scripts/check_claude_compact_request_shape.py)
first built Cairn from source revision `ca2e4b3` and launched the installed
Claude Code `2.1.310` twice. The candidate binary's SHA-256 was
`3710c0e6fcff74f54a73832764ea636c29fb5e216a7874b90d7787c2679931b3`.
Each run had its own private home, a fake API key,
`--bare`, strict one-server MCP config, no built-in tools, and a loopback
Messages stub. The same built Cairn binary served full and `compact/1`
declarations; the compact run added only `--tool-profile compact/1`.
Neither run opened a Cairn API socket or made a subscription provider request.

The stub captured one actual outbound Claude `/v1/messages` request per
profile in memory. It returned a scripted text response without asking for a
tool call. The probe discarded raw requests and retained only the bounded
[aggregate](claude-compact-request-shape-2026-10-24.json), including the
binary and client pins and hashes of the serialized declarations.

| Tool in Claude request | Full UTF-8 bytes | Compact UTF-8 bytes |
| --- | ---: | ---: |
| `mcp__cairn__cairn_search` | 6,371 | 2,684 |
| `mcp__cairn__cairn_pull` | 1,588 | 636 |

All ten tool names remained available. After removing description fields,
the validation shapes of each tool were equal across profiles; the eight
other canonicalized declarations were byte-identical. The model label in the outgoing
fixture request was `claude-sonnet-5-5`; no model was reached. These byte
counts are the client request's JSON declaration shape, not billable tokens,
cache savings, or an accepted task outcome.

The probe closes one local measurement gap in CAIRN-225: Claude can carry
the opt-in compact declarations into its prepared request. The installed
subscription route, Codex and OpenCode request shapes, cold/retry/resume
costs, discovery/help behavior, accepted tasks, live canary and rollback
remain unmeasured. The release gate remains **defer**.

Run `python3 -B scripts/check_claude_compact_request_shape.py` with the
installed Claude CLI available on `PATH`. The local probe passed on the
original checkout and again after rebase with the retained aggregate unchanged;
`make check`, Python syntax parsing and
`git diff --check` also passed.
