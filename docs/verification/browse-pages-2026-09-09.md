# Browse continuation, 2026-09-09

The previous installed browse returned six of nine eligible operational notes at
32,000 available input tokens; the older storage note was absent. Following
`browse.next_offset` now reaches that note on page two without increasing the
per-call budget. The CLI, native Codex client and normal OpenCode session returned six previews,
then three, with nine distinct record IDs and an exact saved-body pull.

## Contract and choice

Agent CLI `search --browse --offset N`, MCP `cairn_search` and the native OpenCode
tool accept the returned continuation offset. The core owns ordering and page
packing. Required instructions remain on every page. Destination, applicability,
policy and pull checks remain in the existing retrieval path. Each request has
its own receipt, budget and expansion credits; the host owns accumulated context.

Offsets refer to eligible optional candidate positions, including candidates
omitted during packing. A page stops before the first candidate that would fit a
fresh page but cannot fit the remaining budget. It does not fill that space with
later smaller previews that would then repeat on the next page. Candidates too
large for any page remain omissions. If the overall envelope cannot fit a single
preview while continuation remains, compilation returns `BUDGET_REFUSED`.

Each page reads current state. Captures or edits can shift positions, so callers
may need to restart. This is bounded browsing, not a complete database export or
a retained snapshot. Required instructions and duplicate bodies may recur across
pages. No aggregate multi-page token limit is claimed.

Leaving browsing unchanged preserved the observed older-note omission. Raising
the default budget would increase every call's allowance. Snapshot cursors would
add lifetime and invalidation state. Current-state offsets address the observed
use with a smaller contract; lexical ranking remains unchanged.

Paged indexes use semantic format v6; unpaged indexes retain v5. No SQL migration
was added. Old-binary v5 receipts for both empty and nonempty queries recompiled
identically with the candidate, including their seals, in a disposable database.
The first compatibility probe mistakenly compared the CLI's historical wrapper
to its contained package; the corrected comparison passed. The report preserves
that verifier error. Internal positional `CompileRequest` test literals were
converted to keyed fields without changing values. External Go consumers using
positional literals must likewise update for the added field.

## Verification

- `make check` and `make test` passed.
- `CAIRN_OPENCODE_TOOLS_BINARY=... make test-integration` passed against a
  disposable PostgreSQL cluster, including the race detector, real API/CLI/MCP
  paths and native OpenCode custom tools.
- Database coverage follows multiple pages with varied preview lengths, retains
  a mandatory instruction on every page, excludes a private note from hosted
  positions, rejects invalid offsets, recompiles each page and pulls a body.
- Installed CLI and one native Codex conversation each followed offsets 0 and 6,
  kept 32,000 available tokens per call and pulled the existing storage note from
  page two. Codex used its actual app-server MCP client with no model turn.
- A normal OpenCode 1.18.21 session followed the same sequence against the installed
  adapter and API using scripted loopback completions. Both pages retained the
  same actual session scope; the final pull matched the saved version and body.
  The initial probe expected a CLI envelope instead of the native result and
  timed out after page one. Its corrected parser passed; both artifacts remain.

The candidate CLI and native OpenCode adapter were installed locally. The API
service was restarted after a backup; the store and API are active. The manifest
records exact hashes of the binaries, source and verification artifacts. The
binary was built from the pending source tree, not a clean release commit.

## Limits

This repairs an observed access gap. It does not establish independent model
selection, reduced task cost or better task outcomes. There was no model
inference. No standalone TypeScript typecheck was available; native OpenCode
executed the adapter. Vocabulary misses, the bounded candidate scan, concurrent
changes between pages and policy omissions remain limitations.
