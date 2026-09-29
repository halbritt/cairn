#!/usr/bin/env python3
"""Versioned local embeddings. The host owns persistence, eligibility and retries."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import semantic_rank
from semantic_rank import PREFIX, Scorer

ALGORITHM = "bge-original-passages/1"
WINDOW, STRIDE = 128, 96


def source_passages(tokenizer, body):
    """Tokenizer offsets address Python characters; expose original UTF-8 bytes."""
    encoding = tokenizer.encode(body, add_special_tokens=False)
    offsets = encoding.offsets
    if not offsets:
        raise ValueError("document has no model tokens")
    # Adapt the stride only for unusually token-dense maximum-size notes. Every
    # token remains covered within 256 passages; no silent tail truncation.
    stride = max(STRIDE, (len(offsets) + 255) // 256)
    window = max(WINDOW, stride + 32)
    for start in range(0, len(offsets), stride):
        end = min(start + window, len(offsets))
        first, last = offsets[start][0], offsets[end - 1][1]
        if last <= first:
            raise ValueError("invalid tokenizer offsets")
        text = body[first:last]
        yield text, dict(offset=len(body[:first].encode()), length=len(text.encode()))
        # Keep the final smaller window: unrelated prefix text must not drown
        # a short final decision merely because an earlier window covers it.


class Embeddings:
    def __init__(self, model_dir):
        self.scorer = Scorer(model_dir)
        # Include this derivation's source as well as model, tokenizer, packages
        # and encoding constants in the index identity.
        identity = dict(base=self.scorer.model_hash, algorithm=ALGORITHM,
                        source=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        scorer_source=hashlib.sha256(Path(semantic_rank.__file__).read_bytes()).hexdigest())
        self.identity = dict(model_sha256=hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest(),
                             algorithm=ALGORITHM, dimensions=384)

    def vectors(self, texts):
        np = self.scorer.np
        vectors = list(self.scorer.model.embed(texts, batch_size=1))
        if len(vectors) != len(texts):
            raise ValueError("incomplete embedding batch")
        for v in vectors:
            if v.shape != (384,) or not np.isfinite(v).all() or not np.any(v):
                raise ValueError("invalid embedding")
        return [v.tolist() for v in vectors]

    def respond(self, request):
        if not isinstance(request, dict) or set(request) != {"id", "operation", "text"}:
            raise ValueError("invalid embedding request")
        request_id, operation, text = request["id"], request["operation"], request["text"]
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128 or not isinstance(text, str):
            raise ValueError("invalid request identity or text")
        response = dict(id=request_id, identity=self.identity)
        if operation == "identity":
            if text:
                raise ValueError("identity request must have empty text")
        elif operation == "query":
            question = PREFIX + text
            if not text.strip() or len(text.encode()) > 4096 or len(self.scorer.tokenizer.encode(question).ids) > 512:
                raise ValueError("query exceeds input bounds")
            response["vector"] = self.vectors([question])[0]
        elif operation == "document":
            if not text.strip() or len(text.encode()) > 65536:
                raise ValueError("document exceeds input bounds")
            passages = list(source_passages(self.scorer.tokenizer, text))
            if not 1 <= len(passages) <= 256:
                raise ValueError("document exceeds passage bounds")
            for passage, _ in passages:
                if len(self.scorer.tokenizer.encode(passage).ids) > 512:
                    raise ValueError("passage exceeds model input bounds")
            vectors = self.vectors([p for p, _ in passages])
            response["passages"] = [dict(span=span, vector=v) for (_, span), v in zip(passages, vectors)]
        else:
            raise ValueError("unknown embedding operation")
        return response


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", type=Path, required=True)
    args = parser.parse_args()
    embeddings = Embeddings(args.model_dir)
    while raw := sys.stdin.buffer.readline(512 * 1024 + 1):
        if len(raw) > 512 * 1024 or not raw.endswith(b"\n"):
            raise ValueError("embedding request frame exceeds bounds")
        response = embeddings.respond(json.loads(raw))
        encoded = json.dumps(response, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode()) + 1 > 4 * 1024 * 1024:
            raise ValueError("embedding response frame exceeds bounds")
        print(encoded, flush=True)


if __name__ == "__main__":
    main()
