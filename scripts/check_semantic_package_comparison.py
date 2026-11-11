"""Compare retained package captures with frozen Python outputs; run in isolation.

This reporter launches no worker and never captures a replacement reference.
"""

import argparse
import collections
import importlib.util
import itertools
import json
from pathlib import Path
import runpy
import struct
import zipfile


def load(path):
    return json.loads(Path(path).read_text())


def frames(path):
    raw = Path(path).read_bytes()
    if raw and not raw.endswith(b"\n"):
        raise ValueError("incomplete JSONL capture")
    return [json.loads(line) for line in raw.splitlines()]


def exact_frames(actual, ids):
    if [row.get("id") for row in actual] != ids or len(set(ids)) != len(ids):
        raise ValueError("missing, duplicate, reordered or unexpected capture ID")


def distribution(deltas):
    return dict(signed=dict(collections.Counter(deltas)),
                absolute=dict(collections.Counter(map(abs, deltas))),
                maximum_absolute=max(map(abs, deltas), default=None),
                exact=sum(x == 0 for x in deltas),
                within1=sum(abs(x) <= 1 for x in deltas),
                within500=sum(abs(x) <= 500 for x in deltas), paired=len(deltas))


def score_comparison(actual, references, expected_identity):
    exact_frames(actual, [row["id"] for row in references])
    deltas, changed, pair_count = [], [], 0
    for frame, ref in zip(actual, references, strict=True):
        result = frame["result"]
        if result["model_sha256"] != expected_identity or result["algorithm"] != "bge-max-chunk/1":
            raise ValueError("actual package rank identity or algorithm mismatch")
        got, want = result["scores"], ref["scores"]
        if len(got) != len(want):
            raise ValueError("score count mismatch")
        for a, b in zip(got, want, strict=True):
            if any(a[k] != b[k] or type(a[k]) is not type(b[k])
                   for k in ("record_id", "version", "body_sha256")):
                raise ValueError("score order/source metadata mismatch")
            if type(a["score"]) is not int or not -1000000 <= a["score"] <= 1000000:
                raise ValueError("invalid integer score")
            deltas.append(a["score"] - b["score"])
        for i, j in itertools.combinations(range(len(want)), 2):
            before, after = want[i]["score"] - want[j]["score"], got[i]["score"] - got[j]["score"]
            pair_count += 1
            if (before > 0) - (before < 0) != (after > 0) - (after < 0):
                changed.append(dict(case=frame["id"], i=i, j=j,
                                    reference_margin=before, candidate_margin=after))
    return dict(scores=distribution(deltas), pair_relationships=pair_count,
                changed_pairs=changed, outside_neartie_breaches=0 if not changed else None,
                near_tie_scope="No invented threshold; changed margins require owner interpretation.")


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def raw_references(reference, rank_root):
    occurrences = load(reference / "occurrences.json")
    mapping = load(reference / "coverage-and-row-mapping.json")["occurrence_mapping"]
    vectors, metadata = {}, {}
    for case in mapping:
        root = rank_root / "raw" / case["case_id"]
        trace = load(root / "trace.json")
        with zipfile.ZipFile(root / "trace-arrays.npz") as archive:
            rows = reference.math["npy"](archive.read("stack_before_second_norm.npy"))
        for item in case["occurrence_to_row"]:
            ordinal = item["ordinal"]
            vectors[ordinal] = rows[item["row"]]
            metadata[ordinal] = trace["row_order"][item["row"]]
    if len(occurrences) != 294 or len(vectors) != 294:
        raise ValueError("frozen 294-row inventory mismatch")
    return occurrences, vectors, metadata


class Reference:
    """Paths plus the unchanged accepted vector-decoding and distance helpers."""

    def __init__(self, path, metadata):
        self.path = path
        self.metadata = metadata
        self.math = runpy.run_path(str(path / "compare_cache.py"), run_name="cache_math")

    def __truediv__(self, name):
        return self.path / name


def pair(reference, actual, math, **metadata):
    gap = math["distance"](reference, actual)
    return dict(metadata, cosine_gap=gap,
                bit_exact=struct.pack("<384f", *reference) == struct.pack("<384f", *actual))


def vector_summary(rows):
    distances = [abs(row["cosine_gap"]) for row in rows]
    return dict(paired=len(rows), within1e_6=sum(x <= 1e-6 for x in distances),
                maximum_absolute_cosine_gap=max(distances, default=None),
                absolute_gap_distribution=dict(collections.Counter(distances)),
                bit_exact=sum(row["bit_exact"] for row in rows))


def embedding_references(reference, rank_root, requests):
    projections = {row["id"]: row for row in load(reference / "expected-embedding-projections.json")}
    new = {row["id"]: row for row in frames(reference / "new-python-references.jsonl")}
    occurrences = load(reference / "occurrences.json")
    mapping = load(reference / "coverage-and-row-mapping.json")["occurrence_mapping"]
    rows = {item["ordinal"]: (case["case_id"], item["row"])
            for case in mapping for item in case["occurrence_to_row"]}
    expected = {}
    for request in requests:
        cid = request["id"]
        path = reference / "positive-refs" / (cid + ".json")
        if path.exists():
            reply = load(path)
        elif cid == "document-maximum-latin":
            reply = new[cid]
        else:
            ordinal = int(cid.rsplit("-", 1)[1])
            case, row = rows[ordinal]
            with zipfile.ZipFile(rank_root / "raw" / case / "trace-arrays.npz") as archive:
                first = reference.math["npy"](archive.read("stack_before_second_norm.npy"))[row]
            parts = projections[cid]["passages"]
            tail = new["gap-tail-" + parts[1]["text_sha256"][:16]]["passages"][0]["vector"]
            if reference.math["sha"](request["text"].encode()) != occurrences[ordinal]["text_sha256"]:
                raise ValueError("frozen gap input mismatch")
            reply = {"passages": [{"vector": first}, {"vector": tail}]}
        expected[cid] = ([reply["vector"]] if request["operation"] == "query"
                         else [part["vector"] for part in reply["passages"]])
    return expected, projections


def compare_embedding(requests, actual, reference, rank_root, expected_identity):
    exact_frames(actual, [row["id"] for row in requests])
    expected, projections = embedding_references(reference, rank_root, requests)
    pairs = []
    for request, reply in zip(requests, actual, strict=True):
        cid = request["id"]
        identity = reply["identity"]
        if identity != dict(model_sha256=expected_identity, algorithm="bge-original-passages/1", dimensions=384):
            raise ValueError("actual package embedding identity mismatch")
        if request["operation"] == "query":
            if reply["query_projection"] != projections[cid]["query_projection"]:
                raise ValueError("query projection/token metadata mismatch")
            got = [reply["vector"]]
        else:
            got = [part["vector"] for part in reply["passages"]]
            if [part["span"] for part in reply["passages"]] != [part["span"] for part in projections[cid]["passages"]]:
                raise ValueError("original UTF8 span/count mismatch")
            texts = reference.math["passage_texts"](request["text"], reply["passages"])
            if [x["text_sha256"] for x in texts] != [x["text_sha256"] for x in projections[cid]["passages"]]:
                raise ValueError("original passage substring mismatch")
        if len(got) != len(expected[cid]):
            raise ValueError("embedding vector count mismatch")
        pairs.extend(pair(want, value, reference.math, case=cid, vector_ordinal=i)
                     for i, (want, value) in enumerate(zip(expected[cid], got, strict=True)))
    if len(pairs) != 549:
        raise ValueError("required 549 embedding vectors missing")
    return pairs


def stream_wire(capture, stream_helper):
    matches = []
    rejected = []
    # Go thread files can contain partial parent I/O. Only the complete Rust
    # pipe has 38 cache-bearing replies; malformed candidate pipes still fail.
    for path in capture.glob("stream-trace.*"):
        try:
            wire = stream_helper.trace_frames(path.read_text())
        except ValueError as exc:
            rejected.append(dict(path=path.name, reason=str(exc)))
            continue
        if len(wire["write"]) == 38 and all("cache" in row and "id" in row for row in wire["write"]):
            matches.append(wire)
    if len(matches) != 1:
        raise ValueError("expected one actual continuous 38-frame worker pipe")
    actual = frames(capture / "stream.stdout")
    inputs = frames(capture / "stream-inputs.jsonl")
    if len(matches[0]["read"]) != 38 or [x["result"] for x in matches[0]["write"]] != [x["result"] for x in actual] or [x["request"] for x in matches[0]["read"]] != [x["request"] for x in inputs] or any(a["id"] != b["id"] for a, b in zip(matches[0]["read"], matches[0]["write"], strict=True)):
        raise ValueError("worker pipe does not bind actual Go inputs and results")
    matches[0]["unselected_trace_fragments"] = rejected
    return matches[0]


def compare_raw(capture, reference, rank_root, wire, expected_rank, expected_embed):
    occurrences, expected, metadata = raw_references(reference, rank_root)
    queries = {row["id"]: row for row in frames(capture / "embed.stdout")[37:]}
    rank_cases = {row["id"]: row for row in load(reference / "rank-cases.json")}
    token_ids = load(reference.metadata / "python-ids.json")
    scores = wire["write"]
    results = []
    for occurrence, ids in zip(occurrences, token_ids, strict=True):
        ordinal, case = occurrence["ordinal"], occurrence["case_id"]
        case_index = list(rank_cases).index(case)
        frame = scores[case_index * 2]
        cache = frame["cache"]
        if cache["format"] != "cairn.passage-vectors/1" or cache["model_sha256"] != expected_rank:
            raise ValueError("actual raw-cache identity mismatch")
        if occurrence["kind"] == "query":
            reply = queries["rank-query/" + case]
            projection = reply["query_projection"]
            text = rank_cases[case]["query"]
            if reply["identity"]["model_sha256"] != expected_embed or projection["truncated"] or projection["prefix_bytes"] != len(text.encode()) or projection["prefix_sha256"] != reference.math["sha"](text.encode()) or projection["original_tokens"] != len(ids) or projection["embedded_tokens"] != len(ids):
                raise ValueError("frozen rank-query projection/token binding mismatch")
            actual = reply["vector"]
        else:
            key = occurrence["text_sha256"]
            info = metadata[ordinal]
            note = rank_cases[case]["notes"][info["note_index"]]
            score = frame["result"]["scores"][info["note_index"]]
            if info["passage_sha256"] != key or any(note[k] != score[k] for k in ("record_id", "version", "body_sha256")):
                raise ValueError("raw cache source/row binding mismatch")
            actual = reference.math["cached"](cache["vectors"][key])
        results.append(pair(expected[ordinal], actual, reference.math,
                            ordinal=ordinal, case=case, kind=occurrence["kind"],
                            text_sha256=occurrence["text_sha256"], model_tokens=len(ids)))
    return results


def require_rule_c(report):
    if report["rank"]["scores"]["within500"] != 269 or report["stream"]["scores"]["within500"] != 538:
        raise ValueError("Rule C score bound exceeded")
    for key, count in (("embedding", 549), ("raw_firstnormalized", 294), ("distinct_union", 821)):
        if report[key]["paired"] != count or report[key]["within1e_6"] != count:
            raise ValueError("Rule C vector bound or required coverage violated")
    if report["rank"]["changed_pairs"] or report["stream"]["changed_pairs"]:
        raise ValueError("rank relationships changed; report margins for owner near-tie interpretation")


def stream_references(helper, manifest_path, plan, actual, rank_refs):
    retained = helper.retained_reference(manifest_path, plan, actual)
    if retained["verified_frames"] != 34 or retained["paired_score_entries"] != 408:
        raise ValueError("retained 34-frame Python reference missing")
    if retained["input_sha256"] != "cd62bf92204d9a5457fd69b77bfb387fcecb0d1d1a59791b98f806e1c708182b" or retained["output_sha256"] != "60100b209f91ceabf7a24a65e88fd1e549c054652c4c4561f03aabf1e19767a7":
        raise ValueError("frozen historical stream bytes changed")
    record = next(row for row in load(manifest_path)["records"]
                  if row["name"] == "retrieval-quality-stream")
    for i, (request, reply) in enumerate(zip(frames(record["input_path"]), frames(record["stdout"]), strict=True)):
        _, mapped, _ = helper.mapped_reference(request["request"], reply)
        reference = rank_refs[i // 2]
        if mapped["scores"] != reference["scores"] or mapped["model_sha256"] != reference["model_sha256"]:
            raise ValueError("historical stream/rank reference equivalence failed")
    return dict(retained, captured_scores_rank_equal=408, rank_backed_frames=4,
                rank_backed_scores=130,
                selection_decision="d8b70f55-48fb-4c27-bfc5-f8405c56fe97/v1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("capture", "reference", "rank-metadata", "rank-inputs", "rank-arrays", "stream-manifest", "package-manifest", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir()
    reference = Reference(args.reference, args.rank_metadata)
    package = load(args.package_manifest)
    rank_id, embed_id = (package["derivations"][x]["identity"] for x in ("rank", "embed"))
    rank_refs = load(args.rank_inputs / "rank-reference-scores.json")
    rank = score_comparison(frames(args.capture / "rank.stdout"), rank_refs, rank_id)
    stream_refs = [dict(ref, id="stream/" + ref["id"] + "/" + phase)
                   for ref in rank_refs for phase in ("cold", "warm")]
    stream = score_comparison(frames(args.capture / "stream.stdout"), stream_refs, rank_id)
    helper = module(Path(__file__).with_name("semantic_stream_evidence.py"), "stream_helper")
    requests = frames(args.capture / "rank-inputs.jsonl")
    inventory = load(Path(__file__).parents[1] / "semantic/testdata/executable-harness/reference-inventory.json")
    plan = helper.stream_plan(inventory, requests)
    retained = stream_references(helper, args.stream_manifest, plan,
                                 frames(args.capture / "stream.stdout"), rank_refs)
    wire = stream_wire(args.capture, helper)
    embedding_requests = frames(args.capture / "embed-inputs.jsonl")[:37]
    embedding = compare_embedding(embedding_requests, frames(args.capture / "embed.stdout")[:37], reference, args.rank_arrays, embed_id)
    raw = compare_raw(args.capture, reference, args.rank_arrays, wire, rank_id, embed_id)
    overlaps = {x["ordinal"] for x in load(reference.metadata / "prior-unpaired.json")}
    union = embedding + [row for row in raw if row["ordinal"] not in overlaps]
    if len(union) != 821 or rank["scores"]["paired"] != 269 or stream["scores"]["paired"] != 538:
        raise ValueError("frozen case/vector denominator mismatch")
    report = dict(rank=rank, stream=stream, stream_reference=retained,
                  embedding=vector_summary(embedding), raw_firstnormalized=vector_summary(raw),
                  distinct_union=vector_summary(union), overlap=22,
                  vector_pairs=union, raw_rows=raw,
                  raw_tensor_scope="294 frozen token/tensor-construction bindings reused with unchanged worker bytes; no new wire tensor observation.",
                  scope="Selected local BLAS2 numerics; no full fleet/runtime/production qualification.")
    (args.output / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    summary = {k: v for k, v in report.items() if k not in ("vector_pairs", "raw_rows", "stream_reference")}
    print(json.dumps(summary, indent=2))
    require_rule_c(report)


if __name__ == "__main__":
    main()
