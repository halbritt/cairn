#!/usr/bin/env bash
# Own the complete database lifetime; never use an existing Cairn store.
set -euo pipefail
cd "$(dirname "$0")/.."
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
trial_db_root="$(mktemp -d /tmp/cairn-agent-env-pg.XXXXXXXX)"
cleanup() {
    if [[ -f "$trial_db_root/data/postmaster.pid" ]]; then
        "$pg_bin/pg_ctl" -D "$trial_db_root/data" -m immediate -w stop >/dev/null
    fi
    rm -rf -- "$trial_db_root"
}
trap cleanup EXIT
"$pg_bin/initdb" -D "$trial_db_root/data" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >/dev/null
mkdir "$trial_db_root/socket"
"$pg_bin/pg_ctl" -D "$trial_db_root/data" -l "$trial_db_root/postgres.log" \
    -o "-F -k $trial_db_root/socket -c listen_addresses=''" -w start >/dev/null
"$pg_bin/createdb" -h "$trial_db_root/socket" cairn_test
export CAIRN_TEST_DATABASE_URL="host=$trial_db_root/socket dbname=cairn_test sslmode=disable"
python3 -B scripts/trial_agent_env.py "$@"
