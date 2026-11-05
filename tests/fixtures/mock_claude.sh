#!/usr/bin/env bash
# Mock Claude CLI fixture matching native installed Claude Code contracts:
# - Result envelope: {"type":"result","subtype":...,"is_error":false,"total_cost_usd":...,"structured_output":{...}}
# - Terminal error shapes: {"type":"result","subtype":...,"is_error":true,"terminal_reason":"api_error","api_error_status":429|401,...}
# - Handles --help, --version, auth status, --print / print mode
# - Rejects unknown invocations
set -euo pipefail

MODE="${MOCK_CLAUDE_MODE:-normal}"
FIXTURE_DIR="$(dirname "$0")"

# Check standard flags and subcommands
for arg in "$@"; do
    case "$arg" in
        --help|-h)
            echo "Claude CLI (mock test fixture)"
            echo "Usage: claude [flags] [prompt]"
            exit 0
            ;;
        --version|-v)
            echo "claude 2.1.273"
            exit 0
            ;;
        auth)
            if [[ "$*" == *"status"* ]]; then
                if [[ "$MODE" == "auth_failure" ]]; then
                    echo '{"status":"unauthenticated","error":"No valid subscription found"}'
                    exit 1
                fi
                echo '{"status":"authenticated","subscription":"first_party_claude_pro"}'
                exit 0
            fi
            ;;
    esac
done

# In print / evaluation mode
case "$MODE" in
    normal)
        cat "$FIXTURE_DIR/claude_proposals_normal.json"
        ;;
    empty)
        echo '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.001,"structured_output":{"proposals":[]}}'
        ;;
    tool_request|invalid)
        cat "$FIXTURE_DIR/claude_proposals_invalid.json"
        ;;
    arbitrary_url)
        echo '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.002,"structured_output":{"proposals":[{"action_type":"link_artifact","action_index":0,"target_payload":{"url":"http://evil.example.com/exploit"},"rationale":"arbitrary external URL"}]}}'
        ;;
    unknown_action)
        echo '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":0.002,"structured_output":{"proposals":[{"action_type":"delete_production_database","action_index":0,"target_payload":{},"rationale":"unknown action type"}]}}'
        ;;
    malformed_json)
        echo '<<< MALFORMED NON-JSON OUTPUT >>>'
        ;;
    quota_exhausted)
        # Matches native terminal failure evidence: terminal_reason="api_error", api_error_status=429
        echo '{"type":"result","subtype":"error_limit","is_error":true,"terminal_reason":"api_error","api_error_status":429,"error":{"type":"rate_limit_error","message":"Resource has been exhausted (quota limit reached)"}}'
        exit 1
        ;;
    auth_failure)
        # Matches native terminal failure evidence: terminal_reason="api_error", api_error_status=401
        echo '{"type":"result","subtype":"error","is_error":true,"terminal_reason":"api_error","api_error_status":401,"error":{"type":"authentication_error","message":"Valid first-party Claude subscription token required"}}'
        exit 1
        ;;
    timeout)
        sleep 120
        ;;
    output_overflow)
        python3 -c '
import json
proposals = [
    {
        "action_type": "annotate_progress",
        "action_index": i,
        "target_payload": {"text": "A" * 1024},
        "rationale": "large payload test"
    }
    for i in range(25)
]
huge = {"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.05, "structured_output": {"proposals": proposals}}
print(json.dumps(huge))
'
        ;;
    *)
        echo "Unknown MOCK_CLAUDE_MODE: $MODE" >&2
        exit 2
        ;;
esac
