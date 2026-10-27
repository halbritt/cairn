#!/usr/bin/env bash
# Start only a disposable cluster owned by this invocation. No shared DB touched.
#
# Python fixtures run through scripts/disposable_host.py: a disposable fixture
# creates and migrates its own database (named after the fixture) inside this
# cluster, so adding a fixture is one line below. Shared-database fixtures keep
# using cairn_test through the exported DSN.
#
# CAIRN_TEST_ONLY='<glob> ...' runs only the Python fixtures whose script stem
# matches one of the space-separated globs (for example
# CAIRN_TEST_ONLY='check_panel_* check_inbox_resume_disposable') and skips the
# Go package suites; use it while developing fixtures.
set -euo pipefail
cd "$(dirname "$0")/.."
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
test_root="$(mktemp -d /tmp/cairn-postgres.XXXXXXXX)"
only="${CAIRN_TEST_ONLY:-}"
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
if [[ -z "$only" ]]; then
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
            go test -race -count=1 -timeout=30m "$test_package"
    done < "$test_root/packages"
fi
export CAIRN_TEST_DATABASE_URL="host=$test_root/socket dbname=cairn_test sslmode=disable"
export CAIRN_DATABASE_URL="$CAIRN_TEST_DATABASE_URL"
go run ./cmd/cairn migrate
go run ./cmd/cairn create < fixtures/note.json
go build -o "$test_root/cairn" ./cmd/cairn
selected() {
    [[ -z "$only" ]] && return 0
    local stem pattern
    stem="$(basename "$1" .py)"
    for pattern in $only; do
        [[ "$stem" == $pattern ]] && return 0
    done
    return 1
}
# A disposable fixture receives the cluster root and the built binary; it owns
# its database, home, identities and API through scripts/disposable_host.py.
disposable() {
    local script=$1
    shift
    selected "$script" || return 0
    CAIRN_DISPOSABLE_TEST_ROOT="$test_root" python3 -B "$script" "$test_root/cairn" "$@"
}
# A shared fixture uses the migrated cairn_test database through the exported DSN.
shared() {
    local script=$1
    shift
    selected "$script" || return 0
    python3 -B "$script" "$@"
}
while IFS= read -r panel_workflow; do
    disposable scripts/check_token_efficiency_panel_controller.py "$panel_workflow"
done < <(python3 -B scripts/token_efficiency_panel_controller.py --list-workflows)
disposable scripts/check_panel_process_crash_child.py
disposable scripts/check_panel_stale_journal.py
disposable scripts/check_native_notice_batch.py
disposable scripts/check_notice_watcher_eligibility.py
disposable scripts/check_bound_compact_notice.py
disposable scripts/check_panel_source_forgotten.py
disposable scripts/check_panel_required_conflict_group.py
disposable scripts/check_panel_semantic_unavailable.py
disposable scripts/check_panel_memory_lifecycle.py
disposable scripts/check_panel_relay_uncertain_response.py
disposable scripts/check_panel_completion_reply_lost.py
disposable scripts/check_inbox_resume_disposable.py
disposable scripts/check_panel_compaction_settlement.py
shared scripts/check_agent_events.py "$test_root/cairn" "$test_root/event-home"
shared scripts/check_delivery_health.py "$test_root/cairn" "$test_root/delivery-health-home"
disposable scripts/check_multi_machine.py "$test_root/multi-machine-home"
if selected scripts/check_protocol_skew.py; then
    # Protocol skew against an independently built legacy release (or the
    # retained binary in CAIRN_LEGACY_BINARY) and a raised-minimum test build.
    "$pg_bin/createdb" -h "$test_root/socket" cairn_protocol_skew
    protocol_skew_dsn="host=$test_root/socket dbname=cairn_protocol_skew sslmode=disable"
    CAIRN_DATABASE_URL="$protocol_skew_dsn" "$test_root/cairn" migrate
    CAIRN_TEST_DATABASE_URL="$protocol_skew_dsn" CAIRN_DATABASE_URL="$protocol_skew_dsn" \
        python3 -B scripts/check_protocol_skew.py "$test_root/cairn" "$test_root/protocol-skew-home"
fi
if selected scripts/check_source_opening_gate_skew.py; then
    # Builds of the parent and the pre-gate feature read and write receipts beside this
    # commit's; skips with a notice when their git history is not present.
    "$pg_bin/createdb" -h "$test_root/socket" cairn_source_opening_skew
    source_opening_skew_dsn="host=$test_root/socket dbname=cairn_source_opening_skew sslmode=disable"
    CAIRN_TEST_DATABASE_URL="$source_opening_skew_dsn" CAIRN_DATABASE_URL="$source_opening_skew_dsn" \
        python3 -B scripts/check_source_opening_gate_skew.py
fi
shared scripts/check_native_cancel.py "$test_root/cairn" "$test_root/native-cancel-home"
if [[ -n "${XDG_RUNTIME_DIR:-}" ]] && systemctl --user show-environment >/dev/null 2>&1; then
    shared scripts/check_wakeups.py "$test_root/cairn" "$test_root/wake-home"
    shared scripts/check_worker_pools.py "$test_root/cairn" "$test_root/pool-home"
    shared scripts/check_request_controls.py "$test_root/cairn" "$test_root/control-home"
else
    echo 'Wakeup process probe requires a systemd user manager; store wake tests still ran.' >&2
fi
shared scripts/check_use_report.py "$test_root/cairn"
shared scripts/check_recall_observation.py "$test_root/cairn" "$test_root/recall-observation-home"
shared scripts/check-capture.py "$test_root/cairn" "$test_root/capture-home"
shared scripts/check_capture_observation.py "$test_root/cairn" "$test_root/capture-observation-home"
shared scripts/check-proposal-groups.py "$test_root/cairn" "$test_root/proposal-groups-home"
shared scripts/check-local-api.py "$test_root/cairn" "$test_root/api-home"
if [[ -n "${CAIRN_SEMANTIC_WORKER:-}" ]]; then
    python3 -B scripts/check_semantic_api.py --binary "$test_root/cairn" \
        --worker "$CAIRN_SEMANTIC_WORKER" \
        --output "${CAIRN_SEMANTIC_REPORT:-$test_root/semantic-api}"
fi
if [[ -n "${CAIRN_SEMANTIC_STREAM_WORKER:-}" ]]; then
    semantic_baseline=()
    if [[ -n "${CAIRN_SEMANTIC_BASELINE_WORKER:-}" ]]; then
        semantic_baseline=(--baseline-worker "$CAIRN_SEMANTIC_BASELINE_WORKER")
    fi
    python3 -B scripts/check_semantic_api.py --binary "$test_root/cairn" --stream \
        --worker "$CAIRN_SEMANTIC_STREAM_WORKER" "${semantic_baseline[@]}" \
        --output "${CAIRN_SEMANTIC_STREAM_REPORT:-$test_root/semantic-stream-api}"
fi
if [[ -n "${CAIRN_OPENCODE_BINARY:-}" ]]; then
    python3 -B scripts/probe-opencode.py "$CAIRN_OPENCODE_BINARY" --cairn-binary "$test_root/cairn" --check-edit --disable-thinking --output "${CAIRN_PROBE_OUTPUT:-$test_root/opencode-probe.json}"
fi
