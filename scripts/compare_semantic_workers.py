"""Compare local CPU workers on public fixtures; keeps all results outside the repository."""

from pathlib import Path
import argparse
import hashlib
import json
import platform
import subprocess
import time
import uuid

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--python", required=True, type=Path)
parser.add_argument("--model-dir", required=True, type=Path)
parser.add_argument("--baseline", required=True, type=Path)
parser.add_argument("--candidate", required=True, type=Path)
args = parser.parse_args()
root = args.output
root.mkdir(mode=0o700)
project = Path(__file__).resolve().parents[1]
workload = json.loads((project / "core/testdata/retrieval-quality.json").read_text())
baseline = args.baseline.resolve()
candidate = args.candidate.resolve()
commands = {
    label: [
        str(args.python.absolute()),
        str(worker),
        "--model-dir",
        str(args.model_dir.resolve()),
    ]
    for label, worker in [("baseline", baseline), ("candidate", candidate)]
}


def note(n):
    return dict(
        record_id=str(uuid.uuid5(uuid.NAMESPACE_URL, n["id"])),
        version=1,
        body=n["body"],
        body_sha256=hashlib.sha256(n["body"].encode()).hexdigest(),
    )


notes = [note(n) for n in workload["notes"]]
cases = [dict(id=q["id"], query=q["query"], notes=notes) for q in workload["queries"]]
longbody = (
    "Routine service administration follows the selected runbook. " * 240
) + "\n\nThe zephyr service credential is stored at /run/zephyr/access-key."
cases.append(
    dict(
        id="long-tail",
        query="Where is the zephyr service credential?",
        notes=[note(dict(id="long-tail", body=longbody))],
    )
)
cases.append(
    dict(
        id="64-short",
        query="How do I start the local database?",
        notes=[
            note(
                dict(
                    id="short-" + str(i), body=workload["notes"][i % len(notes)]["body"]
                )
            )
            for i in range(64)
        ],
    )
)
cases += [
    dict(id="repeat-storage-" + str(i), query=cases[0]["query"], notes=notes)
    for i in range(2)
]
env = dict(
    PATH="/usr/bin:/bin",
    HF_HUB_OFFLINE="1",
    HF_HUB_DISABLE_TELEMETRY="1",
    TOKENIZERS_PARALLELISM="false",
)
plan = dict(
    source_commit=subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=project, text=True
    ).strip(),
    cases=[
        dict(
            id=c["id"],
            notes=len(c["notes"]),
            body_bytes=sum(len(n["body"].encode()) for n in c["notes"]),
        )
        for c in cases
    ],
    baseline_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
    workload_sha256=hashlib.sha256(
        (project / "core/testdata/retrieval-quality.json").read_bytes()
    ).hexdigest(),
    python=str(args.python.absolute()),
    platform=platform.platform(),
    commands=commands,
    candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
    boundary="One-shot subprocess launch through output; no API or DB",
    deadline_seconds=20,
    order="alternate baseline/candidate first across cases",
    metrics=[
        "wall_seconds",
        "CPU_seconds",
        "max_RSS_KiB",
        "exact identity-bound integer score equality",
    ],
    target="Report paired latency and exact scores; no universal performance or task-benefit claim",
    constraints="Same prepared model, query and passages; worker settings are defined by the hashed input scripts; public synthetic inputs only",
)
(root / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
rows = []
for i, c in enumerate(cases):
    row = dict(id=c["id"], conditions={})
    raw = json.dumps(dict(query=c["query"], notes=c["notes"]))
    (root / (c["id"] + "-request.json")).write_text(raw)
    for label in ["baseline", "candidate"] if i % 2 == 0 else ["candidate", "baseline"]:
        chosen = commands[label]
        measure = root / (c["id"] + "-" + label + "-resources.json")
        out = root / (c["id"] + "-" + label + ".json")
        start = time.perf_counter()
        # Timeout owns the entire worker process group. The time wrapper waits for it.
        invocation = [
            "/usr/bin/time",
            "-f",
            '{"user_seconds":%U,"system_seconds":%S,"max_rss_kib":%M}',
            "-o",
            str(measure),
            "timeout",
            "--kill-after=1",
            "20",
            *chosen,
        ]
        p = subprocess.run(
            invocation, input=raw, env=env, text=True, capture_output=True, timeout=25
        )
        elapsed = time.perf_counter() - start
        out.write_text(p.stdout)
        if p.stderr:
            (root / (c["id"] + "-" + label + ".stderr")).write_text(p.stderr)
        stats = json.loads(measure.read_text().splitlines()[-1])
        stats.update(wall_seconds=elapsed, exit_code=p.returncode)
        if p.returncode == 0:
            stats["result"] = json.loads(p.stdout)
        row["conditions"][label] = stats
    a, b = row["conditions"]["baseline"], row["conditions"]["candidate"]
    row["exact_scores_equal"] = (
        a["result"]["scores"] == b["result"]["scores"]
        if a["exit_code"] == b["exit_code"] == 0
        else None
    )
    if a["exit_code"] == b["exit_code"] == 0:
        row["speedup"] = a["wall_seconds"] / b["wall_seconds"]
        row["changed_scores"] = [
            dict(record_id=x["record_id"], baseline=x["score"], candidate=y["score"])
            for x, y in zip(a["result"]["scores"], b["result"]["scores"])
            if x != y
        ]
    rows.append(row)
    (root / "report.json").write_text(
        json.dumps(dict(plan=plan, rows=rows), indent=2) + "\n"
    )
    print(
        json.dumps(
            dict(
                id=c["id"],
                baseline=a["wall_seconds"],
                candidate=b["wall_seconds"],
                exit_codes=[a["exit_code"], b["exit_code"]],
                exact=row["exact_scores_equal"],
            )
        ),
        flush=True,
    )
