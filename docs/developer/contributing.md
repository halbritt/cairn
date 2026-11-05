# Contributing to Cairn

This document details development standards, design constraints, and trunk integration requirements for contributing to Cairn.

---

## 1. Code Style & Standards

- **Standard Go Idioms**: Write idiomatic Go adhering to standard conventions. Avoid unnecessary abstractions.
- **No `pkg/` Directory**: Cairn maintains a flat root package structure (`core/`, `artifacts/`, `runner/`, `localapi/`, `mcpapi/`, `semantic/`, `internal/`). Do not introduce a `pkg/` directory.
- **Formatting**: Format all Go files with `gofmt`. Run `make check` to verify compliance.
- **Error Handling**: Return typed `core.Error` instances with standardized error codes (e.g. `INVALID_REQUEST`, `AUTHORITY_DENIED`, `VERSION_CONFLICT`, `BUDGET_REFUSED`).

---

## 2. Design Source Immutability

Historical design specifications and operator synthesis documents located in `docs/sources/` are **permanently immutable**:
- Never edit, overwrite, or delete files in `docs/sources/`.
- `scripts/check_design_sources.py` validates SHA-256 digests of these snapshots on every `make check`.
- If requirements evolve, create new documents in `docs/` or update current guides rather than altering historical sources.

---

## 3. Trunk Integration & Worktree Hygiene

Development in Cairn is performed by parallel autonomous agents without human pull-request reviews. Every contributor and agent is strictly required to follow the trunk-integration lifecycle:

1. **Clean Worktrees**: Never leave uncommitted or untracked changes before concluding a turn. `git status --porcelain` must be clean.
2. **No Worktrees in `/tmp`**: System cleanup daemons (`systemd-tmpfiles-clean`) remove aged files in `/tmp` while leaving `.git` references intact, corrupting repositories. When creating worktrees, use `git-wt create <slug>` (placing worktrees under `~/.cache/agent-worktrees/cairn/<slug>`).
3. **Never Leave Unmerged Branches**: Rebase work onto `origin/main` cleanly, run required checks (`make check`, `go test ./...`), and fast-forward `main`.
4. **Push Verified Work**: Local commits that are not pushed to `origin/main` are invisible to other agents and hosts. Always execute:
   ```sh
   git push origin main
   ```
5. **Clean Removal**: Delete temporary worktrees and branches upon landing changes.

---

## 4. Pre-Commit Verification Checklist

Before committing and pushing changes:

- [ ] `go test ./...` passes without errors.
- [ ] `make check` passes cleanly (vet, contract, formatting, design sources).
- [ ] `python3 scripts/verify_docs.py` passes 100% across all 4 tiers.
- [ ] `git status --porcelain` shows only intended modifications.
- [ ] Commit message clearly states what changed and why.

---

## Related Documentation

- [Architecture Guide](architecture.md): Package layout and core concepts.
- [Testing Guide](testing.md): Test commands, fixtures, and verification suites.
- [Roadmap](../roadmap.md): Project roadmap and implementation status.
