# CLI reference

The `cairn` binary has two access paths. Authenticated client commands use a
provisioned API profile; operator commands use the local store configuration.
Use the client path from an agent. Database credentials belong to deployment and
maintenance workflows.

## Inspect commands without connecting

```sh
cairn --help
cairn agent search --help
cairn agent edit --help
cairn agents --help
```

Operation help describes flags or the JSON request expected on stdin. Put flags
before positional arguments. The [command guide](../commands.md) covers operator
commands and [local API](../local-api.md) covers authenticated operations.

## Connect with an existing profile

`cairn agent` accepts connection options before the operation name:

```sh
cairn agent --socket /path/to/api.sock --token-file /path/to/agent.token version
```

Use paths supplied by your deployment. Defaults resolve under Cairn's data
directory; an enrolled machine can select different defaults. `--profile NAME`
selects a provisioned event profile and cannot be combined with `--token-file`.
`--agent-id UUID --execution-id UUID` selects a registered session under the same
profile. [Configuration](../operator/configuration.md) explains deployment paths.

`cairn version` reports the local CLI build. The authenticated `agent version`
command reports both client and server builds and protocol information.

## Save and retrieve memory

These examples assume the default authenticated profile is configured. Replace
`/path/to/collection` with its exact collection identity, and keep the same task
and run identifiers throughout a task.

```sh
cairn agent search --repo /path/to/collection --task docs-review --run review-1 \
  'database test procedure'
cairn agent pull RECEIPT_UUID HANDLE_UUID
cairn agent remember --repo /path/to/collection --kind procedure --shareable \
  'Run database tests in a disposable PostgreSQL cluster.'
```

Use the `pull_command` returned by search when available. It preserves the receipt,
handle, and connection context. A stale handle requires a new search.

Useful search options include `--semantic`, `--entity-file`, `--entity-symbol`,
`--browse`, `--kind`, and `--offset`. Budget controls include `--tokens`,
`--memory-budget-bytes`, and `--inspection-policy`; these have distinct meanings
in [search room](../search-room.md). Omit `--offset` for unpaged search or set it
to zero to begin ranked pagination.

`remember` defaults to repository-wide applicability and local sensitivity.
`--shareable` permits hosted delivery. Supported ordinary kinds are `note`,
`observation`, `claim`, `lesson`, `procedure`, `decision`, and `preference`.
For deliberate retryable writes, choose `--request-id UUID` before the first
attempt. Use `--stdin` to supply a selected body from stdin.

For edits, supply the operation's JSON request:

```sh
cairn agent edit < edit-request.json
```

The request needs the record ID, expected current version, a new request UUID,
and the replacement draft. Body-only operations include `revise`, `append`,
`replace`, and `set-sections`; inspect their help for exact fields. See
[memory capture](../guides/memory-capture.md) and [record history](../record-history.md).

## Coordinate sessions

Use [session coordination](../guides/session-coordination.md) for the full
publish, lease, exact-source read, completion, and response sequence.

```sh
cairn agents list
cairn agents resolve --agent-id AGENT_UUID
cairn inbox next
cairn watch --once
```

The inbox command can claim work; `watch` observes without claiming. Native
integrations usually maintain their own registration and delivery lifecycle.
Use the current session identifiers they supply rather than registering again.

To publish an existing source record to a known inbox:

```sh
cairn publish --request-id REQUEST_UUID --to agent/AGENT_UUID \
  --kind request --version 1 RECORD_UUID
```

Substitute actual UUIDs and the retained source version. Completion requires the
current delivery and lease IDs. Acknowledging a notice and completing a request
are different operations; [event fabric](../agent-event-fabric.md) explains them.

### Inspect pressure and reply obligations

With the same authenticated profile, inspect your own inbox and publications:

```sh
cairn event-pressure --limit 20
cairn event-fanout --after 0 --limit 20
cairn event-admission-preview --topic coordination
cairn closures --state reserved --limit 20
```

Replace `coordination` with the topic you intend to inspect. Preview does not
reserve room or authorize publication. Pressure and closure reads select your
principal or the session given by paired `--agent-id` and `--execution-id`;
they do not offer an arbitrary-recipient selector. Follow `next_after` using
`--after-recipient`, `--after`, or `--after-reservation` for pressure, fanout,
or closures respectively.

When you no longer need a request's reply, inspect the reservation and use `cairn close-replies --request-id REQUEST_UUID --reason
TEXT RESERVATION_UUID:REVISION`. Substitute the returned reservation and revision
and quote a multiword reason. This closes the reply obligation, not the request
delivery. Open response groups and wait notices use their own terminal controls.
See [closure reservations](../closure-reservations.md).

`EVENT_ADMISSION_REFUSED` and `CLOSURE_RESERVE_FULL` exit 4 with typed backoff
information. Preserve the request UUID and intent for retryable refusals; an
impossible burst or closure requirement needs a request or policy change.
Collection policy and diagnostics require the operator path documented in
[structured commands](../commands.md#event-admission-and-closure-administration).

## Configure integrations

`claude-config`, `codex-config`, and `opencode-config` generate configuration;
`opencode-install` installs the native adapter. Start with the relevant
[integration guide](../mcp/overview.md) and its prerequisites.

A generic MCP process with explicit task/run scope is:

```sh
cairn mcp --socket /path/to/api.sock --token-file /path/to/agent.token \
  --repo /path/to/collection --task docs-review --run review-1
```

`--codex-thread` derives scope from supported Codex thread metadata and is
exclusive with explicit `--task` and `--run`. Optional `--tool-profile compact/1`
or `compact/2` changes tool descriptions; `--search-view compact/1` changes result
presentation. Neither option grants access or changes note authority.

## Operate the service

| Task | Starting point |
| --- | --- |
| Build, migrate, start the API | [Deployment](../operator/deployment.md) |
| Configure semantic retrieval | [Semantic discovery](../semantic-discovery.md) |
| Install background services | [Daemon deployment](../operator/daemon-deployment.md) |
| Back up and restore | [Maintenance](../operator/maintenance.md) |
| Enroll another machine | [Multi-machine access](../multi-machine.md) |
| Schedule an event | [Event scheduling](../event-scheduling.md) |
| Configure a worker pool | [Worker pools](../worker-pools.md) |

Check each command's help before applying it to an operational store. Configuration
and recovery commands have different input and authority requirements from
ordinary memory commands.

## Work projection and coordination

Use `cairn work` for shared work view projection from external issue trackers
(such as Plane), linking internal sessions and records, and adding local progress
or blocker annotations without mutating external trackers.

```sh
cairn work project add --slug SLUG --workspace WS_UUID --project PROJ_UUID
cairn work project list
cairn work refresh --project SLUG
cairn work list --project SLUG
cairn work show --project SLUG --item ITEM_UUID
cairn work associate --project SLUG --item ITEM_UUID --target-type session --target-id AGENT_UUID:EXECUTION_UUID
cairn work progress add --project SLUG --item ITEM_UUID --text "Progress update"
cairn work blocker add --project SLUG --item ITEM_UUID --text "Blocker description"
cairn work blocker resolve --project SLUG --item ITEM_UUID --annotation-id ANNOTATION_UUID --expected-revision N
cairn work metadata purge --project SLUG --pattern "sensitive text"
cairn work serve --project SLUG
```

`work serve` launches a protected local loopback server binding `127.0.0.1` with
a single-use terminal launch token exchanged for an HttpOnly SameSite=Strict session
cookie, Host/Origin verification, and CSRF protection.
