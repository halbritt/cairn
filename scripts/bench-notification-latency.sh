#!/usr/bin/env bash
# Run only against a new PostgreSQL cluster owned by this invocation.
set -euo pipefail
cd "$(dirname "$0")/.."
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
bench_root="$(mktemp -d /tmp/cairn-notification-bench.XXXXXXXX)"
cleanup() {
    local status=$?
    if [[ -f "$bench_root/data/postmaster.pid" ]]; then
        "$pg_bin/pg_ctl" -D "$bench_root/data" -m immediate -w stop >/dev/null
    fi
    rm -rf -- "$bench_root"
    exit "$status"
}
trap cleanup EXIT
"$pg_bin/initdb" -D "$bench_root/data" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >/dev/null
mkdir "$bench_root/socket"
"$pg_bin/pg_ctl" -D "$bench_root/data" -l "$bench_root/postgres.log" \
    -o "-k $bench_root/socket -c listen_addresses='' -c fsync=on -c synchronous_commit=on" -w start >/dev/null
"$pg_bin/createdb" -h "$bench_root/socket" cairn_notification_bench
settings="$("$pg_bin/psql" -h "$bench_root/socket" -d cairn_notification_bench -Atqc \
    "SELECT current_setting('fsync') || ',' || current_setting('synchronous_commit')")"
[[ "$settings" == "on,on" ]] || { echo "Expected durable PostgreSQL settings, got $settings" >&2; exit 1; }
export CAIRN_DISPOSABLE_BENCH_ROOT="$bench_root"
export CAIRN_TEST_DATABASE_URL="host=$bench_root/socket dbname=cairn_notification_bench sslmode=disable"
export CAIRN_DATABASE_URL="$CAIRN_TEST_DATABASE_URL"
export CAIRN_HOME="$bench_root/home"
mkdir -m 0700 "$CAIRN_HOME"
go build -o "$bench_root/cairn" ./cmd/cairn
go build -o "$bench_root/store-probe" scripts/notification_store_probe.go
"$bench_root/cairn" migrate >/dev/null
python3 -B scripts/bench_notification_latency.py --root "$bench_root" \
    --binary "$bench_root/cairn" --store-probe "$bench_root/store-probe" "$@"
