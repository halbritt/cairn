#!/usr/bin/env python3
"""Reproduce the independent advice patch in a clean pinned scratch checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

BASE = "6ff1e1ca3b30fc8b914720681fcf5fe62e95f856"


def run(arguments, cwd, **kwargs):
    return subprocess.run(arguments, cwd=cwd, check=True, **kwargs)


def source_digest(source):
    names = run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                source, capture_output=True).stdout.split(b"\0")
    result = hashlib.sha256()
    result.update(run(["git", "rev-parse", "HEAD"], source, capture_output=True).stdout)
    for name in sorted(set(names) - {b""}):
        path = source / os.fsdecode(name)
        result.update(name + b"\0")
        result.update(str(path.lstat().st_mode & 0o7777).encode() + b"\0")
        if path.is_symlink():
            result.update(os.fsencode(os.readlink(path)) + b"\0")
        with path.open("rb") as content:
            result.update(hashlib.file_digest(content, "sha256").digest())
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    bundle = Path(__file__).resolve().parent
    patch = bundle / "advice.patch"
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    receipt_bytes = (bundle / "source.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    if receipt != dict(schema="cairn.codex-advice-source/1", upstream_commit=BASE,
                       upstream_tag="rust-v0.166.0", patch_sha256=digest, toolchain="1.99.1"):
        parser.error("source receipt does not match this patch")
    revision = run(["git", "rev-parse", "HEAD"], source, capture_output=True, text=True).stdout.strip()
    status = run(["git", "status", "--porcelain", "--untracked-files=normal"], source,
                 capture_output=True, text=True).stdout
    if revision != BASE or status:
        parser.error("source must be a clean exact-base checkout")
    if output.exists() or output == source or source in output.parents:
        parser.error("output must be a new directory outside the source checkout")
    run(["git", "apply", "--check", str(patch)], source)
    run(["git", "apply", str(patch)], source)
    applied_source = source_digest(source)

    def verify_source():
        if source_digest(source) != applied_source or hashlib.sha256(patch.read_bytes()).hexdigest() != digest or (bundle / "source.json").read_bytes() != receipt_bytes:
            raise RuntimeError("source or selected patch changed during build; artifact acceptance incomplete")

    env = dict(os.environ, CAIRN_ADVICE_PATCH_SHA256=digest,
               CARGO_TARGET_DIR=str(source / "codex-rs/target"))
    run(["cargo", "+1.99.1", "build", "--locked", "-p", "codex-cli"], source / "codex-rs", env=env)
    binary = source / "codex-rs/target/debug/codex"
    capability = dict(schema="cairn.codex-advice-capabilities/1",
                      request_schema="cairn.codex-advice-request/1",
                      result_schema="cairn.codex-advice-result/1", provider="openai",
                      auth_method="chatgpt_subscription", one_request=True, tools=False,
                      retries=False, upstream_commit=BASE, patch_sha256=digest)

    def verify(path):
        result = run([str(path), "cairn-advice", "--capabilities"], source,
                     capture_output=True, text=True, timeout=15)
        if result.stderr or json.dumps(json.loads(result.stdout), sort_keys=True) != json.dumps(capability, sort_keys=True):
            raise RuntimeError("native capability differs from selected source receipt")

    verify(binary)
    verify_source()
    output.mkdir(parents=True, mode=0o700)
    selected = output / "codex"
    with binary.open("rb") as reader, selected.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
    run(["strip", "--strip-debug", str(selected)], source)
    selected.chmod(0o500)
    verify(selected)
    verify_source()
    with selected.open("rb") as reader:
        binary_digest = hashlib.file_digest(reader, "sha256").hexdigest()
    manifest = dict(schema="cairn.codex-advice/1", binary_path=str(selected),
                    binary_sha256=binary_digest, upstream_commit=BASE, patch_sha256=digest,
                    capability_schema=capability["schema"], request_schema=capability["request_schema"],
                    result_schema=capability["result_schema"], provider="openai",
                    auth_method="chatgpt_subscription")
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    manifest_path.chmod(0o400)
    build = dict(manifest, applied_source_sha256=applied_source,
                 rustc=run(["rustc", "+1.99.1", "-Vv"], source, capture_output=True, text=True).stdout,
                 cargo=run(["cargo", "+1.99.1", "-V"], source, capture_output=True, text=True).stdout.strip(),
                 strip=run(["strip", "--version"], source, capture_output=True, text=True).stdout.splitlines()[0])
    build["schema"] = "cairn.codex-advice-build/1"
    (output / "build.json").write_text(json.dumps(build, indent=2) + "\n")
    print(manifest_path)


if __name__ == "__main__":
    main()
