#!/usr/bin/env python3
"""Build the reviewed query-only update for the pinned legacy lifecycle engine.

This creates a new artifact; it never installs it or changes configuration.
The donor is integrations/lifecycle/memory.py from revision 0d857cd2357afd36d8c75d8fa0e8c26f9d3d5485.
Other preimages or donor revisions require a separate compatibility review.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path


BASE_SHA256 = "2ebf92bcc40f50755db6b680e61aea0787c898b3c608d1e5a88dd1f257153c7a"
DONOR_SHA256 = "0bdcd0879dea2b6d1f5babea1228c9c771cb1fb3d04500b2e81e139ea725735b"
FUNCTIONS = {"term_words", "terms", "retrieval_intent"}


def checked_source(path, expected):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f"unreviewed source: {path}")
    return data.decode("utf-8")


def compose(base, donor):
    original = ast.parse(base)
    source = ast.parse(donor)
    definitions = {node.name: ast.get_source_segment(donor, node)
                   for node in source.body
                   if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS}
    if set(definitions) != FUNCTIONS:
        raise ValueError("donor definitions are incomplete")
    lines = base.splitlines(keepends=True)
    targets = [node for node in original.body
               if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
    if [node.name for node in targets] != ["terms", "retrieval_intent"]:
        raise ValueError("unexpected base definitions")
    for node in reversed(targets):
        replacement = definitions[node.name]
        if node.name == "terms":
            replacement = definitions["term_words"] + "\n\n\n" + replacement
        lines[node.lineno - 1:node.end_lineno] = [replacement + "\n"]
    result = "".join(lines)
    assembled = ast.parse(result)
    for tree in (original, assembled):
        tree.body = [node for node in tree.body
                     if not (isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS)]
    if ast.dump(original) != ast.dump(assembled):
        raise ValueError("composition changed code outside the three definitions")
    return result.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--donor", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    candidate = compose(checked_source(args.base, BASE_SHA256),
                        checked_source(args.donor, DONOR_SHA256))
    with args.output.open("xb") as output:
        output.write(candidate)
    print(json.dumps({"base_sha256": BASE_SHA256, "donor_sha256": DONOR_SHA256,
                      "candidate_sha256": hashlib.sha256(candidate).hexdigest(),
                      "functions": sorted(FUNCTIONS)}))


if __name__ == "__main__":
    main()
