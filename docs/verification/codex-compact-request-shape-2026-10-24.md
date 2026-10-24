# Codex local tool schema probe, 2026-10-24

The corrected isolated Codex setup captured one outgoing request per profile
at a loopback Responses stub. The stub returned HTTP 418 before producing any
model response. The test used Codex CLI 0.164.0, a source-built Cairn binary,
an empty temporary `CODEX_HOME`, no provider credentials, a custom local-only
provider with OpenAI authentication disabled, and no OpenRouter route. It
retained only sanitized counts, names, hashes, and validation results. It made
no external provider request or inference call.

The first request's tool-bearing location was
`input[].additional_tools.tools[]`. It contained three namespaces—`clock`,
`collaboration`, and `functions`—with no Cairn tool name or schema. The compact
and full first requests had the same namespace names and serialized namespace
bytes (24,972 each). Whole serialized request sizes were 52,184 bytes for full
and 52,199 for compact; they include the Codex prompt/context and are not a
Cairn-only allocation measure. No second request was made, so this did not
observe post-discovery or later-turn Cairn schema exposure.

Separately, Codex `app-server`'s local `mcpServerStatus/list` returned the
configured Cairn registry without starting a thread or making a provider
request. All ten tool names were present in both profiles. The normalized
argument validation schemas matched after removing prose `description`
fields. These registry declarations are available to Codex, but they were not
present in the captured first model request and must not be described as
model-visible exposure.

| Registry declaration | Full JSON bytes | Full o200k proxy | Compact JSON bytes | Compact o200k proxy | Validation schema |
| --- | ---: | ---: | ---: | ---: | --- |
| `cairn_search` | 6,464 | 1,257 | 2,777 | 580 | Same hash in both profiles |
| `cairn_pull` | 1,681 | 343 | 729 | 163 | Same hash in both profiles |

The proxy uses `tiktoken` 0.12.0 `o200k_base`; it is not a verified Codex or
provider tokenizer. Registry byte/token reductions do not establish that
Codex sent those declarations to a model, saved tokens, reduced quota use, or
preserved task outcomes. Provider usage, quota/capacity, and accepted task
outcomes remain unmeasured. CAIRN-225 remains open.

Reproduce from the repository root:

```sh
uv run --with tiktoken==0.12.0 python3 -B scripts/check_codex_compact_request_shape.py
python3 -B -m unittest discover -s scripts -p test_codex_compact_request_shape.py
```

The sanitized run output is [codex-compact-request-shape-2026-10-24.json](codex-compact-request-shape-2026-10-24.json).
