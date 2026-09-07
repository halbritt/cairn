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
    python3 - "$backup" <<'PYREQ' | CAIRN_DATABASE_URL="host=$store_dir/socket dbname=cairn sslmode=disable" "$project_dir/bin/cairn" checkpoint >"$backup.checkpoint.pending"
import json, pathlib, sys, uuid
print(json.dumps({'request_id':str(uuid.uuid4()), 'export_id':pathlib.Path(sys.argv[1]).name}))
PYREQ
    "$pg_bin/pg_dump" -h "$store_dir/socket" -d cairn -Fc -f "$backup.pending"
    mv "$backup.pending" "$backup"
    sha256sum "$backup" >"$backup.sha256"
    python3 - "$backup" <<'PYCATALOG'
import hashlib, json, pathlib, sys
backup = pathlib.Path(sys.argv[1])
checkpoint_path = pathlib.Path(str(backup)+'.checkpoint.pending')
checkpoint = json.loads(checkpoint_path.read_text())['data']
with backup.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
catalog = {'schema':'cairn.backup-catalog/1', 'dump':backup.name, 'dump_sha256':digest,
    'checkpoint_expectation':{'checkpoint_id':checkpoint['checkpoint_id'],
      'expected_sha256':checkpoint['sha256'], 'expected_export_id':checkpoint['export_id']},
    'audit_count':checkpoint['count'],
    'coverage':'Checkpoint committed before dump snapshot; later audit members may be present but are not checkpointed.'}
pending = pathlib.Path(str(backup)+'.catalog.pending')
pending.write_text(json.dumps(catalog, indent=2)+'\n')
pending.replace(str(backup)+'.catalog.json')
checkpoint_path.unlink()
PYCATALOG
    printf '%s\n' "$backup"
    ;;
*) printf '%s\n' 'usage: scripts/local-store.sh start|stop|status|backup' >&2;exit 2;;
esac
