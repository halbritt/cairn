#!/usr/bin/env bash
# Run the disposable watcher probe; real-clock legs are explicit opt-in.
set -euo pipefail
cd "$(dirname "$0")/.."
python_args=()
if [[ "${1:-}" == "--real-clock-pilot" ]]; then
    python_args+=(--real-clock-pilot)
elif [[ "${1:-}" == "--multi-arrival-pilot" ]]; then
    python_args+=(--multi-arrival-pilot)
elif [[ $# -gt 0 ]]; then
    echo "usage: run_notice_watcher_probe.sh [--real-clock-pilot|--multi-arrival-pilot]" >&2
    exit 2
fi
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
test_root="$(mktemp -d /tmp/cairn-postgres.XXXXXXXX)"
cleanup() {
    local status=$?
    if [[ -f "$test_root/data/postmaster.pid" ]]; then
        if ! "$pg_bin/pg_ctl" -D "$test_root/data" -m immediate -w stop >/dev/null; then
            echo "Could not stop the pilot database; artifacts retained at $test_root" >&2
            exit 1
        fi
    fi
    if [[ "$status" -eq 0 ]]; then
        rm -rf -- "$test_root"
    else
        echo "Real-clock pilot failed (exit $status); artifacts retained at $test_root" >&2
    fi
    exit "$status"
}
trap cleanup EXIT

"$pg_bin/initdb" -D "$test_root/data" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >/dev/null
mkdir "$test_root/socket"
"$pg_bin/pg_ctl" -D "$test_root/data" -l "$test_root/postgres.log" \
    -o "-F -k $test_root/socket -c listen_addresses=''" -w start >/dev/null
"$pg_bin/createdb" -h "$test_root/socket" cairn_notice_watcher_eligibility
dsn="host=$test_root/socket dbname=cairn_notice_watcher_eligibility sslmode=disable"
CAIRN_DATABASE_URL="$dsn" go run ./cmd/cairn migrate
go build -o "$test_root/cairn" ./cmd/cairn
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" \
CAIRN_TEST_DATABASE_URL="$dsn" \
CAIRN_DATABASE_URL="$dsn" \
    python3 scripts/check_notice_watcher_eligibility.py "${python_args[@]}" "$test_root/cairn"
