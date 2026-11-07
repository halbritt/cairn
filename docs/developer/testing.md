# Testing

Run Cairn's validation locally. Database tests use disposable PostgreSQL clusters;
never set a test DSN to an operational database. The [Makefile](../../Makefile)
and test scripts define the checks that run for this checkout.

## Choose the required checks

| Change | Checks |
| --- | --- |
| Any Go change | `make check` and relevant package tests |
| Store or database behavior | `make test-integration` and `make check` |
| Backup, restore, or lifecycle recovery | `make test-lifecycle` plus affected integration tests |
| Python lifecycle hooks | Relevant Python tests and affected native integration fixtures |
| Inactive hybrid runtime operations | `make test-hybrid-runtime-operations` |
| OpenCode adapter | Node checks and affected integration fixtures |
| Documentation | `python3 scripts/verify_docs.py` plus manual source/example review |

`make check` runs Go vet, generated API contract comparison, Go formatting checks,
and pinned design-source validation. It does not run the test suites or the
separate documentation checker.

## Run database integration tests

```sh
make test-integration
```

The target runs `scripts/test-postgres.sh`, starts a private PostgreSQL cluster,
and assigns separate databases to Go packages. Go integration tests run with the
race detector. The script stops the cluster on exit; failed runs retain artifacts
and print their location for diagnosis. See [disposable fixtures](../disposable-fixtures.md)
for the fixture contract.

To work on a particular Python integration fixture:

```sh
CAIRN_TEST_ONLY='check_inbox_resume_disposable' make test-integration
```

The selector matches script stems without `.py`, with space-separated shell
globs permitted. This focused mode skips the Go package suites and does not
replace the full required integration run.

`go test ./...` and `make test` skip database coverage unless
`CAIRN_TEST_DATABASE_URL` is supplied. Prefer the integration target, which
creates and cleans up its own database.

### Keep CLI and fixture defaults isolated

The integration runner passes both `CAIRN_TEST_DATABASE_URL` and
`CAIRN_DATABASE_URL` with the same disposable DSN to each Go package. The first
selects integration-test stores; the second selects ordinary CLI store access.
Setting only the test variable is insufficient for standalone CLI subprocesses.

`cmd/cairn/testmain_test.go` creates a private `CAIRN_HOME` before CLI tests run,
so default sockets and tokens cannot resolve to the installed profile. It
replaces an inherited `CAIRN_DATABASE_URL` with the test DSN, or a private absent
socket when no test DSN exists. This guard is part of the test binary; it does
not protect a separately built CLI. For a manual database probe, explicitly set
both DSNs to a database you created for that probe and use a private
`CAIRN_HOME`. Do not rely on inherited credentials or the local-store default.

Python fixtures using `DisposableHost` receive the same pair of DSNs and a
private home. `ProvidedDatabase` checks DSN equality, not whether a database is
actually disposable; the caller must provision it. See the
[fixture interfaces](../disposable-fixtures.md) before adding a subprocess.

### Event admission coverage

Event admission changes require the full store checks above. The relevant
integration suites cover these boundaries:

| Boundary | Source tests |
| --- | --- |
| Scoped pressure, fanout snapshots, policy revisions | `core/event_admission_test.go` |
| Atomic admission, concurrent capacity, validated replies | `core/event_admission_enforcement_test.go` |
| Closure lifetime, visibility, reconciliation and restore generations | `core/agent_closure_reservations_test.go`, `core/agent_closure_adversarial_test.go` |
| Durable rate balances, owner identity, refill and rollback | `core/event_admission_rates_test.go` |
| Scheduler, stopped notices, quiet-notice and pool transfers | `core/event_admission_alternate_test.go`, `core/event_admission_transfers_test.go` |
| Disabled/re-enabled policy and migration barriers | `core/event_admission_disable_test.go`, `core/event_admission_migration_test.go` |
| HTTP/CLI refusal details and committed scheduler progress after diagnostic failure | `localapi/event_admission_transport_test.go`, `cmd/cairn/event_admission_transport_test.go`, `cmd/cairn/schedule_serve_admission_test.go` |

Use `nativeFixture` in `core/native_fixture_test.go` for existing-session store
scenarios. It centralizes registration, publication, native claim, operator
access and explicit time/state changes; scenario assertions remain in their
tests. It simulates store state and does not establish live harness delivery.
These suites test bounded contracts, not production throughput or better agent
outcomes. Check the [rollout guide](../operator/event-admission-rollout.md) for
upgrade requirements distinct from passing tests.

## Run unit and adapter checks

```sh
make test
```

This runs Go tests, Python unit discovery, and the SDK spawn/IPC Node checks.
Node-based checks require Node.js 24 or newer. Python checks involving native
Codex queue connections need `websocket-client`. Model-report checks need
`tiktoken==0.12.0`; an isolated invocation is:

```sh
uv run --with tiktoken==0.12.0 python -m unittest discover -s scripts -p 'test_*model*.py'
```

For the OpenCode adapter:

```sh
node scripts/check_opencode_recent_files.mjs
node scripts/check_opencode_lifecycle.mjs
```

### Shared Go lifecycle primitives

The Go CLI's state-file wrapper and Claude channel bridge use the shared
`internal/statefile` and `internal/procpeer` packages. The Go wake library also
uses the verified Unix client functions in `procpeer`.

| Test source | Boundary |
| --- | --- |
| `internal/statefile/file_test.go` and `crash_test.go` | State publication, propagated I/O errors and owned-child crashes at existing write boundaries; these do not simulate power loss. |
| `internal/procpeer/frame_test.go` | The retained bridge's 8192-byte request payload bound and two-second server deadlines. |
| `internal/procpeer/client_test.go` | Client credential/identity checks before payload, caller-selected timing, line bounds and a cumulative connection read budget, using Go-only private fixtures. |
| `internal/procpeer/process_test.go` | Measurement identity and the separate Go/Python zombie policies; the zombie comparison invokes the retained Python helper. |
| `internal/claudechannel/claudechannel_test.go` | Retained bridge protocol, publication, ownership and refusal behavior. |
| `internal/lifecycle/binding/binary_linux_test.go` | Staged image ownership, safe paths, digest/inode identity, deleted/replaced images and descriptor metadata changes. |
| `scripts/test_foundation_f01_dispatch.py` | Retained Python client against the real Claude bridge and checked-image fixtures. |

Select the tests for the affected consumer explicitly when a Go-only check is
required. Client tests have the `TestClient` prefix; the zombie comparison is a
separate test with a Python dependency. Socket fixtures use private temporary
paths; process-control fixtures act only on children they created. These checks
cover source behavior. Installed lifecycle selection and runtime qualification
remain separate.

F-01 process and image fixtures require Go and Python and act only on their own staged files and child processes. They establish source behavior when executed; they do not qualify an installed lifecycle selection. Campaign tests await the coordinator-approved isolation wrapper that hides production homes, sockets, network and host processes. Build and vet results alone are not parity acceptance.

## Exercise backup and recovery

```sh
make test-lifecycle
```

This builds the CLI and runs `scripts/test-local-lifecycle.sh` against disposable
state. It exercises startup, backup/restore, recovery expectations, replay,
recompilation, and invalidation of restored handles. Passing this test does not
validate a particular deployment's backup location or recovery catalog.

## Check documentation

```sh
python3 scripts/verify_docs.py
python3 scripts/verify_docs.py --tier 1,3
```

The checker scans links and anchors, known CLI syntax, fenced JSON and shell
syntax, and the presence of required guides. It uses heuristics; it does not
execute examples or prove their semantics. [Documentation validation](../../TEST_INFRA.md)
explains coverage and limitations. Dated reports in
[verification](../verification/README.md) retain historical evidence, not a live
pass/fail status.
