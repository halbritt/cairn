# Preserve known refusal gate results — 2026-09-10

A required instruction could refuse compilation correctly while its protected
candidate trace reported `mandatory: false` and `EVALUATION_INCOMPLETE`. The
compiler returned on the eligibility error before copying the mandatory flag
and known reason into the diagnostic record. Missing context followed the same
pattern. A mandatory category-limit refusal could instead be labelled `SELECTED`
before it passed admission.

The correction records those known values before returning. Missing context now
retains `CONTEXT_MISSING`; required runtime/support failures retain
`POLICY_UNENFORCEABLE`; disputes retain `OPEN_CONFLICT`; category-limit failures
retain `CATEGORY_BUDGET`. New compiler refusals use explanation version 2. The
existing refusal code, message, operation identity and access rules remain.
See the [refusal contract](../refusals.md).

## Preservation boundary

This changes diagnostics, not which records are admitted or which requests fail.
The scan still stops at the first binding failure, traces remain explicitly
partial and the existing 1,000-candidate cap remains. No later record is evaluated
to make the trace appear complete. Bodies, raw queries, evidence/grant snapshots
and private candidate identities remain excluded. Hosted API profiles still
cannot inspect protected refusal traces, even for their own requests.

Stored version-1 observations and exact refusal retries retain their original
details; the correction does not rewrite earlier explanations. There is no
migration, new tool, API operation, reporting service or recovery mechanism.
R5 remains partial for its broader explanation requirement.

## Evidence and limits

The initial regression reproduced the wrong mandatory flag and missing reason
for runtime mediation, missing context and an explicit dispute, in both body
and index mode. A separate test reproduced the premature category `SELECTED`
label. A proposed two-position test had an incorrect expectation: issuing
conflicting policies already creates a dispute, so compilation legitimately
stops at its first candidate. The corrected test verifies that stopping behavior;
the initial failure remains in the retained log. An API fixture also needed to
reuse its original bootstrap request across destination cases, and the CLI check
needed its expected explanation version updated from 1 to 2. Those failed runs
remain retained; they did not require weakening production validation.

The recalled task-phase procedure described mandatory unknown-context refusal
and the need to preserve declared pins. Its full body was inspected after the first reproduction and corroborated the
expected behavior. It did not establish discovery of the defect. The code and
existing tests were also available, so this does not establish an independent
memory contribution or net task benefit. The useful result is more accurate
diagnostics for a refused retrieval.

Targeted PostgreSQL regressions, the full disposable race/API/MCP integration
suite and `make check` pass. Authenticated local callers receive the corrected
candidate detail; hosted owners remain denied inspection. A compatibility check
retains a version-1 observation and verifies that an identical retry does not
rewrite it. Installation is recorded below.

[Verification metadata](refusal-gates-2026-09-10.json) retains hashes of the source,
checks and failed runs. Pincite packet `pkt-0bf5356afef1acdc` uses corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`, release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`, a validated decision receipt and closed
citation loops. Seven interface/nil-contract obligations remain nonmaterial:
this correction changes no interface or nil-return contract. Private observations
remain under `/tmp/cairn-refusal-completion-20260910`.


## Installation

Installed clean `c5fa55ad4b0b9c85eb6cb39c83acdac2412a8040` as the CLI and API after
a store backup. The API restarted at PID 3655879 and reports the same build as
the client. PostgreSQL stayed running at migration 034. All 78 retained record
versions and their combined digest are unchanged, along with the native adapter,
recent-file plugin, identities, host settings and semantic-worker configuration.
No operational note or instruction was created to exercise this diagnostic fix;
its behavior was verified against disposable stores before installation. The
metadata retains executable, configuration and backup hashes.


## Shared-database fixture correction

[CI 34528936872](https://github.com/halbritt/cairn/actions/runs/34528936872)
failed in the new API fixture because core tests had already installed the shared
database's root. The local integration script isolates package databases and had
passed. The API test now uses the core suite's exact synthetic operator/bootstrap
identity, so it works after the existing root is installed. A local UTC run of
core followed by API against one disposable database passes with the race detector;
static checks pass. Production source and installed `c5fa55a` are unchanged.
The initial failed CI result remains in the metadata; corrected-source CI is
recorded separately when complete.


[Corrected CI 34529414559](https://github.com/halbritt/cairn/actions/runs/34529414559)
passed on `3ad6e134320c882e6c5cc86e1f0c598d56aa27c0`. Both jobs and every
recorded step passed, including the shared-database PostgreSQL race suite,
Python checks, static checks, authenticated CLI checks and OpenCode plugin check.
The tested production source matches installed `c5fa55a`; the intervening commit
changes only the API fixture and documentation. The CLI and API still report the
clean installed build. The initial CI failure remains in the history and metadata.
