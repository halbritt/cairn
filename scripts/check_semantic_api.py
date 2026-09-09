"""Opt-in real CPU/API check; CAIRN_TEST_DATABASE_URL must name an owned disposable DB."""

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import time
import uuid
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--binary", required=True, type=Path)
parser.add_argument("--worker", required=True, type=Path)
parser.add_argument("--baseline-worker", type=Path, help="compare stream API to a one-shot worker")
parser.add_argument("--stream", action="store_true", help="worker uses the reusable JSON-line protocol")
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(mode=0o700)
project = Path(__file__).resolve().parents[1]
run_id = uuid.uuid4().hex
repos = {
    label: "trial:semantic-" + label + "-" + run_id for label in ["corpus", "long", "small"]
}
store = root / "store"
store.mkdir(mode=0o700)
binary = str(args.binary.resolve())
binary_sha256 = hashlib.sha256(Path(binary).read_bytes()).hexdigest()
dsn = os.environ["CAIRN_TEST_DATABASE_URL"]
env = dict(os.environ, CAIRN_HOME=str(store), CAIRN_DATABASE_URL=dsn)


def write(p, v):
    p.write_text(json.dumps(v, indent=2) + "\n")


def cli(*args, payload=None):
    r = subprocess.run(
        [binary, *args],
        input=None if payload is None else json.dumps(payload),
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )
    if r.returncode:
        raise RuntimeError(r.stdout or r.stderr)
    return json.loads(r.stdout).get("data")


cli("migrate")
workload = json.loads((project / "core/testdata/retrieval-quality.json").read_text())
write(
    root / "plan.json",
    dict(
        binary_sha256=binary_sha256,
        workload_sha256=hashlib.sha256(
            (project / "core/testdata/retrieval-quality.json").read_bytes()
        ).hexdigest(),
        queries=[q["id"] for q in workload["queries"]],
        conditions=["lexical", "semantic"],
        worker_mode="stream" if args.stream else "one-shot",
        available_tokens=64000,
        source="same fixture store and original public passages",
        checks=[
            "ranking and actual budgeted index",
            "exact body pull",
            "long-note source coverage",
            "unconfigured worker fallback",
        ],
        no_task_benefit_claim=True,
    ),
)
identities = []
for label in ["corpus", "long", "small"]:
    token = secrets.token_urlsafe(32)
    p = store / (label + ".token")
    p.write_text(token)
    p.chmod(0o600)
    identities.append(
        dict(
            token_sha256=hashlib.sha256(token.encode()).hexdigest(),
            principal="agent:semantic-" + label + "-" + run_id,
            repo=repos[label],
            role="agent",
            destination="hosted",
        )
    )
p = store / "identities.json"
write(p, identities)
p.chmod(0o600)
records = {}
for n in workload["notes"]:
    records[n["id"]] = cli(
        "remember", "--repo", repos["corpus"], "--shareable", n["body"]
    )
longbody = (
    "Routine service administration follows the selected runbook. " * 240
) + "\n\nThe zephyr service credential is stored at /run/zephyr/access-key."
longrecord = cli("remember", "--repo", repos["long"], "--shareable", longbody)
cli(
    "remember",
    "--repo",
    repos["long"],
    "--shareable",
    "The archive location is /srv/archive; unrelated to the service credential.",
)
small_records = [cli("remember", "--repo", repos["small"], "--shareable",
                     "--kind", "decision" if i == 0 else "preference", n["body"])
                 for i, n in enumerate(workload["notes"][:3])]
local_record = cli("remember", "--repo", repos["small"], "--kind", "decision",
                   "LOCAL_ONLY_FIXTURE: database location must not enter hosted scoring.")
rows = []
paired = []
stream_pids = set()
assert args.worker.is_file()


def agent(label, *args, payload=None, socket=None):
    return cli(
        "agent",
        "--socket",
        str(socket or store / "api.sock"),
        "--token-file",
        str(store / (label + ".token")),
        *args,
        payload=payload,
    )


with (root / "api.log").open("wb") as log:
    api = subprocess.Popen(
        [binary, "serve", "--semantic-stream-command" if args.stream else "--semantic-command", str(args.worker.resolve())],
        env=env,
        stdout=log,
        stderr=log,
    )
    try:
        deadline = time.monotonic() + 10
        while not (store / "api.sock").exists():
            if api.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("API startup failed")
            time.sleep(0.02)
        def children():
            return {int(pid) for path in Path(f"/proc/{api.pid}/task").glob("*/children")
                    for pid in path.read_text().split()}

        if args.stream:
            assert not children(), "worker should start lazily"
        for q in workload["queries"]:
            for condition in ["lexical", "semantic"]:
                search_args = [
                    "search",
                    "--repo",
                    repos["corpus"],
                    "--task",
                    "comparison",
                    "--run",
                    q["id"],
                    "--tokens",
                    "64000",
                ]
                if condition == "semantic":
                    search_args += ["--semantic"]
                start = time.monotonic()
                result = agent("corpus", *search_args, q["query"])
                elapsed = time.monotonic() - start
                if condition == "semantic":
                    assert result["discovery"]["state"] == "ready", result["status"]
                    if args.stream:
                        active = children()
                        assert len(active) == 1, active
                        stream_pids.update(active)
                        assert len(stream_pids) == 1, "model worker restarted during valid requests"
                labels = {r["record_id"]: k for k, r in records.items()}
                returned = [labels[e["record_id"]] for e in result["index"]]
                rank = min(
                    [i + 1 for i, n in enumerate(returned) if n in q["relevant"]],
                    default=None,
                )
                rawpath = root / (q["id"] + "-" + condition + ".json")
                write(rawpath, result)
                rows.append(
                    dict(
                        query_id=q["id"],
                        condition=condition,
                        rank=rank,
                        returned=returned,
                        status=result["status"],
                        discovery=result.get("discovery"),
                        elapsed_seconds=elapsed,
                        receipt_id=result["receipt_id"],
                    )
                )
            print(
                q["id"],
                [(r["condition"], r["rank"], r["status"]) for r in rows[-2:]],
                flush=True,
            )
        selected = json.loads((root / "storage-topic-semantic.json").read_text())
        entry = next(
            e
            for e in selected["index"]
            if e["record_id"] == records["storage"]["record_id"]
        )
        pulled = agent("corpus", "expand", payload=entry["pull_arguments"])
        assert pulled["selection"]["record"]["body"] == records["storage"]["body"]
        long = agent(
            "long",
            "search",
            "--repo",
            repos["long"],
            "--task",
            "long",
            "--run",
            "1",
            "--semantic",
            "Where is the zephyr service credential stored?",
        )
        write(root / "long.json", long)
        assert (
            long["discovery"]["state"] == "ready"
            and long["index"][0]["record_id"] == longrecord["record_id"]
        )
        expanded = agent("long", "expand", payload=long["index"][0]["pull_arguments"])
        assert expanded["selection"]["record"]["body"] == longbody
        if args.stream and args.baseline_worker:
            baseline_socket = store / "baseline.sock"
            baseline_api = subprocess.Popen(
                [binary, "serve", "--socket", str(baseline_socket),
                 "--semantic-command", str(args.baseline_worker.resolve())],
                env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 10
                while not baseline_socket.exists():
                    if baseline_api.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Baseline API startup failed")
                    time.sleep(0.02)
                # Alternate first condition. Same store, identity, query, budget
                # and source versions; each API owns its own worker mechanism.
                for i, count in enumerate([1, 3, 12, 12, 3, 1]):
                    label = "corpus" if count == 12 else "small"
                    search = ["search", "--repo", repos[label], "--task", "residency",
                              "--run", str(i), "--tokens", "64000", "--semantic"]
                    if count == 1:
                        search += ["--kind", "decision"]
                    search += ["Where does Cairn keep its data?"]
                    conditions = {}
                    for condition in (["one-shot", "stream"] if i % 2 == 0 else ["stream", "one-shot"]):
                        start = time.monotonic()
                        response = agent(label, *search,
                                         socket=baseline_socket if condition == "one-shot" else None)
                        elapsed = time.monotonic() - start
                        assert response["discovery"]["state"] == "ready"
                        conditions[condition] = dict(elapsed_seconds=elapsed, response=response)
                    a, b = (conditions[k]["response"] for k in ["one-shot", "stream"])
                    assert a["discovery"] == b["discovery"]
                    keys = lambda r: [(e["record_id"], e["version"], e["body_sha256"]) for e in r["index"]]
                    assert keys(a) == keys(b)
                    paired.append(dict(notes=count, conditions=conditions))
                write(root / "residency-pairs.json", paired)
            finally:
                baseline_api.terminate()
                baseline_api.wait(timeout=10)

        if args.stream:
            before = agent("small", "search", "--repo", repos["small"], "--task", "mutation",
                           "--run", "before", "--semantic", "--kind", "decision", "database")
            old_entry = before["index"][0]
            record = small_records[0]
            new_body = "Changed fixture storage guidance: data is in /srv/fixture-memory."
            agent("small", "revise", payload=dict(request_id=str(uuid.uuid4()),
                  record_id=record["record_id"], expected_version=1, repo=repos["small"], body=new_body))
            after = agent("small", "search", "--repo", repos["small"], "--task", "mutation",
                          "--run", "after", "--semantic", "--kind", "decision", "database")
            assert after["discovery"]["state"] == "ready"
            assert after["index"][0]["version"] == 2
            assert after["index"][0]["body_sha256"] == hashlib.sha256(new_body.encode()).hexdigest()
            try:
                agent("small", "expand", payload=old_entry["pull_arguments"])
            except RuntimeError as error:
                assert "STALE_HANDLE" in str(error)
            else:
                raise AssertionError("old body handle survived edit")
            preview = cli("preview-retract", record["record_id"])
            cli("supersede", payload=dict(request_id=str(uuid.uuid4()), record_id=record["record_id"],
                expected_version=2, replacement=dict(record_id=small_records[1]["record_id"], version=1),
                preview_id=preview["preview_id"], reason="Retire a fixture note while the model is warm"))
            hidden = agent("small", "search", "--repo", repos["small"], "--task", "mutation",
                           "--run", "hidden", "--semantic", "--kind", "decision", "database")
            assert not hidden.get("index") and hidden["discovery"]["state"] == "not_needed"
            assert children() == stream_pids, "model worker changed across note updates"
            write(root / "residency-mutation.json", dict(before=before, after=after, hidden=hidden,
                stale_handle_refused=True, current_version_scored=True, retired_note_excluded=True,
                local_note_excluded=True))
        # Restart only this fixture API without a scorer; never modify the supplied worker.
        api.terminate()
        api.wait(timeout=10)
        if args.stream:
            assert all(not Path(f"/proc/{pid}").exists() for pid in stream_pids), "shutdown left worker alive"
        api = subprocess.Popen([binary, "serve"], env=env, stdout=log, stderr=log)
        deadline = time.monotonic() + 10
        while not (store / "api.sock").exists():
            if api.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Fallback API startup failed")
            time.sleep(0.02)
        fallback = agent(
            "corpus",
            "search",
            "--repo",
            repos["corpus"],
            "--task",
            "fallback",
            "--run",
            "1",
            "--semantic",
            "CAIRN_HOME",
        )
        assert (
            fallback["status"] == "DEGRADED_NO_EMBEDDINGS"
            and fallback["index"][0]["record_id"] == records["storage"]["record_id"]
        )
        write(root / "fallback.json", fallback)
        assert hashlib.sha256(Path(binary).read_bytes()).hexdigest() == binary_sha256
        write(
            root / "report.json",
            dict(
                repos=repos,
                binary_sha256=binary_sha256,
                worker_mode="stream" if args.stream else "one-shot",
                residency_pairs=len(paired),
                reused_worker_pids=sorted(stream_pids),
                rows=rows,
                exact_storage_pull=True,
                long_note_bytes=len(longbody.encode()),
                long_note_first=True,
                exact_long_pull=True,
                unavailable_fallback=True,
            ),
        )
    finally:
        api.terminate()
        api.wait(timeout=10)
        subprocess.run(
            ["pg_dump", dsn, "-Fc", "-f", str(root / "trial.dump")],
            check=True,
            timeout=30,
        )
        for name in ["identities.json", "corpus.token", "long.token", "small.token"]:
            (store / name).unlink(missing_ok=True)
