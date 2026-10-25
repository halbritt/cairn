# CAIRN-225 schema exposure recheck

Date: 2026-10-24. This is a provider-free recheck of the retained private
Claude request-shape sample. It does not measure a new live workflow or establish
causal token savings.

Reproduction (requires the private manifest already described in the token
efficiency plan):

```sh
uv run --with tiktoken==0.12.0 python scripts/audit_schema_exposure.py \
  --manifest "$HOME/.cache/cairn-token-audit-20261023/sample-manifest.json" \
  --since 2026-10-21T00:00:00Z --until 2026-10-24T00:00:00Z \
  --output "$HOME/.cache/cairn-token-audit-20261023/cairn-225-schema-recheck.json"
```

The recheck parsed 4,407 Claude requests from 16 files. All requests had a
retained shape. Cairn schemas appeared in 1,714 requests; the other 2,693 had
zero Cairn tools. Among Cairn-bearing requests, the observed count was two
tools in 162 requests, three in 1,352, and four in 200. Summed across requests,
declared Cairn schema material was 17,312,490 UTF-8 bytes and 3,512,938
`o200k_base` proxy tokens. Those sums include repeated schemas and cached
requests; they are not billed or marginal token costs.

The coldest file-first phase had only one Cairn-bearing request among 14
requests; its four-tool shape measured 15,624 bytes / 3,173 proxy tokens. The
first-Cairn-use phase had three requests (two with Cairn tools), so it is too
small to estimate typical overhead. The after-first-use phase contains 2,067
requests and 3,500,589 proxy tokens in Cairn schema declarations, including
repeated request shapes. Claude request digests and response usage are retained
telemetry, not a controlled cache experiment.

The script reports Codex per-request schemas as unavailable in retained native
logs. It cannot support Codex schema cost inference. This bounds one part of the
ticket: Claude request exposure is measurable, while a comparable Codex
measurement and a third-harness workflow remain open.

The repository already has an opt-in MCP `compact/1` tool profile. Its
`mcpapi/tool_profile_test.go` checks that the retrieval tools remain listed,
annotations and input validation stay equivalent, and declarations shrink. This
is a local candidate, not evidence that any harness loads it or that full
workflow cost falls after search, pull, retry, and resume. Keep the full schema
as the compatibility default until paired harness traces account for actual
discovery calls and cache behavior.
