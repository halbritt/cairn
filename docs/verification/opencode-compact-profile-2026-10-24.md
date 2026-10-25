# OpenCode compact tool description profile, 2026-10-24

The native OpenCode adapter now has an opt-in `compact/1` description profile.
It keeps the existing full descriptions by default. Set
`CAIRN_OPENCODE_TOOL_PROFILE=compact/1` in the OpenCode process environment to
select shorter tool descriptions. Unknown profile names fail during adapter
load.

The probe ran the installed OpenCode `1.19.4+cairn.148842e` against a loopback
OpenAI-compatible stub, once with each profile. It retained only declaration
names, byte counts and hashes in the [sanitized report](opencode-compact-profile-2026-10-24.json).
Both tool-bearing requests included the same nine Cairn tools and their
argument validation contracts matched after removing prose descriptions. The
canonical JSON size of the nine complete declarations fell from 23,821 bytes
to 19,829 bytes (16.8%).

The probe opened no Cairn API or store, made no external provider request or
model inference, executed no tools, and retained no raw request. These are
local declaration-shape measurements. They do not establish provider token or
cache savings, reduced workflow cost, or equivalent task outcomes.

Reproduce from the repository root with the installed OpenCode binary:

```sh
python3 -B scripts/check_opencode_cairn_request_shape.py \
  --opencode "$(command -v opencode)" \
  --output /tmp/opencode-compact-profile.json
```

The command requires `opencode` and tests both profiles through actual
tool-bearing requests to the local stub. It fails if the compact profile drops
a tool, changes an argument validation contract, or does not reduce declaration
bytes.
