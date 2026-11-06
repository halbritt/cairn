# Cairn

Cairn gives AI agents memory across sessions and a way to coordinate work with
other agents. It stores selected knowledge in PostgreSQL and makes it available
through command-line tools, MCP, and integrations for Codex, Claude Code,
OpenCode, Agy, and Hermes.

An agent starting a new session can recover a project's decisions, a working
procedure, or the state of unfinished work. Agents can share that knowledge
across tools and send requests and results through durable inboxes.

## Why use Cairn?

Agent conversations have limited context. When a session ends or its history is
compacted, useful decisions and hard-won fixes can disappear with it. Switching
tools can mean explaining the same project again.

Cairn lets you keep the parts worth carrying forward. For example, an agent can
save the reason a migration needs a particular test, retrieve that guidance when
working on the next migration, and revise it when the procedure changes. A
handoff can preserve what remains to be done so another session can continue.

Memory stays inspectable: notes have versions, retrieval can return a small
index before fetching full text, and saved evidence and observations can help
you review what happened. Scope and sharing settings control which memory is
eligible for a task and whether it can be sent to a hosted model. Agents still
need to check remembered advice against current source and instructions.

## What you can do

- **Remember and recall:** save decisions, preferences, procedures, lessons, and
  handoffs; search by text, file or symbol association, or optional semantic
  discovery; read and correct notes.
- **Share across sessions and tools:** connect supported agents to the same
  collection and use lifecycle integrations for selected recall and capture.
- **Coordinate agents:** discover sessions, publish requests or notices, track
  replies, and schedule work through durable inboxes. Native delivery and worker
  launch require configured host integrations.
- **Inspect and operate:** examine retrieval receipts and recorded outcomes,
  manage evidence and authorized instructions, delete or forget records through
  explicit workflows, and back up or restore the store.

Cairn is an untagged alpha designed for a trusted, single-user environment. It
requires operating a PostgreSQL store; harness integrations have different
setup and delivery contracts. The [capability overview](docs/implementation-status.md)
describes the implemented surfaces and their limits. Broad improvements in task
quality or cost remain evaluation questions.

## Get started

The [quickstart](docs/quickstart.md) walks through building Cairn, starting an
isolated store, and saving and retrieving your first note without a model
account. It then links to authenticated agent setup.

| Your goal | Start here |
| --- | --- |
| Try memory locally | [Quickstart](docs/quickstart.md) |
| Connect an agent | [MCP setup](docs/mcp/overview.md) |
| Save useful knowledge | [Memory capture](docs/guides/memory-capture.md) |
| Find and inspect memory | [Memory search](docs/guides/memory-search.md) |
| Resume or hand off work | [Agent workflows](docs/guides/agent-workflows.md) |
| Coordinate several agents | [Session coordination](docs/guides/session-coordination.md) |
| Inspect work and deliverables | [Work coordination](docs/operator/work-coordination.md) |
| Run a shared service | [Deployment](docs/operator/deployment.md) |
| Understand or extend Cairn | [Architecture](docs/developer/architecture.md) and [contributing](docs/developer/contributing.md) |

Use the [documentation index](docs/README.md) for reference material and advanced
features. The [glossary](GLOSSARY.md) explains terms used in the API and design.

## How it fits together

The Go core owns record validation, retrieval, and state transitions in
PostgreSQL. The operator CLI can use the store directly. Agent clients use an
authenticated local API; the MCP server exposes ordinary memory tools over that
API. Host integrations connect those tools and inbox delivery to agent sessions.

Ordinary notes are fallible knowledge. Evidence-backed claims and authorized
instructions have additional contracts; saving a note does not grant it
authority. Retrieval records what context was supplied, while task assessments
record judgments about the result. Those are separate observations.

The default store runs locally. Shared collections and
[multiple-machine deployments](docs/multi-machine.md) use explicit configuration.
Collection identity is independent of a project's working directory, so a note
shared across projects should name the project when that affects its meaning.

## Verify changes

Validation runs locally. Start with the [testing guide](docs/developer/testing.md)
for dependencies and checks appropriate to your change:

```sh
make check
make test-integration
```

Integration tests create disposable PostgreSQL clusters. `make test` alone skips
database coverage unless a test DSN is supplied. Use a disposable database for
all tests. Backup and recovery changes also require `make test-lifecycle`.

See the [roadmap](docs/roadmap.md) for remaining acceptance work and the
[changelog](CHANGELOG.md) for development changes. Dated
[verification records](docs/verification/README.md) retain the evidence and limits
of individual tests and deployments.
