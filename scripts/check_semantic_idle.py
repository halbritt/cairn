"""Real-model idle restart and current-source checks in the caller's disposable store."""
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid


def check(binary, worker, root, environment, agent, repo):
    output = root / "idle-restart"
    output.mkdir(mode=0o700)
    socket = output / "api.sock"
    project = Path(__file__).resolve().parents[1]
    with (output / "api.log").open("wb") as log:
        api = subprocess.Popen([binary, "serve", "--socket", str(socket),
              "--semantic-stream-command", str(worker.resolve()), "--semantic-idle-timeout", "200ms"],
              env=environment, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 10
            while not socket.exists():
                if api.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError("idle fixture API did not start")
                time.sleep(.02)

            def call(*args, payload=None):
                return agent("idle", *args, payload=payload, socket=socket)

            def children():
                return {int(pid) for path in Path(f"/proc/{api.pid}/task").glob("*/children")
                        for pid in path.read_text().split()}

            def released():
                deadline = time.monotonic() + 5
                while children():
                    assert time.monotonic() < deadline, "idle child was not released"
                    time.sleep(.01)

            records = []
            for name in ["docs/semantic-discovery.md", "docs/opencode-tools.md"]:
                records.append(call("remember", "--repo", repo, "--shareable", (project / name).read_text()))
            private = call("remember", "--repo", repo, "PRIVATE_FIXTURE: excluded from hosted scoring.")
            responses = []
            def search(label):
                start = time.monotonic()
                result = call("search", "--repo", repo, "--task", "idle-restart", "--run", label,
                              "--tokens", "64000", "--semantic", "Continue task memory across idle workers and sessions")
                elapsed = time.monotonic() - start
                assert result["discovery"]["state"] == "ready", result["status"]
                pids = children()
                assert len(pids) == 1, pids
                assert private["record_id"] not in json.dumps(result)
                assert '"cache"' not in json.dumps(result) and '"vectors"' not in json.dumps(result)
                (output / (label + ".json")).write_text(json.dumps(result, indent=2)+"\n")
                responses.append(dict(case=label,seconds=elapsed,pid=next(iter(pids))))
                return result

            first = search("cold")
            released()
            second = search("restored")
            assert responses[0]["pid"] != responses[1]["pid"]
            assert first["discovery"] == second["discovery"]
            keys = lambda r: [(x["record_id"], x["version"], x["body_sha256"]) for x in r["index"]]
            assert keys(first) == keys(second)
            old = next(x for x in second["index"] if x["record_id"] == records[0]["record_id"])
            new_body = "Current fixture guidance: choose the declared task before resuming work."
            call("revise", payload=dict(request_id=str(uuid.uuid4()), record_id=records[0]["record_id"],
                 expected_version=1, repo=repo, body=new_body))
            released()
            changed = search("changed")
            entry = next(x for x in changed["index"] if x["record_id"] == records[0]["record_id"])
            assert entry["version"] == 2 and entry["body_sha256"] == hashlib.sha256(new_body.encode()).hexdigest()
            pulled = call("expand", payload=entry["pull_arguments"])
            assert pulled["selection"]["record"]["body"] == new_body
            try:
                call("expand", payload=old["pull_arguments"])
            except RuntimeError as error:
                assert "STALE_HANDLE" in str(error)
            else:
                raise AssertionError("old handle survived edit and idle restart")
            released()
            report = dict(cases=responses,identical_score_digest=True,changed_body_pulled=True,
                          stale_handle_refused=True,private_note_excluded=True,cache_not_in_response=True,
                          restored_below_half_cold=responses[1]["seconds"] < responses[0]["seconds"]/2)
            (output / "report.json").write_text(json.dumps(report,indent=2)+"\n")
            print(json.dumps(report),flush=True)
        finally:
            api.terminate()
            api.wait(timeout=10)
