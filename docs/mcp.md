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

Searches use the fixed startup scope. Capture saves reusable notes in that
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
| `cairn_search` | `query`, optional retry `request_id`. Returns mandatory context plus an ordered index. Each entry has a complete `pull_arguments` object for the next call. |
| `cairn_pull` | Pass an entry's `pull_arguments` unchanged to receive its full body. Reuse those arguments for retries. |
| `cairn_pull_evidence` | Original `receipt_id` and `handle`, an attached `evidence_id`, its `expected_sha256`, and a retry `request_id`. Shares the body's expansion credits and bytes. |
| `cairn_remember` | `body` and a stable UUID `request_id`; optional `kind` and `shareable`. Defaults to an ordinary local note. Returns the record ID, version and retry ID, without echoing the body. |

With an ordinary agent profile, capture is A testimony. Select reusable knowledge
with source/verification context; exclude raw sessions, private Council material
and credentials. `shareable: true` explicitly permits hosted delivery. Default
local notes do not appear in hosted searches. Capture does not promote authority.

Search's `cairn.mcp-search/1` is a presentation of the source package, with its
`source_schema` and `source_seal` retained. The augmented view is not itself sealed.
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

## OpenCode example

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
