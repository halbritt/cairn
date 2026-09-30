#!/usr/bin/env bash
# Own the complete database lifetime; never use an existing Cairn store.
set -euo pipefail
cd "$(dirname "$0")/.."
trial_command=(python3 -B scripts/trial_task_eval.py "$@")
if [[ "${1-}" == -- ]]; then
    shift
    if (( $# == 0 )); then
        printf 'usage: %s -- COMMAND [ARG ...]\n' "$0" >&2
        exit 2
    fi
    trial_command=("$@")
fi
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
trial_db_root="$(mktemp -d /tmp/cairn-task-eval-pg.XXXXXXXX)"
cleanup() {
    local status=$?
    local pg_status=3
    trap - EXIT
    set +e
    if [[ -f "$trial_db_root/data/postmaster.pid" ]]; then
        "$pg_bin/pg_ctl" -D "$trial_db_root/data" status >/dev/null 2>&1
        pg_status=$?
        if (( pg_status == 0 )); then
            "$pg_bin/pg_ctl" -D "$trial_db_root/data" -m immediate -w stop >/dev/null
            "$pg_bin/pg_ctl" -D "$trial_db_root/data" status >/dev/null 2>&1
            pg_status=$?
        fi
    fi
    # Only status 3 proves a checked server is not running. Inaccessible data
    # (4) and command failures leave its state unknown; retain the directory.
    if (( pg_status != 3 )); then
        printf 'cleanup failed: PostgreSQL state unresolved (pg_ctl status %s); retained %s\n' "$pg_status" "$trial_db_root" >&2
        if (( status == 0 )); then status=1; fi
    elif ! rm -rf -- "$trial_db_root"; then
        printf 'cleanup failed: could not remove %s\n' "$trial_db_root" >&2
        if (( status == 0 )); then status=1; fi
    fi
    exit "$status"
}
trap cleanup EXIT
"$pg_bin/initdb" -D "$trial_db_root/data" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >/dev/null
mkdir "$trial_db_root/socket"
"$pg_bin/pg_ctl" -D "$trial_db_root/data" -l "$trial_db_root/postgres.log" \
    -o "-F -k $trial_db_root/socket -c listen_addresses='' -c max_connections=200" -w start >/dev/null
export CAIRN_TASK_EVAL_PG="$trial_db_root/socket" CAIRN_TASK_EVAL_PG_BIN="$pg_bin"
"${trial_command[@]}"
