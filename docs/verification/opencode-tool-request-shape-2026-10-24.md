# OpenCode native Cairn tool request shape, 2026-10-24

[`check_opencode_cairn_request_shape.py`](../../scripts/check_opencode_cairn_request_shape.py)
installed the current [native adapter](../../integrations/opencode/cairn.ts)
in a private temporary project and ran OpenCode 1.19.4+cairn.148842e against a
loopback OpenAI-compatible stub. The first canary request carried no tool
declarations; the subsequent tool-bearing request exposed all nine native
Cairn tools:

`cairn_assess`, `cairn_assessments`, `cairn_edit`, `cairn_history`,
`cairn_prepare_note`, `cairn_pull`, `cairn_pull_evidence`, `cairn_remember`,
and `cairn_search`.

The captured validation schemas are represented by per-tool SHA-256 hashes and
an aggregate hash in the [sanitized report](opencode-tool-request-shape-2026-10-24.json).
The nine complete declarations total 23,821 bytes when independently encoded
as canonical compact JSON; the parameter-schema portions total 17,797 bytes by
the same measure. These are reproducible declaration-shape sizes, not exact
marginal wire bytes or tokenizer counts. The complete-declaration total
includes each outer `type: function` wrapper and its full function object; the
earlier exploratory subtotal counted only the inner function object and was
superseded. The fixture pins both the complete-declaration-set SHA-256
(`7eeee7a8157975de8fdb172d6b3041b7b1870ffd797b4f4270de4c6337018aff`) and
the validation-schema-set SHA-256
(`08968bbea4606c355440d273d826368c28eeb36ddfbde743005042eba82d977c`) as
deterministic drift assertions. The OpenCode binary SHA-256 was
`66209749ccbe28bd46d14b4ef992f7d1f6750f874147476e581b0223ec0015c5`; the
adapter SHA-256 matched the installed/source identity already recorded in the
schema exposure audit (`bbf3ce4b9a3c07c489da04d8bd82246b1925c71c1907b3f8bfaa12172b0f6012`).

The fixture returned scripted text and made no tool call. It opened no Cairn
API or store, changed no installed account settings, and retained no raw
request. No external provider or model inference occurred. This was the initial
full-profile OpenCode observation. The later
[full/compact comparison](opencode-compact-profile-2026-10-24.md) adds an
opt-in compact description profile and measures its outgoing declaration
shape. Neither probe measures provider usage/cache or discovery-call cost, or
establishes a useful task outcome. CAIRN-225 remains open pending paired
workflow and acceptance measurements.

Reproduce with:

```sh
python3 -B scripts/check_opencode_cairn_request_shape.py \
  --opencode /absolute/path/to/opencode \
  --output /tmp/opencode-tool-request-shape.json
```
