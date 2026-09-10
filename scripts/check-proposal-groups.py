"""Operator CLI review path, using only the suite's disposable store."""
import hashlib
import json
import os
import subprocess
import sys
import uuid

from check_proposal_versions import check as check_versions


binary, artifact_home = sys.argv[1:]
env = dict(os.environ, CAIRN_HOME=artifact_home)
repo = "fixture:proposal-groups"


def request(command, payload=None, *args):
    result = subprocess.run(
        [binary, command, *args], env=env, text=True, capture_output=True,
        input=json.dumps(payload) if payload is not None else None,
        check=True, timeout=30,
    )
    response = json.loads(result.stdout)
    assert response["ok"], response
    return response["data"]


sources = {}
signature = hashlib.sha256(b"synthetic command exits one").hexdigest()
for task in ("task-a", "task-b"):
    process = subprocess.run(
        [binary, "run", "--repo", repo, "--task", task,
         "--task-class", "repair", "--binding", "fixture:local",
         "--capability", "fixture:false", "--", "/bin/false"],
        env=env, text=True, capture_output=True, timeout=30,
    )
    assert process.returncode == 1, process
    response = json.loads(process.stderr)
    assert response["ok"], response
    receipt = response["data"]["receipt_id"]
    evidence = request("capture-evidence", {
        "request_id": str(uuid.uuid4()), "repo": repo,
        "body": f"Synthetic {task}: wrapped /bin/false exited one.",
        "source": "proposal-group-cli-fixture/1",
    })
    assessment = request("assess-run", {
        "request_id": str(uuid.uuid4()), "receipt_id": receipt,
        "task_outcome": "rejected", "failure_domain": "task",
        "failure_kind": "fixture_exit", "error_signature_sha256": signature,
        "method": "operator-fixture-review/1",
        "evidence_ids": [evidence["evidence_id"]],
        "reason": "Explicit assessment of a synthetic task failure",
    })
    assert assessment["witness"] == "testimony", assessment
    sources[receipt] = (task, evidence["evidence_id"], assessment["version"])

batch = request("generate-proposals", {"request_id": str(uuid.uuid4()), "repo": repo})
assert len(batch["proposals"]) == 2 and not batch["more"], batch
docket = request("docket", None, repo)
assert len(docket["items"]) == 1 and not docket["truncated"], docket
row = docket["items"][0]
assert row["reason"] == "FAILURE_CLUSTER" and row["task_count"] == 2, row
key = row["proposal_group_key"]
first = request("proposal-group", None, "--limit", "1", repo, key)
assert first["more"] and first["next_offset"] == 1, first
second = request("proposal-group", None, "--limit", "1", "--offset", "1", repo, key)
assert not second["more"] and second["next_offset"] == 2, second
assert first["summary"]["testimony_count"] == 2, first
assert first["summary"]["instrumented_count"] == 0, first
members = first["members"] + second["members"]
assert len(members) == 2, members
seen = set()
for member in members:
    proposal = member["proposal"]
    receipt = proposal["failure_receipt"]
    assert receipt not in seen, members
    seen.add(receipt)
    task, evidence_id, version = sources[receipt]
    assert member["failure_scope"]["task_id"] == task, member
    assert proposal["evidence_ids"] == [evidence_id], member
    assert proposal["failure_version"] == version and proposal["source_current"], member
    assert member["failure_witness"] == "testimony", member
    assert member["failure_observer"] == f"local-uid:{os.geteuid()}", member
assert seen == sources.keys(), members

proposal = members[0]["proposal"]
request("review-proposal", {
    "request_id": str(uuid.uuid4()), "proposal_id": proposal["proposal_id"],
    "expected_version": proposal["version"], "disposition": "dismissed",
    "reason": "Dismiss only this fixture source",
})
remaining = request("docket", None, repo)["items"]
assert len(remaining) == 1, remaining
assert remaining[0]["reason"] == "TASK_FAILURE", remaining
assert remaining[0]["proposal_group_key"] == key, remaining
assert remaining[0]["proposal_id"] == members[1]["proposal"]["proposal_id"], remaining
group = request("proposal-group", None, repo, key)
assert group["summary"]["proposal_count"] == 1 and len(group["members"]) == 1, group
assert group["members"][0]["proposal"]["disposition"] == "open", group
print("Proposal grouping CLI preserves paginated sources, testimony and individual review decisions")

check_versions(binary, env, members[1]["proposal"])
