"""Observed compact CLI input and real ordinary-profile child pulls."""
import json
import subprocess
import sys
import uuid


def check(binary, root, environment):
    env = dict(environment, CAIRN_DATABASE_URL="host=/absent-index-client dbname=denied")
    observer = [binary, "agent", "--socket", str(root / "api.sock"), "--token-file", str(root / "hosted.token")]
    agent = [binary, "agent", "--socket", str(root / "api.sock"), "--token-file", str(root / "hosted-agent.token")]

    def call(base, operation, payload):
        result = subprocess.run([*base, operation], input=json.dumps(payload), env=env,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, (result.stdout, result.stderr)
        return json.loads(result.stdout)["data"]

    scope = dict(repo="fixture:socket", task_id="observed-index", run_id=str(uuid.uuid4()))
    note_body = "Compiler source guidance. " + "Exact retained details. " * 100
    record = call(agent, "create", dict(request_id=str(uuid.uuid4()), draft=dict(
        kind="procedure", body=note_body, sensitivity="shareable", claim_type="self", scope=scope)))
    source = call(agent, "evidence", dict(request_id=str(uuid.uuid4()), repo=scope["repo"],
        body="Observed index source passage.", source="selected fixture source", sensitivity="shareable"))
    call(agent, "cite", dict(request_id=str(uuid.uuid4()), record_id=record["record_id"], expected_version=1,
         repo=scope["repo"], evidence_citations=[dict(evidence_id=source["evidence_id"], expected_sha256=source["sha256"])]))
    pins = dict(task_class="review", binding_id="observed-cli", capability_id="shell")
    request = dict(request_id=str(uuid.uuid4()), scope=scope, context=pins, query="compiler", purpose="context",
                   available_tokens=16000, expansion_reader="agent:hosted-capture")
    index = call(observer, "index", request)
    ref = dict(receipt_id=index["package"]["receipt_id"], seal=index["package"]["seal"])
    assert call(observer, "run-index", ref) == index
    args = [*observer, "run", "--index", "--expansion-reader", request["expansion_reader"],
            "--pull-tool", "cairn_pull", "--search-tool", "cairn_search", "--destination", "hosted",
            "--repo", scope["repo"], "--task", scope["task_id"], "--run", scope["run_id"],
            "--request-id", request["request_id"], "--query", "compiler", "--tokens", "16000", "--prompt", "Read the selected source.",
            "--task-class", "review", "--binding", "observed-cli", "--capability", "shell",
            "--receipt-id", ref["receipt_id"], "--seal", ref["seal"]]
    for extra in (["--expansion-reader", "wrong"], ["--tokens", "15999"], ["--pull-tool", ""],
                  ["--query", "different"], ["--index=false"]):
        denied = subprocess.run([*args, *extra, "--", "/bin/echo", "UNEXPECTED-LAUNCH"], env=env,
                                text=True, capture_output=True, timeout=15)
        assert denied.returncode != 0 and "UNEXPECTED-LAUNCH" not in denied.stdout
        status = call(observer, "run-status", dict(receipt_id=ref["receipt_id"]))
        assert not status["binding_observed"] and not status["launch_claimed"]
    child = root / "observed-index-child.py"
    child.write_text(r"""import json,os,subprocess,sys
text=sys.stdin.read() if sys.argv[4]=='stdin' else sys.argv[-1]
view=json.loads(text.split('\n',2)[2].split('\nTASK\n',1)[0])
entry=next(e for e in view['index'] if e['record_id']==sys.argv[3])
base=[sys.argv[1],'agent','--socket',sys.argv[2]+'/api.sock','--token-file',sys.argv[2]+'/hosted-agent.token']
def call(op,req):
 p=subprocess.run(base+[op],input=json.dumps(req),capture_output=True,text=True,timeout=10,check=True)
 return json.loads(p.stdout)['data']
pull=call('expand',entry['pull_arguments'])
ref=pull['selection']['evidence'][0]
source=call('expand-evidence',dict(entry['pull_arguments'],evidence_id=ref['evidence_id'],expected_sha256=ref['sha256']))
assert not any(k.startswith('CAIRN_') for k in os.environ)
print(json.dumps(dict(receipt_id=view['receipt_id'],note=pull['selection']['record']['body'],source=source['evidence']['body'],credits=source['credits_remaining'])))
""")
    for carrier in ("stdin", "argv"):
        actual_args = args if carrier == "stdin" else [*args[:-4], "--request-id", str(uuid.uuid4())]
        actual = subprocess.run([*actual_args, "--carrier", carrier, "--", sys.executable, str(child),
                                 binary, str(root), record["record_id"], carrier], env=env,
                                capture_output=True, text=True, timeout=20)
        assert actual.returncode == 0, (actual.stdout, actual.stderr)
        result = json.loads(actual.stderr)["data"]
        read = json.loads(actual.stdout)
        assert result["process_state"] == "exited" and result["exit_code"] == 0
        assert read["receipt_id"] == result["receipt_id"] and read["note"] == note_body
        assert read["source"] == "Observed index source passage." and read["credits"] == 2
        if carrier == "stdin":
            assert result["receipt_id"] == ref["receipt_id"] and result["seal"] == ref["seal"]
        status = call(observer, "run-status", dict(receipt_id=result["receipt_id"]))
        assert status["launch_claimed"] and status["outcome"]["observation_id"] == result["outcome_id"]
    retry = subprocess.run([*args, "--carrier", "stdin", "--", sys.executable, str(child), binary, str(root), record["record_id"], "stdin"], env=env, capture_output=True, text=True, timeout=15)
    assert retry.returncode != 0 and "RUN_ALREADY_STARTED" in retry.stdout + retry.stderr
    for options, flags, query in [
        ({"page_offset": 0}, ["--offset", "0"], "compiler"),
        ({"browse_offset": 0}, ["--browse", "--offset", "0"], ""),
        ({"semantic": True}, ["--semantic"], "compiler"),
    ]:
        selected = call(observer, "index", dict(request, request_id=str(uuid.uuid4()), query=query, **options))
        package = selected["package"]
        invocation = [*args[:-4], "--request-id", str(uuid.uuid4()), "--query", query,
                      "--receipt-id", package["receipt_id"], "--seal", package["seal"]]
        mismatch = subprocess.run([*invocation, "--", "/bin/cat"], env=env, capture_output=True, text=True, timeout=15)
        assert mismatch.returncode != 0 and "INVALID_REQUEST" in mismatch.stdout + mismatch.stderr
        status = call(observer, "run-status", dict(receipt_id=package["receipt_id"]))
        assert not status["binding_observed"] and not status["launch_claimed"]
        matched = subprocess.run([*invocation, *flags, "--", "/bin/cat"], env=env, capture_output=True, text=True, timeout=15)
        assert matched.returncode == 0, (matched.stdout, matched.stderr)
        view = json.loads(matched.stdout.split("\n", 2)[2].split("\nTASK\n", 1)[0])
        assert view["source_seal"] == package["seal"] and view["receipt_id"] == package["receipt_id"]
    print("Observed fresh/retained index stdin/argv execution uses ordinary child body/source pulls; mismatches and duplicate launches refuse")
