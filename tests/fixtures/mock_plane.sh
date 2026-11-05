#!/usr/bin/env bash
# Mock Plane CLI fixture matching the exact installed plane-cli output contracts:
# - stdout carries exactly one JSON envelope: {"ok":true,"data":...,"meta":{...}} or {"ok":false,"error":{...}}
# - exit codes: 0 ok, 2 usage/config, 3 auth (401/403), 4 not found (404), 5 validation, 6 server/network, 7 rate-limited
# - all UUIDs are valid hex [0-9a-f] RFC 4122 strings
set -euo pipefail

MODE="${MOCK_PLANE_MODE:-normal}"
FIXTURE_DIR="$(dirname "$0")"

# Invariant check: reject mutating commands with exit code 2 (usage error)
for arg in "$@"; do
    case "$arg" in
        create|update|delete|patch|edit|archive|remove)
            echo '{"ok":false,"error":{"code":"MUTATION_FORBIDDEN","message":"Mutating commands forbidden in read-only Plane connector","http_status":403,"details":"'${arg}'"}}'
            exit 2
            ;;
    esac
done

case "$MODE" in
    normal)
        if [[ "$*" == *"project get"* ]]; then
            echo '{"ok":true,"data":{"id":"11111111-0000-4000-8000-000000000001","name":"Test Project","identifier":"TEST"},"meta":{}}'
        else
            cat "$FIXTURE_DIR/plane_issues_normal.json"
        fi
        ;;
    too_large)
        # Generate JSON envelope with 10,001 items having valid hex UUIDs
        python3 -c '
import json, uuid
items = [
    {
        "id": f"e2e00001-{i:04x}-4000-8000-{i:012x}"[-36:],
        "name": f"Issue {i}",
        "sequence_id": i,
        "state": {"id": "a0000001-0000-4000-8000-000000000001", "name": "Backlog", "group": "backlog"},
        "assignees": [],
        "updated_at": "2026-11-05T12:00:00Z"
    }
    for i in range(10001)
]
print(json.dumps({"ok": True, "data": items, "meta": {"total": 10001}}))
'
        ;;
    output_overflow)
        # Output > 16 MiB of data
        python3 -c '
import sys
chunk = ("A" * 1024 * 1024).encode()
for _ in range(17):
    sys.stdout.buffer.write(chunk)
'
        ;;
    timeout)
        sleep 40
        ;;
    auth_failure)
        # Real Plane CLI emits auth error JSON on stdout with exit code 3
        echo '{"ok":false,"error":{"code":"AUTH_REQUIRED","message":"Authentication failed: invalid or missing API token","http_status":401,"details":"Token authentication refused"}}'
        exit 3
        ;;
    config_mismatch)
        # Returns project with mismatched workspace and project UUIDs
        echo '{"ok":true,"data":{"id":"ffffffff-0000-4000-8000-000000000000","workspace":"00000000-ffff-4000-8000-000000000000","name":"Mismatched Project","identifier":"MISMATCH"},"meta":{}}'
        ;;
    *)
        echo '{"ok":false,"error":{"code":"UNKNOWN_MODE","message":"Unknown MOCK_PLANE_MODE: '${MODE}'","http_status":500,"details":null}}'
        exit 2
        ;;
esac
