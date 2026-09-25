#!/usr/bin/env python3
"""Verify that the retained design snapshots match their pinned manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCES = (ROOT / 'docs/sources').resolve()


def check(manifest):
    data = json.loads(manifest.read_text())
    if data.get('schema') != 'cairn.design-sources/1' or not isinstance(data.get('sources'), list) or not data['sources']:
        raise ValueError('invalid design source manifest')
    seen = set()
    for entry in data['sources']:
        if not isinstance(entry, dict) or not isinstance(entry.get('snapshot'), str):
            raise ValueError('invalid design source entry')
        expected = entry.get('sha256')
        if not isinstance(expected, str) or re.fullmatch(r'[0-9a-f]{64}', expected) is None:
            raise ValueError('invalid design source digest')
        snapshot = (ROOT / entry['snapshot']).resolve(strict=True)
        if not snapshot.is_relative_to(SOURCES) or not snapshot.is_file() or snapshot in seen:
            raise ValueError('design source snapshot is outside the collection or duplicated')
        seen.add(snapshot)
        if hashlib.sha256(snapshot.read_bytes()).hexdigest() != expected:
            raise ValueError(f'design source digest mismatch: {snapshot.relative_to(ROOT)}')
    return len(seen)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=SOURCES / 'manifest.json')
    args = parser.parse_args()
    try:
        count = check(args.manifest)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(1, f'{exc}\n')
    print(f'{count} pinned design source snapshots match their SHA-256 digests')
