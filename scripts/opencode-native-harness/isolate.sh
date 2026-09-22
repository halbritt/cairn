#!/bin/sh
# Applies OS-level loopback-only isolation to "$@" and executes it.
# Mechanism order: (1) network namespace via unshare -rn, (2) LD_PRELOAD
# connect() interposer built from loopback_interposer.c. Each mechanism is
# verified; if none verifies, exits 77 WITHOUT running the command.
set -eu
HARNESS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${HARNESS_FIXTURE_ROOT:-/tmp/opencode-native-harness}

if [ "$(id -u)" = "0" ] && command -v unshare >/dev/null 2>&1; then
  exec unshare -n "$@"
fi

if command -v unshare >/dev/null 2>&1 && unshare -rn true 2>/dev/null; then
  exec unshare -rn "$@"
fi

# Fallback: LD_PRELOAD connect interposer (verified by isolation-negative-tests).
SO_DIR=${ISOLATE_SO_DIR:-$ROOT/isolate}
mkdir -p "$SO_DIR"
SO="$SO_DIR/loopback_interposer.so"
if [ ! -f "$SO" ]; then
  CC=$(command -v gcc || command -v cc)
  [ -n "$CC" ] || { echo "FAIL-CLOSED: no unshare and no compiler for interposer" >&2; exit 77; }
  $CC -shared -fPIC -O2 -o "$SO" "$HARNESS_DIR/loopback_interposer.c" -ldl
fi
export LD_PRELOAD="$SO"
exec "$@"
