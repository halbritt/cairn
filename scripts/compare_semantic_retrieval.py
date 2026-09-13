#!/usr/bin/env python3
"""Measure local semantic ranking on the frozen public documentation workload.

This optional experiment does not connect to Cairn or implement eligibility,
packing, no-answer detection, or production retrieval. Dependencies are separate
from the Go application: fastembed==0.8.0 (CPU ONNX runtime).
"""

import argparse
import hashlib
import importlib.metadata
import json
import platform
import re
import time
from collections import Counter
from pathlib import Path

MODEL = "BAAI/bge-small-en-v1.5"
PREFIX = "Represent this sentence for searching relevant passages: "
STOP = set("a an and are as at be by for from in is it of on or that the this "
           "to was were with what where when why who whom whose which how do "
           "does did".split())


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def terms(text):
    # Mirrors ranker v4 for this ASCII-only development corpus. Runtime lexical
    # semantics remain owned by core/compiler.go, not this experiment.
    words = set(re.findall(r"[a-z0-9_]+", text.lower()))
    return (words | {part for word in words for part in word.split("_") if part}) - STOP


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path,
                        help="new directory; existing results are never overwritten")
    parser.add_argument("--cache", required=True, type=Path,
                        help="local model cache; first use downloads model files")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    source = repo / "core/testdata/retrieval-quality.json"
    workload = json.loads(source.read_text())
    notes, queries = workload["notes"], workload["queries"]
    ids = [n["id"] for n in notes]
    if len(set(ids)) != len(ids) or len({q["id"] for q in queries}) != len(queries):
        raise ValueError("duplicate workload identifiers")
    for note in notes:
        if hashlib.sha256(note["body"].encode()).hexdigest() != note["sha256"]:
            raise ValueError(f"source bytes changed: {note['id']}")
        if not note["body"].isascii():
            raise ValueError("lexical comparator supports this ASCII corpus only")
    for query in queries:
        if not query["query"].isascii() or not set(query["relevant"]) <= set(ids):
            raise ValueError(f"unsupported query or relevance label: {query['id']}")
    if importlib.metadata.version("fastembed") != "0.8.0":
        raise ValueError("this experiment requires fastembed==0.8.0")

    args.output.mkdir(mode=0o700)
    plan = dict(
        schema="cairn.semantic-retrieval-plan/1", workload_sha256=sha256(source),
        script_sha256=sha256(__file__), model=MODEL, query_prefix=PREFIX,
        provider="CPUExecutionProvider", threads=2, batch_size=16,
        methods=["lexical-v4", "inverse-document-frequency", "cosine"],
        metrics=["answer rank", "top one", "top three", "no-answer top score"],
        ties="lexical uses forward/reverse insertion recency; cosine uses note ID",
        scope="Ranking only; no store, eligibility, packing or answer generation",
        constraints=["One model; no parameter or threshold tuning after results",
                     "No truncation; refuse inputs above 512 tokens",
                     "No-answer controls have no calibrated refusal threshold",
                     "Development workload; no generalization or task-benefit claim"],
        python=platform.python_version(),
        packages={name: importlib.metadata.version(name) for name in
                  ("fastembed", "onnxruntime", "numpy", "tokenizers", "huggingface-hub")},
    )
    write_json(args.output / "plan.json", plan)

    import numpy as np
    from fastembed import TextEmbedding
    from tokenizers import Tokenizer

    started = time.monotonic()
    model = TextEmbedding(model_name=MODEL, cache_dir=str(args.cache), threads=2,
                          providers=["CPUExecutionProvider"], cuda=False)
    load_seconds = time.monotonic() - started
    # Pinned FastEmbed internals supply artifact identity and an untruncated
    # tokenizer copy. Refuse API drift rather than silently omit either check.
    model_dir = Path(model.model._model_dir)
    tokenizer = Tokenizer.from_str(model.model.tokenizer.to_str())
    tokenizer.no_truncation()
    tokenizer.no_padding()
    passages = [n["body"] for n in notes]
    questions = [PREFIX + q["query"] for q in queries]
    lengths = [len(e.ids) for e in tokenizer.encode_batch(passages + questions)]
    if max(lengths) > 512:
        raise ValueError("workload exceeds model context; no truncated result admitted")
    artifacts = [dict(path=str(p.relative_to(model_dir)), sha256=sha256(p),
                      bytes=p.stat().st_size) for p in sorted(model_dir.rglob("*"))
                 if p.is_file() and ".cache" not in p.parts]
    write_json(args.output / "model.json", dict(artifacts=artifacts,
               input_tokens=lengths, max_input_tokens=max(lengths), load_seconds=load_seconds))
    started = time.monotonic()
    docs = np.array(list(model.embed(passages, batch_size=16)))
    questions_embedded = np.array(list(model.embed(questions, batch_size=16)))
    embed_seconds = time.monotonic() - started
    if docs.shape != (len(notes), 384) or questions_embedded.shape != (len(queries), 384):
        raise ValueError("unexpected embedding cardinality or dimension")
    for values in (docs, questions_embedded):
        norms = np.linalg.norm(values, axis=1, keepdims=True)
        if not np.isfinite(values).all() or (norms == 0).any():
            raise ValueError("invalid embedding")
        values /= norms
    np.savez(args.output / "vectors.npz", documents=docs, queries=questions_embedded)
    similarities = questions_embedded @ docs.T
    words = [terms(n["body"]) for n in notes]
    frequency = Counter(word for document in words for word in document)
    results = []
    for qi, query in enumerate(queries):
        matched = [terms(query["query"]) & document for document in words]
        for method in plan["methods"]:
            scores = ([float(s) for s in similarities[qi]] if method == "cosine" else
                      [sum(1 if method == "lexical-v4" else 1_000_000 // frequency[t]
                           for t in matches) for matches in matched])
            for order in (["stable-id"] if method == "cosine" else ["forward", "reverse"]):
                candidates = [i for i in range(len(notes)) if method == "cosine" or scores[i] > 0]
                ranked = sorted(candidates, key=lambda i: (
                    -scores[i], ids[i] if order == "stable-id" else (-i if order == "forward" else i)))
                answer_ranks = [rank + 1 for rank, i in enumerate(ranked) if ids[i] in query["relevant"]]
                results.append(dict(query_id=query["id"], method=method, order=order,
                                    answer_rank=min(answer_ranks, default=None),
                                    ranked=[dict(id=ids[i], score=scores[i]) for i in ranked]))
    summary = []
    answerable = {q["id"] for q in queries if q["relevant"]}
    for method, order in dict.fromkeys((r["method"], r["order"]) for r in results):
        rows = [r for r in results if r["method"] == method and r["order"] == order
                and r["query_id"] in answerable]
        summary.append(dict(method=method, order=order, answerable=len(rows),
                            first=sum(r["answer_rank"] == 1 for r in rows),
                            top_three=sum(r["answer_rank"] is not None and r["answer_rank"] <= 3 for r in rows)))
    report = dict(schema="cairn.semantic-retrieval-report/1", plan_sha256=sha256(args.output / "plan.json"),
                  model_sha256=sha256(args.output / "model.json"), vectors_sha256=sha256(args.output / "vectors.npz"),
                  embed_seconds=embed_seconds, results=results, summary=summary)
    write_json(args.output / "report.json", report)
    print(json.dumps(dict(summary=summary, embed_seconds=embed_seconds), indent=2))


if __name__ == "__main__":
    main()
