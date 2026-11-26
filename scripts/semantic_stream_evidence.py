"""Capture continuous Go/Rust stream frames and a separately observed idle handoff.

Run inside semantic_acceptance_isolation. strace observes the actual Rust pipe;
the production Go driver and supervisors are used without an executable proxy.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from semantic_fixture_cases import _note


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def stream_plan(inventory, requests):
    ids = inventory["rank_ids"]
    if len(ids) != 19 or [x["id"] for x in requests] != ids:
        raise ValueError("requests differ from the frozen 19-case order")
    cases = [dict(id=x["id"], **x["request"]) for x in requests]
    if digest(canonical(cases)) != inventory["fixture_sha256"]["rank"]:
        raise ValueError("request content differs from the frozen fixture digest")
    cells = [x for x in inventory["required_reference_cells"] if x.startswith("stream/") and x != "stream/idle-restore"]
    expected = [f"stream/{case}/{phase}" for case in ids for phase in ("cold", "warm")]
    if cells != expected or inventory["expected_counts"]["stream_replies"] != 38:
        raise ValueError("frozen 38-frame reference order changed")
    return [dict(cell=cell, inventory_phase=cell.rsplit("/", 1)[1],
                 actual_phase="initial_cold" if i == 0 else "warm_same_child",
                 request=requests[i // 2]["request"]) for i, cell in enumerate(cells)]


def compare_scores(result, reference):
    scores, expected = result["scores"], reference["scores"]
    if result["algorithm"] != reference["algorithm"] or len(scores) != len(expected):
        raise ValueError("result algorithm or score count differs")
    deltas = []
    for got, want in zip(scores, expected):
        if any(got[k] != want[k] for k in ("record_id", "version", "body_sha256")):
            raise ValueError("score order/source metadata differs")
        if type(got["score"]) is not int or not -1000000 <= got["score"] <= 1000000:
            raise ValueError("invalid integer score")
        delta = got["score"] - want["score"]
        if abs(delta) > 500:
            raise ValueError("rule C score bound exceeded")
        deltas.append(delta)
    return deltas


def trace_frames(text):
    """Reassemble successful read(0)/write(1) bytes, including partial writes."""
    streams = {"read": bytearray(), "write": bytearray()}
    pending = ""
    for line in text.splitlines():
        if "<unfinished ...>" in line and line.startswith(("read(0,", "write(1,")):
            pending = line.split("<unfinished ...>", 1)[0]
            continue
        if line.startswith("<... ") and " resumed>" in line and pending:
            line = pending + line.split(" resumed>", 1)[1]
            pending = ""
        match = re.match(r'^(read|write)\(([01]), (.*)\)\s+=\s+([0-9]+)', line)
        if not match:
            continue
        kind, fd, arguments, count = match.groups()
        if (kind, fd) not in (("read", "0"), ("write", "1")):
            continue
        count = int(count)
        if not count:
            continue
        raw = re.match(r'^"((?:\\x[0-9a-fA-F]{2})*)",\s+[0-9]+$', arguments.lstrip())
        if raw is None:
            raise ValueError("truncated or unsupported protocol trace string")
        data = bytes.fromhex(raw[1].replace("\\x", ""))
        if count > len(data):
            raise ValueError("protocol trace lost bytes")
        streams[kind].extend(data[:count])
    result = {}
    for kind, data in streams.items():
        if data and not data.endswith(b"\n"):
            raise ValueError("incomplete protocol frame in trace")
        result[kind] = [json.loads(line) for line in data.splitlines()]
    return result


def cache_handoff(previous, fresh_input):
    cache = previous.get("cache")
    if not isinstance(cache, dict) or not cache:
        raise ValueError("old worker did not supply a cache snapshot")
    if fresh_input.get("cache") != cache:
        raise ValueError("fresh worker did not receive the retained snapshot")
    raw = canonical(cache)
    return dict(cache_sha256=digest(raw), canonical_bytes=len(raw),
                exact_json_state_handoff=True,
                scope="observed old reply -> fresh child's first request; no ORT-call-count claim")


def mapped_reference(request, reply):
    """Apply the already committed fixture producer's alias-to-UUID mapping."""
    adapted = dict(query=request["query"], notes=[])
    aliases = {}
    for note in request["notes"]:
        produced = _note(note["record_id"], note["body"])
        if any(produced[k] != note[k] for k in ("version", "body", "body_sha256")):
            raise ValueError("retained reference source changed beyond the ID adapter")
        aliases[note["record_id"]] = produced["record_id"]
        adapted["notes"].append(produced)
    result = dict(reply["result"])
    result["scores"] = [dict(score, record_id=aliases[score["record_id"]]) for score in result["scores"]]
    return adapted, result, aliases


def retained_reference(manifest_path, plan, frames):
    manifest = json.loads(Path(manifest_path).read_text())
    record = next(x for x in manifest["records"] if x["name"] == "retrieval-quality-stream")
    source, replies = Path(record["input_path"]), Path(record["stdout"])
    if digest(source.read_bytes()) != record["input_sha256"] or digest(replies.read_bytes()) != record["stdout_sha256"] or record["exit_code"] != 0:
        raise ValueError("retained stream capture binding changed")
    inputs = [json.loads(x) for x in source.read_bytes().splitlines()]
    outputs = [json.loads(x) for x in replies.read_bytes().splitlines()]
    if len(inputs) != len(outputs) or len(inputs) > len(plan):
        raise ValueError("retained stream reference frame count differs")
    paired = 0
    mappings = []
    for i, (request, reply) in enumerate(zip(inputs, outputs)):
        adapted, result, aliases = mapped_reference(request["request"], reply)
        if adapted != plan[i]["request"] or reply["id"] != request["id"]:
            raise ValueError("retained stream sequence/input differs")
        compare_scores(frames[i]["result"], result)
        paired += len(reply["result"]["scores"])
        mappings.append(aliases)
    return dict(manifest=str(manifest_path), manifest_sha256=digest(Path(manifest_path).read_bytes()),
                input_sha256=record["input_sha256"], output_sha256=record["stdout_sha256"],
                verified_frames=len(outputs), paired_score_entries=paired,
                record_id_adapter="committed semantic_fixture_cases._note UUID5/URL; all numeric/source bytes retained",
                adapter_sha256=digest(Path(__file__).with_name("semantic_fixture_cases.py").read_bytes()),
                record_id_mappings=mappings,
                missing_cells=[x["cell"] for x in plan[len(outputs):]],
                full_38_reference_available=len(outputs) == 38)


def workers(executable):
    found = []
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            argv = (path / "cmdline").read_bytes().split(b"\0")
            if not argv or argv[0] != os.fsencode(executable):
                continue
            fields = (path / "stat").read_text().rsplit(") ", 1)[1].split()
            found.append(dict(pid=int(path.name), start_ticks=int(fields[19]),
                              argv=[os.fsdecode(x) for x in argv if x]))
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


class GoStream:
    def __init__(self, driver, config, output):
        self.output = output
        self.executable = json.loads(Path(config).read_text())["executable"]
        self.stderr = (output / "driver.stderr").open("wb")
        self.process = subprocess.Popen([
            "strace", "-ff", "-xx", "-s", "8388608", "--trace-fds=0,1",
            "-e", "trace=read,write", "-o", str(output / "trace"),
            driver, "--config", config,
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.stderr, bufsize=0)
        self.frames = []
        self.children = {}

    def exchange(self, cell, request):
        start = time.monotonic()
        self.process.stdin.write(canonical(dict(id=cell, request=request)) + b"\n")
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 32)[0]:
            raise RuntimeError("Go request produced no bounded reply (Go deadline remains 25s)")
        line = self.process.stdout.readline(65537)
        if not line.endswith(b"\n") or len(line) > 65536:
            raise ValueError("missing or oversized Go result")
        reply = json.loads(line)
        if reply["id"] != cell:
            raise ValueError("Go driver case ID changed")
        live = workers(self.executable)
        if len(live) != 1:
            raise ValueError("expected exactly one live Rust child")
        child = live[0]
        self.children[child["pid"]] = child
        self.frames.append(dict(cell=cell, result=reply["result"], child=child,
                                seconds=time.monotonic() - start))
        return reply["result"], child

    def close(self):
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.stderr.close()
        if self.process.returncode:
            raise RuntimeError(f"traced Go driver exited {self.process.returncode}")

    def wire(self, pid):
        return trace_frames((self.output / f"trace.{pid}").read_text())


def run(args):
    output = Path(args.output)
    output.mkdir(mode=0o700)
    inventory = json.loads(Path(args.inventory).read_text())
    requests = [json.loads(x) for x in Path(args.requests).read_bytes().splitlines()]
    reference = json.loads(Path(args.reference).read_text())
    if [x["id"] for x in reference] != inventory["rank_ids"]:
        raise ValueError("retained scalar reference order differs")
    references = {x["id"]: x for x in reference}
    plan = stream_plan(inventory, requests)
    manifest = {name: dict(path=getattr(args, name), sha256=digest(Path(getattr(args, name)).read_bytes()))
                for name in ("driver", "config", "inventory", "requests", "reference")}
    report = dict(inputs=manifest, planned_cells=[x["cell"] for x in plan],
                  reference_scope="retained 19-case scalar oracle reused for repeated frames; not a newly generated Python oracle",
                  stream={}, idle={})
    continuous = output / "continuous"
    continuous.mkdir()
    owner = GoStream(args.driver, args.config, continuous)
    try:
        deltas = []
        for item in plan:
            result, _ = owner.exchange(item["cell"], item["request"])
            case = item["cell"].split("/")[1]
            deltas += compare_scores(result, references[case])
        if len(owner.children) != 1:
            raise ValueError("continuous 38-frame sequence restarted its child")
    finally:
        owner.close()
    pid = next(iter(owner.children))
    wire = owner.wire(pid)
    if len(wire["read"]) != 38 or len(wire["write"]) != 38:
        raise ValueError("missing or extra continuous wire frames")
    if [x["id"] for x in wire["read"]] != [str(i) for i in range(1, 39)]:
        raise ValueError("Go protocol wire IDs did not preserve the single owner sequence")
    if "cache" in wire["read"][0] or any("cache" in x for x in wire["read"][1:]):
        raise ValueError("unexpected restore/reset inside continuous lifetime")
    report["stream"] = dict(replies=38, score_pairs=len(deltas),
                            exact=sum(x == 0 for x in deltas), max_abs=max(map(abs, deltas)),
                            children=list(owner.children.values()),
                            inventory_phases=[x["inventory_phase"] for x in plan],
                            actual_phases=[x["actual_phase"] for x in plan], frames=owner.frames,
                            wire_counts={k: len(v) for k, v in wire.items()})
    report["retained_stream_reference"] = retained_reference(args.retained_stream_manifest, plan, owner.frames)
    idle = output / "idle"
    idle.mkdir()
    owner = GoStream(args.driver, args.config, idle)
    request = requests[0]["request"]
    try:
        before, old = owner.exchange("stream/idle-restore/before", request)
        warm, same = owner.exchange("stream/idle-restore/warm", request)
        if old != same or before != warm:
            raise ValueError("idle case did not retain its initial child/results")
        started = time.monotonic()
        # A zombie has an empty cmdline too; require the original proc entry to
        # disappear so the evidence includes reaping, not only signal delivery.
        while Path("/proc", str(old["pid"])).exists():
            if time.monotonic() - started > 35:
                raise ValueError("old Rust child did not terminate after actual 30s idle")
            time.sleep(0.05)
        terminated = time.monotonic()
        if terminated - started < 29:
            raise ValueError("child terminated before the production idle lifetime")
        if workers(owner.executable):
            raise ValueError("unexpected Rust child before the explicit restart request")
        restored, fresh = owner.exchange("stream/idle-restore/after", request)
        if fresh == old or fresh["pid"] == old["pid"] or restored != before:
            raise ValueError("idle replacement PID/result contract failed")
        compare_scores(restored, reference[0])
    finally:
        owner.close()
    previous, replacement = owner.wire(old["pid"]), owner.wire(fresh["pid"])
    if len(previous["write"]) != 2 or len(replacement["read"]) != 1:
        raise ValueError("idle wire frame counts differ")
    report["idle"] = dict(cases=1, exchanges=3, old_child=old, new_child=fresh,
                          old_terminated_after_seconds=terminated - started,
                          new_child_observed=True, old_child_absent_before_restart=True,
                          state=cache_handoff(previous["write"][-1], replacement["read"][0]),
                          frames=owner.frames)
    if workers(owner.executable):
        raise ValueError("Rust child survived Go owner close")
    report["cleanup"] = dict(go_drivers_reaped=True, live_rust_children=0)
    report["acceptance"] = "selected observations; missing retained reference cells and full S-02/other criteria remain explicit"
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(replies=38, score_pairs=len(deltas), exact=report["stream"]["exact"],
                         max_abs=report["stream"]["max_abs"], idle=report["idle"]["state"],
                         old_pid=old["pid"], new_pid=fresh["pid"],
                         idle_seconds=terminated-started, cleanup=report["cleanup"])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("driver", "config", "inventory", "requests", "reference", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--retained-stream-manifest", required=True)
    run(parser.parse_args())
