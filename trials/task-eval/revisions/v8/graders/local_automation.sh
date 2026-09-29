#!/usr/bin/env bash
# v8 grader for local-ci (v7 with the effective hook directory only): an installed automatic trigger runs the tests and rejects a regression.
# Run from the case workspace. Candidate triggers: executable pre-commit, pre-push or
# pre-merge-commit hooks in the directory git actually uses: core.hooksPath when set,
# otherwise the default hooks directory (`git rev-parse --git-path hooks`). A Makefile target
# alone is not automatic. A trigger passes only if it exits 0 on the clean tree AND
# exits non-zero after a failing regression is injected into calc.py (restored afterwards).
# Exit 0: such a trigger exists. Exit 1: no trigger, or none catches the regression.
# Exit 4: undetermined (calc.py missing or the tree changed shape).
set -u
# Stale bytecode would hide the regression: never write or reuse caches.
export PYTHONDONTWRITEBYTECODE=1
clear_caches() { find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null; true; }
[[ -f calc.py ]] || { echo "calc.py missing"; exit 4; }
grep -q 'return a + b' calc.py || { echo "calc.py no longer has the fixture body"; exit 4; }
# Git runs hooks only from its effective directory; a core.hooksPath override disables .git/hooks.
dir="$(git rev-parse --git-path hooks)"
triggers=()
for h in pre-commit pre-push pre-merge-commit; do
    [[ -x "$dir/$h" ]] && triggers+=("$dir/$h")
done
[[ ${#triggers[@]} -gt 0 ]] || { echo "no installed automatic trigger"; exit 1; }
backup="$(mktemp)"; cp calc.py "$backup"
restore() { cp "$backup" calc.py; rm -f "$backup"; }
trap restore EXIT
for hook in "${triggers[@]}"; do
    cp "$backup" calc.py; clear_caches
    timeout 120 "$hook" </dev/null >/dev/null 2>&1 || { echo "$hook fails on the clean tree"; continue; }
    sed -i 's/return a + b/return a + b + 1/' calc.py; clear_caches
    if timeout 120 "$hook" </dev/null >/dev/null 2>&1; then
        echo "$hook accepted a failing regression"
        continue
    fi
    echo "trigger ok: $hook"
    exit 0
done
exit 1
