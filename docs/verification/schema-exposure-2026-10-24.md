# Cairn tool declaration exposure: retained native evidence

Date: 2026-10-24. Source checkout: `9a1369d` plus the already landed opt-in
MCP `compact/1` tool profile (`fa1eed9`). No successful provider request was
made for this audit. A later Codex loopback preflight accidentally attempted a
public API connection; it returned HTTP 401 before any request body was
captured. The aggregate [JSON](schema-exposure-2026-10-24.json) has no paths,
session IDs, request IDs, tool arguments, messages, or credentials. The private
frozen manifest and source logs stay under the owner's account.

## Measurement boundary

The read-only [analyzer](../../scripts/audit_schema_exposure.py) examines the
same 2026-10-21 00:00 to 2026-10-24 00:00 UTC convenience sample as the
[token-efficiency baseline](../plans/token-efficiency-2026-10-23.md): 16 Claude
files and 24 Codex files. It measures Cairn declarations in retained Claude
`api-request-shape` records. `o200k_base` is a proxy tokenizer and is not
verified for the deployed models. A repeated shape is a declaration repeated
in a retained request record, not proof that its full text was uncached, billed,
or even available to the model after host processing. Usage is the last
observed streaming value per assistant message ID adjacent to each request;
376 of 4,407 requests have no adjacent response usage in this window, while
some requests have two adjacent response IDs (4,278 IDs total). The
input/cache totals cover the **whole response**, including unrelated work.

The phase labels overlap. `file_first` is the first request in the retained
session file, only when it falls inside the time window. `first_cairn_use`,
`first_pull`, and `first_write` label the preceding retained request when the
corresponding first tool call appears. `post_compaction` is the first request
after a native `compact_boundary`. `same_digest_repeat` is an identical
request digest seen earlier in the file; the log alone does not prove a
transport retry. `after_first_cairn_use` is later activity in that file.
Ordinary resume after a new process is not identified by these logs.

| Claude request cohort | Requests | Requests with Cairn declarations | Declaration proxy tokens | Whole-response cache read tokens | Whole-response input less cache read |
| --- | ---: | ---: | ---: | ---: | ---: |
| All | 4,407 | 1,714 | 3,512,938 | 1,596,082,911 | 11,866,450 |
| File first | 14 | 1 | 3,173 | 1,049,178 | 186,491 |
| First Cairn use | 3 | 2 | 4,588 | 1,760,764 | 2,058 |
| First pull | 2 | 2 | 3,417 | 158,374 | 7,866 |
| First write | 2 | 2 | 4,588 | 957,064 | 1,636 |
| Same digest repeated | 128 | 69 | 133,702 | 38,047,402 | 1,453,410 |
| First after compaction | 8 | 6 | 14,088 | 1,200,228 | 85,664 |

Claude request shapes carried zero Cairn tools in 2,693 requests, two in 162,
three in 1,352, and four in 200. All 4,407 in-window requests resolved to a
retained shape. This supports the earlier finding that Cairn declarations are
not globally eager. It does not establish marginal provider token savings
from shortening them: the response cache telemetry is not divided by tool.

## Installed exposure and profile readiness

- The inspected Codex homes (`.codex`, `.codex-harm`, `.codex-3`) list a Cairn
  MCP server without `--tool-profile`. Codex's retained native records in this
  sample do not include per-request tool schemas. Deferred discovery may load
  a subset, so a configured server cannot be counted as a loaded declaration.
- The inspected Claude home lists `cairn` and `cairn-events` without a compact
  profile flag. The sample predates the source profile rollout; it measures
  full declarations only.
- The installed OpenCode native tool file matches the repository source
  SHA-256 `bbf3ce4b9a3c07c489da04d8bd82246b1925c71c1907b3f8bfaa12172b0f6012`.
  It is a separate native adapter, not this MCP profile. No comparable
  provider-request schema records were available in this audit; static tool
  availability cannot substitute for them.
- The installed Cairn CLI and API report revision `37ad2df`, older than the
  source opt-in profile. No installed-home setting was changed. Source MCP
  tests show both `cairn_search` and `cairn_pull` remain listed, their
  validation schema and handlers stay the same, and the default is full.
  Static declaration bytes from that test are search 6,464 full / 2,777
  compact and pull 1,681 full / 729 compact. These are source fixture bytes,
  not observed compact provider requests.

## Decision and next evidence

The profile remains opt-in and unenabled on the inspected homes. A live paired
trial needs a predeclared model/effort/route and token limit, frozen cold,
first-use, retry, resume, and steady workflows, identical tool availability,
accepted task outcomes, and observed provider request/cache accounting. Include
tool search/help and recovery calls. No provider admission or account setting
change was authorized by this read-only audit. The current evidence supports
only baseline exposure and a reversible candidate; it does not establish a
workflow cost improvement or full CAIRN-225 acceptance.

A later [isolated Claude loopback probe](claude-compact-request-shape-2026-10-24.md)
observed the compact declarations in a real Claude client's outgoing request
to a local Messages stub. It did not use the subscription route or measure
provider usage, and it changed no installed account setting.

The Codex loopback preflight did not capture a request shape. Its invocation
used `--ignore-user-config`, which made Codex ignore the isolated `CODEX_HOME`
configuration pointing to the local Responses stub. Codex instead attempted
`wss://api.openai.com/v1/responses` and received HTTP 401. The attempt was
stopped immediately; there was no retry on another provider or account, and no
request body was captured. This establishes no Codex schema exposure, cache
behavior, token usage or subscription quota state. It is not evidence of
subscription exhaustion. The failure is recorded here so it cannot be mistaken
for a completed request-shape observation.

Reproduce locally with the existing private manifest:

```sh
uv run --with tiktoken==0.12.0 python scripts/audit_schema_exposure.py \
  --manifest "$HOME/.cache/cairn-token-audit-20261023/sample-manifest.json" \
  --since 2026-10-21T00:00:00Z --until 2026-10-24T00:00:00Z \
  --output "$HOME/.cache/cairn-token-audit-20261023/schema-exposure-20261024.json"
uv run --with tiktoken==0.12.0 python -m unittest discover \
  -s scripts -p test_audit_schema_exposure.py
```
