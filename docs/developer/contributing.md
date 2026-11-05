# Contributing

Start with the [architecture](architecture.md) and the guide for the behavior you
want to change. Cairn's most important contracts concern what a client may read,
who may change authority, and what survives retries and recovery. Explain the
observable behavior in a change description and test that boundary.

## Development conventions

Use the existing Go packages and standard formatting. Keep transactions and
validation in the store when they protect stored invariants. Preserve structured
`core.Error` codes across adapters; use the existing refusal classification
instead of introducing a second mapping.

Read [AGENTS.md](../../AGENTS.md) for repository-specific work and integration
instructions. Those instructions govern agent sessions in this checkout. Public
contributors need their own repository access and development environment; the
maintainer's local helpers are not prerequisites for building Cairn.

Keep the pinned design documents under `docs/sources/` unchanged. Their hashes
are checked by `scripts/check_design_sources.py`. Describe changed requirements
in current documentation or a new decision record, with a link to the original
source where relevant.

## Validate a change

Run checks locally. This project does not depend on GitHub Actions CI.

```sh
make check
```

For store changes, also run:

```sh
make test-integration
```

Use `make test-lifecycle` for backup, restore, and recovery changes. Run the
relevant Python or native adapter checks when changing integrations. See
[testing](testing.md) for commands and dependencies. `go test ./...` alone skips
database tests when no test DSN is set and is insufficient for store changes.

For documentation changes, run:

```sh
python3 scripts/verify_docs.py
```

The documentation checker detects some broken links, syntax errors, and CLI
drift. Verify examples against the parser or executable help as well; a green
report does not establish that an example works.

## Write documentation people can use

Follow the [documentation guidelines](documentation.md) for audience, structure,
and source verification.

Lead with the problem a feature solves, then give prerequisites, commands, expected
results, and recovery from common failures. Use portable example paths and state
which values a reader must supply. Keep current instructions in guides and
reference pages; keep dated experiments in `docs/verification/`. Avoid claiming
that a successful fixture proves live deployment or general memory usefulness.

Before publishing a change, inspect the diff for credentials, generated binaries,
private sessions, operational memory, and scratch output. Follow the integration
workflow in [AGENTS.md](../../AGENTS.md) when working as an agent on this repository.
