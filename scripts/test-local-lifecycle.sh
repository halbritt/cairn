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
python3 - "$test_root" <<'PYBOOT'
import json, pathlib, subprocess, sys, uuid
root=pathlib.Path(sys.argv[1])
def invoke(command, request):
    return json.loads(subprocess.check_output(['bin/cairn',command],input=json.dumps(request).encode()))['data']
grant=invoke('bootstrap',{'request_id':str(uuid.uuid4()),'reason':'Install disposable restore fixture root'})
(root/'root.json').write_text(json.dumps(grant))
invoke('issue',{'request_id':str(uuid.uuid4()),'draft':{'kind':'instruction','body':'Restore fixture instruction.', 'scope':{'repo':'fixture:restore','task_id':'*','run_id':'*'}, 'claim_type':'self'},'grant_id':grant['grant_id'],'policy_key':'restore','reason':'Issue disposable restore fixture'})
PYBOOT
bin/cairn remember --repo fixture:restore 'Restore fixture: preserve this exact note.' >"$test_root/note.json"
bin/cairn search --repo fixture:restore --tokens 64000 'Restore fixture' >"$test_root/package.json"
receipt="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["data"]["receipt_id"])' "$test_root/package.json")"
python3 - "$test_root" <<'PYINDEX'
import json, pathlib, subprocess, sys, uuid
request=dict(request_id=str(uuid.uuid4()),scope=dict(repo='fixture:restore',task_id='restore',run_id='restore'),query='Restore fixture',purpose='context',available_tokens=64000)
result=subprocess.check_output(['bin/cairn','index'],input=json.dumps(request).encode())
(pathlib.Path(sys.argv[1])/'index.json').write_bytes(result)
PYINDEX
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
assert len(replayed['semantic']['selected']) == 2
print('Restored database reproduces the exact semantic package and seal')
PY
python3 - "$test_root" "$backup" <<'PYVERIFY'
import hashlib, json, os, pathlib, subprocess, sys, uuid
root=pathlib.Path(sys.argv[1]);backup=pathlib.Path(sys.argv[2])
catalog=json.loads(pathlib.Path(str(backup)+'.catalog.json').read_text())
with backup.open('rb') as stream:
    assert hashlib.file_digest(stream,'sha256').hexdigest()==catalog['dump_sha256']
env=dict(os.environ,CAIRN_DATABASE_URL=f"host={root}/store/socket dbname=cairn_restore sslmode=disable")
def invoke(command, request, environment=None):
    return json.loads(subprocess.check_output(['bin/cairn',command],input=json.dumps(request).encode(),env=environment))['data']
verified=invoke('verify-checkpoint',catalog['checkpoint_expectation'],env)
assert verified['valid'] and verified['uncovered_count']==0
receipt=json.loads((root/'package.json').read_text())['data']
recompiled=invoke('recompile',{'receipt_id':receipt['receipt_id'],'query':'Restore fixture'},env)
assert recompiled['package']['seal']==receipt['seal']
# A separately retained newer expectation must reject the older consistent dump.
later=invoke('checkpoint',{'request_id':str(uuid.uuid4()),'export_id':'fixture:newer-expectation'})
request={'checkpoint_id':later['checkpoint_id'],'expected_sha256':later['sha256'],'expected_export_id':later['export_id']}
missing=subprocess.run(['bin/cairn','verify-checkpoint'],input=json.dumps(request).encode(),env=env,capture_output=True)
assert missing.returncode!=0 and json.loads(missing.stdout)['status']=='CHECKPOINT_MISMATCH'
index=json.loads((root/'index.json').read_text())['data']
index_recompile=invoke('recompile',{'receipt_id':index['package']['receipt_id'],'query':'Restore fixture'},env)
assert index_recompile['package']['seal']==index['package']['seal']
invoke('invalidate-handles',{'request_id':str(uuid.uuid4()),'reason':'Fence sessions before admitting traffic to restored fixture'},env)
pull=dict(request_id=str(uuid.uuid4()),receipt_id=index['package']['receipt_id'],handle=index['handles'][0]['handle'])
fenced=subprocess.run(['bin/cairn','expand'],input=json.dumps(pull).encode(),env=env,capture_output=True)
assert fenced.returncode!=0 and json.loads(fenced.stdout)['status']=='STALE_HANDLE'
print('Restored inputs and index recompile; expected audit set verifies; restored handles are fenced')
PYVERIFY
python3 scripts/check-deletion.py "$test_root" "$pg_bin"
bash scripts/local-store.sh stop
bash scripts/local-store.sh start
bin/cairn replay "$receipt" >/dev/null
bash scripts/local-store.sh stop
printf '%s\n' 'Local store start, backup, restore, stop and restart passed'
