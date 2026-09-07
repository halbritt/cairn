#!/usr/bin/env bash
# Owns only Cairn's dedicated cluster. Never redirects to the host PostgreSQL.
set -euo pipefail
umask 077
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
store_dir="${CAIRN_HOME:-$HOME/.local/share/cairn}"
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
case "$store_dir" in /*) ;; *) printf '%s\n' 'CAIRN_HOME must be absolute' >&2; exit 2;; esac
if [[ "$store_dir" == *"'"* || "$store_dir" == *$'\n'* || "$store_dir" == *' '* ]]; then
    printf '%s\n' 'The local cluster directory cannot contain quotes, spaces or newlines' >&2
    exit 2
fi
mkdir -p "$store_dir"
chmod 700 "$store_dir"
exec 9>"$store_dir/lifecycle.lock"
flock -w 15 9
case "${1:-status}" in
start)
    if [[ ! -f "$store_dir/data/PG_VERSION" ]]; then
        "$pg_bin/initdb" -D "$store_dir/data" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >"$store_dir/init.log"
        cat >>"$store_dir/data/postgresql.conf" <<EOF
listen_addresses = ''
unix_socket_directories = '$store_dir/socket'
unix_socket_permissions = 0700
EOF
    fi
    mkdir -p "$store_dir/socket" "$store_dir/backups"
    if ! "$pg_bin/pg_ctl" -D "$store_dir/data" status >/dev/null 2>&1; then
        # A daemon must not inherit the lifecycle lock: it would hold every
        # later backup/stop request until the server exited.
        "$pg_bin/pg_ctl" -D "$store_dir/data" -l "$store_dir/postgres.log" -w start 9>&-
    fi
    exists="$("$pg_bin/psql" -h "$store_dir/socket" -d postgres -Atqc "SELECT 1 FROM pg_database WHERE datname='cairn'")"
    if [[ "$exists" != 1 ]]; then "$pg_bin/createdb" -h "$store_dir/socket" cairn; fi
    CAIRN_DATABASE_URL="host=$store_dir/socket dbname=cairn sslmode=disable" "$project_dir/bin/cairn" migrate
    printf '%s\n' "Cairn store ready at $store_dir/socket"
    ;;
stop)
    "$pg_bin/pg_ctl" -D "$store_dir/data" -m fast -w stop
    ;;
status)
    "$pg_bin/pg_ctl" -D "$store_dir/data" status
    "$pg_bin/pg_isready" -h "$store_dir/socket" -d cairn
    ;;
backup)
    mkdir -p "$store_dir/backups"
    backup="$store_dir/backups/cairn-$(date -u +%Y%m%dT%H%M%S)-$$.dump"
    "$pg_bin/pg_dump" -h "$store_dir/socket" -d cairn -Fc -f "$backup.pending"
    mv "$backup.pending" "$backup"
    sha256sum "$backup" >"$backup.sha256"
    printf '%s\n' "$backup"
    ;;
*) printf '%s\n' 'usage: scripts/local-store.sh start|stop|status|backup' >&2;exit 2;;
esac
