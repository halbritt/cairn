#!/usr/bin/env bash
# Build an immutable historical peer and test it against this checkout.
# All servers, credentials and PostgreSQL files belong to this invocation.
set -euo pipefail
cd "$(dirname "$0")/.."
legacy_revision=0fb09c3c6698d96ae62921d546e3263000e59732
candidate_revision="$(git rev-parse HEAD)"
# Test committed sources, including the harness itself, and stamp both binaries.
# A dirty checkout must be committed first so the report identifies what ran.
if [[ -n "$(git status --porcelain)" ]]; then
    echo "Commit the candidate tree before running the historical peer check." >&2
    exit 1
fi
pg_bin="${CAIRN_PG_BIN:-$(pg_config --bindir)}"
test_root="$(mktemp -d /tmp/cairn-api-skew.XXXXXXXX)"
cleanup() {
    local status=$?
    if [[ -f "$test_root/data/postmaster.pid" ]]; then
        if ! "$pg_bin/pg_ctl" -D "$test_root/data" -m immediate -w stop >/dev/null; then
            echo "Could not stop disposable PostgreSQL; retained $test_root" >&2
            exit 1
        fi
    fi
    if [[ "$status" -eq 0 ]]; then
        rm -rf -- "$test_root"
    else
        echo "Version-skew check failed; retained $test_root" >&2
    fi
    exit "$status"
}
trap cleanup EXIT
# A private local clone has a real .git directory, so Go's VCS discovery stamps
# the historical build even on toolchains that overlook linked-worktree files.
git clone --quiet --shared --no-checkout . "$test_root/legacy"
git -C "$test_root/legacy" checkout --quiet --detach "$legacy_revision"
(cd "$test_root/legacy" && go build -o "$test_root/legacy-cairn" ./cmd/cairn)
git clone --quiet --shared --no-checkout . "$test_root/candidate"
git -C "$test_root/candidate" checkout --quiet --detach "$candidate_revision"
(cd "$test_root/candidate" && go build -o "$test_root/candidate-cairn" ./cmd/cairn)
"$pg_bin/initdb" -D "$test_root/data" --auth-local=trust --auth-host=reject --no-locale -E UTF8 >/dev/null
mkdir "$test_root/socket"
"$pg_bin/pg_ctl" -D "$test_root/data" -l "$test_root/postgres.log" \
    -o "-F -k $test_root/socket -c listen_addresses=''" -w start >/dev/null
for peer in legacy candidate; do
    "$pg_bin/createdb" -h "$test_root/socket" "cairn_$peer"
done
CAIRN_DISPOSABLE_TEST_ROOT="$test_root" CAIRN_PG_BIN="$pg_bin" \
    python3 -B scripts/check_api_version_skew.py \
    --legacy "$test_root/legacy-cairn" --candidate "$test_root/candidate-cairn" \
    --candidate-revision "$candidate_revision" --root "$test_root" "$@"
