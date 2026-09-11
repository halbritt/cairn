# Everyday agent flag help

Source checkpoint: base revision `c3208eb32c59071fae2ebb6a7d0f6b5942774209`,
reviewed 2026-09-11. This report covers source behavior and tests, not installation
acceptance.

## Observed gap

The project binary returned exit 2 and an `INVALID_REQUEST` envelope for
`cairn agent search --help`, `remember --help`, `pull --help`, and
`pull-evidence --help`. The saved independent check failed on the first command.
The adjacent JSON-operation help already worked without credentials, API access,
HOME, or stdin.

## Bounded OpenCode trial

An isolated OpenCode run used model `deepseek/deepseek-v4-flash-0731`, a 24-request
relay bound, and a 600-second task timeout. It started from the base revision,
connected to the native Cairn MCP server, and passed its Go preflight. The run
made 23 provider steps, one Cairn search, no Cairn pull, and 14 shell attempts;
seven shell forms were denied before permitted alternatives worked.

The process exited 1 after 461.6 seconds. Its worktree contained only
`cmd/cairn/scratch_help_probe_test.go`, which printed results but asserted no
behavior. There was no production patch. Receipt
`7bea52f5-991c-4b07-81f4-ce1e9b7fb65c` now has an instrumented version-2
`rejected` assessment with `failure_domain=capability` and selected evidence.
Version 2 corrects version 1's six-denial count to seven. The denial friction is
retained as a competing explanation, not treated as the sole cause. A search
call, MCP connection, or process exit does not establish memory benefit.

## Implemented behavior

The CLI's early operation-help dispatch now recognizes all four flag-based
commands. Search, remember, and pull each construct one `flag.FlagSet` that both
help rendering and execution use. `pull-evidence` shares pull's parser with its
own positional usage. The existing execution functions still parse and consume
the resulting option values; help returns before the connection defaults or
client are resolved.

The public help gives both positional forms and, for pull operations, the existing
JSON-stdin form. Go's standard flag renderer lists the registered options. Both
`--help` and `-h` return readable text. Extra arguments, unknown operations, and
literal `--help` data behind `--` do not become successful help calls.

## Verification

- Four public-interface tests failed individually before their corresponding
  help cases were implemented, then passed.
- The consolidated Go test exercises all four commands, both help spellings,
  absent HOME/CAIRN_HOME, and an input reader that panics if read.
- `scripts/check_agent_help.py` builds on the existing public subprocess check.
  It keeps stdin open, removes HOME/CAIRN_HOME, points database access at an
  absent host, checks both spellings, and requires malformed invocations to exit
  2 with `INVALID_REQUEST`.
- The baseline-derived independent check verifies current parser options,
  positional usage, literal-help behavior, and an empty scratch directory.
- `make test-integration` passed against a disposable PostgreSQL cluster. This
  includes authenticated search, pull, remember, existing JSON help examples,
  and the public offline-help process check.
- `make check` passed `go vet ./...` and the repository formatting gate.

Pincite packet `pkt-1fda759a1a719776` (content SHA-256
`1fda759a1a7197768816af4a6b2dea18fc247ec10d57283d9a7fab02ec48a605`)
has a validated `verified` decision receipt and five closed concept citations.
Unmet obligations about ingestion, ranking, UI, configuration references, and
data keys are nonmaterial because those surfaces are not changed. The no-change
procedure and expected-future-change obligations are also nonmaterial because
the current public defect and bounded selected task establish present pressure.

## Limits

The checks establish behavior for the exercised CLI and disposable API/store.
They do not establish installation, setup-time savings, model-task improvement,
or durable memory benefit. The OpenCode failure is one bounded model run with
permission friction; it is not a general capability estimate. No API, database,
schema, authority, destination, or delivery behavior changed.
