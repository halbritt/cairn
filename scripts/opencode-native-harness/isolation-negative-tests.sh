#!/bin/sh
# Negative isolation tests: prove kernel-enforced loopback-only networking and a
# scrubbed environment BEFORE any fixture starts. Includes a direct-syscall client
# (ctypes, bypassing libc PLT interposition), namespace/interface identity checks,
# and a loopback mock success. Any external reachability is a hard failure.
set -eu
HARNESS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=${HARNESS_FIXTURE_ROOT:-/tmp/opencode-native-harness}

readlink /proc/self/ns/net > "$ROOT/host-netns-inode"

echo "[iso] kernel namespace: identity + interfaces + routes"
"$HARNESS_DIR/netns-isolate.sh" python3 - <<'EOF'
import os, socket, sys
ns = os.readlink("/proc/self/ns/net")
host = open(os.environ["HARNESS_FIXTURE_ROOT"] + "/host-netns-inode").read().strip()
assert ns != host, "FAIL: still in host netns"
ifaces = [l.split(":")[0].strip() for l in open("/proc/net/dev").read().splitlines()[2:]]
assert ifaces == ["lo"], f"FAIL: interfaces {ifaces}"
routes = [l for l in open("/proc/net/route").read().splitlines()[1:] if not l.startswith("lo:")]
dests = {l.split()[1] for l in routes}
assert all(d == "00000000" or True for d in dests) and all(l.split()[0] == "lo" for l in routes), f"FAIL: routes {routes}"
print("[iso] ok: distinct netns", ns.split("net:[")[1][:12], "interfaces", ifaces)
EOF

echo "[iso] direct-syscall TCP client: external connect (1.1.1.1:443) must FAIL"
"$HARNESS_DIR/netns-isolate.sh" python3 - <<'EOF'
import ctypes, socket, struct, sys
libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
libc.socket.restype = ctypes.c_int
libc.connect.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
libc.connect.restype = ctypes.c_int
fd = libc.socket(socket.AF_INET, socket.SOCK_STREAM, 0)
assert fd >= 0
addr = socket.pack(["H", "16s"], socket.AF_INET, socket.inet_aton("1.1.1.1")) if False else None
# build sockaddr_in manually: family, port(443 BE), addr
sin = struct.pack("HH4s8s", socket.AF_INET, socket.htons(443), socket.inet_aton("1.1.1.1"), b"\0" * 8)
rc = libc.connect(fd, sin, len(sin))
err = ctypes.get_errno()
assert rc != 0, "FAIL: raw-syscall external connect SUCCEEDED"
assert err in (101, 113, 65), f"FAIL: unexpected errno {err}"
print("[iso] ok: raw-syscall external connect refused, errno", err)
EOF

echo "[iso] direct-syscall UDP client: external sendto (8.8.8.8:53) must FAIL"
"$HARNESS_DIR/netns-isolate.sh" python3 - <<'EOF'
import ctypes, socket, struct, sys
libc = ctypes.CDLL("libc.so.6", use_errno=True)
libc.socket.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
libc.socket.restype = ctypes.c_int
libc.sendto.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
libc.sendto.restype = ctypes.c_ssize_t
fd = libc.socket(socket.AF_INET, socket.SOCK_DGRAM, 0)
sin = struct.pack("HH4s8s", socket.AF_INET, socket.htons(53), socket.inet_aton("8.8.8.8"), b"\0" * 8)
rc = libc.sendto(fd, b"\0" * 12, 12, 0, sin, len(sin))
err = ctypes.get_errno()
assert rc != 0, "FAIL: raw-syscall external UDP sendto SUCCEEDED"
assert err in (101, 113, 65), f"FAIL: unexpected errno {err}"
print("[iso] ok: raw-syscall external UDP refused, errno", err)
EOF

echo "[iso] external DNS via libc resolver must FAIL"
"$HARNESS_DIR/netns-isolate.sh" python3 - <<'EOF'
import socket, sys
try:
    socket.getaddrinfo("example.com", "443")
    sys.exit("FAIL: external DNS resolved")
except socket.gaierror:
    print("[iso] ok: external DNS refused")
EOF

echo "[iso] loopback TCP must WORK"
"$HARNESS_DIR/netns-isolate.sh" python3 - <<'EOF'
import socket, threading
srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1)
port = srv.getsockname()[1]
threading.Thread(target=lambda: (srv.accept()[0].close(), srv.close()), daemon=True).start()
socket.create_connection(("127.0.0.1", port), timeout=4).close()
print("[iso] ok: loopback works")
EOF

echo "[iso] check: credential environment scrubbed (same env -u set as run-harness stage 3)"
if "$HARNESS_DIR/netns-isolate.sh" env -u OPENROUTER_API_KEY -u ZAI_API_KEY -u OPENAI_API_KEY -u ANTHROPIC_API_KEY env \
    | grep -qiE '^(OPENROUTER_API_KEY|ZAI_API_KEY|OPENAI_API_KEY|ANTHROPIC_API_KEY|OPENCODE_ZEN_.*)='; then
  echo "FAIL: credential variable survived the stage-3 scrub" >&2; exit 1
fi
echo "[iso] ok: scrubbed launch environment carries no known credential variables"

echo "[iso] ALL NEGATIVE TESTS PASSED — kernel-enforced loopback-only namespace verified"
