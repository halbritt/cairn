#!/usr/bin/env bash
# Start only a disposable cluster owned by this invocation. No shared DB touched.
set -euo pipefail
cd "$(dirname "$0")/.."
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
test_root="$(mktemp -d /tmp/cairn-postgres.XXXXXXXX)"
cleanup() {
    local check_status=$?
    if [[ -f "$test_root/data/postmaster.pid" ]]; then
        if ! "$pg_bin/pg_ctl" -D "$test_root/data" -m immediate -w stop >/dev/null; then
            echo "Could not stop the test database; artifacts retained at $test_root" >&2
            exit 1
        fi
    fi
    if [[ "$check_status" -eq 0 ]]; then
        rm -rf -- "$test_root"
    else
        echo "Integration check failed (exit $check_status); artifacts retained at $test_root" >&2
    fi
    exit "$check_status"
}
trap cleanup EXIT
"$pg_bin/initdb" -D "$test_root/data" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >/dev/null
mkdir "$test_root/socket"
"$pg_bin/pg_ctl" -D "$test_root/data" -l "$test_root/postgres.log" -o "-F -k $test_root/socket -c listen_addresses=''" -w start >/dev/null
"$pg_bin/createdb" -h "$test_root/socket" cairn_test
# Package suites are independent consumers, not one shared database workload.
# Keep their explicit race tests while preventing unrelated package writes from
# exhausting bounded Serializable retries in another package's setup.
go list ./... > "$test_root/packages"
test_db_number=0
while IFS= read -r test_package; do
    test_db_number=$((test_db_number + 1))
    test_database="cairn_package_$test_db_number"
    "$pg_bin/createdb" -h "$test_root/socket" "$test_database"
    test_dsn="host=$test_root/socket dbname=$test_database sslmode=disable"
    CAIRN_TEST_DATABASE_URL="$test_dsn" CAIRN_DATABASE_URL="$test_dsn" \
        go test -race -count=1 "$test_package"
done < "$test_root/packages"
export CAIRN_TEST_DATABASE_URL="host=$test_root/socket dbname=cairn_test sslmode=disable"
export CAIRN_DATABASE_URL="$CAIRN_TEST_DATABASE_URL"
go run ./cmd/cairn migrate
go run ./cmd/cairn create < fixtures/note.json
go build -o "$test_root/cairn" ./cmd/cairn
python3 scripts/check_agent_events.py "$test_root/cairn" "$test_root/event-home"
python3 scripts/check_native_cancel.py "$test_root/cairn" "$test_root/native-cancel-home"
python3 scripts/check_hermes_control.py "$test_root/cairn" "$test_root/hermes-control-home"
if [[ -n "${XDG_RUNTIME_DIR:-}" ]] && systemctl --user show-environment >/dev/null 2>&1; then
    python3 scripts/check_wakeups.py "$test_root/cairn" "$test_root/wake-home"
    python3 scripts/check_worker_pools.py "$test_root/cairn" "$test_root/pool-home"
    python3 scripts/check_request_controls.py "$test_root/cairn" "$test_root/control-home"
else
    echo 'Wakeup process probe requires a systemd user manager; store wake tests still ran.' >&2
fi
python3 scripts/check_use_report.py "$test_root/cairn"
python3 scripts/check-capture.py "$test_root/cairn" "$test_root/capture-home"
python3 scripts/check-proposal-groups.py "$test_root/cairn" "$test_root/proposal-groups-home"
python3 scripts/check-local-api.py "$test_root/cairn" "$test_root/api-home"
if [[ -n "${CAIRN_SEMANTIC_WORKER:-}" ]]; then
    python3 scripts/check_semantic_api.py --binary "$test_root/cairn" \
        --worker "$CAIRN_SEMANTIC_WORKER" \
        --output "${CAIRN_SEMANTIC_REPORT:-$test_root/semantic-api}"
fi
if [[ -n "${CAIRN_SEMANTIC_STREAM_WORKER:-}" ]]; then
    semantic_baseline=()
    if [[ -n "${CAIRN_SEMANTIC_BASELINE_WORKER:-}" ]]; then
        semantic_baseline=(--baseline-worker "$CAIRN_SEMANTIC_BASELINE_WORKER")
    fi
    python3 scripts/check_semantic_api.py --binary "$test_root/cairn" --stream \
        --worker "$CAIRN_SEMANTIC_STREAM_WORKER" "${semantic_baseline[@]}" \
        --output "${CAIRN_SEMANTIC_STREAM_REPORT:-$test_root/semantic-stream-api}"
fi
if [[ -n "${CAIRN_OPENCODE_BINARY:-}" ]]; then
    python3 scripts/probe-opencode.py "$CAIRN_OPENCODE_BINARY" --cairn-binary "$test_root/cairn" --check-edit --disable-thinking --output "${CAIRN_PROBE_OUTPUT:-$test_root/opencode-probe.json}"
fi
