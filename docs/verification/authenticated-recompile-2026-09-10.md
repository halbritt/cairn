# Authenticated historical reconstruction — 2026-09-10

Authenticated agent and observer profiles can now recompile their own retained
receipts. This closes the access gap found in the [replay audit](replay-requirements-2026-09-10.md):
the actual September 8 host receipt was reconstructed with its original observer
token, rather than relying on a separate operator preflight with the same seal.

`agent recompile` accepts the original receipt ID, query and any explicit entity
intent. The API obtains identity, repository and destination from its provisioned
profile. It returns `historical: true` with the exact reconstructed package. The
core retains responsibility for historical ranking, eligibility, integrity and
missing-payload checks. No database migration or new ordinary MCP tool is needed.

## Access and historical meaning

The new destination-aware core entry point requires the original receipt owner
and destination. It checks every returned record body and index preview against
current repository, sensitivity and forgetting state in the reconstruction
transaction. Current record identities remain locked against edits/forgetting
until that read completes. A restriction refuses the whole package, preserving
the meaning of its original seal.

Historical corrections and eligibility changes remain visible through the old
read set. They are not permission to execute it: current `run-package`, binding,
claim and pull checks remain in place. The operation issues no handles, renews no
credits, and writes no receipt, exposure, delivery or outcome rows. It does not
retain the supplied plaintext query. The trusted library/local CLI `Recompile`
interface retains its existing behavior.

## Verification

The first API test failed with `NOT_FOUND: unknown endpoint`. After implementation,
the same observer could reconstruct the exact original package after a note was
revised and another note was created. Current execution still returned
`STALE_PACKAGE`. Further tests cover ordinary-agent ownership, another caller's
receipt, changed query, caller-supplied destination/identity, body and index
privacy, destination changes, invalid destination and forgetting. Sensitivity
restriction is injected only in a disposable fixture; ordinary editing cannot
change sensitivity.

`make test-integration` passed the disposable PostgreSQL race suite and existing
authenticated CLI/MCP integration checks. `make check` passed. These checks cover
the new access path and existing contracts; they do not establish memory benefit.

The retained real-run check restored the reviewed September 8 trial into a
separate disposable database, using the original identity configuration and
observer token. The API recompiled receipt
`ad4f4fad-1400-425c-810b-b08d10513b58` to its original seal, declared historical
revision and selected note version/body digest. An identical retry returned the
same result. The original collector token and local operator CLI were refused
access to that host-owned receipt.

Its selected note and supporting evidence predated the recorded run. The lesson
written after the failure stayed excluded. All eight original artifact hashes,
credential/configuration file hashes and inspected receipt/candidate/use/delivery/
outcome/assessment tables were unchanged. The temporary API stopped and the
disposable cluster was removed. No model task ran, and no historical workspace
was recreated.

## CI assertion correction

The first CI run failed in two new core tests under UTC. `reflect.DeepEqual`
compared internal `time.Time` location pointers: a local reproduction showed equal
instants and identical serialized packages despite different pointers. The tests
now compare the complete serialized package, including identity and seal. They
pass in both UTC and America/Los_Angeles. Production code is unchanged by this
correction; the initial failed CI result remains in the metadata.

## Remaining evidence

This reconstruction concerns the September 8 recurrence. Its advice still
postdates the older Striatum incident. The original recurrence classification
and rejected outcome remain unchanged. E3/E4 remain partial pending original-
incident evidence and broader task-value work; historical inspection alone adds
no task-benefit claim.

The [metadata manifest](authenticated-recompile-2026-09-10.json) retains source
identities, checks, sanitized real-run results, installation evidence and doctrine
provenance. Raw historical packages, credentials and model artifacts stay outside
the repository.


Pincite packet `pkt-d95c8e9d819ddc26` records the typed evidence, validated decision
receipt and closed citation traces. Four remaining interface-design obligations
are nonmaterial: this change adds a concrete method and an API operation without
introducing or relocating a Go interface.


Clean source `3f17cf2` is installed as both CLI and API. A pre-install backup was
retained, migration stayed at 034, and all 77 record versions and host settings
were preserved. The installed ordinary hosted profile reconstructed an existing
five-entry schema-13 index with its original seal. The metadata includes exact
build, service, backup and preservation hashes.
