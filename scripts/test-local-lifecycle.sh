#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test_root="$(mktemp -d /tmp/cairn-lifecycle.XXXXXXXX)"
export CAIRN_HOME="$test_root/store"
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
cleanup() {
    if [[ -f "$CAIRN_HOME/data/postmaster.pid" ]]; then
        "$pg_bin/pg_ctl" -D "$CAIRN_HOME/data" -m immediate -w stop >/dev/null
    fi
    rm -rf -- "$test_root"
}
trap cleanup EXIT
make build
bash scripts/local-store.sh start
export CAIRN_DATABASE_URL="host=$CAIRN_HOME/socket dbname=cairn sslmode=disable"
bin/cairn remember --repo fixture:restore 'Restore fixture: preserve this exact note.' >"$test_root/note.json"
bin/cairn search --repo fixture:restore --tokens 64000 'Restore fixture' >"$test_root/package.json"
receipt="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["receipt_id"])' "$test_root/package.json")"
backup="$(bash scripts/local-store.sh backup)"
"$pg_bin/createdb" -h "$CAIRN_HOME/socket" cairn_restore
"$pg_bin/pg_restore" -h "$CAIRN_HOME/socket" --no-owner --no-privileges -d cairn_restore "$backup"
CAIRN_DATABASE_URL="host=$CAIRN_HOME/socket dbname=cairn_restore sslmode=disable" bin/cairn replay "$receipt" >"$test_root/replayed.json"
python3 - "$test_root" <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
original = json.loads((root / 'package.json').read_text())['data']
replayed = json.loads((root / 'replayed.json').read_text())['data']['package']
assert original['seal'] == replayed['seal']
assert original['semantic'] == replayed['semantic']
assert len(replayed['semantic']['selected']) == 1
print('Restored database reproduces the exact semantic package and seal')
PY
bash scripts/local-store.sh stop
bash scripts/local-store.sh start
bin/cairn replay "$receipt" >/dev/null
bash scripts/local-store.sh stop
printf '%s\n' 'Local store start, backup, restore, stop and restart passed'
