# Share memory across projects and agents

Use one Cairn collection when several agents need the same decisions, procedures,
and lessons. A note saved through one configured client can be retrieved through
another, subject to the collection's scope and destination rules. This lets you
carry project knowledge into a new conversation without copying entire chat logs.

## Choose the collection

The `repo` setting identifies the memory collection. It does not have to match
an agent's working directory. Configure each client with the same collection
identifier and a profile authorized for that collection. For example, a team
could use `/projects/shared-memory` while working in `/projects/api` and
`/projects/web`.

A shared collection does not create automatic per-project isolation. Include the
project name in a note's body when its guidance applies to one project. Use
separate collections and profiles when projects need separate access boundaries.
Changing a client's `repo` value does not move existing records into the new
collection.

## Connect your clients

First [deploy the API and provision an ordinary agent profile](operator/deployment.md).
Then configure each client with that profile, socket, and collection:

| Client | Setup guide |
| --- | --- |
| Codex | [MCP configuration](mcp/codex.md) |
| Claude Code | [MCP configuration](mcp/claude-code.md) |
| OpenCode | [Native tools and MCP](mcp/opencode.md) |
| Agy and Hermes | [MCP configuration](mcp/agy-hermes.md) |

Choose user-level installation to make tools available across projects. An
explicit project configuration can override user settings. Restart the client
after installation so it loads the new tools and configuration.

Task and run labels organize retrieval within a collection. Codex's thread mode
and OpenCode's native adapter can derive these labels from their conversation
or session. Other MCP setups supply them at startup. Labels are declarations,
not authentication or proof of an individual execution attempt.

## Save knowledge that can be reused

1. Search for existing guidance and pull relevant notes before saving a new one.
2. Save a selected decision, procedure, correction, or lesson with enough context
   to explain when it applies. Include its source or verification method.
3. Use the default `repository` capture scope for guidance shared across tasks.
   Use `task` or `run` only when the note belongs to those configured labels.
4. Set `shareable: true` only for content that may be delivered to hosted models.
   Local-only notes are withheld from hosted searches.
5. Correct an existing note with its current version when guidance changes.

Ordinary notes remain fallible testimony. A saved preference or procedure does
not override the user's current request, authenticate a caller, or prove that a
previous task succeeded. Exclude credentials, private conversations, and raw
session transcripts. See [capture and correction](guides/memory-capture.md) for
examples.

## Retrieve complete context

Search before substantive work, then pass a relevant result's complete
`pull_arguments` to `cairn_pull`. A preview can omit a qualification; read the
whole note before relying on advice that needs its surrounding conditions.
Check the guidance against current source and the task at hand.

Automatic recall has a bounded allowance. When a task needs a larger complete
note or handoff, start a new search with enough available context and
`memory_budget_bytes` for the source and response overhead. The
`first-fitting-whole/2` inspection policy supports larger explicit reads when
the client and API expose it. Existing receipts keep their original allowance.
See [source inspection allocation](inspection-allocation.md).

## Add automatic recall and capture

Optional lifecycle integrations retrieve relevant context at supported session
boundaries and capture selected durable notes or workstream checkpoints. Install
only the integration for your client, and check its documented permissions and
capture settings:

- [Claude Code lifecycle](claude-lifecycle.md)
- [Codex lifecycle](codex-lifecycle.md)
- [OpenCode lifecycle](opencode-lifecycle.md)
- [Hermes lifecycle](hermes-lifecycle.md)

Explicit memory tools remain available independently of these hooks. For agents
on another machine, use the [relay and enrollment setup](multi-machine.md).
