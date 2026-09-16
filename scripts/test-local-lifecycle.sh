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
# Retain actual event traffic in the backup, including subscriptions and handling.
CAIRN_TEST_DATABASE_URL="$CAIRN_DATABASE_URL" python3 scripts/check_agent_events.py "$PWD/bin/cairn" "$test_root/event-home"
if [[ -n "${XDG_RUNTIME_DIR:-}" ]] && systemctl --user show-environment >/dev/null 2>&1; then
    CAIRN_TEST_DATABASE_URL="$CAIRN_DATABASE_URL" python3 scripts/check_wakeups.py "$PWD/bin/cairn" "$test_root/wake-home"
    CAIRN_TEST_DATABASE_URL="$CAIRN_DATABASE_URL" python3 scripts/check_worker_pools.py "$PWD/bin/cairn" "$test_root/pool-home"
fi
event_snapshot_sql="SELECT jsonb_build_object('worker_pools',(SELECT jsonb_agg(to_jsonb(p) ORDER BY repo,name) FROM cairn.agent_worker_pool p),'worker_slots',(SELECT jsonb_agg(to_jsonb(w) ORDER BY repo,consumer) FROM cairn.agent_worker_slot w),'pool_requests',(SELECT jsonb_agg(to_jsonb(q) ORDER BY event_id) FROM cairn.agent_pool_request q),'pool_service',(SELECT jsonb_agg(to_jsonb(f) ORDER BY repo,pool,publisher) FROM cairn.agent_pool_publisher_service f),'sessions',(SELECT jsonb_agg(to_jsonb(a) ORDER BY agent_id) FROM cairn.agent_session a),'native_attempts',(SELECT jsonb_agg(to_jsonb(n) ORDER BY attempt_id) FROM cairn.agent_session_attempt n),'native_polls',(SELECT jsonb_agg(to_jsonb(p) ORDER BY request_id) FROM cairn.agent_session_poll p),'wakes',(SELECT jsonb_agg(to_jsonb(w) ORDER BY attempt_id) FROM cairn.agent_wake_attempt w),'events',(SELECT jsonb_agg(to_jsonb(e) ORDER BY position) FROM cairn.agent_event e),'deliveries',(SELECT jsonb_agg(to_jsonb(d) ORDER BY delivery_id) FROM cairn.agent_delivery d),'subscriptions',(SELECT jsonb_agg(to_jsonb(s) ORDER BY repo,consumer,topic) FROM cairn.agent_subscription s),'changes',(SELECT jsonb_agg(to_jsonb(c) ORDER BY change_id) FROM cairn.agent_subscription_change c))"
"$pg_bin/psql" -h "$CAIRN_HOME/socket" -d cairn -Atqc "$event_snapshot_sql" > "$test_root/events-before.json"
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
python3 - "$test_root" <<'PYSUPERSEDE'
import json, pathlib, subprocess, sys, uuid
def invoke(command, request):
    return json.loads(subprocess.check_output(['bin/cairn', command], input=json.dumps(request).encode()))['data']
draft = dict(kind='note', body='Obsolete disposable restore note.', scope=dict(repo='fixture:supersession', task_id='*', run_id='*'), claim_type='self')
old = invoke('create', dict(request_id=str(uuid.uuid4()), draft=draft))
draft['body'] = 'Replacement disposable restore note.'
replacement = invoke('create', dict(request_id=str(uuid.uuid4()), draft=draft))
preview = json.loads(subprocess.check_output(['bin/cairn', 'preview-retract', old['record_id']]))['data']
request = dict(request_id=str(uuid.uuid4()), record_id=old['record_id'], expected_version=old['version'], replacement=dict(record_id=replacement['record_id'], version=replacement['version']), preview_id=preview['preview_id'], reason='Retire an ordinary fixture before the backup')
transition = invoke('supersede', request)
(pathlib.Path(sys.argv[1])/'supersession.json').write_text(json.dumps(dict(request=request, transition=transition)))
PYSUPERSEDE
python3 - "$test_root" <<'PYSCOPE'
import json, pathlib, subprocess, sys, uuid
root=pathlib.Path(sys.argv[1])
def invoke(command, request):
    return json.loads(subprocess.check_output(['bin/cairn',command],input=json.dumps(request).encode()))['data']
grant=json.loads((root/'root.json').read_text())
draft=dict(kind='note',body='Scope authorization restore fixture.',scope=dict(repo='fixture:scope-restore',task_id='original',run_id='*'),claim_type='self')
record=invoke('create',dict(request_id=str(uuid.uuid4()),draft=draft))
preview=json.loads(subprocess.check_output(['bin/cairn','preview-retract',record['record_id']]))['data']
request=dict(request_id=str(uuid.uuid4()),record_id=record['record_id'],expected_version=record['version'],scope=dict(repo='fixture:scope-restore',task_id='*',run_id='*'),pins={},grant_id=grant['grant_id'],preview_id=preview['preview_id'],reason='Authorize broader applicability before the disposable backup')
authorization=invoke('authorize-scope',request)
(root/'scope-authorization.json').write_text(json.dumps(dict(request=request,authorization=authorization)))
PYSCOPE
python3 - "$test_root" <<'PYPOLICY'
import json, pathlib, subprocess, sys, uuid
root=pathlib.Path(sys.argv[1]);grant=json.loads((root/'root.json').read_text())
def invoke(command, request):
    return json.loads(subprocess.check_output(['bin/cairn',command],input=json.dumps(request).encode()))['data']
repo='fixture:policy-restore'
invoke('create',dict(request_id=str(uuid.uuid4()),draft=dict(kind='note',body='Policy restore fixture advice.',scope=dict(repo=repo,task_id='*',run_id='*'),claim_type='self')))
first=invoke('policy-revise',dict(request_id=str(uuid.uuid4()),repo=repo,grant_id=grant['grant_id'],rules=dict(optional_percent=10,optional_max_tokens=6000),reason='Establish governed restore fixture budget'))
request=dict(request_id=str(uuid.uuid4()),scope=dict(repo=repo,task_id='task',run_id='run'),purpose='context',available_tokens=64000)
packages=[invoke('compile',request)]
request['request_id']=str(uuid.uuid4());request['mode']='index'
packages.append(invoke('compile',request))
second=invoke('policy-revise',dict(request_id=str(uuid.uuid4()),repo=repo,expected_revision_id=first['revision_id'],grant_id=grant['grant_id'],rules={},reason='Disable optional advice in a governed baseline'))
request['request_id']=str(uuid.uuid4());request.pop('mode')
packages.append(invoke('compile',request))
rollback_request=dict(request_id=str(uuid.uuid4()),repo=repo,expected_revision_id=second['revision_id'],restore_revision_id=first['revision_id'],grant_id=grant['grant_id'],reason='Restore earlier rules as a new policy revision')
rollback=invoke('policy-revise',rollback_request)
(root/'policy.json').write_text(json.dumps(dict(request=rollback_request,rollback=rollback,packages=packages)))
PYPOLICY
python3 - "$test_root" <<'PYCATEGORIES'
import json, pathlib, subprocess, sys, uuid
root=pathlib.Path(sys.argv[1]);grant=json.loads((root/'root.json').read_text())
def invoke(command, request):
    return json.loads(subprocess.check_output(['bin/cairn',command],input=json.dumps(request).encode()))['data']
repo='fixture:category-restore'
issue=dict(request_id=str(uuid.uuid4()),draft=dict(kind='instruction',body='Retain this security category through restore.',scope=dict(repo=repo,task_id='*',run_id='*'),claim_type='self'),grant_id=grant['grant_id'],mandatory=True,category='security',policy_key='category-restore',reason='Issue a categorized disposable restore instruction')
record=invoke('issue',issue)
limits=dict(security=dict(max_count=1,max_tokens=100),workflow=dict(max_count=0,max_tokens=0),preference=dict(max_count=0,max_tokens=0))
request=dict(request_id=str(uuid.uuid4()),repo=repo,grant_id=grant['grant_id'],rules=dict(optional_percent=10,optional_max_tokens=6000,instruction_limits=limits),reason='Preserve explicit category limits through a backup')
revision=invoke('policy-revise',request)
packages=[]
for mode in ['', 'index']:
    packages.append(invoke('compile',dict(request_id=str(uuid.uuid4()),scope=dict(repo=repo,task_id='task',run_id='run'),purpose='context',available_tokens=64000,mode=mode)))
(root/'categories.json').write_text(json.dumps(dict(issue=issue,record=record,request=request,revision=revision,packages=packages)))
PYCATEGORIES
python3 scripts/check-restore-fence.py "$test_root" seed
backup="$(bash scripts/local-store.sh backup)"
"$pg_bin/createdb" -h "$CAIRN_HOME/socket" cairn_restore
"$pg_bin/pg_restore" -h "$CAIRN_HOME/socket" --no-owner --no-privileges -d cairn_restore "$backup"
"$pg_bin/psql" -h "$CAIRN_HOME/socket" -d cairn_restore -Atqc "$event_snapshot_sql" > "$test_root/events-restored.json"
cmp "$test_root/events-before.json" "$test_root/events-restored.json"
printf '%s\n' 'Agent sessions, events, deliveries, subscriptions and history survive real backup/restore'
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
supersession=json.loads((root/'supersession.json').read_text())
assert invoke('supersede',supersession['request'],env)==supersession['transition']
restored_transition=json.loads(subprocess.check_output(['bin/cairn','supersession',supersession['request']['record_id']],env=env))['data']
assert restored_transition==supersession['transition']
retired=json.loads(subprocess.check_output(['bin/cairn','get',supersession['request']['record_id']],env=env))['data']
assert retired['lifecycle']=='superseded' and retired['version']==2
print('Restored supersession preserves its pinned replacement and idempotent retry')
scope=json.loads((root/'scope-authorization.json').read_text())
assert invoke('authorize-scope',scope['request'],env)==scope['authorization']
authorization=json.loads(subprocess.check_output(['bin/cairn','scope-authorization',scope['request']['record_id']],env=env))['data']
assert authorization==scope['authorization'] and authorization['scope_grant_live']
compiled=invoke('compile',dict(request_id=str(uuid.uuid4()),scope=dict(repo='fixture:scope-restore',task_id='other',run_id='run'),purpose='context',available_tokens=64000),env)
assert len(compiled['semantic']['selected'])==1
print('Restored scope authorization retains its live grant, wider applicability and idempotent retry')
policy=json.loads((root/'policy.json').read_text())
assert invoke('policy-revise',policy['request'],env)==policy['rollback']
effective=json.loads(subprocess.check_output(['bin/cairn','policy','fixture:policy-restore'],env=env))['data']
assert effective['revision']==policy['rollback'] and effective['revision']['authority_live']
for original in policy['packages']:
    historical=invoke('recompile',dict(receipt_id=original['receipt_id']),env)['package']
    assert historical['seal']==original['seal']
fresh=invoke('compile',dict(request_id=str(uuid.uuid4()),scope=dict(repo='fixture:policy-restore',task_id='task',run_id='fresh'),purpose='context',available_tokens=64000),env)
assert len(fresh['semantic']['selected'])==1 and fresh['semantic']['policy_revision']['revision_id']==policy['rollback']['revision_id']
runs=json.loads(subprocess.check_output(['bin/cairn','runs','--policy-rev',policy['rollback']['revision_id'],'fixture:policy-restore'],env=env))['data']
assert runs['rows']==[] and not runs['more']
print('Restored policy rollback retains effective rules, idempotent retry, policy pins and historical body/index seals')
categories=json.loads((root/'categories.json').read_text())
assert invoke('issue',categories['issue'],env)==categories['record']
assert invoke('policy-revise',categories['request'],env)==categories['revision']
instruction=json.loads(subprocess.check_output(['bin/cairn','instruction-policy',categories['record']['record_id']],env=env))['data']
assert instruction['category']=='security' and instruction['mandatory']
effective=json.loads(subprocess.check_output(['bin/cairn','policy','fixture:category-restore'],env=env))['data']
assert effective['engine']=='local-loop/3' and effective['rules']==categories['revision']['rules']
for original in categories['packages']:
    historical=invoke('recompile',dict(receipt_id=original['receipt_id']),env)['package']
    replayed=json.loads(subprocess.check_output(['bin/cairn','replay',original['receipt_id']],env=env))['data']['package']
    assert historical['seal']==original['seal']==replayed['seal']
    fresh=invoke('compile',dict(request_id=str(uuid.uuid4()),scope=dict(repo='fixture:category-restore',task_id='task',run_id='fresh'),purpose='context',available_tokens=64000,mode=original['semantic'].get('mode','')),env)
    assert len(fresh['semantic']['selected'])==1 and fresh['semantic']['selected'][0]['category']=='security'
print('Restored instruction categories retain limits, issue and policy retries, and body/index history')
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
python3 scripts/check-restore-fence.py "$test_root"
python3 scripts/check-deletion.py "$test_root" "$pg_bin"
python3 scripts/check-recovery.py "$test_root" "$pg_bin"
bash scripts/local-store.sh stop
bash scripts/local-store.sh start
bin/cairn replay "$receipt" >/dev/null
bash scripts/local-store.sh stop
printf '%s\n' 'Local store start, backup, restore, stop and restart passed'
