# Experimental provider debit owner

`internal/experimentaldebit` is an isolated implementation of durable arm,
debit and consume transitions for synthetic provider allocations. It is outside
the core schema and migration path. `New` returns `ErrUnavailable`; this package
does not enable provider execution.

The store computes each transition from locked state. It checks all shared
currency, counter, resource and pacing balances before committing a debit. A
known consume acknowledgment and an original, invocation-local right are
required before entering the transport factory. Status reads cannot recreate
that right. Unknown commits or outcomes close the owner and retain exposure;
they do not trigger a refund, automatic retry or replacement attempt.

Before consuming a pending right, the owner refreshes the authoritative revision
and checks the original identity, attempt, phase, binding and debit. This lets
unrelated valid work advance the ledger without invalidating another pending
right. A mutation after that read still fails the conditional transaction.

Shared balances, retained slots and individual audit events have separate rows.
Metadata pages contain at most 128 rows and 65536 encoded bytes. Individual slot
documents are bounded at 262144 bytes. These limits do not cap total retained
history or campaign attempts. Completed observations can leave the volatile
cache while durable attempt tombstones remain. Prototype body, allocation and
representation limits still require review against an actual workload.

Failures distinguish the primary operation from storage rollback uncertainty,
quarantine-write uncertainty and transport-close uncertainty. An unsuccessful
quarantine write is not reported as a durable quarantine.

## Local validation

Run the ordinary tests without a database:

```sh
go test ./internal/experimentaldebit
make check
```

The `ownerpg` build tag includes the experimental PostgreSQL adapter and its
database tests. Compiling these tests does not execute them:

```sh
go test -tags ownerpg -c -o /tmp/cairn-debit-owner.test ./internal/experimentaldebit
```

Database tests require a separately selected, newly created disposable cluster
root directly below `/tmp`, named `cairn-debit-pg-*`, owned by the current user
with mode 0700. The cluster must expose only its private Unix socket, contain
database `cairn_debit_disposable` and role `cairn_debit_test`, and have `fsync`,
`synchronous_commit` and `full_page_writes` enabled. Its data directory must be
`ROOT/data`. `ROOT/SOURCE-TEST-SELECTION` must contain exactly
`disposable-debit-owner-tests/1` followed by a newline. The test environment must
exclude inherited PostgreSQL and database URL variables. These checks are test
selection interlocks, not authentication against a hostile filesystem. The tests
check root ownership, the selection file, database and durable settings; the
operator must configure the private socket and disable TCP listening.

With that disposable setup explicitly selected, the ordinary database suite is:

```sh
/tmp/cairn-debit-owner.test -test.run '^TestPostgreSQLOwnerContract$' \
  -test.timeout 75s -owner-pg-root ROOT
```

The suite creates its experimental schema and requires a fresh database. It
tests rollback after an audit-write failure, competing shared debits, retained
history beyond one metadata page, lost commit acknowledgment and cooperative
dispatch fencing. The fence test accepts any error from the contending recovery;
passing alone does not identify a lock wait. Stronger ordering claims require
separate evidence of the contending statement, sessions and callback interval.
The cleanup helper attempts a bounded dispatch join and releases the acquired
fence connection before reporting test failures. An unsuccessful join remains
explicit.

The separate crash-stage and after-restart tests require their own externally
controlled experiment. Do not include them in the ordinary suite or treat
client reconstruction or durable settings alone as crash durability evidence.
`make test-integration` validates the repository's ordinary store paths; it does
not select the `ownerpg` tests.

## Remaining integration

Real authentication, qualified allocation and price inputs, funding and holds,
provider transport, restart coordination and stale-sender egress enforcement
remain unavailable. Private adapter interfaces express required contracts;
caller-supplied identities and hashes do not authenticate them. A healthy
cooperating PostgreSQL lock does not make a remote provider effect atomic or
revoke a stale process's network permissions after connection loss.

Synthetic role and conservation tests are not workload calibration, dedicated
evaluation capacity or campaign acceptance. This package does not change the
original evaluation scope, native runtime limits or capture hold.
