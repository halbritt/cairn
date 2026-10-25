# OpenCode retry and resume request sequence, 2026-10-24

[`check_opencode_cairn_request_sequence.py`](../../scripts/check_opencode_cairn_request_sequence.py)
ran the installed OpenCode 1.19.4+cairn.148842e native adapter in a fresh
temporary home/project against a scripted loopback OpenAI-compatible endpoint.
The adapter pointed to a deliberately absent Cairn executable and socket. Its
three attempted local tool invocations therefore returned local errors; no
Cairn API or store was contacted. No external provider, model inference, account
configuration, or installed hook was involved.

The endpoint observed the actual outgoing requests. It saw the same nine native
Cairn declarations on first use, retry after a local tool error, post-retry
final request, same-session resume first use, and resume retry. Their complete
declaration set remained 23,821 canonical JSON bytes with SHA-256
`7eeee7a8157975de8fdb172d6b3041b7b1870ffd797b4f4270de4c6337018aff`; the
validation-schema set remained
`08968bbea4606c355440d273d826368c28eeb36ddfbde743005042eba82d977c`. The
resumed process used the original OpenCode session ID. The [sanitized report](opencode-request-sequence-2026-10-24.json)
retains phase labels, tool names, hashes, byte counts, and aggregate field
names only; it contains no request body, arguments, dialogue, or tool result
text.

None of the observed request bodies contained cache-specific fields from the
fixture's allowlist. OpenCode's JSON event stream did expose
`part.tokens.cache.read`, `part.tokens.cache.write`, `part.tokens.input`,
`part.tokens.output`, and `part.tokens.reasoning`. The loopback server supplied
scripted usage-shaped response fields, including cached-token values, solely to
exercise that reporting path. These are fixture counters, not provider usage,
cache savings, billing, or measured overhead. This fixture measures schema
stability across local error/retry and same-session resume; it does not exercise
a successful Cairn operation or establish an accepted task outcome.

Reproduce with:

```sh
python3 -B scripts/check_opencode_cairn_request_sequence.py \
  --opencode /absolute/path/to/opencode \
  --output /tmp/opencode-request-sequence.json
```

CAIRN-225 remains open for paired accepted workflows, provider cache/usage
accounting, and demonstrated useful outcomes.
