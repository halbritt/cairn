# Ultrareview repairs — 2026-09-13

The owner requested repairs from Cairn note
`32a814a4-9d16-4099-ad8f-b3eef73153d0` before Hermes integration. The note reviewed
`921f8bb`; this repair checked each finding against `06975ed`. The nine findings
and the additional budget lead are addressed below.

| Finding | Result |
| --- | --- |
| N1: top-level help returns a JSON string | Return `commandHelp`, matching ordinary command help. Native `go run ./cmd/cairn help` prints text and the new paging syntax. |
| N2: cleanup error prevents outcome persistence | Retain the cleanup error, write the pending request and attempt `RecordOutcome`, then return joined errors. Tests use a real process and injected cleanup failure, both with a functioning outcome store and a refused outcome write. The latter recovers exactly once from its pending request. |
| Core sentinel comparisons | Use `errors.Is` throughout non-test core code for `pgx.ErrNoRows`. This is a consistency repair; no production wrapped-sentinel failure was observed. |
| CLI EOF comparisons | Use `errors.Is` for decoder EOF checks, including the recovery reader. |
| Missing conflict classification | `Resolve` returns `NOT_FOUND` for an absent conflict. |
| Inspection validation order | Validate requests before repository authorization in run/use reports, conflicts and proposal groups. `List` had the same issue and is corrected too. Scoped-channel tests distinguish empty input from an unauthorized nonempty repository. |
| Refusal truncation | Persist `candidates_count` before the 1,000-entry cap. Historical records without the field retain an unknown total; a newly observed zero is explicit. Tests cover 0, 1,000 and 1,001 candidates. |
| CLI pagination | `list [--limit N] [--offset N] REPO` and `impact [--offset N] UUID` expose existing core paging. Impact retains its 100-exposure page size. A disposable database test reads beyond the first 100 records and exposures without repeating the preceding page. |
| OpenCode launcher check | Match exact `opencode`/`opencode.exe` basenames and resolve renamed symlinks. Names such as `my-opencode-plugin` no longer trigger the guard. Arbitrary wrapper scripts still require an accurate operator destination declaration; basename checks do not authenticate providers. |
| Unconfirmed index envelope lead | Reproduced: repeated CLI shell commands can exceed core's reserved envelope. Omit those optional strings when necessary while preserving all entries, mandatory context, source seal and complete pull arguments. Still refuse if the structured response cannot fit. |

## Verification

The index presentation regression failed before its repair. A second test uses a
real disposable store, a mandatory instruction and optional notes to reach a
core-admitted index whose full CLI view exceeds the requested room. Its compact
view retains the exact selected context and usable handles.

`make test-integration` passed all Go packages with the race detector and the
CLI/API/MCP fixture checks. `make check`, all 74 Python tests, and native
top-level help passed. Tests did not use the running store. There is no migration, authority change, new memory
provider or Hermes installation in this repair.

## Boundaries and decisions

The runner preserves its existing process classifications, separate task-outcome
assessment, idempotent outcome request and artifact capture. It reports cleanup
failure even when the child exited zero. The test seam is an unexported per-call
kill function; it adds no mutable global hook or production configuration.

The presentation repair removes only redundant command text. Changing the sealed
compiler package, silently dropping entries or increasing the user's budget would
change a different contract. A response that cannot fit even with structured
arguments remains an explicit refusal. MCP and existing native adapters continue
to use their own presentation budgets.

The note's script slices remain unreviewed as a complete set. This repair uses
local review of the changed paths and existing integration checks; it does not
start another paid cloud review or claim coverage of all 78 scripts.

The selected Pincite concepts are explicit contextual errors, explicit failure
policy, repository-contract precedence, evidence before intervention, and behavior
preservation. The associated decision receipt records the packet and remaining
evidence limits. No production incident rate, full-design acceptance or general
memory usefulness is inferred from these tests.

Pincite release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f` passed its
retrieval-state gate. Final packet `pkt-08467984d9052110` has SHA-256
`08467984d905211059e244df153665d7f2faaa4f2cc446b982460b42620b35e4`,
using `doctrine-f6bbb5196a3f8bf9` and `retriever-ec995ecdd083b2c8`.
The [decision receipt](ultrareview-repairs-decision-2026-09-13.json) lists all 11
unmet obligations and why they are nonmaterial to these tested claims. No material
obligation remains for the bounded repair.
