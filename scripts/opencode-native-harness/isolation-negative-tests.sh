#!/bin/sh
# Negative isolation tests: prove that inside the namespace non-loopback egress is
# impossible while loopback works. Every check must hold before any fixture starts.
set -eu
HARNESS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

echo "[iso] check: unshare -rn is available"
$HARNESS_DIR/netns-isolate.sh true

echo "[iso] check: external TCP connect (1.1.1.1:443) must FAIL"
if $HARNESS_DIR/netns-isolate.sh python3 - <<'EOF'
import socket, sys
s = socket.socket()
s.settimeout(4)
try:
    s.connect(("1.1.1.1", 443))
    sys.exit(0)  # egress possible -> isolation broken
except OSError:
    sys.exit(7)  # expected
EOF
then
  echo "FAIL: external TCP connect succeeded inside namespace" >&2; exit 1
else
  code=$?
  [ "$code" = "7" ] || { echo "FAIL: unexpected probe error $code" >&2; exit 1; }
fi
echo "[iso] ok: external TCP refused"

echo "[iso] check: external DNS resolution must FAIL"
if $HARNESS_DIR/netns-isolate.sh python3 - <<'EOF'
import socket, sys
try:
    socket.getaddrinfo("example.com", "443")
    sys.exit(0)
except socket.gaierror:
    sys.exit(7)
EOF
then
  echo "FAIL: external DNS resolved inside namespace" >&2; exit 1
else
  code=$?
  [ "$code" = "7" ] || { echo "FAIL: unexpected DNS probe error $code" >&2; exit 1; }
fi
echo "[iso] ok: external DNS refused"

echo "[iso] check: loopback TCP must WORK"
$HARNESS_DIR/netns-isolate.sh python3 - <<'EOF'
import socket, threading, sys
srv = socket.socket()
srv.bind(("127.0.0.1", 0))
srv.listen(1)
port = srv.getsockname()[1]
def accept():
    c, _ = srv.accept(); c.close()
threading.Thread(target=accept, daemon=True).start()
c = socket.create_connection(("127.0.0.1", port), timeout=4)
c.close()
srv.close()
EOF
echo "[iso] ok: loopback works"

echo "[iso] check: credential environment scrubbed"
if $HARNESS_DIR/netns-isolate.sh env | grep -qiE '^(OPENROUTER_API_KEY|ZAI_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|OPENCODE_ZEN_.*|CAIRN.*TOKEN)='; then
  echo "FAIL: credential variable still present in fixture environment" >&2; exit 1
fi
echo "[iso] ok: no credential variables"

echo "[iso] ALL NEGATIVE TESTS PASSED — namespace is loopback-only and credential-free"
