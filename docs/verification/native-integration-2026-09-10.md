# Native Striatum integration — 2026-09-10

The existing native Cairn implementation and two compatibility repairs are
merged and pushed to Striatum main at [`5848b23`](https://github.com/halbritt/striatum-next/commit/5848b23f1800e7051e59f4f70303244c711e0575).
The temporary integration branch and worktree are removed. Native contract
adoption and task benefit remain open.

## Findings and repairs

The branch at `ee8a463` changed two historical paths despite leaving the accepted
catalog at `observation@1` and `build@3`:

- Ordinary request issuance rejected every subject beginning `cairn-context/`.
  The guard now reserves that prefix only under observation contract 2 or later.
  Contract 1 continues producing its ordinary Markdown ECR.
- Build preparation parsed prefixed packet paths as native capture identities
  before checking the consuming contract. An ordinary path such as
  `cairn-context/source.go` withheld a historical build. Contracts below 4 now
  retain their ordinary input interpretation. Contract 4 still validates selected
  native evidence; unsupported newer native contracts refuse.

Both final regression fixtures fail against the old production code through a
Go overlay and pass after repair. The observation case uses public request
issuance and an actual Driver execution/admission. The build case uses actual
step preparation with the repository's existing folded Work Graph fixture.
The latter is not independent evidence that a model completes a build.

## Verification

Focused Driver race tests and the eight-package native race suite pass. The
latter includes real disposable Cairn service acquisition, a current confined
launch, stale-selection refusal and the complete Driver admission chain. The
repair experiment skips because no explicit experiment specification is set.
No model inference or operational database test ran.

The strict supervision target passes using Go's cached results. Full `make check`
passes, including the Driver suite (615.422 seconds). Updated proposal
documents pass the accepted Decision pin lint. The catalog, generated decisions,
policy and backend declarations have no diff from main. New schemas remain
under `schema/proposed/`.

## Scope and remaining work

The source review follows the changed old-contract entry points through request
issuance, environment resolution, build preparation, rendering, supervisor and
admission. It supports integrating preparatory code with the exercised historical
behavior preserved. It is not a comprehensive security audit, native contract
acceptance or proof of durable task value.

The existing opening request remains RQ-408328 at `captured`. Its eventual
closing observation must describe the actual completed pass. This preparatory
integration does not close the wider native contract/adoption work or resolve
any acceptance gate. The installed Driver remains clean `a6b1ae7`; deployment
requires a separate concrete adoption step. The retired model comparison must
not be repeated unchanged.

Ordinary Cairn search and pull recovered procedure
`82fcfec8-474b-48fe-9a00-a36f59d34ac1` v2 before implementation. It helped locate
the existing host path and cautioned against a second supervisor. The two
compatibility defects were found by source review, not by that note. This is
workflow context, not a measured incremental memory benefit.


The unrelated untracked `cmd_test.go` in Striatum main was preserved byte for
byte. The merge is a Git source integration; it does not admit an architectural
change through compiler gates. No deployment, native contract acceptance or
operational graph mutation occurred. GitHub returned no workflow runs for this
repository at the post-push check, so no CI result is claimed.

Validated Pincite packet `pkt-a80609a7700cd409` supported contract precedence,
behavior preservation and separation of evidence from acceptance. The private
`decision-receipt/2` validates and both citation loops are closed. The
[verification metadata](native-integration-2026-09-10.json) retains the 23
nonmaterial generic obligations and their scope reasons. Scratch logs and
advisory evidence are in `/tmp/cairn-native-integration-20260910/`.

The selected capture procedure was revised from v2 to v3 through ordinary
compare-and-swap passage replacement. It now points to merged main and records
the removed worktree and historical-contract guards. All other text was preserved;
the complete v2 body remains unchanged in history. Fresh ordinary search and pull
returned v3 with the expected digest. This prevents a stale branch reference in
future retrieval, without establishing independent downstream task benefit.
