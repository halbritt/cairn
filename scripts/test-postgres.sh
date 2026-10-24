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
        go test -race -count=1 -timeout=30m "$test_package"
done < "$test_root/packages"
export CAIRN_TEST_DATABASE_URL="host=$test_root/socket dbname=cairn_test sslmode=disable"
export CAIRN_DATABASE_URL="$CAIRN_TEST_DATABASE_URL"
go run ./cmd/cairn migrate
go run ./cmd/cairn create < fixtures/note.json
go build -o "$test_root/cairn" ./cmd/cairn
for panel_workflow in baseline.single_notice baseline.actionable_response baseline.duplicate_send \
    baseline.small_request_result_reply baseline.concurrent_receivers \
    baseline.routine_burst_urgent_request envelope.routine_burst_urgent_request \
    batch.routine_burst_urgent_request combined.routine_burst_urgent_request \
    baseline.wrong_project_memory envelope.wrong_project_memory \
    batch.wrong_project_memory combined.wrong_project_memory \
    baseline.corrected_note envelope.corrected_note \
    batch.corrected_note combined.corrected_note \
    baseline.meaningful_owner_correction envelope.meaningful_owner_correction \
    batch.meaningful_owner_correction combined.meaningful_owner_correction; do
    panel_case="${panel_workflow#*.}"
    panel_arm="${panel_workflow%%.*}"
    if [[ "$panel_case" == routine_burst_urgent_request ||
          "$panel_case" == wrong_project_memory ||
          "$panel_case" == corrected_note ||
          "$panel_case" == meaningful_owner_correction ]]; then
        panel_database="cairn_panel_${panel_arm}_${panel_case}"
    else
        panel_database="cairn_panel_$panel_case"
    fi
    "$pg_bin/createdb" -h "$test_root/socket" "$panel_database"
    panel_controller_dsn="host=$test_root/socket dbname=$panel_database sslmode=disable"
    CAIRN_DATABASE_URL="$panel_controller_dsn" "$test_root/cairn" migrate
    CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$panel_controller_dsn" CAIRN_DATABASE_URL="$panel_controller_dsn" \
        python3 scripts/check_token_efficiency_panel_controller.py "$test_root/cairn" "$panel_workflow"
done
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_process_crash_child
crash_dsn="host=$test_root/socket dbname=cairn_panel_process_crash_child sslmode=disable"
CAIRN_DATABASE_URL="$crash_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$crash_dsn" CAIRN_DATABASE_URL="$crash_dsn" \
    python3 scripts/check_panel_process_crash_child.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_stale_journal
stale_journal_dsn="host=$test_root/socket dbname=cairn_panel_stale_journal sslmode=disable"
CAIRN_DATABASE_URL="$stale_journal_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$stale_journal_dsn" CAIRN_DATABASE_URL="$stale_journal_dsn" \
    python3 scripts/check_panel_stale_journal.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_native_notice_batch
notice_batch_dsn="host=$test_root/socket dbname=cairn_native_notice_batch sslmode=disable"
CAIRN_DATABASE_URL="$notice_batch_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$notice_batch_dsn" CAIRN_DATABASE_URL="$notice_batch_dsn" \
    python3 scripts/check_native_notice_batch.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_notice_watcher_eligibility
notice_watcher_dsn="host=$test_root/socket dbname=cairn_notice_watcher_eligibility sslmode=disable"
CAIRN_DATABASE_URL="$notice_watcher_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$notice_watcher_dsn" CAIRN_DATABASE_URL="$notice_watcher_dsn" \
    python3 scripts/check_notice_watcher_eligibility.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_bound_compact_notice
bound_compact_dsn="host=$test_root/socket dbname=cairn_bound_compact_notice sslmode=disable"
CAIRN_DATABASE_URL="$bound_compact_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$bound_compact_dsn" CAIRN_DATABASE_URL="$bound_compact_dsn" \
    python3 scripts/check_bound_compact_notice.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_source_forgotten
source_forgotten_dsn="host=$test_root/socket dbname=cairn_panel_source_forgotten sslmode=disable"
CAIRN_DATABASE_URL="$source_forgotten_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$source_forgotten_dsn" CAIRN_DATABASE_URL="$source_forgotten_dsn" \
    python3 scripts/check_panel_source_forgotten.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_required_conflict_group
conflict_dsn="host=$test_root/socket dbname=cairn_panel_required_conflict_group sslmode=disable"
CAIRN_DATABASE_URL="$conflict_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$conflict_dsn" CAIRN_DATABASE_URL="$conflict_dsn" \
    python3 scripts/check_panel_required_conflict_group.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_semantic_unavailable
semantic_unavailable_dsn="host=$test_root/socket dbname=cairn_panel_semantic_unavailable sslmode=disable"
CAIRN_DATABASE_URL="$semantic_unavailable_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$semantic_unavailable_dsn" CAIRN_DATABASE_URL="$semantic_unavailable_dsn" \
    python3 scripts/check_panel_semantic_unavailable.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_memory_lifecycle
memory_lifecycle_dsn="host=$test_root/socket dbname=cairn_panel_memory_lifecycle sslmode=disable"
CAIRN_DATABASE_URL="$memory_lifecycle_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$memory_lifecycle_dsn" CAIRN_DATABASE_URL="$memory_lifecycle_dsn" \
    python3 scripts/check_panel_memory_lifecycle.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_relay_uncertain_response
uncertain_dsn="host=$test_root/socket dbname=cairn_panel_relay_uncertain_response sslmode=disable"
CAIRN_DATABASE_URL="$uncertain_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$uncertain_dsn" CAIRN_DATABASE_URL="$uncertain_dsn" \
    python3 scripts/check_panel_relay_uncertain_response.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_completion_reply_lost
reply_lost_dsn="host=$test_root/socket dbname=cairn_panel_completion_reply_lost sslmode=disable"
CAIRN_DATABASE_URL="$reply_lost_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$reply_lost_dsn" CAIRN_DATABASE_URL="$reply_lost_dsn" \
    python3 scripts/check_panel_completion_reply_lost.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_inbox_resume_disposable
resume_dsn="host=$test_root/socket dbname=cairn_inbox_resume_disposable sslmode=disable"
CAIRN_DATABASE_URL="$resume_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$resume_dsn" CAIRN_DATABASE_URL="$resume_dsn" \
    python3 scripts/check_inbox_resume_disposable.py "$test_root/cairn"
"$pg_bin/createdb" -h "$test_root/socket" cairn_panel_compaction_settlement
compaction_dsn="host=$test_root/socket dbname=cairn_panel_compaction_settlement sslmode=disable"
CAIRN_DATABASE_URL="$compaction_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$compaction_dsn" CAIRN_DATABASE_URL="$compaction_dsn" \
    python3 -B scripts/check_panel_compaction_settlement.py "$test_root/cairn"
python3 scripts/check_agent_events.py "$test_root/cairn" "$test_root/event-home"
python3 scripts/check_delivery_health.py "$test_root/cairn" "$test_root/delivery-health-home"
"$pg_bin/createdb" -h "$test_root/socket" cairn_multi_machine
multi_machine_dsn="host=$test_root/socket dbname=cairn_multi_machine sslmode=disable"
CAIRN_DATABASE_URL="$multi_machine_dsn" "$test_root/cairn" migrate
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_TEST_DATABASE_URL="$multi_machine_dsn" CAIRN_DATABASE_URL="$multi_machine_dsn" \
    python3 scripts/check_multi_machine.py "$test_root/cairn" "$test_root/multi-machine-home"
# Protocol skew against an independently built legacy release (or the
# retained binary in CAIRN_LEGACY_BINARY) and a raised-minimum test build.
"$pg_bin/createdb" -h "$test_root/socket" cairn_protocol_skew
protocol_skew_dsn="host=$test_root/socket dbname=cairn_protocol_skew sslmode=disable"
CAIRN_DATABASE_URL="$protocol_skew_dsn" "$test_root/cairn" migrate
CAIRN_TEST_DATABASE_URL="$protocol_skew_dsn" CAIRN_DATABASE_URL="$protocol_skew_dsn" \
    python3 scripts/check_protocol_skew.py "$test_root/cairn" "$test_root/protocol-skew-home"
# Builds of the parent and the pre-gate feature read and write receipts beside this
# commit's; skips with a notice when their git history is not present.
"$pg_bin/createdb" -h "$test_root/socket" cairn_source_opening_skew
source_opening_skew_dsn="host=$test_root/socket dbname=cairn_source_opening_skew sslmode=disable"
CAIRN_TEST_DATABASE_URL="$source_opening_skew_dsn" CAIRN_DATABASE_URL="$source_opening_skew_dsn" \
    python3 scripts/check_source_opening_gate_skew.py
python3 scripts/check_native_cancel.py "$test_root/cairn" "$test_root/native-cancel-home"
if [[ -n "${XDG_RUNTIME_DIR:-}" ]] && systemctl --user show-environment >/dev/null 2>&1; then
    python3 scripts/check_wakeups.py "$test_root/cairn" "$test_root/wake-home"
    python3 scripts/check_worker_pools.py "$test_root/cairn" "$test_root/pool-home"
    python3 scripts/check_request_controls.py "$test_root/cairn" "$test_root/control-home"
else
    echo 'Wakeup process probe requires a systemd user manager; store wake tests still ran.' >&2
fi
python3 scripts/check_use_report.py "$test_root/cairn"
python3 scripts/check_recall_observation.py "$test_root/cairn" "$test_root/recall-observation-home"
python3 scripts/check-capture.py "$test_root/cairn" "$test_root/capture-home"
python3 scripts/check_capture_observation.py "$test_root/cairn" "$test_root/capture-observation-home"
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
