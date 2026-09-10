#!/usr/bin/env python3
"""Screen fixed BM25 against lexical v4 on the frozen public workload.

Ranking only: no Cairn database, eligibility, packing, or answering model.
Uses IR-book equation 86 with k1=1.2, b=0.75 and log(N/df) weights.
This reused development corpus cannot establish generalization or task value.
"""

import argparse
import json
import math
import platform
import re
from collections import Counter
from pathlib import Path

from compare_semantic_retrieval import STOP, sha256, terms, write_json


def frequencies(body):
    # Count each original token and its distinct underscore parts once per
    # occurrence. For this ASCII corpus, the keys equal lexical v4's term set.
    result = Counter()
    for word in re.findall(r"[a-z0-9_]+", body.lower()):
        result.update(({word} | set(word.split("_"))) - STOP - {""})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "core/testdata/retrieval-quality.json"
    workload = json.loads(source.read_text())
    notes, queries = workload["notes"], workload["queries"]
    ids = [note["id"] for note in notes]
    if not notes or len(set(ids)) != len(ids):
        raise ValueError("empty corpus or duplicate note IDs")
    if len({q["id"] for q in queries}) != len(queries):
        raise ValueError("duplicate query IDs")
    import hashlib
    for note in notes:
        if not note["body"].isascii():
            raise ValueError("only the frozen ASCII workload is supported")
        if hashlib.sha256(note["body"].encode()).hexdigest() != note["sha256"]:
            raise ValueError("note checksum mismatch")
    for query in queries:
        if not query["query"].isascii() or not set(query["relevant"]) <= set(ids):
            raise ValueError("unsupported query or relevance labels")
    counts = [frequencies(note["body"]) for note in notes]
    if any(set(count) != terms(note["body"]) for count, note in zip(counts, notes)):
        raise ValueError("candidate token vocabulary differs from baseline")
    lengths = [sum(count.values()) for count in counts]
    average = sum(lengths) / len(lengths)
    if average == 0:
        raise ValueError("empty token corpus")
    frequency = Counter(term for count in counts for term in count)
    args.output.mkdir(mode=0o700)
    plan = dict(
        schema="cairn.bm25-screen/1", workload_sha256=sha256(source),
        script_sha256=sha256(__file__),
        shared_comparator_sha256=sha256(Path(__file__).with_name("compare_semantic_retrieval.py")),
        python=platform.python_version(), k1=1.2, b=0.75,
        idf="log(N/df)", length="expanded non-stopword token occurrences",
        query_terms="distinct lexical-v4 terms", ties="forward/reverse insertion recency",
        metrics=["first relevant rank", "top one", "top three", "no-answer candidates"],
        decision="Screen only; no parameter tuning or production selection from this corpus",
        source="https://nlp.stanford.edu/IR-book/html/htmledition/okapi-bm25-a-non-binary-model-1.html",
        lengths=dict(zip(ids, lengths)),
    )
    write_json(args.output / "plan.json", plan)
    results = []
    for query in queries:
        query_terms = terms(query["query"])
        shared = [query_terms & count.keys() for count in counts]
        lexical = [len(matches) for matches in shared]
        bm25 = [sum(math.log(len(notes) / frequency[term]) *
                    (count[term] * (plan["k1"] + 1)) /
                    (count[term] + plan["k1"] *
                     (1 - plan["b"] + plan["b"] * length / average))
                    for term in sorted(matches))
                for count, length, matches in zip(counts, lengths, shared)]
        for method, scores in (("lexical-v4", lexical), ("bm25", bm25)):
            for order in ("forward", "reverse"):
                ranked = sorted((i for i, score in enumerate(scores) if score > 0),
                                key=lambda i: (-scores[i], -i if order == "forward" else i))
                answer_ranks = [rank + 1 for rank, i in enumerate(ranked)
                                if ids[i] in query["relevant"]]
                results.append(dict(query_id=query["id"], method=method, order=order,
                                    answer_rank=min(answer_ranks, default=None),
                                    ranked=[dict(id=ids[i], score=scores[i]) for i in ranked]))
    answerable = {q["id"] for q in queries if q["relevant"]}
    summary = []
    for method, order in dict.fromkeys((r["method"], r["order"]) for r in results):
        rows = [r for r in results if r["method"] == method and r["order"] == order
                and r["query_id"] in answerable]
        summary.append(dict(method=method, order=order, answerable=len(rows),
                            first=sum(r["answer_rank"] == 1 for r in rows),
                            top_three=sum(r["answer_rank"] is not None and r["answer_rank"] <= 3
                                          for r in rows)))
    write_json(args.output / "report.json", dict(plan_sha256=sha256(args.output / "plan.json"),
                                               results=results, summary=summary))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
