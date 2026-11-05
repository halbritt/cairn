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
CAIRN_TEST_ONLY='check_fixture_name.py' make test-integration
```

Replace the pattern with an existing fixture name. This focused mode skips the
Go package suites and does not replace the full required integration run.

`go test ./...` and `make test` skip database coverage unless
`CAIRN_TEST_DATABASE_URL` is supplied. Prefer the integration target, which
creates and cleans up its own database.

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
