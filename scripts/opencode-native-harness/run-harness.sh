#!/bin/sh
# Orchestrates the fail-closed harness: negative isolation tests first, then the
# native admission probe inside the loopback-only namespace with a scrubbed
# environment. Refuses to start fixtures if any negative test fails.
set -eu
HARNESS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${HARNESS_FIXTURE_ROOT:-/tmp/opencode-native-harness}
OPENCODE=${HARNESS_OPENCODE:-/home/halbritt/.npm-global/bin/opencode}
MOCK_PORT=18131

echo "[harness] stage 1: negative isolation tests (must pass before any fixture)"
"$HARNESS_DIR/isolation-negative-tests.sh"

echo "[harness] stage 2: prepare fixture root with explicit mock-only config"
mkdir -p "$ROOT/config/opencode" "$ROOT/data" "$ROOT/cache" "$ROOT/workspace"
cat > "$ROOT/config/opencode/opencode.json" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "mock": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Mock",
      "options": { "baseURL": "http://127.0.0.1:$MOCK_PORT/v1", "apiKey": "unused-loopback-only" },
      "models": { "mock-model": { "name": "Mock Model", "release_date": "2026-01-01", "attachment": false, "reasoning": false, "temperature": true, "tool_call": true, "cost": { "input": 0, "output": 0, "cache_read": 0, "cache_write": 0 } } }
    }
  },
  "model": "mock/mock-model",
  "small_model": "mock/mock-model",
  "autoupdate": false,
  "share": "disabled"
}
EOF

echo "[harness] stage 3: admission probe inside the namespace (canary-checked)"
"$HARNESS_DIR/netns-isolate.sh" env -u OPENROUTER_API_KEY -u ZAI_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY \
  HARNESS_FIXTURE_ROOT="$ROOT" HARNESS_OPENCODE="$OPENCODE" \
  python3 "$HARNESS_DIR/admission_probe.py" "${1:-3}"
