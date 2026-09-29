#!/usr/bin/env bash
# v3 grader for local-ci: is there local test automation, and does it run?
# Run from the case workspace. Exit 0 if an executable git hook (through core.hooksPath
# or .git/hooks) or a new Makefile target exists and exits 0 when run. Exit 1 otherwise.
set -u
run_ok() { timeout 120 "$@" >/dev/null 2>&1; }
hooks_dir="$(git config --get core.hooksPath || true)"
candidates=()
if [[ -n "$hooks_dir" ]]; then
    for h in pre-commit pre-push pre-merge-commit; do [[ -x "$hooks_dir/$h" ]] && candidates+=("$hooks_dir/$h"); done
fi
gitdir="$(git rev-parse --git-dir)"
for h in pre-commit pre-push pre-merge-commit; do [[ -x "$gitdir/hooks/$h" ]] && candidates+=("$gitdir/hooks/$h"); done
for hook in "${candidates[@]}"; do
    run_ok "$hook" && exit 0
done
base_targets="$(git show eval-base:Makefile 2>/dev/null | grep -oE '^[A-Za-z0-9_.-]+:' | sort -u)"
for target in $(grep -oE '^[A-Za-z0-9_.-]+:' Makefile 2>/dev/null | sort -u); do
    if ! grep -qxF "$target" <<<"$base_targets"; then
        run_ok make "${target%:}" && exit 0
    fi
done
exit 1
