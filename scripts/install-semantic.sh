#!/usr/bin/env bash
# Stage the offline Rust semantic package. Does not change running services.
# The Python CPU worker (FastEmbed venv, semantic_rank.py, embedding_worker.py)
# is retired; there is no Python install path.
set -euo pipefail
if [[ "${1:-}" != --stage-rust ]]; then
    printf '%s\n' 'usage: install-semantic.sh --stage-rust [package arguments]' \
        'The Python semantic worker is retired; stage the Rust package instead (docs/semantic-package.md).' >&2
    exit 2
fi
shift
exec /usr/bin/python3 -I -B "$(dirname "$0")/semantic_package.py" stage "$@"
