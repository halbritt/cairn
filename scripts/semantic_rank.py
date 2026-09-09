#!/usr/bin/env python3
"""Local CPU scoring worker; use a prepared model directory, never download on a query."""

import argparse
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path

MODEL = "BAAI/bge-small-en-v1.5"
PREFIX = "Represent this sentence for searching relevant passages: "
ALGORITHM = "bge-max-chunk/1"
WINDOW, STRIDE = 384, 320
BATCH_SIZE = 1


def chunks(tokenizer, body):
    tokens = tokenizer.encode(body, add_special_tokens=False).ids
    if not tokens:
        raise ValueError("note has no model tokens")
    for start in range(0, len(tokens), STRIDE):
        yield tokenizer.decode(tokens[start:start + WINDOW], skip_special_tokens=False)
        if start + WINDOW >= len(tokens):
            break


def validate_request(request):
    notes, query = request["notes"], request["query"]
    if not 1 <= len(notes) <= 64 or not query.strip() or len(query.encode()) > 4096:
        raise ValueError("worker requires a bounded query and 1-64 notes")
    if sum(len(n["body"].encode()) for n in notes) > 1024 * 1024:
        raise ValueError("note bytes exceed limit")
    for note in notes:
        if hashlib.sha256(note["body"].encode()).hexdigest() != note["body_sha256"]:
            raise ValueError("note content hash mismatch")


class Scorer:
    """Own the prepared model; request notes and vectors remain local to score."""

    def __init__(self, model_dir):
        versions = {name: importlib.metadata.version(name) for name in
                    ("fastembed", "onnxruntime", "numpy", "tokenizers")}
        if versions["fastembed"] != "0.8.0":
            raise ValueError("worker requires fastembed==0.8.0")
        files = {}
        for name in ("model_optimized.onnx", "config.json", "tokenizer.json",
                     "special_tokens_map.json", "tokenizer_config.json"):
            with (model_dir / name).open("rb") as stream:
                files[name] = hashlib.file_digest(stream, "sha256").hexdigest()
        identity = dict(files=files, packages=versions, model=MODEL, prefix=PREFIX,
                        window=WINDOW, stride=STRIDE, algorithm=ALGORITHM, batch_size=BATCH_SIZE)
        model_hash = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()

        import numpy as np
        from fastembed import TextEmbedding
        from tokenizers import Tokenizer

        model = TextEmbedding(model_name=MODEL, specific_model_path=str(model_dir),
                              local_files_only=True, threads=2, cuda=False,
                              providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_str(model.model.tokenizer.to_str())
        tokenizer.no_truncation()
        tokenizer.no_padding()
        self.model = model
        self.tokenizer = tokenizer
        self.model_hash = model_hash
        self.np = np

    def score(self, request):
        np = self.np
        notes, query = request["notes"], request["query"]
        question = PREFIX + query
        if len(self.tokenizer.encode(question).ids) > 512:
            raise ValueError("query exceeds model input limit")
        passages, owners = [], []
        for index, note in enumerate(notes):
            for chunk in chunks(self.tokenizer, note["body"]):
                if len(passages) == 128:
                    raise ValueError("semantic chunk budget exceeded")
                if len(self.tokenizer.encode(chunk).ids) > 512:
                    raise ValueError("decoded chunk exceeds model input limit")
                passages.append(chunk)
                owners.append(index)
        # Single-passage batches avoid padding shorter passages to their neighbours.
        vectors = np.array(list(self.model.embed([question] + passages, batch_size=BATCH_SIZE)))
        if vectors.shape != (len(passages) + 1, 384) or not np.isfinite(vectors).all():
            raise ValueError("invalid model embeddings")
        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
        if (norms == 0).any():
            raise ValueError("empty model embedding")
        vectors /= norms
        similarities = vectors[1:] @ vectors[0]
        scores = [-1.0] * len(notes)
        for owner, value in zip(owners, similarities):
            scores[owner] = max(scores[owner], float(value))
        result = dict(model_sha256=self.model_hash, algorithm=ALGORITHM, scores=[
            dict(record_id=n["record_id"], version=n["version"], body_sha256=n["body_sha256"],
                 score=round(max(-1.0, min(1.0, scores[i])) * 1_000_000))
            for i, n in enumerate(notes)])
        return result


def serve_stream(model_dir, input_stream, output_stream):
    scorer = None
    while True:
        raw = input_stream.readline(8 * 1024 * 1024 + 2)
        if not raw:
            return
        if len(raw) > 8 * 1024 * 1024 + 1 or not raw.endswith(b"\n"):
            raise ValueError("worker requires a bounded JSON line")
        envelope = json.loads(raw)
        if set(envelope) != {"id", "request"}:
            raise ValueError("worker requires id and request")
        request_id = envelope["id"]
        if not isinstance(request_id, str) or not request_id or len(request_id) > 64:
            raise ValueError("invalid worker request id")
        request = envelope["request"]
        validate_request(request)
        if scorer is None:
            scorer = Scorer(model_dir)
        result = scorer.score(request)
        response = json.dumps(dict(id=request_id, result=result), allow_nan=False).encode()
        if len(response) + 1 > 65536:
            raise ValueError("worker response exceeds limit")
        output_stream.write(response + b"\n")
        output_stream.flush()
        # Drop request and response references before waiting for another line.
        # This is not a promise to erase Python/native allocator memory.
        del raw, envelope, request, result, response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--stream", action="store_true", help="serve bounded id/request JSON lines")
    args = parser.parse_args()
    if args.stream:
        serve_stream(args.model_dir, sys.stdin.buffer, sys.stdout.buffer)
        return
    raw = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("worker request exceeds limit")
    request = json.loads(raw)
    validate_request(request)
    print(json.dumps(Scorer(args.model_dir).score(request), allow_nan=False))


if __name__ == "__main__":
    main()
