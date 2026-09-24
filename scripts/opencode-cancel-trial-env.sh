#!/usr/bin/env bash
# Disposable Cairn store and API for the OpenCode cancellation trial (CAIRN-2).
# Never touches the production database, API socket, profile or watcher.
#
#   opencode-cancel-trial-env.sh up DIR    create DIR and print env exports
#   opencode-cancel-trial-env.sh down DIR  stop the API and cluster, remove DIR
#
# DIR must not exist for `up`. It holds the cluster (pg/), the API home with
# its socket (home/api.sock), two trial tokens (home/trial-agent.token for the
# OpenCode session, home/trial-sender.token for the publisher) and a built
# cairn binary. Run `down` after recording the trial, including when C5 leaves
# an attempt held: the held state is disposed of with this store.
#
# `up` writes DIR/trial-manifest.json with the canonical directory and the API
# process identity (pid, /proc start time, boot id, executable). `down` signals
# and deletes nothing unless the manifest matches DIR and the trial layout; it
# signals the API only through a pidfd whose identity still matches, waits for
# it and the cluster to stop, and keeps every artifact if anything fails.
set -euo pipefail
cd "$(dirname "$0")/.."
mode="${1:-}" dir="${2:-}"
[[ -n "$mode" && -n "$dir" ]] || { echo "usage: $0 up|down DIR" >&2; exit 2; }
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"

# manifest record DIR PID | manifest stop-api DIR
manifest() {
    python3 - "$@" <<'PY'
import json, os, signal, sys, time
from pathlib import Path

mode, root = sys.argv[1], Path(sys.argv[2])
path = root / 'trial-manifest.json'
SCHEMA = 'cairn.opencode-cancel-trial/1'

def identity(pid):
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        exe = os.readlink(f'/proc/{pid}/exe')
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    fields = raw[raw.rindex(')') + 2:].split()
    return dict(pid=pid, start=int(fields[19]), state=fields[0], exe=exe,
                boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip())

if mode == 'record':
    api = identity(int(sys.argv[3]))
    if not api or api['exe'] != str(root / 'cairn'):
        sys.exit('trial API process identity could not be recorded')
    api.pop('state')
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        json.dump(dict(schema=SCHEMA, dir=str(root), api=api), out)
    sys.exit(0)

# stop-api: validate the manifest and trial layout, then stop only that process.
try:
    manifest = json.loads(path.read_text())
except (OSError, ValueError):
    sys.exit(f'{root} has no readable trial manifest; nothing was signalled or deleted')
identities = root / 'home' / 'identities.json'
if (manifest.get('schema') != SCHEMA or manifest.get('dir') != str(root) or
        not (root / 'pg' / 'PG_VERSION').is_file() or not identities.is_file() or
        not all(entry.get('principal', '').startswith('opencode-cancel-trial/')
                for entry in json.loads(identities.read_text()))):
    sys.exit(f'{root} does not match its trial manifest; nothing was signalled or deleted')
api = manifest['api']

def ours():
    current = identity(api['pid'])
    return bool(current and current['state'] != 'Z' and
                all(current[key] == api[key] for key in ('start', 'boot', 'exe')))

if ours():
    try:
        pidfd = os.pidfd_open(api['pid'])
    except ProcessLookupError:
        pidfd = None
    if pidfd is not None:
        try:
            for sig, wait in ((signal.SIGTERM, 10), (signal.SIGKILL, 5)):
                if not ours():
                    break
                signal.pidfd_send_signal(pidfd, sig)
                deadline = time.monotonic() + wait
                while ours() and time.monotonic() < deadline:
                    time.sleep(0.1)
        except ProcessLookupError:
            pass
        finally:
            os.close(pidfd)
if ours():
    sys.exit(f'trial API {api["pid"]} did not stop; artifacts retained at {root}')
PY
}

case "$mode" in
up)
    [[ ! -e "$dir" ]] || { echo "$dir already exists" >&2; exit 1; }
    mkdir -m 700 -p "$dir"
    dir="$(cd "$dir" && pwd)"
    # Unix socket paths are limited to 107 bytes; home/api.sock and the
    # cluster's socket/.s.PGSQL.5432 live under DIR.
    if (( ${#dir} > 80 )); then
        rmdir "$dir"
        echo "DIR path is too long for Unix sockets; use a short one such as /tmp/cairn-oc-trial" >&2
        exit 1
    fi
    api_pid=
    failed() {
        # The API is this shell's own unreaped child, so its pid cannot be reused.
        [[ -n "$api_pid" ]] && kill "$api_pid" 2>/dev/null && wait "$api_pid" 2>/dev/null || true
        if [[ -f "$dir/pg/postmaster.pid" ]]; then
            "$pg_bin/pg_ctl" -D "$dir/pg" -m immediate -w stop >/dev/null || true
        fi
        echo "trial environment setup failed; logs retained at $dir" >&2
    }
    trap failed ERR
    "$pg_bin/initdb" -D "$dir/pg" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >/dev/null
    mkdir "$dir/socket"
    "$pg_bin/pg_ctl" -D "$dir/pg" -l "$dir/postgres.log" -o "-F -k $dir/socket -c listen_addresses=''" -w start >/dev/null
    "$pg_bin/createdb" -h "$dir/socket" cairn_trial
    dsn="host=$dir/socket dbname=cairn_trial sslmode=disable"
    go build -o "$dir/cairn" ./cmd/cairn
    CAIRN_DATABASE_URL="$dsn" "$dir/cairn" migrate >/dev/null
    repo="opencode-cancel-trial:$(python3 -c 'import uuid; print(uuid.uuid4())')"
    mkdir -m 700 "$dir/home"
    python3 - "$dir/home" "$repo" <<'PY'
import hashlib, json, os, secrets, sys
home, repo = sys.argv[1], sys.argv[2]
identities = []
for name in ('trial-agent', 'trial-sender'):
    token = secrets.token_urlsafe(32)
    path = os.path.join(home, name + '.token')
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        out.write(token)
    identities.append(dict(principal='opencode-cancel-trial/' + name, role='agent', repo=repo,
                           destination='hosted', token_sha256=hashlib.sha256(token.encode()).hexdigest()))
with open(os.open(os.path.join(home, 'identities.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
    json.dump(identities, out)
PY
    CAIRN_HOME="$dir/home" CAIRN_DATABASE_URL="$dsn" nohup "$dir/cairn" serve \
        >/dev/null 2>"$dir/api.log" &
    api_pid=$!
    for _ in $(seq 100); do [[ -S "$dir/home/api.sock" ]] && break; sleep 0.1; done
    [[ -S "$dir/home/api.sock" ]] || { failed; exit 1; }
    manifest record "$dir" "$api_pid"
    trap - ERR
    cat <<ENV
export CAIRN_TRIAL_DIR='$dir'
export CAIRN_TRIAL_REPO='$repo'
export CAIRN_TRIAL_BINARY='$dir/cairn'
export CAIRN_TRIAL_SOCKET='$dir/home/api.sock'
export CAIRN_TRIAL_AGENT_TOKEN='$dir/home/trial-agent.token'
export CAIRN_TRIAL_SENDER_TOKEN='$dir/home/trial-sender.token'
# Operator commands (work-cancel, coordination-review) against the trial store only:
export CAIRN_TRIAL_OPERATOR_ENV="CAIRN_HOME='$dir/home' CAIRN_DATABASE_URL='$dsn'"
ENV
    ;;
down)
    [[ -d "$dir" ]] || { echo "$dir is not a directory" >&2; exit 1; }
    dir="$(cd "$dir" && pwd)"
    manifest stop-api "$dir"
    if [[ -f "$dir/pg/postmaster.pid" ]] &&
            ! "$pg_bin/pg_ctl" -D "$dir/pg" -m immediate -w stop >/dev/null; then
        echo "trial cluster did not stop; artifacts retained at $dir" >&2
        exit 1
    fi
    rm -rf -- "$dir"
    ;;
*)
    echo "usage: $0 up|down DIR" >&2; exit 2 ;;
esac
