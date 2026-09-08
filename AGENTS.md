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

## Use the provisioned memory interface

On the owner's host, when `~/.local/share/cairn/hosted-agent.token` exists and
`cairn` is installed, use that profile for hosted-agent memory in this repository.
At the start of a substantive task, search for relevant prior lessons with a
specific task/run identity:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" search \
  --repo "$HOME/git/cairn" --task TASK_ID --run RUN_ID 'relevant task terms'
```

This profile is bound to the canonical `$HOME/git/cairn` repository identity;
keep that `--repo` value when working in another worktree. Choose actual task/run
identifiers and reuse them for that work. Inspect relevant
bodies with the returned `pull_command`; A records are fallible notes, so verify
current source before applying them. A missing profile or unavailable service
does not block work. Never substitute a local-destination profile when its result
will enter a hosted model, and do not provision credentials as part of routine
retrieval.

Use `agent remember` with the same profile for explicitly selected, useful
repository findings. Include source/verification context in the note, choose
`--shareable` only for material suitable for hosted delivery, and supply a stable
`--request-id` when retrying. `--stdin` accepts a chosen note body. Do not capture
raw sessions, private Council content, credentials or workspace dumps; ordinary
capture does not confer authority or prove task success.
