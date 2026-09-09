# Native OpenCode tools

The [native tool adapter](../integrations/opencode/cairn.ts) gives OpenCode
session-scoped search, body/evidence pulls and ordinary note maintenance through
the existing authenticated Cairn CLI. It uses OpenCode's `context.sessionID`;
the MCP alternative continues to require explicit task/run configuration.

Verified with OpenCode 1.18.21. Its
[custom-tool interface](https://opencode.ai/docs/custom-tools/) supplies session
context. Its pinned
[MCP adapter](https://github.com/anomalyco/opencode/blob/v1.18.21/packages/opencode/src/mcp/catalog.ts#L38)
does not send that context in MCP tool-call metadata. Do not use `--codex-thread`
with OpenCode or substitute a process ID for a session ID.

## Install in a project

Install the current Cairn CLI at a stable path and provision an ordinary API
profile for the intended repository and destination. From an existing target
project, run:

```sh
cairn opencode-install --project "$PWD" \
  --socket /absolute/path/to/api.sock \
  --token-file /absolute/path/to/hosted-agent.token \
  --repo /absolute/path/to/canonical/repository
```

The binary embeds its matching adapter. The command writes
`.opencode/tools/cairn.ts` and `.opencode/cairn.json`, both owner-only, without
requiring a source checkout. Connection paths are made absolute relative to the
invoking directory, and the executable path names the binary running the command.
The repository identity stays exactly as supplied. No token bytes are read and
no API, database, model or package service is contacted during installation.

Identical reruns leave files untouched. If either file's contents differ, the command
refuses before writing either file; inspect the differences and repeat the full
command with `--replace` for an intentional upgrade or reconfiguration. That
replaces differing Cairn files and tightens their permissions to `0600`. Symlink
destinations are refused. Each file replacement is atomic, but the pair is not a
filesystem transaction: after an I/O failure, rerun the same command to finish.
Successful output lists both paths, content hashes and written/unchanged states.
An `INSTALL_FAILED` response identifies a filesystem failure and exits 7.

The installer does not edit `opencode.json`/`opencode.jsonc` or tool permissions, add
credentials, edit Git exclusions, or start a harness. Keep the connection file
out of Git and start a fresh OpenCode session to load the installed tools. Upgrade
the API separately when a new tool feature requires it.

Use `--tokens` to set memory room (default 32,000). Optional `--revision`,
`--workspace-sha256`, `--task-class`, `--binding` and `--capability` flags populate
the existing declared context settings; the API validates them on retrieval.
There are no task/run flags because native OpenCode supplies session scope.

The installed connection file has this shape and can also be maintained manually:

```json
{
  "executable": "/absolute/path/to/cairn",
  "socket": "/absolute/path/to/api.sock",
  "token_file": "/absolute/path/to/hosted-agent.token",
  "repo": "/absolute/path/to/canonical/repository",
  "tokens": 32000
}
```

Keep this connection file out of Git and make it owner-only (`chmod 600
.opencode/cairn.json`). It contains the token path, never its bytes. Use the
hosted profile for a hosted model; do not substitute operator/observer credentials
or a local-destination token. The CLI verifies token custody and the API owns
principal, role, repository authorization and destination filtering.

OpenCode loads `@opencode-ai/plugin` for the TypeScript tool definition. The
verified host supplied version 1.18.21. No separate Bun executable is needed for
the tool; OpenCode runs it. On updates, copy the new adapter together with the
matching Cairn CLI. Search refuses a CLI result without structured pull arguments.

The names are `cairn_search`, `cairn_pull`, `cairn_pull_evidence`,
`cairn_remember` and `cairn_edit`. OpenCode's tool permissions apply, including
explicit requests through the native permission context. Choose permissions for
the intended task. These names differ from the `cairn_cairn_*` MCP names; an
existing MCP-only permission entry does not automatically allow native tools.

## Scope and behavior

Search uses the configured repository, task `opencode/<sessionID>` and run
`<sessionID>`. This groups a conversation, including multiple turns, and is not
an execution-attempt identity or observed outcome. Missing/invalid native session
IDs refuse search. No environment or random-ID fallback exists.

Search returns full mandatory `selected` context and an ordered index. Pass each
relevant entry's complete `pull_arguments` to `cairn_pull`. Retain those arguments
for retries. The CLI also retains its existing shell `pull_command`; the native
adapter omits that redundant command from the tool result. Its
`cairn.opencode-search/1` presentation preserves the underlying `source_schema`
and `source_seal` and is not itself a sealed package.

For an unfamiliar topic, `cairn_search` also accepts `{"browse": true}` without
a query. This exposes eligible previews ordered by scope specificity and recency
within the existing budget. Inspect `omitted.OPTIONAL_BUDGET` for notes left out
by packing; browsing is not a complete inventory. Scope, applicability,
destination filtering and mandatory context still apply. A blank search does
not enable browsing implicitly, and a nonempty query cannot accompany it.

If the result contains `browse.next_offset`, call `cairn_search` again with
`{"browse": true, "offset": N}` using that value. Keep the same session and
context. Every page repeats required instructions, and each page/pull consumes
context in addition to prior calls. Pages read current state; edits and captures
may shift positions. Update both the API service and CLI for paged browsing.

For vocabulary mismatches, use `{"query":"storage?","semantic":true}` with
the [optional local semantic backend](semantic-discovery.md). Inspect
`discovery.state`; unavailable scoring produces labelled lexical fallback.
Similarity does not prove the note answers the question. Pull and verify the
source. Semantic search cannot accompany browsing, and ordinary queries remain
lexical. Update the API, CLI and adapter together to use this argument.

Capture saves selected reusable knowledge as repository-wide A testimony.
`kind` defaults to `note`; omitted `shareable` keeps the note local. Writes return
only IDs, version and retry ID. For a text-only correction, edit accepts `body`, the pulled record ID and
expected version, and a new request UUID. Stored draft metadata is preserved.
Alternatively, edit takes the same full replacement `draft` as the
[MCP edit tool](mcp.md#tools); supply exactly one of `body` or `draft`. With a full
draft, preserve scope, sensitivity, applicability, relations and attribution. Stale versions require a fresh pull and reconciliation.
Neither capture nor edit grants authority or establishes task success.

The adapter validates arguments itself before accessing connection settings or
calling Cairn. In the verified OpenCode version, publishing a tool schema does
not enforce its types at execution time. For example, `shareable` must be a JSON
boolean; the string `"false"` is refused with `INVALID_REQUEST` instead of being
treated as permission to share. Omitted `shareable` still keeps a note local.

Connection settings are read for each call. Optional `context` keys are
`revision`, `workspace_sha256`, `task_class`, `binding` and `capability`; they map
to the existing CLI search flags. These are host declarations, not attestations
of the physical checkout. Pulls continue to use their original receipts.

`tokens` defaults to 32,000, with the same 256–1,000,000 range as MCP. The adapter
uses the conservative UTF-8 byte bound and refuses oversized results rather than
returning a partial body. A refused result can follow a committed write or pull;
reuse the original request ID. OpenCode applies its own result truncation and
task budgets separately. Each CLI child has a 30-second timeout and receives the
native cancellation signal. API errors remain errors; connection failures do not
become empty search results.

## Verify

The opt-in native checks use a disposable PostgreSQL database and actual OpenCode
custom-tool execution, with a model catalog fixture that has no usable endpoint:

```sh
CAIRN_OPENCODE_TOOLS_BINARY=/absolute/path/to/opencode make test-integration
```

This variable enables no-model custom-tool checks. It is separate from the older
`CAIRN_OPENCODE_BINARY` model probe. Tests cover session scope, default local
capture, exact body/evidence pulls, retries, edits, stale handles, hosted filtering
and permission/repository refusals, including malformed capture arguments. See the
[verification record](verification/opencode-tools-2026-09-09.md) for evidence and
remaining limits. Remove the installed tool file to undo this integration; keep
ordinary memory records and the existing MCP/CLI alternatives.

`cairn_pull` also accepts `span: {offset: 0, length: 4096}` for a partial A/B
note. An index entry's `summary_span` can be copied into `span` to read the
exact preview source bytes, excluding synthetic omission markers. Its text,
byte range and full/selected hashes appear in `span`, while
`selection.record.body` is empty. Instructions require a whole pull. Use a new
request UUID for each range and read the full note before replacing its body.
The [note excerpt contract](index-and-pull.md#index-and-expansion-contract)
defines limits and currentness checks.

`cairn_pull_evidence` accepts an optional `span: {offset: 0, length: 4096}`
for byte ranges of larger captured sources. Keep `expected_sha256` bound to the
full object and use a new request UUID for each range. Selected bytes and their
checksum appear separately in `span`; see the
[evidence expansion contract](index-and-pull.md#index-and-expansion-contract).
Update the API, CLI and installed adapter together before using this option.

To check the separate normal-session argument path on OpenCode 1.18.21:

```sh
python3 scripts/check_opencode_defaults.py \
  --opencode /absolute/path/to/opencode \
  --output /tmp/cairn-opencode-defaults-check
```

The output directory must be new. This uses a scripted loopback completion
endpoint, with no model inference or Cairn database access. It characterizes the
upstream default/type behavior and checks the shipped adapter's rejection path.
A changed upstream result requires review after a harness upgrade. The
[validation repair](verification/opencode-validation-2026-09-09.md) records the
original failure and the distinction from the debug tool path.

### Select saved kinds

`cairn_search` accepts `kinds`, for example
`{"browse": true, "kinds": ["decision", "preference"]}` to find saved project
direction. It also works with lexical or semantic queries. Required instructions
always apply, and labels confer no authority. Keep the same kinds when following
`browse.next_offset`. See [index and pull](index-and-pull.md) for the full contract.
Update the API, CLI and bundled adapter together before using this argument.
