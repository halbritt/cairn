# Documentation validation

Run the documentation checker from the repository root:

```sh
python3 scripts/verify_docs.py
```

It uses the Python standard library and invokes `bash -n` for shell examples.
The default strict mode exits nonzero when a selected tier reports a failure.
Use `--json` for machine-readable results, `--tier 1,3` to select checks, or
`--no-strict` to inspect failures without a failing exit status.

## What it checks

| Tier | Checks | Limits |
| --- | --- | --- |
| 1 | Relative Markdown links, target files, heading anchors, and explicit HTML anchors | Uses regular expressions and its own slug generator; does not fetch external links or fully parse Markdown |
| 2 | Documented Cairn command names, known flags, and selected command contracts | Discovers flags from source but does not validate every command-specific flag combination or execute examples |
| 3 | JSON parsing and shell grammar in recognized fenced blocks | Syntax validity does not establish valid API fields, available tools, or safe execution |
| 4 | Required guide files with at least three nonblank lines | Presence and length do not establish accuracy or usefulness |

The source of these checks and the required-file list is
[scripts/verify_docs.py](scripts/verify_docs.py). Discovery skips hidden
directories and configured exclusions, including the pinned `sources` tree.
CLI checks cover the README and `docs/` pages; other tiers use their own scopes.

## Review beyond the checker

For changed examples, inspect the command's parser or `--help`, confirm field
names against request types, and run a disposable fixture where execution adds
useful evidence. Check error handling and required setup as well as the happy
path. Do not run a documented mutation against an operational database just to
validate its syntax.

Keep historical verification reports labelled as historical. Their commands,
counts, and deployment observations describe the recorded run. Current guides
should explain how a reader can reproduce a check in their own environment.

The checker is separate from `make check`; run both when a change affects Go
contracts and their documentation. See [developer testing](docs/developer/testing.md)
for the full validation workflow and [interpreting results](TEST_READY.md) for
what a successful run establishes.
