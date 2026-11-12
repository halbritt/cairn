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

### Lifecycle publication and deadline fixtures

The F-04 state/failure tests and `test_r02_installed_contracts` exercise actual
Go activation, with the latter using the installed R-02 file producer. Their
Linux syscall-fault cases need `strace`, `libseccomp.so.2` and kernel seccomp user
notification support. `scripts/fixture_publication_fault.py` selects actual regular
`.cairn-state-*` file descriptors and parent-directory syncs in the fixture's
exact ledger directory. Renames select their exact destination in `strace`.
Fault ordinals span Go threads; scheduler eventfds and diagnostic writes continue. Missing support fails the
fixture rather than replacing the fault with a simulated result.

The retained-cue test compares the absolute deadline observed by Go with the
retained phase's deadline, then controls that phase's clock to check the actual
pending-lookup timeout against an eighth-second remainder. The fixture repins its
test-only observer through its selected source producer. The public parent's
timer and subprocess clocks stay real; separate parent-timer cases cover the
full trusted hook budgets. This avoids requiring process startup within the
last 400 milliseconds of a two-second deadline on a loaded host.

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

For staged hooks in a disposable native Claude client, use the
[native Claude hook fixture](native-claude-hook-fixture.md). It checks cached
selections, receipt refusals and same-conversation resume through the existing
hybrid-runtime isolation wrapper.

For staged hooks and native trust in a disposable Codex app-server, use the
[native Codex hook fixture](native-codex-hook-fixture.md). It checks cached
selections, receipt refusals and archive/unarchive/resume with the same thread
UUID and history, while control threads keep their original selections.

For receipt-checked coordination in a disposable native Agy CLI, use the
[native Agy hook fixture](native-agy-hook-fixture.md). It checks project-layer
PreInvocation/Stop selection, same-conversation continuity, receipt refusals
and reversal with raw kernel evidence.

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

The retained real-registry assertion uses a fresh private build supplied
through its test's `build_cairn` helper. The retained
`scripts/test_claude_channel.py` assertions stay unchanged; the wrapper neither
uses nor clears the UID-shared temporary binary cache. Other retained Claude
client cases use Python fake peers. Frame cases include an independent literal
8192-byte success, valid-JSON 8193-byte refusal and an oversized open sender
without a newline; the latter must refuse by byte bound before the deadline.

Image fixtures distinguish an in-process root-owned pin refusal from executing
a root-owned image. Positive temporary staging covers `/tmp` and `/var/tmp`;
`/dev/shm` and other-writable nonsticky ancestors are negative descriptor-walk
cases. Missing host conditions remain explicit skips. The descriptor-metadata
case blocks a pipe while changing metadata; it does not mutate a live ELF.
There is no deterministic rename-during-hashing case. Foreign-owned ancestors,
temporary root-owner/sticky conditions, root invocation, real PID reuse, reboot
and X process state remain unprivileged or unexercised gaps. Inactive helper
entrypoints do not count as asserting tests.

The binary primitive does not check `/` as an ancestor or reject noncanonical
path spellings. Release requires canonical persistent production placement;
production pins never use the temporary-ancestor exception. Python credential
mismatch cases exercise the verifier directly; the killed-owned-bridge case
exercises the retained verify-before-send path. None of these historical
fixture descriptions claims that the rebased candidate has passed them.

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
