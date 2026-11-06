#!/usr/bin/env python3
"""Reproduce the carried native patch in an explicit clean scratch checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import re
import urllib.request

BASE = '6ff1e1ca3b30fc8b914720681fcf5fe62e95f856'
CONTRACT = 'codex.owner-input/1'
ATTESTATION = 'cairn.owner-input-attestation/1'
TARGET = 'x86_64-unknown-linux-gnu'


def prepare_v8(source, cache):
    """Follow the pinned upstream setup-rusty-v8 checksum chain, locally."""
    version = '152.2.0'
    profile = 'ptrcomp_sandbox_release'
    archive = f'librusty_v8_{profile}_{TARGET}.a.gz'
    binding = f'src_binding_{profile}_{TARGET}.rs'
    manifest = f'rusty_v8_{profile}_{TARGET}.sha256'
    trusted = source / 'third_party/v8/rusty_v8_152_2_0_release_manifests.sha256'
    matches = [line.split()[0] for line in trusted.read_text().splitlines()
               if line.split()[1:] == [manifest]]
    if len(matches) != 1 or not re.fullmatch(r'[a-f0-9]{64}', matches[0]):
        raise ValueError('pinned source lacks the exact V8 checksum manifest')
    base = f'https://github.com/openai/codex/releases/download/rusty-v8-v{version}/'
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)

    def verified(name, expected, limit):
        path = cache / name
        if not path.exists():
            pending = path.with_suffix(path.suffix + '.partial')
            try:
                with urllib.request.urlopen(base + name, timeout=60) as response, pending.open('xb') as writer:
                    total = 0
                    while chunk := response.read(1048576):
                        total += len(chunk)
                        if total > limit:
                            raise ValueError('V8 artifact exceeds size bound')
                        writer.write(chunk)
                pending.replace(path)
            finally:
                pending.unlink(missing_ok=True)
        with path.open('rb') as reader:
            actual = hashlib.file_digest(reader, 'sha256').hexdigest()
        if path.stat().st_size > limit or actual != expected:
            raise ValueError('V8 artifact checksum mismatch: ' + name)
        return path

    manifest_path = verified(manifest, matches[0], 4096)
    entries = [line.split() for line in manifest_path.read_text().splitlines()]
    if (len(entries) != 2 or any(len(row) != 2 or not re.fullmatch(r'[a-f0-9]{64}', row[0]) for row in entries)
            or {row[1] for row in entries} != {archive, binding}):
        raise ValueError('V8 checksum manifest must name exactly the selected archive and binding')
    checksums = {name: checksum for checksum, name in entries}
    archive_path = verified(archive, checksums[archive], 1024**3)
    binding_path = verified(binding, checksums[binding], 16*1024**2)
    return dict(version=version, target=TARGET, release_url=base,
                manifest_sha256=matches[0], archive_sha256=checksums[archive], binding_sha256=checksums[binding],
                archive_path=str(archive_path), binding_path=str(binding_path))


def run(arguments, directory, **kwargs):
    return subprocess.run(arguments, cwd=directory, check=True, **kwargs)


def source_inputs(source):
    names=run(['git','ls-files','-z','--cached','--others','--exclude-standard'],
              source,capture_output=True).stdout.split(b'\0')
    result={}
    for name in sorted(set(names)-{b''}):
        path=source/os.fsdecode(name)
        with path.open('rb') as stream:
            item=dict(mode=path.lstat().st_mode & 0o7777,sha256=hashlib.file_digest(stream,'sha256').hexdigest())
        if path.is_symlink(): item['symlink']=os.readlink(path)
        result[os.fsdecode(name)]=item
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True, help='clean exact-base scratch checkout; receives patch')
    parser.add_argument('--output', type=Path, required=True, help='new artifact directory outside source')
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    bundle = Path(__file__).resolve().parent
    receipt_bytes = (bundle / 'source.json').read_bytes()
    receipt = json.loads(receipt_bytes)
    patch = bundle / 'owner-origin.patch'
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    if receipt != dict(schema='cairn.codex-owner-origin-source/1', upstream_commit=BASE,
                       upstream_tag='rust-v0.166.0', patch_sha256=digest, contract=CONTRACT, attestation_contract=ATTESTATION,
                       toolchain='1.99.1'):
        parser.error('source receipt does not match this patch')
    revision = run(['git', 'rev-parse', 'HEAD'], source, capture_output=True, text=True).stdout.strip()
    status = run(['git', 'status', '--porcelain', '--untracked-files=normal'], source,
                 capture_output=True, text=True).stdout
    if revision != BASE or status:
        parser.error('source must be a clean checkout of the exact pinned upstream commit')
    if output.exists() or output == source or source in output.parents:
        parser.error('output must be a new directory outside the source checkout')
    toolchain = run(['rustc', '+1.99.1', '-Vv'], source, capture_output=True, text=True).stdout
    if f'host: {TARGET}\n' not in toolchain:
        parser.error('this validation package supports only ' + TARGET)
    v8 = prepare_v8(source, source.parent / 'cairn-v8-152.2.0' / TARGET)
    run(['git', 'apply', '--check', str(patch)], source)
    run(['git', 'apply', str(patch)], source)
    applied_inputs=source_inputs(source)
    applied_manifest=json.dumps(dict(upstream_commit=BASE,files=applied_inputs),sort_keys=True,separators=(',',':')).encode()
    applied_digest=hashlib.sha256(applied_manifest).hexdigest()

    def verify_source():
        if (source_inputs(source)!=applied_inputs
                or run(['git','rev-parse','HEAD'],source,capture_output=True,text=True).stdout.strip()!=BASE
                or hashlib.sha256(patch.read_bytes()).hexdigest()!=digest
                or (bundle/'source.json').read_bytes()!=receipt_bytes):
            raise RuntimeError('source or selected patch changed during build; artifact acceptance incomplete')

    env = dict(os.environ, CAIRN_OWNER_ORIGIN_PATCH_SHA256=digest,
               CARGO_TARGET_DIR=str(source / 'codex-rs/target'))
    for key in tuple(env):
        if key.startswith('RUSTY_V8_') or key in ('V8_FROM_SOURCE', 'V8_FORCE_DEBUG'):
            del env[key]
    env.update(RUSTY_V8_ARCHIVE=v8['archive_path'], RUSTY_V8_SRC_BINDING_PATH=v8['binding_path'])
    # No source file contains its own patch digest. The artifact binds the final
    # canonical patch through this compile-time stamp after application checks.
    run(['cargo', '+1.99.1', 'build', '--locked', '-p', 'codex-cli', '-p', 'codex-code-mode-host'], source / 'codex-rs', env=env)
    binary = source / 'codex-rs/target/debug/codex'
    probe = run([str(binary), '--cairn-owner-origin-capabilities'], source, capture_output=True, text=True)
    capability = dict(schema='codex.owner-input-capabilities/1', contract=CONTRACT, version=1, attestation_contract=ATTESTATION,
                      upstream_commit=BASE, patch_sha256=digest)
    if json.loads(probe.stdout) != capability or probe.stderr:
        raise RuntimeError('built native capability differs from the selected source receipt')
    verify_source()
    output.mkdir(mode=0o700, parents=True)
    selected = output / 'codex'
    # Both executables come from this exact source build. Code mode resolves
    # its helper as the adjacent fixed-name sibling of the running Codex.
    digests = {}
    for name in ('codex', 'codex-code-mode-host'):
        built = source / 'codex-rs/target/debug' / name
        packaged = output / name
        with built.open('rb') as reader, packaged.open('xb') as writer:
            import shutil
            shutil.copyfileobj(reader, writer)
        run(['strip', '--strip-debug', str(packaged)], source)
        packaged.chmod(0o500)
        with packaged.open('rb') as reader:
            digests[name] = hashlib.file_digest(reader, 'sha256').hexdigest()
    selected_probe = run([str(selected), '--cairn-owner-origin-capabilities'], source, capture_output=True, text=True)
    if json.loads(selected_probe.stdout) != capability or selected_probe.stderr:
        raise RuntimeError('packaged native capability differs from the selected source receipt')
    verify_source()
    with selected.open('rb') as reader:
        runtime_digest = hashlib.file_digest(reader, 'sha256').hexdigest()
    manifest = dict(schema='cairn.codex-owner-origin/1', binary_path=str(selected),
                    binary_sha256=runtime_digest,
                    code_mode_host=dict(path=str(output / 'codex-code-mode-host'), sha256=digests['codex-code-mode-host']),
                    **{key: capability[key] for key in ('contract', 'version', 'upstream_commit', 'patch_sha256', 'attestation_contract')})
    manifest_path = output / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    manifest_path.chmod(0o400)
    build = dict(manifest,
                 rustc=toolchain, v8_inputs=v8,
                 applied_source_sha256=applied_digest, applied_source_inputs=applied_inputs,
                 cargo=run(['cargo', '+1.99.1', '-V'], source, capture_output=True, text=True).stdout.strip())
    build['schema'] = 'cairn.codex-owner-origin-build/1'
    build['strip'] = run(['strip', '--version'], source, capture_output=True, text=True).stdout.splitlines()[0]
    (output / 'build.json').write_text(json.dumps(build, indent=2) + '\n')
    print(manifest_path)


if __name__ == '__main__':
    main()
