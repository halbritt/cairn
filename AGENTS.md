# Cairn

Read README.md and docs/design-review.md before extending the initial slice.
The source design is pinned under docs/sources. Follow the operator-authored
producer-attribution correction where it supersedes earlier synthesis.

- Keep PostgreSQL as the operational store; tests use disposable clusters.
- Run `make test-integration` and `make check` for store changes. `make test`
  alone skips database coverage when no test DSN is set.
- Never use an existing production database for tests or test cleanup.
- Preserve store-owned identity and observation channel fields. A payload field
  or a caller-selected principal flag is not authentication.
- Keep unimplemented authority, consequential reads, and destination delivery
  unavailable until their contracts and tests exist. Do not infer permissions
  from these project instructions; the user's current authorization governs.
- Do not capture private Council sessions or commit operational memory,
  credentials, generated binaries, model output, or raw workspace exhaust.
- Report the implemented slice and tested claims separately from full design
  acceptance and measured usefulness.
