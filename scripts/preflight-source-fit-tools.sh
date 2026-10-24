#!/usr/bin/env bash
# Exercise the frozen source-fit Claude/MCP flags against a loopback script.
set -euo pipefail
cd "$(dirname "$0")/.."
probe_root="$(mktemp -d /tmp/cairn-source-fit-tools.XXXXXXXX)"
cleanup() {
    rm -rf -- "$probe_root"
}
trap cleanup EXIT
go build -o "$probe_root/cairn" ./cmd/cairn
bash scripts/trial-task-eval.sh -- python3 -B scripts/check_source_fit_tool_recovery.py \
    --binary "$probe_root/cairn" "$@"
