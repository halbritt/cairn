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
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
root = args.output.resolve()
root.mkdir(mode=0o700)
project = Path(__file__).resolve().parents[1]
run_id = uuid.uuid4().hex
repos = {
    label: "trial:semantic-" + label + "-" + run_id for label in ["corpus", "long"]
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
for label in ["corpus", "long"]:
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
rows = []
assert args.worker.is_file()


def agent(label, *args, payload=None):
    return cli(
        "agent",
        "--socket",
        str(store / "api.sock"),
        "--token-file",
        str(store / (label + ".token")),
        *args,
        payload=payload,
    )


with (root / "api.log").open("wb") as log:
    api = subprocess.Popen(
        [binary, "serve", "--semantic-command", str(args.worker.resolve())],
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
        # Restart only this fixture API without a scorer; never modify the supplied worker.
        api.terminate()
        api.wait(timeout=10)
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
        for name in ["identities.json", "corpus.token", "long.token"]:
            (store / name).unlink(missing_ok=True)
