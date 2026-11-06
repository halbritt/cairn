#!/usr/bin/env python3
"""Build immutable normal79/lower79/old78 candidates; no scenario execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile

OLD = '42de49de8345754a7e9da72018c1ee661ff0b9e4'


def require(value, message):
    if not value:
        raise RuntimeError(message)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest(source):
    rows = {}
    for path in sorted(Path(source).rglob('*')):
        name = str(path.relative_to(source))
        if path.is_symlink():
            rows[name] = {'symlink': os.readlink(path)}
        elif path.is_file():
            rows[name] = {'sha256': digest(path), 'mode': path.stat().st_mode & 0o777}
    return rows


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    path.chmod(0o600)


def reusable_capture(source, commit, inputs):
    # Existing work UI/restore readers deliberately forbid source symlinks.
    require(not any('symlink' in row for row in inputs.values()), 'shared source capture cannot contain symlinks')
    hashes = {name: row['sha256'] for name, row in inputs.items()}
    identity = dict(revision=commit, dirty=[], inputs=hashes,
        input_sha256=hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest())
    return dict(schema='cairn.work-ui-source/1', source_path=str(source.resolve()), source=identity)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--candidate', required=True, help='Full frozen commit SHA; never a mutable working tree')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(re.fullmatch('[0-9a-f]{40}', args.candidate), 'full immutable candidate SHA required')
    root = args.output.resolve()
    require(not root.exists(), 'build output must be new')
    root.mkdir(parents=True, mode=0o700)
    report = {'schema': 'cairn.lower-support79-build/1', 'status': 'building',
              'build_script_sha256': digest(__file__), 'candidates': {}}
    env = dict(os.environ, GOFLAGS='', GOWORK='off', GOTOOLCHAIN='go1.25.0', PYTHONDONTWRITEBYTECODE='1')
    try:
        for name, commit in [('normal', args.candidate), ('lower', args.candidate), ('old78', OLD)]:
            source = root / (name + '-source'); source.mkdir()
            archive = root / (name + '.tar')
            with archive.open('wb') as stream:
                subprocess.run(['git', 'archive', '--format=tar', commit], cwd=args.repo, stdout=stream, check=True, timeout=60)
            with tarfile.open(archive) as stream:
                stream.extractall(source, filter='data')
            policy = source / 'core/store.go'
            if name == 'lower':
                original = policy.read_text()
                changed, count = re.subn(r'^\s*"work-advice/1":\s+true,\n', '', original, flags=re.M)
                require(count == 2, 'expected exactly default/fallback supported advice map entries')
                policy.write_text(changed)
                normal = manifest(root / 'normal-source'); altered = manifest(source)
                require(normal.keys() == altered.keys() and [key for key in normal if normal[key] != altered[key]] == ['core/store.go'],
                        'lower candidate changed more than its two advice support map entries')
                diff = subprocess.run(['diff', '-u', str(root / 'normal-source/core/store.go'), str(policy)], capture_output=True, timeout=10)
                require(diff.returncode == 1, 'lower policy diff missing')
                (root / 'lower-policy.diff').write_bytes(diff.stdout)
                report['policy_delta_sha256'] = digest(root / 'lower-policy.diff')
            inputs = manifest(source)
            save(root / (name + '-source-manifest.json'), inputs)
            binary = root / ('cairn-' + name)
            with (root / (name + '-build.log')).open('wb') as log:
                result = subprocess.run(['go', 'build', '-trimpath', '-buildvcs=false', '-o', str(binary), './cmd/cairn'],
                    cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
            require(result.returncode == 0, name + ': build failed')
            require(manifest(source) == inputs, name + ': source changed during build')
            binary.chmod(0o500)
            row = dict(commit=commit, source=str(source), binary=str(binary),
                binary_sha256=digest(binary), archive_sha256=digest(archive), source_unchanged=True,
                source_manifest=str(root / (name + '-source-manifest.json')),
                source_manifest_sha256=digest(root / (name + '-source-manifest.json')))
            if name in ('normal', 'old78'):
                capture = reusable_capture(source, commit, inputs)
                capture_path = root / (name + '-capture-manifest.json')
                save(capture_path, capture)
                physical = dict(schema='cairn.physical-restore-build/1', source_commit=commit,
                    source_input_sha256=capture['source']['input_sha256'], source_unchanged=True,
                    build_returncode=result.returncode, binary_sha256=row['binary_sha256'],
                    binary=str(binary), source_path=str(source), capture_manifest=str(capture_path),
                    capture_manifest_sha256=digest(capture_path), build_script_sha256=report['build_script_sha256'],
                    command=['go', 'build', '-trimpath', '-buildvcs=false', '-o', str(binary), './cmd/cairn'],
                    environment_overrides={key: env[key] for key in ('GOFLAGS', 'GOWORK', 'GOTOOLCHAIN')})
                physical_path = root / (name + '-physical-build-receipt.json')
                save(physical_path, physical)
                row.update(capture_manifest=str(capture_path), capture_manifest_sha256=digest(capture_path),
                    physical_build_receipt=str(physical_path), physical_build_receipt_sha256=digest(physical_path))
            report['candidates'][name] = row
        # The earlier candidate remains frozen while later candidates build.
        for name, row in report['candidates'].items():
            require(digest(row['source_manifest']) == row['source_manifest_sha256']
                    and manifest(row['source']) == json.loads(Path(row['source_manifest']).read_text())
                    and digest(row['binary']) == row['binary_sha256'], name + ': final source/binary drift')
            for key in ('capture_manifest', 'physical_build_receipt'):
                if key in row:
                    require(digest(row[key]) == row[key + '_sha256'], name + ': shared receipt drift')
        require(digest(__file__) == report['build_script_sha256'], 'build helper changed')
        report['status'] = 'passed'
    except BaseException as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save(root / 'build-receipt.json', report)


if __name__ == '__main__':
    main()
