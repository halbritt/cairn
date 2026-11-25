# 0018 — Local validation, no GitHub CI

Status: accepted by the owner, 2026-09-16 (commit `175fe9fe`).

## Decision

Validation runs on the developer's host. The repository has no GitHub Actions
workflows and no check depends on GitHub CI.

## Consequences

- `make check` and `make test-integration` are the required checks for store
  changes. `make test` alone skips database coverage when no test DSN is set.
- Tests use disposable PostgreSQL clusters. A production database is never a
  test target or a cleanup target.
- A change is integrated when its checks pass locally and `main` is pushed.
  See [contributing](../developer/contributing.md) and
  [testing](../developer/testing.md).
