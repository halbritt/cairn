#!/usr/bin/env bash
# Start only a disposable cluster owned by this invocation. No shared DB touched.
set -euo pipefail
cd "$(dirname "$0")/.."
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
test_root="$(mktemp -d /tmp/cairn-postgres.XXXXXXXX)"
cleanup() {
    if [[ -f "$test_root/data/postmaster.pid" ]]; then
        "$pg_bin/pg_ctl" -D "$test_root/data" -m immediate -w stop >/dev/null
    fi
    rm -rf -- "$test_root"
}
trap cleanup EXIT
"$pg_bin/initdb" -D "$test_root/data" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >/dev/null
mkdir "$test_root/socket"
"$pg_bin/pg_ctl" -D "$test_root/data" -l "$test_root/postgres.log" -o "-F -k $test_root/socket -c listen_addresses=''" -w start >/dev/null
"$pg_bin/createdb" -h "$test_root/socket" cairn_test
export CAIRN_TEST_DATABASE_URL="host=$test_root/socket dbname=cairn_test sslmode=disable"
export CAIRN_DATABASE_URL="$CAIRN_TEST_DATABASE_URL"
go test -race -count=1 ./...
go run ./cmd/cairn migrate
go run ./cmd/cairn create < fixtures/note.json
go build -o "$test_root/cairn" ./cmd/cairn
python3 scripts/check-capture.py "$test_root/cairn" "$test_root/capture-home"
if [[ -n "${CAIRN_OPENCODE_BINARY:-}" ]]; then
    python3 scripts/probe-opencode.py "$CAIRN_OPENCODE_BINARY" --cairn-binary "$test_root/cairn" --output "${CAIRN_PROBE_OUTPUT:-$test_root/opencode-probe.json}"
fi
