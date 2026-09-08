# Authenticated note capture in everyday use

`cairn agent remember` now saves ordinary notes through the existing authenticated
`create` API. It shares parsing with operator `remember`; both support explicit
`--stdin` for a selected multiline body. An ordinary agent profile produces A
testimony under its provisioned identity. Scope, retry identity, validation and
record semantics remain server-owned. No schema, authority or ranking change was
needed.

Code `6a21da9f4a2b587ecca49b4ad22077a077acea44` is installed. Full local
`make test-integration` and `make check` passed, as did all 17 Python tests and
[code CI 34269006799](https://github.com/halbritt/cairn/actions/runs/34269006799).
The [usage examples](../local-api.md#save-an-ordinary-note) cover argument text,
stdin, explicit shareability and retry IDs.

## What was exercised

- A real Unix HTTP tracer test checked nested create JSON and unchanged
  server-owned identity/class, literal quotes and Unicode, while HOME/CAIRN_HOME
  were empty and the operator DSN was unusable.
- Input tests cover preserved line breaks, explicit scopes, local sensitivity by
  default, mixed-source refusal, oversized/blank/invalid UTF-8 input and rejected
  identity/destination flags.
- The disposable PostgreSQL/API check saved through an ordinary agent, retried
  the same request without another record, and rejected changed intent. A second
  ordinary hosted agent independently used the same UUID and retained its own
  writer identity.
- That second client searched and pulled the first writer's shareable note in a
  later task/run scope. Local-only notes stayed excluded. Out-of-profile capture
  and attempted instruction creation were rejected. Operator stdin capture also
  preserved its own identity and exact text.

One initial fixture assertion expected two identical bodies in the search index.
That contradicted existing duplicate-body suppression; the fixture now uses
separate notes. No ranking behavior was changed to satisfy it.

## Operational use and deployment

The first hosted-destination search in this coding session returned no eligible
records. A selected, previously verified connection lesson was then retained as
a shareable A note under the local agent profile and retrieved before the command
implementation. The first raw `create` attempt used the wrong JSON shape and was
rejected; correcting the nested `draft` saved the note. That concrete error is
one reason the convenience command is useful.

A separate ordinary hosted-agent profile is now provisioned for the canonical
Cairn repository. Existing local/observer profiles were preserved. The API was
restarted with the clean CI-tested binary to load that profile; both services
are active, and existing record versions were unchanged by deployment.
[AGENTS.md](../../AGENTS.md) describes the provisioned route, including using the
canonical repository identity from a worktree and verifying retrieved A notes
against current source.

This Codex session then used the hosted profile's actual `search` and generated
`pull` command to read the earlier local writer's shareable note. It saved a chosen
procedure with `agent remember --stdin`, repeated the identical request and got
the same record, then searched and pulled that procedure. Both notes remain
ordinary, unpromoted A testimony in the operational store. Capture succeeded
with HOME/CAIRN_HOME absent and an unusable operator database address.

This is actual operational capture, persistence and retrieval through two
provisioned identities, exercised by the current coding agent. It is not evidence
of two independent models helping one another, automatic learning, causal memory
benefit or native Striatum integration. The earlier lesson was already known to
this session; no independently observed host run or performance comparison is
claimed. No new model experiment was launched.

Exact binary/profile state, checks and private evidence pointers are in the
[verification data](agent-remember-2026-09-08.json). Operational note bodies,
record IDs and request/receipt IDs remain in the private `/tmp/cairn-agent-remember-*`
artifacts and the store, outside Git. The user-facing route is now available for
subsequent hosted coding work; the wider roadmap remains open.
