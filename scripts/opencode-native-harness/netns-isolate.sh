#!/bin/sh
# Executes "$@" inside a fresh kernel network namespace containing ONLY a brought-up
# loopback interface, dropped back to the invoking user with no_new_privs.
# Fail-closed: if sudo namespace setup cannot be VERIFIED, exits 77 without running
# the workload. The LD_PRELOAD interposer fallback was removed by root review:
# LD_PRELOAD is not OS network isolation (direct syscalls/static code bypass it).
set -eu
HARNESS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${HARNESS_FIXTURE_ROOT:-/tmp/opencode-native-harness}

if [ "${HARNESS_IN_NETNS:-0}" = "1" ]; then
  # Inside the namespace: fail-closed identity checks before the workload runs.
  NS_INODE=$(readlink /proc/self/ns/net)
  HOST_NS_INODE=$(cat "$ROOT/host-netns-inode" 2>/dev/null || echo unknown)
  if [ "$HOST_NS_INODE" != "unknown" ] && [ "$NS_INODE" = "$HOST_NS_INODE" ]; then
    echo "FAIL-CLOSED: still in the host network namespace" >&2
    exit 77
  fi
  # /proc/net/dev is netns-aware; /sys/class/net is NOT (stale sysfs after unshare).
  IFACES=$(awk 'NR>2 {sub(":.*","",$1); printf "%s ", $1}' /proc/net/dev)
  echo "[netns] interfaces: $IFACES" >&2
  case "$IFACES" in
    *eth*|*enp*|*wlan*|*tailscale*|*docker*|*br-*|*veth*|*wwan*|*wlp*)
      echo "FAIL-CLOSED: unexpected host interfaces visible in namespace: $IFACES" >&2
      exit 77 ;;
  esac
  echo "$IFACES" | grep -q '\blo\b' || { echo "FAIL-CLOSED: loopback missing" >&2; exit 77; }
  exec "$@"
fi

UID_ARG=$(id -u)
GID_ARG=$(id -g)
GROUPS_ARG=$(id -G | tr ' ' ',')

# Verify sudo + namespace creation BEFORE running any workload.
sudo -n unshare --net -- /bin/true

exec sudo -n env \
  HARNESS_UID="$UID_ARG" HARNESS_GID="$GID_ARG" HARNESS_GROUPS="$GROUPS_ARG" \
  HARNESS_DIR="$HARNESS_DIR" HARNESS_FIXTURE_ROOT="$ROOT" HARNESS_OPENCODE="${HARNESS_OPENCODE:-/home/halbritt/.npm-global/bin/opencode}" \
  unshare --net -- /bin/sh -c '
    ip link set lo up
    exec setpriv --reuid "$HARNESS_UID" --regid "$HARNESS_GID" --groups "$HARNESS_GROUPS" \
      --no-new-privs -- env HARNESS_IN_NETNS=1 HARNESS_DIR="$HARNESS_DIR" \
      HARNESS_FIXTURE_ROOT="$HARNESS_FIXTURE_ROOT" HARNESS_OPENCODE="$HARNESS_OPENCODE" \
      "$@"' sh "$@"
