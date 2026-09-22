#!/bin/sh
# Fail-closed network isolation for the OpenCode native admission harness.
# Enters a user namespace with a fresh network namespace (loopback only).
# Refuses to run anything if namespace isolation is unavailable.
set -eu
if [ "$(id -u)" = "0" ]; then
  exec unshare -n "$@"
fi
if command -v unshare >/dev/null 2>&1 && unshare -rn true 2>/dev/null; then
  exec unshare -rn "$@"
fi
echo "FAIL-CLOSED: network namespace isolation (unshare -rn) unavailable; refusing to start fixtures" >&2
exit 77
