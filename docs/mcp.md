# MCP memory tools

`cairn mcp` runs a local stdio MCP server for harnesses that support MCP tools.
It connects to the existing authenticated Unix API. PostgreSQL credentials are
unnecessary in the harness process. The host supplies explicit connection paths
and a repository/task/run identity:

```sh
cairn mcp \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" --task investigate-storage --run attempt-1
```

This command waits for an MCP client on stdin. Use the provisioned ordinary agent
profile for the intended destination. A hosted model needs a hosted profile;
the facade cannot infer the model's destination or correct a host's wrong token
choice. The API owns identity, role, repository authorization and destination
filtering. Do not give models operator or observer credentials.

By default, searches use the fixed startup scope. Capture saves reusable notes in that
repository with task/run `*`. Pulls retain the API's caller/destination/handle
checks. Start a new server with the host's actual task/run IDs for a new task;
reusing an example's IDs combines observations under that declared scope.

For memories restricted to a particular build or task context, the host can also
supply `--revision` (immutable Git object ID), `--workspace-sha256`, `--task-class`,
`--binding`, and `--capability`. These optional declarations stay fixed for the
server's lifetime and accompany every search. For example, append
`--revision "$(git rev-parse HEAD)" --task-class repair` when launching from the
intended checkout. They declare applicability; Cairn does not attest that the
physical checkout matches them. Restart with updated declarations when the
context changes. Invalid values produce the API's `INVALID_REQUEST` on search.

A missing or mismatched required pin withholds an optional memory. A mandatory
instruction requiring unavailable context can refuse the search with
`POLICY_UNENFORCEABLE`. Model tool arguments cannot override these declarations.
Leaving all five flags empty preserves searches without a context object.

## Tools

| Tool | Inputs and behavior |
| --- | --- |
| `cairn_search` | A `query`, or `browse: true` without a query; optional retry `request_id`. Returns mandatory context plus a bounded index. Each entry has a complete `pull_arguments` object for the next call. |
| `cairn_pull` | Pass an entry's `pull_arguments` unchanged to receive its full body. Reuse those arguments for retries. |
| `cairn_pull_evidence` | Original `receipt_id` and `handle`, an attached `evidence_id`, its `expected_sha256`, and a retry `request_id`. Shares the body's expansion credits and bytes. |
| `cairn_remember` | `body` and a stable UUID `request_id`; optional `kind` and `shareable`. Defaults to an ordinary local note. Returns the record ID, version and retry ID, without echoing the body. |
| `cairn_edit` | `record_id`, `expected_version`, a stable UUID `request_id`, and either `body` for a text-only correction or the complete replacement `draft`. Revises an active A note through the existing ordinary edit API. Returns identifiers without echoing the body. |

When the vocabulary of a saved note is unknown, call `cairn_search` with
`{"browse": true}` to inspect available topics, then pull relevant entries or
search again using words from their previews. Browsing uses the same scope,
applicability, destination and memory-budget checks. Optional notes are ordered
by scope specificity and recency, rather than query relevance. Older notes may
be omitted when the budget fills; the index is not a complete inventory.
Omitting the query requires explicit browsing, and combining browsing with query
text is an error.

Continue with `{"browse": true, "offset": N}` when a result supplies
`browse.next_offset: N`. Keep the same conversation, context and budget, and use
a new request ID for a new page. Every page repeats mandatory context and uses
its own retrieval budget; callers must budget the combined context. Pages read
current state rather than a retained snapshot, so new or edited notes can shift
positions. [Paging details](index-and-pull.md#agent-commands-without-request-json)
include limits and the raw API form.

With an ordinary agent profile, capture is A testimony. Select reusable knowledge
with source/verification context; exclude raw sessions, private Council material
and credentials. `shareable: true` explicitly permits hosted delivery. Default
local notes do not appear in hosted searches. Capture does not promote authority.

To correct a saved note, search and pull its current body first. For a text-only
correction, call `cairn_edit` with `record_id`, `expected_version`, a stable
`request_id` UUID and the new `body`. Cairn preserves kind, scope, sensitivity,
pins, relations and delegation attribution from that exact version. The new
version records the authenticated writer. Supply exactly one of `body` or `draft`.

For a full replacement through `cairn_edit`,
copy `kind`, `body`, `scope`, `claim_type` and any `sensitivity`, `pins`,
`relations`, `attributed_producer`, `attempt_id` and `result_ref` fields from
the returned record into `draft`. Change the intended content and use the pulled
record's ID and version. Keep source and verification context in the correction.
Do not copy output fields such as `class`, `attribution_state`, `observed_writer`
or `written_at` into the draft; the store owns these. Editing records the authenticated writer and
retains the earlier version.

Retry with the exact same request ID and arguments. A new request using an old
version returns `VERSION_CONFLICT`: search and pull again, reconcile the other
edit, then submit a new request. Edits cannot change scope, sensitivity or pins,
nor revise B/C records. The tool rejects a draft outside the server's configured
repository. It is marked as destructive because it revises existing content;
the generated `--memory-only` host policy continues to allow only search and body
pull. [Verification](verification/mcp-edit-2026-09-08.md) covers the ordinary
edit path and its limits.

Search's `cairn.mcp-search/1` is a presentation of the source package, with its
`source_schema` and `source_seal` retained. The augmented view is not itself sealed.

For vocabulary mismatches, add `semantic: true` to a nonempty search query.
[Semantic discovery](semantic-discovery.md) requires an optional local CPU backend
and preserves the configured scope, mandatory context and pull budgets. It cannot
be combined with browsing. Inspect `discovery.state` and the status banner;
unavailable backends return labelled lexical fallback. Similarity is not evidence
that a note answers the question.
The `selected` field retains full mandatory bootstrap; index summaries are not
substitutes for inspecting relevant bodies. Memory content remains fallible data.

`--tokens` defaults to 32,000, supplies the search's available memory room and
limits the encoded MCP tool result. The host must reserve room for prompts, tool
definitions, conversation and output separately. This is not a global task budget.
No tool result is silently truncated. A presentation `BUDGET_REFUSED` may occur
after an API operation commits; it does not undo a receipt or refund pull credits.
Use the same request UUID when retrying with sufficient configured room.

Tool errors preserve Cairn's error code and any protected-refusal identifier.
Transport failures are errors, not empty search results. `STALE_HANDLE` requires
a new search; an unknown receipt returns `NOT_FOUND`. Startup diagnostics use
stderr, and normal EOF emits no extra Cairn response envelope on stdout.

The facade does not expose operator actions, run observation or automatic capture.

## Codex example

Codex can launch the stdio server with conversation scope derived from its native
tool-call metadata. Replace the absolute paths:

```toml
[mcp_servers.cairn]
command = "/absolute/path/to/cairn"
args = [
  "mcp",
  "--socket", "/absolute/path/to/cairn/api.sock",
  "--token-file", "/absolute/path/to/cairn/hosted-agent.token",
  "--repo", "/absolute/path/to/repository",
  "--codex-thread",
]
enabled_tools = [
  "cairn_search", "cairn_pull", "cairn_pull_evidence",
  "cairn_remember", "cairn_edit",
]
required = true
startup_timeout_sec = 15
```

Use a provisioned profile authorized for that repository and hosted delivery.
This example exposes ordinary capture and edit as well as retrieval. Remove
`cairn_remember` and `cairn_edit` for retrieval-only access. The
[native maintenance check](verification/codex-maintenance-2026-09-08.md) saved a
selected procedure, revised it, and retrieved its current version in a fresh task.
`--codex-thread` requires each search call to carry `_meta.threadId`. Cairn sets
`task_id` to `codex/<threadId>` and `run_id` to `<threadId>`, keeping the configured
repository. Repeated searches within a conversation keep that scope; new
conversations get distinct scopes. This groups a whole conversation, including
multiple turns, and does not identify execution attempts or prove task outcomes.
The metadata is a host declaration, not authentication. The API profile still
controls identity, repository access and destination. Capture remains repository-wide;
pulls use their original receipts and existing caller/destination checks.

The flag cannot be combined with `--task` or `--run`. Missing or invalid metadata
refuses search with `--codex-thread requires tool-call _meta.threadId`; Cairn does
not guess from environment variables or process IDs. The identifier must be a
nonempty string of at most 240 bytes, with no whitespace or control characters,
and cannot be `*`. Use explicit `--task` and `--run` instead for clients without
this metadata or tasks needing finer scope. Explicit mode ignores thread metadata.
`opencode-config` requires explicit scope and rejects `--codex-thread`.

The [native thread check](verification/codex-thread-2026-09-09.md) verifies this
metadata with Codex CLI 0.153.4. Compatibility with other versions remains to be
checked. Optional context flags still describe startup revision, workspace and
binding; restart with updated declarations when these change. Conversation scope
does not attest that those declarations match the current checkout.

Codex's [configuration instructions](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
cover user/project placement and command-line overrides. `codex mcp get cairn --json` inspects the resolved configuration; it does not prove the server connected.
For a trusted project, put the entry in `.codex/config.toml` to keep its repository
and token profile local to that project. Keep installation-specific paths out of
Git. Set `required = false` if unavailable memory should not block startup.
The [local adoption check](verification/codex-project-2026-09-09.md) verified
discovery and exact retrieval from Cairn's project configuration without MCP
overrides. New conversations can use it; an already running conversation may
need a client restart to load the new tools.
An [actual Codex client check](verification/codex-mcp-2026-09-08.md) connected,
listed the permitted tools, searched and pulled the exact saved lesson without
starting a model turn. Model-selected use and task benefit remain unverified.

## OpenCode example

For automatic session scope, use the [native OpenCode tools](opencode-tools.md).
The MCP configuration below remains the explicit task/run alternative.

Generate the server entry with the installed Cairn executable:

```sh
cairn opencode-config \
  --socket "$HOME/.local/share/cairn/api.sock" \
  --token-file "$HOME/.local/share/cairn/hosted-agent.token" \
  --repo "$HOME/git/cairn" --task actual-task-id --run actual-run-id \
  > /tmp/cairn-opencode.json
```

The output is an OpenCode JSON configuration fragment, without a Cairn response
envelope. It names the executable that generated it, resolves relative socket
and token paths against the generating working directory, and preserves the
repository/task/run identities literally. It accepts the same `--tokens` and
context flags as `cairn mcp`. Generation does not read the token, contact the API,
or require HOME or database access; connection and context validation still occur
when the harness uses the configuration.

By default the fragment contains only the `mcp.cairn` entry, preserving the host's
choice of tool permissions when merged into its configuration. For a standalone
task limited to memory search and body pulls, add `--memory-only`. That emits a
top-level permission policy denying other tools. Do not assume that merging this
policy removes explicit tool allowances from another configuration. Generation
does not modify the host's files or launch a task. Regenerate with actual task/run
IDs for each new task and keep the generated executable available.

The following local-server form was verified with OpenCode 1.18.21. Substitute
your absolute executable, socket and token paths, repository, and actual task/run
IDs. Add the entry to the configuration owned by the launching host:

```json
{
  "mcp": {
    "cairn": {
      "type": "local",
      "command": [
        "/absolute/path/to/cairn", "mcp",
        "--socket", "/absolute/path/to/api.sock",
        "--token-file", "/absolute/path/to/hosted-agent.token",
        "--repo", "/absolute/path/to/repository",
        "--task", "actual-task-id", "--run", "actual-run-id"
      ],
      "enabled": true
    }
  }
}
```

`command` is an array containing the executable and its arguments. Do not use a
string `command` with a separate `args` field. Tool permissions belong in the
top-level `permission` object, not a `tools` object inside the server entry.
For a task restricted to memory search and body pulls, that permission object is
`{"*":"deny","cairn_cairn_search":"allow","cairn_cairn_pull":"allow"}`.
Choose permissions appropriate to the full task when adding other tools.

`opencode mcp list --pure` checks connection. Verify that it lists `cairn` as
connected: an observed invalid configuration returned exit zero while reporting
no configured servers. OpenCode prefixes tool names with
the configured server name; this entry exposes names such as
`cairn_cairn_search`. Its permission configuration can allow search/pull while
denying capture. See the [OpenCode MCP documentation](https://opencode.ai/docs/mcp-servers/)
for the configuration supported by your harness version.

The facade uses the official [MCP Go SDK v1.7.0](https://github.com/modelcontextprotocol/go-sdk/tree/v1.7.0),
including its protocol negotiation and typed input validation. The
[verification report](verification/mcp-2026-09-08.md) distinguishes protocol tests,
actual OpenCode tool use and remaining usefulness claims.

A [configuration task and follow-up](verification/mcp-host-use-2026-09-08.md)
demonstrate a curated procedure revision, native MCP use and host-run association.
The host observes tool results and calls `link-run-retrieval` with its observer
profile; the model keeps its ordinary agent profile. The association does not
turn a process exit into task acceptance.
