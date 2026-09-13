# Evidence impact inspection

`check-evidence` previously reported only an affected-record count, while `impact`
accepted a record ID and listed its direct exposures. The new `evidence-impact`
command exposes the exact evidence-reference and versioned-relation edges already
retained by the store. It requires no new dependency schema or inference from
opaque source labels.

## Implemented behavior

A protected local query starts from an evidence ID, finds directly citing
versions, follows explicit reverse relations and returns those versions with
recorded uses. Multiple paths are deduplicated. `via` preserves parent versions
and `derived_from`, `specializes` or `contradicts` types; a contradiction is not
presented as supporting evidence. Historical version class and current record
version/lifecycle are distinguished. Original exposure kind distinguishes an
index preview from supplied body context.

Record and use pages each contain at most 100 rows, with separate offsets and
truncation flags. Both read the same repeatable-read snapshot per request;
subsequent calls can observe changes. Recursive work has a 30-second deadline.
Timeouts do not produce a success report claiming complete coverage.

The local CLI and authenticated local API expose the same response. Hosted API
profiles are refused before inspecting evidence. No source labels, evidence/note
bodies or raw queries enter the report. Inspection creates no check generation,
mutation preview, authority event or new evidence claim.

## Verification

The disposable PostgreSQL/race suite passed, including these new behaviors:

- A directly supported B version, an ordinary derived note and a C instruction
  form a diamond of retained relations. All three versions and their actual
  compiled exposures appear once, with the exact relation types.
- Correcting B using independent evidence preserves its earlier citation and
  exposures without assigning the old dependency to the new version. Retraction
  preserves the historical C version and exposes its current inactive state.
- Altered evidence bytes are labelled divergent while their recorded impact
  remains inspectable. Body, label and query canaries are absent. Foreign repo,
  unknown ID, negative offsets and cancelled reads are checked.
- A fixture creates 102 affected versions and 101 actual compiled exposures.
  Two pages cover each set without duplicate or missing identities. Changing
  only the use offset does not advance the record page. Unused evidence returns
  empty collections.
- Actual CLI/Unix API calls inspect a captured support object and its promoted
  claim after hosted index/pull. Operator and local-agent results agree; flags
  advance both collections independently. The local client has an unusable DB
  address. Hosted inspection returns `AUTHORITY_DENIED` without source IDs.

`make test` passed all Go packages and 26 Python tests. `make check` passed vet
and formatting. The final integration log is
`/tmp/cairn-evidence-impact-paths-integration.log`; adjacent JSON records source
and artifact hashes. Earlier successful runs predate the final exposure-kind and
relation-path fields and are not substituted for final-source coverage.

## Scope and limits

This is a read-only evidence-ID view, not a retraction/deletion authorization or
an automatic qualification update. It does not infer derivation from replacement,
shared record identity, similar text or source labels. Managed external artifacts,
as-cited spans, unknown derivations and broader source lifecycle remain open.
Pagination bounds returned rows; large recursive graphs may time out. No large
production graph or real evidence incident is qualified by these synthetic checks.
The feature makes existing links inspectable; downstream task benefit is unproved.

## Local deployment

After a backup, a clean build of `6e604ce24e1697d939dbd9f17893bc9fa8c752fe`
was installed and the API restarted. The running executable matches binary
SHA-256 `1adc2afa7feebf3da4cd40662dc8d071c4dac92ad9e2e2cfe144c687252ec62c`.
PostgreSQL remained running; the semantic worker script and launch configuration
were preserved.

A hosted search/pull retrieved the current storage note v2 with its exact indexed
body hash. A hosted call to the new evidence-impact endpoint returned
`AUTHORITY_DENIED`. Protected operational traces were not read; the positive
inspection behavior is established by the disposable CLI/API fixtures above.
