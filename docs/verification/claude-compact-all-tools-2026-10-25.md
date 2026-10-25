# Claude MCP all-tool description profile, 2026-10-25

The new opt-in MCP `tool-profile compact/2` shortens all ten tool descriptions.
The full default and existing `compact/1` profile remain unchanged. The
profile changes descriptions only; it does not hide tools or change handlers.

[`check_claude_compact_all_tools.py`](../../scripts/check_claude_compact_all_tools.py)
built Cairn from the candidate source and ran Claude Code 2.1.310 in three
separate private homes. Each home used `--bare`, a fake fixture key, strict
single-server MCP configuration and a loopback Messages stub. The stub captured
one outbound tool-bearing request per profile in memory and returned a scripted
text response. The probe retained only aggregate declaration counts, byte/token
proxy measurements, and hashes. It did not call a subscription provider, invoke
a model, execute a tool, connect to Cairn API/store, alter installed settings,
or save raw requests.

| MCP profile | Tools | Declaration array bytes | `o200k_base` proxy tokens | Saved vs full |
|---|---:|---:|---:|---:|
| Full (default) | 10 | 25,139 | 5,032 | — |
| `compact/1` | 10 | 20,500 | 4,175 | 4,639 bytes / 857 proxy tokens |
| `compact/2` | 10 | 17,707 | 3,674 | 7,432 bytes / 1,358 proxy tokens |

The compact/2 declaration array is 29.6% smaller than full and 13.6% smaller
than compact/1 (an additional 2,793 bytes / 501 proxy tokens). It keeps the ten
names and the exact validation-contract hash
for every tool. Search and pull retain compact/1's schemas and descriptions;
the other eight tools now have shorter top-level descriptions. Full-size
per-tool declarations, token proxies and validation hashes are in the
[sanitized report](claude-compact-all-tools-2026-10-25.json).

| Tool | Full bytes / tokens | `compact/1` bytes / tokens | `compact/2` bytes / tokens |
|---|---:|---:|---:|
| `cairn_assess` | 1,505 / 316 | 1,505 / 316 | 1,013 / 228 |
| `cairn_assessments` | 795 / 156 | 795 / 156 | 606 / 122 |
| `cairn_client_info` | 866 / 155 | 866 / 155 | 457 / 91 |
| `cairn_edit` | 5,059 / 1,064 | 5,059 / 1,064 | 4,156 / 898 |
| `cairn_history` | 2,125 / 432 | 2,125 / 432 | 1,759 / 357 |
| `cairn_prepare_note` | 3,538 / 673 | 3,538 / 673 | 3,344 / 640 |
| `cairn_pull` | 1,588 / 323 | 636 / 143 | 636 / 143 |
| `cairn_pull_evidence` | 880 / 195 | 880 / 195 | 762 / 177 |
| `cairn_remember` | 2,401 / 488 | 2,401 / 488 | 2,279 / 465 |
| `cairn_search` | 6,371 / 1,237 | 2,684 / 560 | 2,684 / 560 |

The size unit is canonical UTF-8 JSON for the complete `tools` declaration
array in each captured request. `o200k_base` uses tiktoken 0.12.0 and is only a
proxy; it is not a verified Claude tokenizer or provider-usage measurement.
The JSON report distinguishes the measured candidate source-file SHA-256 from
the worktree's base commit and records whether that worktree had uncommitted
changes; this run measured a dirty candidate based on `668e4c8`.
These requests contain no tool result and do not measure discovery calls,
cache/billing effects, task quality, or accepted outcomes. Authorization and
handler behavior were not varied in this probe. CAIRN-225 remains open for
paired successful workflows and usage/cache evidence.

Reproduce with the installed Claude CLI and a local Go toolchain:

```sh
uv run --with tiktoken==0.12.0 python3 -B scripts/check_claude_compact_all_tools.py
```
