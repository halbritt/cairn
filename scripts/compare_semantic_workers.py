"""Compare local CPU workers on public fixtures; keeps all results outside the repository."""

from pathlib import Path
import argparse
import hashlib
import json
import platform
import subprocess
import time
import os
import base64
import selectors
import math
import sys
import stat
import pwd
import shutil

from semantic_acceptance_isolation import (
    IsolationError,
    SemanticIsolation,
    WORKER_ENV,
    stop_process,
)


class ComparisonFailure(RuntimeError):
    def __init__(self, message, *, evidence=None):
        super().__init__(message)
        self.evidence = evidence or {}


def paired_worker_runs(commands, identities, run):
    outcomes = {}
    for role, command, identity in zip(
        ("reference", "candidate"), commands, identities
    ):
        try:
            outcomes[role] = run(command, identity, role)
        except (ComparisonFailure, IsolationError) as error:
            raise ComparisonFailure(
                str(error),
                evidence=dict(
                    completed=outcomes, failed_role=role, failed_attempt=error.evidence
                ),
            ) from error
    return [outcomes["reference"], outcomes["candidate"]]


def fixed_executable(path):
    path = Path(path)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ComparisonFailure(f"requires an absolute fixed executable: {path}")
    return [str(path)]


def worker_result(isolation, argv, request, *, timeout=25, limit=65536, role="tool"):
    raw = json.dumps(request, separators=(",", ":")).encode()
    completed = isolation.run(
        argv, input=raw, timeout=timeout, stdout_limit=limit, role=role, mode="rank"
    )
    outcome = dict(
        argv=list(argv),
        exit_code=completed.returncode,
        request=raw,
        stdout=completed.stdout,
        stderr=completed.stderr,
        invocation=completed.invocation,
    )
    if completed.returncode:
        raise ComparisonFailure(
            f"worker exited {completed.returncode}", evidence=outcome
        )
    try:
        outcome["reply"] = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise ComparisonFailure(
            f"invalid worker JSON: {error}", evidence=outcome
        ) from error
    return outcome


def validate_rank(reply, request, identity):
    if not isinstance(reply, dict) or set(reply) - {
        "model_sha256",
        "algorithm",
        "scores",
    }:
        raise ComparisonFailure("invalid typed rank response fields")
    if (
        reply.get("model_sha256") != identity
        or reply.get("algorithm") != "bge-max-chunk/1"
    ):
        raise ComparisonFailure("rank identity does not match its own manifest")
    scores = reply.get("scores")
    if not isinstance(scores, list) or len(scores) != len(request["notes"]):
        raise ComparisonFailure("missing/extra rank scores")
    for score, note in zip(scores, request["notes"]):
        if (
            not isinstance(score, dict)
            or set(score) - {"record_id", "version", "body_sha256", "score"}
            or type(score.get("version")) is not int
            or any(
                score.get(key) != note[key]
                for key in ("record_id", "version", "body_sha256")
            )
        ):
            raise ComparisonFailure("score order or source metadata mismatch")
        if (
            type(score.get("score")) is not int
            or not -1000000 <= score["score"] <= 1000000
        ):
            raise ComparisonFailure("invalid scaled integer score")


def compare_rank_case(
    reference, candidate, request, *, isolation, reference_identity, candidate_identity
):
    commands = [fixed_executable(reference), fixed_executable(candidate)]
    outcomes = {}
    try:
        for role, command, identity in zip(
            ("reference", "candidate"),
            commands,
            (reference_identity, candidate_identity),
        ):
            outcomes[role] = worker_result(isolation, command, request, role=role)
            validate_rank(outcomes[role]["reply"], request, identity)
        if (
            outcomes["reference"]["reply"]["scores"]
            != outcomes["candidate"]["reply"]["scores"]
        ):
            raise ComparisonFailure("exact integer score mismatch")
    except (ComparisonFailure, IsolationError) as error:
        raise ComparisonFailure(
            str(error), evidence=dict(**outcomes, failed_attempt=error.evidence)
        ) from error
    return dict(**outcomes, exact_scores_equal=True)


class JsonLineProcess:
    def __init__(
        self, isolation, command, *, limit, cache=False, role="tool", mode="stream"
    ):
        env = dict(WORKER_ENV)
        if cache:
            env["CAIRN_SEMANTIC_STREAM_CACHE"] = "1"
        self.command = command
        self.process = isolation.popen(
            command,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            role=role,
            mode=mode,
        )
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            os.set_blocking(pipe.fileno(), False)
        self.limit, self.pending, self.stderr = limit, bytearray(), bytearray()
        self.frames = []
        self.requests = []

    def exchange(self, request, *, timeout=25):
        raw = json.dumps(request, separators=(",", ":")).encode() + b"\n"
        self.requests.append(raw)
        writing = memoryview(raw)
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdin, selectors.EVENT_WRITE, "stdin")
            selector.register(self.process.stdout, selectors.EVENT_READ, "stdout")
            selector.register(self.process.stderr, selectors.EVENT_READ, "stderr")
            while True:
                newline = self.pending.find(b"\n")
                if not writing and newline >= 0:
                    frame = bytes(self.pending[: newline + 1])
                    del self.pending[: newline + 1]
                    self.frames.append(frame)
                    if len(frame) > self.limit:
                        raise ComparisonFailure("worker frame exceeds limit")
                    try:
                        reply = json.loads(frame.decode("utf-8"))
                    except (UnicodeDecodeError, ValueError) as error:
                        raise ComparisonFailure("invalid framed worker JSON") from error
                    if not isinstance(reply, dict) or reply.get("id") != request["id"]:
                        raise ComparisonFailure("response ID mismatch")
                    return reply
                if newline < 0 and len(self.pending) > self.limit:
                    raise ComparisonFailure("worker frame exceeds limit")
                wait = deadline - time.monotonic()
                if wait <= 0:
                    raise ComparisonFailure("worker exchange deadline exceeded")
                for event, _ in selector.select(wait):
                    if event.data == "stdin":
                        try:
                            count = os.write(event.fileobj.fileno(), writing[:65536])
                        except BrokenPipeError as error:
                            raise ComparisonFailure("worker input closed") from error
                        writing = writing[count:]
                        if not writing:
                            selector.unregister(event.fileobj)
                    else:
                        chunk = os.read(event.fileobj.fileno(), 65536)
                        if not chunk:
                            if event.data == "stdout":
                                raise ComparisonFailure(
                                    "worker EOF before complete reply"
                                )
                            selector.unregister(event.fileobj)
                            continue
                        if event.data == "stderr":
                            if len(self.stderr) + len(chunk) > 65536:
                                raise ComparisonFailure("worker stderr exceeds limit")
                            self.stderr.extend(chunk)
                        else:
                            if len(self.pending) + len(chunk) > self.limit:
                                self.pending.extend(
                                    chunk[: self.limit - len(self.pending)]
                                )
                                raise ComparisonFailure("worker frame exceeds limit")
                            self.pending.extend(chunk)

    def close(self):
        self.process._semantic_tree.stop()
        deadline = time.monotonic() + 2
        try:
            with selectors.DefaultSelector() as selector:
                for pipe, buffer, limit in (
                    (self.process.stderr, self.stderr, 65536),
                    (self.process.stdout, self.pending, self.limit),
                ):
                    selector.register(pipe, selectors.EVENT_READ, (buffer, limit))
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ComparisonFailure(
                            "worker pipes did not close after cleanup"
                        )
                    for event, _ in selector.select(remaining):
                        buffer, limit = event.data
                        chunk = os.read(event.fileobj.fileno(), 65536)
                        if not chunk:
                            selector.unregister(event.fileobj)
                        elif len(buffer) < limit:
                            buffer.extend(chunk[: limit - len(buffer)])
        finally:
            stop_process(self.process)

    def evidence(self):
        return dict(
            argv=self.command,
            request_frames=list(self.requests),
            stdout_frames=list(self.frames),
            stdout_pending=bytes(self.pending),
            stderr=bytes(self.stderr),
            exit_code=self.process.returncode,
            invocation=self.process._semantic_invocation,
        )


def _go_rank_result(result):
    """Zero values and field order of core.SemanticRankResult for Go's bound."""
    if not isinstance(result, dict):
        raise ComparisonFailure("worker omitted typed rank result")
    typed = {}
    for key in ("model_sha256", "algorithm"):
        value = result.get(key)
        if value is not None and not isinstance(value, str):
            raise ComparisonFailure("invalid typed rank string")
        typed[key] = "" if value is None else value
    scores = result.get("scores")
    if scores is not None:
        if not isinstance(scores, list):
            raise ComparisonFailure("invalid typed rank scores")
        typed_scores = []
        for score in scores:
            score = {} if score is None else score
            if not isinstance(score, dict):
                raise ComparisonFailure("invalid typed rank score")
            fields = {}
            for key, default in (
                ("record_id", ""),
                ("version", 0),
                ("body_sha256", ""),
                ("score", 0),
            ):
                value = score.get(key)
                value = default if value is None else value
                if type(value) is not type(default) or (
                    isinstance(value, int) and not -(1 << 63) <= value < 1 << 63
                ):
                    raise ComparisonFailure("invalid typed rank score field")
                fields[key] = value
            typed_scores.append(fields)
        scores = typed_scores
    typed["scores"] = scores
    return typed


def stream_response_limits(frame, reply):
    if set(reply) - {"id", "result", "cache"}:
        raise ComparisonFailure("invalid typed stream response fields")
    text = frame.decode("utf-8")
    decoder = json.JSONDecoder()
    position = text.index("{") + 1
    fields = {}
    while True:
        position += len(text[position:]) - len(text[position:].lstrip())
        if text[position] == "}":
            break
        key, position = decoder.raw_decode(text, position)
        position = text.index(":", position) + 1
        position += len(text[position:]) - len(text[position:].lstrip())
        start = position
        _, position = decoder.raw_decode(text, position)
        fields[key] = text[start:position].encode("utf-8")
        position += len(text[position:]) - len(text[position:].lstrip())
        if text[position] == ",":
            position += 1
    cache = fields.get("cache", b"null")
    ordinary = json.dumps(
        dict(id=reply["id"], result=_go_rank_result(reply.get("result"))),
        separators=(",", ":"),
        ensure_ascii=False,
    )
    # encoding/json.Marshal emits UTF-8 and escapes HTML and these separators.
    for literal, escaped in (
        ("<", r"\u003c"),
        (">", r"\u003e"),
        ("&", r"\u0026"),
        ("\u2028", r"\u2028"),
        ("\u2029", r"\u2029"),
    ):
        ordinary = ordinary.replace(literal, escaped)
    if (
        len(ordinary.encode("utf-8")) + 1 > 65536
        or (cache != b"null" and len(cache) > 600 * 1024)
        or (cache == b"null" and len(frame) > 65536)
    ):
        raise ComparisonFailure("stream ordinary/cache response exceeds Go limits")


def stream_rounds(isolation, command, request, identity, restore, role="tool"):
    worker = JsonLineProcess(
        isolation, command, limit=600 * 1024 + 65536, cache=True, role=role
    )
    replies = []
    failure = None
    try:
        for sequence in ("1", "2"):
            reply = worker.exchange(dict(id=sequence, request=request))
            stream_response_limits(worker.frames[-1], reply)
            if not isinstance(reply.get("result"), dict):
                raise ComparisonFailure("worker omitted rank result")
            validate_rank(reply["result"], request, identity)
            replies.append(reply)
        if replies[0]["result"] != replies[1]["result"]:
            raise ComparisonFailure("warm/cold exact score mismatch")
        snapshot = replies[-1].get("cache")
        frames = list(worker.frames)
        if restore and snapshot is None:
            raise ComparisonFailure("required stream cache snapshot missing")
    except (ComparisonFailure, IsolationError, OSError) as error:
        failure = error
    finally:
        worker.close()
    if failure is not None:
        raise ComparisonFailure(str(failure), evidence=worker.evidence()) from failure
    executions = [worker.evidence()]
    if restore:
        fresh = JsonLineProcess(
            isolation, command, limit=600 * 1024 + 65536, cache=True, role=role
        )
        try:
            envelope = dict(id="3", request=request)
            if snapshot is not None:
                envelope["cache"] = snapshot
            reply = fresh.exchange(envelope)
            stream_response_limits(fresh.frames[-1], reply)
            validate_rank(reply.get("result", {}), request, identity)
            if reply["result"] != replies[0]["result"]:
                raise ComparisonFailure("fresh restore changed exact scores")
            replies.append(reply)
            frames.extend(fresh.frames)
        except (ComparisonFailure, IsolationError, OSError) as error:
            failure = error
        finally:
            fresh.close()
        executions.append(fresh.evidence())
        if failure is not None:
            raise ComparisonFailure(
                str(failure), evidence=dict(executions=executions)
            ) from failure
    return dict(
        argv=command,
        replies=replies,
        stdout_frames=frames,
        executions=executions,
        cache_restored=snapshot is not None and restore,
    )


def compare_stream_case(
    reference,
    candidate,
    request,
    *,
    isolation,
    reference_identity,
    candidate_identity,
    restore=True,
):
    commands = [fixed_executable(reference), fixed_executable(candidate)]
    outcomes = paired_worker_runs(
        commands,
        (reference_identity, candidate_identity),
        lambda command, identity, role: stream_rounds(
            isolation, command, request, identity, restore, role=role
        ),
    )
    if any(
        a["result"]["scores"] != b["result"]["scores"]
        for a, b in zip(outcomes[0]["replies"], outcomes[1]["replies"])
    ):
        raise ComparisonFailure(
            "exact stream score mismatch",
            evidence=dict(reference=outcomes[0], candidate=outcomes[1]),
        )
    return dict(
        reference=outcomes[0],
        candidate=outcomes[1],
        successful_replies=sum(len(o["replies"]) for o in outcomes),
        fresh_restore_equal=restore,
    )


def vector_norm(vector):
    if (
        not isinstance(vector, list)
        or len(vector) != 384
        or any(type(v) not in (int, float) for v in vector)
    ):
        raise ComparisonFailure("embedding must have 384 finite components")
    try:
        if not all(math.isfinite(v) for v in vector):
            raise ComparisonFailure("embedding must have 384 finite components")
        norm = math.hypot(*vector)
    except OverflowError as error:
        raise ComparisonFailure(
            "embedding component cannot be represented as a finite number"
        ) from error
    if not norm or not math.isfinite(norm):
        raise ComparisonFailure("embedding must have a finite, nonzero norm")
    return norm


def cosine(left, right):
    left_norm, right_norm = vector_norm(left), vector_norm(right)
    return math.fsum((a / left_norm) * (b / right_norm) for a, b in zip(left, right))


def validate_embedding(reply, case, identity):
    if not isinstance(reply, dict) or set(reply) - {
        "id",
        "identity",
        "vector",
        "passages",
        "query_projection",
    }:
        raise ComparisonFailure("invalid typed embedding response fields")
    expected = dict(
        model_sha256=identity, algorithm="bge-original-passages/1", dimensions=384
    )
    if not isinstance(reply, dict) or reply.get("identity") != expected:
        raise ComparisonFailure("embedding identity differs from its own manifest")
    if case["operation"] == "query":
        vector_norm(reply.get("vector"))
        projection = reply.get("query_projection")
        if projection is not None:
            if not isinstance(projection, dict) or set(projection) != {
                "method",
                "truncated",
                "original_tokens",
                "embedded_tokens",
                "prefix_bytes",
                "prefix_sha256",
            }:
                raise ComparisonFailure("invalid typed query projection fields")
            raw = case["text"].encode()
            try:
                length = projection["prefix_bytes"]
                prefix = raw[:length]
                prefix.decode("utf-8")
                valid = (
                    projection["method"] == "original-prefix/1"
                    and type(length) is int
                    and 0 < length <= len(raw)
                    and type(projection["truncated"]) is bool
                    and type(projection["embedded_tokens"]) is int
                    and type(projection["original_tokens"]) is int
                    and 0 < projection["embedded_tokens"] <= 512
                    and projection["embedded_tokens"]
                    <= projection["original_tokens"]
                    <= 8192
                    and projection["prefix_sha256"]
                    == hashlib.sha256(prefix).hexdigest()
                )
                if projection["truncated"]:
                    valid = (
                        valid
                        and length < len(raw)
                        and projection["original_tokens"] > 512
                    )
                else:
                    valid = (
                        valid
                        and length == len(raw)
                        and projection["original_tokens"]
                        == projection["embedded_tokens"]
                    )
            except (KeyError, TypeError, UnicodeDecodeError) as error:
                raise ComparisonFailure("invalid query projection") from error
            if not valid:
                raise ComparisonFailure("invalid query projection")
    else:
        if reply.get("query_projection") is not None:
            raise ComparisonFailure("projection on a non-query reply")
        passages = reply.get("passages")
        if not isinstance(passages, list) or not 1 <= len(passages) <= 256:
            raise ComparisonFailure("invalid passage count")
        source = case["text"].encode()
        for passage in passages:
            try:
                if set(passage) != {"span", "vector"} or set(passage["span"]) != {
                    "offset",
                    "length",
                }:
                    raise ComparisonFailure("invalid typed passage fields")
                offset, length = passage["span"]["offset"], passage["span"]["length"]
                valid = (
                    type(offset) is int
                    and type(length) is int
                    and offset >= 0
                    and length > 0
                    and offset + length <= len(source)
                )
                source[offset : offset + length].decode("utf-8")
            except (KeyError, TypeError, UnicodeDecodeError) as error:
                raise ComparisonFailure(
                    "invalid original UTF-8 passage span"
                ) from error
            if not valid:
                raise ComparisonFailure("invalid original UTF-8 passage span")
            vector_norm(passage.get("vector"))


def embedding_rounds(isolation, command, cases, identity, role="tool"):
    worker = JsonLineProcess(
        isolation, command, limit=4 * 1024 * 1024, cache=True, role=role, mode="embed"
    )
    replies = []
    failure = None
    try:
        handshake = worker.exchange(dict(id="identity", operation="identity", text=""))
        if handshake.get("identity") != dict(
            model_sha256=identity, algorithm="bge-original-passages/1", dimensions=384
        ):
            raise ComparisonFailure("embedding handshake identity mismatch")
        for case in cases:
            if case.get("expected") != "accepted":
                raise ComparisonFailure(
                    "positive embedding case requires explicit expected=accepted"
                )
            reply = worker.exchange(
                {key: case[key] for key in ("id", "operation", "text")},
                timeout=90 if case["operation"] == "document" else 25,
            )
            validate_embedding(reply, case, identity)
            replies.append(reply)
    except (ComparisonFailure, IsolationError, OSError) as error:
        failure = error
    finally:
        worker.close()
    if failure is not None:
        raise ComparisonFailure(str(failure), evidence=worker.evidence()) from failure
    return dict(argv=command, replies=replies, execution=worker.evidence())


def compare_embedding_cases(
    reference, candidate, cases, *, isolation, reference_identity, candidate_identity
):
    commands = [fixed_executable(reference), fixed_executable(candidate)]
    outcomes = paired_worker_runs(
        commands,
        (reference_identity, candidate_identity),
        lambda command, identity, role: embedding_rounds(
            isolation, command, cases, identity, role=role
        ),
    )
    diagnostics = []
    query_pairs = []
    passage_pairs = []
    for case, left, right in zip(cases, outcomes[0]["replies"], outcomes[1]["replies"]):
        if case["operation"] == "query":
            if left.get("query_projection") != right.get("query_projection"):
                raise ComparisonFailure(
                    "exact query projection mismatch",
                    evidence=dict(reference=outcomes[0], candidate=outcomes[1]),
                )
            pairs = [(left["vector"], right["vector"])]
            query_pairs.extend(pairs)
        else:
            if [p["span"] for p in left["passages"]] != [
                p["span"] for p in right["passages"]
            ]:
                raise ComparisonFailure(
                    "exact passage span/order mismatch",
                    evidence=dict(reference=outcomes[0], candidate=outcomes[1]),
                )
            pairs = [
                (a["vector"], b["vector"])
                for a, b in zip(left["passages"], right["passages"])
            ]
            passage_pairs.extend(pairs)
        for index, (a, b) in enumerate(pairs):
            angle = 1 - cosine(a, b)
            diagnostics.append(
                dict(
                    case=case["id"],
                    index=index,
                    one_minus_cosine=angle,
                    max_component_difference=max(abs(x - y) for x, y in zip(a, b)),
                    reference_norm=vector_norm(a),
                    candidate_norm=vector_norm(b),
                )
            )
            if angle > 1e-6:
                raise ComparisonFailure(
                    "vector cosine tolerance exceeded",
                    evidence=dict(
                        reference=outcomes[0],
                        candidate=outcomes[1],
                        vectors=diagnostics,
                    ),
                )
    pairwise = [
        abs(cosine(qa, pa) - cosine(qb, pb))
        for qa, qb in query_pairs
        for pa, pb in passage_pairs
    ]
    if any(difference > 1e-6 for difference in pairwise):
        raise ComparisonFailure(
            "query/passage pairwise cosine tolerance exceeded",
            evidence=dict(
                reference=outcomes[0],
                candidate=outcomes[1],
                vectors=diagnostics,
                pairwise=pairwise,
            ),
        )
    return dict(
        reference=outcomes[0],
        candidate=outcomes[1],
        vectors=diagnostics,
        pairwise_count=len(pairwise),
        max_pairwise_difference=max(pairwise, default=0),
    )


def file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def new_output_root(path, project):
    root = path.absolute()
    if (
        root.is_symlink()
        or any(p.is_symlink() for p in root.parents)
        or root.exists()
        or root == project
        or project in root.parents
    ):
        raise ComparisonFailure(
            "use a new artifact directory outside the repository, without symlinks"
        )
    root.mkdir(parents=True, mode=0o700)
    return root


def checked_worker_manifest(path, phase):
    manifest = json.loads(Path(path).read_text())
    if manifest.get("schema") != "cairn.semantic-comparison-worker/1":
        raise ComparisonFailure("unsupported worker manifest")
    expected_kind = "protocol-fixture" if phase == "mechanics" else "model"
    if manifest.get("kind") != expected_kind:
        raise ComparisonFailure("worker kind does not match selected phase")
    for mode in ("rank", "stream", "embed"):
        worker = manifest["workers"][mode]
        command = fixed_executable(worker["path"])
        if file_digest(command[0]) != worker["sha256"]:
            raise ComparisonFailure(f"{mode} executable digest mismatch")
        if phase == "mechanics" and Path(command[0]).read_bytes()[:4] != b"\x7fELF":
            raise ComparisonFailure(
                "native mechanics requires a real compiled ELF fixture"
            )
        if (
            not isinstance(worker.get("model_sha256"), str)
            or len(worker["model_sha256"]) != 64
        ):
            raise ComparisonFailure("missing own-manifest derivation identity")
        if phase != "mechanics":
            runtime = worker.get("runtime_libraries")
            if not isinstance(runtime, list) or not runtime:
                raise ComparisonFailure(
                    "each model mode requires pinned runtime libraries"
                )
            names = [Path(library["path"]).name for library in runtime]
            if not any(
                "onnxruntime_pybind11_state" in name
                or name.startswith("libonnxruntime.so")
                for name in names
            ):
                raise ComparisonFailure("the actual ORT image must be pinned per mode")
            if any("onnxruntime_pybind11_state" in name for name in names) and not (
                any("_multiarray_umath" in name for name in names)
                and any("openblas" in name.lower() for name in names)
            ):
                raise ComparisonFailure(
                    "Python runtime pins must include NumPy and OpenBLAS"
                )
            for library in runtime:
                if (
                    not Path(library["path"]).is_absolute()
                    or file_digest(library["path"]) != library["sha256"]
                ):
                    raise ComparisonFailure(
                        "runtime library missing or digest mismatch"
                    )
    assets = manifest.get("assets")
    if not isinstance(assets, list) or (phase != "mechanics" and not assets):
        raise ComparisonFailure("model/runtime asset inventory required")
    for asset in assets:
        if (
            not Path(asset["path"]).is_absolute()
            or file_digest(asset["path"]) != asset["sha256"]
        ):
            raise ComparisonFailure("missing or mismatched model/runtime asset")
    manifest["_manifest_sha256"] = file_digest(path)
    return manifest


def checked_json_artifact(path, pin, label):
    if (
        path is None
        or not isinstance(pin, str)
        or len(pin) != 64
        or any(c not in "0123456789abcdef" for c in pin)
    ):
        raise ComparisonFailure(f"caller-pinned {label} path and SHA-256 required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ComparisonFailure(f"{label} must be a regular file")
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != pin:
        raise ComparisonFailure(f"{label} digest/size mismatch")
    return json.loads(raw)


def require_review_receipt(args):
    receipt = checked_json_artifact(
        args.review_receipt, args.review_receipt_sha256, "comparison review receipt"
    )
    module = file_digest(Path(__file__).with_name("semantic_acceptance_isolation.py"))
    binding = dict(
        status="accepted",
        source_commit=receipt["source_commit"],
        module_sha256=module,
        catalog_sha256=args.reference_catalog_sha256,
        raw_qualification_sha256=args.raw_qualification_sha256,
    )
    if len(binding["source_commit"]) != 40 or any(
        c not in "0123456789abcdef" for c in binding["source_commit"]
    ):
        raise ComparisonFailure("review source_commit must be an immutable commit SHA")
    if receipt.get("schema") != "cairn.semantic-comparison-review/1" or any(
        receipt.get(k) != v for k, v in binding.items()
    ):
        raise ComparisonFailure(
            "review receipt does not bind this module/catalog/raw proof"
        )
    ref = receipt["review_record"]
    cairn = shutil.which("cairn")
    if not cairn:
        raise ComparisonFailure("Cairn exact review source verification unavailable")
    token = (
        Path(pwd.getpwuid(os.getuid()).pw_dir) / ".local/share/cairn/hosted-agent.token"
    )
    result = subprocess.run(
        [cairn, "agent", "--token-file", str(token), "history"],
        input=json.dumps(
            dict(record_id=ref["record_id"], version=ref["version"])
        ).encode(),
        capture_output=True,
        timeout=10,
    )
    if result.returncode:
        raise ComparisonFailure("exact review record unavailable")
    envelope = json.loads(result.stdout)
    current = envelope.get("data", {})
    if (
        current.get("current_version") != ref["version"]
        or current.get("current_lifecycle") != "active"
    ):
        raise ComparisonFailure("selected review source is no longer current/active")
    versions = (
        envelope.get("data", {}).get("versions", []) if envelope.get("ok") else []
    )
    selected = [v for v in versions if v.get("version") == ref["version"]]
    if (
        len(selected) != 1
        or selected[0].get("observed_writer")
        != "agent/e1061fdd-091a-4360-80b5-d7c77bd4ee07"
    ):
        raise ComparisonFailure(
            "review record is not the selected independent reviewer source"
        )
    record = selected[0]
    if (
        not record.get("payload_available")
        or hashlib.sha256(record["body"].encode()).hexdigest() != ref["body_sha256"]
    ):
        raise ComparisonFailure("review source body digest mismatch")
    verdict = json.loads(record["body"])
    if verdict.get("schema") != "cairn.semantic-comparison-verdict/1" or any(
        verdict.get(k) != v for k, v in binding.items()
    ):
        raise ComparisonFailure(
            "actual reviewer verdict did not accept these exact bindings"
        )
    source = subprocess.check_output(
        [
            "git",
            "show",
            binding["source_commit"] + ":scripts/semantic_acceptance_isolation.py",
        ],
        cwd=Path(__file__).resolve().parents[1],
    )
    if hashlib.sha256(source).hexdigest() != module:
        raise ComparisonFailure(
            "accepted review source commit has different module bytes"
        )
    return receipt


def require_comparison_runtimes(isolation, comparison, mode, manifests):
    evidence = {}
    for role, manifest in zip(("reference", "candidate"), manifests):
        outcome = comparison[role]
        executions = (
            [outcome]
            if mode == "rank"
            else outcome["executions"]
            if mode == "stream"
            else [outcome["execution"]]
        )
        evidence[role] = [
            isolation.invocation_runtime(
                item["invocation"], manifest["workers"][mode]["runtime_libraries"]
            )
            for item in executions
        ]
    comparison["runtime_evidence"] = evidence


def require_reference_cells(catalog, inventory):
    if catalog.get("schema") != "cairn.semantic-reference-catalog/1":
        raise ComparisonFailure("unsupported reference catalog")
    cells = catalog.get("cells", [])
    identifiers = [cell["id"] for cell in cells]
    if len(identifiers) != len(set(identifiers)):
        raise ComparisonFailure("duplicate reference inventory cell")
    by_id = {cell["id"]: cell for cell in cells}
    missing = [
        name for name in inventory["required_reference_cells"] if name not in by_id
    ]
    unqualified = [
        name
        for name in inventory["required_reference_cells"]
        if name in by_id and by_id[name].get("status") != "qualified"
    ]
    if missing or unqualified:
        raise ComparisonFailure(
            f"incomplete reference cells: missing={missing}; unqualified={unqualified}"
        )


RAW_BASELINE = dict(
    ids="1762a94e5b785ffcfa300f404252994026b18a71f763086f297f89a9809394a9",
    lengths="a81f2c9f819d10049549d14dcdb38983febf0a41cbfe314421e4f70bba8ded33",
    cls="1f7ba8c969c2053385ce6e89528d2decea78b627e9fb6ef40cfc322fd760b623",
)


def require_raw_qualification(path, manifests, *, sha256=None):
    if path is None:
        raise ComparisonFailure("reviewed raw-output invariance proof is required")
    proof = checked_json_artifact(path, sha256, "raw qualification")
    if (
        proof.get("schema") != "cairn.semantic-raw-qualification/1"
        or proof.get("state") != "qualified"
        or not proof.get("review_record")
        or proof.get("wrapper_profile") != "default"
        or proof.get("wrapper_sha256")
        != file_digest(Path(__file__).with_name("semantic_acceptance_isolation.py"))
    ):
        raise ComparisonFailure(
            "raw-output qualification is missing, unreviewed or for another wrapper"
        )
    for role, manifest in zip(("baseline", "candidate"), manifests):
        bound = proof["roles"][role]
        if bound.get("manifest_sha256") != manifest["_manifest_sha256"] or bound[
            "workers"
        ] != {mode: worker["sha256"] for mode, worker in manifest["workers"].items()}:
            raise ComparisonFailure(
                "raw-output proof is not bound to the selected executables"
            )
        for name, expected in RAW_BASELINE.items():
            array = bound["arrays"][name]
            if array["sha256"] != expected or file_digest(array["path"]) != expected:
                raise ComparisonFailure(
                    f"{role} raw {name} differs from the frozen baseline"
                )


def verify_go_events(raw, expected):
    events = [json.loads(line) for line in raw.splitlines() if line]
    if any(event.get("Action") in ("skip", "fail") for event in events):
        raise ComparisonFailure("required Go test skipped or failed")
    ran = [
        event["Test"]
        for event in events
        if event.get("Action") == "run" and "/" not in event.get("Test", "/")
    ]
    passed = [
        event["Test"]
        for event in events
        if event.get("Action") == "pass" and "/" not in event.get("Test", "/")
    ]
    if (
        not expected
        or sorted(ran) != sorted(expected)
        or sorted(passed) != sorted(expected)
    ):
        raise ComparisonFailure("missing/zero/duplicate named Go test inventory")
    return dict(tests_ran=len(ran), tests_passed=len(passed), skipped=0)


def verify_python_events(document, expected):
    if (
        not expected
        or document.get("skipped")
        or not document.get("successful")
        or document.get("tests_ran") != len(expected)
        or sorted(document.get("ids", [])) != sorted(expected)
    ):
        raise ComparisonFailure(
            "required Python tests skipped, failed, missing or zero-ran"
        )
    return dict(tests_ran=len(expected), skipped=0)


_PYTHON_TEST_DRIVER = """
import json,sys,unittest
sys.path.insert(0,sys.argv[1])
names=json.loads(sys.argv[2]); ids=[]; skipped=[]
class ObservedResult(unittest.TextTestResult):
 def startTest(self,test):
  ids.append(test.id());super().startTest(test)
 def addSkip(self,test,reason):
  skipped.append(test.id());super().addSkip(test,reason)
suite=unittest.TestLoader().loadTestsFromNames(names)
result=unittest.TextTestRunner(resultclass=ObservedResult,verbosity=2).run(suite)
print(json.dumps(dict(schema='cairn.semantic-test-events/1',ids=ids,skipped=skipped,
 tests_ran=result.testsRun,successful=result.wasSuccessful())))
"""


def run_required_tests(args, inventory, isolation, reference, candidate, root):
    expected = [
        group + "." + method
        for group, methods in inventory["python_tests"].items()
        for method in methods
    ]
    tokenizer = [
        asset["path"]
        for asset in reference["assets"]
        if Path(asset["path"]).name == "tokenizer.json"
    ]
    if len(tokenizer) != 1:
        raise ComparisonFailure("one prepared tokenizer asset is required")
    env = dict(WORKER_ENV, CAIRN_EMBEDDING_TOKENIZER=tokenizer[0])
    tested = isolation.run(
        [
            str(args.python),
            "-I",
            "-B",
            "-c",
            _PYTHON_TEST_DRIVER,
            str(Path(__file__).parent),
            json.dumps(list(inventory["python_tests"])),
        ],
        env=env,
        timeout=60,
        stdout_limit=1048576,
    )
    (root / "python-tests.stdout").write_bytes(tested.stdout)
    (root / "python-tests.stderr").write_bytes(tested.stderr)
    if tested.returncode:
        raise ComparisonFailure("prepared Python test command failed")
    events = [
        json.loads(line)
        for line in tested.stdout.splitlines()
        if line.startswith(b'{"schema": "cairn.semantic-test-events/1"')
    ]
    if len(events) != 1:
        raise ComparisonFailure("missing Python test observation")
    verified_python = verify_python_events(events[0], expected)
    modes = {
        "rank": "CAIRN_SEMANTIC_WORKER",
        "stream": "CAIRN_SEMANTIC_STREAM_WORKER",
        "embed": "CAIRN_EMBEDDING_WORKER",
    }
    env.update(
        CAIRN_SEMANTIC_ACCEPTANCE_REQUIRED="1",
        CAIRN_SEMANTIC_ACCEPTANCE_HOST_NET=os.readlink("/proc/self/ns/net"),
        CAIRN_SEMANTIC_ACCEPTANCE_HOME_INODE=str(isolation.proof["home_probe_inode"]),
        CAIRN_SEMANTIC_ACCEPTANCE_INPUTS=str(root / "go-inputs.json"),
    )
    verified_go = {}
    for role, manifest in (("reference", reference), ("candidate", candidate)):
        env.update(
            {
                selector: manifest["workers"][mode]["path"]
                for mode, selector in modes.items()
            }
        )
        selected = inventory["go_tests"] + (
            inventory["go_process_tests"] if role == "reference" else []
        )
        tested = isolation.run(
            [
                str(args.go_test2json),
                "-t",
                "-p",
                "semantic",
                str(args.go_test_binary),
                "-test.v",
                "-test.run",
                "^(" + "|".join(selected) + ")$",
            ],
            env=env,
            timeout=240,
            stdout_limit=4194304,
        )
        (root / ("go-tests-" + role + ".jsonl")).write_bytes(tested.stdout)
        (root / ("go-tests-" + role + ".stderr")).write_bytes(tested.stderr)
        if tested.returncode:
            raise ComparisonFailure(
                "required Go supervisor test command failed",
                evidence=dict(
                    role=role,
                    stdout=tested.stdout,
                    stderr=tested.stderr,
                    exit_code=tested.returncode,
                ),
            )
        verified_go[role] = verify_go_events(tested.stdout, selected)
    return dict(python=verified_python, go=verified_go)


def persist_bytes(document, root):
    counter = 0

    def persist(value):
        nonlocal counter
        if isinstance(value, bytes):
            counter += 1
            name = f"raw-{counter:05d}.bin"
            (root / name).write_bytes(value)
            return dict(
                artifact=name,
                bytes=len(value),
                sha256=hashlib.sha256(value).hexdigest(),
                prefix_base64=base64.b64encode(value[:96]).decode(),
            )
        if isinstance(value, dict):
            return {key: persist(child) for key, child in value.items()}
        if isinstance(value, list):
            return [persist(child) for child in value]
        return value

    return persist(document)


def acceptance_main(args):
    project = Path(__file__).resolve().parents[1]
    root = new_output_root(args.output, project)
    report = dict(
        phase=args.phase,
        model_acceptance=False,
        passed_modes=[],
        failures=[],
        comparisons={},
    )
    try:
        inventory = json.loads(args.inventory.read_text())
        committed_inventory = (
            project / "semantic/testdata/executable-harness/reference-inventory.json"
        )
        if (
            not isinstance(inventory, dict)
            or inventory.get("schema") != "cairn.semantic-acceptance-inventory/1"
            or file_digest(args.inventory) != file_digest(committed_inventory)
        ):
            raise ComparisonFailure("the complete committed test inventory is required")
        reference = checked_worker_manifest(args.baseline_manifest, args.phase)
        candidate = checked_worker_manifest(args.candidate_manifest, args.phase)
        if args.phase != "mechanics":
            if args.reference_catalog is None:
                raise ComparisonFailure("qualified reference catalog is required")
            require_review_receipt(args)
            require_reference_cells(
                checked_json_artifact(
                    args.reference_catalog,
                    args.reference_catalog_sha256,
                    "reference catalog",
                ),
                inventory,
            )
            require_raw_qualification(
                args.raw_qualification,
                (reference, candidate),
                sha256=args.raw_qualification_sha256,
            )
            if any(
                value is None
                for value in (args.python, args.go_test_binary, args.go_test2json)
            ):
                raise ComparisonFailure(
                    "prepared Python and Go test executables are required"
                )
            for path in (args.python, args.go_test_binary, args.go_test2json):
                fixed_executable(path)
            from semantic_fixture_cases import ranking_cases, embedding_cases

            rank_cases = ranking_cases()
            embed_cases = embedding_cases()
            if (
                [case["id"] for case in rank_cases] != inventory["rank_ids"]
                or [case["id"] for case in embed_cases]
                != inventory["query_ids"] + inventory["document_ids"]
                or any(case.get("expected") != "accepted" for case in embed_cases)
            ):
                raise ComparisonFailure(
                    "fixture IDs/order/expected schema differ from reviewed inventory"
                )
            for mode, cases in (("rank", rank_cases), ("embedding", embed_cases)):
                canonical = json.dumps(
                    cases, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
                if (
                    hashlib.sha256(canonical).hexdigest()
                    != inventory["fixture_sha256"][mode]
                ):
                    raise ComparisonFailure(
                        "fixture content differs from the reviewed input digest"
                    )
        else:
            rank_cases = [inventory["protocol_fixture"]["rank"]]
            embed_cases = inventory["protocol_fixture"]["embedding"]
        mounts = {
            Path(worker["path"]).parent
            for manifest in (reference, candidate)
            for worker in manifest["workers"].values()
        }
        mounts.update(
            Path(asset["path"])
            for manifest in (reference, candidate)
            for asset in manifest["assets"]
        )
        mounts.update(
            Path(path)
            for manifest in (reference, candidate)
            for path in manifest.get("read_only_paths", [])
        )
        mounts.update(
            Path(pin["path"])
            for manifest in (reference, candidate)
            for worker in manifest["workers"].values()
            for pin in worker.get("runtime_libraries", [])
        )
        if args.phase != "mechanics":
            (root / "go-inputs.json").write_text(
                json.dumps(dict(rank=rank_cases, embedding=embed_cases)) + "\n"
            )
            mounts.update(
                (
                    Path(__file__).parent,
                    args.python.parent.parent,
                    args.python.resolve().parent.parent,
                    args.go_test_binary.parent,
                    args.go_test2json.parent,
                    root / "go-inputs.json",
                )
            )
        with SemanticIsolation(
            read_only_paths=sorted(mounts), output_dir=root / "isolation"
        ) as isolation:
            for mode in ("rank", "stream", "embed"):
                a, b = reference["workers"][mode], candidate["workers"][mode]
                try:
                    if mode in ("rank", "stream"):
                        compared = []
                        report["comparisons"][mode] = compared
                        for index, case in enumerate(rank_cases):
                            kwargs = dict(
                                isolation=isolation,
                                reference_identity=a["model_sha256"],
                                candidate_identity=b["model_sha256"],
                            )
                            if mode == "stream":
                                kwargs["restore"] = index == 0
                            compare = (
                                compare_rank_case
                                if mode == "rank"
                                else compare_stream_case
                            )
                            compared.append(
                                dict(
                                    case_id=case["id"],
                                    **compare(
                                        a["path"],
                                        b["path"],
                                        {k: case[k] for k in ("query", "notes")},
                                        **kwargs,
                                    ),
                                )
                            )
                            if args.phase != "mechanics":
                                require_comparison_runtimes(
                                    isolation,
                                    compared[-1],
                                    mode,
                                    (reference, candidate),
                                )
                    else:
                        compared = compare_embedding_cases(
                            a["path"],
                            b["path"],
                            embed_cases,
                            isolation=isolation,
                            reference_identity=a["model_sha256"],
                            candidate_identity=b["model_sha256"],
                        )
                    report["comparisons"][mode] = compared
                    if mode == "embed" and args.phase != "mechanics":
                        require_comparison_runtimes(
                            isolation, compared, mode, (reference, candidate)
                        )
                    report["passed_modes"].append(mode)
                except (
                    ComparisonFailure,
                    IsolationError,
                    OSError,
                    subprocess.TimeoutExpired,
                ) as error:
                    report["failures"].append(
                        dict(
                            mode=mode,
                            error=str(error),
                            error_type=type(error).__name__,
                            failure_code=getattr(error, "code", "comparison"),
                            evidence=getattr(error, "evidence", {}),
                        )
                    )
                    if args.phase != "mechanics":
                        break
            if args.phase != "mechanics":
                if report["failures"]:
                    report["required_tests"] = dict(
                        status="not-run",
                        reason="comparison failed; no automatic further model execution",
                    )
                else:
                    report["required_tests"] = run_required_tests(
                        args, inventory, isolation, reference, candidate, root
                    )
    except (
        ComparisonFailure,
        IsolationError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        ImportError,
        AttributeError,
    ) as error:
        report["failures"].append(
            dict(
                mode="preflight",
                error=str(error),
                error_type=type(error).__name__,
                failure_code=getattr(error, "code", "comparison"),
                evidence=getattr(error, "evidence", {}),
            )
        )
    report["status"] = "failed" if report["failures"] else "passed"
    report["model_comparison_passed"] = (
        args.phase != "mechanics" and not report["failures"]
    )
    (root / "report.json").write_text(
        json.dumps(persist_bytes(report, root), indent=2, allow_nan=False) + "\n"
    )
    print(
        json.dumps(
            dict(
                status=report["status"],
                phase=args.phase,
                model_acceptance=report["model_acceptance"],
                report=str(root / "report.json"),
            )
        )
    )
    return 1 if report["failures"] else 0


def measure_legacy_worker(isolation, command, request, label):
    if Path(label).name != label:
        raise ComparisonFailure("invalid resource measurement label")
    measure = isolation.output_dir / (label + "-resources.json")
    invocation = [
        "/usr/bin/time",
        "-f",
        '{"user_seconds":%U,"system_seconds":%S,"max_rss_kib":%M}',
        "-o",
        str(measure),
        "/usr/bin/timeout",
        "--kill-after=1",
        "20",
        *command,
    ]
    started = time.monotonic()
    completed = isolation.run(
        invocation,
        input=request,
        timeout=25,
        stdout_limit=65536,
        role=label,
        mode="legacy",
    )
    stats = json.loads(measure.read_text().splitlines()[-1])
    stats.update(
        wall_seconds=time.monotonic() - started,
        exit_code=completed.returncode,
        measurement=str(measure),
    )
    return completed, stats


def legacy_main(args):
    """Preserved exploratory Python-script mode, now behind the same test boundary."""
    root = args.output.absolute()
    project = Path(__file__).resolve().parents[1]
    if root.exists() or root == project or project in root.parents:
        raise ComparisonFailure("use a new artifact directory outside the repository")
    python = args.python.absolute()
    fixed_executable(python)
    workers = dict(
        baseline=args.baseline.resolve(strict=True),
        candidate=args.candidate.resolve(strict=True),
    )
    model = args.model_dir.resolve(strict=True)
    commands = {
        role: [str(python), str(worker), "--model-dir", str(model)]
        for role, worker in workers.items()
    }
    if args.raw_qualification is None:
        raise ComparisonFailure(
            "legacy model runs require reviewed wrapper raw-output qualification"
        )
    require_review_receipt(args)
    proof = checked_json_artifact(
        args.raw_qualification, args.raw_qualification_sha256, "raw qualification"
    )
    if (
        proof.get("schema") != "cairn.semantic-raw-qualification/1"
        or proof.get("state") != "qualified"
        or not proof.get("review_record")
        or proof.get("wrapper_profile") != "default"
        or proof.get("wrapper_sha256")
        != file_digest(Path(__file__).with_name("semantic_acceptance_isolation.py"))
    ):
        raise ComparisonFailure(
            "legacy raw qualification is unreviewed or for another wrapper"
        )
    for role, command in commands.items():
        bound = proof["legacy_roles"][role]
        if (
            bound["argv"] != command
            or bound["script_sha256"] != file_digest(workers[role])
            or bound["python_sha256"] != file_digest(python)
        ):
            raise ComparisonFailure(
                "legacy qualification does not bind the selected command"
            )
        if not bound.get("model_assets"):
            raise ComparisonFailure(
                "legacy qualification requires a model asset inventory"
            )
        for asset in bound["model_assets"]:
            path = Path(asset["path"])
            if (
                not path.is_absolute()
                or model not in path.parents
                or file_digest(path) != asset["sha256"]
            ):
                raise ComparisonFailure("legacy model asset missing or changed")
        for name, expected in RAW_BASELINE.items():
            array = bound["arrays"][name]
            if array["sha256"] != expected or file_digest(array["path"]) != expected:
                raise ComparisonFailure(
                    "legacy raw arrays differ from the frozen baseline"
                )
    from semantic_fixture_cases import ranking_cases

    cases = ranking_cases()
    if len(cases) != 19:
        raise ComparisonFailure("reviewed19-case producer inventory is required")
    cases += [dict(cases[0], id="repeat-storage-" + str(i)) for i in range(2)]
    root = new_output_root(root, project)
    plan = dict(
        source_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=project, text=True
        ).strip(),
        commands=commands,
        phase="legacy-exploratory",
        model_acceptance=False,
        deadline_seconds=20,
        order="alternate baseline/candidate first across cases",
        boundary="offline namespace, disposable OS home, one-shot Python script through output",
        metrics=[
            "wall_seconds including namespace launch",
            "CPU_seconds",
            "max_RSS_KiB",
            "exact integer scores",
        ],
        script_sha256={role: file_digest(worker) for role, worker in workers.items()},
        platform=platform.platform(),
        raw_qualification_sha256=file_digest(args.raw_qualification),
    )
    (root / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    rows = []
    mounts = {
        python.parent.parent,
        python.resolve().parent.parent,
        model,
        *[w.parent for w in workers.values()],
    }
    with SemanticIsolation(
        read_only_paths=sorted(mounts), output_dir=root / "isolation"
    ) as isolation:
        for index, case in enumerate(cases):
            row = dict(id=case["id"], conditions={})
            raw = json.dumps({k: case[k] for k in ("query", "notes")}).encode()
            (root / (case["id"] + "-request.json")).write_bytes(raw)
            order = (
                ("baseline", "candidate")
                if index % 2 == 0
                else ("candidate", "baseline")
            )
            for role in order:
                completed, stats = measure_legacy_worker(
                    isolation, commands[role], raw, case["id"] + "-" + role
                )
                isolation.invocation_runtime(
                    completed.invocation,
                    proof["legacy_roles"][role]["runtime_libraries"],
                )
                (root / (case["id"] + "-" + role + ".stdout")).write_bytes(
                    completed.stdout
                )
                (root / (case["id"] + "-" + role + ".stderr")).write_bytes(
                    completed.stderr
                )
                if completed.returncode == 0:
                    stats["result"] = json.loads(completed.stdout)
                row["conditions"][role] = stats
            a, b = row["conditions"]["baseline"], row["conditions"]["candidate"]
            row["exact_scores_equal"] = (
                a["result"]["scores"] == b["result"]["scores"]
                if a["exit_code"] == b["exit_code"] == 0
                else None
            )
            if row["exact_scores_equal"] is not None:
                row["speedup"] = a["wall_seconds"] / b["wall_seconds"]
            rows.append(row)
            (root / "report.json").write_text(
                json.dumps(dict(plan=plan, rows=rows), indent=2) + "\n"
            )
            print(
                json.dumps(dict(id=case["id"], exact=row["exact_scores_equal"])),
                flush=True,
            )
        isolation.runtime_evidence(require_ort=True)
    return 0 if all(row["exact_scores_equal"] for row in rows) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--python", type=Path)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--phase", choices=("mechanics", "reference", "rank", "embed"))
    parser.add_argument(
        "--inventory",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "semantic/testdata/executable-harness/reference-inventory.json",
    )
    parser.add_argument("--baseline-manifest", type=Path)
    parser.add_argument("--candidate-manifest", type=Path)
    parser.add_argument("--reference-catalog", type=Path)
    parser.add_argument("--raw-qualification", type=Path)
    parser.add_argument("--reference-catalog-sha256")
    parser.add_argument("--raw-qualification-sha256")
    parser.add_argument("--review-receipt", type=Path)
    parser.add_argument("--review-receipt-sha256")
    parser.add_argument("--go-test-binary", type=Path)
    parser.add_argument("--go-test2json", type=Path)
    args = parser.parse_args()
    if args.phase:
        if args.baseline_manifest is None or args.candidate_manifest is None:
            parser.error("fixed worker manifests are required for acceptance")
        if any((args.baseline, args.candidate, args.model_dir)):
            parser.error(
                "Python-script legacy mode and fixed-executable acceptance are separate"
            )
        return acceptance_main(args)
    if any(
        value is None
        for value in (args.python, args.model_dir, args.baseline, args.candidate)
    ):
        parser.error("legacy mode requires --python --model-dir --baseline --candidate")
    try:
        return legacy_main(args)
    except (
        ComparisonFailure,
        IsolationError,
        OSError,
        ValueError,
        KeyError,
        ImportError,
        AttributeError,
    ) as error:
        print("semantic comparison refused: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
