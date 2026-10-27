# Disposable fixture hosts

Integration fixtures under `scripts/check_*.py` need a private PostgreSQL
database, an owner-only home with agent identities, a running local API, one
way to read CLI envelopes, and clean teardown. `scripts/disposable_host.py`
owns that implementation. A fixture keeps its scenario and assertions; the
module's interface is the fixture's test surface.

## Interface

- `ProvidedCluster.from_environment()` selects the cluster that
  `scripts/test-postgres.sh` provisioned (`CAIRN_DISPOSABLE_TEST_ROOT`): an
  owner-only `/tmp/cairn-postgres.*` directory with an initialised data
  directory and a socket directory. Anything else is refused. `cluster.binary()`
  is the binary built beside it.
- `OwnedCluster.start(root)` initialises and starts a cluster for a fixture that
  runs outside the runner. `stop()` reports whether the server was observed
  stopped; `remove()` deletes the directory only then.
- `ProvidedDatabase.from_environment()` names an already migrated disposable
  database through `CAIRN_TEST_DATABASE_URL` and `CAIRN_DATABASE_URL`, which
  must be equal. Shared-database fixtures and `scripts/test-local-lifecycle.sh`
  use it.
- `DisposableHost.start(source, repo=..., ...)` creates and migrates the
  database (`cairn_<fixture label>` unless named), stages one owner-only token
  per role with `identities.json`, starts `cairn serve` and waits until the
  socket accepts. `host.agent(role, operation, body)`,
  `host.operator(command, body)` and `host.command(argv, body)` read one
  `cairn.response/1` envelope: bounded, one JSON value without duplicate keys,
  `ok`, `status` and the exit code in agreement, and the expected status.
  `expected` names a refusal to receive the whole envelope; `expected=None`
  accepts any status. `host.sql(statement)` runs one bounded psql statement and
  `host.require_fresh(table, ...)` refuses a reused database.
  `host.restart_server()` and `host.close()` touch only the processes the host
  started; `close()` returns the server's exit code.
- `HookOwner.start(script, config, env)` runs lifecycle hooks in one long-lived
  synthetic owner process, one event per line (`send`, `hook`, `compact`).
- Startup failures carry the fixed classes `classify_pg_start_failure` and
  `classify_api_start_output` that the Codex wake-join fixture introduced; that
  fixture now imports them from here.

## Runner

`scripts/test-postgres.sh` provisions one cluster, runs the Go package suites,
builds the binary, then runs each fixture. A `disposable` fixture receives the
root and the binary and owns its database; a `shared` fixture uses `cairn_test`
through the exported DSN. The panel controller lists its workflows
(`token_efficiency_panel_controller.py --list-workflows`) and names each
database (`database_name`), so the runner holds no second registry.
`CAIRN_TEST_ONLY='<glob> ...'` runs only the matching fixtures and skips the Go
suites while a fixture is being developed.

## Boundaries

The module never uses an existing store: a provided root must be a fresh
private cluster, an owned cluster is created by the fixture, and a provided
database must be the test database named twice. `TrialStore` in
`scripts/trial_task_eval.py` keeps its own cluster routing and delegates
identity staging, API startup and teardown here. Fixtures that drive several
services (`check_multi_machine.py`, `check_panel_relay_uncertain_response.py`,
`check_protocol_skew.py`, `check_api_version_skew.py`) keep their own service
helpers for now.
