#!/usr/bin/env bash
# Provision a disposable PostgreSQL cluster and run the database-backed tests against it.
set -euo pipefail
pg="$(ls -d /usr/lib/postgresql/*/bin | sort -V | tail -1)"
root="$(mktemp -d)"
cleanup() {
    "$pg/pg_ctl" -D "$root/data" -m immediate -w stop >/dev/null 2>&1 || true
    rm -rf "$root"
}
trap cleanup EXIT
"$pg/initdb" -D "$root/data" -U postgres --auth-local=trust --no-locale -E UTF8 >/dev/null
"$pg/pg_ctl" -D "$root/data" -l "$root/postgres.log" \
    -o "-k $root -c listen_addresses='' -c dynamic_shared_memory_type=mmap" -w start >/dev/null
"$pg/createdb" -h "$root" -U postgres cairn_test
CAIRN_TEST_DATABASE_URL="host=$root user=postgres dbname=cairn_test" go test -count=1 ./...
